from datetime import UTC, datetime

import pytest

from cvestream.domain.model import CvssScore, Sourced
from cvestream.domain.severity import Band, band_of, max_score

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def _scores(*values: float | None) -> list[Sourced[CvssScore | None]]:
    return [
        Sourced(
            CvssScore(score=v) if v is not None else None,
            source="euvd",
            observed_at=NOW,
        )
        for v in values
    ]


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (None, Band.UNKNOWN),
        (0.0, Band.NONE),
        (0.1, Band.LOW),
        (3.9, Band.LOW),
        (4.0, Band.MEDIUM),
        (6.9, Band.MEDIUM),
        (7.0, Band.HIGH),
        (8.9, Band.HIGH),
        (9.0, Band.CRITICAL),
        (10.0, Band.CRITICAL),
    ],
    ids=["none", "0.0", "0.1", "3.9", "4.0", "6.9", "7.0", "8.9", "9.0", "10.0"],
)
def test_band_of(score: float | None, band: Band) -> None:
    assert band_of(score) is band


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((5.0, 9.8), 9.8),
        ((9.8, 5.0), 9.8),
        ((None, 4.3), 4.3),
        ((0.0, None), 0.0),
        ((None, None), None),
        ((), None),
    ],
    ids=[
        "conflict",
        "conflict-reversed",
        "one-unscored",
        "real-zero",
        "all-none",
        "empty",
    ],
)
def test_max_score(values: tuple[float | None, ...], expected: float | None) -> None:
    assert max_score(_scores(*values)) == expected


def test_no_score_is_unknown_never_low() -> None:
    assert band_of(max_score(_scores(None))) is Band.UNKNOWN


def test_band_values_and_rank_order() -> None:
    assert [b.value for b in Band] == [
        "unknown",
        "none",
        "low",
        "medium",
        "high",
        "critical",
    ]
    assert [b.rank for b in Band] == [0, 1, 2, 3, 4, 5]
    assert Band.UNKNOWN.rank < Band.LOW.rank < Band.CRITICAL.rank
