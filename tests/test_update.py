"""Проверки слоя обновления.

Цикл обновления проверяется на настоящем локальном git-репозитории без
сети: создаётся исходный репозиторий, из него клон, в исходный вносится
коммит — клон оказывается позади и обновляется. Отдельно проверяется
главное обещание: при провале проверки после установки проект
возвращается в рабочее состояние.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sayuri_yukishiro.database import CoreDatabase, SCHEMA_VERSION
from sayuri_yukishiro.update import backup as backup_module
from sayuri_yukishiro.update.changelog import parse_changelog
from sayuri_yukishiro.update.checks import blocking_failures, run_update_checks
from sayuri_yukishiro.update.git_client import GitClient, GitError, GitResult
from sayuri_yukishiro.update.inventory import collect_components, diff_components
from sayuri_yukishiro.update.service import STAGE_SEQUENCE, UpdateService

CHANGELOG = """# Журнал

---

## v0.1.0

Дата: 2026-01-01

Статус: completed

Изменения:

- CHG-0001 / ARCH-0001 — заложена архитектура.
- CHG-0002 / FEAT-0001 — добавлено ядро.
- CHG-0003 / BUG-0001 — обнаружен дефект запуска.
- CHG-0004 / FIX-0001 -> BUG-0001 — дефект запуска исправлен.

Проверки:

- tests: PASS (10 тестов)
- lint: NOT_CONFIGURED
- smoke-test: FAIL

Commit:

1111111111111111111111111111111111111111

Следующий шаг:

next_action: сделать v0.2.0.
"""


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_repository(root: Path) -> tuple[Path, Path]:
    """Исходный репозиторий и клон, отстающий от него."""

    origin = root / "origin"
    origin.mkdir()
    git(origin, "init", "--quiet", "--bare", "--initial-branch=main")

    seed = root / "seed"
    seed.mkdir()
    git(seed, "clone", "--quiet", str(origin), ".")
    git(seed, "config", "user.email", "test@example.invalid")
    git(seed, "config", "user.name", "Test")
    (seed / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (seed / "payload.txt").write_text("первая версия\n", encoding="utf-8")
    # Как в настоящем проекте: runtime-данные не отслеживаются, иначе
    # проверка «нет своих правок» срабатывала бы на собственной базе.
    (seed / ".gitignore").write_text("data/\ncore.db\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "--quiet", "-m", "init")
    git(seed, "push", "--quiet", "origin", "main")

    clone = root / "clone"
    clone.mkdir()
    git(clone, "clone", "--quiet", str(origin), ".")
    git(clone, "config", "user.email", "test@example.invalid")
    git(clone, "config", "user.name", "Test")
    return seed, clone


def publish_update(seed: Path, version: str = "1.1.0") -> str:
    (seed / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (seed / "payload.txt").write_text("вторая версия\n", encoding="utf-8")
    (seed / "added.txt").write_text("новый файл\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "--quiet", "-m", f"release {version}")
    git(seed, "push", "--quiet", "origin", "main")
    return git(seed, "rev-parse", "HEAD")


class GitClientTests(unittest.TestCase):
    def test_reads_state_of_a_real_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _seed, clone = make_repository(Path(tmp))
            client = GitClient(clone)
            self.assertTrue(client.available())
            self.assertTrue(client.is_worktree())
            self.assertEqual(client.current_branch(), "main")
            self.assertEqual(client.upstream(), "origin/main")
            self.assertTrue(client.is_clean())
            self.assertEqual(len(client.head()), 40)

    def test_detects_local_modifications(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _seed, clone = make_repository(Path(tmp))
            (clone / "payload.txt").write_text("моя правка\n", encoding="utf-8")
            client = GitClient(clone)
            self.assertFalse(client.is_clean())
            self.assertEqual(len(client.dirty_files()), 1)

    def test_commits_and_files_between_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed, clone = make_repository(Path(tmp))
            target = publish_update(seed)
            client = GitClient(clone)
            client.fetch("origin", "main")
            base = client.head()

            commits = client.commits_between(base, target)
            self.assertEqual(len(commits), 1)
            self.assertEqual(commits[0].subject, "release 1.1.0")

            files = client.changed_files(base, target)
            paths = {item["path"]: item["status"] for item in files}
            self.assertEqual(paths["added.txt"], "A")
            self.assertEqual(paths["VERSION"], "M")

    def test_fast_forward_only_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed, clone = make_repository(Path(tmp))
            target = publish_update(seed)
            client = GitClient(clone)
            client.fetch("origin", "main")
            client.merge_fast_forward(target)
            self.assertEqual(client.head(), target)
            self.assertEqual((clone / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")

    def test_diverged_history_is_not_fast_forwardable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed, clone = make_repository(Path(tmp))
            target = publish_update(seed)
            # Свой коммит в клоне: история разошлась.
            (clone / "local.txt").write_text("моё\n", encoding="utf-8")
            git(clone, "add", "-A")
            git(clone, "commit", "--quiet", "-m", "local work")

            client = GitClient(clone)
            client.fetch("origin", "main")
            self.assertFalse(client.is_ancestor(client.head(), target))
            with self.assertRaises(GitError):
                client.merge_fast_forward(target)

    def test_rollback_refuses_when_head_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seed, clone = make_repository(Path(tmp))
            target = publish_update(seed)
            client = GitClient(clone)
            base = client.head()
            client.fetch("origin", "main")
            client.merge_fast_forward(target)
            with self.assertRaises(GitError):
                client.hard_reset_to(base, expected_head="0" * 40)

    def test_read_failures_are_fail_closed(self) -> None:
        client = GitClient(Path("."))
        failed = GitResult(("status", "--porcelain"), 128, "", "fatal: repository error")
        with patch.object(client, "run", return_value=failed):
            with self.assertRaises(GitError):
                client.is_clean()
            with self.assertRaises(GitError):
                client.commits_between("a" * 40, "b" * 40)
            with self.assertRaises(GitError):
                client.changed_files("a" * 40, "b" * 40)

    def test_missing_git_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = GitClient(Path(tmp))
            with patch("sayuri_yukishiro.update.git_client.shutil.which", return_value=None):
                self.assertFalse(client.available())
                with self.assertRaises(GitError):
                    client.run("status")


class ChangelogTests(unittest.TestCase):
    def test_parses_versions_changes_and_checks(self) -> None:
        entries = parse_changelog(CHANGELOG)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.version, "0.1.0")
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.date, "2026-01-01")
        self.assertEqual(len(entry.changes), 4)
        self.assertEqual(entry.commits, ["1" * 40])
        self.assertEqual(entry.next_action, "сделать v0.2.0.")

    def test_fix_keeps_its_bug_reference(self) -> None:
        entry = parse_changelog(CHANGELOG)[0]
        fix = next(change for change in entry.changes if change.kind == "FIX")
        self.assertEqual(fix.bug_ref, "BUG-0001")

    def test_checks_separate_status_from_note(self) -> None:
        entry = parse_changelog(CHANGELOG)[0]
        payload = entry.to_dict()
        self.assertEqual(payload["checks_passed"], 1)
        self.assertEqual(payload["checks_failed"], 1)
        tests_check = next(item for item in payload["checks"] if item["name"] == "tests")
        self.assertEqual(tests_check["result"], "PASS")
        self.assertEqual(tests_check["note"], "10 тестов")

    def test_changes_are_grouped_by_kind(self) -> None:
        payload = parse_changelog(CHANGELOG)[0].to_dict()
        kinds = [group["kind"] for group in payload["groups"]]
        self.assertEqual(kinds, ["FEAT", "FIX", "ARCH", "BUG"])

    def test_real_project_changelog_parses(self) -> None:
        from sayuri_yukishiro.paths import PROJECT_ROOT

        entries = parse_changelog((PROJECT_ROOT / "UPDATE_LOG.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(entries), 3)
        # Свежие версии первыми: интерфейс показывает историю сверху вниз.
        self.assertGreater(entries[0].version, entries[-1].version)


class InventoryTests(unittest.TestCase):
    def test_components_cover_everything_that_updates(self) -> None:
        keys = {item.key for item in collect_components()}
        self.assertEqual(
            keys,
            {"project", "core", "database", "protocol", "web", "launcher", "tray", "entry"},
        )

    def test_schema_component_matches_database(self) -> None:
        schema = next(item for item in collect_components() if item.key == "database")
        self.assertEqual(schema.version, f"v{SCHEMA_VERSION}")

    def test_diff_marks_only_changed_components(self) -> None:
        before = collect_components()
        after = [
            item if item.key != "project" else type(item)(
                key=item.key, title=item.title, version="9.9.9",
                kind=item.kind, source=item.source, detail=item.detail,
            )
            for item in before
        ]
        rows = diff_components(before, after)
        changed = [row for row in rows if row["changed"]]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["key"], "project")
        self.assertEqual(changed[0]["after"], "9.9.9")


class UpdateChecksTests(unittest.TestCase):
    def test_clean_clone_passes_every_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _seed, clone = make_repository(Path(tmp))
            checks = run_update_checks(GitClient(clone), data_dir=clone / "data")
            self.assertEqual(blocking_failures(checks), [])

    def test_local_changes_block_the_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _seed, clone = make_repository(Path(tmp))
            (clone / "payload.txt").write_text("моя правка\n", encoding="utf-8")
            checks = run_update_checks(GitClient(clone), data_dir=clone / "data")
            failed = blocking_failures(checks)
            self.assertEqual([check.name for check in failed], ["clean"])
            self.assertTrue(failed[0].hint, "отказ обязан объяснять, что делать")

    def test_plain_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Скачанный ZIP — не рабочая копия, обновлять нечем.
            checks = run_update_checks(GitClient(Path(tmp)), data_dir=Path(tmp) / "data")
            self.assertIn("worktree", [check.name for check in blocking_failures(checks)])


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        patcher = patch.object(backup_module, "DATA_DIR", self.root / "data")
        patcher.start()
        self.addCleanup(patcher.stop)
        ensure = patch.object(backup_module, "ensure_runtime_dirs", lambda: None)
        ensure.start()
        self.addCleanup(ensure.stop)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

    def test_backup_captures_database_and_state_files(self) -> None:
        (self.root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (self.root / "PROJECT_STATE.json").write_text("{}", encoding="utf-8")
        db = CoreDatabase(self.root / "core.db")
        db.initialize()

        record = backup_module.create_backup(
            commit="a" * 40, project_version="1.0.0", db=db, project_root=self.root
        )
        stored = Path(record.path)
        self.assertTrue((stored / "VERSION").is_file())
        self.assertTrue((stored / "core.db").is_file())
        self.assertEqual(record.database, "core.db")
        self.assertGreater(record.size_bytes, 0)

    def test_restore_returns_previous_state(self) -> None:
        (self.root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        db = CoreDatabase(self.root / "core.db")
        db.initialize()
        record = backup_module.create_backup(
            commit="a" * 40, project_version="1.0.0", db=db, project_root=self.root
        )

        (self.root / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        result = backup_module.restore_backup(record.id, db=db, project_root=self.root)

        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")
        self.assertTrue(result["database_restored"])

    def test_old_backups_are_pruned(self) -> None:
        db = CoreDatabase(self.root / "core.db")
        db.initialize()
        for index in range(8):
            backup_module.create_backup(
                commit=f"{index:040d}", project_version="1.0.0", db=db, project_root=self.root
            )
        self.assertLessEqual(len(backup_module.list_backups()), backup_module.KEEP_BACKUPS)


class UpdateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.seed, self.clone = make_repository(self.root)
        self.db = CoreDatabase(self.clone / "core.db")
        self.db.initialize()
        self.events: list[tuple[str, dict]] = []

        patcher = patch.object(backup_module, "DATA_DIR", self.clone / "data")
        patcher.start()
        self.addCleanup(patcher.stop)
        ensure = patch.object(backup_module, "ensure_runtime_dirs", lambda: None)
        ensure.start()
        self.addCleanup(ensure.stop)
        (self.clone / "data").mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

    def make_service(self, verify=None) -> UpdateService:
        return UpdateService(
            self.db,
            root=self.clone,
            publish=lambda event_type, payload: self.events.append((event_type, payload)),
            verify=verify or (lambda: (True, "проверено")),
        )

    def test_check_reports_no_update_when_current(self) -> None:
        service = self.make_service()
        plan = service.check()
        self.assertTrue(plan["ready"])
        self.assertFalse(plan["update_available"])
        self.assertEqual(plan["reason"], "up_to_date")

    def test_check_builds_a_plan_with_before_and_after(self) -> None:
        target = publish_update(self.seed)
        service = self.make_service()
        plan = service.check()

        self.assertTrue(plan["update_available"])
        self.assertEqual(plan["to_commit"], target)
        self.assertEqual(len(plan["commits"]), 1)
        self.assertTrue(plan["files"])
        project_row = next(row for row in plan["components"] if row["key"] == "project")
        self.assertTrue(project_row["changed"])
        self.assertEqual(project_row["after"], "1.1.0")

    def test_diverged_history_refuses_automatic_update(self) -> None:
        publish_update(self.seed)
        (self.clone / "local.txt").write_text("моё\n", encoding="utf-8")
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "--quiet", "-m", "local work")

        plan = self.make_service().check()
        self.assertFalse(plan["update_available"])
        self.assertEqual(plan["reason"], "not_fast_forward")

    def test_local_changes_block_the_plan(self) -> None:
        publish_update(self.seed)
        (self.clone / "payload.txt").write_text("моя правка\n", encoding="utf-8")
        plan = self.make_service().check()
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["reason"], "blocking_checks")

    def test_apply_updates_files_and_records_the_run(self) -> None:
        target = publish_update(self.seed)
        service = self.make_service()
        service.check()
        result = service.apply()

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["restart_required"])
        self.assertEqual(result["percent"], 100)
        self.assertEqual(GitClient(self.clone).head(), target)
        self.assertEqual((self.clone / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")
        self.assertTrue((self.clone / "added.txt").is_file())

        stages = {stage["key"]: stage["state"] for stage in result["stages"]}
        self.assertEqual(stages, {key: "done" for key, _title in STAGE_SEQUENCE})

        runs = self.db.list_update_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["to_version"], "1.1.0")
        self.assertTrue(runs[0]["backup_id"])

    def test_failed_verification_rolls_the_project_back(self) -> None:
        publish_update(self.seed)
        before_commit = GitClient(self.clone).head()
        service = self.make_service(verify=lambda: (False, "тесты не прошли"))
        service.check()
        result = service.apply()

        self.assertEqual(result["status"], "rolled_back")
        self.assertTrue(result["rolled_back"])
        # Главное обещание: проект вернулся в рабочее состояние.
        self.assertEqual(GitClient(self.clone).head(), before_commit)
        self.assertEqual((self.clone / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")
        self.assertFalse((self.clone / "added.txt").exists())

        stages = {stage["key"]: stage["state"] for stage in result["stages"]}
        self.assertEqual(stages["verify"], "failed")
        self.assertEqual(self.db.list_update_runs()[0]["status"], "rolled_back")

    def test_apply_without_a_plan_is_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            self.make_service().apply()

    def test_stale_plan_is_refused(self) -> None:
        publish_update(self.seed)
        service = self.make_service()
        service.check()
        with self.assertRaises(RuntimeError):
            service.ensure_can_apply("не-тот-план")

    def test_source_moving_after_check_blocks_installation(self) -> None:
        publish_update(self.seed, "1.1.0")
        service = self.make_service()
        service.check()
        # Источник ушёл вперёд уже после проверки.
        publish_update(self.seed, "1.2.0")
        result = service.apply()

        self.assertEqual(result["status"], "blocked")
        self.assertIn("Источник изменился", result["error"])
        # Ничего не установлено: версия осталась прежней.
        self.assertEqual((self.clone / "VERSION").read_text(encoding="utf-8").strip(), "1.0.0")

    def test_progress_publishes_stage_events(self) -> None:
        publish_update(self.seed)
        service = self.make_service()
        service.check()
        service.apply()

        kinds = [name for name, _payload in self.events]
        self.assertIn("update.checked", kinds)
        self.assertIn("update.started", kinds)
        self.assertIn("update.progress", kinds)
        self.assertIn("update.finished", kinds)
        percents = [
            payload["percent"] for name, payload in self.events if name == "update.progress"
        ]
        self.assertEqual(percents, sorted(percents), "прогресс не должен идти назад")

    def test_status_exposes_components_and_repository(self) -> None:
        status = self.make_service().status()
        self.assertEqual(len(status["components"]), 8)
        self.assertTrue(status["repository"]["worktree"])
        self.assertEqual(status["repository"]["upstream"], "origin/main")
        self.assertFalse(status["busy"])

    def test_history_merges_changelog_and_runs(self) -> None:
        publish_update(self.seed)
        service = self.make_service()
        service.check()
        service.apply()
        history = service.history()
        self.assertIn("versions", history)
        self.assertEqual(len(history["runs"]), 1)


if __name__ == "__main__":
    unittest.main()
