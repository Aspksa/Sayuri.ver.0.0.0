"""Безопасная обёртка над git.

Разрешены только чтение и fast-forward слияние. Ни reset, ни force,
ни checkout с перезаписью: обновление не вправе уничтожать работу,
которая есть в рабочем каталоге. Единственное исключение — явный откат
к коммиту, который этот же слой зафиксировал перед обновлением.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 60
FETCH_TIMEOUT = 180


class GitError(RuntimeError):
    """Git недоступен или вернул ошибку."""


@dataclass(frozen=True)
class GitResult:
    args: tuple[str, ...]
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return self.stdout.strip()


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    short: str
    author: str
    date: str
    subject: str

    def to_dict(self) -> dict[str, str]:
        return {
            "sha": self.sha,
            "short": self.short,
            "author": self.author,
            "date": self.date,
            "subject": self.subject,
        }


class GitClient:
    def __init__(self, root: Path, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.root = root
        self.timeout = timeout

    # --- низкий уровень ---------------------------------------------

    def available(self) -> bool:
        return shutil.which("git") is not None

    def run(self, *args: str, timeout: int | None = None) -> GitResult:
        if not self.available():
            raise GitError("Git не найден в системе")
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {' '.join(args)}: превышено время ожидания") from exc
        except OSError as exc:
            raise GitError(f"git {' '.join(args)}: {exc}") from exc
        return GitResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)

    def require(self, *args: str, timeout: int | None = None) -> str:
        result = self.run(*args, timeout=timeout)
        if not result.ok:
            detail = result.stderr.strip() or result.stdout.strip() or f"код {result.code}"
            raise GitError(f"git {' '.join(args)}: {detail}")
        return result.text

    # --- чтение состояния -------------------------------------------

    def is_worktree(self) -> bool:
        result = self.run("rev-parse", "--is-inside-work-tree")
        return result.ok and result.text == "true"

    def head(self) -> str:
        return self.require("rev-parse", "HEAD")

    def current_branch(self) -> str | None:
        result = self.run("symbolic-ref", "--quiet", "--short", "HEAD")
        return result.text if result.ok and result.text else None

    def upstream(self) -> str | None:
        """Настроенный upstream текущей ветки.

        Жёсткий origin/main был бы ошибкой: пользователь может работать на
        другой ветке, и обновление увело бы его в чужую историю.
        """

        result = self.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        return result.text if result.ok and result.text else None

    def remote_url(self, remote: str = "origin") -> str | None:
        result = self.run("remote", "get-url", remote)
        return result.text if result.ok and result.text else None

    def dirty_files(self) -> list[str]:
        output = self.require("status", "--porcelain")
        return [line.strip() for line in output.splitlines() if line.strip()]

    def is_clean(self) -> bool:
        return not self.dirty_files()

    def fetch(self, remote: str, branch: str) -> None:
        self.require("fetch", "--quiet", remote, branch, timeout=FETCH_TIMEOUT)

    def resolve(self, ref: str) -> str:
        return self.require("rev-parse", ref)

    def is_ancestor(self, candidate: str, descendant: str) -> bool:
        return self.run("merge-base", "--is-ancestor", candidate, descendant).ok

    def commits_between(self, base: str, target: str, limit: int = 200) -> list[CommitInfo]:
        if base == target:
            return []
        separator = "\x1f"
        output = self.require(
            "log",
            f"--max-count={max(1, int(limit))}",
            f"--pretty=format:%H{separator}%h{separator}%an{separator}%aI{separator}%s",
            f"{base}..{target}",
        )
        commits: list[CommitInfo] = []
        for line in output.splitlines():
            parts = line.split(separator)
            if len(parts) != 5:
                continue
            commits.append(CommitInfo(*parts))
        return commits

    def changed_files(self, base: str, target: str) -> list[dict[str, str]]:
        if base == target:
            return []
        output = self.require("diff", "--name-status", f"{base}..{target}")
        statuses = {"A": "добавлен", "M": "изменён", "D": "удалён", "R": "переименован"}
        files: list[dict[str, str]] = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            code = parts[0][:1]
            files.append(
                {
                    "path": parts[-1],
                    "status": code,
                    "label": statuses.get(code, code),
                }
            )
        return files

    def file_at(self, ref: str, path: str) -> str | None:
        result = self.run("show", f"{ref}:{path}")
        return result.stdout if result.ok else None

    def list_files(self, ref: str, prefix: str = "") -> list[str]:
        args = ["ls-tree", "-r", "--name-only", ref]
        if prefix:
            args.extend(["--", prefix])
        output = self.require(*args)
        return [line.strip() for line in output.splitlines() if line.strip()]

    # --- изменяющие операции ----------------------------------------

    def merge_fast_forward(self, ref: str) -> None:
        """Только fast-forward: своя история пользователя не переписывается."""

        self.require("merge", "--ff-only", "--quiet", ref)

    def hard_reset_to(self, sha: str, *, expected_head: str) -> None:
        """Откат к зафиксированному состоянию.

        Разрешён единственный сценарий: сразу после неудачного обновления,
        когда HEAD — это именно тот коммит, в который обновление привело.
        Любое расхождение означает, что состояние уже изменилось, и тогда
        автоматический откат опаснее остановки.
        """

        current = self.head()
        if current != expected_head:
            raise GitError(
                "Откат отменён: HEAD изменился после обновления "
                f"({current[:12]} вместо {expected_head[:12]})"
            )
        if not self.is_ancestor(sha, expected_head):
            raise GitError(
                f"Откат отменён: {sha[:12]} не является предком {expected_head[:12]}"
            )
        self.require("reset", "--hard", sha)
