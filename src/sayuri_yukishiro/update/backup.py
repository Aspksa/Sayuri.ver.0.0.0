"""Резервная копия перед обновлением.

Миграции базы применяет новая версия кода, и откатить их кодом нельзя.
Поэтому состояние снимается до обновления: откат возвращает и файлы, и
базу. Без этого «безопасный откат» был бы обещанием, а не фактом.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database import CoreDatabase
from ..paths import DATA_DIR, PROJECT_ROOT, ensure_runtime_dirs

BACKUP_DIR_NAME = "backups"
KEEP_BACKUPS = 5
METADATA_NAME = "backup.json"

# Небольшие файлы, определяющие версию и состояние проекта.
TRACKED_FILES = (
    "VERSION",
    "PROJECT_STATE.json",
    "UPDATE_IDS.json",
    "UPDATE_LOG.md",
)


@dataclass(frozen=True)
class BackupRecord:
    id: str
    path: str
    commit: str
    project_version: str
    created_at: str
    database: str | None
    files: list[str]
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def backup_root() -> Path:
    ensure_runtime_dirs()
    root = DATA_DIR / BACKUP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def create_backup(
    *,
    commit: str,
    project_version: str,
    db: CoreDatabase | None = None,
    project_root: Path | None = None,
) -> BackupRecord:
    root = project_root or PROJECT_ROOT
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_root() / f"{stamp}-{commit[:12]}"
    target.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    for name in TRACKED_FILES:
        source = root / name
        if source.is_file():
            shutil.copy2(source, target / name)
            saved_files.append(name)

    database_name: str | None = None
    database = db or CoreDatabase()
    if database.path.is_file():
        database_name = database.path.name
        # Копия делается средствами SQLite: обычный copy во время записи
        # может дать нецелостный файл.
        with database.session() as conn:
            destination = __import__("sqlite3").connect(target / database_name)
            try:
                conn.backup(destination)
            finally:
                destination.close()

    record = BackupRecord(
        id=target.name,
        path=str(target),
        commit=commit,
        project_version=project_version,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        database=database_name,
        files=saved_files,
        size_bytes=_directory_size(target),
    )
    (target / METADATA_NAME).write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    prune_backups()
    return record


def list_backups() -> list[dict[str, Any]]:
    root = backup_root()
    records: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        metadata = directory / METADATA_NAME
        if not metadata.is_file():
            continue
        try:
            records.append(json.loads(metadata.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return records


def prune_backups(keep: int = KEEP_BACKUPS) -> list[str]:
    root = backup_root()
    directories = [item for item in sorted(root.iterdir(), reverse=True) if item.is_dir()]
    removed: list[str] = []
    for directory in directories[max(0, int(keep)) :]:
        shutil.rmtree(directory, ignore_errors=True)
        removed.append(directory.name)
    return removed


def restore_backup(
    backup_id: str,
    *,
    db: CoreDatabase | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Вернуть базу и файлы состояния из копии."""

    source = backup_root() / backup_id
    if not source.is_dir():
        raise FileNotFoundError(f"Резервная копия не найдена: {backup_id}")

    root = project_root or PROJECT_ROOT
    restored: list[str] = []
    for name in TRACKED_FILES:
        candidate = source / name
        if candidate.is_file():
            shutil.copy2(candidate, root / name)
            restored.append(name)

    database = db or CoreDatabase()
    database_restored = False
    metadata_path = source / METADATA_NAME
    database_name = None
    if metadata_path.is_file():
        try:
            database_name = json.loads(metadata_path.read_text(encoding="utf-8")).get("database")
        except (OSError, json.JSONDecodeError):
            database_name = None
    if database_name:
        candidate = source / database_name
        if candidate.is_file():
            shutil.copy2(candidate, database.path)
            database_restored = True

    return {
        "backup_id": backup_id,
        "files": restored,
        "database_restored": database_restored,
    }
