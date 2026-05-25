"""Background scheduler for post-signal outcome labeling."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol
from uuid import uuid4

from outcome_labeler import label_recent_signals

log = logging.getLogger("agent.outcome_scheduler")

LabelerFn = Callable[..., int]


class SchedulerRunTracker(Protocol):
    def start_run(
        self,
        *,
        scheduler_name: str,
        metadata: dict[str, Any],
    ) -> Optional[str]:
        ...

    def finish_run(
        self,
        *,
        run_id: Optional[str],
        status: str,
        duration_ms: int,
        rows_processed: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        ...


@dataclass
class OutcomeLabelerScheduler:
    thread: threading.Thread
    stop_event: threading.Event

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        self.thread.join(timeout=timeout)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        log.warning("Invalid %s value; using default %d", name, default)
        return default
    if value < minimum:
        log.warning("%s below minimum %d; using %d", name, minimum, minimum)
        return minimum
    return value


class SupabaseSchedulerRunTracker:
    """Write scheduler activity rows without controlling scheduler behavior."""

    def __init__(self) -> None:
        from supabase import create_client
        from supabase.client import ClientOptions

        self._worker_name = (
            os.environ.get("AGENT_WORKER_NAME")
            or os.environ.get("REDIS_CONSUMER_NAME")
            or f"agent-{socket.gethostname()}"
        )
        self._client = create_client(
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
            options=ClientOptions(
                schema=os.environ.get("SUPABASE_DB_SCHEMA", "public"),
            ),
        )

    def start_run(
        self,
        *,
        scheduler_name: str,
        metadata: dict[str, Any],
    ) -> Optional[str]:
        run_id = str(uuid4())
        record = {
            "id": run_id,
            "scheduler_name": scheduler_name,
            "status": "RUNNING",
            "worker_name": self._worker_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata,
        }
        try:
            self._client.table("scheduler_runs").insert(
                record,
                returning="minimal",
            ).execute()
            return run_id
        except Exception as exc:
            log.warning("Could not record scheduler run start: %s", exc)
            return None

    def finish_run(
        self,
        *,
        run_id: Optional[str],
        status: str,
        duration_ms: int,
        rows_processed: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if not run_id:
            return
        record = {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "rows_processed": rows_processed,
            "error_message": error_message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._client.table("scheduler_runs").update(
                record,
                returning="minimal",
            ).eq("id", run_id).execute()
        except Exception as exc:
            log.warning("Could not record scheduler run finish: %s", exc)


def default_scheduler_run_tracker() -> Optional[SchedulerRunTracker]:
    if not env_bool("OUTCOME_LABELER_ENABLED", default=False):
        return None
    if not env_bool("SCHEDULER_RUN_TRACKING_ENABLED", default=True):
        log.info("Scheduler run tracking disabled")
        return None
    if not os.environ.get("SUPABASE_URL") or not os.environ.get(
        "SUPABASE_SERVICE_ROLE_KEY"
    ):
        log.info("Scheduler run tracking disabled; Supabase env is missing")
        return None
    return SupabaseSchedulerRunTracker()


def start_outcome_labeler_scheduler(
    *,
    label_fn: LabelerFn = label_recent_signals,
    run_tracker: Optional[SchedulerRunTracker] = None,
    enabled: Optional[bool] = None,
    interval_seconds: Optional[int] = None,
    limit: Optional[int] = None,
    run_on_startup: Optional[bool] = None,
    stop_event: Optional[threading.Event] = None,
) -> Optional[OutcomeLabelerScheduler]:
    """Start the outcome labeler in a daemon thread when enabled by env/config."""

    is_enabled = (
        env_bool("OUTCOME_LABELER_ENABLED", default=False)
        if enabled is None
        else enabled
    )
    if not is_enabled:
        log.info("Outcome labeler scheduler disabled")
        return None

    interval = (
        env_int("OUTCOME_LABELER_INTERVAL_SECONDS", 60 * 60, minimum=60)
        if interval_seconds is None
        else max(1, interval_seconds)
    )
    batch_limit = (
        env_int("OUTCOME_LABELER_LIMIT", 250, minimum=1)
        if limit is None
        else max(1, limit)
    )
    run_immediately = (
        env_bool("OUTCOME_LABELER_RUN_ON_STARTUP", default=True)
        if run_on_startup is None
        else run_on_startup
    )
    stop = stop_event or threading.Event()

    def loop() -> None:
        log.info(
            "Outcome labeler scheduler started interval=%ss limit=%s run_on_startup=%s",
            interval,
            batch_limit,
            run_immediately,
        )
        if not run_immediately and stop.wait(interval):
            return

        while not stop.is_set():
            started = time.monotonic()
            run_id = None
            metadata = {
                "limit": batch_limit,
                "interval_seconds": interval,
                "run_on_startup": run_immediately,
            }
            if run_tracker:
                run_id = run_tracker.start_run(
                    scheduler_name="outcome_labeler",
                    metadata=metadata,
                )
            try:
                labeled = label_fn(limit=batch_limit, force=False)
                duration_ms = int((time.monotonic() - started) * 1000)
                if run_tracker:
                    run_tracker.finish_run(
                        run_id=run_id,
                        status="SUCCESS",
                        duration_ms=duration_ms,
                        rows_processed=labeled,
                    )
                log.info("Outcome labeler completed; upserted %d rows", labeled)
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                if run_tracker:
                    run_tracker.finish_run(
                        run_id=run_id,
                        status="ERROR",
                        duration_ms=duration_ms,
                        error_message=str(exc),
                    )
                log.exception("Outcome labeler run failed; scheduler will retry later")

            if stop.wait(interval):
                return

    thread = threading.Thread(target=loop, name="outcome-labeler", daemon=True)
    thread.start()
    return OutcomeLabelerScheduler(thread=thread, stop_event=stop)
