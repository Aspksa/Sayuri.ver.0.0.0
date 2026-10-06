"""Структурированное журналирование ядра с ротацией файлов."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .service import ManagedService

LOGGER_NAME = "sayuri"


class LoggingService(ManagedService):
    name = "logging"

    def __init__(
        self,
        log_dir: Path,
        *,
        level: str = "INFO",
        max_bytes: int = 2000000,
        backup_count: int = 3,
    ) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.level = level
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.logger = logging.getLogger(LOGGER_NAME)
        self._handler: RotatingFileHandler | None = None

    def on_start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.logger.setLevel(getattr(logging, self.level.upper(), logging.INFO))
        self.logger.propagate = False
        if self._handler is not None:
            return
        handler = RotatingFileHandler(
            self.log_dir / "sayuri-core.log",
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        self.logger.addHandler(handler)
        self._handler = handler
        self.logger.info("core logging started")

    def on_stop(self) -> None:
        if self._handler is None:
            return
        self.logger.info("core logging stopped")
        self.logger.removeHandler(self._handler)
        self._handler.close()
        self._handler = None
