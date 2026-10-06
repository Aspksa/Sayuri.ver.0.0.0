"""Ограниченный интерфейс ядра для будущих модулей.

Модуль не получает SystemCore целиком: только то, что перечислено здесь.
"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import SystemCore


class CoreAPI:
    def __init__(self, core: "SystemCore") -> None:
        self._core = core

    def status(self) -> dict[str, Any]:
        return self._core.status()

    def config_get(self, path: str, default: Any = None) -> Any:
        return self._core.config.get(path, default)

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "module",
    ) -> dict[str, Any]:
        return self._core.events.publish(event_type, payload, source=source).to_dict()

    def subscribe(
        self,
        event_type: str,
        handler: Callable[[Any], None],
    ) -> Callable[[], None]:
        return self._core.events.subscribe(event_type, handler)

    def submit_job(
        self,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        return self._core.jobs.submit(name, func, *args, **kwargs)

    def job_status(self, job_id: str) -> dict[str, Any]:
        return self._core.jobs.get(job_id)

    def save_checkpoint(
        self,
        task_id: str,
        payload: dict[str, Any],
        next_action: str,
    ) -> None:
        self._core.checkpoints.save(task_id, payload, next_action)

    def complete_checkpoint(
        self,
        task_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._core.checkpoints.complete(task_id, payload)

    def recoverable_tasks(self) -> list[dict[str, Any]]:
        return self._core.recovery.refresh()

    def log(self, message: str, *, level: str = "info") -> None:
        logger = self._core.logging.logger
        getattr(logger, level.lower(), logger.info)(message)
