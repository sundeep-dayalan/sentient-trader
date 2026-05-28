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
import time
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
    RiskAssessment,
    SynthesisResult,
    TradeAnalysis,
)
from trader import AlpacaTrader

log = logging.getLogger("agent.analyst")

ARTICLE_EXECUTION_SCORE_FLOOR = 0.60
LOG_FIELD_LIMIT = 240


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
    all_positions: Optional[list[dict]]  # all Alpaca positions for concentration checks
    momentum_opinion: Optional[PersonaAnalysis]
    value_opinion: Optional[PersonaAnalysis]
    risk_opinion: Optional[RiskAssessment]
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
    processing_started_at: Optional[str]


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
        unrealized_plpc = position.get("unrealized_plpc")
        avg_entry = position.get("avg_entry_price")
        lines.append(
            "POSITION: "
            f"{side} qty={qty}, "
            f"market_value={market_value if market_value is not None else 'n/a'}, "
            f"unrealized_pl={unrealized_pl if unrealized_pl is not None else 'n/a'}"
        )
        # P&L awareness for existing positions
        if (
            unrealized_pl is not None
            and unrealized_plpc is not None
            and avg_entry is not None
            and float(qty or 0) != 0
        ):
            try:
                pl_val = float(unrealized_pl)
                pl_pct = float(unrealized_plpc) * 100
                lines.append(
                    f"POSITION P&L: entry=${float(avg_entry):.2f}, "
                    f"unrealized={'+' if pl_val >= 0 else ''}${pl_val:.2f} "
                    f"({'+' if pl_pct >= 0 else ''}{pl_pct:.1f}%)"
                )
            except (TypeError, ValueError):
                pass

    # Technical indicators (config-gated)
    tech = ctx.get("technical_indicators")
    if tech:
        try:
            from market_intelligence import format_technical_prompt_block
            tech_block = format_technical_prompt_block(tech)
            if tech_block:
                lines.append(tech_block)
        except Exception:
            pass

    return f"\n\nTRADING CONTEXT:\n" + "\n".join(lines) if lines else ""


def _log_text(value: Any, *, limit: int = LOG_FIELD_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _log_list(values: Any, *, limit: int = 4) -> str:
    if not values:
        return "none"
    if not isinstance(values, list):
        values = [values]
    items = [_log_text(item, limit=160) for item in values if str(item or "").strip()]
    if not items:
        return "none"
    suffix = f"; +{len(items) - limit} more" if len(items) > limit else ""
    return "; ".join(items[:limit]) + suffix


def _log_signal_input(news: NewsMessage) -> None:
    log.info(
        'Signal input [%s]: source=%s simulated=%s article_id=%s published_at=%s '
        'summary_chars=%d url=%s headline="%s"',
        news.ticker,
        news.source or "unknown",
        news.is_simulated,
        news.article_id or "n/a",
        news.published_at or "n/a",
        len(news.summary or ""),
        "yes" if news.article_url else "no",
        _log_text(news.headline),
    )


def _log_article_quality(ticker: str, quality: dict[str, Any], *, label: str) -> None:
    log.info(
        "%s [%s]: grade=%s score=%.2f floor=%.2f category=%s has_summary=%s "
        "flags=%s reasons=%s",
        label,
        ticker,
        quality.get("grade", "LOW"),
        float(quality.get("score", 0.0) or 0.0),
        ARTICLE_EXECUTION_SCORE_FLOOR,
        quality.get("category") or "unknown",
        quality.get("has_summary", False),
        _log_list(quality.get("flags")),
        _log_list(quality.get("reasons")),
    )


def _opinion_block(label: str, opinion: PersonaAnalysis) -> str:
    """Format one persona's opinion for inclusion in downstream prompts."""
    return (
        f"{label} [{opinion.stance}, conviction={opinion.conviction:.2f}]:\n"
        f'  Take: "{opinion.headline_take}"\n'
        f"  Reasoning: {opinion.analysis}"
    )


def _risk_assessment_block(label: str, risk: RiskAssessment) -> str:
    """Format the risk manager's non-directional assessment for prompts."""
    blockers = (
        "; ".join(risk.disqualifying_conditions)
        if risk.disqualifying_conditions
        else "none"
    )
    return (
        f"{label} [risk={risk.risk_level}, score={risk.risk_score:.2f}, "
        f"confidence_cap={risk.confidence_cap:.2f}]:\n"
        f'  Take: "{risk.headline_take}"\n'
        f"  Disqualifying conditions: {blockers}\n"
        f"  Reasoning: {risk.analysis}"
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


def _to_risk_persona_opinion(
    risk: RiskAssessment,
    model: Optional[str] = None,
    quality: Optional[dict[str, Any]] = None,
) -> PersonaOpinion:
    """Store risk as a neutral risk card instead of a directional vote."""
    return PersonaOpinion(
        name="Risk Manager",
        stance="NEUTRAL",
        conviction=risk.risk_score,
        view=risk.headline_take,
        reasoning=risk.analysis,
        model=model,
        risk_level=risk.risk_level,
        risk_confidence_cap=risk.confidence_cap,
        disqualifying_conditions=risk.disqualifying_conditions,
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
        news = state["news"]
        cached = cache.is_duplicate(
            news.headline,
            ticker=news.ticker,
            article_id=news.article_id,
        )
        if cached:
            log.info("Cache HIT — skipping duplicate: %s %s", news.ticker, news.headline[:60])
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
        _log_signal_input(news)
        _log_article_quality(
            news.ticker,
            article_quality.to_dict(),
            label="Article quality",
        )

        account_context = trader.get_account_context()
        position_context = trader.get_position_context(news.ticker)
        all_positions = trader.get_all_positions()

        if data_client is None:
            return {
                "market_context": {
                    "price": None,
                    "day_change_pct": None,
                    "account": account_context,
                    "position": position_context,
                },
                "article_quality": article_quality.to_dict(),
                "all_positions": all_positions,
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

            # Technical indicators enrichment (config-gated)
            try:
                if config.TECHNICAL_INDICATORS_ENABLED and snap:
                    from alpaca.data.requests import StockBarsRequest
                    from alpaca.data.timeframe import TimeFrame
                    from market_intelligence import build_technical_context

                    try:
                        bars = data_client.get_stock_bars(
                            StockBarsRequest(
                                symbol_or_symbols=ticker,
                                timeframe=TimeFrame.Hour,
                                start=datetime.now(timezone.utc) - __import__('datetime').timedelta(days=5),
                            )
                        )
                        bar_list = bars.get(ticker) if bars else None
                        if bar_list and len(bar_list) >= 5:
                            closes = [float(b.close) for b in bar_list]
                            volumes = [float(b.volume) for b in bar_list]
                            tech = build_technical_context(closes, volumes, price)
                            if tech:
                                ctx["technical_indicators"] = tech
                                log.info(
                                    "Technical indicators [%s]: RSI=%s MACD=%s VR=%s",
                                    ticker,
                                    tech.get("rsi_14"),
                                    tech.get("macd"),
                                    tech.get("volume_ratio"),
                                )
                    except Exception as tech_exc:
                        log.debug("Technical indicators unavailable for %s: %s", ticker, tech_exc)
            except Exception:
                pass

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
            return {"market_context": ctx, "article_quality": article_quality.to_dict(), "all_positions": all_positions}

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
                "all_positions": all_positions,
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

        if isinstance(score, (int, float)) and score >= ARTICLE_EXECUTION_SCORE_FLOOR:
            log.info(
                "Pre-screen [%s]: PASS to LLM debate grade=%s score=%.2f floor=%.2f "
                "category=%s flags=%s reasons=%s",
                news.ticker,
                quality.get("grade", "UNKNOWN"),
                float(score),
                ARTICLE_EXECUTION_SCORE_FLOOR,
                quality.get("category") or "unknown",
                _log_list(quality.get("flags")),
                _log_list(quality.get("reasons")),
            )
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

        logged_score = float(score) if isinstance(score, (int, float)) else 0.0
        log.info(
            "Pre-screen [%s]: HOLD before LLM grade=%s score=%.2f floor=%.2f "
            "category=%s flags=%s reasons=%s",
            news.ticker,
            grade,
            logged_score,
            ARTICLE_EXECUTION_SCORE_FLOOR,
            category,
            _log_list(quality.get("flags")),
            _log_list(reasons),
        )
        log.info(
            "Pre-screen [%s]: LLM skipped model=deterministic-pre-screen "
            "committee=3xNEUTRAL sentiment=0.00 confidence=%.2f reason=%s",
            news.ticker,
            analysis.confidence,
            _log_text(analysis.reasoning),
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
            # Graceful fallback: return a neutral stance rather than None
            fallback = PersonaAnalysis(
                stance="NEUTRAL",
                conviction=0.30,
                analysis="LLM call failed; defaulting to neutral stance with low conviction for safety.",
                headline_take="Unable to analyze; recommending caution.",
            )
            return {
                "momentum_opinion": fallback,
                "momentum_model": "fallback-deterministic",
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

    Sees both prior opinions in full. The risk role is not a directional voter;
    it identifies concrete execution risks and caps confidence when needed.
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
            f"As the Risk Manager, produce a non-directional execution risk assessment for {news.ticker}. "
            f"Do not answer BUY, SELL, BULLISH, or BEARISH. Identify concrete tail risk, regulatory exposure, "
            f"account/position constraint, stale-data issue, or source-quality issue that should cap or block execution. "
            f"If risk is generic rather than article-specific, set risk_level LOW or MEDIUM and leave "
            f"disqualifying_conditions empty."
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
                RiskAssessment,
                messages,
            )
            log.info(
                "Risk      [%s] level=%s score=%.2f cap=%.2f blockers=%s via %s",
                news.ticker,
                result.risk_level,
                result.risk_score,
                result.confidence_cap,
                _log_list(result.disqualifying_conditions),
                model,
            )
            operation = _llm_operation_trace(
                step="risk_analyst",
                kind="persona_analysis",
                response_schema="RiskAssessment",
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
                response_schema="RiskAssessment",
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

        def risk_entry(op: Optional[RiskAssessment]) -> str:
            if op is None:
                return "RISK MANAGER: (analysis unavailable)"
            return _risk_assessment_block("RISK MANAGER", op)

        debate_transcript = "\n\n".join(
            [
                opinion_entry("MOMENTUM TRADER", m),
                opinion_entry("VALUE INVESTOR", v),
                risk_entry(r),
            ]
        )

        prompt = (
            f"{_market_line(news.ticker, state.get('market_context'))}\n"
            f'HEADLINE: "{news.headline}" — {news.source}'
            f"{_summary_section(news)}"
            f"{_trading_context_section(state.get('market_context'))}"
            f"{quality_prompt_block(state.get('article_quality'))}\n\n"
            f"FULL COMMITTEE DEBATE:\n{debate_transcript}\n\n"
        )

        # Signal momentum context (config-gated)
        try:
            if config.SIGNAL_MOMENTUM_ENABLED:
                from market_intelligence import SignalMomentumTracker
                from redis_client import create_redis_client
                tracker = SignalMomentumTracker(create_redis_client())
                momentum_block = tracker.format_momentum_prompt(news.ticker)
                if momentum_block:
                    prompt += f"{momentum_block}\n\n"
        except Exception:
            pass

        # Source credibility note (config-gated)
        try:
            if config.SOURCE_CREDIBILITY_ENABLED:
                from source_credibility import source_credibility_prompt_note
                cred_note = source_credibility_prompt_note(news.source)
                if cred_note:
                    prompt += f"{cred_note}\n\n"
        except Exception:
            pass

        # Historical feedback loop (config-gated)
        feedback_note = ""
        try:
            if config.FEEDBACK_LOOP_ENABLED:
                from feedback_loop import compute_historical_accuracy, query_recent_outcomes
                from supabase import create_client as _fb_create_client
                from supabase.client import ClientOptions as _FBClientOptions
                _fb_client = _fb_create_client(
                    supabase_url=os.environ["SUPABASE_URL"],
                    supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
                    options=_FBClientOptions(
                        schema=os.environ.get("SUPABASE_DB_SCHEMA", "public"),
                    ),
                )
                # Check accuracy for the dominant action
                for check_action in ["BUY", "SELL"]:
                    outcomes = query_recent_outcomes(
                        _fb_client, news.ticker, check_action,
                        days=config.FEEDBACK_LOOP_LOOKBACK_DAYS,
                    )
                    if outcomes:
                        accuracy = compute_historical_accuracy(outcomes, check_action)
                        if accuracy.prompt_note:
                            feedback_note += f"{accuracy.prompt_note}\n"
                if feedback_note:
                    prompt += f"{feedback_note}\n"
        except Exception:
            pass

        # Structured synthesis framework (config-gated)
        if config.STRUCTURED_SYNTHESIS_ENABLED:
            prompt += (
                f"As the Portfolio Manager for {news.ticker}, synthesize this debate into a final "
                f"trade decision. Before deciding BUY/SELL/HOLD, explicitly evaluate:\n"
                f"1. CATALYST CLARITY: Is the catalyst specific and actionable, or vague?\n"
                f"2. TIMING: Has the price already moved? Is this news priced in?\n"
                f"3. POSITION CONTEXT: Are we adding to an existing position or opening new?\n"
                f"4. RISK-REWARD: What's the upside target vs downside risk?\n"
                f"5. CONVICTION ALIGNMENT: Do the committee members agree on direction?\n\n"
                f"Only recommend BUY/SELL if items 1, 4, and 5 are clearly favorable. "
                f"Weight the momentum/value directional views, then apply the Risk "
                f"Manager's level, confidence cap, and disqualifying conditions as execution constraints. "
                f"Acknowledge the key tension if the committee was split or risk-capped."
            )
        else:
            prompt += (
                f"As the Portfolio Manager for {news.ticker}, synthesize this debate into a final "
                f"trade decision. Weight the momentum/value directional views, then apply the Risk "
                f"Manager's level, confidence cap, and disqualifying conditions as execution constraints. "
                f"Acknowledge the key tension if the committee was split or risk-capped. "
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

            def safe_risk_opinion(
                pa: Optional[RiskAssessment], mdl: Optional[str] = None
            ) -> PersonaOpinion:
                if pa is not None:
                    return _to_risk_persona_opinion(pa, model=mdl, quality=quality)
                return PersonaOpinion(
                    name="Risk Manager",
                    stance="NEUTRAL",
                    conviction=0.0,
                    view="Risk assessment unavailable.",
                    reasoning="The risk manager LLM call failed; no risk cap beyond deterministic gates was added.",
                    model=None,
                    risk_level="MEDIUM",
                    risk_confidence_cap=None,
                    disqualifying_conditions=[],
                    **_quality_metadata(quality),
                )

            analysis = TradeAnalysis(
                committee=[
                    safe_opinion("Momentum Trader", m, state.get("momentum_model")),
                    safe_opinion("Value Investor", v, state.get("value_model")),
                    safe_risk_opinion(r, state.get("risk_model")),
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
        news = state["news"]
        article_quality = (
            state.get("article_quality")
            or evaluate_article_quality(state["news"]).to_dict()
        )

        if state.get("analysis") is None:
            log.info(
                "Risk gate [%s]: BLOCK no valid analysis available; defaulting to HOLD",
                news.ticker,
            )
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

        # Use positions fetched in fetch_context for concentration checks
        all_positions: list[dict] = state.get("all_positions") or []

        # Apply feedback loop confidence adjustment (config-gated)
        feedback_adjustment = 0.0
        try:
            if config.FEEDBACK_LOOP_ENABLED:
                from feedback_loop import compute_historical_accuracy, query_recent_outcomes
                from supabase import create_client as _fb_create_client
                from supabase.client import ClientOptions as _FBClientOptions
                _fb_client = _fb_create_client(
                    supabase_url=os.environ["SUPABASE_URL"],
                    supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
                    options=_FBClientOptions(
                        schema=os.environ.get("SUPABASE_DB_SCHEMA", "public"),
                    ),
                )
                outcomes = query_recent_outcomes(
                    _fb_client, news.ticker, a.action,
                    days=config.FEEDBACK_LOOP_LOOKBACK_DAYS,
                )
                if outcomes:
                    accuracy = compute_historical_accuracy(outcomes, a.action)
                    feedback_adjustment = accuracy.confidence_adjustment
                    if feedback_adjustment != 0.0:
                        log.info(
                            "Feedback loop [%s]: adjusting calibrated confidence by %+.4f "
                            "(win_rate_1h=%s, %d signals)",
                            news.ticker,
                            feedback_adjustment,
                            accuracy.win_rate_1h,
                            accuracy.total_signals,
                        )
        except Exception as fb_exc:
            log.debug("Feedback loop adjustment unavailable: %s", fb_exc)

        # Apply the feedback adjustment to calibrated confidence
        calibrated_with_feedback = max(
            0.0,
            min(1.0, metrics["calibrated_confidence"] + feedback_adjustment),
        )
        if feedback_adjustment != 0.0:
            metrics["calibrated_confidence_before_feedback"] = metrics["calibrated_confidence"]
            metrics["feedback_adjustment"] = feedback_adjustment
            metrics["calibrated_confidence"] = round(calibrated_with_feedback, 4)

        plan = build_execution_plan(
            action=a.action,
            order_qty=config.ORDER_QTY,
            market_context=state.get("market_context"),
            calibrated_confidence=metrics.get("calibrated_confidence", 0.0),
            thesis_quality=metrics.get("thesis_quality", "WEAK"),
            all_positions=all_positions,
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
        quality_ok = (
            article_quality.get("score", 0.0) >= ARTICLE_EXECUTION_SCORE_FLOOR
        )
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
        unique_blockers = list(dict.fromkeys(blockers))

        should_trade = (
            (is_strong_buy or is_strong_sell)
            and is_confident
            and quality_ok
            and plan_ok
        )
        reason = (
            "Signal passed thesis, source-quality, account, and execution-plan gates."
        )
        if unique_blockers:
            if a.action == "HOLD":
                reason = "Portfolio Manager chose HOLD."
            else:
                reason = " ".join(unique_blockers)

        # ── SIM safety: simulated signals never trigger Alpaca orders ──
        if state.get("is_simulated", False):
            log.info(
                "Risk gate [%s]: simulated signal; Alpaca order submission is disabled",
                news.ticker,
            )
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
            unique_blockers = list(dict.fromkeys(blockers))

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
        log.info(
            "Risk gate [%s]: checks strong_buy=%s strong_sell=%s confident=%s "
            "quality_ok=%s execution_plan_ok=%s",
            news.ticker,
            is_strong_buy,
            is_strong_sell,
            is_confident,
            quality_ok,
            plan_ok,
        )
        log.info(
            "Risk gate [%s]: thresholds buy>=%.2f sell<=%.2f confidence>=%.2f "
            "effective_confidence>=%.2f quality_score>=%.2f",
            news.ticker,
            config.BUY_SENTIMENT_THRESHOLD,
            config.SELL_SENTIMENT_THRESHOLD,
            config.CONFIDENCE_THRESHOLD,
            effective_confidence_threshold,
            ARTICLE_EXECUTION_SCORE_FLOOR,
        )
        log.info(
            "Risk gate [%s]: committee_metrics agreement=%.3f net_weight=%.3f "
            "cap=%.2f calibrated=%.2f thesis=%s cap_reasons=%s dissenters=%s",
            news.ticker,
            float(metrics.get("agreement", 0.0) or 0.0),
            float(metrics.get("net_weight", 0.0) or 0.0),
            float(metrics.get("confidence_cap", 0.0) or 0.0),
            float(metrics.get("calibrated_confidence", 0.0) or 0.0),
            metrics.get("thesis_quality") or "unknown",
            _log_list(metrics.get("cap_reasons")),
            _log_list(metrics.get("high_conviction_dissenters")),
        )
        log.info(
            "Risk gate [%s]: execution_plan action=%s side=%s qty=%s intent=%s "
            "notional=%s buying_power=%s position_qty=%s blocked=%s",
            news.ticker,
            plan.get("action"),
            plan.get("side") or "none",
            plan.get("quantity"),
            plan.get("position_intent"),
            plan.get("estimated_notional"),
            plan.get("buying_power"),
            plan.get("position_qty"),
            _log_list(plan.get("blocked_reasons")),
        )
        if unique_blockers:
            log.info(
                "Risk gate [%s]: blockers=%s",
                news.ticker,
                _log_list(unique_blockers, limit=8),
            )
        log.info("Risk gate [%s]: reason=%s", news.ticker, _log_text(reason))
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
                "blockers": unique_blockers,
                "reason": reason,
            },
        }

    return assess_risk


def _make_execute_trade_node(trader: AlpacaTrader, cache: HeadlineCache):
    """Build a data-client once for the price-move gate re-check."""
    try:
        _exec_data_client = StockHistoricalDataClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
        )
    except Exception:
        _exec_data_client = None

    def _refetch_live_price(ticker: str) -> Optional[float]:
        """Get the latest trade price for a ticker right before order submission."""
        if _exec_data_client is None:
            return None
        try:
            snap = _exec_data_client.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=ticker)
            ).get(ticker)
            if snap and snap.latest_trade:
                return float(snap.latest_trade.price)
        except Exception as exc:
            log.debug("Price re-fetch failed for %s: %s", ticker, exc)
        return None

    def execute_trade(state: AgentState) -> dict:
        """Submit the order to Alpaca."""
        news = state["news"]
        action = state["analysis"].action
        plan = state.get("execution_plan") or {}
        quantity = int(plan.get("quantity") or config.ORDER_QTY)

        # ── Price-move gate (config-gated) ────────────────────────────────
        # Re-check the live price and compare to the snapshot from
        # fetch_context. If the stock has already moved more than the
        # configured threshold, the opportunity has passed — don't chase.
        price_move_gate: dict[str, Any] = {"enabled": config.PRICE_MOVE_GATE_ENABLED}
        if config.PRICE_MOVE_GATE_ENABLED:
            ctx = state.get("market_context") or {}
            snapshot_price = ctx.get("price")
            live_price = _refetch_live_price(news.ticker)

            if snapshot_price and live_price and snapshot_price > 0:
                move_pct = abs(live_price - snapshot_price) / snapshot_price
                price_move_gate.update({
                    "snapshot_price": snapshot_price,
                    "live_price": round(live_price, 2),
                    "move_pct": round(move_pct, 4),
                    "threshold_pct": config.MAX_PRICE_MOVE_PCT,
                })

                if move_pct > config.MAX_PRICE_MOVE_PCT:
                    direction = "up" if live_price > snapshot_price else "down"
                    gate_reason = (
                        f"Price-move gate: {news.ticker} moved {direction} "
                        f"{move_pct:.1%} (${snapshot_price:.2f} → ${live_price:.2f}) "
                        f"since analysis, exceeding {config.MAX_PRICE_MOVE_PCT:.0%} threshold. "
                        f"Order blocked to avoid chasing."
                    )
                    price_move_gate["blocked"] = True
                    price_move_gate["reason"] = gate_reason
                    log.warning(
                        "Price-move gate BLOCKED [%s]: %s moved %s %.1f%% "
                        "($%.2f → $%.2f) > %.0f%% threshold",
                        news.ticker, news.ticker, direction,
                        move_pct * 100, snapshot_price, live_price,
                        config.MAX_PRICE_MOVE_PCT * 100,
                    )
                    return {
                        "trade_order_id": None,
                        "execution": {
                            "step": "execute_trade",
                            "submitted": False,
                            "ticker": news.ticker,
                            "action": action,
                            "quantity": quantity,
                            "order_id": None,
                            "status": "price_move_blocked",
                            "error": gate_reason,
                            "execution_plan": plan,
                            "price_move_gate": price_move_gate,
                        },
                    }
                else:
                    price_move_gate["blocked"] = False
                    log.info(
                        "Price-move gate OK [%s]: moved %.2f%% ($%.2f → $%.2f), "
                        "within %.0f%% threshold",
                        news.ticker, move_pct * 100,
                        snapshot_price, live_price,
                        config.MAX_PRICE_MOVE_PCT * 100,
                    )
            else:
                price_move_gate["blocked"] = False
                price_move_gate["reason"] = (
                    "Could not compare prices (snapshot or live price unavailable)."
                )
                log.debug(
                    "Price-move gate [%s]: skipped — snapshot=$%s live=$%s",
                    news.ticker, snapshot_price, live_price,
                )

        # ── Idempotency ──────────────────────────────────────────────────
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

        # Compute limit price if limit orders are enabled
        limit_price = None
        try:
            if config.USE_LIMIT_ORDERS:
                ctx = state.get("market_context") or {}
                price = ctx.get("price")
                if price is not None and price > 0:
                    buffer = config.LIMIT_ORDER_BUFFER_PCT
                    if action == "BUY":
                        limit_price = round(price * (1 + buffer), 2)
                    elif action == "SELL":
                        limit_price = round(price * (1 - buffer), 2)
        except Exception:
            pass

        # Compute bracket prices for atomic bracket order (config-gated)
        take_profit_price = None
        stop_loss_price = None
        bracket_info: dict[str, Any] = {}
        if action == "BUY" and config.BRACKET_ORDERS_ENABLED:
            try:
                ctx = state.get("market_context") or {}
                entry_price = ctx.get("price")
                if entry_price and entry_price > 0:
                    tp_price = round(float(entry_price) * (1 + config.TAKE_PROFIT_PCT), 2)
                    sl_price = round(float(entry_price) * (1 - config.STOP_LOSS_PCT), 2)
                    take_profit_price = tp_price
                    stop_loss_price = sl_price
                    bracket_info = {
                        "entry_price": float(entry_price),
                        "take_profit_price": tp_price,
                        "stop_loss_price": sl_price,
                        "take_profit_pct": config.TAKE_PROFIT_PCT,
                        "stop_loss_pct": config.STOP_LOSS_PCT,
                        "method": "atomic_bracket",
                    }
                    log.info(
                        "Bracket prices for %s: entry=$%.2f TP=$%.2f (+%.1f%%) SL=$%.2f (-%.1f%%)",
                        news.ticker, entry_price, tp_price,
                        config.TAKE_PROFIT_PCT * 100, sl_price,
                        config.STOP_LOSS_PCT * 100,
                    )
            except Exception as bracket_exc:
                log.warning("Bracket price computation failed for %s: %s", news.ticker, bracket_exc)
                bracket_info = {"error": str(bracket_exc)}

        result = trader.place_order(
            ticker=news.ticker,
            action=action,
            quantity=quantity,
            client_order_id=client_order_id,
            limit_price=limit_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
        )

        execution_data: dict[str, Any] = {
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
        }
        if limit_price is not None:
            execution_data["limit_price"] = limit_price
        if bracket_info:
            execution_data["bracket_orders"] = bracket_info
            if result.submitted:
                bracket_info["status"] = "attached_to_primary_order"
            else:
                bracket_info["status"] = "order_failed_no_bracket"
        if price_move_gate.get("enabled"):
            execution_data["price_move_gate"] = price_move_gate

        # ── Fill verification with retry ──────────────────────────────────
        # Market orders fill near-instantly, but Alpaca's API may not
        # reflect the fill status on the first check. We retry up to 3
        # times with a short sleep so the DB records the actual fill
        # status rather than just "submitted". This is what lets
        # executed_action reflect reality.
        if result.submitted and result.order_id:
            fill: dict[str, Any] = {}
            for attempt in range(3):
                try:
                    fill = trader.verify_fill(result.order_id)
                    fill_status = str(fill.get("status", "")).lower()
                    if fill_status in ("filled", "partially_filled", "cancelled", "expired", "rejected"):
                        break  # Terminal state — no point retrying
                    if attempt < 2:
                        time.sleep(0.5)  # 500ms between retries
                except Exception:
                    break
            if fill:
                execution_data["fill_verification"] = fill
                # Propagate the confirmed fill status to the top-level
                # so logger.py can gate executed_action on it.
                fill_status = str(fill.get("status", "")).lower()
                execution_data["fill_status"] = fill_status
                if fill.get("filled_avg_price"):
                    execution_data["filled_avg_price"] = fill["filled_avg_price"]
                log.info(
                    "Fill verification [%s]: status=%s filled_qty=%s avg_price=$%s (attempts=%d)",
                    news.ticker,
                    fill.get("status"),
                    fill.get("filled_qty"),
                    fill.get("filled_avg_price"),
                    attempt + 1,
                )

        # Record signal for momentum tracking (config-gated)
        try:
            if config.SIGNAL_MOMENTUM_ENABLED:
                from market_intelligence import SignalMomentumTracker
                from redis_client import create_redis_client
                analysis = state.get("analysis")
                if analysis:
                    tracker = SignalMomentumTracker(create_redis_client())
                    tracker.record_signal(
                        ticker=news.ticker,
                        sentiment=analysis.sentiment,
                        confidence=analysis.confidence,
                        action=analysis.action,
                    )
        except Exception:
            pass

        return {
            "trade_order_id": result.order_id,
            "execution": execution_data,
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
    processing_finished_at = datetime.now(timezone.utc).isoformat()

    # Build enhanced feature observability report
    enhanced_features = None
    try:
        from observability import build_feature_report
        enhanced_features = build_feature_report(
            ticker=news.ticker,
            market_context=state.get("market_context"),
            article_quality=state.get("article_quality"),
            execution_plan=plan,
            execution=execution,
        )
    except Exception as obs_exc:
        log.debug("Could not build feature report: %s", obs_exc)

    trace = {
        "schema_version": 3,
        "pipeline": "decision_core",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "processing_started_at": state.get("processing_started_at"),
        "processing_finished_at": processing_finished_at,
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
    if enhanced_features:
        trace["enhanced_features"] = enhanced_features
    return trace


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
            cache.mark_seen(news.headline, ticker=news.ticker, article_id=news.article_id)
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
        cache.mark_seen(news.headline, ticker=news.ticker, article_id=news.article_id)

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
