"""Манифесты модулей и их строгая валидация."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..database import SAFE_MODULE_ID

MANIFEST_SCHEMA_VERSION = 1
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ALLOWED_PERMISSIONS = frozenset(
    {
        "core.status.read",
        "config.read",
        "events.publish",
        "events.subscribe",
        "jobs.submit",
        "jobs.read",
        "checkpoints.write",
        "recovery.read",
        "log.write",
    }
)


class ModuleManifestError(ValueError):
    """Манифест небезопасен или противоречив."""


@dataclass(frozen=True)
class ModuleDependency:
    id: str
    min_version: str = "0.0.0"
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "min_version": self.min_version,
            "optional": self.optional,
        }


@dataclass(frozen=True)
class ModuleMigration:
    version: int
    description: str
    statements: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "statement_count": len(self.statements),
        }


@dataclass(frozen=True)
class ModuleManifest:
    schema_version: int
    id: str
    name: str
    version: str
    entrypoint_file: str
    entrypoint_symbol: str
    enabled: bool
    permissions: tuple[str, ...]
    dependencies: tuple[ModuleDependency, ...]
    migrations: tuple[ModuleMigration, ...]
    root: Path

    @property
    def entrypoint_path(self) -> Path:
        return self.root / self.entrypoint_file

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "entrypoint": f"{self.entrypoint_file}:{self.entrypoint_symbol}",
            "enabled": self.enabled,
            "permissions": list(self.permissions),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "migrations": [item.to_dict() for item in self.migrations],
        }


def semver_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if match is None:
        raise ModuleManifestError(f"Некорректная SemVer: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_at_least(current: str, minimum: str) -> bool:
    return semver_tuple(current) >= semver_tuple(minimum)


def _safe_relative_file(value: str, *, field: str) -> str:
    path = Path(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts:
        raise ModuleManifestError(f"{field} должен быть относительным путём внутри модуля")
    return path.as_posix()


def _dependencies(raw: Any) -> tuple[ModuleDependency, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ModuleManifestError("dependencies должен быть массивом")
    result: list[ModuleDependency] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            dep = ModuleDependency(id=item)
        elif isinstance(item, dict):
            dep = ModuleDependency(
                id=str(item.get("id", "")).strip(),
                min_version=str(item.get("min_version", "0.0.0")).strip(),
                optional=bool(item.get("optional", False)),
            )
        else:
            raise ModuleManifestError("dependency должен быть строкой или объектом")
        if not SAFE_MODULE_ID.fullmatch(dep.id):
            raise ModuleManifestError(f"Некорректный dependency id: {dep.id!r}")
        semver_tuple(dep.min_version)
        if dep.id in seen:
            raise ModuleManifestError(f"Повторная зависимость: {dep.id}")
        seen.add(dep.id)
        result.append(dep)
    return tuple(result)


def _migrations(raw: Any) -> tuple[ModuleMigration, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ModuleManifestError("database.migrations должен быть массивом")
    result: list[ModuleMigration] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ModuleManifestError("migration должен быть объектом")
        try:
            version = int(item["version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModuleManifestError("migration.version должен быть целым числом") from exc
        description = str(item.get("description", "")).strip()
        statements_raw = item.get("statements", [])
        if version < 1 or not description or not isinstance(statements_raw, list):
            raise ModuleManifestError("migration требует version>=1, description и statements[]")
        statements = tuple(str(value).strip() for value in statements_raw)
        if not statements or any(not value for value in statements):
            raise ModuleManifestError("migration.statements не может быть пустым")
        result.append(ModuleMigration(version, description, statements))

    versions = [item.version for item in result]
    if versions != list(range(1, len(result) + 1)):
        raise ModuleManifestError("версии миграций должны идти подряд: 1, 2, 3...")
    return tuple(result)


def load_manifest(module_root: Path) -> ModuleManifest:
    path = module_root / "module.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModuleManifestError(f"Не удалось прочитать {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ModuleManifestError(f"Некорректный JSON в {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ModuleManifestError("Корень module.json должен быть объектом")

    schema_version = int(raw.get("schema_version", 0))
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise ModuleManifestError(
            f"schema_version={schema_version}, поддерживается {MANIFEST_SCHEMA_VERSION}"
        )

    module_id = str(raw.get("id", "")).strip()
    name = str(raw.get("name", "")).strip()
    version = str(raw.get("version", "")).strip()
    if not SAFE_MODULE_ID.fullmatch(module_id):
        raise ModuleManifestError(f"Некорректный module id: {module_id!r}")
    if not name:
        raise ModuleManifestError("name обязателен")
    semver_tuple(version)

    entrypoint = str(raw.get("entrypoint", "module.py:Module")).strip()
    file_name, separator, symbol = entrypoint.partition(":")
    file_name = _safe_relative_file(file_name, field="entrypoint")
    if not separator or not symbol.isidentifier() or not file_name.endswith(".py"):
        raise ModuleManifestError("entrypoint должен иметь вид relative.py:PythonSymbol")

    permissions_raw = raw.get("permissions", [])
    if not isinstance(permissions_raw, list):
        raise ModuleManifestError("permissions должен быть массивом")
    permissions = tuple(str(value).strip() for value in permissions_raw)
    unknown = sorted(set(permissions) - ALLOWED_PERMISSIONS)
    if unknown:
        raise ModuleManifestError(f"Неизвестные permissions: {unknown}")
    if len(set(permissions)) != len(permissions):
        raise ModuleManifestError("permissions содержит повторы")

    database = raw.get("database", {})
    if not isinstance(database, dict):
        raise ModuleManifestError("database должен быть объектом")

    return ModuleManifest(
        schema_version=schema_version,
        id=module_id,
        name=name,
        version=version,
        entrypoint_file=file_name,
        entrypoint_symbol=symbol,
        enabled=bool(raw.get("enabled", True)),
        permissions=permissions,
        dependencies=_dependencies(raw.get("dependencies")),
        migrations=_migrations(database.get("migrations")),
        root=module_root.resolve(),
    )
