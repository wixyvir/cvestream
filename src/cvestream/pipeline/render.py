"""RSS 2.0 rendering of one endpoint's feed (FR-17, FR-18, FR-24).

Pure and deterministic: no clock, no I/O, no logging, so an unchanged feed
keeps its ETag and readers get `304`. The document is built with
`xml.etree.ElementTree`, never by string formatting; the serializer escapes
every text. The item description is an HTML fragment (RSS 2.0 reads it as
entity-encoded HTML) in which every value is HTML-escaped first, so source text
shows literally and only http(s) URLs become links. Characters XML 1.0 forbids
are removed, otherwise no parser would accept the document.

The title says what was published: the severity band and the known-exploited
flag recorded at decision time, and the affected product the first matched
entry names in the current merged data (the entry's own name when there is no
such product), flagged with emoji markers built from those decision-time
values only (fire, red circle, red triangle, squared new, in that order). The
body shows the current merged data of the vulnerability,
leading with what a reader sees first: Slack's RSS app shows only about the
first `SLACK_PREVIEW_CHARS` plain-text characters of the description and keeps
only the first block-level elements, so the first `<p>` holds the facts line,
the matched products and the full description. Unknown is `unknown`, never
`0`, `n/a` or an empty string.
"""

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import urlsplit

from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.identity import AliasKind, alias_kind
from cvestream.domain.matcher import PassThrough, affected_hits
from cvestream.domain.model import KevCatalog, KevEntry, RecordRef, Vulnerability
from cvestream.domain.publication import KEV, NEW
from cvestream.domain.severity import Band, band_of, max_score

# RSS 2.0 requires a channel link; the daemon does not know its public base URL.
CHANNEL_LINK = "https://github.com/wixyvir/cvestream"
MEDIA_TYPE = "application/rss+xml; charset=utf-8"

UNKNOWN = "unknown"
# Slack's RSS app cuts the item description with "..." after about this many
# plain-text characters (measured 492 to 498 on three long posts, 2026-11-21;
# not documented by Slack). The body leads with what must fit in this window;
# a later change is a one-line fix here.
SLACK_PREVIEW_CHARS = 495
_MAX_REFERENCES = 10
_LINK_SCHEMES = frozenset({"http", "https"})
_UPDATE_PREFIX = "[UPDATE] "
_EXPLOITED = "EXPLOITED"
_SEPARATORS = re.compile(r"[\s_-]+")
_SOURCE_ORDER = {"euvd": 0, "eu_kev": 1, "cisa_kev": 2, "cert_eu": 3}
# Sources whose affected products are prose, not product names: they decide the
# match, but never name the title (the entry name is used instead).
_PROSE_SOURCES = frozenset({"cert_eu"})
_KEV_NAMES = {KevCatalog.CISA_KEV: "CISA KEV", KevCatalog.EU_KEV: "EU KEV"}
_KEV_ORDER = (KevCatalog.CISA_KEV, KevCatalog.EU_KEV)
# XML 1.0 `Char` production; also drops lone surrogates. Escapes, not literal
# characters: U+E000 is invisible and an editor may normalise the others.
_FORBIDDEN = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


@dataclass(frozen=True, slots=True)
class FeedEntry:
    """One publication of an endpoint, ready to render."""

    guid: str
    kind: str
    # Decision-time values (they go in the title).
    primary_id: str
    band: Band
    is_known_exploited: bool
    products: tuple[str, ...]
    labels: frozenset[PassThrough]
    published_at: datetime
    # Current merged data (it goes in the description).
    vulnerability: Vulnerability
    # Decision-time freshness (`None` on rows from before the column).
    is_fresh: bool | None = None


@dataclass(frozen=True, slots=True)
class RenderedFeed:
    endpoint_id: str
    xml: bytes
    etag: str
    item_count: int
    rendered_at: datetime
    changed_at: datetime


def etag_of(xml: bytes) -> str:
    """Quoted SHA-256 of the document: the only ETag construction site."""
    return f'"{hashlib.sha256(xml).hexdigest()}"'


def _xml_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return _FORBIDDEN.sub("", value)


def _one_line(value: str) -> str:
    # Forbidden characters go first: some (`\x0b`, `\x1f`) count as whitespace.
    return " ".join(_FORBIDDEN.sub("", value).split())


def _rfc822(value: datetime) -> str:
    return format_datetime(value.astimezone(UTC), usegmt=True)


def _words(value: str) -> str:
    return f" {_SEPARATORS.sub(' ', value).strip().casefold()} "


def _product_label(name: str, vendor: str | None) -> str:
    product = _one_line(name)
    vendor = _one_line(vendor or "")
    # `PHP Group PHP` reads fine; `wild-apricot Wild Apricot Login` does not.
    if not vendor or _words(vendor) in _words(product):
        return product
    return f"{vendor} {product}"


def _title_product(entry: FeedEntry, endpoint: Endpoint) -> str:
    if not entry.products:
        return UNKNOWN
    name = entry.products[0]
    # The entry as configured now; a removed entry still has its name.
    product_entry = next(
        (p for p in endpoint.products if p.name == name), ProductEntry(name)
    )
    affected = [
        a.value for a in entry.vulnerability.affected if a.source not in _PROSE_SOURCES
    ]
    hits = affected_hits(product_entry, affected)
    if not hits:
        return _one_line(name)
    return _product_label(hits[0].product, hits[0].vendor)


def _markers(entry: FeedEntry) -> str:
    """The title's emoji, in order, all that apply: decision-time values only.

    Escapes, not literal characters (ruff RUF001). They flag the item, they
    never replace the words.
    """
    markers = ""
    if entry.is_known_exploited:
        markers += "\N{FIRE}"
    if entry.band is Band.CRITICAL:
        markers += "\N{LARGE RED CIRCLE}"
    # A rise to `none` or `low` keeps its words but gets no triangle (review
    # decision D1 of wixyvir/cvestream#51).
    if entry.kind.startswith("band-") and entry.band.rank >= Band.MEDIUM.rank:
        markers += "\N{UP-POINTING RED TRIANGLE}"
    if entry.is_fresh is True:
        markers += "\N{SQUARED NEW}"
    return markers


def _title(entry: FeedEntry, endpoint: Endpoint) -> str:
    head = f"{entry.primary_id} in {_title_product(entry, endpoint)}"
    band = entry.band.value
    exploited = f", {_EXPLOITED}" if entry.is_known_exploited else ""
    if entry.kind == NEW:
        text = f"{head}: severity {band}{exploited}"
    elif entry.kind == KEV:
        text = f"{_UPDATE_PREFIX}{head}: now {_EXPLOITED}, severity {band}"
    elif entry.kind.startswith("band-"):
        text = f"{_UPDATE_PREFIX}{head}: severity raised to {band}{exploited}"
    else:
        text = f"{_UPDATE_PREFIX}{head}: severity {band}{exploited}"
    markers = _markers(entry)
    return f"{markers} {text}" if markers else text


def _ordered_records(vulnerability: Vulnerability) -> list[RecordRef]:
    # Stable: records of one source keep their `record_id` order.
    return sorted(
        vulnerability.records,
        key=lambda r: (_SOURCE_ORDER.get(r.source, len(_SOURCE_ORDER)), r.source),
    )


def _link(records: Sequence[RecordRef]) -> str | None:
    return next((r.url for r in records if r.url), None)


def _ids(vulnerability: Vulnerability) -> str:
    ids: list[str] = []
    for kind in (AliasKind.CVE, AliasKind.EUVD):
        ids.extend(sorted(a for a in vulnerability.aliases if alias_kind(a) is kind))
    return ", ".join(ids) if ids else UNKNOWN


def _matched(entry: FeedEntry) -> str:
    matched = ", ".join(_one_line(p) for p in entry.products) or UNKNOWN
    suffix = (
        " (versions unknown)" if PassThrough.VERSIONS_UNKNOWN in entry.labels else ""
    )
    return f"{matched}{suffix}"


def _facts(entry: FeedEntry) -> str:
    """The facts line: band, highest CVSS, exploited, published, updated."""
    vulnerability = entry.vulnerability
    score = max_score(vulnerability.scores)
    band = band_of(score).value
    if score is None:
        cvss = f"CVSS {UNKNOWN}"
        if PassThrough.SCORE_UNKNOWN in entry.labels:
            cvss += " (no score; let through)"
    else:
        cvss = f"CVSS {score:.1f}"
    # KEV presence is a fact, never unknown.
    exploited = "yes" if vulnerability.is_known_exploited else "no"
    dot = " \N{MIDDLE DOT} "
    published = _day(vulnerability.published_at)
    facts = f"{band}{dot}{cvss}{dot}exploited: {exploited}{dot}published {published}"
    # Left out when it falls on the same UTC day as `published` (comparing the
    # rendered days also drops `updated unknown` when both are unknown).
    updated = _day(vulnerability.updated_at)
    if updated != published:
        facts += f"{dot}updated {updated}"
    return facts


def _cvss(vulnerability: Vulnerability) -> str:
    parts: list[str] = []
    for sourced in vulnerability.scores:
        if sourced.value is None:
            parts.append(f"{UNKNOWN} ({sourced.source})")
            continue
        version = sourced.value.version or UNKNOWN
        parts.append(f"{sourced.value.score:.1f} (CVSS {version}, {sourced.source})")
    return "; ".join(parts) if parts else UNKNOWN


def _ransomware(value: bool | None) -> str:
    if value is None:
        return UNKNOWN
    return "known" if value else "no"


def _day(value: datetime | None) -> str:
    """A calendar day in UTC: sources give day-level dates for most fields."""
    return value.astimezone(UTC).date().isoformat() if value is not None else UNKNOWN


def _kev(vulnerability: Vulnerability) -> str:
    by_catalog: dict[KevCatalog, bool | None] = {}
    added: dict[KevCatalog, datetime] = {}
    for sourced in vulnerability.kev:
        entry: KevEntry = sourced.value
        known = by_catalog.get(entry.catalog)
        # One part per catalog; a recorded ransomware use wins over unknown.
        if entry.catalog not in by_catalog or known is None:
            by_catalog[entry.catalog] = entry.known_ransomware_use
        elif entry.known_ransomware_use and not known:
            by_catalog[entry.catalog] = True
        # The earliest known addition to the catalog.
        if entry.added_at is not None and (
            entry.catalog not in added or entry.added_at < added[entry.catalog]
        ):
            added[entry.catalog] = entry.added_at
    if not by_catalog:
        return UNKNOWN
    parts = [
        f"{_KEV_NAMES[catalog]} (added {_day(added.get(catalog))},"
        f" ransomware use: {_ransomware(by_catalog[catalog])})"
        for catalog in _KEV_ORDER
        if catalog in by_catalog
    ]
    return ", ".join(parts)


def _affected(vulnerability: Vulnerability) -> str:
    """One label per product, its version texts verbatim, at most 3 (+N more)."""
    labels: list[str] = []
    versions: dict[str, list[str]] = {}
    for sourced in vulnerability.affected:
        affected = sourced.value
        # The title rule: the vendor is dropped when the product name already
        # contains it. The version text is source text, never parsed here.
        label = _product_label(affected.product, affected.vendor)
        if label not in versions:
            labels.append(label)
            versions[label] = []
        text = _one_line(affected.version_text or "")
        if text and text not in versions[label]:
            versions[label].append(text)
    parts: list[str] = []
    for label in labels:
        rows = versions[label]
        if not rows:
            parts.append(f"{label} versions unknown")
        elif len(rows) > 3:
            parts.append(f"{label} {', '.join(rows[:3])} (+{len(rows) - 3} more)")
        else:
            parts.append(f"{label} {', '.join(rows)}")
    return "; ".join(parts) if parts else UNKNOWN


def _summary(vulnerability: Vulnerability) -> str:
    text = (
        _one_line(vulnerability.descriptions[0].value)
        if vulnerability.descriptions
        else ""
    )
    return text or UNKNOWN


# --- HTML ------------------------------------------------------------------------
# RSS 2.0 `description` is entity-encoded HTML. Every value goes through `_h`,
# so source text shows literally (`<b>` stays text) and cannot inject markup;
# only the tags below are ours. The serializer then entity-encodes the whole
# fragment once more as XML text.


def _h(value: str) -> str:
    return html.escape(value, quote=False)


def _safe_url(value: str) -> str | None:
    """`value` when it is an absolute http(s) URL, else `None` (never a link)."""
    url = _one_line(value)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in _LINK_SCHEMES or not parts.netloc:
        return None
    return url


def _anchor(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{_h(text)}</a>'


def _field(label: str, value: str) -> str:
    """One labelled fact; `value` is HTML already."""
    return f"<b>{label}:</b> {value}"


def _sources(records: Sequence[RecordRef]) -> str:
    parts: list[str] = []
    for record in records:
        url = _safe_url(record.url) if record.url else None
        if url is not None:
            parts.append(_anchor(url, record.source))
        else:
            parts.append(
                _h(f"{record.source} {_one_line(record.url or record.record_id)}")
            )
    return ", ".join(parts) if parts else _h(UNKNOWN)


def _references(vulnerability: Vulnerability) -> str | None:
    urls = list(dict.fromkeys(_one_line(r.value) for r in vulnerability.references))
    if not urls:
        return None
    items = []
    for value in urls[:_MAX_REFERENCES]:
        url = _safe_url(value)
        items.append(f"<li>{_anchor(url, url) if url else _h(value)}</li>")
    if len(urls) > _MAX_REFERENCES:
        items.append(f"<li>and {len(urls) - _MAX_REFERENCES} more</li>")
    return "\n".join(["<p><b>References:</b></p>", "<ul>", *items, "</ul>"])


def _description(entry: FeedEntry, records: Sequence[RecordRef]) -> str:
    """The item body: an HTML fragment, one fact per line.

    The first `<p>` holds what a reader must see even when the description is
    cut (Slack's RSS app keeps only the first block-level elements and shows
    about the first `SLACK_PREVIEW_CHARS` plain-text characters): the facts
    line, the matched products and the full description, never cut here.
    """
    vulnerability = entry.vulnerability
    ids_lines = [
        _field("IDs", _h(_ids(vulnerability))),
        _field("CVSS", _h(_cvss(vulnerability))),
    ]
    # The facts line already says `exploited: yes`; the detail is only useful
    # when the vulnerability is known-exploited.
    if vulnerability.is_known_exploited:
        ids_lines.append(_field("Known exploited", _h(_kev(vulnerability))))
    blocks = [
        "<p>"
        + "<br/>\n".join(
            [
                _h(_facts(entry)),
                _field("Matched", _h(_matched(entry))),
                _field("Description", _h(_summary(vulnerability))),
            ]
        )
        + "</p>",
        f"<p>{_field('Affected', _h(_affected(vulnerability)))}</p>",
        "<p>" + "<br/>\n".join(ids_lines) + "</p>",
        f"<p>{_field('Sources', _sources(records))}</p>",
    ]
    references = _references(vulnerability)
    if references is not None:
        blocks.append(references)
    return "\n".join(blocks)


def _text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = _xml_text(value)
    return element


def _item(channel: ET.Element, entry: FeedEntry, endpoint: Endpoint) -> None:
    records = _ordered_records(entry.vulnerability)
    item = ET.SubElement(channel, "item")
    _text(item, "title", _title(entry, endpoint))
    link = _link(records)
    if link is not None:
        _text(item, "link", link)
    _text(item, "description", _description(entry, records))
    guid = _text(item, "guid", entry.guid)
    guid.set("isPermaLink", "false")
    _text(item, "pubDate", _rfc822(entry.published_at))


def render_feed(endpoint: Endpoint, entries: Sequence[FeedEntry]) -> bytes:
    """The RSS 2.0 document of `endpoint`; `entries` come newest first."""
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    _text(channel, "title", f"cvestream: {endpoint.name}")
    _text(channel, "link", CHANNEL_LINK)
    _text(
        channel,
        "description",
        f"Vulnerabilities matching the products of {endpoint.name}",
    )
    _text(channel, "language", "en")
    if entries:
        # The newest item, never the render time: that would change the ETag
        # at every run.
        _text(channel, "lastBuildDate", _rfc822(max(e.published_at for e in entries)))
    for entry in entries:
        _item(channel, entry, endpoint)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
