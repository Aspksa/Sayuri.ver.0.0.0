"""Служба обновления проекта из GitHub.

Главное требование: обновление не должно ломать проект. Отсюда вся
конструкция:

1. до установки — проверки готовности, и ни одного блокирующего отказа;
2. только fast-forward от настроенного источника текущей ветки;
3. резервная копия базы и файлов состояния до изменений;
4. установка стадиями, каждая публикует событие прогресса;
5. после установки — проверка работоспособности нового кода;
6. если проверка провалилась — откат к зафиксированному коммиту и
   восстановление базы из копии.

Служба не перезапускает ядро сама: работающий процесс всё ещё старый код,
и решение о перезапуске остаётся за пользователем.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from ..core.service import ManagedService
from ..database import CoreDatabase
from ..paths import PROJECT_ROOT, project_version
from . import backup as backup_module
from .changelog import changelog_payload
from .checks import blocking_failures, checks_summary, run_update_checks
from .git_client import GitClient, GitError
from .inventory import collect_components, components_at_ref, diff_components

STAGE_SEQUENCE = (
    ("checks", "Проверка готовности"),
    ("backup", "Резервная копия"),
    ("fetch", "Загрузка изменений"),
    ("apply", "Установка"),
    ("verify", "Проверка после установки"),
    ("done", "Готово"),
)

VERIFY_TIMEOUT = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Stage:
    key: str
    title: str
    state: str = "pending"
    detail: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UpdateProgress:
    id: str
    status: str = "idle"
    stages: list[Stage] = field(default_factory=list)
    message: str = ""
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    from_commit: str = ""
    to_commit: str = ""
    from_version: str = ""
    to_version: str = ""
    backup_id: str = ""
    rolled_back: bool = False
    restart_required: bool = False

    @property
    def percent(self) -> int:
        if not self.stages:
            return 0
        finished = sum(1 for stage in self.stages if stage.state in {"done", "skipped"})
        return int(round(100 * finished / len(self.stages)))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stages"] = [stage.to_dict() for stage in self.stages]
        data["percent"] = self.percent
        return data


class UpdateService(ManagedService):
    name = "update"

    def __init__(
        self,
        db: CoreDatabase,
        *,
        root: Path | None = None,
        publish: Callable[[str, dict[str, Any]], None] | None = None,
        verify: Callable[[], tuple[bool, str]] | None = None,
    ) -> None:
        super().__init__()
        self._db = db
        self._root = root or PROJECT_ROOT
        self._git = GitClient(self._root)
        self._publish = publish or (lambda _type, _payload: None)
        self._verify = verify or self._default_verify
        self._lock = RLock()
        self._plan: dict[str, Any] | None = None
        self._progress = UpdateProgress(id="", status="idle")
        self._last_check: str = ""

    # --- состояние ---------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            progress = self._progress.to_dict()
            plan = dict(self._plan) if self._plan else None
            last_check = self._last_check

        return {
            "project_version": project_version(),
            "components": [item.to_dict() for item in collect_components(self._root)],
            "repository": self._repository_info(),
            "last_check": last_check,
            "plan": plan,
            "progress": progress,
            "backups": backup_module.list_backups(),
            "busy": progress["status"] in {"running"},
        }

    def _repository_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "git_available": self._git.available(),
            "worktree": False,
            "branch": None,
            "upstream": None,
            "remote": None,
            "commit": None,
            "clean": None,
        }
        if not info["git_available"]:
            return info
        try:
            info["worktree"] = self._git.is_worktree()
            if not info["worktree"]:
                return info
            info["branch"] = self._git.current_branch()
            info["upstream"] = self._git.upstream()
            info["remote"] = self._git.remote_url()
            info["commit"] = self._git.head()
            info["clean"] = self._git.is_clean()
        except GitError as exc:
            info["error"] = str(exc)
        return info

    def history(self) -> dict[str, Any]:
        payload = changelog_payload(self._root / "UPDATE_LOG.md")
        payload["runs"] = self._db.list_update_runs(20)
        return payload

    # --- проверка обновления ----------------------------------------

    def check(self) -> dict[str, Any]:
        """Проверить готовность и построить план. Ничего не меняет."""

        checks = run_update_checks(self._git)
        summary = checks_summary(checks)
        plan: dict[str, Any] = {
            "id": str(uuid4()),
            "created_at": _now(),
            "checks": [check.to_dict() for check in checks],
            "checks_summary": summary,
            "ready": summary["blocking"] == 0,
            "update_available": False,
            "reason": "",
            "components": [],
            "commits": [],
            "files": [],
            "from_commit": "",
            "to_commit": "",
            "from_version": project_version(),
            "to_version": project_version(),
        }

        if not plan["ready"]:
            plan["reason"] = "blocking_checks"
            failures = blocking_failures(checks)
            plan["reason_text"] = "; ".join(check.detail for check in failures)
            self._remember(plan)
            return plan

        try:
            upstream = self._git.upstream()
            remote, _, branch = (upstream or "").partition("/")
            if not remote or not branch:
                plan["reason"] = "no_upstream"
                plan["reason_text"] = "Источник обновлений не распознан"
                self._remember(plan)
                return plan

            self._git.fetch(remote, branch)
            current = self._git.head()
            target = self._git.resolve(upstream)

            plan["from_commit"] = current
            plan["to_commit"] = target
            plan["upstream"] = upstream

            if current == target:
                plan["reason"] = "up_to_date"
                plan["reason_text"] = "Установлена последняя версия"
                self._remember(plan)
                return plan

            if not self._git.is_ancestor(current, target):
                # Не fast-forward: либо локальные коммиты, либо переписанная
                # история. Автоматически такое сливать нельзя.
                plan["reason"] = "not_fast_forward"
                plan["reason_text"] = (
                    "История разошлась с источником: обновление возможно только "
                    "вперёд по той же истории"
                )
                self._remember(plan)
                return plan

            before = collect_components(self._root)
            after = components_at_ref(self._git, target, self._root)
            plan["components"] = diff_components(before, after)
            plan["commits"] = [item.to_dict() for item in self._git.commits_between(current, target)]
            plan["files"] = self._git.changed_files(current, target)
            plan["update_available"] = True
            plan["reason"] = "update_available"
            version_row = next(
                (row for row in plan["components"] if row["key"] == "project"), None
            )
            if version_row:
                plan["to_version"] = version_row["after"]
            plan["changed_components"] = sum(
                1 for row in plan["components"] if row["changed"]
            )
        except GitError as exc:
            plan["ready"] = False
            plan["reason"] = "git_error"
            plan["reason_text"] = str(exc)

        self._remember(plan)
        return plan

    def _remember(self, plan: dict[str, Any]) -> None:
        with self._lock:
            self._plan = plan
            self._last_check = plan["created_at"]
        self._publish(
            "update.checked",
            {
                "ready": plan["ready"],
                "update_available": plan["update_available"],
                "reason": plan["reason"],
                "to_version": plan.get("to_version"),
            },
        )

    def plan(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._plan) if self._plan else None

    def progress(self) -> dict[str, Any]:
        with self._lock:
            return self._progress.to_dict()

    # --- установка ---------------------------------------------------

    def ensure_can_apply(self, plan_id: str | None = None) -> dict[str, Any]:
        """Проверить возможность установки синхронно.

        Вызывается до постановки фоновой задачи: иначе отказ всплыл бы
        внутри задачи, а интерфейс уже сообщил бы «установка началась».
        """

        with self._lock:
            if self._progress.status == "running":
                raise RuntimeError("Обновление уже выполняется")
            plan = dict(self._plan) if self._plan else None

        if plan is None:
            raise RuntimeError("Сначала выполните проверку обновления")
        if plan_id is not None and plan["id"] != plan_id:
            raise RuntimeError("План устарел: выполните проверку заново")
        if not plan["ready"] or not plan["update_available"]:
            raise RuntimeError(
                f"Обновление невозможно: {plan.get('reason_text') or plan['reason']}"
            )
        return plan

    def apply(self, *, plan_id: str | None = None) -> dict[str, Any]:
        plan = self.ensure_can_apply(plan_id)

        progress = UpdateProgress(
            id=str(uuid4()),
            status="running",
            stages=[Stage(key=key, title=title) for key, title in STAGE_SEQUENCE],
            started_at=_now(),
            from_commit=plan["from_commit"],
            to_commit=plan["to_commit"],
            from_version=plan["from_version"],
            to_version=plan["to_version"],
        )
        with self._lock:
            self._progress = progress
        self._publish("update.started", {"id": progress.id, "to_version": progress.to_version})
        self._db.create_update_run(
            progress.id,
            from_commit=progress.from_commit,
            to_commit=progress.to_commit,
            from_version=progress.from_version,
            to_version=progress.to_version,
        )

        try:
            self._run_stages(progress, plan)
        except Exception as exc:  # стадии сами решают, что делать с ошибкой
            progress.status = "failed"
            progress.error = str(exc)
        finally:
            progress.finished_at = _now()
            self._db.finish_update_run(
                progress.id,
                status=progress.status,
                stages=[stage.to_dict() for stage in progress.stages],
                error=progress.error,
                rolled_back=progress.rolled_back,
                backup_id=progress.backup_id,
            )
            self._publish(
                "update.finished",
                {
                    "id": progress.id,
                    "status": progress.status,
                    "rolled_back": progress.rolled_back,
                    "error": progress.error,
                },
            )
        return progress.to_dict()

    def _run_stages(self, progress: UpdateProgress, plan: dict[str, Any]) -> None:
        self._begin(progress, "checks")
        checks = run_update_checks(self._git)
        failures = blocking_failures(checks)
        if failures:
            self._fail(progress, "checks", "; ".join(check.detail for check in failures))
            progress.status = "blocked"
            progress.error = "Проверки готовности не пройдены"
            return
        self._finish(progress, "checks", f"пройдено {checks_summary(checks)['passed']}")

        self._begin(progress, "backup")
        record = backup_module.create_backup(
            commit=plan["from_commit"],
            project_version=plan["from_version"],
            db=self._db,
            project_root=self._root,
        )
        progress.backup_id = record.id
        self._finish(
            progress,
            "backup",
            f"{record.id} · {record.size_bytes // 1024} КБ",
        )

        self._begin(progress, "fetch")
        upstream = plan.get("upstream") or self._git.upstream() or ""
        remote, _, branch = upstream.partition("/")
        self._git.fetch(remote, branch)
        target = self._git.resolve(upstream)
        if target != plan["to_commit"]:
            # Источник ушёл вперёд между проверкой и установкой.
            self._fail(progress, "fetch", "источник изменился после проверки")
            progress.status = "blocked"
            progress.error = "Источник изменился: выполните проверку заново"
            return
        self._finish(progress, "fetch", f"{target[:12]}")

        self._begin(progress, "apply")
        try:
            self._git.merge_fast_forward(plan["to_commit"])
        except GitError as exc:
            self._fail(progress, "apply", str(exc))
            progress.status = "failed"
            progress.error = str(exc)
            return
        self._finish(progress, "apply", f"{plan['from_commit'][:12]} → {plan['to_commit'][:12]}")

        self._begin(progress, "verify")
        ok, detail = self._verify()
        if not ok:
            self._fail(progress, "verify", detail)
            self._rollback(progress, plan)
            return
        self._finish(progress, "verify", detail)

        self._begin(progress, "done")
        progress.status = "completed"
        progress.restart_required = True
        progress.message = (
            f"Установлена версия {progress.to_version}. "
            "Перезапустите систему, чтобы изменения вступили в силу."
        )
        self._finish(progress, "done", progress.message)

    def _rollback(self, progress: UpdateProgress, plan: dict[str, Any]) -> None:
        """Вернуть проект в состояние до обновления."""

        details: list[str] = []
        try:
            self._git.hard_reset_to(plan["from_commit"], expected_head=plan["to_commit"])
            details.append("файлы возвращены")
        except GitError as exc:
            progress.status = "failed"
            progress.error = (
                f"Проверка не пройдена, и откат не удался: {exc}. "
                f"Резервная копия: {progress.backup_id}"
            )
            return

        if progress.backup_id:
            try:
                restored = backup_module.restore_backup(
                    progress.backup_id,
                    db=self._db,
                    project_root=self._root,
                )
                if restored["database_restored"]:
                    details.append("база восстановлена")
            except Exception as exc:
                details.append(f"база не восстановлена: {exc}")

        progress.rolled_back = True
        progress.status = "rolled_back"
        progress.error = progress.error or "Проверка после установки не пройдена"
        progress.message = "Обновление отменено, проект возвращён в рабочее состояние: " + ", ".join(details)
        self._publish("update.rolled_back", {"id": progress.id, "backup_id": progress.backup_id})

    # --- стадии ------------------------------------------------------

    def _stage(self, progress: UpdateProgress, key: str) -> Stage:
        for stage in progress.stages:
            if stage.key == key:
                return stage
        raise KeyError(key)

    def _begin(self, progress: UpdateProgress, key: str) -> None:
        stage = self._stage(progress, key)
        stage.state = "running"
        stage.started_at = _now()
        self._emit(progress, stage)

    def _finish(self, progress: UpdateProgress, key: str, detail: str = "") -> None:
        stage = self._stage(progress, key)
        stage.state = "done"
        stage.detail = detail
        stage.finished_at = _now()
        self._emit(progress, stage)

    def _fail(self, progress: UpdateProgress, key: str, detail: str) -> None:
        stage = self._stage(progress, key)
        stage.state = "failed"
        stage.detail = detail
        stage.finished_at = _now()
        for other in progress.stages:
            if other.state == "pending":
                other.state = "skipped"
        self._emit(progress, stage)

    def _emit(self, progress: UpdateProgress, stage: Stage) -> None:
        self._publish(
            "update.progress",
            {
                "id": progress.id,
                "stage": stage.key,
                "state": stage.state,
                "detail": stage.detail,
                "percent": progress.percent,
            },
        )

    # --- проверка после установки ------------------------------------

    def _default_verify(self) -> tuple[bool, str]:
        """Запустить проверки нового кода отдельным процессом.

        Отдельный процесс обязателен: текущий уже импортировал старые
        модули, и проверять ими новый код бессмысленно.
        """

        steps = (
            ("протокол", [sys.executable, "scripts/validate_update_protocol.py"]),
            ("компиляция", [sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests"]),
            ("тесты", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
        )
        environment = {"PYTHONPATH": str(self._root / "src")}
        import os

        merged = {**os.environ, **environment}

        for title, command in steps:
            try:
                completed = subprocess.run(
                    command,
                    cwd=self._root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=VERIFY_TIMEOUT,
                    env=merged,
                )
            except subprocess.TimeoutExpired:
                return False, f"{title}: превышено время ожидания"
            except OSError as exc:
                return False, f"{title}: {exc}"
            if completed.returncode != 0:
                tail = (completed.stderr or completed.stdout).strip().splitlines()
                reason = tail[-1] if tail else f"код {completed.returncode}"
                return False, f"{title}: {reason}"
        return True, "протокол, компиляция и тесты пройдены"
