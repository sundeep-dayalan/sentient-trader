"""
Supabase Logger
================
Writes every trade decision to the `trades` table.

We log EVERYTHING — executed trades AND HOLD decisions.
This is what powers the "Agent Monologue" on the dashboard:
the recruiter can see the AI was actively reasoning even when it
decided the signal wasn't strong enough to pull the trigger.

Implementation note:
  We use the SERVICE ROLE key (not the anon key) because:
  - This runs server-side on Fly.io — the key is never exposed
  - Service role bypasses Row Level Security for writes
  - The anon key would be blocked by our RLS policy (SELECT-only)
"""

import logging
import os
from typing import Optional

from supabase import Client, create_client

log = logging.getLogger("agent.logger")


class SupabaseLogger:
    """
    Thin wrapper around supabase-py for writing trade records.
    The `trades` table must have Realtime enabled in Supabase for the
    frontend live feed to update instantly.
    """

    def __init__(self) -> None:
        self._client: Client = create_client(
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
        log.info("Supabase logger connected")

    def log_trade(
        self,
        ticker: str,
        headline: str,
        sentiment_score: float,
        confidence_score: float,
        reasoning: str,
        trade_action: str,
        order_id: Optional[str] = None,
        quantity: int = 1,
        is_simulated: bool = False,
    ) -> None:
        """
        Insert one row into the trades table.

        Supabase Realtime picks this up immediately and pushes it to any
        subscribed browser clients — no polling, no refresh needed.
        """
        record = {
            "ticker":           ticker,
            "headline":         headline,
            "sentiment_score":  round(sentiment_score, 4),
            "confidence_score": round(confidence_score, 4),
            "reasoning":        reasoning,
            "trade_action":     trade_action,
            "order_id":         order_id,
            "quantity":         quantity,
            "is_simulated":     is_simulated,
        }

        try:
            self._client.table("trades").insert(record).execute()
            log.info("Logged to Supabase: [%s] %s", trade_action, ticker)
        except Exception as e:
            # Don't crash the pipeline if Supabase is momentarily unavailable.
            # The trade already happened — we just lose the log entry this time.
            log.error("Failed to log trade to Supabase: %s", e)
