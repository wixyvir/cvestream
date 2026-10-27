"""Time source.

Aware UTC datetimes only. Inject a `Clock`; never call `datetime.now()` elsewhere.
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
