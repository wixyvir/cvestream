"""Severity bands and the highest score across sources (FR-24).

No score is `Band.UNKNOWN`, never `Band.LOW`. Every `Sourced` score is kept;
these functions compute and never overwrite.
"""

from collections.abc import Iterable
from enum import StrEnum

from cvestream.domain.model import CvssScore, Sourced


class Band(StrEnum):
    """Declaration order is severity order; compare with `rank`, not as strings."""

    UNKNOWN = "unknown"
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return list(Band).index(self)


def max_score(scores: Iterable[Sourced[CvssScore | None]]) -> float | None:
    """Highest score among the sources that scored, `None` when none did."""
    known = [s.value.score for s in scores if s.value is not None]
    return max(known) if known else None


def band_of(score: float | None) -> Band:
    """CVSS qualitative scale (v3.x and v4.0, applied to every version)."""
    if score is None:
        return Band.UNKNOWN
    if score == 0.0:
        return Band.NONE
    if score < 4.0:
        return Band.LOW
    if score < 7.0:
        return Band.MEDIUM
    if score < 9.0:
        return Band.HIGH
    return Band.CRITICAL
