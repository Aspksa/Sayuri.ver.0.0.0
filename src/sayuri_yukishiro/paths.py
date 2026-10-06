"""Переносимые пути проекта.

Ни один компонент не вправе предполагать букву диска или каталог установки.
Все пути выводятся от корня проекта, поэтому проект работает с любого
носителя: внутреннего диска, внешнего диска или USB-флешки.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = PROJECT_ROOT / "web"
CONFIG_FILE = PROJECT_ROOT / "config" / "system.json"
VERSION_FILE = PROJECT_ROOT / "VERSION"


def _resolve_data_dir() -> Path:
    """Каталог runtime-данных.

    По умолчанию data/ рядом с проектом — это и делает носитель
    самодостаточным. SAYURI_DATA_DIR позволяет вынести данные, например
    когда носитель смонтирован только для чтения.
    """

    raw = os.environ.get("SAYURI_DATA_DIR", "").strip()
    if not raw:
        return PROJECT_ROOT / "data"
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve(strict=False)


DATA_DIR = _resolve_data_dir()
CORE_DATA_DIR = DATA_DIR / "core"
MODULE_DATA_DIR = DATA_DIR / "modules"
LOG_DIR = DATA_DIR / "logs"
CACHE_DIR = DATA_DIR / "cache"
RUNTIME_DIR = DATA_DIR / "runtime"

_RUNTIME_DIRS = (
    DATA_DIR,
    CORE_DATA_DIR,
    MODULE_DATA_DIR,
    LOG_DIR,
    CACHE_DIR,
    RUNTIME_DIR,
)


def ensure_runtime_dirs() -> None:
    for path in _RUNTIME_DIRS:
        path.mkdir(parents=True, exist_ok=True)


def project_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0-unknown"
    except OSError:
        return "0.0.0-unknown"
