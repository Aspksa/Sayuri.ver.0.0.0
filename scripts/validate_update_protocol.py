"""Машинная проверка протокола обновлений.

Запуск: python scripts/validate_update_protocol.py
Проверяется ровно то, что протокол объявляет обязательным, поэтому
нарушение дисциплины ID и журнала валит сборку, а не обнаруживается позже.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDS_PATH = ROOT / "UPDATE_IDS.json"
LOG_PATH = ROOT / "UPDATE_LOG.md"
STATE_PATH = ROOT / "PROJECT_STATE.json"
VERSION_PATH = ROOT / "VERSION"

PREFIXES = ("CHG", "FEAT", "BUG", "FIX", "IMP", "ARCH")

CHANGE_LINE = re.compile(
    r"^- (CHG-\d{4}) / (ARCH|FEAT|BUG|FIX|IMP)-(\d{4})(?: -> (BUG-\d{4}))? — ",
    re.MULTILINE,
)
VERSION_HEADING = re.compile(r"^##\s+v(\d+\.\d+\.\d+)\s*$", re.MULTILINE)
STATUS_LINE = re.compile(r"^Статус:\s*(\S+)\s*$", re.MULTILINE)
SECTION_SPLIT = re.compile(r"(?=^##\s+v\d+\.\d+\.\d+\s*$)", re.MULTILINE)


class ProtocolError(AssertionError):
    pass


def fail(message: str) -> None:
    raise ProtocolError(message)


def main() -> int:
    ids = json.loads(IDS_PATH.read_text(encoding="utf-8"))
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    log = LOG_PATH.read_text(encoding="utf-8")
    release_version = VERSION_PATH.read_text(encoding="utf-8").strip()

    changes = CHANGE_LINE.findall(log)
    if not changes:
        fail("UPDATE_LOG.md не содержит ни одного зарегистрированного изменения")

    primary_ids: list[str] = []
    declared: dict[str, list[int]] = {prefix: [] for prefix in PREFIXES}
    bug_ids: set[str] = set()
    fix_refs: list[tuple[str, str]] = []

    for chg_id, kind, number_text, bug_ref in changes:
        typed_id = f"{kind}-{number_text}"
        primary_ids.extend([chg_id, typed_id])
        declared["CHG"].append(int(chg_id.split("-")[1]))
        declared[kind].append(int(number_text))
        if kind == "BUG":
            bug_ids.add(typed_id)
        if kind == "FIX":
            if not bug_ref:
                fail(f"{typed_id} не ссылается на BUG")
            fix_refs.append((typed_id, bug_ref))

    duplicates = sorted(
        identifier for identifier in set(primary_ids) if primary_ids.count(identifier) > 1
    )
    if duplicates:
        fail(f"Повторные ID в UPDATE_LOG.md: {duplicates}")

    for fix_id, bug_id in fix_refs:
        if bug_id not in bug_ids:
            fail(f"{fix_id} ссылается на несуществующий {bug_id}")

    for prefix in PREFIXES:
        if prefix not in ids["last_assigned"]:
            fail(f"В UPDATE_IDS.json нет счётчика {prefix}")
        registry_value = int(ids["last_assigned"][prefix])
        journal_max = max(declared[prefix], default=0)
        if registry_value != journal_max:
            fail(
                f"Счётчик {prefix} расходится: реестр={registry_value}, журнал={journal_max}"
            )
        expected_next = f"{prefix}-{registry_value + 1:04d}"
        if ids["next"].get(prefix) != expected_next:
            fail(f"Следующий ID {prefix} должен быть {expected_next}")

    completed: list[str] = []
    in_progress: list[str] = []
    for section in SECTION_SPLIT.split(log):
        heading = VERSION_HEADING.search(section)
        if not heading:
            continue
        version = heading.group(1)
        status_match = STATUS_LINE.search(section)
        if not status_match:
            fail(f"Для v{version} не указан Статус")
        if "next_action:" not in section:
            fail(f"Для v{version} не записан next_action")
        status = status_match.group(1)
        if status == "completed":
            completed.append(version)
        elif status == "in_progress":
            in_progress.append(version)

    if not completed:
        fail("В UPDATE_LOG.md нет ни одной завершённой версии")

    latest = completed[-1]
    if release_version != latest:
        fail(f"VERSION={release_version}, последняя завершённая версия журнала={latest}")
    if state.get("version") != release_version:
        fail("PROJECT_STATE.version не совпадает с VERSION")
    if not str(state.get("next_action", "")).strip():
        fail("PROJECT_STATE.next_action пуст")

    if in_progress:
        active = ids.get("active_development", {}).get("version")
        expected = f"v{in_progress[-1]}"
        if active != expected:
            fail(f"active_development={active}, ожидается {expected}")

    print("Проверка протокола обновлений: PASS")
    print(f"Релиз: v{release_version}")
    if in_progress:
        print(f"В разработке: v{in_progress[-1]}")
    for prefix in PREFIXES:
        print(
            f"{prefix}: назначено={ids['last_assigned'][prefix]} "
            f"следующий={ids['next'][prefix]}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProtocolError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"Проверка протокола обновлений: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
