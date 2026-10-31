"""EUVD adapter (ENISA European Union Vulnerability Database).

Incremental `/api/search` paging on `fromUpdatedDate` plus the full `/api/kev/dump`
on every fetch. Assumptions (timezone, placeholders, ordering) are documented in
`docs/sources.md`.
"""

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cvestream.clock import Clock
from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    Sourced,
    SourceRecord,
)
from cvestream.errors import CvestreamError
from cvestream.http import HttpClient
from cvestream.sources.base import (
    Cursor,
    FetchResult,
    SourceContractError,
    clean_text,
    get_json,
    parse_calendar_date,
)

NAME = "euvd"
EU_KEV_SOURCE = "eu_kev"
BASE_URL = "https://euvdservices.enisa.europa.eu/api"
RECORD_URL = "https://euvd.enisa.europa.eu/vulnerability/{id}"
PAGE_SIZE = 100  # API maximum
PAGE_PAUSE = 0.5  # seconds between pages: sequential pages, short pauses
MAX_PAGES = 1000  # safety cap (14 days is about 200 pages)
INITIAL_WINDOW = timedelta(days=14)

_EU_KEV_LIST = "eukev_kev"
_ID_PATTERN = re.compile(r"EUVD-\d{4}-\d+")
_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}")
_CURSOR_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ),
        start=1,
    )
}
# "Oct 26, 2026, 6:36:49 PM"; `\s` before AM/PM also accepts U+202F.
_DATE_PATTERN = re.compile(
    r"(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?P<year>\d{4}), "
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})\s(?P<ampm>AM|PM)"
)

log = structlog.get_logger()


class _InvalidRecordError(CvestreamError):
    """A record that cannot be used; the adapter skips and counts it."""


# --- payload models (boundary only, never in the domain) --------------------


class _Vendor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None


class _Product(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    vendor: _Vendor | None = None


class _ProductEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product: _Product | None = None
    product_version: str | None = None


class _SearchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    description: str | None = None
    date_published: str | None = Field(default=None, alias="datePublished")
    date_updated: str = Field(alias="dateUpdated")
    base_score: float | str | None = Field(default=None, alias="baseScore")
    base_score_version: str | None = Field(default=None, alias="baseScoreVersion")
    base_score_vector: str | None = Field(default=None, alias="baseScoreVector")
    references: str | None = None
    aliases: str | None = None
    epss: float | str | None = None
    products: list[_ProductEntry] | None = Field(default=None, alias="enisaIdProduct")


class _KevEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cve_id: str | None = Field(default=None, alias="cveId")
    euvd_id: str | None = Field(default=None, alias="euvdId")
    vulnerability_name: str | None = Field(default=None, alias="vulnerabilityName")
    date_added: str | None = Field(default=None, alias="dateAdded")
    sources: list[str] = Field(default_factory=list)


# --- parsing helpers ---------------------------------------------------------


def parse_euvd_date(value: str) -> datetime | None:
    """EUVD wall-clock text to aware UTC (EUVD dates are UTC), `None` if unusable.

    No `strptime`: `%b` and `%p` depend on the locale.
    """
    match = _DATE_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    month = _MONTHS.get(match["mon"])
    hour12 = int(match["hour"])
    if month is None or not 1 <= hour12 <= 12:
        return None
    hour = hour12 % 12 + (12 if match["ampm"] == "PM" else 0)
    try:
        return datetime(
            int(match["year"]),
            month,
            int(match["day"]),
            hour,
            int(match["minute"]),
            int(match["second"]),
            tzinfo=UTC,
        )
    except ValueError:
        return None


def _number(value: object) -> float | None:
    """A finite number, or `None` for placeholders, text and everything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = clean_text(value)
        if text is None:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, int | float):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _lines(value: str | None) -> list[str]:
    return [] if value is None else [line.strip() for line in value.split("\n")]


def _score(item: _SearchItem, observed_at: datetime) -> Sourced[CvssScore | None]:
    value = _number(item.base_score)
    version = clean_text(item.base_score_version)
    if value is None or not 0.0 <= value <= 10.0 or (value == 0.0 and version is None):
        # EUVD writes 0.0 and omits the version for an unscored record
        return Sourced(None, NAME, observed_at)
    return Sourced(
        CvssScore(round(value, 1), version, clean_text(item.base_score_vector)),
        NAME,
        observed_at,
    )


def _epss(item: _SearchItem, observed_at: datetime) -> Sourced[float] | None:
    # EUVD gives a percentage and shows 0.0 for CVEs not scored yet; a real EPSS
    # probability is never exactly 0, so 0 means unknown.
    percent = _number(item.epss)
    if percent is None or not 0.0 < percent <= 100.0:
        return None
    return Sourced(round(percent / 100, 6), NAME, observed_at)


def _affected(
    item: _SearchItem, observed_at: datetime
) -> tuple[Sourced[AffectedProduct], ...]:
    affected = []
    for entry in item.products or []:
        product_info = entry.product
        name = clean_text(product_info.name) if product_info else None
        if product_info is None or name is None:
            continue
        vendor = clean_text(product_info.vendor.name) if product_info.vendor else None
        product = AffectedProduct(
            product=name,
            vendor=vendor,
            version_text=clean_text(entry.product_version),
        )
        affected.append(Sourced(product, NAME, observed_at))
    return tuple(affected)


def _record(item: _SearchItem, observed_at: datetime) -> SourceRecord:
    record_id = item.id.strip()
    if _ID_PATTERN.fullmatch(record_id) is None:
        msg = "id is not an EUVD ID"
        raise _InvalidRecordError(msg)
    updated_at = parse_euvd_date(item.date_updated)
    if updated_at is None:
        msg = "dateUpdated is not an EUVD date"
        raise _InvalidRecordError(msg)
    aliases: dict[str, None] = {record_id: None}
    for alias in _lines(item.aliases):
        if clean_text(alias) is not None:
            aliases.setdefault(alias)
    description = clean_text(item.description)
    published_at = parse_euvd_date(item.date_published or "")
    return SourceRecord(
        source=NAME,
        record_id=record_id,
        observed_at=observed_at,
        aliases=tuple(aliases),
        description=(
            None if description is None else Sourced(description, NAME, observed_at)
        ),
        scores=(_score(item, observed_at),),
        affected=_affected(item, observed_at),
        references=tuple(line for line in _lines(item.references) if line),
        url=RECORD_URL.format(id=record_id),
        published_at=published_at,
        updated_at=updated_at,
        epss=_epss(item, observed_at),
    )


def _identifier(value: str | None, pattern: re.Pattern[str]) -> str | None:
    """Upper-cased ID when it fullmatches `pattern`, else `None` (unusable)."""
    text = clean_text(value)
    if text is None:
        return None
    text = text.upper()
    return text if pattern.fullmatch(text) else None


def _kev_record(entry: _KevEntry, observed_at: datetime) -> SourceRecord:
    euvd_id = _identifier(entry.euvd_id, _ID_PATTERN)
    cve_id = _identifier(entry.cve_id, _CVE_PATTERN)
    record_id = euvd_id or cve_id
    if record_id is None:
        msg = "KEV entry has no usable euvdId or cveId"
        raise _InvalidRecordError(msg)
    name = clean_text(entry.vulnerability_name)
    kev = KevEntry(
        catalog=KevCatalog.EU_KEV,
        known_ransomware_use=None,
        added_at=parse_calendar_date(entry.date_added),
    )
    return SourceRecord(
        source=EU_KEV_SOURCE,
        record_id=record_id,
        observed_at=observed_at,
        aliases=tuple(i for i in (euvd_id, cve_id) if i is not None),
        title=None if name is None else Sourced(name, EU_KEV_SOURCE, observed_at),
        kev=(Sourced(kev, EU_KEV_SOURCE, observed_at),),
        url=None if euvd_id is None else RECORD_URL.format(id=euvd_id),
    )


def _encode_cursor(moment: datetime) -> Cursor:
    return moment.astimezone(UTC).strftime(_CURSOR_FORMAT)


def _decode_cursor(cursor: Cursor) -> datetime | None:
    try:
        moment = datetime.fromisoformat(cursor)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(UTC)


def _skipped(record_id: str | None, exc: Exception) -> None:
    log.debug(
        "source.record_skipped",
        source=NAME,
        record_id=record_id,
        error_type=type(exc).__name__,
    )


# --- adapter -----------------------------------------------------------------


class EuvdAdapter:
    """Fetches EUVD records updated since the cursor and the EU KEV dump."""

    def __init__(
        self,
        http: HttpClient,
        clock: Clock,
        *,
        page_size: int = PAGE_SIZE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._clock = clock
        self._page_size = page_size
        self._sleep = sleep

    @property
    def name(self) -> str:
        return NAME

    async def fetch(self, cursor: Cursor | None) -> FetchResult:
        """All-or-nothing: any source-level failure raises and returns nothing."""
        observed_at = self._clock.now()
        start = self._start(cursor, observed_at)
        kev_records, kev_skipped = await self._fetch_kev(observed_at)
        search_records, search_skipped, complete = await self._fetch_search(
            start, observed_at
        )
        newest = max(
            (r.updated_at for r in search_records if r.updated_at), default=start
        )
        # Capped at the fetch time: a future `dateUpdated` (bad source data) must
        # not park the cursor ahead of every real record. The cap can only cause
        # re-delivery (duplicates), never a gap. An incomplete paging keeps the
        # cursor where it was: the unread items are older than the ones read.
        next_start = min(max(start, newest), observed_at) if complete else start
        return FetchResult(
            records=(*kev_records, *search_records),
            next_cursor=_encode_cursor(next_start),
            skipped=kev_skipped + search_skipped,
        )

    def _start(self, cursor: Cursor | None, now: datetime) -> datetime:
        if cursor is not None:
            start = _decode_cursor(cursor)
            if start is not None and start <= now:
                return start
            # Unparseable, naive, or in the future (it would skip every record).
            log.warning("source.cursor_invalid", source=NAME)
        return now - INITIAL_WINDOW

    async def _fetch_kev(self, observed_at: datetime) -> tuple[list[SourceRecord], int]:
        data = await get_json(self._http, NAME, f"{BASE_URL}/kev/dump")
        if not isinstance(data, list) or not data:
            msg = "EUVD /kev/dump is not a non-empty list"
            raise SourceContractError(NAME, msg)
        records: list[SourceRecord] = []
        skipped = 0
        for raw in data:
            try:
                entry = _KevEntry.model_validate(raw)
                if _EU_KEV_LIST not in entry.sources:
                    continue  # CISA-only: `cisa_kev` is authoritative for those
                records.append(_kev_record(entry, observed_at))
            except (ValidationError, _InvalidRecordError) as exc:
                skipped += 1
                _skipped(None, exc)
        if skipped and not records:
            # Every EU KEV candidate rejected is drift, not bad records: fail loudly.
            msg = "EUVD /kev/dump: no entry passed validation"
            raise SourceContractError(NAME, msg)
        return records, skipped

    async def _fetch_search(
        self, start: datetime, observed_at: datetime
    ) -> tuple[list[SourceRecord], int, bool]:
        """Records, skipped count, and whether paging read the whole window."""
        records: dict[str, SourceRecord] = {}
        skipped = 0
        read = 0  # items read so far, whatever page size the server applied
        previous: datetime | None = None
        for page in range(MAX_PAGES):
            params = {
                "fromUpdatedDate": start.date().isoformat(),  # UTC day, day-granular
                "size": self._page_size,
                "page": page,
            }
            data = await get_json(self._http, NAME, f"{BASE_URL}/search", params=params)
            items, total = _search_page(data)
            read += len(items)
            oldest: datetime | None = None
            for raw in items:
                try:
                    record = _record(_SearchItem.model_validate(raw), observed_at)
                except (ValidationError, _InvalidRecordError) as exc:
                    skipped += 1
                    _skipped(raw.get("id") if isinstance(raw, dict) else None, exc)
                    continue
                updated_at = record.updated_at
                if updated_at is None:  # unreachable: `_record` requires it
                    continue
                if record.record_id in records:
                    # Already emitted: records inserted on top while paging shift
                    # older items onto the next page. Not an ordering violation.
                    continue
                if previous is not None and updated_at > previous:
                    msg = "EUVD /search is no longer sorted by dateUpdated descending"
                    raise SourceContractError(NAME, msg)
                previous = oldest = updated_at
                if updated_at >= start:
                    records.setdefault(record.record_id, record)
            # No short-page stop: EUVD silently caps `size`, so a page shorter
            # than requested is not the last one. Only `total` says so.
            if read >= total or (oldest is not None and oldest < start):
                return list(records.values()), skipped, True
            if not items:
                # Stale cache or a glitch: keep what was read, without failing the
                # fetch, but do not move the cursor past the unread items.
                log.warning(
                    "source.paging_incomplete", source=NAME, count=read, total=total
                )
                return list(records.values()), skipped, False
            await self._sleep(PAGE_PAUSE)
        msg = f"EUVD /search still returned data after {MAX_PAGES} pages"
        raise SourceContractError(NAME, msg)


def _search_page(data: object) -> tuple[list[object], int]:
    if not isinstance(data, dict):
        msg = "EUVD /search is not a JSON object"
        raise SourceContractError(NAME, msg)
    items = data.get("items")
    total = data.get("total")
    if not isinstance(items, list):
        msg = "EUVD /search has no list 'items'"
        raise SourceContractError(NAME, msg)
    if not isinstance(total, int) or isinstance(total, bool):
        msg = "EUVD /search has no integer 'total'"
        raise SourceContractError(NAME, msg)
    return items, total
