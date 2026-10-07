"""Изолированная SQLite-база одного модуля."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..database import utc_now
from ..storage_policy import sqlite_journal_mode
from .manifest import ModuleMigration


class ModuleDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        mode = sqlite_journal_mode(self.path)
        conn.execute(f"PRAGMA journal_mode = {mode}")
        conn.execute(
            "PRAGMA synchronous = FULL"
            if mode == "DELETE"
            else "PRAGMA synchronous = NORMAL"
        )
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self, migrations: tuple[ModuleMigration, ...]) -> list[int]:
        applied: list[int] = []
        declared = {item.version for item in migrations}
        with self.session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS module_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            known = {
                int(row[0])
                for row in conn.execute(
                    "SELECT version FROM module_schema_migrations"
                ).fetchall()
            }
            unknown = sorted(known - declared)
            if unknown:
                raise RuntimeError(
                    f"Module database is newer than manifest: unknown migrations {unknown}"
                )
            for migration in migrations:
                if migration.version in known:
                    continue
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(
                    """
                    INSERT INTO module_schema_migrations(version, description, applied_at)
                    VALUES(?, ?, ?)
                    """,
                    (migration.version, migration.description, utc_now()),
                )
                applied.append(migration.version)
        return applied

    def schema_version(self) -> int:
        with self.session() as conn:
            row = conn.execute(
                "SELECT MAX(version) FROM module_schema_migrations"
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def quick_check(self) -> str:
        with self.session() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return str(row[0]) if row else "unknown"
