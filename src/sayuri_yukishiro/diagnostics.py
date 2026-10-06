"""Предстартовая диагностика.

Цель — до запуска ответить на один вопрос: на этой машине и с этого
носителя система поднимется или нет, и если нет, то почему именно.
"""

from __future__ import annotations

import shutil
import socket
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .core.runtime import SystemCore
from .database import CoreDatabase, SCHEMA_VERSION
from .endpoint import running_instance
from .paths import DATA_DIR, PROJECT_ROOT, WEB_DIR, ensure_runtime_dirs
from .storage_policy import describe_media, sqlite_journal_mode
from .version import DEFAULT_PORT

MIN_PYTHON = (3, 11)

FATAL = "fatal"
WARNING = "warning"
INFO = "info"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    severity: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check_python() -> Check:
    version = sys.version_info
    ok = (version.major, version.minor) >= MIN_PYTHON
    return Check(
        "python",
        ok,
        INFO if ok else FATAL,
        f"Python {version.major}.{version.minor}.{version.micro}; "
        f"требуется >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
    )


def _check_media() -> Check:
    media = describe_media(DATA_DIR)
    mode = sqlite_journal_mode(DATA_DIR)
    return Check(
        "media",
        True,
        INFO,
        f"Носитель: {media}; режим журнала SQLite: {mode}",
    )


def _check_storage() -> Check:
    try:
        ensure_runtime_dirs()
        probe = DATA_DIR / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return Check("storage", True, INFO, f"Хранилище доступно для записи: {DATA_DIR}")
    except OSError as exc:
        return Check(
            "storage",
            False,
            FATAL,
            f"Нет записи в {DATA_DIR}: {exc}. Задайте SAYURI_DATA_DIR на записываемый путь.",
        )


def _check_web_shell() -> Check:
    index = WEB_DIR / "index.html"
    if index.is_file():
        return Check("web_shell", True, INFO, f"Интерфейс найден: {index}")
    return Check("web_shell", False, FATAL, f"Нет файла интерфейса: {index}")


def _check_database() -> Check:
    try:
        db = CoreDatabase()
        db.initialize()
        result = db.quick_check()
        ok = result.lower() == "ok"
        return Check(
            "database",
            ok,
            INFO if ok else FATAL,
            f"SQLite quick_check: {result}; схема v{db.schema_version()}; {db.path}",
        )
    except Exception as exc:
        return Check("database", False, FATAL, f"База ядра недоступна: {exc}")


def _check_core() -> Check:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = SystemCore(
                db=CoreDatabase(root / "core-smoke.db"),
                config_path=root / "system.json",
                log_dir=root / "logs",
            )
            core.start()
            try:
                status = core.status()
                job_id = core.api.submit_job("smoke", lambda: "ok")
                job_result = core.jobs.wait(job_id, timeout=10)
            finally:
                core.stop()

        health = status["health"]
        ok = (
            health["overall"] == "healthy"
            and job_result == "ok"
            and status["schema_version"] == SCHEMA_VERSION
        )
        return Check(
            "core",
            ok,
            INFO if ok else FATAL,
            f"Ядро v{status['core_version']}; службы "
            f"{health['healthy_count']}/{health['service_count']}; "
            f"схема v{status['schema_version']}; фоновая задача: {job_result}",
        )
    except Exception as exc:
        return Check("core", False, FATAL, f"Ядро не поднялось: {exc}")


def _check_browser() -> Check:
    import webbrowser

    try:
        webbrowser.get()
        return Check("browser", True, INFO, "Браузер по умолчанию доступен")
    except Exception:
        return Check(
            "browser",
            False,
            WARNING,
            "Браузер по умолчанию не определён. Система запустится, "
            "но сайт придётся открыть вручную.",
        )


def _check_instance() -> Check:
    existing = running_instance()
    if existing is None:
        return Check("instance", True, INFO, "Других экземпляров с этого носителя нет")
    return Check(
        "instance",
        False,
        WARNING,
        f"Уже запущен экземпляр: {existing.url} (pid {existing.pid})",
    )


def _check_port(port: int) -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        busy = probe.connect_ex(("127.0.0.1", port)) == 0
    if busy:
        return Check(
            "port",
            True,
            INFO,
            f"Порт {port} занят — будет взят свободный порт автоматически.",
        )
    return Check("port", True, INFO, f"Порт {port} свободен.")


def _check_git() -> Check:
    git = shutil.which("git")
    if git:
        return Check("git", True, INFO, f"Git доступен: {git}")
    return Check(
        "git",
        False,
        WARNING,
        "Git не найден. Система работает, обновление из репозитория недоступно.",
    )


def run_diagnostics(port: int = DEFAULT_PORT) -> list[Check]:
    return [
        Check("project_root", True, INFO, str(PROJECT_ROOT)),
        _check_python(),
        _check_media(),
        _check_storage(),
        _check_web_shell(),
        _check_database(),
        _check_core(),
        _check_browser(),
        _check_instance(),
        _check_port(port),
        _check_git(),
    ]


def has_fatal_failures(checks: list[Check]) -> bool:
    return any((not check.ok) and check.severity == FATAL for check in checks)


def print_report(checks: list[Check]) -> None:
    print("Диагностика Sayuri Yukishiro")
    print("-" * 70)
    for check in checks:
        if check.ok:
            mark = "OK"
        elif check.severity == WARNING:
            mark = "WARN"
        else:
            mark = "FAIL"
        print(f"[{mark:<4}] {check.name:<13} {check.detail}")
    print("-" * 70)
