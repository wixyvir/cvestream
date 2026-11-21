"""Publication decisions (FR-19) and GUIDs (NFR-3).

A vulnerability is published as `new` on its first match for an endpoint, then
again only when it becomes known-exploited (`kev`) or its severity band rises
(`band-<band>`). Nothing else re-publishes it: not a score change within the
band, a new source, a description edit or a band drop.

The GUID is built here, once, at decision time. The caller stores it and never
recomputes it. Pure: no clock, no I/O, no logging — the decision time is an
input (`now`), the caller's `Clock` value.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cvestream.domain.matcher import MatchResult, PassThrough
from cvestream.domain.model import Vulnerability
from cvestream.domain.severity import Band, band_of, max_score

NEW = "new"
KEV = "kev"
# FR-16: an endpoint without publication history is offered the last 7 days.
BACKFILL_WINDOW = timedelta(days=7)
# FR-17: feed window. Only vulnerabilities active in this window are decided.
PUBLICATION_WINDOW = timedelta(days=14)


def band_kind(band: Band) -> str:
    """Publication kind of a band rise, e.g. `band-high`."""
    return f"band-{band.value}"


@dataclass(frozen=True, slots=True)
class PastPublication:
    """What one earlier publication of this vulnerability on this endpoint says."""

    kind: str
    guid: str
    band: Band
    is_known_exploited: bool


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    endpoint_id: str
    # Display ID at decision time (it may change later; the GUID does not).
    primary_id: str
    kind: str
    guid: str
    band: Band
    is_known_exploited: bool
    # Matched entry names, `matches[0]` first.
    products: tuple[str, ...]
    labels: frozenset[PassThrough]
    # True when a `new` decision happened on the vulnerability's first UTC
    # publication day. Stored, never recomputed.
    is_fresh: bool = False


def _kind(
    vulnerability: Vulnerability, band: Band, history: Sequence[PastPublication]
) -> str | None:
    if not history:
        return NEW
    if vulnerability.is_known_exploited and not any(
        h.is_known_exploited for h in history
    ):
        return KEV
    # `unknown` -> any rated band counts as a rise, `none` included.
    if band is not Band.UNKNOWN and band.rank > max(h.band.rank for h in history):
        return band_kind(band)
    return None


def _is_fresh(vulnerability: Vulnerability, kind: str, now: datetime) -> bool:
    # Sources give day-level dates: a calendar day, not "the last 24 hours".
    published = vulnerability.published_at
    return (
        kind == NEW
        and published is not None
        and published.astimezone(UTC).date() == now.astimezone(UTC).date()
    )


def decide(
    match: MatchResult,
    vulnerability: Vulnerability,
    history: Sequence[PastPublication],
    now: datetime,
) -> PublicationDecision | None:
    """Decide whether `vulnerability` is published on `match.endpoint_id` now.

    `history` holds the earlier publications of this vulnerability on this
    endpoint. `now` is the decision time (the run's `Clock` value, also stored
    as the row's `published_at`). At most one decision per call: when the
    vulnerability becomes known-exploited and its band rises together, `kev`
    wins and records the current band, so the rise is not published again later.
    """
    band = band_of(max_score(vulnerability.scores))
    kind = _kind(vulnerability, band, history)
    if kind is None:
        return None
    # The only GUID construction site: computed once, stored, never recomputed.
    guid = f"cvestream:{match.endpoint_id}:{vulnerability.primary_id}:{kind}"
    if any(h.guid == guid for h in history):
        return None
    return PublicationDecision(
        endpoint_id=match.endpoint_id,
        primary_id=vulnerability.primary_id,
        kind=kind,
        guid=guid,
        band=band,
        is_known_exploited=vulnerability.is_known_exploited,
        products=tuple(m.entry.name for m in match.matches),
        labels=match.labels,
        is_fresh=_is_fresh(vulnerability, kind, now),
    )
