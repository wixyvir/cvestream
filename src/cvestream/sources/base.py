"""Source adapter contract.

An adapter turns one upstream source into `SourceRecord`s. A record that fails
validation is skipped and counted; a source-level failure raises a
`SourceError` and returns nothing, so the caller never advances the cursor.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from cvestream.domain.model import SourceRecord
from cvestream.errors import CvestreamError
from cvestream.http import (
    DEFAULT_MAX_BYTES,
    HttpClient,
    HttpError,
    HttpResponseTooLargeError,
)

# Opaque, owned by the adapter; the state layer stores it as text.
type Cursor = str

# Strings a source writes when it means "unknown" (compared after strip + lower).
PLACEHOLDERS = frozenset({"", "n/a", "na", "-", "unknown"})
_CALENDAR_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True, slots=True)
class FetchResult:
    records: tuple[SourceRecord, ...]
    # None: the adapter keeps no cursor (KEV catalogues are loaded in full).
    next_cursor: Cursor | None
    # Records dropped by record-level validation.
    skipped: int = 0


class SourceAdapter(Protocol):
    @property
    def name(self) -> str:
        """Registry key, cursor key and `source` log field (euvd, cisa_kev)."""
        ...

    async def fetch(self, cursor: Cursor | None) -> FetchResult: ...


class SourceError(CvestreamError):
    """A source-level failure: the whole fetch is lost, the cursor stays."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(message)
        self.source = source


class SourceTransportError(SourceError):
    """No usable response: network, TLS, timeout or HTTP error status."""


class SourcePayloadError(SourceError):
    """A response that is not data: not JSON, bad encoding, too large."""


class SourceContractError(SourceError):
    """Valid JSON in an unexpected shape: the upstream API drifted."""


async def get_json(
    http: HttpClient,
    source: str,
    url: str,
    *,
    params: Mapping[str, str | int] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> object:
    """GET `url` and decode the JSON body.

    The single place where HTTP and decoding failures become `SourceError`s.
    """
    try:
        body = await http.get(url, params=params, max_bytes=max_bytes)
    except HttpResponseTooLargeError as exc:
        raise SourcePayloadError(source, str(exc)) from exc
    except HttpError as exc:
        raise SourceTransportError(source, str(exc)) from exc
    try:
        return json.loads(body)
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        msg = f"GET {url}: response is not valid JSON ({type(exc).__name__})"
        raise SourcePayloadError(source, msg) from exc


def clean_text(value: object) -> str | None:
    """Stripped text, or `None` when the value is not text or is a placeholder."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return None if text.lower() in PLACEHOLDERS else text


def parse_calendar_date(value: object) -> datetime | None:
    """`YYYY-MM-DD` as midnight UTC (display only), or `None` when unusable."""
    if not isinstance(value, str):
        return None
    match = _CALENDAR_DATE.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day = (int(group) for group in match.groups())
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None
