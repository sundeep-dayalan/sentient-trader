"""
AI Analyst — LangGraph + Groq
===============================
The cognitive core of Sentient Trader. Defines a LangGraph state machine
that processes each news headline through a structured decision pipeline.

Graph topology:
                                                    ┌──────────────────────┐
  START → [check_cache] ──── cached? YES ──────────→│         END          │
                │                                    └──────────────────────┘
             not cached
                │
          [analyze] ← calls Groq LLaMA 3.1 8B via instructor
                │
         [assess_risk] ← checks sentiment/confidence thresholds
                │
      ┌─────────┴──────────┐
   trade?                 hold?
      │                    │
 [execute_trade]      [log_result]
      │                    │
 [log_result] ─────────────┘
      │
     END

Why LangGraph instead of a plain function chain?
  - State is explicit and type-checked at every step
  - Adding a new node (e.g., a "research" step that fetches earnings data)
    requires zero changes to existing nodes — just wire a new edge
  - The conditional routing makes the decision logic readable at a glance
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, TypedDict

import instructor
from groq import Groq
from langgraph.graph import END, START, StateGraph

from cache import HeadlineCache
from logger import SupabaseLogger
from schemas import NewsMessage, TradeAnalysis
from trader import AlpacaTrader

log = logging.getLogger("agent.analyst")


# Trading thresholds — read from environment so they can be tuned via
# `flyctl secrets set` without a full redeploy.
BUY_SENTIMENT_THRESHOLD = float(os.environ.get("BUY_SENTIMENT_THRESHOLD", "0.8"))
SELL_SENTIMENT_THRESHOLD = float(os.environ.get("SELL_SENTIMENT_THRESHOLD", "-0.8"))
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.9"))
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")


# ── LangGraph State ─────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """
    Everything the agent knows at each step of the graph.
    Nodes read from this dict and return partial updates to it.
    """
    news:           NewsMessage
    is_cached:      bool
    analysis:       Optional[TradeAnalysis]
    should_trade:   bool
    trade_order_id: Optional[str]
    error:          Optional[str]
    is_simulated:   bool  # propagated from NewsMessage → Supabase row


# ── Node Factories ───────────────────────────────────────────────────────────
# We use factory functions (functions that return functions) so each node
# closes over its dependency (cache, groq_client, etc.) at build time.
# This keeps node signatures clean: they only take AgentState as input.

def _make_check_cache_node(cache: HeadlineCache):
    def check_cache(state: AgentState) -> dict:
        """Check Redis before spending tokens on the LLM."""
        cached = cache.is_duplicate(state["news"].headline)
        if cached:
            log.info("Cache HIT — skipping duplicate: %s", state["news"].headline[:60])
        return {"is_cached": cached}
    return check_cache


def _make_analyze_node(groq_client: Any):
    def analyze(state: AgentState) -> dict:
        """
        Call Groq with instructor to get a structured TradeAnalysis.

        instructor wraps the Groq client and automatically retries if
        the LLM returns JSON that fails Pydantic validation. This makes
        structured output reliable without any manual parsing code.
        """
        news = state["news"]

        prompt = (
            f"You are a quantitative trading analyst. Analyze this financial news "
            f"headline and determine its likely market impact on the stock {news.ticker}.\n\n"
            f"Headline: \"{news.headline}\"\n"
            f"Source: {news.source}\n\n"
            f"Provide a sentiment score (-1.0 to 1.0), a confidence level (0.0 to 1.0), "
            f"a brief 1-2 sentence reasoning, and a recommended trade action."
        )

        try:
            analysis: TradeAnalysis = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                response_model=TradeAnalysis,
                messages=[{"role": "user", "content": prompt}],
                max_retries=2,  # instructor retries if schema validation fails
            )
            log.info(
                "[%s] sentiment=%.2f  confidence=%.2f  action=%s",
                news.ticker, analysis.sentiment, analysis.confidence, analysis.action,
            )
            return {"analysis": analysis, "error": None}

        except Exception as e:
            log.error("LLM call failed for [%s]: %s", news.ticker, e)
            return {"analysis": None, "error": str(e)}

    return analyze


def _make_assess_risk_node():
    def assess_risk(state: AgentState) -> dict:
        """
        Decide whether the analysis clears the thresholds for a real order.

        We require BOTH a strong directional signal AND high confidence.
        This dual gate keeps the false-positive rate low on noisy news days.
        Example: a mildly bullish headline (sentiment=0.6) never triggers a BUY
        even if the model is very confident (confidence=0.95).
        """
        if state["analysis"] is None:
            return {"should_trade": False}

        a = state["analysis"]
        is_strong_buy = a.action == "BUY" and a.sentiment >= BUY_SENTIMENT_THRESHOLD
        is_strong_sell = a.action == "SELL" and a.sentiment <= SELL_SENTIMENT_THRESHOLD
        is_confident = a.confidence >= CONFIDENCE_THRESHOLD

        should_trade = (is_strong_buy or is_strong_sell) and is_confident

        log.info(
            "Risk assessment: should_trade=%s  (sentiment=%.2f, confidence=%.2f, action=%s)",
            should_trade, a.sentiment, a.confidence, a.action,
        )
        return {"should_trade": should_trade}

    return assess_risk


def _make_execute_trade_node(trader: AlpacaTrader, cache: HeadlineCache):
    def execute_trade(state: AgentState) -> dict:
        """Submit the order to Alpaca and mark the headline as seen in Redis."""
        news = state["news"]
        action = state["analysis"].action

        order_id = trader.place_order(
            ticker=news.ticker,
            action=action,
            quantity=1,
        )

        # Mark as seen AFTER a successful trade attempt to prevent double-trading
        # if this node is somehow called twice for the same message
        cache.mark_seen(news.headline)

        return {"trade_order_id": order_id}

    return execute_trade


def _make_log_result_node(db: SupabaseLogger, cache: HeadlineCache):
    def log_result(state: AgentState) -> dict:
        """
        Write the full interaction to Supabase — runs for every analyzed headline.
        The Supabase Realtime subscription on the frontend picks this up instantly.

        Always returns at least one state key — LangGraph 0.2.x requires every
        node to produce at least one update or it raises InvalidUpdateError.
        """
        if state["analysis"] is None:
            # LLM failed — nothing to log, but we must return a state update
            return {"error": state.get("error", "analysis_failed")}

        news = state["news"]
        a = state["analysis"]

        db.log_trade(
            ticker=news.ticker,
            headline=news.headline,
            sentiment_score=a.sentiment,
            confidence_score=a.confidence,
            reasoning=a.reasoning,
            trade_action=a.action,
            order_id=state.get("trade_order_id"),
            is_simulated=state.get("is_simulated", False),
        )

        # Mark as seen for HOLD decisions too, so we don't re-analyze the same
        # story from a different source 30 seconds later
        if not state.get("trade_order_id"):
            cache.mark_seen(news.headline)

        return {"error": None}

    return log_result


# ── Routing Functions ────────────────────────────────────────────────────────

def _route_after_cache_check(state: AgentState) -> str:
    """If this is a duplicate headline, skip everything and end the graph run."""
    return "skip" if state["is_cached"] else "analyze"


def _route_after_risk_assessment(state: AgentState) -> str:
    """Branch to execute_trade only if thresholds were cleared."""
    return "execute_trade" if state["should_trade"] else "log_result"


# ── Graph Assembly ───────────────────────────────────────────────────────────

def build_agent_graph(
    cache: HeadlineCache,
    trader: AlpacaTrader,
    db: SupabaseLogger,
) -> Any:
    """
    Assemble and compile the LangGraph state machine.
    Returns a compiled graph; call .invoke(initial_state) to process one headline.
    """
    groq_client = instructor.from_groq(Groq(api_key=os.environ["GROQ_API_KEY"]))

    graph = StateGraph(AgentState)

    # Register every node with its factory-produced function
    graph.add_node("check_cache",   _make_check_cache_node(cache))
    graph.add_node("analyze",       _make_analyze_node(groq_client))
    graph.add_node("assess_risk",   _make_assess_risk_node())
    graph.add_node("execute_trade", _make_execute_trade_node(trader, cache))
    graph.add_node("log_result",    _make_log_result_node(db, cache))

    # Wire the edges
    graph.add_edge(START, "check_cache")

    graph.add_conditional_edges(
        "check_cache",
        _route_after_cache_check,
        {"skip": END, "analyze": "analyze"},
    )

    graph.add_edge("analyze", "assess_risk")

    graph.add_conditional_edges(
        "assess_risk",
        _route_after_risk_assessment,
        {"execute_trade": "execute_trade", "log_result": "log_result"},
    )

    graph.add_edge("execute_trade", "log_result")
    graph.add_edge("log_result", END)

    return graph.compile()
