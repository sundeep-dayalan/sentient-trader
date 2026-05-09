"""
News Relevance Filter
======================
A lightweight pre-filter that runs BEFORE we spend Kafka quota on a message.

The goal: drop obvious noise so the AI agent doesn't waste inference tokens
on headlines that can't drive a trade decision.

Design philosophy — two lists:
  BOILERPLATE_PHRASES: administrative press releases (always drop)
  HIGH_SIGNAL_WORDS:   action words that suggest a meaningful price move

A headline is kept if:
  - It passes the boilerplate check, AND
  - The ticker symbol appears in the text OR a high-signal word is present
"""

import logging
import re

log = logging.getLogger("ingestion.filter")


# Phrases found in routine administrative announcements.
# These generate zero trading signal — drop them without further checks.
BOILERPLATE_PHRASES = [
    "conference call",
    "webcast",
    "investor day",
    "annual meeting",
    "proxy statement",
    "form 10-",
    "form 8-k",
    "dividend declared",
    "dividend record date",
    "notice of annual",
    "shareholder meeting",
]

# Words that signal a meaningful business event likely to move the stock price.
# If any of these appear, the headline is worth analyzing even without a ticker.
HIGH_SIGNAL_WORDS = [
    "earnings",
    "revenue",
    "beats",
    "misses",
    "guidance",
    "acquisition",
    "merger",
    "fda approval",
    "recall",
    "layoffs",
    "partnership",
    "contract",
    "lawsuit",
    "investigation",
    "ceo",
    "raises",
    "cuts forecast",
    "outlook",
    "record quarter",
    "surge",
    "plunge",
    "rally",
    "crash",
    "bankruptcy",
    "restructuring",
]


def is_relevant(headline: str, ticker: str) -> bool:
    """
    Returns True if this headline is worth sending to the AI agent.

    Fast path: drop boilerplate immediately without checking signal words.
    Slow path: ticker symbol in headline = strong signal; high-signal word = keep.
    """
    lower = headline.lower()

    # Fast reject: administrative noise
    if any(phrase in lower for phrase in BOILERPLATE_PHRASES):
        log.debug("Filtered (boilerplate): %s", headline[:60])
        return False

    # Strong signal: ticker symbol mentioned explicitly in the headline text
    if re.search(rf"\b{re.escape(ticker)}\b", headline, re.IGNORECASE):
        return True

    # Moderate signal: contains a market-moving action word
    if any(word in lower for word in HIGH_SIGNAL_WORDS):
        return True

    log.debug("Filtered (low signal): %s", headline[:60])
    return False
