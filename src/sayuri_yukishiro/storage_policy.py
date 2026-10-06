"""Политика хранения SQLite в зависимости от носителя.

WAL быстрее, но повреждается на синхронизируемых, сетевых и съёмных
носителях: там файлы -wal и -shm могут рассинхронизироваться с базой при
отключении или синхронизации. Поэтому для таких путей используется DELETE
с synchronous=FULL, а WAL остаётся только для безопасного локального диска.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

ALLOWED_JOURNAL_MODES = frozenset({"WAL", "DELETE"})

# Коды GetDriveTypeW: 2 — съёмный носитель, 4 — сетевой диск.
_DRIVE_REMOVABLE = 2
_DRIVE_REMOTE = 4

_SYNC_DIR_MARKERS = ("onedrive", "dropbox", "google drive", "yandexdisk", "icloud")


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _is_synced_path(path: Path) -> bool:
    for variable in (
        "OneDrive",
        "OneDriveCommercial",
        "OneDriveConsumer",
        "Dropbox",
    ):
        raw = os.environ.get(variable)
        if raw and _is_under(path, Path(raw)):
            return True
    parts = [part.casefold() for part in path.parts]
    return any(part.startswith(_SYNC_DIR_MARKERS) for part in parts)


def _is_unc_path(path: Path) -> bool:
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


def windows_drive_type(path: Path) -> int | None:
    if os.name != "nt":
        return None
    anchor = path.resolve(strict=False).anchor
    if not anchor:
        return None
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(str(anchor)))
    except Exception:
        return None


def describe_media(path: Path) -> str:
    resolved = path.resolve(strict=False)
    if _is_unc_path(resolved):
        return "network"
    if _is_synced_path(resolved):
        return "synced"
    drive_type = windows_drive_type(resolved)
    if drive_type == _DRIVE_REMOVABLE:
        return "removable"
    if drive_type == _DRIVE_REMOTE:
        return "network"
    return "local"


def sqlite_journal_mode(path: Path) -> str:
    override = os.environ.get("SAYURI_SQLITE_JOURNAL_MODE", "").strip().upper()
    if override:
        if override not in ALLOWED_JOURNAL_MODES:
            raise ValueError("SAYURI_SQLITE_JOURNAL_MODE must be WAL or DELETE")
        return override
    return "WAL" if describe_media(path) == "local" else "DELETE"
