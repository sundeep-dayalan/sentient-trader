"""
Pydantic Schemas
=================
The single source of truth for every data structure that flows through
the agent pipeline.

Why Pydantic here?
  - TradeAnalysis is the CONTRACT between us and the LLM.
    instructor uses it to validate the LLM's JSON output and auto-retry
    if the model returns something that doesn't match the shape.
  - NewsMessage ensures Kafka payloads are valid before touching the graph.
  - TradeRecord documents exactly what lands in Supabase per decision.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class NewsMessage(BaseModel):
    """A single news article as it arrives from the Kafka topic."""

    ticker: str
    headline: str
    source: str
    published_at: str


class TradeAnalysis(BaseModel):
    """
    The structured output we demand from the LLM for every headline.

    instructor enforces this shape — if the model returns malformed JSON
    or missing fields, it retries the request automatically (up to max_retries).

    sentiment:  -1.0 = extremely bearish, 0.0 = neutral, 1.0 = extremely bullish
    confidence: 0.0 = complete uncertainty, 1.0 = near-certain signal
    reasoning:  1-2 sentence human-readable explanation of the AI's logic
    action:     what the model recommends given sentiment + confidence together
    """

    sentiment: float = Field(
        ge=-1.0,
        le=1.0,
        description="Market sentiment from -1.0 (very bearish) to 1.0 (very bullish)",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the analysis from 0.0 (uncertain) to 1.0 (certain)",
    )
    reasoning: str = Field(
        description="1-2 sentence explanation of why this news would move the stock price",
    )
    action: Literal["BUY", "SELL", "HOLD"] = Field(
        description="Recommended trade action based on the sentiment and confidence level",
    )
