"""Проверки Module Runtime: manifests, permissions, DB и lifecycle."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sayuri_yukishiro.database import CoreDatabase
from sayuri_yukishiro.modules.api import ModuleAPI, ModulePermissionError
from sayuri_yukishiro.modules.database import ModuleDatabase
from sayuri_yukishiro.modules.manifest import (
    ModuleManifestError,
    ModuleMigration,
    load_manifest,
)
from sayuri_yukishiro.modules.runtime import ModuleRuntime


class FakeCoreAPI:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def status(self) -> dict:
        return {"running": True}

    def config_get(self, path: str, default=None):
        return default

    def publish(self, event_type: str, payload=None, *, source: str = "module") -> dict:
        body = payload or {}
        self.events.append((event_type, body, source))
        return {"event_type": event_type, "payload": body, "source": source}

    def subscribe(self, _event_type: str, _handler):
        return lambda: None

    def submit_job(self, _name: str, func, *args, **kwargs) -> str:
        func(*args, **kwargs)
        return "job-1"

    def job_status(self, job_id: str) -> dict:
        return {"id": job_id, "status": "completed"}

    def save_checkpoint(self, _task_id: str, _payload: dict, _next_action: str) -> None:
        return None

    def complete_checkpoint(self, _task_id: str, _payload=None) -> None:
        return None

    def recoverable_tasks(self) -> list[dict]:
        return []

    def log(self, _message: str, *, level: str = "info") -> None:
        return None


def write_module(
    root: Path,
    module_id: str,
    *,
    version: str = "1.0.0",
    enabled: bool = True,
    dependencies: list[dict] | None = None,
    permissions: list[str] | None = None,
) -> Path:
    module_root = root / module_id
    module_root.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "id": module_id,
        "name": module_id.title(),
        "version": version,
        "entrypoint": "module.py:Module",
        "enabled": enabled,
        "permissions": permissions or [],
        "dependencies": dependencies or [],
        "database": {
            "migrations": [
                {
                    "version": 1,
                    "description": "state table",
                    "statements": [
                        "CREATE TABLE state (value TEXT NOT NULL)"
                    ],
                }
            ]
        },
    }
    (module_root / "module.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (module_root / "module.py").write_text(
        """
class Module:
    def __init__(self, context):
        self.context = context

    def start(self):
        with self.context.database.session() as conn:
            conn.execute("INSERT INTO state(value) VALUES('started')")

    def stop(self):
        return None

    def health(self):
        return {"healthy": True, "detail": "ok"}
""".lstrip(),
        encoding="utf-8",
    )
    return module_root


class ManifestTests(unittest.TestCase):
    def test_valid_manifest_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module_root = write_module(
                root,
                "alpha",
                permissions=["events.publish", "log.write"],
            )
            manifest = load_manifest(module_root)
            self.assertEqual(manifest.id, "alpha")
            self.assertEqual(manifest.version, "1.0.0")
            self.assertEqual(manifest.migrations[0].version, 1)
            self.assertIn("events.publish", manifest.permissions)

    def test_manifest_rejects_unknown_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module_root = write_module(Path(tmp), "alpha")
            path = module_root / "module.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["permissions"] = ["filesystem.root"]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ModuleManifestError):
                load_manifest(module_root)

    def test_manifest_rejects_entrypoint_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module_root = write_module(Path(tmp), "alpha")
            path = module_root / "module.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["entrypoint"] = "../escape.py:Module"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ModuleManifestError):
                load_manifest(module_root)


class ModuleDatabaseTests(unittest.TestCase):
    def test_migrations_are_ordered_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = ModuleDatabase(Path(tmp) / "module.db")
            migrations = (
                ModuleMigration(
                    1,
                    "one",
                    ("CREATE TABLE demo (id INTEGER PRIMARY KEY)",),
                ),
                ModuleMigration(
                    2,
                    "two",
                    ("ALTER TABLE demo ADD COLUMN value TEXT",),
                ),
            )
            self.assertEqual(db.initialize(migrations), [1, 2])
            self.assertEqual(db.initialize(migrations), [])
            self.assertEqual(db.schema_version(), 2)
            self.assertEqual(db.quick_check().lower(), "ok")

    def test_database_newer_than_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = ModuleDatabase(Path(tmp) / "module.db")
            migrations = (
                ModuleMigration(
                    1,
                    "one",
                    ("CREATE TABLE demo (id INTEGER PRIMARY KEY)",),
                ),
            )
            db.initialize(migrations)
            with db.session() as conn:
                conn.execute(
                    "INSERT INTO module_schema_migrations(version, description, applied_at) "
                    "VALUES(99, 'future', '2030-01-01T00:00:00Z')"
                )
            with self.assertRaises(RuntimeError):
                db.initialize(migrations)


class ModuleAPITests(unittest.TestCase):
    def test_permission_is_required(self) -> None:
        api = ModuleAPI("alpha", FakeCoreAPI(), ())
        with self.assertRaises(ModulePermissionError):
            api.status()

    def test_event_source_is_namespaced(self) -> None:
        core = FakeCoreAPI()
        api = ModuleAPI("alpha", core, ("events.publish",))
        api.publish("alpha.ready", {"ok": True})
        self.assertEqual(core.events[0][2], "module:alpha")


class ModuleRuntimeTests(unittest.TestCase):
    def make_runtime(self, root: Path) -> tuple[CoreDatabase, FakeCoreAPI, ModuleRuntime]:
        core_db = CoreDatabase(root / "core.db")
        core_db.initialize()
        core_api = FakeCoreAPI()
        runtime = ModuleRuntime(
            core_db,
            core_api,  # type: ignore[arg-type]
            modules_root=root / "modules",
            module_data_dir=root / "data" / "modules",
        )
        return core_db, core_api, runtime

    def test_dependencies_start_in_order_and_stop_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            modules = root / "modules"
            write_module(modules, "alpha")
            write_module(
                modules,
                "beta",
                dependencies=[
                    {"id": "alpha", "min_version": "1.0.0", "optional": False}
                ],
            )
            core_db, _api, runtime = self.make_runtime(root)
            runtime.start()
            try:
                snapshot = runtime.snapshot()
                self.assertEqual(snapshot["start_order"], ["alpha", "beta"])
                states = {
                    item["id"]: item["state"]
                    for item in snapshot["modules"]
                }
                self.assertEqual(states, {"alpha": "running", "beta": "running"})
                self.assertTrue(runtime.health().healthy)

                with self.assertRaises(RuntimeError):
                    runtime.stop_module("alpha")
                runtime.stop_module("beta")
                runtime.stop_module("alpha")
                persisted = {
                    item["id"]: item["status"]
                    for item in core_db.list_modules()
                }
                self.assertEqual(
                    persisted,
                    {"alpha": "stopped", "beta": "stopped"},
                )
            finally:
                runtime.stop()

    def test_missing_dependency_blocks_only_that_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_module(
                root / "modules",
                "beta",
                dependencies=[
                    {"id": "missing", "min_version": "1.0.0", "optional": False}
                ],
            )
            _db, _api, runtime = self.make_runtime(root)
            runtime.start()
            try:
                module = runtime.snapshot()["modules"][0]
                self.assertEqual(module["state"], "blocked")
                self.assertFalse(runtime.health().healthy)
            finally:
                runtime.stop()

    def test_disabled_module_is_discovered_but_not_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_module(root / "modules", "alpha", enabled=False)
            _db, _api, runtime = self.make_runtime(root)
            runtime.start()
            try:
                module = runtime.snapshot()["modules"][0]
                self.assertEqual(module["state"], "disabled")
                self.assertEqual(runtime.snapshot()["start_order"], [])
            finally:
                runtime.stop()

    def test_bad_manifest_degrades_runtime_without_crashing_core_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "modules" / "wrong_folder"
            bad.mkdir(parents=True)
            (bad / "module.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "different_id",
                        "name": "Bad",
                        "version": "1.0.0",
                    }
                ),
                encoding="utf-8",
            )
            _db, _api, runtime = self.make_runtime(root)
            runtime.start()
            try:
                self.assertEqual(len(runtime.snapshot()["discovery_errors"]), 1)
                self.assertFalse(runtime.health().healthy)
            finally:
                runtime.stop()


if __name__ == "__main__":
    unittest.main()
