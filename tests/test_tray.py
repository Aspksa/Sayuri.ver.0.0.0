"""Проверки режима трея и иконки приложения.

PowerShell в среде сборки на Linux недоступен, поэтому контракт трея
проверяется статически: структура скрипта, порядок объявлений, сценарии
остановки. Реальный запуск выполняет Windows-джоба CI.
"""

from __future__ import annotations

import importlib.util
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

from sayuri_yukishiro.paths import PROJECT_ROOT

TRAY = PROJECT_ROOT / "scripts" / "tray.ps1"
LAUNCHER = PROJECT_ROOT / "scripts" / "launcher.ps1"
ICON = PROJECT_ROOT / "assets" / "sayuri.ico"
CHECKER = PROJECT_ROOT / "scripts" / "check_powershell.py"


def load_icon_module():
    spec = importlib.util.spec_from_file_location(
        "make_icon", PROJECT_ROOT / "scripts" / "make_icon.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IconTests(unittest.TestCase):
    def setUp(self) -> None:
        self.icon = load_icon_module()

    def test_icon_asset_exists(self) -> None:
        self.assertTrue(ICON.is_file(), "assets/sayuri.ico отсутствует")

    def test_icon_is_a_valid_ico_container(self) -> None:
        data = ICON.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual(reserved, 0)
        self.assertEqual(kind, 1, "type=1 означает именно иконку, а не курсор")
        self.assertEqual(count, len(self.icon.SIZES))

    def test_every_declared_size_is_present_and_32bit(self) -> None:
        data = ICON.read_bytes()
        _reserved, _kind, count = struct.unpack("<HHH", data[:6])
        found: list[int] = []
        for index in range(count):
            entry = data[6 + 16 * index : 22 + 16 * index]
            width, _height, _colors, _res, planes, bits, size, offset = struct.unpack(
                "<BBBBHHII", entry
            )
            found.append(width or 256)
            self.assertEqual(planes, 1)
            self.assertEqual(bits, 32, "альфа-канал нужен для чистых краёв в трее")
            self.assertGreater(size, 0)
            self.assertLessEqual(offset + size, len(data), "слой выходит за файл")
        self.assertEqual(found, list(self.icon.SIZES))

    def test_tray_size_is_included(self) -> None:
        # Область уведомлений использует 16 px при обычном масштабе.
        self.assertIn(16, self.icon.SIZES)

    def test_generator_is_reproducible(self) -> None:
        self.assertEqual(self.icon.build_ico(), ICON.read_bytes())

    def test_small_sizes_drop_fine_detail(self) -> None:
        # Тонкие ответвления при 16 px превратились бы в кашу.
        self.assertEqual(self.icon.detail_for(16)["branches"], ())
        self.assertTrue(self.icon.detail_for(32)["branches"])
        self.assertGreater(
            self.icon.detail_for(16)["spoke_width"],
            self.icon.detail_for(32)["spoke_width"],
        )

    def test_rendered_pixels_have_transparent_corners_and_opaque_centre(self) -> None:
        size = 32
        pixels = self.icon.render_bgra(size)
        self.assertEqual(len(pixels), size * size * 4)

        def alpha_at(x: int, y: int) -> int:
            row = (size - 1 - y) * size * 4
            return pixels[row + x * 4 + 3]

        self.assertEqual(alpha_at(0, 0), 0, "угол должен быть прозрачным")
        self.assertEqual(alpha_at(size // 2, size // 2), 255, "центр должен быть плотным")


class PowerShellStaticCheckTests(unittest.TestCase):
    def test_static_checker_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_checker_detects_call_before_definition(self) -> None:
        import tempfile

        spec = importlib.util.spec_from_file_location("checker", CHECKER)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)

        broken = "Do-Thing\n\nfunction Do-Thing {\n    return 1\n}\n"
        self.assertTrue(checker.definition_order(broken))

        correct = "function Do-Thing {\n    return 1\n}\n\nDo-Thing\n"
        self.assertEqual(checker.definition_order(correct), [])

    def test_checker_detects_automatic_variable_assignment(self) -> None:
        spec = importlib.util.spec_from_file_location("checker", CHECKER)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.assertTrue(checker.automatic_variables('$args = @("a")'))
        self.assertEqual(checker.automatic_variables('$cliArgs = @("a")'), [])


class TrayContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tray = TRAY.read_text(encoding="utf-8")
        self.launcher = LAUNCHER.read_text(encoding="utf-8")

    def test_tray_script_exists(self) -> None:
        self.assertTrue(TRAY.is_file())

    def test_uses_notify_icon_in_notification_area(self) -> None:
        self.assertIn("System.Windows.Forms.NotifyIcon", self.tray)
        self.assertIn("$script:Tray.Visible = $true", self.tray)

    def test_uses_project_icon_with_system_fallback(self) -> None:
        self.assertIn("assets\\sayuri.ico", self.tray)
        self.assertIn("System.Drawing.SystemIcons", self.tray)

    def test_menu_covers_expected_actions(self) -> None:
        for label in (
            "Открыть Sayuri",
            "Состояние",
            "Показать окно",
            "Перезапустить ядро",
            "Выход",
        ):
            with self.subTest(label):
                self.assertIn(label, self.tray)

    def test_double_click_opens_the_site(self) -> None:
        self.assertIn("add_DoubleClick", self.tray)

    def test_console_window_can_be_hidden_and_restored(self) -> None:
        self.assertIn("GetConsoleWindow", self.tray)
        self.assertIn("ShowWindow", self.tray)
        self.assertIn("function Hide-Console", self.tray)
        self.assertIn("function Show-Console", self.tray)

    def test_exit_prefers_soft_shutdown_over_kill(self) -> None:
        stop_block = self.tray[self.tray.index("function Stop-Core") :]
        stop_block = stop_block[: stop_block.index("\nfunction ")]
        soft = stop_block.index('Invoke-CoreApi -Path "shutdown"')
        kill = stop_block.index("$process.Kill()")
        self.assertLess(soft, kill, "мягкая остановка должна идти раньше Kill")
        self.assertIn("WaitForExit(15000)", stop_block)

    def test_shutdown_is_authenticated_by_token(self) -> None:
        self.assertIn('"X-Sayuri-Token" = $endpoint.token', self.tray)

    def test_tray_uses_the_selected_data_directory(self) -> None:
        self.assertIn('[string]$DataDir = ""', self.tray)
        self.assertIn('$env:SAYURI_DATA_DIR = $DataDir', self.tray)
        self.assertIn('$EndpointPath = Join-Path $DataDir "runtime\\endpoint.json"', self.tray)
        self.assertIn('DataDir       = $storage.Path', self.launcher)
        self.assertNotIn('Join-Path $Root "data\\runtime\\endpoint.json"', self.tray)

    def test_state_is_polled_into_the_tooltip(self) -> None:
        self.assertIn("System.Windows.Forms.Timer", self.tray)
        self.assertIn("function Update-TrayState", self.tray)
        self.assertIn('Invoke-CoreApi -Path "health"', self.tray)

    def test_tooltip_respects_the_framework_limit(self) -> None:
        # NotifyIcon.Text длиннее 63 символов бросает ArgumentException.
        self.assertIn("function Set-TrayTooltip", self.tray)
        self.assertIn("-gt 63", self.tray)
        assignments = re.findall(r"\$script:Tray\.Text\s*=", self.tray)
        self.assertEqual(
            len(assignments), 1, "подсказка должна выставляться только через Set-TrayTooltip"
        )

    def test_tray_disposes_icon_on_exit(self) -> None:
        self.assertIn("$script:Tray.Dispose()", self.tray)
        self.assertIn("finally", self.tray)

    def test_state_change_raises_a_notification(self) -> None:
        self.assertIn("function Show-Balloon", self.tray)
        self.assertIn("$script:LastStatus", self.tray)


class LauncherTrayIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = LAUNCHER.read_text(encoding="utf-8")

    def test_tray_is_default_and_can_be_disabled(self) -> None:
        self.assertIn("[switch]$NoTray", self.launcher)
        self.assertIn("[switch]$Tray", self.launcher)
        self.assertIn("if (-not $NoTray", self.launcher)

    def test_core_runs_as_a_separate_hidden_process(self) -> None:
        self.assertIn("-WindowStyle Hidden -PassThru", self.launcher)
        self.assertIn("function Wait-ForEndpoint", self.launcher)

    def test_launcher_waits_for_its_own_core_process(self) -> None:
        # Файл от прошлого запуска не должен приниматься за готовность.
        self.assertIn("$data.pid -eq $ExpectedPid", self.launcher)

    def test_missing_forms_falls_back_to_foreground_core(self) -> None:
        self.assertIn("function Test-WindowsForms", self.launcher)
        self.assertIn("Windows Forms недоступны", self.launcher)

    def test_dead_core_is_not_left_running_when_tray_fails(self) -> None:
        self.assertIn("$trayCode -eq 2", self.launcher)
        self.assertIn("$core.Kill()", self.launcher)

    def test_powershell_5_has_no_isWindows_variable(self) -> None:
        # $IsWindows появился в PowerShell 7: в 5.1 его нет.
        self.assertIn("$IsWindowsHost", self.launcher)
        self.assertNotRegex(self.launcher, r"if\s*\(\s*\$IsWindows\s*\)")


if __name__ == "__main__":
    unittest.main()
