"""Проверки готовности обновления.

Каждая проверка отвечает на один вопрос и объясняет, что делать, если
ответ отрицательный. Установка начинается только когда нет ни одного
блокирующего отказа.
"""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import console
from ..paths import DATA_DIR, ensure_runtime_dirs
from .git_client import GitClient, GitError

BLOCKING = "blocking"
WARNING = "warning"
INFO = "info"

MIN_FREE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class UpdateCheck:
    name: str
    title: str
    ok: bool
    severity: str
    detail: str
    hint: str = ""

    @property
    def kind(self) -> str:
        if self.ok:
            return console.OK
        return console.WARN if self.severity != BLOCKING else console.BAD

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind
        return data


def _check(name: str, title: str, ok: bool, severity: str, detail: str, hint: str = "") -> UpdateCheck:
    return UpdateCheck(name, title, ok, severity if not ok else INFO, detail, hint if not ok else "")


def run_update_checks(git: GitClient, *, data_dir: Path | None = None) -> list[UpdateCheck]:
    checks: list[UpdateCheck] = []

    if not git.available():
        return [
            _check(
                "git",
                "Git установлен",
                False,
                BLOCKING,
                "Git не найден в системе",
                "Установите Git или обновите проект вручную, заменив файлы.",
            )
        ]
    checks.append(_check("git", "Git установлен", True, INFO, "Git доступен"))

    try:
        if not git.is_worktree():
            checks.append(
                _check(
                    "worktree",
                    "Проект получен из репозитория",
                    False,
                    BLOCKING,
                    "Каталог проекта не является рабочей копией Git",
                    "Скачанный ZIP-архив обновлять нельзя: нужен git clone.",
                )
            )
            return checks
        checks.append(
            _check("worktree", "Проект получен из репозитория", True, INFO, "Рабочая копия Git")
        )

        dirty = git.dirty_files()
        checks.append(
            _check(
                "clean",
                "Нет своих несохранённых правок",
                not dirty,
                BLOCKING,
                "Чисто" if not dirty else f"Изменённых файлов: {len(dirty)}",
                "Обновление перезаписало бы вашу работу. Сохраните или отмените правки.",
            )
        )

        branch = git.current_branch()
        checks.append(
            _check(
                "branch",
                "Ветка определена",
                branch is not None,
                BLOCKING,
                f"Ветка {branch}" if branch else "HEAD не на ветке (detached)",
                "Перейдите на ветку: git checkout <ветка>.",
            )
        )

        upstream = git.upstream()
        checks.append(
            _check(
                "upstream",
                "Источник обновлений настроен",
                upstream is not None,
                BLOCKING,
                f"Источник: {upstream}" if upstream else "У ветки нет upstream",
                "Задайте источник: git branch --set-upstream-to=origin/<ветка>.",
            )
        )

        remote = git.remote_url()
        checks.append(
            _check(
                "remote",
                "Адрес репозитория известен",
                remote is not None,
                BLOCKING,
                remote or "Удалённый репозиторий не настроен",
                "Добавьте источник: git remote add origin <адрес>.",
            )
        )
    except GitError as exc:
        checks.append(
            _check("git_state", "Состояние репозитория", False, BLOCKING, str(exc), "")
        )
        return checks

    target_dir = data_dir or DATA_DIR
    # На свежем носителе каталога данных ещё нет: создаём, иначе проверки
    # места и записи отказали бы на пустом месте.
    try:
        if data_dir is None:
            ensure_runtime_dirs()
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    try:
        free = shutil.disk_usage(target_dir).free
        enough = free >= MIN_FREE_BYTES
        checks.append(
            _check(
                "space",
                "Хватает места на носителе",
                enough,
                BLOCKING,
                f"Свободно {free // (1024 * 1024)} МБ",
                "Освободите место: резервная копия и обновление не поместятся.",
            )
        )
    except OSError as exc:
        checks.append(
            _check("space", "Хватает места на носителе", False, WARNING, str(exc), "")
        )

    try:
        probe = target_dir / ".update-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks.append(
            _check("writable", "Носитель доступен для записи", True, INFO, str(target_dir))
        )
    except OSError as exc:
        checks.append(
            _check(
                "writable",
                "Носитель доступен для записи",
                False,
                BLOCKING,
                f"Нет записи: {exc}",
                "Снимите защиту от записи или задайте SAYURI_DATA_DIR.",
            )
        )

    return checks


def blocking_failures(checks: list[UpdateCheck]) -> list[UpdateCheck]:
    return [check for check in checks if not check.ok and check.severity == BLOCKING]


def checks_summary(checks: list[UpdateCheck]) -> dict[str, int]:
    return {
        "total": len(checks),
        "passed": sum(1 for check in checks if check.ok),
        "warnings": sum(1 for check in checks if not check.ok and check.severity == WARNING),
        "blocking": len(blocking_failures(checks)),
    }
