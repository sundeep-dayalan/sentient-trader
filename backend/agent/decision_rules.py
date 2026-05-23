"""
Decision Rules
==============
Deterministic guardrails that sit around the LLM committee.

The model still produces the narrative analysis, but these functions make the
system harder to fool by weak headlines, unsupported claims, split debates, and
position-blind SELL calls.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from schemas import NewsMessage, TradeAnalysis

STRONG_CATALYST_PATTERNS = [
    r"\bbeats?\b",
    r"\bmiss(?:es|ed)?\b",
    r"\braises? (?:guidance|forecast|outlook)\b",
    r"\bcuts? (?:guidance|forecast|outlook)\b",
    r"\brecord (?:revenue|quarter|earnings)\b",
    r"\bfda (?:approval|clearance|rejects?|hold)\b",
    r"\bclinical (?:trial|data|results?)\b",
    r"\bmerger\b",
    r"\bacquisit(?:ion|ions|ive)\b",
    r"\bbankrupt(?:cy)?\b",
    r"\brestructur(?:ing|es?)\b",
    r"\bsec (?:investigation|probe|charges?)\b",
    r"\blawsuit\b",
    r"\brecall\b",
    r"\bcontract\b",
    r"\bpartnership\b",
]

MODERATE_CATALYST_PATTERNS = [
    r"\bupgrad(?:e|ed|es)\b",
    r"\bdowngrad(?:e|ed|es)\b",
    r"\bprice target\b",
    r"\binitiates? coverage\b",
    r"\bexpands?\b",
    r"\blaunch(?:es|ed)?\b",
    r"\bappoints?\b",
    r"\bceo\b",
    r"\bcfo\b",
    r"\bdividend\b",
]

WEAK_ARTICLE_PATTERNS = [
    r"\bstocks? to watch\b",
    r"\bwatchlist\b",
    r"\bon (?:the )?radar\b",
    r"\bwhat'?s going on\b",
    r"\bwhy .* (?:shares|stock) (?:are|is) (?:trading|moving)\b",
    r"\bhere'?s how much\b",
    r"\bif you invested\b",
    r"\b\d+\s+years? ago\b",
    r"\bmarket update\b",
    r"\bpenny stocks?\b",
    r"\btop \d+\b",
    r"\bbest .* stocks?\b",
    r"\boptions activity\b",
]


@dataclass
class ArticleQuality:
    score: float
    grade: str
    category: str
    reasons: list[str]
    flags: list[str]
    has_summary: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _matches(patterns: list[str], text: str) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def evaluate_article_quality(news: NewsMessage) -> ArticleQuality:
    """Score how tradeable the input article is before spending risk budget."""
    headline = news.headline.strip()
    summary = (news.summary or "").strip()
    text = f"{headline} {summary}".lower()
    ticker = news.ticker.strip().upper()

    score = 0.35
    reasons: list[str] = []
    flags: list[str] = []
    category = "single-name headline"

    if summary and len(summary) >= 80:
        score += 0.18
        reasons.append("Alpaca supplied a usable article summary.")
    else:
        flags.append("missing_or_thin_summary")

    if re.search(rf"\b{re.escape(ticker)}\b", headline, re.IGNORECASE):
        score += 0.10
        reasons.append("Headline explicitly names the ticker.")

    strong = _matches(STRONG_CATALYST_PATTERNS, text)
    moderate = _matches(MODERATE_CATALYST_PATTERNS, text)
    weak = _matches(WEAK_ARTICLE_PATTERNS, text)

    if strong:
        score += min(0.30, 0.14 + 0.05 * len(strong))
        reasons.append("Contains concrete market-moving catalyst language.")
        category = "hard catalyst"
    elif moderate:
        score += min(0.16, 0.08 + 0.03 * len(moderate))
        reasons.append("Contains moderate catalyst language.")
        category = "soft catalyst"

    if weak:
        score -= min(0.38, 0.20 + 0.06 * len(weak))
        flags.append("weak_or_broad_article")
        category = "weak/broad article"

    comma_count = headline.count(",")
    if comma_count >= 2 or " and " in headline.lower():
        score -= 0.08
        flags.append("multi_name_or_basket_headline")

    if len(headline) < 35:
        score -= 0.06
        flags.append("very_short_headline")

    score = round(max(0.0, min(1.0, score)), 2)
    if score >= 0.72:
        grade = "HIGH"
    elif score >= 0.48:
        grade = "MEDIUM"
    else:
        grade = "LOW"

    if not reasons:
        reasons.append("No strong article-specific trading catalyst detected.")

    return ArticleQuality(
        score=score,
        grade=grade,
        category=category,
        reasons=reasons,
        flags=flags,
        has_summary=bool(summary),
    )


def quality_prompt_block(quality: ArticleQuality | dict[str, Any] | None) -> str:
    """Small prompt block that tells every persona how reliable the source is."""
    if quality is None:
        return ""
    if isinstance(quality, dict):
        quality = ArticleQuality(**quality)
    flags = ", ".join(quality.flags) if quality.flags else "none"
    reasons = " ".join(quality.reasons)
    return (
        "\n\nSOURCE QUALITY CHECK:\n"
        f"- Grade: {quality.grade} ({quality.score:.2f}/1.00), category: {quality.category}.\n"
        f"- Flags: {flags}.\n"
        f"- Notes: {reasons}\n"
        "- Do not upgrade a LOW-quality or broad/watchlist article into a tradeable thesis unless the provided text contains a concrete catalyst."
    )


def guarded_system_prompt(base_prompt: str, role: str) -> str:
    """
    Add non-negotiable evidence rules to configurable persona prompts.

    The Supabase prompt remains user-editable, while this layer prevents the
    model from inventing facts when Alpaca only gave us a headline/summary.
    """
    role_specific = {
        "risk": (
            "As Risk Manager, do not default to BEARISH just because risks exist. "
            "Use NEUTRAL when the risk is generic or already reflected in the source text."
        ),
        "synthesis": (
            "As Portfolio Manager, separate recommendation quality from executable order quality. "
            "SELL means reduce/exit an existing long unless the system explicitly says shorting is allowed."
        ),
    }.get(role, "")

    return (
        f"{base_prompt}\n\n"
        "EVIDENCE DISCIPLINE:\n"
        "- Use only the provided headline, Alpaca summary, market context, and account/position context.\n"
        "- Do not invent EPS, revenue, analyst estimates, valuation, contracts, lawsuits, FDA details, or macro facts that are not in the provided text.\n"
        "- If a key fact is missing, say it is missing and lower conviction.\n"
        "- Broad watchlists, historical-return articles, generic radar pieces, and unsourced options-flow blurbs should usually be NEUTRAL/HOLD.\n"
        "- Calibrate conviction: 0.80+ requires a concrete catalyst and clear directional implication; 0.50-0.70 is a watch/read-through; below 0.50 is weak evidence.\n"
        f"{role_specific}"
    )


def _stance_weight(stance: str, conviction: float) -> float:
    if stance == "BULLISH":
        return conviction
    if stance == "BEARISH":
        return -conviction
    return 0.0


def committee_metrics(
    analysis: TradeAnalysis,
    quality: ArticleQuality | dict[str, Any],
    market_context: Optional[dict],
) -> dict[str, Any]:
    """Compute deterministic confidence caps from debate shape and source quality."""
    if isinstance(quality, dict):
        quality = ArticleQuality(**quality)
    opinions = analysis.committee
    total_conviction = sum(max(0.0, p.conviction) for p in opinions) or 1.0
    bull = sum(p.conviction for p in opinions if p.stance == "BULLISH")
    bear = sum(p.conviction for p in opinions if p.stance == "BEARISH")
    neutral = sum(p.conviction for p in opinions if p.stance == "NEUTRAL")
    net = sum(_stance_weight(p.stance, p.conviction) for p in opinions)
    dominant_weight = max(bull, bear, neutral)
    agreement = round(dominant_weight / total_conviction, 3)

    action_sign = (
        1 if analysis.action == "BUY" else -1 if analysis.action == "SELL" else 0
    )
    high_conviction_dissenters = [
        p.name
        for p in opinions
        if action_sign
        and p.conviction >= 0.72
        and (
            (action_sign > 0 and p.stance == "BEARISH")
            or (action_sign < 0 and p.stance == "BULLISH")
        )
    ]

    confidence_cap = 0.95
    cap_reasons: list[str] = []
    if quality.grade == "LOW":
        confidence_cap = min(confidence_cap, 0.62)
        cap_reasons.append("low article quality")
    elif quality.grade == "MEDIUM":
        confidence_cap = min(confidence_cap, 0.80)
        cap_reasons.append("medium article quality")

    if not quality.has_summary:
        confidence_cap = min(confidence_cap, 0.78)
        cap_reasons.append("missing article summary")

    if agreement < 0.58:
        confidence_cap = min(confidence_cap, 0.70)
        cap_reasons.append("split committee")
    elif agreement < 0.70:
        confidence_cap = min(confidence_cap, 0.82)
        cap_reasons.append("partial committee agreement")

    if high_conviction_dissenters:
        confidence_cap = min(confidence_cap, 0.72)
        cap_reasons.append("high-conviction dissenter")

    day_change_pct = (market_context or {}).get("day_change_pct")
    if (
        analysis.action == "BUY"
        and isinstance(day_change_pct, (int, float))
        and day_change_pct > 8
    ):
        confidence_cap = min(confidence_cap, 0.82)
        cap_reasons.append("already extended intraday")
    if (
        analysis.action == "SELL"
        and isinstance(day_change_pct, (int, float))
        and day_change_pct < -8
    ):
        confidence_cap = min(confidence_cap, 0.82)
        cap_reasons.append("already deeply sold off intraday")

    calibrated_confidence = round(min(analysis.confidence, confidence_cap), 4)
    thesis_quality = (
        "EXECUTABLE"
        if calibrated_confidence >= 0.78 and quality.grade == "HIGH"
        else "WATCH" if calibrated_confidence >= 0.62 else "WEAK"
    )

    return {
        "bullish_weight": round(bull, 3),
        "bearish_weight": round(bear, 3),
        "neutral_weight": round(neutral, 3),
        "net_weight": round(net, 3),
        "agreement": agreement,
        "confidence_cap": round(confidence_cap, 4),
        "calibrated_confidence": calibrated_confidence,
        "cap_reasons": cap_reasons,
        "high_conviction_dissenters": high_conviction_dissenters,
        "thesis_quality": thesis_quality,
    }


def _floatish(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_execution_plan(
    *,
    action: str,
    order_qty: int,
    market_context: Optional[dict],
) -> dict[str, Any]:
    """Create a position-aware execution plan before the risk gate fires."""
    ctx = market_context or {}
    account = ctx.get("account") or {}
    position = ctx.get("position") or {}
    price = _floatish(ctx.get("price") or position.get("current_price"))
    buying_power = _floatish(account.get("buying_power"))
    position_qty = _floatish(position.get("qty")) or 0.0

    plan: dict[str, Any] = {
        "action": action,
        "side": None,
        "quantity": 0,
        "position_intent": "none",
        "estimated_notional": None,
        "buying_power": buying_power,
        "position_qty": position_qty,
        "blocked_reasons": [],
    }

    if action == "HOLD":
        plan["blocked_reasons"].append("Portfolio Manager chose HOLD.")
        return plan

    if account.get("trading_blocked") or account.get("account_blocked"):
        plan["blocked_reasons"].append("Alpaca account is trading/account blocked.")

    if price is None or price <= 0:
        plan["blocked_reasons"].append(
            "Live price unavailable; refusing blind market order."
        )

    if action == "BUY":
        plan["side"] = "buy"
        plan["quantity"] = int(order_qty)
        plan["position_intent"] = (
            "open_or_add_long" if position_qty <= 0 else "add_to_long"
        )
        if price is not None:
            plan["estimated_notional"] = round(price * order_qty, 2)
            if buying_power is not None and buying_power < price * order_qty * 1.02:
                plan["blocked_reasons"].append(
                    "Insufficient buying power for order plus safety buffer."
                )

    if action == "SELL":
        plan["side"] = "sell"
        if position_qty <= 0:
            plan["position_intent"] = "no_long_position"
            plan["blocked_reasons"].append(
                "No long position to reduce; short sells are disabled by policy."
            )
        else:
            whole_share_qty = int(position_qty)
            if whole_share_qty <= 0:
                plan["position_intent"] = "fractional_position_below_minimum"
                plan["blocked_reasons"].append(
                    "Position is below one whole share; configured order flow uses whole-share orders."
                )
                return plan
            sell_qty = min(int(order_qty), whole_share_qty)
            plan["quantity"] = sell_qty
            plan["position_intent"] = "reduce_long"
            if price is not None:
                plan["estimated_notional"] = round(price * sell_qty, 2)

    return plan
