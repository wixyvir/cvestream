import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import pytest
from structlog.testing import capture_logs

from cvestream.app.feed_cache import FeedCacheHolder
from cvestream.app.private_api import RuntimeStatus
from cvestream.app.scheduler import (
    CRASH_BACKOFF_CAP,
    JITTER,
    PipelineWorker,
    fetch_outcome,
    source_loop,
    supervise,
)
from cvestream.clock import Clock
from cvestream.config.provider import ConfigProvider, ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.model import AffectedProduct, Sourced, SourceRecord
from cvestream.pipeline.run import CycleResult, SourceOutcome, cycle
from cvestream.sources.base import (
    FetchResult,
    SourceContractError,
    SourceError,
    SourceTransportError,
)
from cvestream.sources.registry import ScheduledSource
from cvestream.state.db import State
from tests.conftest import FakeClock

START = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
TOKEN = "t" * 43
ENDPOINT = Endpoint("team-php", "PHP team", (ProductEntry("php"),))
INTERVAL = timedelta(minutes=10)


class _Stop(Exception):
    """Raised by the injected `sleep` to end a source loop."""


class _Provider:
    def revision(self) -> str:
        return "r"

    def load(self) -> ConfigSnapshot:
        return _snapshot()


def _snapshot() -> ConfigSnapshot:
    return ConfigSnapshot("r", (ENDPOINT,), MappingProxyType({TOKEN: "team-php"}))


class _Adapter:
    """Replays a script of results and errors, remembers the cursors it got."""

    def __init__(
        self, name: str, *script: FetchResult | SourceError | Exception
    ) -> None:
        self.name = name
        self._script = list(script)
        self.cursors: list[str | None] = []

    async def fetch(self, cursor: str | None) -> FetchResult:
        self.cursors.append(cursor)
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _record(clock: Clock, record_id: str = "EUVD-2026-1") -> SourceRecord:
    now = clock.now()
    return SourceRecord(
        source="euvd",
        record_id=record_id,
        observed_at=now,
        aliases=("CVE-2026-1001",),
        description=Sourced("A flaw", "euvd", now),
        affected=(Sourced(AffectedProduct("php"), "euvd", now),),
        updated_at=now - timedelta(hours=1),
    )


def _result(clock: Clock, cursor: str | None = "c1") -> FetchResult:
    return FetchResult((_record(clock),), cursor)


def _scheduled(adapter: _Adapter, interval: timedelta = INTERVAL) -> ScheduledSource:
    return ScheduledSource(adapter, interval)


class _Delays:
    """An injected `sleep`: records the delays, stops the loop after `limit`."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self.limit:
            raise _Stop


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def holder() -> FeedCacheHolder:
    return FeedCacheHolder()


@pytest.fixture
def status() -> RuntimeStatus:
    return RuntimeStatus(started_at=START)


@pytest.fixture
async def worker(
    state: State, holder: FeedCacheHolder, clock: FakeClock
) -> AsyncIterator[PipelineWorker]:
    stop = asyncio.Event()
    pipeline = PipelineWorker(state, _Provider(), _snapshot(), holder, clock)
    task = asyncio.create_task(pipeline.work(stop))
    yield pipeline
    stop.set()
    await task


async def _run_loop(
    scheduled: ScheduledSource,
    worker: PipelineWorker,
    state: State,
    status: RuntimeStatus,
    clock: Clock,
    *,
    iterations: int,
    random: Callable[[], float] = lambda: 0.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    iteration_done: Callable[[], None] = lambda: None,
) -> _Delays:
    delays = _Delays(iterations)
    with pytest.raises(_Stop):
        await source_loop(
            scheduled,
            worker,
            state,
            status,
            clock,
            random=random,
            sleep=sleep or delays,
            iteration_done=iteration_done,
        )
    return delays


def _events(logs: list[dict[str, object]] | list, name: str) -> list:
    return [entry for entry in logs if entry["event"] == name]


@pytest.mark.anyio
async def test_a_successful_fetch_is_applied_and_recorded(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
    holder: FeedCacheHolder,
) -> None:
    adapter = _Adapter("euvd", _result(clock, "c1"))
    with capture_logs() as logs:
        await _run_loop(_scheduled(adapter), worker, state, status, clock, iterations=1)
    source = status.sources["euvd"]
    assert source.last_success_at == START
    assert source.cursor == "c1"
    assert source.last_error_type is None
    assert adapter.cursors == [None]
    with state.transaction() as repository:
        assert repository.cursors() == {"euvd": "c1"}
    (fetched,) = _events(logs, "source.fetched")
    assert fetched["source"] == "euvd"
    assert fetched["count"] == 1
    # the feed cache was swapped by the cycle
    cached = holder.current.get(TOKEN)
    assert cached is not None
    assert cached.item_count == 1
    assert len(_events(logs, "cycle.summary")) == 1


@pytest.mark.anyio
async def test_a_source_error_keeps_the_cursor_and_is_reported(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
    holder: FeedCacheHolder,
) -> None:
    adapter = _Adapter(
        "euvd",
        _result(clock, "c1"),
        SourceTransportError("euvd", "boom"),
        _result(clock, "c2"),
    )
    with capture_logs() as logs:
        await _run_loop(_scheduled(adapter), worker, state, status, clock, iterations=3)
    # the second fetch failed: the third starts from the stored cursor
    assert adapter.cursors == [None, "c1", "c1"]
    (failed,) = _events(logs, "source.failed")
    assert failed["log_level"] == "warning"
    assert failed["source"] == "euvd"
    assert failed["error_type"] == "SourceTransportError"
    summaries = _events(logs, "cycle.summary")
    assert [s["sources"]["euvd"]["result"] for s in summaries] == ["ok", "failed", "ok"]
    # the feed of the first run stayed served through the failure
    assert holder.current.get(TOKEN) is not None


@pytest.mark.anyio
async def test_a_failed_fetch_records_the_error_and_keeps_the_last_success(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
) -> None:
    adapter = _Adapter("euvd", _result(clock), SourceContractError("euvd", "drift"))
    await _run_loop(_scheduled(adapter), worker, state, status, clock, iterations=1)
    clock.advance(timedelta(minutes=1))
    adapter._script.append(SourceContractError("euvd", "drift"))
    await _run_loop(_scheduled(adapter), worker, state, status, clock, iterations=1)
    source = status.sources["euvd"]
    assert source.last_success_at == START
    assert source.last_error_type == "SourceContractError"
    assert source.last_error_at == START + timedelta(minutes=1)


@pytest.mark.anyio
async def test_the_other_source_keeps_fetching_when_one_fails(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
) -> None:
    broken = _Adapter("cisa_kev", SourceTransportError("cisa_kev", "down"))
    healthy = _Adapter("euvd", _result(clock, "c1"))
    await asyncio.gather(
        _run_loop(_scheduled(broken), worker, state, status, clock, iterations=1),
        _run_loop(_scheduled(healthy), worker, state, status, clock, iterations=1),
    )
    assert status.sources["euvd"].last_success_at == START
    assert status.sources["cisa_kev"].last_success_at is None
    with state.transaction() as repository:
        assert repository.cursors() == {"euvd": "c1"}


@pytest.mark.anyio
async def test_a_rolled_back_run_does_not_advance_the_cursor(
    state: State,
    status: RuntimeStatus,
    clock: FakeClock,
    holder: FeedCacheHolder,
) -> None:
    calls: list[int] = []

    def flaky(
        state: State,
        provider: ConfigProvider,
        snapshot: ConfigSnapshot,
        outcomes: list[SourceOutcome],
        clock: Clock,
    ) -> CycleResult:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        return cycle(state, provider, snapshot, outcomes, clock)

    stop = asyncio.Event()
    pipeline = PipelineWorker(
        state, _Provider(), _snapshot(), holder, clock, run_cycle=flaky
    )
    task = asyncio.create_task(pipeline.work(stop))
    adapter = _Adapter("euvd", _result(clock, "c1"), _result(clock, "c1"))
    with capture_logs() as logs:
        await _run_loop(
            _scheduled(adapter), pipeline, state, status, clock, iterations=2
        )
    stop.set()
    await task
    # the first fetch's next_cursor was never stored: the second one restarts
    assert adapter.cursors == [None, None]
    assert status.sources["euvd"].last_success_at == START
    (failure,) = _events(logs, "pipeline.failed")
    assert failure["log_level"] == "error"
    assert failure["exc_info"] is True
    assert failure["error_type"] == "RuntimeError"
    with state.transaction() as repository:
        assert repository.cursors() == {"euvd": "c1"}


@pytest.mark.anyio
async def test_a_failed_run_keeps_the_previous_feed_cache(
    state: State, status: RuntimeStatus, clock: FakeClock, holder: FeedCacheHolder
) -> None:
    runs: list[int] = []

    def flaky(
        state: State,
        provider: ConfigProvider,
        snapshot: ConfigSnapshot,
        outcomes: list[SourceOutcome],
        clock: Clock,
    ) -> CycleResult:
        runs.append(1)
        if len(runs) == 2:
            raise RuntimeError("disk full")
        return cycle(state, provider, snapshot, outcomes, clock)

    stop = asyncio.Event()
    pipeline = PipelineWorker(
        state, _Provider(), _snapshot(), holder, clock, run_cycle=flaky
    )
    task = asyncio.create_task(pipeline.work(stop))
    first = await pipeline.submit(SourceOutcome("euvd", _result(clock, "c1")))
    before = holder.current
    second = await pipeline.submit(SourceOutcome("euvd", _result(clock, "c2")))
    stop.set()
    await task
    assert (first, second) == (True, False)
    assert holder.current is before
    assert pipeline.snapshot.revision == "r"


@pytest.mark.anyio
async def test_the_worker_drains_queued_outcomes_into_one_cycle(
    state: State, clock: FakeClock, holder: FeedCacheHolder
) -> None:
    seen: list[list[str]] = []

    def recording(
        state: State,
        provider: ConfigProvider,
        snapshot: ConfigSnapshot,
        outcomes: list[SourceOutcome],
        clock: Clock,
    ) -> CycleResult:
        seen.append([o.source for o in outcomes])
        return cycle(state, provider, snapshot, outcomes, clock)

    stop = asyncio.Event()
    pipeline = PipelineWorker(
        state, _Provider(), _snapshot(), holder, clock, run_cycle=recording
    )
    # submit before the worker runs: all three are queued together
    submits = [
        asyncio.create_task(pipeline.submit(SourceOutcome(name, FetchResult((), None))))
        for name in ("euvd", "cisa_kev", "other")
    ]
    await asyncio.sleep(0)
    worker_task = asyncio.create_task(pipeline.work(stop))
    results = await asyncio.gather(*submits)
    stop.set()
    await worker_task
    assert results == [True, True, True]
    assert seen == [["euvd", "cisa_kev", "other"]]


@pytest.mark.anyio
async def test_the_worker_exits_between_runs_and_refuses_later_jobs(
    state: State, clock: FakeClock, holder: FeedCacheHolder
) -> None:
    stop = asyncio.Event()
    pipeline = PipelineWorker(state, _Provider(), _snapshot(), holder, clock)
    task = asyncio.create_task(pipeline.work(stop))
    await asyncio.sleep(0)
    stop.set()
    await task
    assert await pipeline.submit(SourceOutcome("euvd", None, "X")) is False


@pytest.mark.anyio
async def test_jobs_still_queued_at_shutdown_are_resolved(
    state: State, clock: FakeClock, holder: FeedCacheHolder
) -> None:
    stop = asyncio.Event()
    pipeline = PipelineWorker(state, _Provider(), _snapshot(), holder, clock)
    stop.set()
    waiting = asyncio.create_task(
        pipeline.submit(SourceOutcome("euvd", FetchResult((), None)))
    )
    await asyncio.sleep(0)
    await pipeline.work(stop)
    assert await waiting is False


@pytest.mark.anyio
async def test_source_stale_is_logged_only_past_three_intervals(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
) -> None:
    failures = [SourceTransportError("euvd", "down") for _ in range(5)]
    adapter = _Adapter("euvd", *failures)
    with capture_logs() as logs:
        # five failing fetches, 10 minutes apart: stale from 30 min + 1 s on
        for index in range(5):
            await _run_loop(
                _scheduled(adapter), worker, state, status, clock, iterations=1
            )
            if index == 2:
                assert not _events(logs, "source.stale")
            clock.advance(timedelta(minutes=10, seconds=1 if index == 2 else 0))
    stale = _events(logs, "source.stale")
    assert len(stale) == 2
    assert stale[0]["log_level"] == "warning"
    assert stale[0]["source"] == "euvd"
    assert stale[0]["last_success_at"] is None


@pytest.mark.anyio
async def test_a_recent_success_is_not_stale(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
) -> None:
    adapter = _Adapter("euvd", _result(clock), SourceTransportError("euvd", "down"))
    with capture_logs() as logs:
        await _run_loop(_scheduled(adapter), worker, state, status, clock, iterations=1)
        clock.advance(timedelta(minutes=29))
        await _run_loop(_scheduled(adapter), worker, state, status, clock, iterations=1)
    assert not _events(logs, "source.stale")


@pytest.mark.anyio
@pytest.mark.parametrize("draw", [0.0, 0.5, 0.999])
async def test_the_sleep_is_the_interval_plus_jitter(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
    draw: float,
) -> None:
    adapter = _Adapter("euvd", _result(clock))
    delays = await _run_loop(
        _scheduled(adapter),
        worker,
        state,
        status,
        clock,
        iterations=1,
        random=lambda: draw,
    )
    assert delays.delays == [pytest.approx(600.0 * (1 + JITTER * draw))]


@pytest.mark.anyio
async def test_the_first_fetch_happens_before_any_sleep(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
) -> None:
    adapter = _Adapter("euvd", _result(clock))
    delays = await _run_loop(
        _scheduled(adapter), worker, state, status, clock, iterations=1
    )
    assert adapter.cursors == [None]
    assert len(delays.delays) == 1


@pytest.mark.anyio
async def test_iteration_done_is_called_after_the_sleep(
    state: State,
    worker: PipelineWorker,
    status: RuntimeStatus,
    clock: FakeClock,
) -> None:
    adapter = _Adapter("euvd", _result(clock), _result(clock))
    calls: list[str] = []
    delays = _Delays(2)

    async def sleep(delay: float) -> None:
        calls.append("sleep")
        await delays(delay)

    with pytest.raises(_Stop):
        await source_loop(
            _scheduled(adapter),
            worker,
            state,
            status,
            clock,
            random=lambda: 0.0,
            sleep=sleep,
            iteration_done=lambda: calls.append("done"),
        )
    assert calls == ["sleep", "done", "sleep"]


@pytest.mark.anyio
async def test_fetch_outcome_never_raises_a_source_error() -> None:
    adapter = _Adapter("euvd", SourceContractError("euvd", "drift"))
    outcome = await fetch_outcome(adapter, "c0")
    assert outcome == SourceOutcome("euvd", None, "SourceContractError")
    assert adapter.cursors == ["c0"]


@pytest.mark.anyio
async def test_an_unexpected_exception_is_not_a_source_error() -> None:
    adapter = _Adapter("euvd", RuntimeError("bug"))
    with pytest.raises(RuntimeError, match="bug"):
        await fetch_outcome(adapter, None)


class _Sleeps:
    def __init__(self, stop: asyncio.Event, limit: int) -> None:
        self.stop = stop
        self.limit = limit
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self.limit:
            self.stop.set()


@pytest.mark.anyio
async def test_a_crashing_loop_is_logged_and_restarted_with_a_capped_backoff() -> None:
    stop = asyncio.Event()
    sleeps = _Sleeps(stop, 12)
    attempts: list[int] = []

    async def loop(_done: Callable[[], None]) -> None:
        attempts.append(1)
        raise RuntimeError("bug")

    with capture_logs() as logs:
        await supervise("euvd", loop, sleep=sleeps, stop=stop)
    assert len(attempts) == 12
    assert sleeps.delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert sleeps.delays[-1] == CRASH_BACKOFF_CAP
    assert max(sleeps.delays) == CRASH_BACKOFF_CAP
    crashed = _events(logs, "source.crashed")
    assert len(crashed) == 12
    assert crashed[0]["log_level"] == "error"
    assert crashed[0]["source"] == "euvd"
    assert crashed[0]["exc_info"] is True


@pytest.mark.anyio
async def test_a_completed_iteration_resets_the_backoff() -> None:
    stop = asyncio.Event()
    sleeps = _Sleeps(stop, 4)
    attempts: list[int] = []

    async def loop(done: Callable[[], None]) -> None:
        attempts.append(1)
        if len(attempts) == 3:
            done()
        raise RuntimeError("bug")

    await supervise("euvd", loop, sleep=sleeps, stop=stop)
    assert sleeps.delays == [1.0, 2.0, 1.0, 2.0]


@pytest.mark.anyio
async def test_the_supervisor_lets_cancellation_through() -> None:
    stop = asyncio.Event()
    started = asyncio.Event()

    async def loop(_done: Callable[[], None]) -> None:
        started.set()
        await asyncio.Event().wait()

    async def no_sleep(_delay: float) -> None:
        return None

    task = asyncio.create_task(supervise("euvd", loop, sleep=no_sleep, stop=stop))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_the_supervisor_does_not_restart_a_loop_that_returned() -> None:
    stop = asyncio.Event()
    attempts: list[int] = []

    async def loop(_done: Callable[[], None]) -> None:
        attempts.append(1)

    async def no_sleep(_delay: float) -> None:
        return None

    await supervise("euvd", loop, sleep=no_sleep, stop=stop)
    assert attempts == [1]


@pytest.mark.anyio
async def test_the_supervisor_does_not_start_when_stopping() -> None:
    stop = asyncio.Event()
    stop.set()

    async def loop(_done: Callable[[], None]) -> None:
        raise AssertionError("must not run")

    async def no_sleep(_delay: float) -> None:
        return None

    await supervise("euvd", loop, sleep=no_sleep, stop=stop)
