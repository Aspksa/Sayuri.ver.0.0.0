"""Точка входа командной строки."""

from __future__ import annotations

import argparse
import json
import sys

from .diagnostics import has_fatal_failures, print_report, run_diagnostics
from .endpoint import read_endpoint, running_instance
from .paths import project_version
from .server import InstanceAlreadyRunning, serve
from .version import CORE_VERSION, DEFAULT_PORT

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_ALREADY_RUNNING = 3


def _force_utf8_output() -> None:
    """Windows-консоль по умолчанию не в UTF-8 — русский текст ломается."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def command_preflight(port: int) -> int:
    print(f"Sayuri Yukishiro v{project_version()} / ядро v{CORE_VERSION}")
    print()
    checks = run_diagnostics(port)
    print_report(checks)
    return EXIT_FATAL if has_fatal_failures(checks) else EXIT_OK


def command_serve(port: int | None, open_browser: bool) -> int:
    try:
        serve(port=port, open_browser=open_browser)
    except InstanceAlreadyRunning as exc:
        print(f"Система уже запущена: {exc.url} (pid {exc.pid})")
        return EXIT_ALREADY_RUNNING
    return EXIT_OK


def command_endpoint() -> int:
    endpoint = read_endpoint()
    if endpoint is None:
        print(json.dumps({"running": False}, ensure_ascii=False))
        return EXIT_FATAL
    alive = running_instance() is not None
    payload = endpoint.public_dict()
    payload["running"] = alive
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_OK if alive else EXIT_FATAL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sayuri-yukishiro",
        description="Переносимая персональная платформа Sayuri Yukishiro.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="только диагностика, затем выход",
    )
    mode.add_argument("--serve", action="store_true", help="запустить ядро и сайт")
    mode.add_argument(
        "--endpoint",
        action="store_true",
        help="вывести адрес запущенного экземпляра",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"порт локального сервера (по умолчанию {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="не открывать сайт автоматически",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)

    if args.port is not None and not (1 <= args.port <= 65535):
        print(f"Недопустимый порт: {args.port}", file=sys.stderr)
        return EXIT_FATAL

    if args.endpoint:
        return command_endpoint()
    if args.preflight:
        return command_preflight(args.port or DEFAULT_PORT)
    if args.serve:
        return command_serve(args.port, not args.no_browser)
    return command_preflight(args.port or DEFAULT_PORT)


if __name__ == "__main__":
    sys.exit(main())
