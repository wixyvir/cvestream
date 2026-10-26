"""Shared fixtures: deterministic clock and anyio backend."""

from datetime import UTC, datetime, timedelta

import pytest


class FakeClock:
    """Controllable `Clock` for tests. Aware UTC datetimes only."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = _require_aware(start or datetime(2026, 1, 1, tzinfo=UTC))

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, value: datetime) -> None:
        self._now = _require_aware(value)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "FakeClock requires an aware datetime"
        raise ValueError(msg)
    return value


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
