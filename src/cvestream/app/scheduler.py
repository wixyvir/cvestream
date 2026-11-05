"""Source loops, the pipeline worker and the supervisor that restarts a loop.

One loop per enabled source fetches on its own interval. Every fetch outcome,
failures included, goes onto one queue consumed by one serialized worker that
runs `pipeline.run.cycle` in a thread; the loop waits for the run that carries
its outcome before it sleeps. A source cursor is therefore always read from the
state before a fetch and never kept in memory: when a run is rolled back, the
next fetch simply starts from the stored cursor again.

All waits and randomness are injected, so tests never sleep for real.
"""

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import structlog

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.app.private_api import RuntimeStatus, SourceActivity, SourceStatus
from cvestream.clock import Clock
from cvestream.config.provider import ConfigProvider, ConfigSnapshot
from cvestream.pipeline.run import CycleResult, SourceOutcome, cycle, load_cursors
from cvestream.sources.base import SourceAdapter, SourceError
from cvestream.sources.registry import ScheduledSource
from cvestream.state.db import State

# Up to +10 % of the interval, so sources do not all fire together.
JITTER = 0.1
CRASH_BACKOFF_BASE = 1.0
# The floor of the cap: a source polled more often than this still waits it.
CRASH_BACKOFF_CAP = 300.0

type Sleep = Callable[[float], Awaitable[None]]
# Every N-th pipeline run reloads the configuration even when its revision did
# not change: the safety net for a writer that forgot to bump the revision.
FULL_RELOAD_EVERY = 12


class RunCycle(Protocol):
    def __call__(
        self,
        state: State,
        provider: ConfigProvider,
        snapshot: ConfigSnapshot,
        outcomes: list[SourceOutcome],
        clock: Clock,
        *,
        force_reload: bool = False,
    ) -> CycleResult: ...


log = structlog.get_logger()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class _Job:
    outcome: SourceOutcome
    done: asyncio.Future[bool] = field(default_factory=asyncio.Future)

    def resolve(self, committed: bool) -> None:
        # A source loop cancelled while it waited leaves a cancelled future.
        if not self.done.done():
            self.done.set_result(committed)


class PipelineWorker:
    """Runs one cycle at a time, never two, never cancelled mid-run."""

    def __init__(
        self,
        state: State,
        provider: ConfigProvider,
        snapshot: ConfigSnapshot,
        holder: FeedCacheHolder,
        clock: Clock,
        *,
        run_cycle: RunCycle = cycle,
    ) -> None:
        self._state = state
        self._provider = provider
        self._holder = holder
        self._clock = clock
        self._run_cycle = run_cycle
        self._queue: asyncio.Queue[_Job] = asyncio.Queue()
        self._closed = False
        self._runs = 0
        self.snapshot = snapshot

    async def submit(self, outcome: SourceOutcome) -> bool:
        """Queue `outcome`; wait for its run. True when the run committed."""
        if self._closed:
            return False
        job = _Job(outcome)
        self._queue.put_nowait(job)
        return await job.done

    async def _next_job(self, stop: asyncio.Event) -> _Job | None:
        if not self._queue.empty():
            return self._queue.get_nowait()
        getter = asyncio.ensure_future(self._queue.get())
        stopper = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({getter, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopper.cancel()
            if not getter.done():
                getter.cancel()
        if getter.done() and not getter.cancelled():
            return getter.result()
        return None

    async def work(self, stop: asyncio.Event) -> None:
        """Consume the queue until `stop` is set; `stop` is checked between runs.

        A run in its thread is never cancelled (the thread would go on and
        could commit after the state is closed): the caller awaits this task.
        """
        try:
            while not stop.is_set():
                first = await self._next_job(stop)
                if first is None:
                    break
                jobs = [first]
                while not self._queue.empty():
                    jobs.append(self._queue.get_nowait())
                await self._run(jobs)
        finally:
            self._closed = True
            while not self._queue.empty():
                self._queue.get_nowait().resolve(False)

    async def _run(self, jobs: list[_Job]) -> None:
        outcomes = [job.outcome for job in jobs]
        self._runs += 1
        # Never on the first run: the configuration was loaded just before it.
        force_reload = self._runs % FULL_RELOAD_EVERY == 0
        try:
            result = await asyncio.to_thread(
                functools.partial(
                    self._run_cycle,
                    self._state,
                    self._provider,
                    self.snapshot,
                    outcomes,
                    self._clock,
                    force_reload=force_reload,
                )
            )
            self.snapshot = result.snapshot
            self._holder.current = FeedCache.build(
                result.snapshot, result.run.feeds, self._clock.now()
            )
        except Exception as exc:
            log.error("pipeline.failed", error_type=type(exc).__name__, exc_info=True)
            committed = False
        else:
            committed = True
        for job in jobs:
            job.resolve(committed)


async def fetch_outcome(adapter: SourceAdapter, cursor: str | None) -> SourceOutcome:
    """Fetch once. A `SourceError` becomes a failed outcome, never an exception."""
    started = time.monotonic()
    try:
        result = await adapter.fetch(cursor)
    except SourceError as exc:
        log.warning(
            "source.failed",
            source=adapter.name,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return SourceOutcome(adapter.name, None, type(exc).__name__)
    log.info(
        "source.fetched",
        source=adapter.name,
        count=len(result.records),
        skipped=result.skipped,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    return SourceOutcome(adapter.name, result)


async def source_loop(
    scheduled: ScheduledSource,
    worker: PipelineWorker,
    state: State,
    status: RuntimeStatus,
    clock: Clock,
    *,
    random: Callable[[], float],
    sleep: Sleep,
    iteration_done: Callable[[], None],
) -> None:
    """Fetch, hand the outcome to the worker, sleep: forever."""
    name = scheduled.adapter.name
    interval = scheduled.interval
    source = status.sources.setdefault(name, SourceStatus(name, interval))
    while True:
        source.enter(SourceActivity.FETCHING, clock.now())
        # Always the stored cursor: a rolled-back run must not advance it.
        cursor = (await asyncio.to_thread(load_cursors, state)).get(name)
        source.cursor = cursor
        outcome = await fetch_outcome(scheduled.adapter, cursor)
        if outcome.result is None:
            source.last_error_type = outcome.error_type
            source.last_error_at = clock.now()
        source.enter(SourceActivity.PROCESSING, clock.now())
        committed = await worker.submit(outcome)
        if committed and outcome.result is not None:
            source.last_success_at = clock.now()
            if outcome.result.next_cursor is not None:
                source.cursor = outcome.result.next_cursor
        if not committed or outcome.result is None:
            _warn_if_stale(status, source, clock)
        delay = interval.total_seconds() * (1 + JITTER * random())
        now = clock.now()
        source.enter(SourceActivity.IDLE, now, now + timedelta(seconds=delay))
        await sleep(delay)
        iteration_done()


def _warn_if_stale(status: RuntimeStatus, source: SourceStatus, clock: Clock) -> None:
    if status.is_stale(source, clock.now()):
        log.warning(
            "source.stale",
            source=source.name,
            last_success_at=_iso(source.last_success_at),
        )


def crash_backoff_cap(interval: timedelta) -> float:
    """A crashing source is never restarted more often than a healthy one polls."""
    return max(CRASH_BACKOFF_CAP, interval.total_seconds())


def record_crash(
    status: RuntimeStatus, name: str, clock: Clock, error: Exception, retry_in: float
) -> None:
    """A crashed loop did not succeed either: record it, warn when stale.

    The supervisor calls it, so a source that keeps crashing still shows its
    error and its restart time (`retry_in` seconds from now) in `/status` and
    still logs `source.stale`.
    """
    source = status.sources.get(name)
    if source is None:
        return
    now = clock.now()
    source.last_error_type = type(error).__name__
    source.last_error_at = now
    source.enter(SourceActivity.CRASHED, now, now + timedelta(seconds=retry_in))
    _warn_if_stale(status, source, clock)


async def supervise(
    name: str,
    loop_factory: Callable[[Callable[[], None]], Awaitable[None]],
    *,
    sleep: Sleep,
    stop: asyncio.Event,
    on_crash: Callable[[Exception, float], None] | None = None,
    cap: float = CRASH_BACKOFF_CAP,
) -> None:
    """Run a source loop; restart it after a capped exponential backoff.

    `cap` is the longest wait (see `crash_backoff_cap`).
    `loop_factory(iteration_done)` builds the loop. The loop calls
    `iteration_done()` after each completed iteration, which resets the backoff.
    `on_crash(error, retry_in)` runs after each logged crash, with the wait
    before the restart (see `record_crash`).
    """
    backoff = CRASH_BACKOFF_BASE

    def reset() -> None:
        nonlocal backoff
        backoff = CRASH_BACKOFF_BASE

    while not stop.is_set():
        try:
            await loop_factory(reset)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("source.crashed", source=name, exc_info=True)
            if on_crash is not None:
                on_crash(exc, backoff)
            await sleep(backoff)
            backoff = min(backoff * 2, cap)
        else:
            return
