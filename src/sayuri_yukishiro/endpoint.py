"""Файл точки доступа и защита от второго запуска с того же носителя.

Launcher и tray не угадывают порт: ядро записывает фактический адрес в
data/runtime/endpoint.json, а потребители читают его. Там же хранится
токен мягкого завершения, поэтому остановка не требует Kill.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import RUNTIME_DIR, ensure_runtime_dirs

ENDPOINT_FILENAME = "endpoint.json"
STALE_AFTER_SECONDS = 3600


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    pid: int
    url: str
    token: str
    project_version: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("token", None)
        return data


def endpoint_path() -> Path:
    return RUNTIME_DIR / ENDPOINT_FILENAME


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write_endpoint(
    *,
    host: str,
    port: int,
    token: str,
    project_version: str,
    pid: int | None = None,
) -> Endpoint:
    ensure_runtime_dirs()
    endpoint = Endpoint(
        host=host,
        port=port,
        pid=pid if pid is not None else os.getpid(),
        url=f"http://{host}:{port}/",
        token=token,
        project_version=project_version,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    path = endpoint_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(endpoint.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Атомарная замена: читатель никогда не увидит половину файла.
    temporary.replace(path)
    return endpoint


def read_endpoint() -> Endpoint | None:
    path = endpoint_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return Endpoint(
            host=str(data["host"]),
            port=int(data["port"]),
            pid=int(data["pid"]),
            url=str(data["url"]),
            token=str(data.get("token", "")),
            project_version=str(data.get("project_version", "")),
            started_at=str(data.get("started_at", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def clear_endpoint() -> None:
    try:
        endpoint_path().unlink(missing_ok=True)
    except OSError:
        pass


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_alive(pid: int) -> bool:
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def running_instance() -> Endpoint | None:
    """Живой экземпляр, запущенный с этого носителя, либо None."""

    endpoint = read_endpoint()
    if endpoint is None:
        return None
    if endpoint.pid == os.getpid():
        return None
    if not process_alive(endpoint.pid):
        clear_endpoint()
        return None
    return endpoint
