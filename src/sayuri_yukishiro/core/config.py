"""Единая конфигурация ядра: значения по умолчанию, файл, окружение."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any

from .service import ManagedService

DEFAULT_CONFIG: dict[str, Any] = {
    "core": {
        "max_workers": 4,
        "job_history_limit": 500,
        "recovery_enabled": True,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "auto_port": True,
        "open_browser": True,
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 2000000,
        "backup_count": 3,
    },
}

_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() not in _FALSE_VALUES


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


class ConfigurationService(ManagedService):
    name = "configuration"

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path
        self._data: dict[str, Any] = deepcopy(DEFAULT_CONFIG)
        self._data_lock = RLock()

    def on_start(self) -> None:
        data = deepcopy(DEFAULT_CONFIG)

        if self._config_path is not None and self._config_path.is_file():
            loaded = json.loads(self._config_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("System configuration root must be an object")
            self._deep_merge(data, loaded)

        overrides: tuple[tuple[tuple[str, str], Any], ...] = (
            (("core", "max_workers"), _env_int("SAYURI_CORE_MAX_WORKERS")),
            (("core", "job_history_limit"), _env_int("SAYURI_JOB_HISTORY_LIMIT")),
            (("core", "recovery_enabled"), _env_flag("SAYURI_RECOVERY_ENABLED")),
            (("server", "port"), _env_int("SAYURI_PORT")),
            (("server", "auto_port"), _env_flag("SAYURI_AUTO_PORT")),
            (("server", "open_browser"), _env_flag("SAYURI_OPEN_BROWSER")),
        )
        for (section, key), value in overrides:
            if value is not None:
                data[section][key] = value

        level = os.environ.get("SAYURI_LOG_LEVEL", "").strip()
        if level:
            data["logging"]["level"] = level.upper()

        with self._data_lock:
            self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        with self._data_lock:
            current: Any = self._data
            for part in path.split("."):
                if not isinstance(current, dict) or part not in current:
                    return default
                current = current[part]
            return deepcopy(current)

    def snapshot(self) -> dict[str, Any]:
        with self._data_lock:
            return deepcopy(self._data)

    @classmethod
    def _deep_merge(cls, target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                cls._deep_merge(target[key], value)
            else:
                target[key] = value
