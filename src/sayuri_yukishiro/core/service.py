"""Управляемый жизненный цикл служб ядра."""

from __future__ import annotations

from abc import ABC
from dataclasses import asdict, dataclass
from enum import Enum
from threading import RLock
from typing import Any

from ..database import CoreDatabase


class ServiceState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class ServiceHealth:
    name: str
    state: str
    healthy: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ManagedService(ABC):
    name = "service"

    def __init__(self) -> None:
        self._state = ServiceState.CREATED
        self._detail = ""
        self._lock = RLock()

    @property
    def state(self) -> ServiceState:
        with self._lock:
            return self._state

    def start(self) -> None:
        with self._lock:
            if self._state == ServiceState.RUNNING:
                return
            self._state = ServiceState.STARTING
        try:
            self.on_start()
        except Exception as exc:
            with self._lock:
                self._state = ServiceState.FAILED
                self._detail = str(exc)
            raise
        with self._lock:
            self._state = ServiceState.RUNNING
            self._detail = ""

    def stop(self) -> None:
        with self._lock:
            if self._state in (ServiceState.CREATED, ServiceState.STOPPED):
                self._state = ServiceState.STOPPED
                return
            self._state = ServiceState.STOPPING
        try:
            self.on_stop()
        except Exception as exc:
            with self._lock:
                self._state = ServiceState.FAILED
                self._detail = str(exc)
            raise
        with self._lock:
            self._state = ServiceState.STOPPED

    def on_start(self) -> None:
        return

    def on_stop(self) -> None:
        return

    def health(self) -> ServiceHealth:
        with self._lock:
            state = self._state
            detail = self._detail
        return ServiceHealth(
            name=self.name,
            state=state.value,
            healthy=state == ServiceState.RUNNING,
            detail=detail,
        )


class ServiceRegistry:
    """Запускает службы в порядке регистрации, останавливает в обратном."""

    def __init__(self, db: CoreDatabase | None = None) -> None:
        self._db = db
        self._services: dict[str, ManagedService] = {}
        self._order: list[str] = []
        self._lock = RLock()

    def register(self, service: ManagedService) -> None:
        with self._lock:
            if service.name in self._services:
                raise ValueError(f"Service already registered: {service.name}")
            self._services[service.name] = service
            self._order.append(service.name)
        self._persist(service)

    def start_all(self) -> None:
        started: list[ManagedService] = []
        try:
            for name in list(self._order):
                service = self._services[name]
                service.start()
                started.append(service)
                self._persist(service)
        except Exception:
            # Частично поднятое ядро опаснее не поднятого: откатываем.
            for service in reversed(started):
                try:
                    service.stop()
                except Exception:
                    pass
                finally:
                    self._persist(service)
            raise

    def stop_all(self) -> None:
        errors: list[Exception] = []
        for name in reversed(list(self._order)):
            service = self._services[name]
            try:
                service.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._persist(service)
        if errors:
            raise RuntimeError(
                f"{len(errors)} core service(s) failed to stop"
            ) from errors[0]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            names = list(self._order)
            services = dict(self._services)
        return [services[name].health().to_dict() for name in names]

    def _persist(self, service: ManagedService) -> None:
        if self._db is None:
            return
        health = service.health()
        try:
            self._db.upsert_service_state(health.name, health.state, health.detail)
        except Exception:
            # Журналирование состояния не должно ломать жизненный цикл.
            pass
