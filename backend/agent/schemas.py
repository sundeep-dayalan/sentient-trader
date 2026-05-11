"""
Pydantic Schemas
=================
The single source of truth for every data structure that flows through
the agent pipeline.

Three layers of schema here:

  1. Wire format    — NewsMessage (Redis stream payload)
  2. LLM contracts  — PersonaAnalysis, SynthesisResult (instructor-enforced output
                      per individual LLM call in the multi-agent debate)
  3. Storage format — PersonaOpinion, TradeAnalysis (assembled from the debate,
                      written to Supabase, consumed by the frontend)

Keeping LLM contracts and storage schemas separate lets us evolve the prompt
engineering (PersonaAnalysis fields) independently from what the UI expects
(PersonaOpinion fields).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Wire Format ──────────────────────────────────────────────────────────────

class NewsMessage(BaseModel):
    """A single news article as it arrives from the Redis stream."""

    ticker:       str
    headline:     str
    source:       str
    published_at: str
    summary:      Optional[str] = None   # Alpaca article summary — 1-3 paragraphs
    article_url:  Optional[str] = None
    article_id:   Optional[str] = None
    is_simulated: bool = False


# ── LLM Contracts (instructor-enforced per-node output) ──────────────────────

class PersonaAnalysis(BaseModel):
    """
    Structured output demanded from each persona's dedicated LLM call.

    Each of the three analyst nodes (momentum, value, risk) produces one of
    these. instructor enforces the shape and retries if validation fails.

    conviction:    How strongly this persona holds their stance. The synthesizer
                   uses this to weight disagreements — a 0.9-conviction dissenter
                   carries more weight than a 0.3-conviction agreement.
    analysis:      2-3 sentences of reasoning from this persona's unique angle.
    headline_take: The single sharpest insight this persona can distil — one
                   sentence, stored as the 'view' field on the UI card.
    """

    stance: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(
        description="This persona's directional view on the headline's market impact",
    )
    conviction: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0 = highly uncertain, 1.0 = very confident in this stance",
    )
    analysis: str = Field(
        description="2-3 sentences of analytical reasoning from this persona's angle",
    )
    headline_take: str = Field(
        description="ONE sentence — the single sharpest insight this persona can offer",
    )


class SynthesisResult(BaseModel):
    """
    Structured output from the synthesizer's LLM call.

    The synthesizer sees the full debate transcript (all three PersonaAnalysis
    objects) and must produce a final, accountable trade decision.

    confidence should reflect debate alignment: three BULLISH personas at 0.9
    conviction produces higher confidence than a split 2:1 debate.
    """

    sentiment: float = Field(
        ge=-1.0,
        le=1.0,
        description="Consensus market sentiment: -1.0 (very bearish) to 1.0 (very bullish)",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Conviction in the consensus, weighted by debate alignment and individual "
            "conviction scores. Split committee = lower confidence."
        ),
    )
    reasoning: str = Field(
        description=(
            "1-2 sentences that synthesize the committee debate, acknowledge the key "
            "tension if analysts disagreed, and justify the final action."
        ),
    )
    action: Literal["BUY", "SELL", "HOLD"] = Field(
        description="Final trade recommendation after weighing all three personas",
    )


# ── Storage Format (Supabase + Frontend) ─────────────────────────────────────

class PersonaOpinion(BaseModel):
    """
    One committee member's opinion as stored in Supabase (committee_debate JSONB)
    and rendered in the AgentMonologue detail panel.

    Assembled from PersonaAnalysis after the LLM call — not produced directly
    by instructor. This decoupling means we can change prompt output fields
    without changing the database schema or frontend types.
    """

    name:       str  # "Momentum Trader" | "Value Investor" | "Risk Manager"
    stance:     Literal["BULLISH", "BEARISH", "NEUTRAL"]
    conviction: float  # 0.0–1.0, used for the UI conviction bar
    view:       str    # = PersonaAnalysis.headline_take — the one-liner shown on the card
    reasoning:  str    # = PersonaAnalysis.analysis — full text shown on expand
    model:      Optional[str] = None  # LLM model that powered this persona (e.g. "qwen/qwen3-32b")


class TradeAnalysis(BaseModel):
    """
    The complete record of one headline's analysis — assembled in the synthesizer
    node from three PersonaOpinion objects and one SynthesisResult.

    This is the authoritative shape that flows to assess_risk and log_result.
    The 'reasoning' field is the consensus summary stored in the trades table's
    TEXT column and shown on dashboard trade cards.
    """

    committee:  list[PersonaOpinion] = Field(
        min_length=3,
        max_length=3,
        description="Three personas in order: Momentum Trader, Value Investor, Risk Manager",
    )
    sentiment:  float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0,  le=1.0)
    reasoning:  str    # consensus one-liner
    action:     Literal["BUY", "SELL", "HOLD"]
    model:      Optional[str] = None  # LLM model that powered the synthesis
