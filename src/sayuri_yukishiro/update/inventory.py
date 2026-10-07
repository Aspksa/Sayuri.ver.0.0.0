"""Инвентарь компонентов проекта и их версий.

Обновление показывает не одну цифру, а каждую часть, которая может
измениться. Поэтому «было и стало» строится по этому же списку.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..database import SCHEMA_VERSION
from ..paths import PROJECT_ROOT, project_version
from ..version import CORE_VERSION

MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Component:
    key: str
    title: str
    version: str
    kind: str
    source: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _file_version(path: Path, fallback: str = "—") -> str:
    """Версия файла без объявленной версии — короткий хеш содержимого.

    Так лаунчер, трей и интерфейс тоже участвуют в сравнении «было и стало»:
    пользователь видит, что файл изменился, даже если номера версии у него нет.
    """

    try:
        data = path.read_bytes()
    except OSError:
        return fallback
    return hashlib.sha256(data).hexdigest()[:8]


def _protocol_version(root: Path) -> str:
    ids = root / "UPDATE_IDS.json"
    try:
        import json

        data = json.loads(ids.read_text(encoding="utf-8"))
        return f"schema {data.get('schema_version', '?')}"
    except Exception:
        return "—"


def collect_components(root: Path | None = None) -> list[Component]:
    base = root or PROJECT_ROOT
    return [
        Component(
            key="project",
            title="Проект",
            version=project_version(),
            kind="semver",
            source="VERSION",
            detail="общая версия релиза",
        ),
        Component(
            key="core",
            title="Системное ядро",
            version=CORE_VERSION,
            kind="semver",
            source="src/sayuri_yukishiro/version.py",
            detail="службы, события, задачи, контрольные точки",
        ),
        Component(
            key="database",
            title="Схема базы",
            version=f"v{SCHEMA_VERSION}",
            kind="schema",
            source="src/sayuri_yukishiro/database.py",
            detail="миграции центральной базы",
        ),
        Component(
            key="protocol",
            title="Протокол обновлений",
            version=_protocol_version(base),
            kind="schema",
            source="UPDATE_IDS.json",
            detail="реестр ID и дисциплина версий",
        ),
        Component(
            key="web",
            title="Интерфейс",
            version=_file_version(base / "web" / "index.html"),
            kind="digest",
            source="web/index.html",
            detail="локальный web shell",
        ),
        Component(
            key="launcher",
            title="Лаунчер",
            version=_file_version(base / "scripts" / "launcher.ps1"),
            kind="digest",
            source="scripts/launcher.ps1",
            detail="запуск с любого носителя",
        ),
        Component(
            key="tray",
            title="Значок у часов",
            version=_file_version(base / "scripts" / "tray.ps1"),
            kind="digest",
            source="scripts/tray.ps1",
            detail="меню и состояние в области уведомлений",
        ),
        Component(
            key="entry",
            title="Точка входа",
            version=_file_version(base / "Sayuri Yukishiro.bat"),
            kind="digest",
            source="Sayuri Yukishiro.bat",
            detail="единственный файл для пользователя",
        ),
    ]


def components_at_ref(git, ref: str, root: Path | None = None) -> list[Component]:
    """Те же компоненты, но какими они будут после обновления."""

    base = root or PROJECT_ROOT
    current = {item.key: item for item in collect_components(base)}

    def text_at(path: str) -> str | None:
        return git.file_at(ref, path)

    def digest_at(path: str) -> str:
        content = text_at(path)
        if content is None:
            return "—"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]

    result: list[Component] = []
    for key, item in current.items():
        version = item.version
        if key == "project":
            raw = text_at("VERSION")
            version = raw.strip() if raw else item.version
        elif key == "core":
            raw = text_at("src/sayuri_yukishiro/version.py")
            version = _extract_assignment(raw, "CORE_VERSION") or item.version
        elif key == "database":
            raw = text_at("src/sayuri_yukishiro/database.py")
            found = _extract_schema_version(raw)
            version = f"v{found}" if found else item.version
        elif key == "protocol":
            raw = text_at("UPDATE_IDS.json")
            version = _extract_schema_field(raw) or item.version
        elif item.kind == "digest":
            version = digest_at(item.source)
        result.append(
            Component(
                key=item.key,
                title=item.title,
                version=version,
                kind=item.kind,
                source=item.source,
                detail=item.detail,
            )
        )
    return result


def _extract_assignment(text: str | None, name: str) -> str | None:
    if not text:
        return None
    import re

    match = re.search(rf'^{re.escape(name)}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def _extract_schema_version(text: str | None) -> int | None:
    if not text:
        return None
    import re

    versions = [int(value) for value in re.findall(r"^\s*version=(\d+),", text, re.MULTILINE)]
    return max(versions) if versions else None


def _extract_schema_field(text: str | None) -> str | None:
    if not text:
        return None
    try:
        import json

        return f"schema {json.loads(text).get('schema_version', '?')}"
    except Exception:
        return None


def diff_components(
    before: list[Component],
    after: list[Component],
) -> list[dict[str, Any]]:
    """Сравнение «было и стало» по каждому компоненту."""

    after_by_key = {item.key: item for item in after}
    rows: list[dict[str, Any]] = []
    for item in before:
        target = after_by_key.get(item.key)
        new_version = target.version if target else item.version
        rows.append(
            {
                "key": item.key,
                "title": item.title,
                "kind": item.kind,
                "source": item.source,
                "detail": item.detail,
                "before": item.version,
                "after": new_version,
                "changed": new_version != item.version,
            }
        )
    return rows
