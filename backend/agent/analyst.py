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
from decision_rules import (
    build_execution_plan,
    committee_metrics,
    evaluate_article_quality,
    guarded_system_prompt,
    quality_prompt_block,
)
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

    news: NewsMessage
    is_cached: bool
    market_context: Optional[dict]  # {price, day_change_pct} from Alpaca
    article_quality: Optional[dict[str, Any]]
    momentum_opinion: Optional[PersonaAnalysis]
    value_opinion: Optional[PersonaAnalysis]
    risk_opinion: Optional[PersonaAnalysis]
    momentum_model: Optional[str]  # model that powered each persona
    value_model: Optional[str]
    risk_model: Optional[str]
    llm_operations: list[
        dict[str, Any]
    ]  # raw prompts/responses per Decision Core LLM call
    analysis: Optional[TradeAnalysis]  # assembled after synthesis
    should_trade: bool
    risk_gate: Optional[dict[str, Any]]
    execution_plan: Optional[dict[str, Any]]
    trade_order_id: Optional[str]
    execution: Optional[dict[str, Any]]
    error: Optional[str]
    is_simulated: bool


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


def _trading_context_section(ctx: Optional[dict]) -> str:
    """Summarize account/position context so personas stay position-aware."""
    if not ctx:
        return ""

    account = ctx.get("account") or {}
    position = ctx.get("position") or {}

    lines: list[str] = []
    if account:
        buying_power = account.get("buying_power")
        cash = account.get("cash")
        status = account.get("status")
        flags = []
        if account.get("trading_blocked"):
            flags.append("trading_blocked")
        if account.get("shorting_enabled"):
            flags.append("shorting_enabled")
        lines.append(
            "ACCOUNT: "
            f"status={status or 'unknown'}, "
            f"buying_power={buying_power if buying_power is not None else 'n/a'}, "
            f"cash={cash if cash is not None else 'n/a'}, "
            f"flags={','.join(flags) if flags else 'none'}"
        )

    if position:
        qty = position.get("qty", 0)
        side = position.get("side") or "flat"
        market_value = position.get("market_value")
        unrealized_pl = position.get("unrealized_pl")
        lines.append(
            "POSITION: "
            f"{side} qty={qty}, "
            f"market_value={market_value if market_value is not None else 'n/a'}, "
            f"unrealized_pl={unrealized_pl if unrealized_pl is not None else 'n/a'}"
        )

    return f"\n\nTRADING CONTEXT:\n" + "\n".join(lines) if lines else ""


def _opinion_block(label: str, opinion: PersonaAnalysis) -> str:
    """Format one persona's opinion for inclusion in downstream prompts."""
    return (
        f"{label} [{opinion.stance}, conviction={opinion.conviction:.2f}]:\n"
        f'  Take: "{opinion.headline_take}"\n'
        f"  Reasoning: {opinion.analysis}"
    )


def _quality_metadata(quality: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Derive storage-only quality fields without burdening the LLM schema."""
    q = quality or {}
    grade = str(q.get("grade") or "LOW")
    category = str(q.get("category") or "")
    score = q.get("score") if isinstance(q.get("score"), (int, float)) else 0.0
    reasons = [str(reason) for reason in (q.get("reasons") or [])[:3]]
    flags = set(str(flag) for flag in (q.get("flags") or []))

    if grade == "HIGH" or "hard" in category:
        catalyst = "STRONG"
    elif grade == "MEDIUM" or "soft" in category:
        catalyst = "MODERATE"
    elif score <= 0.05:
        catalyst = "NONE"
    else:
        catalyst = "WEAK"

    missing_data: list[str] = []
    if "missing_or_thin_summary" in flags:
        missing_data.append("Full Alpaca article summary or transcript details.")
    if "weak_or_broad_article" in flags:
        missing_data.append(
            "Ticker-specific catalyst rather than broad/watchlist context."
        )
    if not missing_data:
        missing_data.append(
            "Independent financial context beyond the supplied Alpaca headline/summary."
        )

    return {
        "catalyst_strength": catalyst,
        "evidence_quality": grade,
        "time_horizon": "INTRADAY" if catalyst in {"STRONG", "MODERATE"} else "UNKNOWN",
        "key_evidence": reasons,
        "missing_data": missing_data,
    }


def _to_persona_opinion(
    name: str,
    pa: PersonaAnalysis,
    model: Optional[str] = None,
    quality: Optional[dict[str, Any]] = None,
) -> PersonaOpinion:
    """Convert a raw LLM output (PersonaAnalysis) to the storage type (PersonaOpinion)."""
    return PersonaOpinion(
        name=name,
        stance=pa.stance,
        conviction=pa.conviction,
        view=pa.headline_take,
        reasoning=pa.analysis,
        model=model,
        **_quality_metadata(quality),
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


def _append_llm_operation(
    state: AgentState, operation: dict[str, Any]
) -> list[dict[str, Any]]:
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


def _make_fetch_context_node(trader: AlpacaTrader):
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
        news = state["news"]
        article_quality = evaluate_article_quality(news)

        account_context = trader.get_account_context()
        position_context = trader.get_position_context(news.ticker)

        if data_client is None:
            return {
                "market_context": {
                    "price": None,
                    "day_change_pct": None,
                    "account": account_context,
                    "position": position_context,
                },
                "article_quality": article_quality.to_dict(),
            }

        ticker = news.ticker
        try:
            snap = data_client.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=ticker)
            ).get(ticker)

            if not snap:
                return {
                    "market_context": {
                        "price": None,
                        "day_change_pct": None,
                        "account": account_context,
                        "position": position_context,
                    },
                    "article_quality": article_quality.to_dict(),
                }

            price = float(snap.latest_trade.price) if snap.latest_trade else None

            day_change_pct = None
            if (
                snap.daily_bar
                and snap.previous_daily_bar
                and snap.previous_daily_bar.close
            ):
                prev = float(snap.previous_daily_bar.close)
                curr = float(snap.daily_bar.close)
                day_change_pct = round(((curr - prev) / prev) * 100, 2)

            ctx = {
                "price": round(price, 2) if price is not None else None,
                "day_change_pct": day_change_pct,
                "account": account_context,
                "position": position_context,
            }
            log.info(
                "Context [%s]: $%.2f  %s",
                ticker,
                ctx["price"] or 0,
                (
                    f"({ctx['day_change_pct']:+.2f}%)"
                    if ctx["day_change_pct"] is not None
                    else "(change n/a)"
                ),
            )
            return {"market_context": ctx, "article_quality": article_quality.to_dict()}

        except Exception as exc:
            log.warning("Could not fetch market context for %s: %s", ticker, exc)
            return {
                "market_context": {
                    "price": None,
                    "day_change_pct": None,
                    "account": account_context,
                    "position": position_context,
                },
                "article_quality": article_quality.to_dict(),
            }

    return fetch_context


def _make_pre_screen_node():
    """
    Deterministically hold low-quality articles before spending LLM quota.

    Weak transcript/watchlist/radar headlines were the biggest source of noisy
    LLM failures. The quality gate is deterministic, auditable, and still
    writes a full HOLD trace through assess_risk/log_result.
    """

    def pre_screen(state: AgentState) -> dict:
        news = state["news"]
        quality = (
            state.get("article_quality") or evaluate_article_quality(news).to_dict()
        )
        score = quality.get("score", 0.0)

        if isinstance(score, (int, float)) and score >= 0.48:
            return {"article_quality": quality}

        metadata = _quality_metadata(quality)
        grade = quality.get("grade", "LOW")
        category = quality.get("category", "low-quality article")
        reasons = quality.get("reasons") or [
            "No concrete article-specific trading catalyst detected."
        ]
        reason_text = " ".join(str(reason) for reason in reasons)

        committee = [
            PersonaOpinion(
                name="Momentum Trader",
                stance="NEUTRAL",
                conviction=0.20,
                view="Source quality is too weak for a momentum trade.",
                reasoning=f"The article is graded {grade} ({category}). {reason_text}",
                model="deterministic-pre-screen",
                **metadata,
            ),
            PersonaOpinion(
                name="Value Investor",
                stance="NEUTRAL",
                conviction=0.20,
                view="No source-backed fundamentals justify a valuation call.",
                reasoning="The supplied article lacks enough ticker-specific financial evidence for a fundamental thesis.",
                model="deterministic-pre-screen",
                **metadata,
            ),
            PersonaOpinion(
                name="Risk Manager",
                stance="NEUTRAL",
                conviction=0.25,
                view="Low-quality input is a data-risk issue, not a trade signal.",
                reasoning="The safest action is to avoid an LLM-amplified call when the source lacks a concrete catalyst.",
                model="deterministic-pre-screen",
                **metadata,
            ),
        ]

        analysis = TradeAnalysis(
            committee=committee,
            sentiment=0.0,
            confidence=min(
                0.35, float(score) if isinstance(score, (int, float)) else 0.0
            ),
            reasoning=f"Pre-screened as HOLD: {grade} source quality with no executable catalyst. {reason_text}",
            action="HOLD",
            model="deterministic-pre-screen",
            thesis_quality="WEAK",
            primary_risk="The article may omit material facts, so the system refused to infer a trade from thin evidence.",
        )

        log.info(
            "Pre-screen [%s]: HOLD low-quality article grade=%s score=%.2f",
            news.ticker,
            grade,
            float(score) if isinstance(score, (int, float)) else 0.0,
        )
        return {"analysis": analysis, "article_quality": quality}

    return pre_screen


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
            f'HEADLINE: "{news.headline}" — {news.source}'
            f"{_summary_section(news)}"
            f"{_trading_context_section(state.get('market_context'))}"
            f"{quality_prompt_block(state.get('article_quality'))}\n\n"
            f"Analyze this headline's impact on {news.ticker} from your momentum trading perspective."
        )
        messages = [
            {
                "role": "system",
                "content": guarded_system_prompt(
                    config.MOMENTUM_SYSTEM_PROMPT, "momentum"
                ),
            },
            {"role": "user", "content": prompt},
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
                news.ticker,
                result.stance,
                result.conviction,
                model,
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
            if m
            else "\n\n(No momentum analysis available — reason independently.)"
        )

        prompt = (
            f"{_market_line(news.ticker, state.get('market_context'))}\n"
            f'HEADLINE: "{news.headline}" — {news.source}'
            f"{_summary_section(news)}"
            f"{_trading_context_section(state.get('market_context'))}"
            f"{quality_prompt_block(state.get('article_quality'))}"
            f"{prior_section}\n\n"
            f"As the Value Investor, respond to this headline and to the Momentum Trader's assessment. "
            f"Do the fundamentals confirm or contradict their directional call?"
        )
        messages = [
            {
                "role": "system",
                "content": guarded_system_prompt(config.VALUE_SYSTEM_PROMPT, "value"),
            },
            {"role": "user", "content": prompt},
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
                news.ticker,
                result.stance,
                result.conviction,
                model,
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
            f'HEADLINE: "{news.headline}" — {news.source}'
            f"{_summary_section(news)}"
            f"{_trading_context_section(state.get('market_context'))}"
            f"{quality_prompt_block(state.get('article_quality'))}"
            f"{debate_section}\n\n"
            f"As the Risk Manager, stress-test the above conclusions for {news.ticker}. "
            f"What concrete tail risk, regulatory exposure, or macro factor are both analysts missing? "
            f"If the risk is generic rather than article-specific, say so and keep stance NEUTRAL."
        )
        messages = [
            {
                "role": "system",
                "content": guarded_system_prompt(config.RISK_SYSTEM_PROMPT, "risk"),
            },
            {"role": "user", "content": prompt},
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
                news.ticker,
                result.stance,
                result.conviction,
                model,
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

        debate_transcript = "\n\n".join(
            [
                opinion_entry("MOMENTUM TRADER", m),
                opinion_entry("VALUE INVESTOR", v),
                opinion_entry("RISK MANAGER", r),
            ]
        )

        prompt = (
            f"{_market_line(news.ticker, state.get('market_context'))}\n"
            f'HEADLINE: "{news.headline}" — {news.source}'
            f"{_summary_section(news)}"
            f"{_trading_context_section(state.get('market_context'))}"
            f"{quality_prompt_block(state.get('article_quality'))}\n\n"
            f"FULL COMMITTEE DEBATE:\n{debate_transcript}\n\n"
            f"As the Portfolio Manager for {news.ticker}, synthesize this debate into a final "
            f"trade decision. Weight conviction scores — high-conviction dissenters matter. "
            f"Acknowledge the key tension if the committee was split. "
            f"Recommend BUY/SELL only for concrete, source-backed catalysts; otherwise HOLD."
        )
        messages = [
            {
                "role": "system",
                "content": guarded_system_prompt(
                    config.SYNTHESIS_SYSTEM_PROMPT, "synthesis"
                ),
            },
            {"role": "user", "content": prompt},
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
            quality = state.get("article_quality")

            def safe_opinion(
                name: str, pa: Optional[PersonaAnalysis], mdl: Optional[str] = None
            ) -> PersonaOpinion:
                if pa is not None:
                    return _to_persona_opinion(name, pa, model=mdl, quality=quality)
                return PersonaOpinion(
                    name=name,
                    stance="NEUTRAL",
                    conviction=0.0,
                    view="Analysis unavailable for this persona.",
                    reasoning="This persona's LLM call failed; opinion not included in synthesis.",
                    model=None,
                    **_quality_metadata(quality),
                )

            analysis = TradeAnalysis(
                committee=[
                    safe_opinion("Momentum Trader", m, state.get("momentum_model")),
                    safe_opinion("Value Investor", v, state.get("value_model")),
                    safe_opinion("Risk Manager", r, state.get("risk_model")),
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
        Execution gate: requires a good thesis, calibrated confidence, valid
        account state, and a position-aware order plan.
        """
        article_quality = (
            state.get("article_quality")
            or evaluate_article_quality(state["news"]).to_dict()
        )

        if state.get("analysis") is None:
            return {
                "should_trade": False,
                "execution_plan": build_execution_plan(
                    action="HOLD",
                    order_qty=config.ORDER_QTY,
                    market_context=state.get("market_context"),
                ),
                "risk_gate": {
                    "step": "assess_risk",
                    "inputs": None,
                    "article_quality": article_quality,
                    "thresholds": {
                        "buy_sentiment": config.BUY_SENTIMENT_THRESHOLD,
                        "sell_sentiment": config.SELL_SENTIMENT_THRESHOLD,
                        "confidence": config.CONFIDENCE_THRESHOLD,
                        "effective_confidence": min(config.CONFIDENCE_THRESHOLD, 0.80),
                    },
                    "should_trade": False,
                    "reason": "No valid analysis available.",
                },
            }

        a = state["analysis"]
        metrics = committee_metrics(a, article_quality, state.get("market_context"))
        plan = build_execution_plan(
            action=a.action,
            order_qty=config.ORDER_QTY,
            market_context=state.get("market_context"),
        )

        is_strong_buy = (
            a.action == "BUY" and a.sentiment >= config.BUY_SENTIMENT_THRESHOLD
        )
        is_strong_sell = (
            a.action == "SELL" and a.sentiment <= config.SELL_SENTIMENT_THRESHOLD
        )
        effective_confidence_threshold = min(config.CONFIDENCE_THRESHOLD, 0.80)
        is_confident = (
            metrics["calibrated_confidence"] >= effective_confidence_threshold
        )
        quality_ok = article_quality.get("score", 0.0) >= 0.48
        plan_ok = len(plan["blocked_reasons"]) == 0

        blockers: list[str] = []
        if a.action == "HOLD":
            blockers.append("Portfolio Manager chose HOLD.")
        if a.action != "HOLD" and not (is_strong_buy or is_strong_sell):
            blockers.append(
                "Directional sentiment did not clear the configured threshold."
            )
        if a.action != "HOLD" and not is_confident:
            blockers.append(
                "Calibrated confidence did not clear the effective execution threshold."
            )
        if a.action != "HOLD" and not quality_ok:
            blockers.append("Article quality is too weak/broad for execution.")
        blockers.extend(plan["blocked_reasons"])

        should_trade = (
            (is_strong_buy or is_strong_sell)
            and is_confident
            and quality_ok
            and plan_ok
        )
        reason = (
            "Signal passed thesis, source-quality, account, and execution-plan gates."
        )
        if blockers:
            if a.action == "HOLD":
                reason = "Portfolio Manager chose HOLD."
            else:
                reason = " ".join(dict.fromkeys(blockers))

        # ── SIM safety: simulated signals never trigger Alpaca orders ──
        if state.get("is_simulated", False) and should_trade:
            log.info(
                "Risk gate: BLOCKED simulated signal from trading "
                "(action=%s  sentiment=%.2f  confidence=%.2f)",
                a.action,
                a.sentiment,
                a.confidence,
            )
            should_trade = False
            reason = "Simulated signals are never sent to Alpaca."
            blockers.append(reason)

        log.info(
            "Risk gate: should_trade=%s  (action=%s  sentiment=%.2f  confidence=%.2f→%.2f  quality=%s %.2f)",
            should_trade,
            a.action,
            a.sentiment,
            a.confidence,
            metrics["calibrated_confidence"],
            article_quality.get("grade"),
            article_quality.get("score", 0.0),
        )
        return {
            "should_trade": should_trade,
            "execution_plan": plan,
            "risk_gate": {
                "step": "assess_risk",
                "inputs": {
                    "action": a.action,
                    "sentiment": a.sentiment,
                    "confidence": a.confidence,
                    "calibrated_confidence": metrics["calibrated_confidence"],
                    "is_simulated": state.get("is_simulated", False),
                },
                "article_quality": article_quality,
                "committee_metrics": metrics,
                "execution_plan": plan,
                "thresholds": {
                    "buy_sentiment": config.BUY_SENTIMENT_THRESHOLD,
                    "sell_sentiment": config.SELL_SENTIMENT_THRESHOLD,
                    "confidence": config.CONFIDENCE_THRESHOLD,
                    "effective_confidence": effective_confidence_threshold,
                },
                "checks": {
                    "strong_buy": is_strong_buy,
                    "strong_sell": is_strong_sell,
                    "confident": is_confident,
                    "quality_ok": quality_ok,
                    "execution_plan_ok": plan_ok,
                },
                "should_trade": should_trade,
                "blockers": list(dict.fromkeys(blockers)),
                "reason": reason,
            },
        }

    return assess_risk


def _make_execute_trade_node(trader: AlpacaTrader, cache: HeadlineCache):
    def execute_trade(state: AgentState) -> dict:
        """Submit the order to Alpaca."""
        news = state["news"]
        action = state["analysis"].action
        plan = state.get("execution_plan") or {}
        quantity = int(plan.get("quantity") or config.ORDER_QTY)

        # Deterministic client_order_id prevents duplicate orders if the
        # worker crashes after placing the order but before xack (SEC-04).
        idempotency_seed = "|".join(
            [
                news.article_id or "",
                news.article_url or "",
                news.ticker,
                news.published_at,
                news.headline,
                action,
            ]
        )
        client_order_id = hashlib.sha256(idempotency_seed.encode()).hexdigest()[:36]

        result = trader.place_order(
            ticker=news.ticker,
            action=action,
            quantity=quantity,
            client_order_id=client_order_id,
        )
        return {
            "trade_order_id": result.order_id,
            "execution": {
                "step": "execute_trade",
                "submitted": result.submitted,
                "ticker": news.ticker,
                "action": action,
                "quantity": quantity,
                "client_order_id": client_order_id,
                "order_id": result.order_id,
                "status": result.status,
                "error": result.error,
                "execution_plan": plan,
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
            "thesis_quality": analysis.thesis_quality,
            "primary_risk": analysis.primary_risk,
        }

    plan = state.get("execution_plan") or {}
    execution = state.get("execution") or {
        "step": "execute_trade",
        "submitted": False,
        "ticker": news.ticker,
        "action": analysis.action if analysis else "HOLD",
        "quantity": plan.get("quantity", config.ORDER_QTY),
        "order_id": state.get("trade_order_id"),
        "execution_plan": plan,
        "reason": "No Alpaca order submitted.",
    }

    return {
        "schema_version": 2,
        "pipeline": "decision_core",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "news": news.model_dump(),
        "market_context": state.get("market_context"),
        "article_quality": state.get("article_quality"),
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
        plan = state.get("execution_plan") or {}

        db.log_trade(
            ticker=news.ticker,
            headline=news.headline,
            sentiment_score=a.sentiment,
            confidence_score=a.confidence,
            reasoning=a.reasoning,
            trade_action=a.action,
            order_id=state.get("trade_order_id"),
            quantity=int(plan.get("quantity") or config.ORDER_QTY),
            is_simulated=state.get("is_simulated", False),
            article_source=news.source,
            article_url=news.article_url,
            article_id=news.article_id,
            decision_trace=_build_decision_trace(state),
        )
        cache.mark_seen(news.headline)

        return {"error": None}

    return log_result


# ── Routing Functions ────────────────────────────────────────────────────────


def _route_after_cache_check(state: AgentState) -> str:
    return "skip" if state["is_cached"] else "fetch_context"


def _route_after_pre_screen(state: AgentState) -> str:
    return "assess_risk" if state.get("analysis") is not None else "momentum_analyst"


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
    router = ModelRouter()

    graph = StateGraph(AgentState)

    graph.add_node("check_cache", _make_check_cache_node(cache))
    graph.add_node("fetch_context", _make_fetch_context_node(trader))
    graph.add_node("pre_screen", _make_pre_screen_node())
    graph.add_node("momentum_analyst", _make_momentum_analyst_node(router, llm_client))
    graph.add_node("value_analyst", _make_value_analyst_node(router, llm_client))
    graph.add_node("risk_analyst", _make_risk_analyst_node(router, llm_client))
    graph.add_node("synthesizer", _make_synthesizer_node(router, llm_client))
    graph.add_node("assess_risk", _make_assess_risk_node())
    graph.add_node("execute_trade", _make_execute_trade_node(trader, cache))
    graph.add_node("log_result", _make_log_result_node(db, cache))

    graph.add_edge(START, "check_cache")

    graph.add_conditional_edges(
        "check_cache",
        _route_after_cache_check,
        {"skip": END, "fetch_context": "fetch_context"},
    )

    # Sequential debate chain — order is deliberate
    graph.add_edge("fetch_context", "pre_screen")
    graph.add_conditional_edges(
        "pre_screen",
        _route_after_pre_screen,
        {"assess_risk": "assess_risk", "momentum_analyst": "momentum_analyst"},
    )
    graph.add_edge("momentum_analyst", "value_analyst")
    graph.add_edge("value_analyst", "risk_analyst")
    graph.add_edge("risk_analyst", "synthesizer")
    graph.add_edge("synthesizer", "assess_risk")

    graph.add_conditional_edges(
        "assess_risk",
        _route_after_risk_assessment,
        {"execute_trade": "execute_trade", "log_result": "log_result"},
    )

    graph.add_edge("execute_trade", "log_result")
    graph.add_edge("log_result", END)

    return graph.compile()
