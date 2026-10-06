"""Проверки визуального языка: значки, цвета, фолбэк, склонение."""

from __future__ import annotations

import io
import json
import os
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sayuri_yukishiro import console
from sayuri_yukishiro.console import BAD, INFO, OK, STEP, WARN, ConsoleStyle
from sayuri_yukishiro.diagnostics import (
    Check,
    FATAL,
    WARNING,
    print_report,
    report_payload,
    status_kind,
    summarize,
)

ANSI = re.compile(r"\x1b\[[0-9;]*m")

CHECKS = [
    Check("python", True, INFO, "Python 3.11.9"),
    Check("storage", True, INFO, "писать можно"),
    Check("git", False, WARNING, "Git не найден"),
    Check("database", False, FATAL, "база недоступна"),
]


class FakeStream:
    """Поток с управляемыми encoding и isatty: io.StringIO их не отдаёт."""

    def __init__(self, encoding: str = "utf-8", tty: bool = True) -> None:
        self.encoding = encoding
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def write(self, text: str) -> int:
        return len(text)


class UnicodeDetectionTests(unittest.TestCase):
    def test_utf8_stream_supports_unicode(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SAYURI_ASCII", None)
            self.assertTrue(console.supports_unicode(FakeStream("utf-8")))

    def test_legacy_codepage_falls_back_to_ascii(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SAYURI_ASCII", None)
            self.assertFalse(console.supports_unicode(FakeStream("cp866")))
            self.assertFalse(console.supports_unicode(FakeStream("ascii")))

    def test_missing_encoding_falls_back_to_ascii(self) -> None:
        self.assertFalse(console.supports_unicode(FakeStream("")))

    def test_ascii_can_be_forced(self) -> None:
        with patch.dict(os.environ, {"SAYURI_ASCII": "1"}, clear=False):
            self.assertFalse(console.supports_unicode(FakeStream("utf-8")))


class ColorDetectionTests(unittest.TestCase):
    def test_no_color_is_respected(self) -> None:
        with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            self.assertFalse(console.supports_color(FakeStream()))

    def test_redirected_output_has_no_color(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_COLOR", None)
            os.environ.pop("SAYURI_COLOR", None)
            self.assertFalse(console.supports_color(FakeStream(tty=False)))

    def test_color_can_be_forced(self) -> None:
        with patch.dict(os.environ, {"SAYURI_COLOR": "1"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertTrue(console.supports_color(FakeStream(tty=False)))

    def test_forced_off_wins_over_tty(self) -> None:
        with patch.dict(os.environ, {"SAYURI_COLOR": "0"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertFalse(console.supports_color(FakeStream(tty=True)))


class StyleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.unicode = ConsoleStyle(unicode=True, color=False)
        self.ascii = ConsoleStyle(unicode=False, color=False)
        self.colored = ConsoleStyle(unicode=True, color=True)

    def test_every_state_has_both_glyph_sets(self) -> None:
        for kind in (OK, WARN, BAD, INFO, STEP):
            self.assertTrue(self.unicode.glyph(kind))
            self.assertTrue(self.ascii.glyph(kind))
            self.assertTrue(self.ascii.glyph(kind).isascii())

    def test_unicode_glyphs_differ_from_ascii(self) -> None:
        self.assertEqual(self.unicode.glyph(OK), "✓")
        self.assertEqual(self.unicode.glyph(BAD), "✗")
        self.assertEqual(self.ascii.glyph(OK), "+")
        self.assertEqual(self.ascii.glyph(BAD), "x")

    def test_status_line_is_aligned_and_contains_glyph(self) -> None:
        line = self.unicode.status(OK, "python", "готов", width=13)
        self.assertIn("[✓]", line)
        self.assertIn("python", line)
        self.assertIn("готов", line)
        other = self.unicode.status(BAD, "db", "сломано", width=13)
        self.assertEqual(line.index("готов"), other.index("сломано"))

    def test_ascii_output_is_pure_ascii(self) -> None:
        rendered = "\n".join(
            [
                self.ascii.status(OK, "python", "ok"),
                self.ascii.step(1, 2, "Check"),
                self.ascii.rule(10),
                self.ascii.panel(["ready", "http://127.0.0.1:8765/"]),
            ]
        )
        self.assertTrue(rendered.isascii(), rendered)

    def test_panel_is_rectangular(self) -> None:
        panel = self.unicode.panel(["короткая", "значительно длиннее строка"])
        lines = panel.splitlines()
        self.assertEqual(len({len(line) for line in lines}), 1)
        self.assertIn("╭", lines[0])
        self.assertIn("╰", lines[-1])

    def test_color_is_applied_only_when_enabled(self) -> None:
        self.assertIsNone(ANSI.search(self.unicode.status(OK, "python")))
        self.assertIsNotNone(ANSI.search(self.colored.status(OK, "python")))

    def test_paint_resets_color(self) -> None:
        painted = self.colored.paint("текст", BAD)
        self.assertTrue(painted.endswith("\x1b[0m"))


class PluralTests(unittest.TestCase):
    def test_russian_forms(self) -> None:
        cases = {
            0: "0 замечаний",
            1: "1 замечание",
            2: "2 замечания",
            4: "4 замечания",
            5: "5 замечаний",
            11: "11 замечаний",
            14: "14 замечаний",
            21: "21 замечание",
            22: "22 замечания",
            25: "25 замечаний",
            101: "101 замечание",
        }
        for count, expected in cases.items():
            with self.subTest(count=count):
                self.assertEqual(
                    console.plural(count, "замечание", "замечания", "замечаний"),
                    expected,
                )


class DiagnosticsRenderingTests(unittest.TestCase):
    def test_status_kind_maps_severity(self) -> None:
        self.assertEqual(status_kind(CHECKS[0]), OK)
        self.assertEqual(status_kind(CHECKS[2]), WARN)
        self.assertEqual(status_kind(CHECKS[3]), BAD)

    def test_summary_counts(self) -> None:
        self.assertEqual(
            summarize(CHECKS),
            {"total": 4, "passed": 2, "warnings": 1, "fatal": 1},
        )

    def test_report_payload_is_json_serializable(self) -> None:
        payload = report_payload(CHECKS)
        restored = json.loads(json.dumps(payload, ensure_ascii=False))
        self.assertTrue(restored["fatal"])
        self.assertEqual(restored["summary"]["passed"], 2)
        self.assertEqual(
            [item["kind"] for item in restored["checks"]],
            [OK, OK, WARN, BAD],
        )
        for item in restored["checks"]:
            self.assertEqual(set(item), {"name", "ok", "severity", "detail", "kind"})

    def test_text_report_shows_glyphs_and_summary(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_report(CHECKS, ConsoleStyle(unicode=True, color=False))
        output = buffer.getvalue()
        self.assertIn("[✓] python", output)
        self.assertIn("[!] git", output)
        self.assertIn("[✗] database", output)
        self.assertIn("2 проверки пройдено", output)
        self.assertIn("1 замечание", output)
        self.assertIn("1 критическая ошибка", output)

    def test_fatal_count_is_grammatically_correct(self) -> None:
        for count, expected in (
            (1, "1 критическая ошибка"),
            (3, "3 критические ошибки"),
            (5, "5 критических ошибок"),
        ):
            with self.subTest(count=count):
                checks = [Check(f"c{i}", False, FATAL, "нет") for i in range(count)]
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    print_report(checks, ConsoleStyle(unicode=True, color=False))
                self.assertIn(expected, buffer.getvalue())

    def test_text_report_degrades_to_ascii(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_report(CHECKS, ConsoleStyle(unicode=False, color=False))
        output = buffer.getvalue()
        self.assertIn("[+] python", output)
        self.assertIn("[x] database", output)
        self.assertNotIn("✓", output)


class CliRenderingTests(unittest.TestCase):
    def test_json_format_prints_only_json(self) -> None:
        from sayuri_yukishiro.main import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--preflight", "--format", "json"])
        payload = json.loads(buffer.getvalue())
        self.assertIn(code, {0, 1})
        self.assertEqual(payload["project"], "Sayuri Yukishiro")
        self.assertIn("checks", payload)
        self.assertIn("summary", payload)

    def test_text_format_prints_banner_and_step(self) -> None:
        from sayuri_yukishiro.main import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["--preflight"])
        output = buffer.getvalue()
        self.assertIn("Sayuri Yukishiro", output)
        self.assertIn("[1/1]", output)
        self.assertIn("Проверка готовности", output)


if __name__ == "__main__":
    unittest.main()
