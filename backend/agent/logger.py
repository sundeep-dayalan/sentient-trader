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


def _floatish(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _decision_path(decision_trace: Optional[dict], trade_action: str) -> str:
    if not isinstance(decision_trace, dict):
        return "legacy"

    risk_gate = decision_trace.get("risk_gate")
    if isinstance(risk_gate, dict) and risk_gate.get("step") == "freshness_gate":
        return "expired"

    pm = decision_trace.get("portfolio_manager_decision")
    if isinstance(pm, dict) and pm.get("model") == "deterministic-pre-screen":
        return "pre_screen"

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
        fields["order_status"] = execution.get("status")
        error = execution.get("error")
        if isinstance(error, str) and error.strip():
            fields["execution_error"] = error.strip()
        if submitted and execution_order_id:
            fields["executed_action"] = execution.get("action") or trade_action
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
