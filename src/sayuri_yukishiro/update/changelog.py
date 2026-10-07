"""Разбор UPDATE_LOG.md в структурированную историю.

Журнал уже обязателен по протоколу и содержит ровно то, что нужно
показать пользователю: какие изменения вошли в версию, какие проверки
прошли и каким коммитом это закреплено. Поэтому история в интерфейсе
читается из него, а не ведётся вторым, расходящимся списком.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..paths import PROJECT_ROOT

VERSION_HEADING = re.compile(r"^##\s+v(\d+\.\d+\.\d+)\s*$", re.MULTILINE)
SECTION_SPLIT = re.compile(r"(?=^##\s+v\d+\.\d+\.\d+\s*$)", re.MULTILINE)
FIELD = re.compile(r"^(Дата|Статус):\s*(.+?)\s*$", re.MULTILINE)
CHANGE_LINE = re.compile(
    r"^- (CHG-\d{4}) / (ARCH|FEAT|BUG|FIX|IMP)-(\d{4})(?: -> (BUG-\d{4}))? — (.+)$",
    re.MULTILINE,
)
# Строка проверки: «- tests: PASS (117 тестов)» — статус отдельно от примечания.
# [ \t]* вместо \s*: \s поглощает перевод строки, и примечание одной
# проверки захватывало бы следующую строку целиком.
CHECK_LINE = re.compile(
    r"^- ([a-z0-9-]+):[ \t]*(PASS|FAIL|PENDING|NOT_CONFIGURED|NOT_APPLICABLE)[ \t]*(.*)$",
    re.MULTILINE,
)
SHA_LINE = re.compile(r"^([0-9a-f]{40})\s*$", re.MULTILINE)
NEXT_ACTION = re.compile(r"^next_action:\s*(.+?)\s*$", re.MULTILINE)

KIND_TITLES = {
    "FEAT": "Новые возможности",
    "FIX": "Исправления",
    "IMP": "Улучшения",
    "ARCH": "Архитектура",
    "BUG": "Зарегистрированные дефекты",
}
KIND_ORDER = ("FEAT", "FIX", "IMP", "ARCH", "BUG")


@dataclass(frozen=True)
class Change:
    chg: str
    kind: str
    typed_id: str
    bug_ref: str | None
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VersionEntry:
    version: str
    date: str = ""
    status: str = ""
    changes: list[Change] = field(default_factory=list)
    checks: list[dict[str, str]] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    next_action: str = ""

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for change in self.changes:
            result[change.kind] = result.get(change.kind, 0) + 1
        return result

    def grouped(self) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for kind in KIND_ORDER:
            items = [change for change in self.changes if change.kind == kind]
            if items:
                groups.append(
                    {
                        "kind": kind,
                        "title": KIND_TITLES[kind],
                        "items": [change.to_dict() for change in items],
                    }
                )
        return groups

    def to_dict(self) -> dict[str, Any]:
        passed = sum(1 for check in self.checks if check["result"] == "PASS")
        failed = sum(1 for check in self.checks if check["result"] == "FAIL")
        return {
            "version": self.version,
            "date": self.date,
            "status": self.status,
            "changes": [change.to_dict() for change in self.changes],
            "groups": self.grouped(),
            "counts": self.counts,
            "change_count": len(self.changes),
            "checks": self.checks,
            "checks_passed": passed,
            "checks_failed": failed,
            "commits": list(self.commits),
            "next_action": self.next_action,
        }


def parse_changelog(text: str) -> list[VersionEntry]:
    entries: list[VersionEntry] = []
    for section in SECTION_SPLIT.split(text):
        heading = VERSION_HEADING.search(section)
        if not heading:
            continue
        entry = VersionEntry(version=heading.group(1))

        for name, value in FIELD.findall(section):
            if name == "Дата":
                entry.date = value
            else:
                entry.status = value

        for chg, kind, number, bug_ref, description in CHANGE_LINE.findall(section):
            entry.changes.append(
                Change(
                    chg=chg,
                    kind=kind,
                    typed_id=f"{kind}-{number}",
                    bug_ref=bug_ref or None,
                    text=description.strip(),
                )
            )

        checks_block = _slice_between(section, "Проверки:", "Commit:")
        for name, result, note in CHECK_LINE.findall(checks_block):
            entry.checks.append(
                {"name": name, "result": result, "note": note.strip(" ()")}
            )

        entry.commits = SHA_LINE.findall(section)
        action = NEXT_ACTION.search(section)
        entry.next_action = action.group(1) if action else ""
        entries.append(entry)

    entries.reverse()  # свежие версии первыми
    return entries


def _slice_between(text: str, start: str, end: str) -> str:
    try:
        begin = text.index(start) + len(start)
    except ValueError:
        return ""
    try:
        finish = text.index(end, begin)
    except ValueError:
        finish = len(text)
    return text[begin:finish]


def load_changelog(path: Path | None = None) -> list[VersionEntry]:
    target = path or (PROJECT_ROOT / "UPDATE_LOG.md")
    try:
        return parse_changelog(target.read_text(encoding="utf-8"))
    except OSError:
        return []


def changelog_payload(path: Path | None = None) -> dict[str, Any]:
    entries = load_changelog(path)
    total_changes = sum(len(entry.changes) for entry in entries)
    return {
        "versions": [entry.to_dict() for entry in entries],
        "version_count": len(entries),
        "total_changes": total_changes,
        "kind_titles": KIND_TITLES,
    }
