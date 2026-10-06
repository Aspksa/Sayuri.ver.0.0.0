"""Единый визуальный язык вывода: значки, цвета, панели.

Одни и те же состояния выглядят одинаково в диагностике, при запуске и в
лаунчере. Там, где консоль не умеет Unicode или цвет, вывод деградирует
до чистого ASCII, а не превращается в мусор из вопросительных знаков.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import IO

OK = "ok"
WARN = "warn"
BAD = "bad"
INFO = "info"
STEP = "step"

_UNICODE_GLYPHS = {
    OK: "✓",
    WARN: "!",
    BAD: "✗",
    INFO: "·",
    STEP: "▸",
}

_ASCII_GLYPHS = {
    OK: "+",
    WARN: "!",
    BAD: "x",
    INFO: ".",
    STEP: ">",
}

_UNICODE_BOX = {
    "tl": "╭",
    "tr": "╮",
    "bl": "╰",
    "br": "╯",
    "h": "─",
    "v": "│",
    "rule": "─",
}

_ASCII_BOX = {
    "tl": "+",
    "tr": "+",
    "bl": "+",
    "br": "+",
    "h": "-",
    "v": "|",
    "rule": "-",
}

_COLORS = {
    OK: "\x1b[32m",
    WARN: "\x1b[33m",
    BAD: "\x1b[31m",
    INFO: "\x1b[90m",
    STEP: "\x1b[36m",
    "title": "\x1b[1m",
    "reset": "\x1b[0m",
}

_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() not in _FALSE_VALUES


def supports_unicode(stream: IO[str] | None = None) -> bool:
    """Умеет ли поток вывести символы вне ASCII."""

    forced_ascii = _env_flag("SAYURI_ASCII")
    if forced_ascii:
        return False

    target = stream or sys.stdout
    encoding = getattr(target, "encoding", None) or ""
    if not encoding:
        return False
    try:
        "✓─".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def supports_color(stream: IO[str] | None = None) -> bool:
    """Цвет только там, где его увидит человек.

    NO_COLOR — договорённость, которую уважают инструменты командной
    строки. Перенаправленный вывод и логи CI не должны содержать escape-кодов.
    """

    if os.environ.get("NO_COLOR") is not None:
        return False
    forced = _env_flag("SAYURI_COLOR")
    if forced is not None:
        return forced

    target = stream or sys.stdout
    if not hasattr(target, "isatty") or not target.isatty():
        return False
    if os.name == "nt":
        # Windows Terminal и современные консоли понимают VT; старый
        # conhost без ANSICON — нет.
        return bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON"))
    return os.environ.get("TERM", "") not in {"", "dumb"}


@dataclass(frozen=True)
class ConsoleStyle:
    unicode: bool
    color: bool

    @classmethod
    def detect(cls, stream: IO[str] | None = None) -> "ConsoleStyle":
        return cls(unicode=supports_unicode(stream), color=supports_color(stream))

    @property
    def glyphs(self) -> dict[str, str]:
        return _UNICODE_GLYPHS if self.unicode else _ASCII_GLYPHS

    @property
    def box(self) -> dict[str, str]:
        return _UNICODE_BOX if self.unicode else _ASCII_BOX

    def glyph(self, kind: str) -> str:
        return self.glyphs.get(kind, self.glyphs[INFO])

    def paint(self, text: str, kind: str) -> str:
        if not self.color:
            return text
        code = _COLORS.get(kind)
        if code is None:
            return text
        return f"{code}{text}{_COLORS['reset']}"

    # --- элементы вывода --------------------------------------------

    def status(self, kind: str, name: str, detail: str = "", *, width: int = 13) -> str:
        mark = self.paint(f"[{self.glyph(kind)}]", kind)
        label = name.ljust(width)
        return f"  {mark} {label} {detail}".rstrip()

    def step(self, index: int, total: int, title: str) -> str:
        head = self.paint(f"[{index}/{total}]", STEP)
        return f"\n{head} {self.paint(title, 'title')}"

    def field(self, name: str, value: str, *, width: int = 10) -> str:
        mark = self.paint(self.glyph(STEP), STEP)
        return f"  {mark} {name.ljust(width)} {value}".rstrip()

    def rule(self, width: int = 62) -> str:
        return "  " + self.paint(self.box["rule"] * width, INFO)

    def panel(self, lines: list[str], *, kind: str = OK, width: int | None = None) -> str:
        box = self.box
        content_width = width or max((len(line) for line in lines), default=0)
        content_width = max(content_width, 28)
        top = self.paint(box["tl"] + box["h"] * (content_width + 2) + box["tr"], kind)
        bottom = self.paint(box["bl"] + box["h"] * (content_width + 2) + box["br"], kind)
        side = self.paint(box["v"], kind)
        body = [f"  {side} {line.ljust(content_width)} {side}" for line in lines]
        return "\n".join(["  " + top, *body, "  " + bottom])


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 замечание, 2 замечания, 5 замечаний."""

    remainder_100 = abs(count) % 100
    remainder_10 = abs(count) % 10
    if 11 <= remainder_100 <= 14:
        form = many
    elif remainder_10 == 1:
        form = one
    elif 2 <= remainder_10 <= 4:
        form = few
    else:
        form = many
    return f"{count} {form}"


def write(text: str, stream: IO[str] | None = None) -> None:
    target = stream or sys.stdout
    print(text, file=target)
