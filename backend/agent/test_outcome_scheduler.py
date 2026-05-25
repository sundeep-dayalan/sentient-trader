import threading
import unittest

from outcome_scheduler import start_outcome_labeler_scheduler


class FakeRunTracker:
    def __init__(self) -> None:
        self.started = []
        self.finished = []
        self.finished_event = threading.Event()

    def start_run(self, *, scheduler_name: str, metadata: dict):
        self.started.append((scheduler_name, metadata))
        return f"run-{len(self.started)}"

    def finish_run(
        self,
        *,
        run_id,
        status: str,
        duration_ms: int,
        rows_processed=None,
        error_message=None,
    ) -> None:
        self.finished.append(
            {
                "run_id": run_id,
                "status": status,
                "duration_ms": duration_ms,
                "rows_processed": rows_processed,
                "error_message": error_message,
            }
        )
        self.finished_event.set()


class OutcomeSchedulerTests(unittest.TestCase):
    def test_disabled_scheduler_does_not_start_thread(self) -> None:
        scheduler = start_outcome_labeler_scheduler(enabled=False)

        self.assertIsNone(scheduler)

    def test_enabled_scheduler_runs_labeler_with_configured_limit(self) -> None:
        ran = threading.Event()
        calls = []

        def fake_labeler(*, limit: int, force: bool = False) -> int:
            calls.append((limit, force))
            ran.set()
            return 3

        scheduler = start_outcome_labeler_scheduler(
            label_fn=fake_labeler,
            enabled=True,
            interval_seconds=60,
            limit=7,
            run_on_startup=True,
        )
        self.assertIsNotNone(scheduler)
        assert scheduler is not None

        try:
            self.assertTrue(ran.wait(1.0))
            self.assertEqual(calls, [(7, False)])
        finally:
            scheduler.stop()

    def test_enabled_scheduler_tracks_successful_run(self) -> None:
        tracker = FakeRunTracker()

        def fake_labeler(*, limit: int, force: bool = False) -> int:
            return 5

        scheduler = start_outcome_labeler_scheduler(
            label_fn=fake_labeler,
            run_tracker=tracker,
            enabled=True,
            interval_seconds=60,
            limit=11,
            run_on_startup=True,
        )
        self.assertIsNotNone(scheduler)
        assert scheduler is not None

        try:
            self.assertTrue(tracker.finished_event.wait(1.0))
            self.assertEqual(tracker.started[0][0], "outcome_labeler")
            self.assertEqual(tracker.started[0][1]["limit"], 11)
            self.assertEqual(tracker.finished[0]["run_id"], "run-1")
            self.assertEqual(tracker.finished[0]["status"], "SUCCESS")
            self.assertEqual(tracker.finished[0]["rows_processed"], 5)
            self.assertIsNone(tracker.finished[0]["error_message"])
        finally:
            scheduler.stop()

    def test_run_on_startup_false_waits_for_first_interval(self) -> None:
        ran = threading.Event()

        def fake_labeler(*, limit: int, force: bool = False) -> int:
            ran.set()
            return 1

        scheduler = start_outcome_labeler_scheduler(
            label_fn=fake_labeler,
            enabled=True,
            interval_seconds=60,
            run_on_startup=False,
        )
        self.assertIsNotNone(scheduler)
        assert scheduler is not None

        try:
            self.assertFalse(ran.wait(0.05))
        finally:
            scheduler.stop()

    def test_labeler_exception_does_not_kill_scheduler_setup(self) -> None:
        ran = threading.Event()
        tracker = FakeRunTracker()

        def fake_labeler(*, limit: int, force: bool = False) -> int:
            ran.set()
            raise RuntimeError("boom")

        with self.assertLogs("agent.outcome_scheduler", level="ERROR"):
            scheduler = start_outcome_labeler_scheduler(
                label_fn=fake_labeler,
                run_tracker=tracker,
                enabled=True,
                interval_seconds=60,
                run_on_startup=True,
            )
            self.assertIsNotNone(scheduler)
            assert scheduler is not None

            try:
                self.assertTrue(ran.wait(1.0))
                self.assertTrue(tracker.finished_event.wait(1.0))
                self.assertEqual(tracker.finished[0]["status"], "ERROR")
                self.assertIn("boom", tracker.finished[0]["error_message"])
                self.assertTrue(scheduler.thread.is_alive())
            finally:
                scheduler.stop()


if __name__ == "__main__":
    unittest.main()
