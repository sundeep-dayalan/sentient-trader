"""
AI Analyst — LangGraph Multi-Agent Committee
==============================================
The cognitive core of Sentient Trader. A LangGraph state machine that runs
a sequential three-persona debate before synthesizing a final trade decision.

Graph topology:

  START → [check_cache] ──── cached? YES ──────────────────────────────→ END
                │
             not cached
                │
        [fetch_context]   pulls live price + day-change from Alpaca Data API
                │
    [momentum_analyst]    LLM call #1 — trend/momentum lens
                │
      [value_analyst]     LLM call #2 — reads momentum opinion, responds
                │
       [risk_analyst]     LLM call #3 — reads both, stress-tests conclusions
                │
        [synthesizer]     LLM call #4 — weighs the debate, makes final call
                │
        [assess_risk]     pure Python — checks sentiment/confidence thresholds
                │
      ┌─────────┴──────────┐
   trade?                hold?
      │                    │
 [execute_trade]      [log_result]
      │                    │
 [log_result] ─────────────┘
      │
     END

Why sequential instead of parallel personas?
  Parallel gives three independent opinions. Sequential gives a real argument:
  the value investor reads the momentum trader's take before responding, so
  genuine disagreement surfaces rather than each persona talking past the others.

Why four LLM calls instead of one?
  Each call has its own system prompt locking the model into a specific worldview.
  A system prompt is context-window-level conditioning — it's fundamentally
  different from asking the same model to "roleplay" three voices in one prompt,
  where the first persona's framing contaminates the others.

LLM provider details (model tiers, rate-limit handling, client init) live in
llm.py — this file is provider-agnostic.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest
from langgraph.graph import END, START, StateGraph

import config
from cache import HeadlineCache
from llm import ModelRouter, create_llm_client, sanitize_llm_error
from logger import SupabaseLogger
from schemas import (
    LLMOperationTrace,
    NewsMessage,
    PersonaAnalysis,
    PersonaOpinion,
    SynthesisResult,
    TradeAnalysis,
)
from trader import AlpacaTrader

log = logging.getLogger("agent.analyst")


# ── LangGraph State ──────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """
    Everything the agent knows at each step of the graph.
    Nodes read from this dict and return partial updates.

    The three persona opinion fields accumulate as the debate progresses:
      - momentum_opinion is set by momentum_analyst
      - value_opinion    is set by value_analyst (after reading momentum_opinion)
      - risk_opinion     is set by risk_analyst  (after reading both above)
    The synthesizer reads all three, then sets analysis.
    llm_operations accumulates the exact messages and structured output for
    every LLM call so log_result can write one complete decision_trace JSONB.
    """
    news:              NewsMessage
    is_cached:         bool
    market_context:    Optional[dict]          # {price, day_change_pct} from Alpaca
    momentum_opinion:  Optional[PersonaAnalysis]
    value_opinion:     Optional[PersonaAnalysis]
    risk_opinion:      Optional[PersonaAnalysis]
    momentum_model:    Optional[str]           # model that powered each persona
    value_model:       Optional[str]
    risk_model:        Optional[str]
    llm_operations:    list[dict[str, Any]]    # raw prompts/responses per Decision Core LLM call
    analysis:          Optional[TradeAnalysis]  # assembled after synthesis
    should_trade:      bool
    risk_gate:         Optional[dict[str, Any]]
    trade_order_id:    Optional[str]
    execution:         Optional[dict[str, Any]]
    error:             Optional[str]
    is_simulated:      bool


# ── Shared Prompt Helpers ────────────────────────────────────────────────────

def _market_line(ticker: str, ctx: Optional[dict]) -> str:
    """Format the market context header line shared across all four prompts."""
    if ctx and ctx.get("price") is not None:
        change = (
            f" ({ctx['day_change_pct']:+.2f}% today)"
            if ctx.get("day_change_pct") is not None
            else ""
        )
        return f"MARKET: {ticker} @ ${ctx['price']:.2f}{change}"
    return f"MARKET: {ticker} (live price unavailable)"


def _summary_section(news: "NewsMessage") -> str:
    """Return a formatted article summary block, or empty string if none available."""
    if not news.summary:
        return ""
    return f"\n\nARTICLE SUMMARY:\n{news.summary.strip()}"


def _opinion_block(label: str, opinion: PersonaAnalysis) -> str:
    """Format one persona's opinion for inclusion in downstream prompts."""
    return (
        f"{label} [{opinion.stance}, conviction={opinion.conviction:.2f}]:\n"
        f"  Take: \"{opinion.headline_take}\"\n"
        f"  Reasoning: {opinion.analysis}"
    )


def _to_persona_opinion(name: str, pa: PersonaAnalysis, model: Optional[str] = None) -> PersonaOpinion:
    """Convert a raw LLM output (PersonaAnalysis) to the storage type (PersonaOpinion)."""
    return PersonaOpinion(
        name=name,
        stance=pa.stance,
        conviction=pa.conviction,
        view=pa.headline_take,
        reasoning=pa.analysis,
        model=model,
    )


def _dump(value: Any) -> Any:
    """Return a JSON-serializable snapshot for Pydantic models and plain values."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _messages_for_trace(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Store the exact chat messages sent to the LLM using plain strings."""
    return [
        {
            "role": str(message.get("role", "")),
            "content": str(message.get("content", "")),
        }
        for message in messages
    ]


def _llm_input_snapshot(
    news: NewsMessage,
    market_context: Optional[dict],
    prior_outputs: dict[str, Any],
) -> dict[str, Any]:
    """Structured inputs that complement the raw prompt text."""
    return {
        "news": news.model_dump(),
        "market_context": market_context,
        "prior_outputs": _dump(prior_outputs),
    }


def _llm_operation_trace(
    *,
    step: str,
    kind: str,
    response_schema: str,
    messages: list[dict[str, str]],
    input_payload: dict[str, Any],
    output: Any = None,
    model: Optional[str] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    """Build one JSONB-ready trace entry for the Decision Core."""
    return LLMOperationTrace(
        step=step,
        kind=kind,
        response_schema=response_schema,
        messages=_messages_for_trace(messages),
        input=input_payload,
        output=_dump(output) if output is not None else None,
        model=model,
        error=error,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    ).model_dump()


def _append_llm_operation(state: AgentState, operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Append an LLM trace entry while preserving earlier sequential steps."""
    return [*state.get("llm_operations", []), operation]


# ── Node Factories ───────────────────────────────────────────────────────────

def _make_check_cache_node(cache: HeadlineCache):
    def check_cache(state: AgentState) -> dict:
        """Check Redis before spending any API quota on this headline."""
        cached = cache.is_duplicate(state["news"].headline)
        if cached:
            log.info("Cache HIT — skipping duplicate: %s", state["news"].headline[:60])
        return {"is_cached": cached}
    return check_cache


def _make_fetch_context_node():
    """
    Pull live market data from Alpaca before the debate starts.

    Knowing the current price and day-change grounds the committee's analysis:
    a NVDA headline on a day where NVDA is already +8% reads very differently
    from the same headline on a flat day.

    Fails gracefully — if Alpaca is unreachable or the ticker is non-standard
    (common in simulate mode), market_context is None and each persona prompt
    falls back to "live price unavailable".
    """
    try:
        data_client = StockHistoricalDataClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
        )
    except Exception as exc:
        log.warning("Alpaca data client init failed: %s", exc)
        data_client = None

    def fetch_context(state: AgentState) -> dict:
        if data_client is None:
            return {"market_context": None}

        ticker = state["news"].ticker
        try:
            snap = data_client.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=ticker)
            ).get(ticker)

            if not snap:
                return {"market_context": None}

            price = float(snap.latest_trade.price) if snap.latest_trade else None

            day_change_pct = None
            if snap.daily_bar and snap.previous_daily_bar and snap.previous_daily_bar.close:
                prev = float(snap.previous_daily_bar.close)
                curr = float(snap.daily_bar.close)
                day_change_pct = round(((curr - prev) / prev) * 100, 2)

            ctx = {
                "price":          round(price, 2) if price is not None else None,
                "day_change_pct": day_change_pct,
            }
            log.info(
                "Context [%s]: $%.2f  %s",
                ticker,
                ctx["price"] or 0,
                f"({ctx['day_change_pct']:+.2f}%)" if ctx["day_change_pct"] is not None else "(change n/a)",
            )
            return {"market_context": ctx}

        except Exception as exc:
            log.warning("Could not fetch market context for %s: %s", ticker, exc)
            return {"market_context": None}

    return fetch_context


def _make_momentum_analyst_node(router: ModelRouter, client: Any):
    """
    LLM call #1 — the Momentum Trader.

    No prior opinions to reference. Sees only the headline and live market
    context. Produces an unconditioned, purely technical/momentum read.
    """
    def momentum_analyst(state: AgentState) -> dict:
        news = state["news"]
        prompt = (
            f"{_market_line(news.ticker, state.get('market_context'))}\n"
            f"HEADLINE: \"{news.headline}\" — {news.source}"
            f"{_summary_section(news)}\n\n"
            f"Analyze this headline's impact on {news.ticker} from your momentum trading perspective."
        )
        messages = [
            {"role": "system", "content": config.MOMENTUM_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
        input_payload = _llm_input_snapshot(
            news,
            state.get("market_context"),
            prior_outputs={},
        )

        try:
            result, model = router.call(
                client,
                PersonaAnalysis,
                messages,
            )
            log.info(
                "Momentum [%s] %s (conviction=%.2f) via %s",
                news.ticker, result.stance, result.conviction, model,
            )
            operation = _llm_operation_trace(
                step="momentum_analyst",
                kind="persona_analysis",
                response_schema="PersonaAnalysis",
                messages=messages,
                input_payload=input_payload,
                output=result,
                model=model,
            )
            return {
                "momentum_opinion": result,
                "momentum_model": model,
                "llm_operations": _append_llm_operation(state, operation),
            }

        except Exception as exc:
            log.error("Momentum analyst failed for [%s]: %s", news.ticker, exc)
            operation = _llm_operation_trace(
                step="momentum_analyst",
                kind="persona_analysis",
                response_schema="PersonaAnalysis",
                messages=messages,
                input_payload=input_payload,
                error=sanitize_llm_error(exc),
            )
            return {
                "momentum_opinion": None,
                "momentum_model": None,
                "llm_operations": _append_llm_operation(state, operation),
            }

    return momentum_analyst


def _make_value_analyst_node(router: ModelRouter, client: Any):
    """
    LLM call #2 — the Value Investor.

    Sees the momentum trader's full opinion in the prompt. This is the first
    true debate step: the value investor can agree, disagree, or nuance —
    and their response is conditioned on what the momentum trader actually said.
    """
    def value_analyst(state: AgentState) -> dict:
        news = state["news"]
        m = state.get("momentum_opinion")

        prior_section = (
            f"\n\nMOMENTUM TRADER'S TAKE:\n{_opinion_block('MOMENTUM TRADER', m)}"
            if m else "\n\n(No momentum analysis available — reason independently.)"
        )

        prompt = (
            f"{_market_line(news.ticker, state.get('market_context'))}\n"
            f"HEADLINE: \"{news.headline}\" — {news.source}"
            f"{_summary_section(news)}"
            f"{prior_section}\n\n"
            f"As the Value Investor, respond to this headline and to the Momentum Trader's assessment. "
            f"Do the fundamentals confirm or contradict their directional call?"
        )
        messages = [
            {"role": "system", "content": config.VALUE_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
        input_payload = _llm_input_snapshot(
            news,
            state.get("market_context"),
            prior_outputs={"momentum_analyst": m},
        )

        try:
            result, model = router.call(
                client,
                PersonaAnalysis,
                messages,
            )
            log.info(
                "Value     [%s] %s (conviction=%.2f) via %s",
                news.ticker, result.stance, result.conviction, model,
            )
            operation = _llm_operation_trace(
                step="value_analyst",
                kind="persona_analysis",
                response_schema="PersonaAnalysis",
                messages=messages,
                input_payload=input_payload,
                output=result,
                model=model,
            )
            return {
                "value_opinion": result,
                "value_model": model,
                "llm_operations": _append_llm_operation(state, operation),
            }

        except Exception as exc:
            log.error("Value analyst failed for [%s]: %s", news.ticker, exc)
            operation = _llm_operation_trace(
                step="value_analyst",
                kind="persona_analysis",
                response_schema="PersonaAnalysis",
                messages=messages,
                input_payload=input_payload,
                error=sanitize_llm_error(exc),
            )
            return {
                "value_opinion": None,
                "value_model": None,
                "llm_operations": _append_llm_operation(state, operation),
            }

    return value_analyst


def _make_risk_analyst_node(router: ModelRouter, client: Any):
    """
    LLM call #3 — the Risk Manager.

    Sees both prior opinions in full. Their mandate is adversarial: find the
    flaw in both arguments. A strong risk opinion with high conviction should
    suppress the synthesizer's final confidence significantly.
    """
    def risk_analyst(state: AgentState) -> dict:
        news = state["news"]
        m = state.get("momentum_opinion")
        v = state.get("value_opinion")

        debate_section = "\n\nDEBATE SO FAR:"
        if m:
            debate_section += f"\n{_opinion_block('MOMENTUM TRADER', m)}"
        if v:
            debate_section += f"\n{_opinion_block('VALUE INVESTOR', v)}"
        if not m and not v:
            debate_section += "\n(No prior analyses available — reason independently.)"

        prompt = (
            f"{_market_line(news.ticker, state.get('market_context'))}\n"
            f"HEADLINE: \"{news.headline}\" — {news.source}"
            f"{_summary_section(news)}"
            f"{debate_section}\n\n"
            f"As the Risk Manager, stress-test the above conclusions for {news.ticker}. "
            f"What tail risk, regulatory exposure, or macro factor are both analysts missing?"
        )
        messages = [
            {"role": "system", "content": config.RISK_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
        input_payload = _llm_input_snapshot(
            news,
            state.get("market_context"),
            prior_outputs={
                "momentum_analyst": m,
                "value_analyst": v,
            },
        )

        try:
            result, model = router.call(
                client,
                PersonaAnalysis,
                messages,
            )
            log.info(
                "Risk      [%s] %s (conviction=%.2f) via %s",
                news.ticker, result.stance, result.conviction, model,
            )
            operation = _llm_operation_trace(
                step="risk_analyst",
                kind="persona_analysis",
                response_schema="PersonaAnalysis",
                messages=messages,
                input_payload=input_payload,
                output=result,
                model=model,
            )
            return {
                "risk_opinion": result,
                "risk_model": model,
                "llm_operations": _append_llm_operation(state, operation),
            }

        except Exception as exc:
            log.error("Risk analyst failed for [%s]: %s", news.ticker, exc)
            operation = _llm_operation_trace(
                step="risk_analyst",
                kind="persona_analysis",
                response_schema="PersonaAnalysis",
                messages=messages,
                input_payload=input_payload,
                error=sanitize_llm_error(exc),
            )
            return {
                "risk_opinion": None,
                "risk_model": None,
                "llm_operations": _append_llm_operation(state, operation),
            }

    return risk_analyst


def _make_synthesizer_node(router: ModelRouter, client: Any):
    """
    LLM call #4 — the Portfolio Manager.

    Receives the full debate transcript. Produces a SynthesisResult, then
    assembles the final TradeAnalysis from all three PersonaOpinion objects.

    If any persona failed (opinion is None), we still synthesize on whatever
    is available — one bad API call shouldn't void the whole analysis.
    """
    def synthesizer(state: AgentState) -> dict:
        news = state["news"]
        m = state.get("momentum_opinion")
        v = state.get("value_opinion")
        r = state.get("risk_opinion")

        # If ALL three failed, we have nothing to synthesize
        if not any([m, v, r]):
            return {
                "analysis": None,
                "error": "All AI model tiers are temporarily unavailable — the system will retry on the next signal.",
            }

        def opinion_entry(label: str, op: Optional[PersonaAnalysis]) -> str:
            if op is None:
                return f"{label}: (analysis unavailable)"
            return _opinion_block(label, op)

        debate_transcript = "\n\n".join([
            opinion_entry("MOMENTUM TRADER", m),
            opinion_entry("VALUE INVESTOR",  v),
            opinion_entry("RISK MANAGER",    r),
        ])

        prompt = (
            f"{_market_line(news.ticker, state.get('market_context'))}\n"
            f"HEADLINE: \"{news.headline}\" — {news.source}"
            f"{_summary_section(news)}\n\n"
            f"FULL COMMITTEE DEBATE:\n{debate_transcript}\n\n"
            f"As the Portfolio Manager for {news.ticker}, synthesize this debate into a final "
            f"trade decision. Weight conviction scores — high-conviction dissenters matter. "
            f"Acknowledge the key tension if the committee was split."
        )
        messages = [
            {"role": "system", "content": config.SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
        input_payload = _llm_input_snapshot(
            news,
            state.get("market_context"),
            prior_outputs={
                "momentum_analyst": m,
                "value_analyst": v,
                "risk_analyst": r,
                "debate_transcript": debate_transcript,
            },
        )

        try:
            synthesis, model = router.call(
                client,
                SynthesisResult,
                messages,
            )

            # Assemble the final TradeAnalysis from all debate components.
            # Substitute a neutral placeholder for any persona whose call failed.
            def safe_opinion(name: str, pa: Optional[PersonaAnalysis], mdl: Optional[str] = None) -> PersonaOpinion:
                if pa is not None:
                    return _to_persona_opinion(name, pa, model=mdl)
                return PersonaOpinion(
                    name=name,
                    stance="NEUTRAL",
                    conviction=0.0,
                    view="Analysis unavailable for this persona.",
                    reasoning="This persona's LLM call failed; opinion not included in synthesis.",
                    model=None,
                )

            analysis = TradeAnalysis(
                committee=[
                    safe_opinion("Momentum Trader", m, state.get("momentum_model")),
                    safe_opinion("Value Investor",  v, state.get("value_model")),
                    safe_opinion("Risk Manager",    r, state.get("risk_model")),
                ],
                sentiment=synthesis.sentiment,
                confidence=synthesis.confidence,
                reasoning=synthesis.reasoning,
                action=synthesis.action,
                model=model,
            )

            log.info(
                "Synthesis [%s] action=%s  sentiment=%.2f  confidence=%.2f  via %s | "
                "committee: %s",
                news.ticker,
                analysis.action,
                analysis.sentiment,
                analysis.confidence,
                model,
                " | ".join(
                    f"{p.name.split()[0]}={p.stance}({p.conviction:.2f})"
                    for p in analysis.committee
                ),
            )
            operation = _llm_operation_trace(
                step="portfolio_manager_synthesis",
                kind="portfolio_manager_synthesis",
                response_schema="SynthesisResult",
                messages=messages,
                input_payload=input_payload,
                output=synthesis,
                model=model,
            )
            return {
                "analysis": analysis,
                "error": None,
                "llm_operations": _append_llm_operation(state, operation),
            }

        except Exception as exc:
            log.error("Synthesizer failed for [%s]: %s", news.ticker, exc)
            clean_error = sanitize_llm_error(exc)
            operation = _llm_operation_trace(
                step="portfolio_manager_synthesis",
                kind="portfolio_manager_synthesis",
                response_schema="SynthesisResult",
                messages=messages,
                input_payload=input_payload,
                error=clean_error,
            )
            return {
                "analysis": None,
                "error": clean_error,
                "llm_operations": _append_llm_operation(state, operation),
            }

    return synthesizer


def _make_assess_risk_node():
    def assess_risk(state: AgentState) -> dict:
        """
        Dual gate: requires both a strong directional signal AND high confidence.

        The committee process naturally produces lower confidence on split
        decisions (by design in the synthesizer prompt), so a 2-vs-1 debate
        result will be harder to clear this gate than a unanimous one.
        """
        if state.get("analysis") is None:
            return {
                "should_trade": False,
                "risk_gate": {
                    "step": "assess_risk",
                    "inputs": None,
                    "thresholds": {
                        "buy_sentiment": config.BUY_SENTIMENT_THRESHOLD,
                        "sell_sentiment": config.SELL_SENTIMENT_THRESHOLD,
                        "confidence": config.CONFIDENCE_THRESHOLD,
                    },
                    "should_trade": False,
                    "reason": "No valid analysis available.",
                },
            }

        a = state["analysis"]
        is_strong_buy  = a.action == "BUY"  and a.sentiment >= config.BUY_SENTIMENT_THRESHOLD
        is_strong_sell = a.action == "SELL" and a.sentiment <= config.SELL_SENTIMENT_THRESHOLD
        is_confident   = a.confidence >= config.CONFIDENCE_THRESHOLD

        should_trade = (is_strong_buy or is_strong_sell) and is_confident
        reason = "Signal passed configured sentiment and confidence gates."
        if not should_trade:
            if a.action == "HOLD":
                reason = "Portfolio Manager chose HOLD."
            elif not (is_strong_buy or is_strong_sell):
                reason = "Directional sentiment did not clear the configured threshold."
            elif not is_confident:
                reason = "Confidence did not clear the configured threshold."

        # ── SIM safety: simulated signals never trigger Alpaca orders ──
        if state.get("is_simulated", False) and should_trade:
            log.info(
                "Risk gate: BLOCKED simulated signal from trading "
                "(action=%s  sentiment=%.2f  confidence=%.2f)",
                a.action, a.sentiment, a.confidence,
            )
            should_trade = False
            reason = "Simulated signals are never sent to Alpaca."

        log.info(
            "Risk gate: should_trade=%s  (action=%s  sentiment=%.2f  confidence=%.2f)",
            should_trade, a.action, a.sentiment, a.confidence,
        )
        return {
            "should_trade": should_trade,
            "risk_gate": {
                "step": "assess_risk",
                "inputs": {
                    "action": a.action,
                    "sentiment": a.sentiment,
                    "confidence": a.confidence,
                    "is_simulated": state.get("is_simulated", False),
                },
                "thresholds": {
                    "buy_sentiment": config.BUY_SENTIMENT_THRESHOLD,
                    "sell_sentiment": config.SELL_SENTIMENT_THRESHOLD,
                    "confidence": config.CONFIDENCE_THRESHOLD,
                },
                "checks": {
                    "strong_buy": is_strong_buy,
                    "strong_sell": is_strong_sell,
                    "confident": is_confident,
                },
                "should_trade": should_trade,
                "reason": reason,
            },
        }

    return assess_risk


def _make_execute_trade_node(trader: AlpacaTrader, cache: HeadlineCache):
    def execute_trade(state: AgentState) -> dict:
        """Submit the order to Alpaca and mark the headline as seen in Redis."""
        news   = state["news"]
        action = state["analysis"].action

        # Deterministic client_order_id prevents duplicate orders if the
        # worker crashes after placing the order but before xack (SEC-04).
        client_order_id = hashlib.sha256(news.headline.encode()).hexdigest()[:36]

        order_id = trader.place_order(
            ticker=news.ticker,
            action=action,
            quantity=config.ORDER_QTY,
            client_order_id=client_order_id,
        )
        cache.mark_seen(news.headline)
        return {
            "trade_order_id": order_id,
            "execution": {
                "step": "execute_trade",
                "submitted": True,
                "ticker": news.ticker,
                "action": action,
                "quantity": config.ORDER_QTY,
                "client_order_id": client_order_id,
                "order_id": order_id,
            },
        }

    return execute_trade


def _build_decision_trace(state: AgentState) -> dict[str, Any]:
    """
    Assemble the single JSONB audit record for all Decision Core internals.

    Top-level trade columns stay as dashboard/query summaries; this payload is
    the complete expandable trace: source news, market context, every LLM call,
    committee outputs, Portfolio Manager decision, risk gate, and execution.
    """
    news = state["news"]
    analysis = state.get("analysis")
    committee = [p.model_dump() for p in analysis.committee] if analysis else []

    portfolio_manager_decision = None
    if analysis is not None:
        portfolio_manager_decision = {
            "model": analysis.model,
            "sentiment": analysis.sentiment,
            "confidence": analysis.confidence,
            "reasoning": analysis.reasoning,
            "action": analysis.action,
        }

    execution = state.get("execution") or {
        "step": "execute_trade",
        "submitted": False,
        "ticker": news.ticker,
        "action": analysis.action if analysis else "HOLD",
        "quantity": config.ORDER_QTY,
        "order_id": state.get("trade_order_id"),
        "reason": "No Alpaca order submitted.",
    }

    return {
        "schema_version": 1,
        "pipeline": "decision_core",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "news": news.model_dump(),
        "market_context": state.get("market_context"),
        "llm_operations": state.get("llm_operations", []),
        "committee_debate": committee,
        "portfolio_manager_decision": portfolio_manager_decision,
        "risk_gate": state.get("risk_gate"),
        "execution": execution,
        "error": state.get("error"),
    }


def _make_log_result_node(db: SupabaseLogger, cache: HeadlineCache):
    def log_result(state: AgentState) -> dict:
        """
        Write the full Decision Core trace to Supabase — runs for every analyzed headline.

        decision_trace is stored as JSONB containing source inputs, exact LLM
        prompts, structured outputs, the committee debate, the Portfolio Manager
        decision, risk gate details, and order execution metadata.

        Even on total failure we write a HOLD row so the signal is visible in the
        UI (with reasoning explaining why analysis was skipped).
        """
        news = state["news"]

        if state.get("analysis") is None:
            err = state.get("error", "analysis_failed")
            db.log_trade(
                ticker=news.ticker,
                headline=news.headline,
                sentiment_score=0.0,
                confidence_score=0.0,
                reasoning=f"Analysis skipped — {err}",
                trade_action="HOLD",
                is_simulated=state.get("is_simulated", False),
                article_source=news.source,
                article_url=news.article_url,
                article_id=news.article_id,
                decision_trace=_build_decision_trace(state),
            )
            cache.mark_seen(news.headline)
            return {"error": err}

        a = state["analysis"]

        db.log_trade(
            ticker=news.ticker,
            headline=news.headline,
            sentiment_score=a.sentiment,
            confidence_score=a.confidence,
            reasoning=a.reasoning,
            trade_action=a.action,
            order_id=state.get("trade_order_id"),
            quantity=config.ORDER_QTY,
            is_simulated=state.get("is_simulated", False),
            article_source=news.source,
            article_url=news.article_url,
            article_id=news.article_id,
            decision_trace=_build_decision_trace(state),
        )

        if not state.get("trade_order_id"):
            cache.mark_seen(news.headline)

        return {"error": None}

    return log_result


# ── Routing Functions ────────────────────────────────────────────────────────

def _route_after_cache_check(state: AgentState) -> str:
    return "skip" if state["is_cached"] else "fetch_context"


def _route_after_risk_assessment(state: AgentState) -> str:
    return "execute_trade" if state["should_trade"] else "log_result"


# ── Graph Assembly ───────────────────────────────────────────────────────────

def build_agent_graph(
    cache: HeadlineCache,
    trader: AlpacaTrader,
    db: SupabaseLogger,
) -> Any:
    """
    Assemble and compile the LangGraph state machine.

    The ModelRouter and LLM client are created once here and shared
    across all four persona nodes via closure — no re-initialization per message.
    """
    llm_client = create_llm_client()
    router     = ModelRouter()

    graph = StateGraph(AgentState)

    graph.add_node("check_cache",       _make_check_cache_node(cache))
    graph.add_node("fetch_context",     _make_fetch_context_node())
    graph.add_node("momentum_analyst",  _make_momentum_analyst_node(router, llm_client))
    graph.add_node("value_analyst",     _make_value_analyst_node(router, llm_client))
    graph.add_node("risk_analyst",      _make_risk_analyst_node(router, llm_client))
    graph.add_node("synthesizer",       _make_synthesizer_node(router, llm_client))
    graph.add_node("assess_risk",       _make_assess_risk_node())
    graph.add_node("execute_trade",     _make_execute_trade_node(trader, cache))
    graph.add_node("log_result",        _make_log_result_node(db, cache))

    graph.add_edge(START, "check_cache")

    graph.add_conditional_edges(
        "check_cache",
        _route_after_cache_check,
        {"skip": END, "fetch_context": "fetch_context"},
    )

    # Sequential debate chain — order is deliberate
    graph.add_edge("fetch_context",    "momentum_analyst")
    graph.add_edge("momentum_analyst", "value_analyst")
    graph.add_edge("value_analyst",    "risk_analyst")
    graph.add_edge("risk_analyst",     "synthesizer")
    graph.add_edge("synthesizer",      "assess_risk")

    graph.add_conditional_edges(
        "assess_risk",
        _route_after_risk_assessment,
        {"execute_trade": "execute_trade", "log_result": "log_result"},
    )

    graph.add_edge("execute_trade", "log_result")
    graph.add_edge("log_result",    END)

    return graph.compile()
