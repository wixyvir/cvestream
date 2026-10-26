"""Immutable domain model.

`None` means unknown, never `0`, `""` or `"n/a"`. Collections are tuples or
frozensets, so every object is hashable. Change an object with
`dataclasses.replace`. The domain never re-validates: boundaries guarantee
scores in 0.0 to 10.0, aware UTC datetimes and non-empty names.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Sourced[T]:
    """A value with its provenance.

    Use `Sourced[X | None]` when a source may say unknown.
    """

    value: T
    source: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class CvssScore:
    score: float
    version: str | None = None
    vector: str | None = None


@dataclass(frozen=True, slots=True)
class AffectedProduct:
    product: str
    vendor: str | None = None
    # Raw free text as the source gives it; parsed only by `domain/version.py`.
    version_text: str | None = None


class KevCatalog(StrEnum):
    CISA_KEV = "cisa_kev"
    EU_KEV = "eu_kev"


@dataclass(frozen=True, slots=True)
class KevEntry:
    catalog: KevCatalog
    known_ransomware_use: bool | None = None
    added_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordRef:
    """Pointer to a contributing source record (every source is kept, FR-7)."""

    source: str
    record_id: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """What a source adapter emits."""

    source: str
    # The source's stable ID (never a URL).
    record_id: str
    observed_at: datetime
    # Raw IDs as given by the source (CVE, EUVD, GHSA, advisory).
    aliases: tuple[str, ...] = ()
    title: Sourced[str] | None = None
    description: Sourced[str] | None = None
    # `Sourced(value=None)` means "this source says unscored".
    scores: tuple[Sourced[CvssScore | None], ...] = ()
    affected: tuple[Sourced[AffectedProduct], ...] = ()
    kev: tuple[Sourced[KevEntry], ...] = ()
    # Links for display only, never identity.
    references: tuple[str, ...] = ()
    url: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    # EPSS probability as a 0-1 fraction.
    epss: Sourced[float] | None = None


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    """A `SourceRecord` with normalized aliases and its primary ID."""

    record: SourceRecord
    aliases: frozenset[str]
    primary_id: str


@dataclass(frozen=True, slots=True)
class Vulnerability:
    """Records of all sources about one vulnerability, merged."""

    primary_id: str
    aliases: frozenset[str]
    records: tuple[RecordRef, ...] = ()
    titles: tuple[Sourced[str], ...] = ()
    descriptions: tuple[Sourced[str], ...] = ()
    scores: tuple[Sourced[CvssScore | None], ...] = ()
    affected: tuple[Sourced[AffectedProduct], ...] = ()
    kev: tuple[Sourced[KevEntry], ...] = ()
    references: tuple[Sourced[str], ...] = ()
    published_at: datetime | None = None

    @property
    def is_known_exploited(self) -> bool:
        return bool(self.kev)
