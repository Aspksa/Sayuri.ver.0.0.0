"""Проверки переносимости: любой носитель, любая буква диска, любой путь."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sayuri_yukishiro import paths
from sayuri_yukishiro.database import CoreDatabase
from sayuri_yukishiro.storage_policy import (
    ALLOWED_JOURNAL_MODES,
    describe_media,
    sqlite_journal_mode,
)


class StoragePolicyTests(unittest.TestCase):
    def test_local_disk_uses_wal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SAYURI_SQLITE_JOURNAL_MODE", None)
                os.environ.pop("OneDrive", None)
                self.assertEqual(sqlite_journal_mode(Path(tmp) / "core.db"), "WAL")

    def test_removable_media_uses_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "core.db"
            with patch(
                "sayuri_yukishiro.storage_policy.windows_drive_type",
                return_value=2,
            ):
                self.assertEqual(describe_media(target), "removable")
                self.assertEqual(sqlite_journal_mode(target), "DELETE")

    def test_network_drive_uses_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "core.db"
            with patch(
                "sayuri_yukishiro.storage_policy.windows_drive_type",
                return_value=4,
            ):
                self.assertEqual(describe_media(target), "network")
                self.assertEqual(sqlite_journal_mode(target), "DELETE")

    def test_synced_folder_uses_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "OneDrive" / "Sayuri" / "core.db"
            with patch.dict(os.environ, {"OneDrive": str(root / "OneDrive")}, clear=False):
                os.environ.pop("SAYURI_SQLITE_JOURNAL_MODE", None)
                self.assertEqual(describe_media(target), "synced")
                self.assertEqual(sqlite_journal_mode(target), "DELETE")

    def test_explicit_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"SAYURI_SQLITE_JOURNAL_MODE": "DELETE"},
                clear=False,
            ):
                self.assertEqual(sqlite_journal_mode(Path(tmp) / "core.db"), "DELETE")

    def test_invalid_override_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"SAYURI_SQLITE_JOURNAL_MODE": "MEMORY"},
                clear=False,
            ):
                with self.assertRaises(ValueError):
                    sqlite_journal_mode(Path(tmp) / "core.db")

    def test_declared_modes_are_the_only_ones_used(self) -> None:
        self.assertEqual(ALLOWED_JOURNAL_MODES, {"WAL", "DELETE"})

    def test_journal_mode_is_actually_applied_to_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"SAYURI_SQLITE_JOURNAL_MODE": "DELETE"},
                clear=False,
            ):
                db = CoreDatabase(Path(tmp) / "core.db")
                db.initialize()
                with db.session() as conn:
                    mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
                self.assertEqual(mode.lower(), "delete")


class PathTests(unittest.TestCase):
    def test_no_hardcoded_drive_letter_in_sources(self) -> None:
        import re

        pattern = re.compile(r"[A-Za-z]:\\\\")
        offenders: list[str] = []
        for file in (paths.PROJECT_ROOT / "src").rglob("*.py"):
            if pattern.search(file.read_text(encoding="utf-8")):
                offenders.append(str(file))
        self.assertEqual(offenders, [])

    def test_data_dir_derives_from_project_root_by_default(self) -> None:
        self.assertTrue(str(paths.DATA_DIR).startswith(str(paths.PROJECT_ROOT)))

    def test_custom_data_dir_is_honoured(self) -> None:
        import importlib

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SAYURI_DATA_DIR": tmp}, clear=False):
                reloaded = importlib.reload(paths)
                try:
                    self.assertEqual(reloaded.DATA_DIR, Path(tmp).resolve())
                    reloaded.ensure_runtime_dirs()
                    self.assertTrue((Path(tmp) / "core").is_dir())
                    self.assertTrue((Path(tmp) / "runtime").is_dir())
                finally:
                    os.environ.pop("SAYURI_DATA_DIR", None)
                    importlib.reload(paths)


class LauncherContractTests(unittest.TestCase):
    """Лаунчер — часть продукта: его контракт проверяется тестами."""

    def setUp(self) -> None:
        self.root = paths.PROJECT_ROOT
        self.batch = (self.root / "Sayuri Yukishiro.bat").read_text(encoding="utf-8")
        self.launcher = (self.root / "scripts" / "launcher.ps1").read_text(encoding="utf-8")

    def test_powershell_scripts_have_utf8_bom_for_windows_powershell_51(self) -> None:
        for name in ("launcher.ps1", "tray.ps1"):
            with self.subTest(name):
                data = (self.root / "scripts" / name).read_bytes()
                self.assertTrue(
                    data.startswith(b"\xef\xbb\xbf"),
                    f"{name} должен иметь UTF-8 BOM для Windows PowerShell 5.1",
                )

    def test_entry_point_files_exist(self) -> None:
        self.assertTrue((self.root / "Sayuri Yukishiro.bat").is_file())
        self.assertTrue((self.root / "scripts" / "launcher.ps1").is_file())

    def test_batch_derives_root_from_its_own_location(self) -> None:
        self.assertIn("%~dp0scripts\\launcher.ps1", self.batch)

    def test_batch_does_not_pass_trailing_backslash_root(self) -> None:
        # "%~dp0" заканчивается обратным слэшем и ломает разбор параметра.
        self.assertNotIn('-Root "%~dp0"', self.batch)

    def test_launcher_does_not_assign_automatic_args_variable(self) -> None:
        self.assertNotRegex(self.launcher, r"\$args\s*=")

    def test_launcher_resolves_python_in_portable_order(self) -> None:
        portable = self.launcher.index("runtime\\python\\python.exe")
        venv = self.launcher.index(".venv\\Scripts\\python.exe")
        system = self.launcher.index('Get-Command "python.exe"')
        self.assertLess(portable, venv)
        self.assertLess(venv, system)

    def test_launcher_handles_read_only_media(self) -> None:
        self.assertIn("SAYURI_DATA_DIR", self.launcher)
        self.assertIn("Ensure-WritableData", self.launcher)

    def test_launcher_waits_in_the_selected_data_directory(self) -> None:
        self.assertIn('$RuntimeDir = Join-Path $storage.Path "runtime"', self.launcher)
        self.assertIn('$endpointPath = Join-Path $RuntimeDir "endpoint.json"', self.launcher)
        self.assertNotIn('Join-Path $Root "data\\runtime\\endpoint.json"', self.launcher)

    def test_launcher_opens_site_of_running_instance(self) -> None:
        self.assertIn("--endpoint", self.launcher)
        self.assertIn("Start-Process", self.launcher)

    def test_no_hardcoded_drive_letter_in_launchers(self) -> None:
        import re

        pattern = re.compile(r"(?<![%$])\b[A-Za-z]:\\\\(?!launcher)")
        self.assertIsNone(pattern.search(self.batch))
        self.assertIsNone(pattern.search(self.launcher))


class LauncherVisualContractTests(unittest.TestCase):
    """Статусы лаунчера — часть продукта, их контракт зафиксирован."""

    def setUp(self) -> None:
        self.launcher = (
            paths.PROJECT_ROOT / "scripts" / "launcher.ps1"
        ).read_text(encoding="utf-8")

    def test_rendering_helpers_exist(self) -> None:
        for helper in (
            "function Write-Status",
            "function Write-Panel",
            "function Write-Step",
            "function Write-Field",
            "function Write-Rule",
            "function Get-Plural",
        ):
            with self.subTest(helper):
                self.assertIn(helper, self.launcher)

    def test_both_glyph_sets_are_declared(self) -> None:
        self.assertIn('$script:Glyph = @{ ok = "✓"', self.launcher)
        self.assertIn('$script:Glyph = @{ ok = "+"', self.launcher)
        self.assertIn('$script:Box = @{ tl = "╭"', self.launcher)
        self.assertIn('$script:Box = @{ tl = "+"', self.launcher)

    def test_every_state_is_covered_by_both_glyph_sets(self) -> None:
        import re

        tables = re.findall(r"\$script:Glyph = @\{([^}]*)\}", self.launcher)
        self.assertEqual(len(tables), 2)
        for table in tables:
            keys = set(re.findall(r"(\w+)\s*=", table))
            self.assertEqual(keys, {"ok", "warn", "bad", "info", "step"})

    def test_ascii_fallback_is_reachable(self) -> None:
        self.assertIn("[switch]$Ascii", self.launcher)
        self.assertIn("$env:SAYURI_ASCII", self.launcher)
        self.assertIn("CodePage -eq 65001", self.launcher)

    def test_statuses_are_driven_by_json_not_text_parsing(self) -> None:
        self.assertIn('"--preflight", "--format", "json"', self.launcher)
        self.assertIn("$result.Data.checks", self.launcher)
        self.assertIn("$check.kind", self.launcher)

    def test_native_colors_are_used_instead_of_ansi(self) -> None:
        # ANSI ненадёжен в старом conhost: цвет задаётся родным параметром.
        self.assertIn("-ForegroundColor", self.launcher)
        self.assertNotIn("\x1b[", self.launcher)
        self.assertNotIn("`e[", self.launcher)

    def test_ready_panel_shows_site_address(self) -> None:
        self.assertIn('Write-Panel -Kind "ok" -Lines @("Система активна"', self.launcher)

    def test_fatal_diagnostics_block_the_launch(self) -> None:
        self.assertIn("if ($result.Data.fatal)", self.launcher)
        self.assertIn("Запуск невозможен", self.launcher)

    def test_storage_reason_distinguishes_explicit_path_from_fallback(self) -> None:
        # Явно заданный SAYURI_DATA_DIR не должен выглядеть как
        # недоступный для записи носитель.
        self.assertIn('Reason = "explicit"', self.launcher)
        self.assertIn('Reason = "media"', self.launcher)
        self.assertIn('Reason = "fallback"', self.launcher)
        self.assertIn('if ($storage.Reason -eq "fallback")', self.launcher)
        self.assertNotIn("SAYURI_DATA_DIR_EXPLICIT", self.launcher)


if __name__ == "__main__":
    unittest.main()
