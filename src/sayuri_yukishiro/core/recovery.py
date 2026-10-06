"""Восстановление незавершённой работы после перезапуска.

Ядро возвращает контекст и next_action, но никогда не повторяет
произвольные действия автоматически: решение остаётся за владельцем задачи.
"""

from __future__ import annotations

from typing import Any

from .checkpoints import CheckpointService
from .events import EventBus
from .service import ManagedService


class RecoveryService(ManagedService):
    name = "recovery"

    def __init__(
        self,
        checkpoints: CheckpointService,
        events: EventBus,
        *,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self._checkpoints = checkpoints
        self._events = events
        self._enabled = enabled
        self._pending: list[dict[str, Any]] = []

    def on_start(self) -> None:
        self._pending = self._checkpoints.recoverable() if self._enabled else []
        if self._pending:
            self._events.publish(
                "core.recovery.available",
                {
                    "count": len(self._pending),
                    "tasks": [item["task_id"] for item in self._pending],
                },
                source="core",
            )

    def pending(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._pending]

    def refresh(self) -> list[dict[str, Any]]:
        self._pending = self._checkpoints.recoverable() if self._enabled else []
        return self.pending()
