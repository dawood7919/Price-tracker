"""Concurrency management and job state tracking (MVP: in-memory, single instance).

Per user's explicit request, there is no daily quota and no per-user
concurrency cap anymore — a user can run as many downloads in parallel
as they want. The only remaining limit is the global semaphore, which
exists purely so the server itself doesn't fall over from unbounded
simultaneous yt-dlp/ffmpeg processes; it is deliberately set high
(``max_concurrent_downloads``) rather than removed, since removing it
entirely would let the process run out of RAM/disk/CPU and crash for
everyone, not just the user who triggered it.

For a multi-instance deployment this semaphore must move to Redis
(design doc §5 — it does not shard across instances).
"""

from __future__ import annotations

import asyncio
import threading


class JobState:
    """Explicit lifecycle states for a job (design doc: queue visibility)."""

    WAITING = "waiting"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class DownloadManager:
    """Owns the global concurrency semaphore and per-job tracking/cancellation."""

    def __init__(self, config) -> None:
        self._config = config
        self._global_sem = asyncio.Semaphore(config.max_concurrent_downloads)
        self._active_per_user: dict[int, int] = {}
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._states: dict[str, str] = {}
        # A threading.Event (not asyncio.Event): the actual download/split
        # work runs in a worker thread via asyncio.to_thread, and cancelling
        # the asyncio task does NOT stop that thread — only this flag,
        # checked from inside the blocking call, can (verified: a
        # to_thread-wrapped call keeps running to completion even after its
        # awaiting task is cancelled).
        self._cancel_events: dict[str, threading.Event] = {}

    # ---- slots (global cap only) ----

    async def acquire_slot(self, user_id: int) -> None:
        """Reserve a global download slot. Never rejects on a per-user basis."""
        await self._global_sem.acquire()
        async with self._lock:
            self._active_per_user[user_id] = self._active_per_user.get(user_id, 0) + 1

    async def release_slot(self, user_id: int) -> None:
        self._global_sem.release()
        async with self._lock:
            count = self._active_per_user.get(user_id, 1) - 1
            if count <= 0:
                self._active_per_user.pop(user_id, None)
            else:
                self._active_per_user[user_id] = count

    def active_for_user(self, user_id: int) -> int:
        return self._active_per_user.get(user_id, 0)

    # ---- job lifecycle ----

    def start_job(self, job_id: str, task: asyncio.Task) -> threading.Event:
        """Register a new job and return its cancellation flag."""
        self._tasks[job_id] = task
        self._states[job_id] = JobState.WAITING
        event = threading.Event()
        self._cancel_events[job_id] = event
        task.add_done_callback(lambda _t: self._forget(job_id))
        return event

    def _forget(self, job_id: str) -> None:
        self._tasks.pop(job_id, None)
        self._cancel_events.pop(job_id, None)
        # keep the final state (completed/cancelled/failed) around briefly
        # would need a TTL/eviction for long-running deployments; fine for MVP

    def set_state(self, job_id: str, state: str) -> None:
        self._states[job_id] = state

    def get_state(self, job_id: str) -> str | None:
        return self._states.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. Sets the flag the worker thread checks AND
        cancels the asyncio task, so both the async and blocking-thread sides
        stop instead of the thread silently running to completion."""
        event = self._cancel_events.get(job_id)
        task = self._tasks.get(job_id)
        if event is None and (task is None or task.done()):
            return False
        if event is not None:
            event.set()
        if task and not task.done():
            task.cancel()
        return True

    def cancel_all(self) -> int:
        """Cancel every currently active job. Returns how many were cancelled."""
        count = 0
        for job_id in list(self._tasks):
            if self.cancel(job_id):
                count += 1
        return count

    def active_jobs(self) -> int:
        return len(self._tasks)

    def active_by_state(self) -> dict[str, int]:
        """Count currently-active jobs (not historical ones) per state."""
        counts: dict[str, int] = {}
        for job_id in self._tasks:
            state = self._states.get(job_id, JobState.WAITING)
            counts[state] = counts.get(state, 0) + 1
        return counts
