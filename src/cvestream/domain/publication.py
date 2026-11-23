"""Publication decisions (FR-19), grouping (FR-27, story 5.2) and GUIDs (NFR-3).

A vulnerability is published as `new` on its first match for an endpoint, then
again only when it becomes known-exploited (`kev`) or its severity band rises
(`band-<band>`). Nothing else re-publishes it: not a score change within the
band, a new source, a description edit or a band drop.

Grouping is a second, pure step over the decisions of one run:
`group_decisions()` buckets the accepted decisions by matched entry name and
kind family and returns the groups (at least `GROUP_MIN_MEMBERS`, split into
chunks of at most `GROUP_MAX_MEMBERS`). `decide()` itself does not change.

The GUID is built here, once, at decision time. The caller stores it and never
recomputes it. Pure: no clock, no I/O, no logging — the decision time is an
input (`now`), the caller's `Clock` value.
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched

from cvestream.domain.identity import alias_sort_key
from cvestream.domain.matcher import MatchResult, PassThrough
from cvestream.domain.model import Vulnerability
from cvestream.domain.severity import Band, band_of, max_score

NEW = "new"
KEV = "kev"
# FR-16: an endpoint without publication history is offered the last 7 days.
BACKFILL_WINDOW = timedelta(days=7)
# FR-17: feed window. Only vulnerabilities active in this window are decided.
PUBLICATION_WINDOW = timedelta(days=14)
# FR-27 (story 5.2): a burst groups from this many members, at most this many
# per item.
GROUP_MIN_MEMBERS = 3
GROUP_MAX_MEMBERS = 5
FAMILY_NEW = "new"
FAMILY_BAND = "band"


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


# --- grouping (FR-27, story 5.2): a second pure step over one run's decisions ---


def kind_family(kind: str) -> str | None:
    """The group family of a decision kind: `new`, `band` or `None` (`kev`)."""
    if kind == NEW:
        return FAMILY_NEW
    if kind.startswith("band-"):
        return FAMILY_BAND
    return None


def member_order_key(band: Band, primary_id: str) -> tuple[int, tuple[int, int, str]]:
    """Group member order: band descending, then primary ID in natural order."""
    return (-band.rank, alias_sort_key(primary_id))


@dataclass(frozen=True, slots=True)
class PublicationGroup:
    """One feed item of a burst: a chunk of members sharing a group GUID."""

    guid: str
    endpoint_id: str
    family: str
    # The matched entry name (`products[0]`) the members share.
    product: str
    # Ordered by `member_order_key`, then `guid`.
    members: tuple[PublicationDecision, ...]
    # 1-based chunk position within the burst.
    part: int
    parts: int


def _group_guid(endpoint_id: str, family: str, member_guids: Iterable[str]) -> str:
    # The only group GUID construction site: computed once, stored in every
    # member's `publication.group_guid`, never recomputed.
    digest = hashlib.sha256("\n".join(sorted(member_guids)).encode("utf-8"))
    return f"cvestream:{endpoint_id}:group-{family}:{digest.hexdigest()[:16]}"


def group_decisions(
    decisions: Sequence[PublicationDecision],
) -> tuple[PublicationGroup, ...]:
    """Group one run's accepted decisions into burst items (FR-27).

    Candidates share the matched entry name (`products[0]`) and the kind
    family, and are not known-exploited. Buckets below `GROUP_MIN_MEMBERS`
    stay single items. Members are ordered by band descending, then primary ID
    in natural order (then GUID as the final tie-break), and split into chunks
    of at most `GROUP_MAX_MEMBERS`. The result does not depend on the order of
    the input decisions.
    """
    buckets: dict[tuple[str, str, str], list[PublicationDecision]] = {}
    for decision in decisions:
        family = kind_family(decision.kind)
        if family is None or decision.is_known_exploited or not decision.products:
            continue
        buckets.setdefault(
            (decision.endpoint_id, decision.products[0], family), []
        ).append(decision)
    groups: list[PublicationGroup] = []
    for (endpoint_id, product, family), members in buckets.items():
        if len(members) < GROUP_MIN_MEMBERS:
            continue
        members.sort(key=lambda d: (member_order_key(d.band, d.primary_id), d.guid))
        chunks = list(batched(members, GROUP_MAX_MEMBERS, strict=False))
        groups.extend(
            PublicationGroup(
                guid=_group_guid(endpoint_id, family, (m.guid for m in chunk)),
                endpoint_id=endpoint_id,
                family=family,
                product=product,
                members=chunk,
                part=index,
                parts=len(chunks),
            )
            for index, chunk in enumerate(chunks, start=1)
        )
    return tuple(
        sorted(
            groups,
            key=lambda g: (
                member_order_key(g.members[0].band, g.members[0].primary_id),
                g.family,
                g.product,
                g.part,
            ),
        )
    )
