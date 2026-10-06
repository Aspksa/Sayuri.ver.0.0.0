"""Диспетчер фоновых задач ядра."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from ..database import CoreDatabase
from .service import ManagedService

TERMINAL_STATUSES = frozenset({"completed", "failed"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class JobStatus:
    id: str
    name: str
    status: str
    created_at: str
    updated_at: str
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobManager(ManagedService):
    name = "job_manager"

    def __init__(
        self,
        db: CoreDatabase,
        *,
        max_workers: int = 4,
        max_history: int = 500,
    ) -> None:
        super().__init__()
        self._db = db
        self._max_workers = max(1, int(max_workers))
        self._max_history = max(1, int(max_history))
        self._executor: ThreadPoolExecutor | None = None
        self._jobs: dict[str, JobStatus] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._jobs_lock = RLock()

    def on_start(self) -> None:
        # Задачи, не дожившие до прошлого завершения, честно помечаются.
        self._db.mark_unfinished_jobs_interrupted()
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="sayuri-core",
        )

    def on_stop(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        with self._jobs_lock:
            self._futures.clear()
            self._prune_locked()

    def submit(
        self,
        name: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        executor = self._executor
        if executor is None:
            raise RuntimeError("Job manager is not running")

        job_id = str(uuid4())
        now = _utc_now()
        with self._jobs_lock:
            self._jobs[job_id] = JobStatus(job_id, name, "pending", now, now)
        self._db.create_core_job(job_id, name, "pending")

        future = executor.submit(self._run_job, job_id, func, args, kwargs)
        with self._jobs_lock:
            self._futures[job_id] = future
        future.add_done_callback(lambda _f, jid=job_id: self._on_future_done(jid))
        return job_id

    def wait(self, job_id: str, timeout: float | None = None) -> Any:
        with self._jobs_lock:
            future = self._futures.get(job_id)
        if future is not None:
            return future.result(timeout=timeout)

        with self._jobs_lock:
            item = self._jobs.get(job_id)
        if item is None:
            raise KeyError(job_id)
        if item.status == "completed":
            return item.result
        if item.status == "failed":
            raise RuntimeError(item.error or f"Job failed: {job_id}")
        raise RuntimeError(f"Job is not waitable: {job_id} ({item.status})")

    def get(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            return self._jobs[job_id].to_dict()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._jobs_lock:
            return [item.to_dict() for item in self._jobs.values()]

    def _run_job(
        self,
        job_id: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        self._update(job_id, "running")
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self._update(job_id, "failed", error=str(exc))
            raise
        self._update(job_id, "completed", result=result)
        return result

    def _update(
        self,
        job_id: str,
        status: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        with self._jobs_lock:
            item = self._jobs.get(job_id)
            if item is not None:
                item.status = status
                item.updated_at = _utc_now()
                item.result = result
                item.error = error
        self._db.update_core_job(job_id, status, result=result, error=error)

    def _on_future_done(self, job_id: str) -> None:
        # Future держит результат в памяти — освобождаем сразу после завершения.
        with self._jobs_lock:
            self._futures.pop(job_id, None)
            self._prune_locked()

    def _prune_locked(self) -> None:
        if len(self._jobs) <= self._max_history:
            return
        terminal = [
            job_id
            for job_id, item in self._jobs.items()
            if item.status in TERMINAL_STATUSES
        ]
        while len(self._jobs) > self._max_history and terminal:
            self._jobs.pop(terminal.pop(0), None)
