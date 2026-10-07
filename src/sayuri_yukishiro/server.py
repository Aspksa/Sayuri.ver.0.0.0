"""Локальный web shell и JSON API.

Сервер слушает только петлевой интерфейс. Если штатный порт занят (а на
чужой машине это обычное дело), ядро берёт свободный порт и записывает
фактический адрес в endpoint.json, после чего сайт открывается сам.
"""

from __future__ import annotations

import errno
import json
import mimetypes
import os
import secrets
import socket
import threading
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from . import console
from .core.runtime import SystemCore
from .database import CoreDatabase
from .endpoint import clear_endpoint, new_token, running_instance, write_endpoint
from .paths import DATA_DIR, WEB_DIR, project_version
from .storage_policy import describe_media
from .version import DEFAULT_HOST, DEFAULT_PORT, SERVER_PRODUCT

TOKEN_HEADER = "X-Sayuri-Token"
LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1"})


class InstanceAlreadyRunning(RuntimeError):
    """С этого носителя уже запущен живой экземпляр."""

    def __init__(self, url: str, pid: int) -> None:
        super().__init__(f"Sayuri is already running at {url} (pid {pid})")
        self.url = url
        self.pid = pid


@dataclass(frozen=True)
class ServerOptions:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    auto_port: bool = True
    open_browser: bool = True


def bind_server(
    host: str,
    port: int,
    *,
    auto_port: bool,
    factory: Any,
) -> Any:
    """Поднять сервер на запрошенном порту, иначе на свободном."""

    try:
        return factory((host, port))
    except OSError as exc:
        busy = exc.errno in {errno.EADDRINUSE, errno.EACCES} or isinstance(
            exc, PermissionError
        )
        if not (auto_port and busy):
            raise
    # Порт 0 — ядро просит свободный порт у операционной системы.
    return factory((host, 0))


def wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = threading.Event()
    step = 0.1
    waited = 0.0
    while waited < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            if probe.connect_ex((host, port)) == 0:
                return True
        deadline.wait(step)
        waited += step
    return False


class SayuriHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], core: SystemCore, token: str) -> None:
        self.core = core
        self.token = token
        self.session_id = str(uuid4())
        super().__init__(address, SayuriHandler)


class SayuriHandler(BaseHTTPRequestHandler):
    server_version = SERVER_PRODUCT
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> SayuriHTTPServer:
        return self.server  # type: ignore[return-value]

    # --- ответы -----------------------------------------------------

    def _send(self, body: bytes, content_type: str, status: int, cache: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict[str, Any] | list[Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status, "no-store")

    def _file(self, relative: str) -> None:
        root = WEB_DIR.resolve()
        target = (root / relative).resolve()
        # Защита от выхода за пределы web/ через ../
        if target != root and root not in target.parents:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
            mime = f"{mime}; charset=utf-8"
        self._send(target.read_bytes(), mime, HTTPStatus.OK, "no-cache")

    def _local_only(self) -> bool:
        if self.client_address[0] in LOCAL_CLIENTS:
            return True
        self._json({"status": "forbidden"}, HTTPStatus.FORBIDDEN)
        return False

    # --- маршруты ---------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        core = self.app.core

        if path == "/api/health":
            status = core.status()
            healthy = status["health"]["overall"] == "healthy"
            self._json(
                {
                    "status": "ok" if healthy else "degraded",
                    "project": "Sayuri Yukishiro",
                    "version": project_version(),
                    "core_version": status["core_version"],
                    "pid": os.getpid(),
                }
            )
            return

        if path == "/api/core":
            self._json(core.status())
            return

        if path == "/api/core/jobs":
            self._json({"jobs": core.jobs.snapshot()})
            return

        if path == "/api/core/recovery":
            self._json({"tasks": core.api.recoverable_tasks()})
            return

        if path == "/api/core/events":
            self._json({"events": core.db.recent_events(50)})
            return

        if path == "/api/core/config":
            self._json(core.config.snapshot())
            return

        if path == "/api/modules":
            self._json(core.modules.snapshot())
            return

        if path == "/api/session":
            # Токен отдаётся только локальному клиенту и только для
            # same-origin запроса: ответ без CORS-заголовков браузер не даст
            # прочитать чужой странице, а изменяющий вызов с собственным
            # заголовком требует preflight, который тоже не пройдёт.
            # Локальный процесс и так может прочитать endpoint.json.
            if self.client_address[0] not in LOCAL_CLIENTS:
                self._json({"status": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            self._json(
                {
                    "token": self.app.token,
                    "token_required_for": [
                        "/api/shutdown",
                        "/api/update/check",
                        "/api/update/apply",
                        "/api/modules/<module_id>/start",
                        "/api/modules/<module_id>/stop",
                    ],
                    "header": TOKEN_HEADER,
                }
            )
            return

        if path == "/api/update":
            self._json(core.update.status())
            return

        if path == "/api/update/plan":
            plan = core.update.plan()
            if plan is None:
                self._json({"plan": None, "hint": "выполните проверку обновления"})
                return
            self._json(plan)
            return

        if path == "/api/update/progress":
            self._json(core.update.progress())
            return

        if path == "/api/update/history":
            self._json(core.update.history())
            return

        if path == "/api/system":
            deep = core.status(deep=True)
            self._json(
                {
                    "project": "Sayuri Yukishiro",
                    "version": project_version(),
                    "core_version": deep["core_version"],
                    "core_health": deep["health"]["overall"],
                    "service_count": deep["health"]["service_count"],
                    "healthy_services": deep["health"]["healthy_count"],
                    "schema_version": deep["schema_version"],
                    "database": str(core.db.path),
                    "database_check": deep["database_check"],
                    "data_dir": str(DATA_DIR),
                    "media": describe_media(core.db.path),
                    "module_count": len(core.db.list_modules()),
                    "recoverable_tasks": deep["recoverable_tasks"],
                    "host": self.app.server_address[0],
                    "port": self.app.server_address[1],
                    "runs": core.db.recent_runtime_sessions(5),
                }
            )
            return

        self._file("index.html" if path == "/" else path.lstrip("/"))

    def do_HEAD(self) -> None:
        self.do_GET()

    def _authorized(self) -> bool:
        """Изменяющий вызов: только локальный клиент и только с токеном.

        Иначе любая страница в браузере или любой процесс на машине мог бы
        запустить обновление или остановить систему.
        """

        if not self._local_only():
            return False
        expected = self.app.token
        supplied = self.headers.get(TOKEN_HEADER, "")
        if not expected:
            self._json({"status": "disabled"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return False
        if not supplied or not secrets.compare_digest(supplied, expected):
            self._json({"status": "forbidden"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        core = self.app.core

        module_action: tuple[str, str] | None = None
        parts = [part for part in path.split("/") if part]
        if (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "modules"
            and parts[3] in {"start", "stop"}
        ):
            module_action = (parts[2], parts[3])

        if path not in {
            "/api/shutdown",
            "/api/update/check",
            "/api/update/apply",
        } and module_action is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not self._authorized():
            return

        if path == "/api/shutdown":
            self._json({"status": "shutting_down"})
            threading.Thread(
                target=self.app.shutdown,
                name="sayuri-shutdown",
                daemon=True,
            ).start()
            return

        if module_action is not None:
            module_id, action = module_action
            try:
                payload = (
                    core.modules.start_module(module_id)
                    if action == "start"
                    else core.modules.stop_module(module_id)
                )
            except KeyError:
                self._json(
                    {"status": "error", "error": "module not found"},
                    HTTPStatus.NOT_FOUND,
                )
                return
            except Exception as exc:
                self._json(
                    {"status": "error", "error": str(exc)},
                    HTTPStatus.CONFLICT,
                )
                return
            self._json({"status": "ok", "module": payload})
            return

        if path == "/api/update/check":
            try:
                self._json(core.update.check())
            except Exception as exc:
                self._json(
                    {"status": "error", "error": str(exc)},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if path == "/api/update/apply":
            body = self._read_json()
            plan_id = body.get("plan_id") if isinstance(body, dict) else None
            try:
                # Возможность установки проверяется здесь, синхронно: отказ
                # внутри фоновой задачи дал бы ответ «началось» на запрос,
                # который начаться не мог.
                plan = core.update.ensure_can_apply(plan_id)
            except Exception as exc:
                self._json({"status": "error", "error": str(exc)}, HTTPStatus.CONFLICT)
                return

            # Установка идёт фоновой задачей ядра: HTTP-запрос не должен
            # висеть минутами, а интерфейс следит через /api/update/progress.
            job_id = core.api.submit_job("update.apply", core.update.apply, plan_id=plan_id)
            self._json(
                {
                    "status": "started",
                    "job_id": job_id,
                    "from_version": plan["from_version"],
                    "to_version": plan["to_version"],
                }
            )
            return

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > 64 * 1024:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def log_message(self, format: str, *args: Any) -> None:
        return


def _open_site(url: str, host: str, port: int) -> None:
    def worker() -> None:
        # Браузер открываем только когда порт реально принимает соединения.
        if wait_for_port(host, port, timeout=10.0):
            try:
                webbrowser.open(url)
            except Exception:
                pass

    threading.Thread(target=worker, name="sayuri-open-site", daemon=True).start()


def serve(
    *,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool | None = None,
    db: CoreDatabase | None = None,
) -> None:
    existing = running_instance()
    if existing is not None:
        raise InstanceAlreadyRunning(existing.url, existing.pid)

    core = SystemCore(db=db)
    server: SayuriHTTPServer | None = None
    opened_session: str | None = None
    try:
        core.start()

        options = ServerOptions(
            host=host or str(core.config.get("server.host", DEFAULT_HOST)),
            port=port if port is not None else int(core.config.get("server.port", DEFAULT_PORT)),
            auto_port=bool(core.config.get("server.auto_port", True)),
            open_browser=(
                open_browser
                if open_browser is not None
                else bool(core.config.get("server.open_browser", True))
            ),
        )
        token = new_token()
        server = bind_server(
            options.host,
            options.port,
            auto_port=options.auto_port,
            factory=lambda address: SayuriHTTPServer(address, core, token),
        )

        actual_host, actual_port = server.server_address[0], server.server_address[1]
        endpoint = write_endpoint(
            host=actual_host,
            port=actual_port,
            token=token,
            project_version=project_version(),
        )
        core.db.open_runtime_session(
            server.session_id,
            pid=os.getpid(),
            host=actual_host,
            port=actual_port,
            project_version=project_version(),
            media=describe_media(core.db.path),
        )
        opened_session = server.session_id
        core.events.publish(
            "core.server.started",
            {"host": actual_host, "port": actual_port, "url": endpoint.url},
            source="core",
        )

        style = console.ConsoleStyle.detect()
        print(style.status(console.OK, "ядро", f"v{core.VERSION} поднято"))
        if actual_port != options.port:
            print(
                style.status(
                    console.WARN,
                    "порт",
                    f"{options.port} занят — взят свободный {actual_port}",
                )
            )
        else:
            print(style.status(console.OK, "порт", str(actual_port)))
        print(style.status(console.OK, "сайт", endpoint.url))
        print()
        print(
            style.panel(
                [
                    style.paint("Система активна", console.OK),
                    endpoint.url,
                    "Остановка: закройте окно или Ctrl+C",
                ]
            )
        )

        if options.open_browser:
            _open_site(endpoint.url, actual_host, actual_port)

        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        visual = console.ConsoleStyle.detect()
        print()
        print(visual.status(console.INFO, "остановка", "по запросу пользователя"))
    finally:
        # Любой выход обязан вернуть носитель в чистое состояние.
        if server is not None:
            server.server_close()
        if opened_session is not None:
            try:
                core.db.close_runtime_session(opened_session)
            except Exception:
                pass
        clear_endpoint()
        core.stop()
