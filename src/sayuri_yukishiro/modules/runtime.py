"""Обнаружение, зависимости и управляемый жизненный цикл модулей."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from ..core.api import CoreAPI
from ..core.service import ManagedService, ServiceHealth, ServiceState
from ..database import CoreDatabase
from .api import ModuleAPI
from .database import ModuleDatabase
from .manifest import (
    ModuleDependency,
    ModuleManifest,
    ModuleManifestError,
    load_manifest,
    version_at_least,
)


@dataclass(frozen=True)
class ModuleContext:
    manifest: ModuleManifest
    api: ModuleAPI
    database: ModuleDatabase
    root: Path


@dataclass
class ModuleRecord:
    manifest: ModuleManifest
    database: ModuleDatabase
    api: ModuleAPI
    state: str = "discovered"
    detail: str = ""
    instance: Any = None
    applied_migrations: list[int] = field(default_factory=list)


class ModuleRuntime(ManagedService):
    """In-process runtime модулей.

    Permission-scoped ModuleAPI является capability boundary. Он не является
    process sandbox: доверенный Python-модуль технически способен импортировать
    внутренности приложения. Недоверенный код должен исполняться отдельно.
    """

    name = "modules"

    def __init__(
        self,
        core_db: CoreDatabase,
        core_api: CoreAPI,
        *,
        modules_root: Path,
        module_data_dir: Path,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self._core_db = core_db
        self._core_api = core_api
        self._modules_root = modules_root
        self._module_data_dir = module_data_dir
        self._enabled = enabled
        self._records: dict[str, ModuleRecord] = {}
        self._start_order: list[str] = []
        self._discovery_errors: list[dict[str, str]] = []
        self._runtime_lock = RLock()

    @property
    def modules_root(self) -> Path:
        return self._modules_root

    def on_start(self) -> None:
        with self._runtime_lock:
            self._records = {}
            self._start_order = []
            self._discovery_errors = []
            if not self._enabled:
                return
            self._discover_locked()
            self._autostart_locked()

    def on_stop(self) -> None:
        with self._runtime_lock:
            for module_id in reversed(self._start_order[:]):
                record = self._records.get(module_id)
                if record is None or record.state != "running":
                    continue
                self._stop_record_locked(record)
            self._start_order.clear()

    def _discover_locked(self) -> None:
        root = self._modules_root
        if not root.is_dir():
            return

        for manifest_path in sorted(root.glob("*/module.json")):
            module_root = manifest_path.parent
            try:
                manifest = load_manifest(module_root)
                if module_root.name != manifest.id:
                    raise ModuleManifestError(
                        f"каталог {module_root.name!r} должен совпадать с id {manifest.id!r}"
                    )
                if manifest.id in self._records:
                    raise ModuleManifestError(f"повторный module id: {manifest.id}")
                db_path = self._module_data_dir / f"{manifest.id}.db"
                database = ModuleDatabase(db_path)
                api = ModuleAPI(manifest.id, self._core_api, manifest.permissions)
                record = ModuleRecord(manifest=manifest, database=database, api=api)
                self._records[manifest.id] = record
                self._core_db.register_module(
                    manifest.id,
                    manifest.name,
                    manifest.version,
                    db_path=db_path,
                )
                self._set_state_locked(record, "discovered")
            except Exception as exc:
                self._discovery_errors.append(
                    {"path": str(manifest_path), "error": str(exc)}
                )

    def _autostart_locked(self) -> None:
        pending: set[str] = set()
        for module_id, record in self._records.items():
            if not record.manifest.enabled:
                self._set_state_locked(record, "disabled")
            else:
                pending.add(module_id)

        while pending:
            progressed = False
            for module_id in sorted(tuple(pending)):
                record = self._records[module_id]
                verdict = self._dependency_verdict_locked(record)
                if verdict[0] == "wait":
                    continue
                pending.remove(module_id)
                progressed = True
                if verdict[0] == "blocked":
                    self._set_state_locked(record, "blocked", verdict[1])
                    continue
                self._start_record_locked(record)

            if not progressed:
                # Оставшиеся модули зависят друг от друга по циклу.
                cycle = ", ".join(sorted(pending))
                for module_id in sorted(pending):
                    self._set_state_locked(
                        self._records[module_id],
                        "blocked",
                        f"циклическая или неразрешимая зависимость: {cycle}",
                    )
                pending.clear()

    def _dependency_verdict_locked(self, record: ModuleRecord) -> tuple[str, str]:
        for dependency in record.manifest.dependencies:
            target = self._records.get(dependency.id)
            if target is None:
                if dependency.optional:
                    continue
                return "blocked", f"нет обязательного модуля {dependency.id}"

            if not version_at_least(target.manifest.version, dependency.min_version):
                if dependency.optional:
                    continue
                return (
                    "blocked",
                    f"{dependency.id}={target.manifest.version}, "
                    f"требуется >= {dependency.min_version}",
                )

            if target.state in {"failed", "blocked", "disabled"}:
                if dependency.optional:
                    continue
                return "blocked", f"зависимость {dependency.id} имеет state={target.state}"

            if target.state != "running":
                if dependency.optional:
                    continue
                return "wait", f"ожидание {dependency.id}"
        return "ready", ""

    def _start_record_locked(self, record: ModuleRecord) -> None:
        try:
            record.applied_migrations = record.database.initialize(
                record.manifest.migrations
            )
            if record.instance is None:
                record.instance = self._load_instance(record)
            start = getattr(record.instance, "start", None)
            stop = getattr(record.instance, "stop", None)
            if not callable(start) or not callable(stop):
                raise TypeError("entrypoint обязан реализовать start() и stop()")
            start()
        except Exception as exc:
            self._set_state_locked(record, "failed", str(exc))
            self._core_api.publish(
                "module.failed",
                {"module_id": record.manifest.id, "error": str(exc)},
                source="module_runtime",
            )
            return

        self._set_state_locked(record, "running")
        if record.manifest.id not in self._start_order:
            self._start_order.append(record.manifest.id)
        self._core_api.publish(
            "module.started",
            {
                "module_id": record.manifest.id,
                "version": record.manifest.version,
                "applied_migrations": list(record.applied_migrations),
            },
            source="module_runtime",
        )

    def _stop_record_locked(self, record: ModuleRecord) -> None:
        try:
            stop = getattr(record.instance, "stop", None)
            if callable(stop):
                stop()
        except Exception as exc:
            self._set_state_locked(record, "failed", f"stop: {exc}")
            return

        self._set_state_locked(record, "stopped")
        self._core_api.publish(
            "module.stopped",
            {"module_id": record.manifest.id},
            source="module_runtime",
        )

    def _load_instance(self, record: ModuleRecord) -> Any:
        manifest = record.manifest
        path = manifest.entrypoint_path.resolve()
        if manifest.root != path and manifest.root not in path.parents:
            raise ModuleManifestError("entrypoint вышел за пределы каталога модуля")
        if not path.is_file():
            raise FileNotFoundError(f"entrypoint не найден: {path}")

        import_name = (
            f"_sayuri_module_{manifest.id}_"
            f"{manifest.version.replace('.', '_')}"
        )
        spec = importlib.util.spec_from_file_location(import_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"не удалось создать import spec для {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[import_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(import_name, None)
            raise

        factory = getattr(module, manifest.entrypoint_symbol, None)
        if not callable(factory):
            raise TypeError(
                f"{manifest.entrypoint_symbol} не является вызываемым entrypoint"
            )
        context = ModuleContext(
            manifest=manifest,
            api=record.api,
            database=record.database,
            root=manifest.root,
        )
        return factory(context)

    def _set_state_locked(
        self,
        record: ModuleRecord,
        state: str,
        detail: str = "",
    ) -> None:
        record.state = state
        record.detail = detail
        self._core_db.set_module_status(record.manifest.id, state)

    def start_module(self, module_id: str) -> dict[str, Any]:
        with self._runtime_lock:
            record = self._records[module_id]
            if record.state == "running":
                return self._record_snapshot_locked(record)

            for dependency in record.manifest.dependencies:
                target = self._records.get(dependency.id)
                if target is None:
                    if dependency.optional:
                        continue
                    raise RuntimeError(f"Нет обязательного модуля {dependency.id}")
                if not version_at_least(
                    target.manifest.version, dependency.min_version
                ):
                    if dependency.optional:
                        continue
                    raise RuntimeError(
                        f"{dependency.id}={target.manifest.version}, "
                        f"требуется >= {dependency.min_version}"
                    )
                if target.state != "running" and not dependency.optional:
                    raise RuntimeError(
                        f"Сначала запустите зависимость {dependency.id}"
                    )

            self._start_record_locked(record)
            return self._record_snapshot_locked(record)

    def stop_module(self, module_id: str) -> dict[str, Any]:
        with self._runtime_lock:
            record = self._records[module_id]
            if record.state != "running":
                return self._record_snapshot_locked(record)

            dependants = [
                item.manifest.id
                for item in self._records.values()
                if item.state == "running"
                and any(
                    dependency.id == module_id and not dependency.optional
                    for dependency in item.manifest.dependencies
                )
            ]
            if dependants:
                raise RuntimeError(
                    "Нельзя остановить модуль: от него зависят "
                    + ", ".join(sorted(dependants))
                )
            self._stop_record_locked(record)
            if module_id in self._start_order:
                self._start_order.remove(module_id)
            return self._record_snapshot_locked(record)

    def dependency_graph(self) -> dict[str, list[dict[str, Any]]]:
        with self._runtime_lock:
            return {
                module_id: [
                    dependency.to_dict()
                    for dependency in record.manifest.dependencies
                ]
                for module_id, record in sorted(self._records.items())
            }

    def snapshot(self) -> dict[str, Any]:
        with self._runtime_lock:
            return {
                "root": str(self._modules_root),
                "enabled": self._enabled,
                "modules": [
                    self._record_snapshot_locked(record)
                    for _module_id, record in sorted(self._records.items())
                ],
                "dependency_graph": self.dependency_graph(),
                "discovery_errors": [dict(item) for item in self._discovery_errors],
                "start_order": list(self._start_order),
            }

    def _record_snapshot_locked(self, record: ModuleRecord) -> dict[str, Any]:
        health = self._instance_health_locked(record)
        return {
            **record.manifest.to_dict(),
            "state": record.state,
            "detail": record.detail,
            "database": str(record.database.path),
            "database_schema": (
                record.database.schema_version()
                if record.database.path.is_file()
                else 0
            ),
            "applied_migrations": list(record.applied_migrations),
            "health": health,
        }

    def _instance_health_locked(self, record: ModuleRecord) -> dict[str, Any]:
        if record.state != "running":
            return {
                "healthy": record.state not in {"failed", "blocked"},
                "detail": record.detail,
            }
        probe = getattr(record.instance, "health", None)
        if not callable(probe):
            return {"healthy": True, "detail": ""}
        try:
            value = probe()
        except Exception as exc:
            return {"healthy": False, "detail": f"health: {exc}"}
        if isinstance(value, bool):
            return {"healthy": value, "detail": ""}
        if isinstance(value, dict):
            return {
                "healthy": bool(value.get("healthy", True)),
                "detail": str(value.get("detail", "")),
            }
        return {"healthy": bool(value), "detail": ""}

    def health(self) -> ServiceHealth:
        state = self.state
        if state != ServiceState.RUNNING:
            return super().health()
        with self._runtime_lock:
            unhealthy = [
                record.manifest.id
                for record in self._records.values()
                if not self._instance_health_locked(record)["healthy"]
            ]
            if self._discovery_errors:
                unhealthy.append("manifest")
            detail = (
                "проблемы: " + ", ".join(sorted(unhealthy))
                if unhealthy
                else f"модулей: {len(self._records)}"
            )
            return ServiceHealth(
                name=self.name,
                state=state.value,
                healthy=not unhealthy,
                detail=detail,
            )
