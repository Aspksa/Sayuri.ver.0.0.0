from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sayuri_yukishiro.core.events import EventBus
from sayuri_yukishiro.core.jobs import JobManager
from sayuri_yukishiro.core.runtime import SystemCore
from sayuri_yukishiro.core.service import ManagedService, ServiceRegistry, ServiceState
from sayuri_yukishiro.database import MIGRATIONS, SCHEMA_VERSION, CoreDatabase
from sayuri_yukishiro.version import CORE_VERSION


class FailingService(ManagedService):
    name = "failing"

    def on_start(self) -> None:
        raise RuntimeError("boom")


class CountingService(ManagedService):
    name = "counting"

    def __init__(self) -> None:
        super().__init__()
        self.started = 0
        self.stopped = 0

    def on_start(self) -> None:
        self.started += 1

    def on_stop(self) -> None:
        self.stopped += 1


class DatabaseTests(unittest.TestCase):
    def test_migrations_apply_once_and_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CoreDatabase(Path(tmp) / "core.db")
            applied = db.initialize()
            self.assertEqual(applied, [item.version for item in MIGRATIONS])
            self.assertEqual(db.initialize(), [])
            self.assertEqual(db.schema_version(), SCHEMA_VERSION)
            self.assertEqual(db.quick_check().lower(), "ok")

    def test_database_newer_than_build_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CoreDatabase(Path(tmp) / "core.db")
            db.initialize()
            with db.session() as conn:
                conn.execute(
                    "INSERT INTO schema_migrations(version, description, applied_at)"
                    " VALUES(999, 'from the future', '2030-01-01T00:00:00Z')"
                )
            with self.assertRaises(RuntimeError):
                db.initialize()

    def test_events_and_audit_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CoreDatabase(Path(tmp) / "core.db")
            db.initialize()
            db.append_event("demo.event", "test", {"value": 1})
            events = db.recent_events(10)
            self.assertEqual(events[0]["event_type"], "demo.event")
            self.assertEqual(events[0]["payload"]["value"], 1)


class ServiceRegistryTests(unittest.TestCase):
    def test_failed_start_rolls_back_started_services(self) -> None:
        registry = ServiceRegistry()
        healthy = CountingService()
        registry.register(healthy)
        registry.register(FailingService())

        with self.assertRaises(RuntimeError):
            registry.start_all()

        self.assertEqual(healthy.started, 1)
        self.assertEqual(healthy.stopped, 1)
        self.assertEqual(healthy.state, ServiceState.STOPPED)

    def test_duplicate_registration_is_refused(self) -> None:
        registry = ServiceRegistry()
        registry.register(CountingService())
        with self.assertRaises(ValueError):
            registry.register(CountingService())

    def test_stop_order_is_reverse_of_start(self) -> None:
        order: list[str] = []

        class Tracked(ManagedService):
            def __init__(self, name: str) -> None:
                super().__init__()
                self.name = name

            def on_start(self) -> None:
                order.append(f"start:{self.name}")

            def on_stop(self) -> None:
                order.append(f"stop:{self.name}")

        registry = ServiceRegistry()
        registry.register(Tracked("first"))
        registry.register(Tracked("second"))
        registry.start_all()
        registry.stop_all()

        self.assertEqual(
            order,
            ["start:first", "start:second", "stop:second", "stop:first"],
        )


class EventBusTests(unittest.TestCase):
    def test_handler_receives_event(self) -> None:
        bus = EventBus()
        bus.start()
        try:
            received: list[str] = []
            bus.subscribe("demo", lambda event: received.append(event.id))
            event = bus.publish("demo", {"a": 1})
            self.assertEqual(received, [event.id])
        finally:
            bus.stop()

    def test_wildcard_subscriber_sees_everything(self) -> None:
        bus = EventBus()
        bus.start()
        try:
            seen: list[str] = []
            bus.subscribe("*", lambda event: seen.append(event.event_type))
            bus.publish("one")
            bus.publish("two")
            self.assertEqual(seen, ["one", "two"])
        finally:
            bus.stop()

    def test_failing_handler_does_not_block_others(self) -> None:
        bus = EventBus()
        bus.start()
        try:
            delivered: list[str] = []

            def broken(_event: object) -> None:
                raise RuntimeError("handler failure")

            bus.subscribe("demo", broken)
            bus.subscribe("demo", lambda event: delivered.append(event.event_type))
            bus.publish("demo")
            self.assertEqual(delivered, ["demo"])
        finally:
            bus.stop()

    def test_unsubscribe_stops_delivery(self) -> None:
        bus = EventBus()
        bus.start()
        try:
            seen: list[str] = []
            unsubscribe = bus.subscribe("demo", lambda event: seen.append(event.id))
            bus.publish("demo")
            unsubscribe()
            bus.publish("demo")
            self.assertEqual(len(seen), 1)
        finally:
            bus.stop()


class JobManagerTests(unittest.TestCase):
    def test_job_runs_and_reports_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CoreDatabase(Path(tmp) / "core.db")
            db.initialize()
            manager = JobManager(db, max_workers=2)
            manager.start()
            try:
                job_id = manager.submit("sum", lambda a, b: a + b, 2, 3)
                self.assertEqual(manager.wait(job_id, timeout=10), 5)
                self.assertEqual(manager.get(job_id)["status"], "completed")
            finally:
                manager.stop()

    def test_failed_job_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CoreDatabase(Path(tmp) / "core.db")
            db.initialize()
            manager = JobManager(db, max_workers=1)
            manager.start()
            try:
                def boom() -> None:
                    raise ValueError("expected")

                job_id = manager.submit("boom", boom)
                with self.assertRaises(ValueError):
                    manager.wait(job_id, timeout=10)
                self.assertEqual(manager.get(job_id)["status"], "failed")
            finally:
                manager.stop()

    def test_history_is_bounded_and_futures_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CoreDatabase(Path(tmp) / "core.db")
            db.initialize()
            manager = JobManager(db, max_workers=2, max_history=3)
            manager.start()
            try:
                for value in range(10):
                    job_id = manager.submit("echo", lambda item=value: item)
                    self.assertEqual(manager.wait(job_id, timeout=10), value)
                self.assertLessEqual(len(manager.snapshot()), 3)
            finally:
                manager.stop()
            self.assertEqual(manager._futures, {})

    def test_submit_without_running_manager_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = CoreDatabase(Path(tmp) / "core.db")
            db.initialize()
            manager = JobManager(db)
            with self.assertRaises(RuntimeError):
                manager.submit("never", lambda: None)


class SystemCoreTests(unittest.TestCase):
    def make_core(self, root: Path) -> SystemCore:
        return SystemCore(
            db=CoreDatabase(root / "core.db"),
            config_path=root / "system.json",
            log_dir=root / "logs",
        )

    def test_core_starts_healthy_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.make_core(Path(tmp)) as core:
                status = core.status()
                self.assertTrue(status["running"])
                self.assertEqual(status["core_version"], CORE_VERSION)
                self.assertEqual(status["health"]["overall"], "healthy")
                self.assertEqual(
                    status["health"]["healthy_count"],
                    status["health"]["service_count"],
                )
                self.assertEqual(status["database_check"], "not_checked")

    def test_deep_status_runs_quick_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.make_core(Path(tmp)) as core:
                self.assertEqual(core.status(deep=True)["database_check"].lower(), "ok")

    def test_checkpoint_survives_restart_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.make_core(root) as core:
                core.api.save_checkpoint("task-1", {"step": 2}, "continue from step 3")

            with self.make_core(root) as restored:
                tasks = restored.api.recoverable_tasks()
                self.assertEqual(len(tasks), 1)
                self.assertEqual(tasks[0]["task_id"], "task-1")
                self.assertEqual(tasks[0]["next_action"], "continue from step 3")
                restored.api.complete_checkpoint("task-1")
                self.assertEqual(restored.api.recoverable_tasks(), [])

    def test_checkpoint_requires_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.make_core(Path(tmp)) as core:
                with self.assertRaises(ValueError):
                    core.api.save_checkpoint("task-2", {}, "   ")

    def test_interrupted_jobs_are_marked_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = CoreDatabase(root / "core.db")
            db.initialize()
            db.create_core_job("stale", "stale-job", "running")

            core = SystemCore(
                db=db,
                config_path=root / "system.json",
                log_dir=root / "logs",
            )
            core.start()
            core.stop()

            with db.session() as conn:
                status = conn.execute(
                    "SELECT status FROM core_jobs WHERE id='stale'"
                ).fetchone()[0]
            self.assertEqual(status, "interrupted")

    def test_core_api_exposes_only_declared_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.make_core(Path(tmp)) as core:
                public = {name for name in dir(core.api) if not name.startswith("_")}
                self.assertEqual(
                    public,
                    {
                        "complete_checkpoint",
                        "config_get",
                        "job_status",
                        "log",
                        "publish",
                        "recoverable_tasks",
                        "save_checkpoint",
                        "status",
                        "submit_job",
                        "subscribe",
                    },
                )


if __name__ == "__main__":
    unittest.main()
