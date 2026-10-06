"""Долговечные контрольные точки с обязательным next_action."""

from __future__ import annotations

from typing import Any

from ..database import CoreDatabase
from .service import ManagedService


class CheckpointService(ManagedService):
    name = "checkpoints"

    def __init__(self, db: CoreDatabase) -> None:
        super().__init__()
        self._db = db

    def save(
        self,
        task_id: str,
        payload: dict[str, Any],
        next_action: str,
        *,
        status: str = "in_progress",
    ) -> None:
        if not task_id.strip():
            raise ValueError("task_id is required")
        if status != "completed" and not next_action.strip():
            raise ValueError("next_action is required for unfinished checkpoints")
        self._db.save_checkpoint(task_id, status, payload, next_action)

    def complete(self, task_id: str, payload: dict[str, Any] | None = None) -> None:
        existing = self._db.get_checkpoint(task_id)
        if existing is None:
            raise KeyError(task_id)
        final_payload = payload if payload is not None else existing["payload"]
        self._db.save_checkpoint(task_id, "completed", final_payload, "")

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._db.get_checkpoint(task_id)

    def recoverable(self) -> list[dict[str, Any]]:
        return self._db.list_recoverable_checkpoints()
