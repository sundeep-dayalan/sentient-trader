"""
Source Credibility
===================
Tier-based scoring for news sources based on editorial quality
and historical reliability for trading signals.

Higher-tier sources (Reuters, Bloomberg) get a positive boost;
PR wires and syndication feeds get penalized.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("agent.source_credibility")


# Tier 1: Primary financial news with dedicated editorial teams
# Tier 2: Major financial media with editorial oversight
# Tier 3: Aggregators and secondary sources
# Tier 4: Press release wires (low signal, high noise)

SOURCE_CREDIBILITY_SCORES: dict[str, float] = {
    # Tier 1 — strong editorial, reliable breaking news
    "Reuters": 0.15,
    "Associated Press": 0.15,
    "Bloomberg": 0.12,
    "Dow Jones": 0.12,
    "Wall Street Journal": 0.10,
    "Financial Times": 0.10,
    "The Wall Street Journal": 0.10,
    "WSJ": 0.10,
    # Tier 2 — credible financial media
    "CNBC": 0.08,
    "MarketWatch": 0.06,
    "Barron's": 0.06,
    "Investor's Business Daily": 0.05,
    "The Motley Fool": 0.03,
    "Yahoo Finance": 0.03,
    "Seeking Alpha": 0.02,
    "TheStreet": 0.02,
    # Tier 3 — mixed quality, useful in context
    "Benzinga": 0.02,
    "Zacks": 0.01,
    "TipRanks": 0.01,
    "24/7 Wall St.": 0.0,
    "InvestorPlace": 0.0,
    # Tier 4 — press releases and wire services (noise)
    "GlobeNewsWire": -0.05,
    "GlobeNewswire": -0.05,
    "PR Newswire": -0.05,
    "Business Wire": -0.04,
    "Accesswire": -0.08,
    "ACCESSWIRE": -0.08,
    "Newsfile": -0.06,
    "Cision": -0.05,
}

# Normalized lookup (lowercase -> score)
_NORMALIZED_SCORES: dict[str, float] = {
    source.lower(): score for source, score in SOURCE_CREDIBILITY_SCORES.items()
}


def source_credibility_score(source: Optional[str]) -> float:
    """
    Return the credibility adjustment for a news source.

    Positive values boost article quality scores; negative values penalize.
    Unknown sources return 0.0 (no adjustment).
    """
    if not source:
        return 0.0
    normalized = source.strip().lower()
    return _NORMALIZED_SCORES.get(normalized, 0.0)


def source_tier(source: Optional[str]) -> str:
    """
    Return a human-readable tier label for a news source.
    """
    score = source_credibility_score(source)
    if score >= 0.10:
        return "TIER_1"
    if score >= 0.04:
        return "TIER_2"
    if score >= 0.0:
        return "TIER_3"
    return "TIER_4"


def source_credibility_prompt_note(source: Optional[str]) -> str:
    """
    Return a prompt annotation about source credibility.
    Only included for notable sources (tier 1 or tier 4).
    """
    tier = source_tier(source)
    if tier == "TIER_1":
        return f"(Source credibility: HIGH — {source} is a top-tier financial news outlet)"
    if tier == "TIER_4":
        return f"(Source credibility: LOW — {source} is a press release/wire service, treat with caution)"
    return ""
