"""Системное ядро Sayuri Yukishiro.

Ядро собирает службы, поднимает их в детерминированном порядке и даёт
единую точку состояния. Оно не содержит прикладной логики.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from ..database import CoreDatabase
from ..paths import CONFIG_FILE, LOG_DIR, project_version
from ..version import CORE_VERSION
from .api import CoreAPI
from .checkpoints import CheckpointService
from .config import ConfigurationService
from .events import EventBus
from .health import HealthMonitor
from .jobs import JobManager
from .logging_service import LoggingService
from .recovery import RecoveryService
from .service import ServiceRegistry


class SystemCore:
    VERSION = CORE_VERSION

    def __init__(
        self,
        *,
        db: CoreDatabase | None = None,
        config_path: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self.db = db or CoreDatabase()
        self.applied_migrations = self.db.initialize()

        self.config = ConfigurationService(
            config_path if config_path is not None else CONFIG_FILE
        )
        self.config.start()

        self.logging = LoggingService(
            log_dir or LOG_DIR,
            level=str(self.config.get("logging.level", "INFO")),
            max_bytes=int(self.config.get("logging.max_bytes", 2000000)),
            backup_count=int(self.config.get("logging.backup_count", 3)),
        )
        self.events = EventBus(self.db)
        self.jobs = JobManager(
            self.db,
            max_workers=int(self.config.get("core.max_workers", 4)),
            max_history=int(self.config.get("core.job_history_limit", 500)),
        )
        self.checkpoints = CheckpointService(self.db)
        self.recovery = RecoveryService(
            self.checkpoints,
            self.events,
            enabled=bool(self.config.get("core.recovery_enabled", True)),
        )
        # Импорт здесь: слой обновления стоит над ядром и зависит от него.
        from ..update.service import UpdateService

        self.update = UpdateService(
            self.db,
            publish=lambda event_type, payload: self.events.publish(
                event_type, payload, source="update"
            ),
        )

        self.registry = ServiceRegistry(self.db)
        for service in (
            self.config,
            self.logging,
            self.events,
            self.jobs,
            self.checkpoints,
            self.recovery,
            self.update,
        ):
            self.registry.register(service)

        self.health = HealthMonitor(self.registry)
        self.api = CoreAPI(self)
        self._running = False
        self._lock = RLock()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self.registry.start_all()
            self._running = True

        self.events.publish(
            "core.started",
            {
                "project_version": project_version(),
                "core_version": self.VERSION,
                "schema_version": self.db.schema_version(),
                "applied_migrations": list(self.applied_migrations),
            },
            source="core",
        )
        self.logging.logger.info(
            "core started: project=%s core=%s schema=%s",
            project_version(),
            self.VERSION,
            self.db.schema_version(),
        )

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self.events.publish("core.stopping", {}, source="core")
            self.logging.logger.info("core stopping")
            try:
                self.registry.stop_all()
            finally:
                self._running = False

    def __enter__(self) -> "SystemCore":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def status(self, *, deep: bool = False) -> dict[str, Any]:
        return {
            "project": "Sayuri Yukishiro",
            "project_version": project_version(),
            "core_version": self.VERSION,
            "running": self.running,
            "schema_version": self.db.schema_version(),
            "health": self.health.snapshot(),
            "recoverable_tasks": len(self.recovery.pending()),
            # quick_check читает всю базу — только по запросу, не на каждый polling.
            "database_check": self.db.quick_check() if deep else "not_checked",
        }
