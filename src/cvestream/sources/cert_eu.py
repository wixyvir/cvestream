"""CERT-EU adapter (security advisories of the EU institutions' CERT).

Reads the advisories RSS (latest 10), then the JSON of each advisory whose RSS
`pubDate` is newer than the cursor. One `SourceRecord` per CVE ID found in the
advisory text (`<serial>:<CVE>`), so a multi-CVE advisory never merges its
vulnerabilities; an advisory without a CVE ID gives one record
`CERT-EU-SA<serial>`.

Assumptions, all checked against the live site on 2026-11-03 and documented in
`docs/sources.md`:

- the RSS `pubDate` is Paris time written with `CEST`/`CET`, which
  `email.utils` does not understand; the JSON `publish_date` is UTC;
- the RSS is parsed with the standard library and a DOCTYPE is refused, so no
  entity is ever declared or expanded (XXE, "billion laughs");
- "Affected Products" is prose or bullets without a product name, so the
  section is kept as text blocks (a lead-in with its list); versions stay
  unknown (`version_text=None`);
- a final 4xx on one advisory JSON (withdrawn, not exported yet) skips that
  advisory; a 5xx, a 429 or a network error fails the whole fetch;
- CERT-EU has no score field: `scores` is empty, never `0`.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from cvestream.clock import Clock
from cvestream.domain.model import AffectedProduct, Sourced, SourceRecord
from cvestream.errors import CvestreamError
from cvestream.http import HttpClient
from cvestream.sources.base import (
    Cursor,
    FetchResult,
    SourceContractError,
    SourcePayloadError,
    SourceTransportError,
    clean_text,
    get_bytes,
    get_json,
    parse_calendar_date,
)

NAME = "cert_eu"
BASE = "https://cert.europa.eu/publications/security-advisories"
RSS_URL = "https://cert.europa.eu/publications/security-advisories-rss"
ADVISORY_URL = BASE + "/{serial}/"  # record.url, display only
JSON_URL = BASE + "/{serial}/json"
ADVISORY_ALIAS = "CERT-EU-SA{serial}"
INITIAL_WINDOW = timedelta(days=14)
MAX_RSS_BYTES = 1024 * 1024  # the RSS is about 10 KB
MAX_ADVISORY_BYTES = 2 * 1024 * 1024  # an advisory is 4 to 19 KB

_LINK_SERIAL = re.compile(r"/security-advisories/(?P<serial>\d{4}-\d{3,})/?$")
_TITLE_SERIAL = re.compile(r"(?P<serial>\d{4}-\d{3,}):")
_CVE = re.compile(r"(?<![\w-])CVE-\d{4}-\d{4,}(?![\w-])", re.IGNORECASE)
_PUBLISH_DATE = re.compile(r"(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2}):(\d{2})")
_CURSOR_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_ZONES = {"CEST": "+0200", "CET": "+0100"}

_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---", re.DOTALL)
_ORIGINAL_DATE = re.compile(
    r"^original_date:\s*['\"]?(?P<date>\d{4}-\d{2}-\d{2})['\"]?\s*$", re.MULTILINE
)
_H1 = re.compile(r"^#(?!#)\s*(?P<title>.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BULLET = re.compile(r"^(?P<indent>\s*)[-*+]\s+(?P<text>.*)$")
_REFERENCE_LINK = re.compile(r"<(?P<url>https?://[^>\s]+)>")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_NEW_MARKER = re.compile(r"\[New\]", re.IGNORECASE)
_REFERENCE_MARKER = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")
_AFFECTED_HEADINGS = frozenset({"affected products", "products affected"})
_REFERENCES_HEADINGS = frozenset({"references"})

log = structlog.get_logger()


class _DoctypeRefusedError(CvestreamError):
    """The document declares a DTD: entities could be declared, so it is refused."""


class _InvalidAdvisoryError(CvestreamError):
    """An advisory that cannot be used; the adapter skips and counts it."""


class _NoDoctypeBuilder(ET.TreeBuilder):
    def doctype(self, name: str | None, pubid: str | None, system: str | None) -> None:
        raise _DoctypeRefusedError(name)


# --- payload model (boundary only, never in the domain) ---------------------


class _Advisory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    serial_number: str
    title: str | None = None
    publish_date: str | None = None
    description: str | None = None
    content_markdown: str


@dataclass(frozen=True, slots=True)
class _Item:
    serial: str
    pub_date: datetime


# --- RSS ----------------------------------------------------------------------


def _parse_rss(body: bytes) -> ET.Element:
    # A DOCTYPE is refused before any entity can be expanded, so neither XXE nor
    # an entity bomb can run; this is what `defusedxml` would do.
    parser = ET.XMLParser(target=_NoDoctypeBuilder())  # noqa: S314
    try:
        parser.feed(body)
        return parser.close()
    except (ET.ParseError, _DoctypeRefusedError) as exc:
        msg = f"GET {RSS_URL}: not usable XML ({type(exc).__name__})"
        raise SourcePayloadError(NAME, msg) from exc


def _parse_pub_date(text: str | None) -> datetime | None:
    """RFC 822 date to aware UTC; `CEST`/`CET` are mapped to their offset."""
    if text is None:
        return None
    value = text.strip()
    head, separator, zone = value.rpartition(" ")
    offset = _ZONES.get(zone.upper())
    if separator and offset is not None:
        value = f"{head} {offset}"
    try:
        moment = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if moment.tzinfo is None:
        # An unknown zone: guessing would be a silent hours-long error.
        return None
    return moment.astimezone(UTC)


def _rss_items(root: ET.Element) -> tuple[list[_Item], int]:
    """Usable items (newest `pubDate` per serial) and the count of unusable ones."""
    channel = root.find("channel")
    if channel is None:
        msg = f"GET {RSS_URL}: no channel element"
        raise SourceContractError(NAME, msg)
    elements = channel.findall("item")
    if not elements:
        msg = f"GET {RSS_URL}: channel without item"
        raise SourceContractError(NAME, msg)
    newest: dict[str, datetime] = {}
    skipped = 0
    for element in elements:
        serial = _item_serial(element)
        pub_date = _parse_pub_date(element.findtext("pubDate"))
        if serial is None or pub_date is None:
            skipped += 1
            log.debug(
                "source.record_skipped",
                source=NAME,
                record_id=serial,
                error_type="_InvalidRssItem",
            )
            continue
        if serial not in newest or pub_date > newest[serial]:
            newest[serial] = pub_date
    if not newest:
        msg = f"GET {RSS_URL}: no item with a serial and a date"
        raise SourceContractError(NAME, msg)
    return [_Item(serial, pub) for serial, pub in newest.items()], skipped


def _item_serial(element: ET.Element) -> str | None:
    # Never the `<guid>`: it is an internal counter, not the serial.
    link = _LINK_SERIAL.search((element.findtext("link") or "").strip())
    if link is not None:
        return link["serial"]
    title = _TITLE_SERIAL.match((element.findtext("title") or "").strip())
    return None if title is None else title["serial"]


# --- advisory text ------------------------------------------------------------


def _cve_ids(text: str) -> tuple[str, ...]:
    """Upper-cased CVE IDs, de-duplicated, in order of first appearance."""
    return tuple(dict.fromkeys(m.group().upper() for m in _CVE.finditer(text)))


def _original_date(markdown: str) -> datetime | None:
    front = _FRONT_MATTER.match(markdown)
    if front is None:
        return None
    found = _ORIGINAL_DATE.search(front["body"])
    return None if found is None else parse_calendar_date(found["date"])


def _parse_publish_date(text: str | None) -> datetime | None:
    """`DD-MM-YYYY HH:MM:SS` as UTC (the JSON dates are UTC), else `None`."""
    if text is None:
        return None
    match = _PUBLISH_DATE.fullmatch(text.strip())
    if match is None:
        return None
    day, month, year, hour, minute, second = (int(g) for g in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


def _section(markdown: str, headings: frozenset[str]) -> list[str]:
    """Lines under the first level-1 heading in `headings`, fenced code dropped."""
    lines: list[str] = []
    inside = False
    fenced = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = _H1.match(line)
        if heading is not None:
            if inside:
                break
            # `# **Affected Products:**` is the same heading.
            inside = _clean_markdown(heading["title"]).lower() in headings
            continue
        if inside:
            lines.append(line)
    return lines


def _references(markdown: str) -> tuple[str, ...]:
    section = "\n".join(_section(markdown, _REFERENCES_HEADINGS))
    return tuple(dict.fromkeys(m["url"] for m in _REFERENCE_LINK.finditer(section)))


def _clean_markdown(text: str) -> str:
    """Plain text: no emphasis, `[New]` or `[n]` markers. `_` stays (`mod_php`)."""
    text = text.replace("**", "").replace("`", "")
    text = _NEW_MARKER.sub("", text)
    text = _REFERENCE_MARKER.sub("", text)
    return " ".join(text.split()).strip(" :;,.")


def _description(text: str | None) -> str | None:
    if text is None:
        return None
    lines = (line.strip() for line in _BR.sub("\n", text).split("\n"))
    return clean_text("\n".join(line for line in lines if line))


type _Block = tuple[str, list[list[str]]]


def _blocks(lines: list[str]) -> list[_Block]:
    """Paragraphs (`"p"`, one text) and bullet lists (`"ul"`, one list per bullet).

    A bullet is `[text, nested, ...]`. A blank line ends a paragraph or a list,
    unless the next line is a bullet (a loose list stays one list).
    """
    blocks: list[_Block] = []
    paragraph: list[str] = []
    bullets: list[list[str]] = []
    top_indent = 0
    blank = False

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(("p", [[" ".join(paragraph)]]))
            paragraph.clear()

    def flush_list() -> None:
        if bullets:
            blocks.append(("ul", list(bullets)))
            bullets.clear()

    for line in lines:
        if not line.strip():
            blank = True
            continue
        bullet = _BULLET.match(line)
        if bullet is not None:
            flush_paragraph()
            indent = len(bullet["indent"].expandtabs(4))
            if bullets and indent > top_indent:
                bullets[-1].append(bullet["text"])
            else:
                if not bullets:
                    top_indent = indent
                bullets.append([bullet["text"]])
        elif bullets and not blank:
            bullets[-1][-1] += " " + line.strip()  # continuation of a bullet
        else:
            flush_list()
            if blank:
                flush_paragraph()
            paragraph.append(line.strip())
        blank = False
    flush_paragraph()
    flush_list()
    return blocks


def _joined(bullet: list[str]) -> str:
    return "; ".join(part for part in map(_clean_markdown, bullet) if part)


def _affected_entries(markdown: str) -> list[tuple[str, frozenset[str]]]:
    """Affected-product texts of the section and the CVE IDs each one names."""
    blocks = _blocks(_section(markdown, _AFFECTED_HEADINGS))
    texts: list[str] = []
    index = 0
    while index < len(blocks):
        kind, items = blocks[index]
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if kind == "p" and following is not None and following[0] == "ul":
            lead = _clean_markdown(items[0][0])
            listed = "; ".join(b for b in map(_joined, following[1]) if b)
            texts.append(f"{lead}: {listed}" if lead and listed else lead or listed)
            index += 2
        elif kind == "p":
            texts.append(_clean_markdown(items[0][0]))
            index += 1
        else:
            texts.extend(map(_joined, items))
            index += 1
    return [(text, frozenset(_cve_ids(text))) for text in texts if text]


# --- records ------------------------------------------------------------------


def _records(
    advisory: _Advisory,
    serial: str,
    pub_date: datetime,
    observed_at: datetime,
) -> tuple[SourceRecord, ...]:
    markdown = advisory.content_markdown
    alias = ADVISORY_ALIAS.format(serial=serial)
    title = clean_text(advisory.title)
    description = _description(advisory.description)
    entries = _affected_entries(markdown)
    sourced_title = None if title is None else Sourced(title, NAME, observed_at)
    sourced_description = (
        None if description is None else Sourced(description, NAME, observed_at)
    )
    references = _references(markdown)
    published_at = _original_date(markdown)
    updated_at = _parse_publish_date(advisory.publish_date) or pub_date
    cves = _cve_ids(markdown)
    records: list[SourceRecord] = []
    for cve in cves or (None,):
        # An entry that names CVEs goes only to those; the others go to all.
        affected = tuple(
            Sourced(AffectedProduct(product=text), NAME, observed_at)
            for text, named in entries
            if not named or cve in named
        )
        records.append(
            SourceRecord(
                source=NAME,
                record_id=alias if cve is None else f"{serial}:{cve}",
                observed_at=observed_at,
                aliases=(alias,) if cve is None else (cve, alias),
                title=sourced_title,
                description=sourced_description,
                affected=affected,
                references=references,
                url=ADVISORY_URL.format(serial=serial),
                published_at=published_at,
                updated_at=updated_at,
            )
        )
    return tuple(records)


def _is_missing(error: SourceTransportError) -> bool:
    """A final 4xx other than 429: this advisory is gone, not the source."""
    status = error.status_code
    return status is not None and 400 <= status < 500 and status != 429


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


# --- adapter ------------------------------------------------------------------


class CertEuAdapter:
    """Fetches the CERT-EU advisories published or revised since the cursor."""

    def __init__(self, http: HttpClient, clock: Clock) -> None:
        self._http = http
        self._clock = clock

    @property
    def name(self) -> str:
        return NAME

    async def fetch(self, cursor: Cursor | None) -> FetchResult:
        """All-or-nothing on source-level failures; a bad advisory is skipped."""
        observed_at = self._clock.now()
        start = self._start(cursor, observed_at)
        body = await get_bytes(self._http, NAME, RSS_URL, max_bytes=MAX_RSS_BYTES)
        items, skipped = _rss_items(_parse_rss(body))
        due = [item for item in items if item.pub_date > start]
        records: list[SourceRecord] = []
        failed: list[datetime] = []
        for item in due:
            try:
                advisory = await self._advisory(item.serial)
            except SourceTransportError as exc:
                if not _is_missing(exc):
                    raise  # an outage, a timeout or a 429 fails the whole fetch
                skipped += 1
                failed.append(item.pub_date)
                log.debug(
                    "source.record_skipped",
                    source=NAME,
                    record_id=item.serial,
                    error_type=type(exc).__name__,
                    status=exc.status_code,
                )
                continue
            except (SourcePayloadError, _InvalidAdvisoryError) as exc:
                skipped += 1
                failed.append(item.pub_date)
                log.debug(
                    "source.record_skipped",
                    source=NAME,
                    record_id=item.serial,
                    error_type=type(exc).__name__,
                )
                continue
            records.extend(_records(advisory, item.serial, item.pub_date, observed_at))
        if due and not records:
            msg = f"every advisory fetched from {RSS_URL} is unusable"
            raise SourceContractError(NAME, msg)
        # A failed advisory keeps the cursor strictly below its `pubDate`, so the
        # next fetch retries it; newer advisories are then fetched again, which is
        # harmless (an unchanged content hash touches nothing). Capped at the
        # fetch time: a future `pubDate` must not park the cursor ahead.
        held = min(failed, default=None)
        usable = (i.pub_date for i in items if held is None or i.pub_date < held)
        next_start = min(max([start, *usable]), observed_at)
        return FetchResult(
            records=tuple(records),
            next_cursor=_encode_cursor(next_start),
            skipped=skipped,
        )

    def _start(self, cursor: Cursor | None, now: datetime) -> datetime:
        floor = now - INITIAL_WINDOW
        if cursor is None:
            return floor
        start = _decode_cursor(cursor)
        if start is None or start > now:
            # Unparseable, naive, or in the future (it would skip every advisory).
            log.warning("source.cursor_invalid", source=NAME)
            return floor
        if start < floor:
            log.warning("source.cursor_clamped", source=NAME)
            return floor
        return start

    async def _advisory(self, serial: str) -> _Advisory:
        data = await get_json(
            self._http,
            NAME,
            JSON_URL.format(serial=serial),
            max_bytes=MAX_ADVISORY_BYTES,
        )
        try:
            advisory = _Advisory.model_validate(data)
        except ValidationError as exc:
            msg = f"advisory {serial} has an unexpected shape"
            raise _InvalidAdvisoryError(msg) from exc
        if advisory.serial_number.strip() != serial:
            msg = f"advisory {serial} answers with another serial"
            raise _InvalidAdvisoryError(msg)
        return advisory
