"""Capability-scoped API, выдаваемый конкретному модулю."""

from __future__ import annotations

from typing import Any, Callable

from ..core.api import CoreAPI


class ModulePermissionError(PermissionError):
    pass


class ModuleAPI:
    def __init__(self, module_id: str, core: CoreAPI, permissions: tuple[str, ...]) -> None:
        self.module_id = module_id
        self._core = core
        self._permissions = frozenset(permissions)
        self._jobs: set[str] = set()
        self._checkpoint_prefix = f"module:{module_id}:"

    @property
    def permissions(self) -> tuple[str, ...]:
        return tuple(sorted(self._permissions))

    def _require(self, permission: str) -> None:
        if permission not in self._permissions:
            raise ModulePermissionError(
                f"Модулю {self.module_id!r} не выдано разрешение {permission!r}"
            )

    def status(self) -> dict[str, Any]:
        self._require("core.status.read")
        return self._core.status()

    def config_get(self, path: str, default: Any = None) -> Any:
        self._require("config.read")
        return self._core.config_get(path, default)

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require("events.publish")
        return self._core.publish(
            event_type,
            payload,
            source=f"module:{self.module_id}",
        )

    def subscribe(
        self,
        event_type: str,
        handler: Callable[[Any], None],
    ) -> Callable[[], None]:
        self._require("events.subscribe")
        return self._core.subscribe(event_type, handler)

    def submit_job(
        self,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        self._require("jobs.submit")
        job_id = self._core.submit_job(
            f"module.{self.module_id}.{name}",
            func,
            *args,
            **kwargs,
        )
        self._jobs.add(job_id)
        return job_id

    def job_status(self, job_id: str) -> dict[str, Any]:
        self._require("jobs.read")
        if job_id not in self._jobs:
            raise ModulePermissionError("Модуль может читать только собственные job id")
        return self._core.job_status(job_id)

    def save_checkpoint(
        self,
        task_id: str,
        payload: dict[str, Any],
        next_action: str,
    ) -> None:
        self._require("checkpoints.write")
        self._core.save_checkpoint(
            self._checkpoint_prefix + task_id,
            payload,
            next_action,
        )

    def complete_checkpoint(
        self,
        task_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._require("checkpoints.write")
        self._core.complete_checkpoint(self._checkpoint_prefix + task_id, payload)

    def recoverable_tasks(self) -> list[dict[str, Any]]:
        self._require("recovery.read")
        result: list[dict[str, Any]] = []
        for item in self._core.recoverable_tasks():
            task_id = str(item.get("task_id", ""))
            if not task_id.startswith(self._checkpoint_prefix):
                continue
            copy = dict(item)
            copy["task_id"] = task_id[len(self._checkpoint_prefix):]
            result.append(copy)
        return result

    def log(self, message: str, *, level: str = "info") -> None:
        self._require("log.write")
        self._core.log(f"[module:{self.module_id}] {message}", level=level)
