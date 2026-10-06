"""Внутренняя шина событий ядра с изоляцией ошибок обработчиков."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from ..database import CoreDatabase
from .service import ManagedService

WILDCARD = "*"


@dataclass(frozen=True)
class CoreEvent:
    id: str
    event_type: str
    source: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EventHandler = Callable[[CoreEvent], None]


class EventBus(ManagedService):
    name = "event_bus"

    def __init__(self, db: CoreDatabase | None = None) -> None:
        super().__init__()
        self._db = db
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock_handlers = RLock()

    def subscribe(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        with self._lock_handlers:
            self._handlers[event_type].append(handler)

        def unsubscribe() -> None:
            with self._lock_handlers:
                handlers = self._handlers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "core",
    ) -> CoreEvent:
        event = CoreEvent(
            id=str(uuid4()),
            event_type=event_type,
            source=source,
            payload=dict(payload or {}),
            created_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        )

        if self._db is not None:
            try:
                self._db.append_event(
                    event_type,
                    source,
                    {"event_id": event.id, **event.payload},
                )
            except Exception:
                pass

        with self._lock_handlers:
            handlers = list(self._handlers.get(event_type, []))
            handlers.extend(self._handlers.get(WILDCARD, []))

        # Сбой одного обработчика не отменяет доставку остальным.
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                if self._db is not None:
                    try:
                        self._db.audit(
                            "event_bus",
                            "handler_error",
                            event_type,
                            {"error": str(exc), "event_id": event.id},
                        )
                    except Exception:
                        pass
        return event
