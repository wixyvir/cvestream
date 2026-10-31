"""CISA Known Exploited Vulnerabilities adapter.

Loads the full JSON catalogue on every fetch (it is small and carries no
incremental filter). Assumptions are documented in `docs/sources.md`.
"""

import re
from datetime import datetime

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cvestream.clock import Clock
from cvestream.domain.model import KevCatalog, KevEntry, Sourced, SourceRecord
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

NAME = "cisa_kev"
CATALOG_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CATALOG_PAGE = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"

_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}")

log = structlog.get_logger()


class _InvalidEntryError(CvestreamError):
    """An entry that cannot be used; the adapter skips and counts it."""


class _Entry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cve_id: str = Field(alias="cveID")
    vulnerability_name: str | None = Field(default=None, alias="vulnerabilityName")
    short_description: str | None = Field(default=None, alias="shortDescription")
    date_added: str | None = Field(default=None, alias="dateAdded")
    ransomware_use: str | None = Field(default=None, alias="knownRansomwareCampaignUse")


def _record(entry: _Entry, observed_at: datetime) -> SourceRecord:
    cve_id = entry.cve_id.strip().upper()
    if _CVE_PATTERN.fullmatch(cve_id) is None:
        msg = "cveID is not a CVE ID"
        raise _InvalidEntryError(msg)
    title = clean_text(entry.vulnerability_name)
    description = clean_text(entry.short_description)
    # CISA only says "Known" or "Unknown": unknown is `None`, never `False`.
    ransomware = (entry.ransomware_use or "").strip().lower() == "known"
    kev = KevEntry(
        catalog=KevCatalog.CISA_KEV,
        known_ransomware_use=True if ransomware else None,
        added_at=parse_calendar_date(entry.date_added),
    )
    return SourceRecord(
        source=NAME,
        record_id=cve_id,
        observed_at=observed_at,
        aliases=(cve_id,),
        title=None if title is None else Sourced(title, NAME, observed_at),
        description=(
            None if description is None else Sourced(description, NAME, observed_at)
        ),
        kev=(Sourced(kev, NAME, observed_at),),
        url=CATALOG_PAGE,
    )


class CisaKevAdapter:
    """Fetches the whole CISA KEV catalogue."""

    def __init__(self, http: HttpClient, clock: Clock) -> None:
        self._http = http
        self._clock = clock

    @property
    def name(self) -> str:
        return NAME

    async def fetch(self, cursor: Cursor | None) -> FetchResult:
        """Full load every time; `cursor` is ignored."""
        observed_at = self._clock.now()
        data = await get_json(self._http, NAME, CATALOG_URL)
        if not isinstance(data, dict) or not isinstance(
            data.get("vulnerabilities"), list
        ):
            msg = "CISA KEV catalogue has no list 'vulnerabilities'"
            raise SourceContractError(NAME, msg)
        entries: list[object] = data["vulnerabilities"]
        if not entries:
            msg = "CISA KEV catalogue is empty"
            raise SourceContractError(NAME, msg)
        records: list[SourceRecord] = []
        skipped = 0
        for raw in entries:
            try:
                records.append(_record(_Entry.model_validate(raw), observed_at))
            except (ValidationError, _InvalidEntryError) as exc:
                skipped += 1
                log.debug(
                    "source.record_skipped",
                    source=NAME,
                    record_id=None,
                    error_type=type(exc).__name__,
                )
        if not records:
            # Every entry rejected is drift, not bad records: fail loudly.
            msg = "CISA KEV catalogue: no entry passed validation"
            raise SourceContractError(NAME, msg)
        version = clean_text(data.get("catalogVersion"))
        return FetchResult(records=tuple(records), next_cursor=version, skipped=skipped)
