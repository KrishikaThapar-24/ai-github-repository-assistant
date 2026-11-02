"""Small in-process job runner for local development."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar, cast

T = TypeVar("T")
logger = logging.getLogger(__name__)


class JobQueueFullError(RuntimeError):
    """Raised when the local worker has no capacity for another job."""


@dataclass(frozen=True, slots=True)
class JobStatus(Generic[T]):
    job_id: str
    status: str
    created_at: str
    result: T | None = None
    error: str | None = None


class InMemoryJobRunner:
    """Runs bounded background tasks; replace with a durable queue for deployment."""

    def __init__(
        self,
        max_workers: int = 2,
        *,
        max_pending: int = 16,
        max_history: int = 500,
    ) -> None:
        if max_workers < 1 or max_pending < 1 or max_history < max_pending:
            raise ValueError("Worker and job history limits must be positive and consistent")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="assistant")
        self._lock = threading.Lock()
        self._jobs: dict[str, JobStatus[object]] = {}
        self._active_jobs: dict[Hashable, str] = {}
        self._max_pending = max_pending
        self._max_history = max_history

    def submit(
        self, task: Callable[[], T], *, dedupe_key: Hashable | None = None
    ) -> JobStatus[T]:
        with self._lock:
            if dedupe_key is not None:
                active_job_id = self._active_jobs.get(dedupe_key)
                active_job = self._jobs.get(active_job_id) if active_job_id else None
                if active_job is not None and active_job.status in {"queued", "running"}:
                    return cast(JobStatus[T], active_job)

            active_count = sum(
                job.status in {"queued", "running"} for job in self._jobs.values()
            )
            if active_count >= self._max_pending:
                raise JobQueueFullError(
                    "The indexing queue is full. Wait for an active job to finish, then retry."
                )

            self._prune_history()
            job_id = str(uuid.uuid4())
            created_at = datetime.now(UTC).isoformat()
            queued: JobStatus[T] = JobStatus(job_id, "queued", created_at)
            self._jobs[job_id] = queued  # type: ignore[assignment]
            if dedupe_key is not None:
                self._active_jobs[dedupe_key] = job_id
            self._executor.submit(self._run, job_id, created_at, task, dedupe_key)
        return queued

    def _prune_history(self) -> None:
        """Drop oldest completed statuses so local polling history stays bounded."""

        for job_id, job in tuple(self._jobs.items()):
            if len(self._jobs) < self._max_history:
                break
            if job.status in {"succeeded", "failed"}:
                del self._jobs[job_id]

    def _run(
        self,
        job_id: str,
        created_at: str,
        task: Callable[[], T],
        dedupe_key: Hashable | None,
    ) -> None:
        self._set_status(JobStatus(job_id, "running", created_at))
        try:
            result = task()
        except Exception as exc:
            logger.exception("Background job %s failed", job_id)
            message = (
                str(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "The job failed. Check the API logs."
            )
            self._set_status(JobStatus(job_id, "failed", created_at, error=message))
        else:
            self._set_status(JobStatus(job_id, "succeeded", created_at, result=result))
        finally:
            if dedupe_key is not None:
                with self._lock:
                    if self._active_jobs.get(dedupe_key) == job_id:
                        del self._active_jobs[dedupe_key]

    def _set_status(self, status: JobStatus[object]) -> None:
        with self._lock:
            self._jobs[status.job_id] = status

    def get(self, job_id: str) -> JobStatus[object] | None:
        with self._lock:
            return self._jobs.get(job_id)
