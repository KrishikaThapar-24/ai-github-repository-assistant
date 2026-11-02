"""Small in-process job runner for local development."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobStatus(Generic[T]):
    job_id: str
    status: str
    created_at: str
    result: T | None = None
    error: str | None = None


class InMemoryJobRunner:
    """Runs bounded background tasks; replace with a durable queue for deployment."""

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="assistant")
        self._lock = threading.Lock()
        self._jobs: dict[str, JobStatus[object]] = {}
        self._futures: dict[str, Future[object]] = {}

    def submit(self, task: Callable[[], T]) -> JobStatus[T]:
        job_id = str(uuid.uuid4())
        created_at = datetime.now(UTC).isoformat()
        queued: JobStatus[T] = JobStatus(job_id, "queued", created_at)
        with self._lock:
            self._jobs[job_id] = queued  # type: ignore[assignment]
        future = self._executor.submit(self._run, job_id, created_at, task)
        with self._lock:
            self._futures[job_id] = future
        return queued

    def _run(self, job_id: str, created_at: str, task: Callable[[], T]) -> None:
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

    def _set_status(self, status: JobStatus[object]) -> None:
        with self._lock:
            self._jobs[status.job_id] = status

    def get(self, job_id: str) -> JobStatus[object] | None:
        with self._lock:
            return self._jobs.get(job_id)
