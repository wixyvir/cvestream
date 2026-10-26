from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from cvestream.clock import Clock, SystemClock

if TYPE_CHECKING:
    from tests.conftest import FakeClock


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)


def test_system_clock_satisfies_protocol() -> None:
    clock: Clock = SystemClock()
    assert isinstance(clock.now(), datetime)


def test_fake_clock_advance_and_set(fake_clock: FakeClock) -> None:
    start = fake_clock.now()
    fake_clock.advance(timedelta(minutes=15))
    assert fake_clock.now() == start + timedelta(minutes=15)
    target = datetime(2030, 5, 6, 7, 8, tzinfo=UTC)
    fake_clock.set(target)
    assert fake_clock.now() == target


def test_fake_clock_rejects_naive_datetimes(fake_clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="aware"):
        type(fake_clock)(datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(ValueError, match="aware"):
        fake_clock.set(datetime(2026, 1, 1))  # noqa: DTZ001
