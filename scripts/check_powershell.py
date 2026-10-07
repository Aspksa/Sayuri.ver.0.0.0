"""Статические проверки PowerShell-скриптов.

Полный парсер есть только в Windows-джобе CI. Здесь ловится то, что
приводит к ошибке уже при запуске: разбалансированные скобки, вызов
функции до её объявления и присваивание автоматических переменных.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (ROOT / "scripts" / "launcher.ps1", ROOT / "scripts" / "tray.ps1")

FUNCTION_DEFINITION = re.compile(r"^function\s+([A-Za-z][\w-]*)\s*\{", re.MULTILINE)
# Автоматические переменные PowerShell: присваивание ломает рантайм.
AUTOMATIC_VARIABLES = ("args", "input", "this", "_", "error", "host", "pwd")


def balanced(text: str) -> list[str]:
    problems: list[str] = []
    for opening, closing in (("{", "}"), ("(", ")"), ("[", "]")):
        count_open = text.count(opening)
        count_close = text.count(closing)
        if count_open != count_close:
            problems.append(
                f"несбалансированные {opening}{closing}: {count_open} против {count_close}"
            )
    return problems


def definition_order(text: str) -> list[str]:
    """Вызов функции из тела скрипта обязан идти после её объявления."""

    problems: list[str] = []
    lines = text.splitlines()
    definitions: dict[str, int] = {}
    for match in FUNCTION_DEFINITION.finditer(text):
        name = match.group(1)
        line_number = text[: match.start()].count("\n")
        definitions.setdefault(name, line_number)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Отступ означает тело функции или блок-скрипт: он выполняется позже.
        if line[:1].isspace():
            continue
        if stripped.startswith("function "):
            continue
        for name, defined_at in definitions.items():
            if re.match(rf"^{re.escape(name)}\b", stripped) and index < defined_at:
                problems.append(
                    f"строка {index + 1}: {name} вызывается до объявления "
                    f"(строка {defined_at + 1})"
                )
    return problems


def automatic_variables(text: str) -> list[str]:
    problems: list[str] = []
    for name in AUTOMATIC_VARIABLES:
        if re.search(rf"\$(?:script:)?{name}\s*=(?!=)", text, re.IGNORECASE):
            problems.append(f"присваивание автоматической переменной ${name}")
    return problems


def main() -> int:
    failed = False
    for script in SCRIPTS:
        if not script.is_file():
            print(f"FAIL {script.name}: файл не найден", file=sys.stderr)
            failed = True
            continue
        text = script.read_text(encoding="utf-8")
        problems = balanced(text) + definition_order(text) + automatic_variables(text)
        if problems:
            failed = True
            for problem in problems:
                print(f"FAIL {script.name}: {problem}", file=sys.stderr)
        else:
            print(f"OK   {script.name}")
    if failed:
        print("Статическая проверка PowerShell: FAIL", file=sys.stderr)
        return 1
    print("Статическая проверка PowerShell: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
