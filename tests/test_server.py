"""Проверки сервера: автопорт, безопасность, автооткрытие сайта."""

from __future__ import annotations

import errno
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from sayuri_yukishiro import endpoint as endpoint_module
from sayuri_yukishiro.core.runtime import SystemCore
from sayuri_yukishiro.database import CoreDatabase
from sayuri_yukishiro.server import (
    SayuriHTTPServer,
    bind_server,
    serve,
    wait_for_port,
)


class BindTests(unittest.TestCase):
    def test_busy_port_falls_back_to_free_port(self) -> None:
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            busy_port = blocker.getsockname()[1]

            attempts: list[int] = []

            class Fake:
                def __init__(self, address: tuple[str, int]) -> None:
                    attempts.append(address[1])
                    if address[1] == busy_port:
                        raise OSError(errno.EADDRINUSE, "Address already in use")
                    self.server_address = ("127.0.0.1", 45000)

            server = bind_server(
                "127.0.0.1",
                busy_port,
                auto_port=True,
                factory=Fake,
            )
            self.assertEqual(attempts, [busy_port, 0])
            self.assertEqual(server.server_address[1], 45000)

    def test_without_auto_port_the_error_propagates(self) -> None:
        def factory(_address: tuple[str, int]) -> None:
            raise OSError(98, "Address already in use")

        with self.assertRaises(OSError):
            bind_server("127.0.0.1", 8765, auto_port=False, factory=factory)

    def test_unrelated_errors_are_never_swallowed(self) -> None:
        def factory(_address: tuple[str, int]) -> None:
            raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")

        with self.assertRaises(OSError):
            bind_server("127.0.0.1", 8765, auto_port=True, factory=factory)

    def test_wait_for_port_times_out_on_closed_port(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]
        self.assertFalse(wait_for_port("127.0.0.1", free_port, timeout=0.5))


class LiveServerTests(unittest.TestCase):
    """Поднимает настоящий сервер на свободном порту."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.core = SystemCore(
            db=CoreDatabase(root / "core.db"),
            config_path=root / "system.json",
            log_dir=root / "logs",
        )
        self.core.start()
        self.token = "test-token"
        self.server = SayuriHTTPServer(("127.0.0.1", 0), self.core, self.token)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
        )
        self.thread.start()
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.core.stop()
        self._tmp.cleanup()

    def get(self, path: str) -> tuple[int, bytes]:
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post(self, path: str, token: str | None) -> int:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=b"",
            method="POST",
        )
        if token is not None:
            request.add_header("X-Sayuri-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_health_reports_ok(self) -> None:
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["pid"], os.getpid())

    def test_site_is_served(self) -> None:
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Sayuri Yukishiro", body)

    def test_api_surface_answers(self) -> None:
        for path in (
            "/api/core",
            "/api/core/jobs",
            "/api/core/recovery",
            "/api/core/events",
            "/api/core/config",
            "/api/modules",
            "/api/system",
        ):
            with self.subTest(path):
                status, body = self.get(path)
                self.assertEqual(status, 200)
                json.loads(body)

    def test_system_reports_media_and_schema(self) -> None:
        _status, body = self.get("/api/system")
        payload = json.loads(body)
        self.assertIn(payload["media"], {"local", "removable", "network", "synced"})
        self.assertGreaterEqual(payload["schema_version"], 1)
        self.assertEqual(payload["core_health"], "healthy")

    def test_path_traversal_is_refused(self) -> None:
        status, _body = self.get("/../VERSION")
        self.assertIn(status, {403, 404})

    def test_unknown_path_is_not_found(self) -> None:
        status, _body = self.get("/api/does-not-exist")
        self.assertEqual(status, 404)

    def test_shutdown_requires_token(self) -> None:
        self.assertEqual(self.post("/api/shutdown", None), 403)
        self.assertEqual(self.post("/api/shutdown", "wrong-token"), 403)

    def test_module_mutation_requires_token(self) -> None:
        self.assertEqual(self.post("/api/modules/missing/start", None), 403)
        self.assertEqual(self.post("/api/modules/missing/start", self.token), 404)

    def test_unknown_post_path_is_not_found(self) -> None:
        self.assertEqual(self.post("/api/other", self.token), 404)


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        patcher = patch.object(endpoint_module, "RUNTIME_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_endpoint_round_trip(self) -> None:
        written = endpoint_module.write_endpoint(
            host="127.0.0.1",
            port=9000,
            token="secret",
            project_version="1.2.3",
        )
        restored = endpoint_module.read_endpoint()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.port, 9000)
        self.assertEqual(restored.url, written.url)
        self.assertNotIn("token", written.public_dict())

    def test_broken_endpoint_file_reads_as_none(self) -> None:
        endpoint_module.endpoint_path().write_text("{broken", encoding="utf-8")
        self.assertIsNone(endpoint_module.read_endpoint())

    def test_dead_pid_clears_stale_endpoint(self) -> None:
        endpoint_module.write_endpoint(
            host="127.0.0.1",
            port=9001,
            token="secret",
            project_version="1.2.3",
            pid=999_000,
        )
        with patch.object(endpoint_module, "process_alive", return_value=False):
            self.assertIsNone(endpoint_module.running_instance())
        self.assertFalse(endpoint_module.endpoint_path().exists())

    def test_live_pid_is_reported_as_running(self) -> None:
        endpoint_module.write_endpoint(
            host="127.0.0.1",
            port=9002,
            token="secret",
            project_version="1.2.3",
            pid=os.getpid() + 1,
        )
        with (
            patch.object(endpoint_module, "process_alive", return_value=True),
            patch.object(endpoint_module, "_endpoint_health_matches", return_value=True),
        ):
            existing = endpoint_module.running_instance()
        self.assertIsNotNone(existing)
        self.assertEqual(existing.port, 9002)

    def test_reused_live_pid_without_sayuri_health_is_cleared(self) -> None:
        endpoint_module.write_endpoint(
            host="127.0.0.1",
            port=9004,
            token="secret",
            project_version="1.2.3",
            pid=os.getpid() + 1,
        )
        with (
            patch.object(endpoint_module, "process_alive", return_value=True),
            patch.object(endpoint_module, "_endpoint_health_matches", return_value=False),
        ):
            self.assertIsNone(endpoint_module.running_instance())
        self.assertFalse(endpoint_module.endpoint_path().exists())

    def test_second_instance_is_refused(self) -> None:
        from sayuri_yukishiro.server import InstanceAlreadyRunning

        endpoint_module.write_endpoint(
            host="127.0.0.1",
            port=9003,
            token="secret",
            project_version="1.2.3",
            pid=os.getpid() + 1,
        )
        with (
            patch.object(endpoint_module, "process_alive", return_value=True),
            patch.object(endpoint_module, "_endpoint_health_matches", return_value=True),
        ):
            with self.assertRaises(InstanceAlreadyRunning):
                serve()


if __name__ == "__main__":
    unittest.main()
