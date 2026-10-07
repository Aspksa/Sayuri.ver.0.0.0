"""Центральная база ядра.

Модель двухуровневая: ядро владеет data/core/sayuri_yukishiro.db, каждый
будущий модуль получает свою data/modules/<id>.db. Миграции объявлены
списком и применяются по порядку, поэтому схема всегда воспроизводима.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import CORE_DATA_DIR, MODULE_DATA_DIR, ensure_runtime_dirs
from .storage_policy import sqlite_journal_mode

CORE_DB_NAME = "sayuri_yukishiro.db"
SAFE_MODULE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=repr)


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="core foundation",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS modules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'registered',
                db_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope, key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, id)",
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        version=2,
        description="core services, jobs and checkpoints",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS core_services (
                name TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                detail TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS core_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_core_jobs_status ON core_jobs(status, updated_at)",
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                next_action TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_checkpoints_status ON checkpoints(status, updated_at)",
        ),
    ),
    Migration(
        version=3,
        description="session history of local runs",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS runtime_sessions (
                id TEXT PRIMARY KEY,
                pid INTEGER NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                project_version TEXT NOT NULL,
                media TEXT NOT NULL,
                started_at TEXT NOT NULL,
                stopped_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_runtime_sessions_started ON runtime_sessions(started_at)",
        ),
    ),
)

MIGRATIONS = MIGRATIONS + (
    Migration(
        version=4,
        description="update runs journal",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS update_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                from_commit TEXT NOT NULL,
                to_commit TEXT NOT NULL,
                from_version TEXT NOT NULL,
                to_version TEXT NOT NULL,
                stages_json TEXT,
                error TEXT,
                rolled_back INTEGER NOT NULL DEFAULT 0,
                backup_id TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_update_runs_started ON update_runs(started_at)",
        ),
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1].version


class CoreDatabase:
    def __init__(self, path: Path | None = None) -> None:
        ensure_runtime_dirs()
        self.path = path or (CORE_DATA_DIR / CORE_DB_NAME)

    # --- соединения -------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        mode = sqlite_journal_mode(self.path)
        conn.execute(f"PRAGMA journal_mode = {mode}")
        conn.execute(
            "PRAGMA synchronous = FULL" if mode == "DELETE" else "PRAGMA synchronous = NORMAL"
        )
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """Явный цикл open -> transaction -> close.

        Соединение закрывается всегда: на Windows незакрытый handle не даёт
        безопасно извлечь носитель и удалить файл базы.
        """

        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- схема ------------------------------------------------------

    def initialize(self) -> list[int]:
        applied: list[int] = []
        with self.session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            known = {
                int(row[0])
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            unknown = sorted(known - {item.version for item in MIGRATIONS})
            if unknown:
                raise RuntimeError(
                    f"Core database is newer than this build: unknown migrations {unknown}"
                )
            for migration in MIGRATIONS:
                if migration.version in known:
                    continue
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version, description, applied_at)
                    VALUES(?, ?, ?)
                    """,
                    (migration.version, migration.description, utc_now()),
                )
                applied.append(migration.version)
            conn.execute(
                """
                INSERT INTO meta(key, value, updated_at) VALUES('schema_version', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (str(SCHEMA_VERSION), utc_now()),
            )
        return applied

    def schema_version(self) -> int:
        with self.session() as conn:
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def quick_check(self) -> str:
        with self.session() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return str(row[0]) if row else "unknown"

    # --- модули -----------------------------------------------------

    def list_modules(self) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT id, name, version, status, db_path, updated_at FROM modules ORDER BY id"
            ).fetchall()
            return [dict(row) for row in rows]

    def register_module(
        self,
        module_id: str,
        name: str,
        version: str,
        *,
        db_path: Path | None = None,
    ) -> Path:
        if not SAFE_MODULE_ID.fullmatch(module_id):
            raise ValueError(
                "Invalid module id. Use lowercase letters, digits and underscore."
            )
        module_path = db_path or module_database_path(module_id)
        now = utc_now()
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO modules(id, name, version, status, db_path, created_at, updated_at)
                VALUES(?, ?, ?, 'registered', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    version=excluded.version,
                    db_path=excluded.db_path,
                    updated_at=excluded.updated_at
                """,
                (module_id, name, version, str(module_path), now, now),
            )
        return module_path

    def set_module_status(self, module_id: str, status: str) -> None:
        if not SAFE_MODULE_ID.fullmatch(module_id):
            raise ValueError(
                "Invalid module id. Use lowercase letters, digits and underscore."
            )
        with self.session() as conn:
            cursor = conn.execute(
                "UPDATE modules SET status=?, updated_at=? WHERE id=?",
                (status, utc_now(), module_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(module_id)

    # --- события и аудит --------------------------------------------

    def append_event(self, event_type: str, source: str, payload: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO events(event_type, source, payload_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (event_type, source, json_text(payload), utc_now()),
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT event_type, source, payload_json, created_at
                FROM events ORDER BY id DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            return result

    def audit(
        self,
        actor: str,
        action: str,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO audit_log(actor, action, target, details_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (actor, action, target, json_text(details or {}), utc_now()),
            )

    # --- службы -----------------------------------------------------

    def upsert_service_state(self, name: str, state: str, detail: str = "") -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO core_services(name, state, detail, updated_at) VALUES(?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    state=excluded.state, detail=excluded.detail, updated_at=excluded.updated_at
                """,
                (name, state, detail, utc_now()),
            )

    def list_service_states(self) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT name, state, detail, updated_at FROM core_services ORDER BY name"
            ).fetchall()
            return [dict(row) for row in rows]

    # --- задачи -----------------------------------------------------

    def create_core_job(self, job_id: str, name: str, status: str) -> None:
        now = utc_now()
        with self.session() as conn:
            conn.execute(
                "INSERT INTO core_jobs(id, name, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                (job_id, name, status, now, now),
            )

    def update_core_job(
        self,
        job_id: str,
        status: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE core_jobs SET status=?, result_json=?, error=?, updated_at=? WHERE id=?",
                (
                    status,
                    json_text(result) if result is not None else None,
                    error,
                    utc_now(),
                    job_id,
                ),
            )

    def mark_unfinished_jobs_interrupted(self) -> int:
        with self.session() as conn:
            cursor = conn.execute(
                """
                UPDATE core_jobs SET status='interrupted', updated_at=?
                WHERE status IN ('pending', 'running')
                """,
                (utc_now(),),
            )
            return int(cursor.rowcount)

    # --- контрольные точки ------------------------------------------

    def save_checkpoint(
        self,
        task_id: str,
        status: str,
        payload: dict[str, Any],
        next_action: str,
    ) -> None:
        now = utc_now()
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints(task_id, status, payload_json, next_action, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    next_action=excluded.next_action,
                    updated_at=excluded.updated_at
                """,
                (task_id, status, json_text(payload), next_action, now, now),
            )

    def get_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        with self.session() as conn:
            row = conn.execute(
                """
                SELECT task_id, status, payload_json, next_action, created_at, updated_at
                FROM checkpoints WHERE task_id=?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            return item

    def list_recoverable_checkpoints(self) -> list[dict[str, Any]]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT task_id, status, payload_json, next_action, created_at, updated_at
                FROM checkpoints WHERE status != 'completed' ORDER BY updated_at DESC
                """
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                result.append(item)
            return result

    # --- журнал запусков --------------------------------------------

    def open_runtime_session(
        self,
        session_id: str,
        *,
        pid: int,
        host: str,
        port: int,
        project_version: str,
        media: str,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO runtime_sessions(
                    id, pid, host, port, project_version, media, started_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, pid, host, port, project_version, media, utc_now()),
            )

    def close_runtime_session(self, session_id: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE runtime_sessions SET stopped_at=? WHERE id=? AND stopped_at IS NULL",
                (utc_now(), session_id),
            )

    def recent_runtime_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime_sessions ORDER BY started_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
            return [dict(row) for row in rows]


    # --- журнал обновлений ------------------------------------------

    def create_update_run(
        self,
        run_id: str,
        *,
        from_commit: str,
        to_commit: str,
        from_version: str,
        to_version: str,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO update_runs(
                    id, status, from_commit, to_commit,
                    from_version, to_version, started_at
                )
                VALUES(?, 'running', ?, ?, ?, ?, ?)
                """,
                (run_id, from_commit, to_commit, from_version, to_version, utc_now()),
            )

    def finish_update_run(
        self,
        run_id: str,
        *,
        status: str,
        stages: list[dict[str, Any]] | None = None,
        error: str = "",
        rolled_back: bool = False,
        backup_id: str = "",
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE update_runs
                SET status=?, stages_json=?, error=?, rolled_back=?, backup_id=?, finished_at=?
                WHERE id=?
                """,
                (
                    status,
                    json_text(stages or []),
                    error or None,
                    1 if rolled_back else 0,
                    backup_id or None,
                    utc_now(),
                    run_id,
                ),
            )

    def list_update_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT id, status, from_commit, to_commit, from_version, to_version,
                       stages_json, error, rolled_back, backup_id, started_at, finished_at
                FROM update_runs ORDER BY started_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                raw = item.pop("stages_json")
                item["stages"] = json.loads(raw) if raw else []
                item["rolled_back"] = bool(item["rolled_back"])
                result.append(item)
            return result


def module_database_path(module_id: str) -> Path:
    if not SAFE_MODULE_ID.fullmatch(module_id):
        raise ValueError(
            "Invalid module id. Use lowercase letters, digits and underscore."
        )
    ensure_runtime_dirs()
    return MODULE_DATA_DIR / f"{module_id}.db"


@contextmanager
def module_connection(module_id: str) -> Iterator[sqlite3.Connection]:
    path = module_database_path(module_id)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    mode = sqlite_journal_mode(path)
    conn.execute(f"PRAGMA journal_mode = {mode}")
    conn.execute(
        "PRAGMA synchronous = FULL" if mode == "DELETE" else "PRAGMA synchronous = NORMAL"
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
