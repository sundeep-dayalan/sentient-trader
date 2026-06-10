"""
Supabase Logger
================
Writes every trade decision to Supabase.

We log EVERYTHING — executed trades AND HOLD decisions.
This is what powers the "Agent Monologue" on the dashboard:
the recruiter can see the AI was actively reasoning even when it
decided the signal wasn't strong enough to pull the trigger.

The live `trades` row stays slim for Realtime. The full decision_trace JSONB
lives in `trade_decision_traces`, preserving the complete LLM audit trail
without broadcasting it to every dashboard client.

Implementation note:
  We use the SERVICE ROLE key (not the anon key) because:
  - This runs server-side — the key is never exposed
  - Service role bypasses Row Level Security for writes
  - The anon key would be blocked by our RLS policy (SELECT-only)
"""

import logging
import os
from typing import Any, Optional
from uuid import uuid4

log = logging.getLogger("agent.logger")

# Alpaca order lifecycle states that mean the broker has accepted the order
# and we own the position (or are committed to owning it). `filled` is terminal;
# the others are en-route to fill but the trade IS happening.
_ACCEPTED_BROKER_STATES = frozenset({
    "filled", "partially_filled",
    "accepted", "new", "pending_new", "held",
    "accepted_for_bidding", "pending_replace", "replaced",
})
_TERMINAL_FAILURE_STATES = frozenset({
    "rejected", "cancelled", "canceled", "expired", "suspended",
})


def _floatish(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_status(value: Any) -> str:
    """
    Mirror of trader._normalize_status. Strip enum prefix, lowercase, trim.
    Duplicated here so logger.py has no runtime dep on trader.py.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def _decision_path(decision_trace: Optional[dict], trade_action: str) -> str:
    if not isinstance(decision_trace, dict):
        return "legacy"

    risk_gate = decision_trace.get("risk_gate")
    if isinstance(risk_gate, dict) and risk_gate.get("step") == "freshness_gate":
        return "expired"

    pm = decision_trace.get("portfolio_manager_decision")
    if isinstance(pm, dict) and pm.get("model") in (
        "deterministic-pre-screen", "budget-pre-screen"
    ):
        return "pre_screen"

    # Approved by the committee/risk gate but blocked because the intraday tape
    # did not confirm the direction. `passed is False` means a real block (a
    # lenient missing-data pass-through keeps passed True).
    pc = decision_trace.get("price_confirmation")
    if isinstance(pc, dict) and pc.get("passed") is False:
        return "unconfirmed"

    llm_operations = decision_trace.get("llm_operations")
    if isinstance(llm_operations, list) and llm_operations:
        return "full_debate"

    if trade_action == "HOLD":
        return "analysis_skipped"
    return "legacy"


def trade_observability_fields(
    *,
    decision_trace: Optional[dict],
    trade_action: str,
    order_id: Optional[str],
) -> dict[str, Any]:
    """
    Derive queryable execution/quality fields from the full JSON trace.

    The trace remains the complete audit record; these columns keep dashboards
    and replay audits honest without forcing every list query to read JSONB.
    """
    fields: dict[str, Any] = {
        "pm_recommendation": trade_action,
        "decision_path": _decision_path(decision_trace, trade_action),
    }
    if not isinstance(decision_trace, dict):
        return fields

    risk_gate = decision_trace.get("risk_gate")
    if isinstance(risk_gate, dict):
        fields["risk_should_trade"] = risk_gate.get("should_trade")
        inputs = risk_gate.get("inputs")
        metrics = risk_gate.get("committee_metrics")
        if isinstance(inputs, dict):
            calibrated = _floatish(inputs.get("calibrated_confidence"))
            if calibrated is not None:
                fields["calibrated_confidence"] = round(calibrated, 4)
        if isinstance(metrics, dict):
            if "calibrated_confidence" not in fields:
                calibrated = _floatish(metrics.get("calibrated_confidence"))
                if calibrated is not None:
                    fields["calibrated_confidence"] = round(calibrated, 4)
            cap = _floatish(metrics.get("confidence_cap"))
            if cap is not None:
                fields["confidence_cap"] = round(cap, 4)
        reason = risk_gate.get("reason")
        if isinstance(reason, str) and reason.strip():
            fields["gate_reason"] = reason.strip()

    execution = decision_trace.get("execution")
    if isinstance(execution, dict):
        execution_order_id = str(execution.get("order_id") or order_id or "").strip()
        submitted = execution.get("submitted") is True
        fields["client_order_id"] = execution.get("client_order_id")
        # Always store normalized status — mixed-case "OrderStatus.PENDING_NEW"
        # vs "accepted" used to break every downstream `== "filled"` check.
        submit_status = _normalize_status(execution.get("status"))
        fill_status = _normalize_status(execution.get("fill_status"))
        # Prefer the post-verification fill status over the submission status
        # since it reflects the broker's latest view of the order.
        effective_status = fill_status or submit_status
        if effective_status:
            fields["order_status"] = effective_status
        error = execution.get("error")
        if isinstance(error, str) and error.strip():
            fields["execution_error"] = error.strip()

        # ── executed_action policy ────────────────────────────────────────
        # Set executed_action when the broker has ACCEPTED the order — not
        # only when it has filled. Reason: an accepted order is a real
        # commitment with capital at risk, and downstream PnL/position
        # tracking needs to see it. The separate `order_status` column
        # carries the lifecycle state (filled vs pending vs partial).
        # Failures (rejected/cancelled/expired) explicitly do NOT set it.
        action_to_record = execution.get("action") or trade_action
        is_recordable_action = action_to_record in ("BUY", "SELL")
        broker_accepted = effective_status in _ACCEPTED_BROKER_STATES
        broker_failed = effective_status in _TERMINAL_FAILURE_STATES

        if submitted and execution_order_id and is_recordable_action and broker_accepted:
            fields["executed_action"] = action_to_record
        elif submitted and execution_order_id and is_recordable_action and not effective_status:
            # No status yet but broker returned an order_id — treat as accepted.
            # This guards against intermittent fill_verification failures.
            fields["executed_action"] = action_to_record
            fields["order_status"] = fields.get("order_status") or "pending_fill"
        elif submitted and execution_order_id and broker_failed:
            # Broker rejected/cancelled — DON'T record as executed.
            fields["execution_error"] = (
                fields.get("execution_error")
                or f"Broker terminated order without fill (status={effective_status})."
            )
        elif submitted and not execution_order_id:
            fields["order_status"] = fields.get("order_status") or "missing_order_id"
            fields["execution_error"] = (
                fields.get("execution_error")
                or "Execution trace reported submitted=true but no Alpaca order_id was captured."
            )

    for key in ("processing_started_at", "processing_finished_at"):
        value = decision_trace.get(key)
        if isinstance(value, str) and value.strip():
            fields[key] = value

    return fields


class SupabaseLogger:
    """
    Thin wrapper around supabase-py for writing trade records.
    The dashboard reads lightweight feed rows and only fetches the full trace
    when a user opens a specific signal.
    """

    def __init__(self) -> None:
        from supabase import create_client
        from supabase.client import ClientOptions

        self._client = create_client(
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            options=ClientOptions(
                schema=os.environ.get("SUPABASE_DB_SCHEMA", "public"),
            ),
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
        article_source: Optional[str] = None,
        article_url: Optional[str] = None,
        article_id: Optional[str] = None,
        decision_trace: Optional[dict] = None,
    ) -> None:
        """
        Insert one row into the trades table.

        decision_trace is a single JSONB document with all Decision Core raw
        details. It is intentionally generic so future personas, tools, or
        multi-step decision branches do not require new table columns.

        The insert uses `returning="minimal"` so the large decision_trace JSONB
        is not streamed back to the agent after every write.
        """
        trade_id = str(uuid4())
        base_record = {
            "id": trade_id,
            "ticker": ticker,
            "headline": headline,
            "sentiment_score": round(sentiment_score, 4),
            "confidence_score": round(confidence_score, 4),
            "trade_action": trade_action,
            "order_id": order_id,
            "quantity": quantity,
            "is_simulated": is_simulated,
        }
        slim_record = {
            **base_record,
            **trade_observability_fields(
                decision_trace=decision_trace,
                trade_action=trade_action,
                order_id=order_id,
            ),
        }
        if article_url:
            slim_record["article_url"] = article_url

        legacy_record = {
            **base_record,
            "reasoning": reasoning,
        }
        if article_url:
            legacy_record["article_url"] = article_url
        if article_source:
            legacy_record["article_source"] = article_source
        if article_id:
            legacy_record["article_id"] = article_id

        try:
            try:
                self._client.table("trades").insert(
                    slim_record, returning="minimal"
                ).execute()
            except Exception as slim_insert_error:
                # Compatibility fallback for deployments where migration 010 has
                # not been applied yet and `trades.reasoning` is still NOT NULL.
                self._client.table("trades").insert(
                    legacy_record, returning="minimal"
                ).execute()
                log.warning(
                    "Inserted legacy trade row after slim insert failed: %s",
                    slim_insert_error,
                )

            if decision_trace:
                trace_record = {
                    "trade_id": trade_id,
                    "decision_trace": decision_trace,
                    "reasoning": reasoning,
                }
                if article_source:
                    trace_record["article_source"] = article_source
                if article_id:
                    trace_record["article_id"] = article_id
                try:
                    self._client.table("trade_decision_traces").insert(
                        trace_record,
                        returning="minimal",
                    ).execute()
                except Exception as detailed_trace_error:
                    base_trace_record = {
                        "trade_id": trade_id,
                        "decision_trace": decision_trace,
                    }
                    try:
                        self._client.table("trade_decision_traces").insert(
                            base_trace_record,
                            returning="minimal",
                        ).execute()
                        log.warning(
                            "Stored base trace after detailed trace insert failed: %s",
                            detailed_trace_error,
                        )
                        continue_trace_fallback = False
                    except Exception as trace_error:
                        continue_trace_fallback = True

                    if not continue_trace_fallback:
                        log.info("Logged to Supabase: [%s] %s", trade_action, ticker)
                        return

                    # Backward-compatible fallback for deployments where the
                    # trace split migration has not been applied yet.
                    try:
                        self._client.table("trades").update(
                            {"decision_trace": decision_trace},
                            returning="minimal",
                        ).eq("id", trade_id).execute()
                        log.warning(
                            "Stored decision trace on legacy trades column after trace table insert failed: %s",
                            trace_error,
                        )
                    except Exception as fallback_error:
                        log.error(
                            "Trade row was logged but decision trace storage failed: %s; fallback failed: %s",
                            trace_error,
                            fallback_error,
                        )
                        raise fallback_error from trace_error
            log.info("Logged to Supabase: [%s] %s", trade_action, ticker)
        except Exception as e:
            # Durable audit logging is part of message resolution. Raise so the
            # consumer can retry or dead-letter instead of silently ACKing.
            log.error("Failed to log trade to Supabase: %s", e)
            raise
