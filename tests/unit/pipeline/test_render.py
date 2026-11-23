import html
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import respx

from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.matcher import PassThrough, match
from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    RecordRef,
    Sourced,
    Vulnerability,
)
from cvestream.domain.severity import Band, band_of, max_score
from cvestream.http import HttpClient
from cvestream.pipeline.correlate import build_vulnerability
from cvestream.pipeline.normalize import normalize
from cvestream.pipeline.render import (
    CHANNEL_LINK,
    GROUP_MEMBER_VERSION_CHARS,
    SLACK_PREVIEW_CHARS,
    FeedEntry,
    count_items,
    etag_of,
    render_feed,
)
from cvestream.sources.euvd import BASE_URL, EuvdAdapter
from tests.conftest import FakeClock

NOW = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
ENDPOINT = Endpoint(id="team-php", name="PHP team", products=(ProductEntry("php"),))
EUVD_URL = "https://euvd.enisa.europa.eu/vulnerability/EUVD-2026-41234"
CISA_URL = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
FIXTURES = Path(__file__).parents[2] / "fixtures"
DOT = " \N{MIDDLE DOT} "
FIRE = "\N{FIRE}"
RED_CIRCLE = "\N{LARGE RED CIRCLE}"
TRIANGLE = "\N{UP-POINTING RED TRIANGLE}"
SQUARED_NEW = "\N{SQUARED NEW}"


def _sourced[T](value: T, source: str = "euvd") -> Sourced[T]:
    return Sourced(value, source, NOW)


def _full_vulnerability() -> Vulnerability:
    return Vulnerability(
        primary_id="CVE-2026-1234",
        aliases=frozenset({"EUVD-2026-41234", "CVE-2026-1234", "CVE-2026-0999"}),
        records=(
            RecordRef("cisa_kev", "CVE-2026-1234", CISA_URL),
            RecordRef("euvd", "EUVD-2026-41234", EUVD_URL),
            RecordRef("custom", "X-1", None),
        ),
        descriptions=(
            _sourced("First   description\nwith lines"),
            _sourced("Second description"),
        ),
        scores=(
            _sourced(CvssScore(8.1, "3.1")),
            _sourced(None, "cisa_kev"),
            _sourced(CvssScore(5.0), "other"),
        ),
        kev=(
            _sourced(KevEntry(KevCatalog.EU_KEV), "eu_kev"),
            _sourced(
                KevEntry(
                    KevCatalog.CISA_KEV,
                    known_ransomware_use=True,
                    added_at=datetime(2026, 10, 24, tzinfo=UTC),
                ),
                "cisa",
            ),
        ),
        affected=(
            _sourced(AffectedProduct("Chrome", "Google", "< 141.0.7390.122")),
            _sourced(AffectedProduct("product", "vendor", None)),
            _sourced(AffectedProduct("Chrome", "Google", "< 141.0.7390.122")),
            _sourced(AffectedProduct("lonely")),
        ),
        references=(
            *(_sourced(f"https://example.test/ref/{i}") for i in range(12)),
            _sourced("https://example.test/ref/0", "cisa_kev"),
        ),
        published_at=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
        # 01:30 UTC on the 26th: the day is taken in UTC.
        updated_at=datetime(2026, 10, 25, 23, 30, tzinfo=timezone(timedelta(hours=-2))),
    )


def _entry(**changes: object) -> FeedEntry:
    entry = FeedEntry(
        guid="cvestream:team-php:CVE-2026-1234:new",
        kind="new",
        primary_id="CVE-2026-1234",
        band=Band.HIGH,
        is_known_exploited=False,
        products=("php", "nodejs"),
        labels=frozenset(),
        published_at=NOW,
        vulnerability=_full_vulnerability(),
    )
    return replace(entry, **changes)


def _bare(**changes: object) -> FeedEntry:
    return _entry(
        vulnerability=Vulnerability(primary_id="CVE-2026-1", aliases=frozenset()),
        **changes,
    )


def _parse(xml: bytes) -> ET.Element:
    return ET.fromstring(xml)  # noqa: S314 - our own output


def _item(entry: FeedEntry) -> ET.Element:
    item = _parse(render_feed(ENDPOINT, [entry])).find("channel/item")
    assert item is not None
    return item


_TAG = re.compile(r"<[^>]+>")


def _html(entry: FeedEntry) -> str:
    description = _item(entry).findtext("description")
    assert description is not None
    return description


def _lines(entry: FeedEntry) -> list[str]:
    """The description as a reader shows it: tags dropped, entities decoded."""
    text = html.unescape(_TAG.sub("", _html(entry)))
    return [line for line in text.split("\n") if line]


def _preview(entry: FeedEntry) -> str:
    """What Slack's RSS app shows: the first `SLACK_PREVIEW_CHARS` plain chars."""
    text = html.unescape(_TAG.sub("", _html(entry)))
    return text[:SLACK_PREVIEW_CHARS]


def test_the_full_description_one_fact_per_line() -> None:
    assert _lines(_entry(labels=frozenset({PassThrough.VERSIONS_UNKNOWN}))) == [
        f"high{DOT}CVSS 8.1{DOT}exploited: yes{DOT}published 2026-10-20"
        f"{DOT}updated 2026-10-26",
        "Matched: php, nodejs (versions unknown)",
        "Description: First description with lines",
        "Affected: Google Chrome < 141.0.7390.122; vendor product versions unknown;"
        " lonely versions unknown",
        "IDs: CVE-2026-0999, CVE-2026-1234, EUVD-2026-41234",
        "CVSS: 8.1 (CVSS 3.1, euvd); unknown (cisa_kev); 5.0 (CVSS unknown, other)",
        "Known exploited: CISA KEV (added 2026-10-24, ransomware use: known),"
        " EU KEV (added unknown, ransomware use: unknown)",
        "Sources: euvd, cisa_kev, custom X-1",
        "References:",
        *(f"https://example.test/ref/{i}" for i in range(10)),
        "and 2 more",
    ]


def test_the_description_is_an_html_fragment() -> None:
    assert _html(_entry()) == "\n".join(
        [
            f"<p>high{DOT}CVSS 8.1{DOT}exploited: yes{DOT}published 2026-10-20"
            f"{DOT}updated 2026-10-26<br/>",
            "<b>Matched:</b> php, nodejs<br/>",
            "<b>Description:</b> First description with lines</p>",
            "<p><b>Affected:</b> Google Chrome &lt; 141.0.7390.122;"
            " vendor product versions unknown; lonely versions unknown</p>",
            "<p><b>IDs:</b> CVE-2026-0999, CVE-2026-1234, EUVD-2026-41234<br/>",
            "<b>CVSS:</b> 8.1 (CVSS 3.1, euvd); unknown (cisa_kev);"
            " 5.0 (CVSS unknown, other)<br/>",
            "<b>Known exploited:</b> CISA KEV (added 2026-10-24,"
            " ransomware use: known), EU KEV (added unknown,"
            " ransomware use: unknown)</p>",
            f'<p><b>Sources:</b> <a href="{EUVD_URL}">euvd</a>,'
            f' <a href="{CISA_URL}">cisa_kev</a>, custom X-1</p>',
            "<p><b>References:</b></p>",
            "<ul>",
            *(
                f'<li><a href="https://example.test/ref/{i}">'
                f"https://example.test/ref/{i}</a></li>"
                for i in range(10)
            ),
            "<li>and 2 more</li>",
            "</ul>",
        ]
    )


def test_only_http_urls_become_links() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        records=(
            RecordRef("euvd", "EUVD-1", "javascript:alert(1)"),
            RecordRef("cisa_kev", "CVE-1", 'https://a.test/?q="x"&y=<1>'),
        ),
        references=(
            _sourced("javascript:alert(1)"),
            _sourced("ftp://files.test/x"),
            _sourced("https:no-host"),
            _sourced("HTTPS://b.test/ok"),
        ),
    )
    body = _html(_entry(vulnerability=vulnerability))
    assert 'href="javascript' not in body
    assert 'href="ftp' not in body
    assert 'href="https:no-host' not in body
    assert "<p><b>Sources:</b> euvd javascript:alert(1)," in body
    assert (
        '<a href="https://a.test/?q=&quot;x&quot;&amp;y=&lt;1&gt;">cisa_kev</a>' in body
    )
    assert "<li>javascript:alert(1)</li>" in body
    assert "<li>ftp://files.test/x</li>" in body
    assert "<li>https:no-host</li>" in body
    assert '<li><a href="HTTPS://b.test/ok">HTTPS://b.test/ok</a></li>' in body


def test_unknown_everywhere_data_is_missing() -> None:
    assert _lines(_bare()) == [
        f"unknown{DOT}CVSS unknown{DOT}exploited: no{DOT}published unknown",
        "Matched: php, nodejs",
        "Description: unknown",
        "Affected: unknown",
        "IDs: unknown",
        "CVSS: unknown",
        "Sources: unknown",
    ]


def test_unknown_score_that_was_let_through_is_said() -> None:
    lines = _lines(_bare(labels=frozenset({PassThrough.SCORE_UNKNOWN})))
    assert (
        f"unknown{DOT}CVSS unknown (no score; let through){DOT}exploited: no"
        f"{DOT}published unknown" in lines
    )


def test_no_description_line_ever_shows_zero_or_empty_values() -> None:
    for line in _lines(_bare()):
        assert not line.endswith(": ")
        assert ": 0" not in line
        assert "n/a" not in line


def test_the_severity_is_recomputed_from_current_scores() -> None:
    entry = _entry(band=Band.LOW)
    assert f"high{DOT}CVSS 8.1{DOT}exploited: yes" in _lines(entry)[0]
    assert _item(entry).findtext("title") == ("CVE-2026-1234 in php: severity low")


def test_a_score_is_shown_with_one_decimal() -> None:
    vulnerability = replace(
        _full_vulnerability(), scores=(_sourced(CvssScore(9.0, "4.0")),)
    )
    lines = _lines(_entry(vulnerability=vulnerability))
    assert lines[0].startswith(f"critical{DOT}CVSS 9.0{DOT}")
    assert "CVSS: 9.0 (CVSS 4.0, euvd)" in lines


def test_the_reference_line_is_omitted_without_references() -> None:
    assert not any(line.startswith("References") for line in _lines(_bare()))


def test_few_references_have_no_more_suffix() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        references=(_sourced("https://a.test"), _sourced("https://a.test", "x")),
    )
    assert _lines(_entry(vulnerability=vulnerability))[-2:] == [
        "References:",
        "https://a.test",
    ]


@pytest.mark.parametrize(
    ("kind", "known", "title"),
    [
        ("new", False, "CVE-2026-1234 in php: severity high"),
        ("new", True, f"{FIRE} CVE-2026-1234 in php: severity high, EXPLOITED"),
        (
            "kev",
            True,
            f"{FIRE} [UPDATE] CVE-2026-1234 in php: now EXPLOITED, severity high",
        ),
        (
            "band-high",
            False,
            f"{TRIANGLE} [UPDATE] CVE-2026-1234 in php: severity raised to high",
        ),
        (
            "band-high",
            True,
            f"{FIRE}{TRIANGLE} [UPDATE] CVE-2026-1234 in php: severity raised to"
            " high, EXPLOITED",
        ),
        ("future", False, "[UPDATE] CVE-2026-1234 in php: severity high"),
    ],
)
def test_titles(kind: str, known: bool, title: str) -> None:
    entry = _entry(kind=kind, is_known_exploited=known)
    assert _item(entry).findtext("title") == title


def test_the_title_names_the_first_matched_product() -> None:
    assert _item(_entry(products=("nodejs", "php"))).findtext("title") == (
        "CVE-2026-1234 in nodejs: severity high"
    )


def _with_affected(*products: AffectedProduct) -> Vulnerability:
    return replace(_full_vulnerability(), affected=tuple(_sourced(p) for p in products))


@pytest.mark.parametrize(
    ("products", "name"),
    [
        ((AffectedProduct("PHP", "PHP Group", "8.1.0 <8.2.29"),), "PHP Group PHP"),
        ((AffectedProduct("php-fpm", "PHP Group"),), "PHP Group php-fpm"),
        ((AffectedProduct("PHP", "PHP"),), "PHP"),
        ((AffectedProduct("php", None),), "php"),
        (
            (AffectedProduct("Login", "Acme"), AffectedProduct("PHP", "PHP Group")),
            "PHP Group PHP",
        ),
        (
            (AffectedProduct("Zend Engine", "PHP"), AffectedProduct("PHP", "Zend")),
            "Zend PHP",
        ),
        ((AffectedProduct("Zend Engine", "PHP"),), "PHP Zend Engine"),
    ],
    ids=[
        "vendor-and-product",
        "sub-product",
        "vendor-repeated",
        "no-vendor",
        "first-hit",
        "product-hit-first",
        "vendor-only-hit",
    ],
)
def test_the_title_names_the_matched_affected_product(
    products: tuple[AffectedProduct, ...], name: str
) -> None:
    entry = _entry(vulnerability=_with_affected(*products), is_known_exploited=True)
    assert _item(entry).findtext("title") == (
        f"{FIRE} CVE-2026-1234 in {name}: severity high, EXPLOITED"
    )


def test_a_vendor_written_with_hyphens_is_not_repeated() -> None:
    vulnerability = _with_affected(
        AffectedProduct("Wild Apricot Login", "wild-apricot")
    )
    endpoint = replace(ENDPOINT, products=(ProductEntry("wild apricot"),))
    entry = _entry(vulnerability=vulnerability, products=("wild apricot",))
    item = _parse(render_feed(endpoint, [entry])).find("channel/item")
    assert item is not None
    assert item.findtext("title") == (
        "CVE-2026-1234 in Wild Apricot Login: severity high"
    )


def test_the_title_uses_the_aliases_of_the_configured_entry() -> None:
    vulnerability = _with_affected(AffectedProduct("php-src", "PHP Group"))
    endpoint = replace(
        ENDPOINT, products=(ProductEntry("hypertext", aliases=("php-src",)),)
    )
    entry = _entry(vulnerability=vulnerability, products=("hypertext",))
    item = _parse(render_feed(endpoint, [entry])).find("channel/item")
    assert item is not None
    assert item.findtext("title") == (
        "CVE-2026-1234 in PHP Group php-src: severity high"
    )


def test_the_title_of_an_exact_entry_names_the_exact_product() -> None:
    vulnerability = _with_affected(
        AffectedProduct("php-fpm", "PHP Group"), AffectedProduct("PHP", "PHP Group")
    )
    exact = replace(ENDPOINT, products=(ProductEntry("php", exact=True),))
    entry = _entry(vulnerability=vulnerability, products=("php",))
    item = _parse(render_feed(exact, [entry])).find("channel/item")
    assert item is not None
    assert item.findtext("title") == "CVE-2026-1234 in PHP Group PHP: severity high"
    word = replace(ENDPOINT, products=(ProductEntry("php"),))
    item = _parse(render_feed(word, [entry])).find("channel/item")
    assert item is not None
    assert item.findtext("title") == (
        "CVE-2026-1234 in PHP Group php-fpm: severity high"
    )


def test_a_removed_entry_still_names_its_affected_product() -> None:
    vulnerability = _with_affected(AffectedProduct("Redis", "Redis Ltd"))
    entry = _entry(vulnerability=vulnerability, products=("redis",))
    assert _item(entry).findtext("title") == (
        "CVE-2026-1234 in Redis Ltd Redis: severity high"
    )


def test_without_a_matching_affected_product_the_entry_name_is_used() -> None:
    assert _item(_entry()).findtext("title") == "CVE-2026-1234 in php: severity high"
    assert _item(_bare()).findtext("title") == "CVE-2026-1234 in php: severity high"


def test_guid_is_the_stored_value_and_not_a_permalink() -> None:
    guid = _item(_entry(guid="stored-guid", primary_id="CVE-2026-9999")).find("guid")
    assert guid is not None
    assert guid.text == "stored-guid"
    assert guid.attrib == {"isPermaLink": "false"}


def test_pubdate_is_rfc_822_gmt() -> None:
    entry = _entry(published_at=datetime(2026, 10, 26, 20, 5, 7, tzinfo=UTC))
    assert _item(entry).findtext("pubDate") == "Mon, 26 Oct 2026 20:05:07 GMT"


def test_link_is_the_first_record_url_in_source_order() -> None:
    assert _item(_entry()).findtext("link") == EUVD_URL


def test_link_is_omitted_without_a_source_url() -> None:
    assert _item(_bare()).find("link") is None


def test_link_falls_back_to_a_later_record_with_a_url() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        records=(RecordRef("euvd", "E", None), RecordRef("cisa_kev", "C", CISA_URL)),
    )
    assert _item(_entry(vulnerability=vulnerability)).findtext("link") == CISA_URL


def test_the_channel_and_its_required_elements() -> None:
    root = _parse(render_feed(ENDPOINT, [_entry()]))
    assert root.tag == "rss"
    assert root.attrib == {"version": "2.0"}
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("title") == "cvestream: PHP team"
    assert channel.findtext("link") == CHANNEL_LINK
    assert channel.findtext("description") == (
        "Vulnerabilities matching the products of PHP team"
    )
    assert channel.findtext("language") == "en"
    assert channel.findtext("lastBuildDate") == "Mon, 26 Oct 2026 20:00:00 GMT"


def test_an_empty_feed_has_no_last_build_date() -> None:
    channel = _parse(render_feed(ENDPOINT, [])).find("channel")
    assert channel is not None
    assert channel.find("lastBuildDate") is None
    assert channel.find("item") is None
    assert channel.findtext("title") == "cvestream: PHP team"


def test_items_keep_the_given_order_and_last_build_is_the_newest() -> None:
    older = _entry(guid="older", published_at=NOW - timedelta(days=2))
    newer = _entry(guid="newer", published_at=NOW)
    for entries in ([newer, older], [older, newer]):
        channel = _parse(render_feed(ENDPOINT, entries)).find("channel")
        assert channel is not None
        assert [i.findtext("guid") for i in channel.findall("item")] == [
            e.guid for e in entries
        ]
        assert channel.findtext("lastBuildDate") == "Mon, 26 Oct 2026 20:00:00 GMT"


def test_the_same_inputs_give_the_same_bytes() -> None:
    assert render_feed(ENDPOINT, [_entry()]) == render_feed(ENDPOINT, [_entry()])


def test_the_document_is_utf_8_with_a_declaration() -> None:
    xml = render_feed(ENDPOINT, [_entry()])
    assert xml.startswith(b"<?xml version='1.0' encoding='utf-8'?>")


def test_markup_in_source_text_is_escaped_text() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        descriptions=(_sourced("<script>alert(1)</script> & <b>more</b>"),),
    )
    xml = render_feed(ENDPOINT, [_entry(vulnerability=vulnerability)])
    assert b"<script>" not in xml
    assert b"&lt;script&gt;" not in xml  # escaped twice: HTML, then XML
    assert b"&amp;lt;script&amp;gt;" in xml
    assert b"CDATA" not in xml
    entry = _entry(vulnerability=vulnerability)
    # A reader parsing the HTML shows the source text literally.
    assert (
        "<b>Description:</b> &lt;script&gt;alert(1)&lt;/script&gt;"
        " &amp; &lt;b&gt;more&lt;/b&gt;</p>"
    ) in _html(entry)
    assert "Description: <script>alert(1)</script> & <b>more</b>" in _lines(entry)


def test_markup_in_product_names_is_escaped_in_the_title_and_body() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        affected=(_sourced(AffectedProduct("php <img src=x onerror=1>", "A&B")),),
    )
    entry = _entry(vulnerability=vulnerability)
    item = _item(entry)
    # The title is plain text in RSS: XML-escaped once, by the serializer.
    assert item.findtext("title") == (
        "CVE-2026-1234 in A&B php <img src=x onerror=1>: severity high"
    )
    assert "A&amp;B php &lt;img src=x onerror=1&gt; versions unknown" in _html(entry)


def test_the_endpoint_name_is_escaped_too() -> None:
    endpoint = replace(ENDPOINT, name="R&D <team>")
    channel = _parse(render_feed(endpoint, [])).find("channel")
    assert channel is not None
    assert channel.findtext("title") == "cvestream: R&D <team>"


def test_forbidden_control_characters_are_removed_and_the_output_parses() -> None:
    dirty = "a\x00b\x0bc\x1fd\ud800e\ufffef\uffff\x7f"  # \x7f is allowed
    vulnerability = replace(_full_vulnerability(), descriptions=(_sourced(dirty),))
    entry = _entry(vulnerability=vulnerability, guid="g\x00uid")
    item = _item(entry)
    assert item.findtext("guid") == "guid"
    assert "Description: abcdef\x7f" in _lines(entry)


def test_the_edges_of_the_allowed_ranges_are_kept() -> None:
    kept = "\t\x20\ud7ff\ue000\ufffd\U00010000\U0010ffff"
    vulnerability = replace(_full_vulnerability(), descriptions=(_sourced(kept),))
    xml = render_feed(ENDPOINT, (_entry(vulnerability=vulnerability),))
    description = _parse(xml).findtext("channel/item/description")
    assert description is not None
    assert "\ud7ff\ue000\ufffd\U00010000\U0010ffff" in description


def test_valid_astral_characters_are_kept() -> None:
    vulnerability = replace(
        _full_vulnerability(), descriptions=(_sourced("rocket \U0001f680 é"),)
    )
    assert "Description: rocket \U0001f680 é" in _lines(
        _entry(vulnerability=vulnerability)
    )


def test_carriage_returns_do_not_become_extra_lines() -> None:
    vulnerability = replace(
        _full_vulnerability(), descriptions=(_sourced("one\r\ntwo\rthree"),)
    )
    assert "Description: one two three" in _lines(_entry(vulnerability=vulnerability))


def test_etag_is_quoted_and_follows_the_content() -> None:
    xml = render_feed(ENDPOINT, [_entry()])
    etag = etag_of(xml)
    assert etag.startswith('"')
    assert etag.endswith('"')
    assert len(etag) == 64 + 2
    assert etag_of(xml) == etag
    assert etag_of(xml + b" ") != etag
    assert etag_of(render_feed(ENDPOINT, [])) != etag


def test_a_kev_entry_of_one_catalog_is_listed_once() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        kev=(
            _sourced(
                KevEntry(
                    KevCatalog.CISA_KEV, added_at=datetime(2026, 9, 2, tzinfo=UTC)
                ),
                "cisa_kev",
            ),
            _sourced(
                KevEntry(
                    KevCatalog.CISA_KEV,
                    known_ransomware_use=True,
                    added_at=datetime(2026, 8, 30, tzinfo=UTC),
                ),
                "cisa_kev",
            ),
            _sourced(
                KevEntry(KevCatalog.CISA_KEV, known_ransomware_use=None), "cisa_kev"
            ),
        ),
    )
    # One part, the earliest addition and the recorded ransomware use.
    assert (
        "Known exploited: CISA KEV (added 2026-08-30, ransomware use: known)"
    ) in _lines(_entry(vulnerability=vulnerability))


def test_a_ransomware_use_of_false_is_not_reported_as_known() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        kev=(
            _sourced(KevEntry(KevCatalog.EU_KEV, known_ransomware_use=False), "eu_kev"),
        ),
    )
    assert "Known exploited: EU KEV (added unknown, ransomware use: no)" in _lines(
        _entry(vulnerability=vulnerability)
    )


# --- CERT-EU affected products are prose (story 2.1) ----------------------------

_CERT_EU_PROSE = (
    "The vulnerability CVE-2026-110264 affects the following versions of PHP: "
    "8.5 before 8.5.12"
)


def test_the_title_never_names_a_product_from_cert_eu_prose() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        affected=(_sourced(AffectedProduct(_CERT_EU_PROSE), "cert_eu"),),
    )
    entry = _entry(vulnerability=vulnerability, products=("php",))
    assert _item(entry).findtext("title") == "CVE-2026-1234 in php: severity high"


def test_the_title_names_the_euvd_product_next_to_cert_eu_prose() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        affected=(
            _sourced(AffectedProduct(_CERT_EU_PROSE), "cert_eu"),
            _sourced(AffectedProduct("PHP", "PHP Group", "8.5.* <8.5.12"), "euvd"),
        ),
    )
    entry = _entry(vulnerability=vulnerability, products=("php",))
    assert _item(entry).findtext("title") == (
        "CVE-2026-1234 in PHP Group PHP: severity high"
    )


def test_the_body_still_lists_the_cert_eu_affected_text() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        affected=(_sourced(AffectedProduct(_CERT_EU_PROSE), "cert_eu"),),
    )
    lines = _lines(_entry(vulnerability=vulnerability, products=("php",)))
    assert f"Affected: {_CERT_EU_PROSE} versions unknown" in lines


def test_cert_eu_records_come_after_the_other_sources_in_the_body() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        records=(
            RecordRef("cert_eu", "2026-018:CVE-2026-1234", None),
            RecordRef("cisa_kev", "CVE-2026-1234", CISA_URL),
            RecordRef("euvd", "EUVD-2026-41234", EUVD_URL),
        ),
    )
    html_body = _html(_entry(vulnerability=vulnerability))
    assert html_body.index("euvd") < html_body.index("cisa_kev")
    assert html_body.index("cisa_kev") < html_body.index("cert_eu")


# --- story 5.1: facts line, compact Affected, first-block layout, budget ---


def test_updated_is_left_out_on_the_same_utc_day() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        published_at=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
        updated_at=datetime(2026, 10, 20, 23, 0, tzinfo=UTC),
    )
    facts = _lines(_entry(vulnerability=vulnerability))[0]
    assert "published 2026-10-20" in facts
    assert "updated" not in facts


def test_a_non_utc_updated_at_on_the_same_utc_day_is_left_out() -> None:
    # 2026-10-25 23:30 at UTC-2 is 2026-10-26 01:30 UTC: the same UTC day.
    vulnerability = replace(
        _full_vulnerability(),
        published_at=datetime(2026, 10, 26, 1, 0, tzinfo=UTC),
        updated_at=datetime(2026, 10, 25, 23, 30, tzinfo=timezone(timedelta(hours=-2))),
    )
    facts = _lines(_entry(vulnerability=vulnerability))[0]
    assert "published 2026-10-26" in facts
    assert "updated" not in facts


def test_updated_on_another_day_is_kept() -> None:
    facts = _lines(_entry())[0]
    assert "published 2026-10-20" in facts
    assert "updated 2026-10-26" in facts


def test_the_affected_line_caps_at_three_versions_with_a_more_suffix() -> None:
    versions = [f"<= {i}" for i in range(5)]
    vulnerability = _with_affected(
        *(AffectedProduct("Tomcat", "Apache Software Foundation", v) for v in versions)
    )
    line = next(
        line_
        for line_ in _lines(_entry(vulnerability=vulnerability))
        if line_.startswith("Affected:")
    )
    assert line == (
        "Affected: Apache Software Foundation Tomcat <= 0, <= 1, <= 2 (+2 more)"
    )


def test_the_affected_line_drops_a_vendor_the_product_already_names() -> None:
    vulnerability = _with_affected(
        AffectedProduct("Wild Apricot Login", "wild-apricot", "< 1.2")
    )
    line = next(
        line_
        for line_ in _lines(_entry(vulnerability=vulnerability))
        if line_.startswith("Affected:")
    )
    assert line == "Affected: Wild Apricot Login < 1.2"


def test_the_affected_line_merges_sources_and_deduplicates_under_one_label() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        affected=(
            Sourced(AffectedProduct("PHP", "PHP Group", "8.5.* <8.5.12"), "euvd", NOW),
            Sourced(
                AffectedProduct("PHP", "PHP Group", "8.5.* <8.5.12"), "cert_eu", NOW
            ),
            Sourced(
                AffectedProduct("PHP", "PHP Group", "8.4.* <8.4.27"), "cisa_kev", NOW
            ),
            Sourced(AffectedProduct("PHP", "PHP Group", None), "euvd", NOW),
        ),
    )
    line = next(
        line_
        for line_ in _lines(_entry(vulnerability=vulnerability))
        if line_.startswith("Affected:")
    )
    # one label, distinct version texts, the row without version text adds nothing
    assert line == "Affected: PHP Group PHP 8.5.* <8.5.12, 8.4.* <8.4.27"


def test_the_affected_line_groups_labels_case_insensitively() -> None:
    # D3 (wixyvir/cvestream#51): the grouping key is the case-folded label and
    # the first-seen spelling is displayed; a row naming the vendor stays a
    # separate label.
    vulnerability = _with_affected(
        AffectedProduct("apache tomcat", None, "<= 9.0.1"),
        AffectedProduct("Apache Tomcat", None, "<= 9.0.2"),
        AffectedProduct("Apache Tomcat", "Apache Software Foundation", "<= 9.0.3"),
    )
    line = next(
        line_
        for line_ in _lines(_entry(vulnerability=vulnerability))
        if line_.startswith("Affected:")
    )
    assert line == (
        "Affected: apache tomcat <= 9.0.1, <= 9.0.2;"
        " Apache Software Foundation Apache Tomcat <= 9.0.3"
    )


def test_the_affected_line_says_versions_unknown_only_without_any_version_text() -> (
    None
):
    vulnerability = _with_affected(AffectedProduct("lonely"))
    line = next(
        line_
        for line_ in _lines(_entry(vulnerability=vulnerability))
        if line_.startswith("Affected:")
    )
    assert line == "Affected: lonely versions unknown"


def test_no_kev_line_when_not_known_exploited() -> None:
    vulnerability = replace(_full_vulnerability(), kev=())
    assert not any(
        line_.startswith("Known exploited")
        for line_ in _lines(_entry(vulnerability=vulnerability))
    )


def test_the_kev_detail_line_appears_when_known_exploited() -> None:
    line = next(
        line_ for line_ in _lines(_entry()) if line_.startswith("Known exploited")
    )
    assert line == (
        "Known exploited: CISA KEV (added 2026-10-24, ransomware use: known),"
        " EU KEV (added unknown, ransomware use: unknown)"
    )


def test_the_first_paragraph_holds_facts_matched_and_description() -> None:
    # Slack's RSS app appears to keep only the first block-level elements
    # (story 5.1 Task 1: consistent with H1/H2, inferred from a local replay
    # and the ~208-character 2026-11-19 posts): the description must live in
    # the first <p>.
    body = _html(_entry())
    first_block = body.split("</p>")[0]
    assert first_block.startswith("<p>")
    assert "exploited:" in first_block
    assert "<b>Matched:</b>" in first_block
    assert "<b>Description:</b>" in first_block


def test_a_description_longer_than_the_budget_appears_in_full() -> None:
    long_text = "word " * 200  # 1000 chars, longer than SLACK_PREVIEW_CHARS
    vulnerability = replace(_full_vulnerability(), descriptions=(_sourced(long_text),))
    body = _html(_entry(vulnerability=vulnerability))
    # `_summary` collapses whitespace: 200 words, all of them, never cut
    assert " ".join(["word"] * 200) in html.unescape(_TAG.sub("", body))
    assert len(_preview(_entry(vulnerability=vulnerability))) == SLACK_PREVIEW_CHARS


async def _no_sleep(_delay: float) -> None:
    return None


@pytest.mark.anyio
async def test_the_tomcat_fixture_fits_the_slack_preview_window(
    respx_mock: respx.MockRouter,
) -> None:
    # Recorded 2026-11-21: CVE-2026-95517 / EUVD-2026-130760, five affected rows,
    # CVSS 9.1, not exploited, a 772-character description.
    respx_mock.get(f"{BASE_URL}/kev/dump").respond(
        200, content=(FIXTURES / "euvd" / "euvd_kev_dump.json").read_bytes()
    )
    respx_mock.get(f"{BASE_URL}/search").respond(
        200,
        content=(
            FIXTURES / "euvd" / "euvd_tomcat_five_affected_rows.json"
        ).read_bytes(),
    )
    clock = FakeClock(datetime(2026, 11, 21, 12, 0, tzinfo=UTC))
    async with HttpClient(sleep=_no_sleep, random=lambda: 0.0, max_attempts=2) as http:
        result = await EuvdAdapter(http, clock, sleep=_no_sleep).fetch(None)
    # Only the search record: `build_vulnerability` merges what it is given,
    # the alias-based split happens in `correlate()`.
    record = next(r for r in result.records if r.record_id == "EUVD-2026-130760")
    normalized = normalize(record)
    assert normalized is not None
    vulnerability = build_vulnerability([normalized])
    endpoint = Endpoint(
        id="team-tomcat", name="Tomcat", products=(ProductEntry("tomcat"),)
    )
    outcome = match(vulnerability, endpoint)
    assert outcome is not None
    entry = FeedEntry(
        guid=f"cvestream:team-tomcat:{vulnerability.primary_id}:new",
        kind="new",
        primary_id=vulnerability.primary_id,
        band=band_of(max_score(vulnerability.scores)),
        is_known_exploited=vulnerability.is_known_exploited,
        products=tuple(m.entry.name for m in outcome.matches),
        labels=outcome.labels,
        published_at=clock.now(),
        vulnerability=vulnerability,
    )
    lines = _lines(entry)
    assert lines[0] == (
        f"critical{DOT}CVSS 9.1{DOT}exploited: no{DOT}published 2026-11-19"
        f"{DOT}updated 2026-11-20"
    )
    assert lines[1] == "Matched: tomcat"
    assert next(line_ for line_ in lines if line_.startswith("Affected:")) == (
        "Affected: Apache Software Foundation Apache Tomcat 10.1.0-M1 ≤10.1.61,"
        " 11.0.0-M1 ≤11.0.27, 7.0.100 ≤7.0.109 (+2 more)"
    )
    # AC 3: the first SLACK_PREVIEW_CHARS plain-text characters hold the band,
    # exploited: no, the matched product and the first 40 description chars.
    preview = _preview(entry)
    description = " ".join(vulnerability.descriptions[0].value.split())
    assert "critical" in preview
    assert "exploited: no" in preview
    assert "Matched: tomcat" in preview
    assert description[:40] in preview


# --- story 5.1: title markers (AC 4, 5) ---


def _title_of(entry: FeedEntry, endpoint: Endpoint = ENDPOINT) -> str:
    item = _parse(render_feed(endpoint, [entry])).find("channel/item")
    assert item is not None
    title = item.findtext("title")
    assert title is not None
    return title


def test_the_four_golden_titles() -> None:
    tomcat_vulnerability = _with_affected(
        AffectedProduct("Apache Tomcat", "Apache Software Foundation", "10.1.0-M1")
    )
    tomcat_endpoint = replace(ENDPOINT, products=(ProductEntry("tomcat"),))
    assert _title_of(
        _entry(
            primary_id="CVE-2026-95517",
            band=Band.CRITICAL,
            is_known_exploited=True,
            is_fresh=True,
            products=("tomcat",),
            vulnerability=tomcat_vulnerability,
        ),
        tomcat_endpoint,
    ) == (
        f"{FIRE}{RED_CIRCLE}{SQUARED_NEW} CVE-2026-95517"
        " in Apache Software Foundation Apache Tomcat: severity critical, EXPLOITED"
    )
    openssl_endpoint = replace(ENDPOINT, products=(ProductEntry("openssl"),))
    assert (
        _title_of(
            _entry(
                primary_id="CVE-2026-107494",
                band=Band.UNKNOWN,
                is_fresh=True,
                products=("openssl",),
                vulnerability=_with_affected(AffectedProduct("OpenSSL")),
            ),
            openssl_endpoint,
        )
        == f"{SQUARED_NEW} CVE-2026-107494 in OpenSSL: severity unknown"
    )
    assert _title_of(
        _entry(
            primary_id="CVE-2026-118028",
            kind="band-critical",
            band=Band.CRITICAL,
            is_fresh=False,
            products=("tomcat",),
            vulnerability=tomcat_vulnerability,
        ),
        tomcat_endpoint,
    ) == (
        f"{RED_CIRCLE}{TRIANGLE} [UPDATE] CVE-2026-118028"
        " in Apache Software Foundation Apache Tomcat: severity raised to critical"
    )
    assert _title_of(
        _entry(
            kind="kev",
            band=Band.HIGH,
            is_known_exploited=True,
            is_fresh=False,
            vulnerability=_with_affected(AffectedProduct("PHP", "PHP Group")),
        )
    ) == (
        f"{FIRE} [UPDATE] CVE-2026-1234 in PHP Group PHP: now EXPLOITED, severity high"
    )


def test_each_marker_alone() -> None:
    # critical, not exploited, not fresh, new: only the red circle
    assert _title_of(_entry(band=Band.CRITICAL)) == (
        f"{RED_CIRCLE} CVE-2026-1234 in php: severity critical"
    )
    # fresh, unknown band, new, not exploited: only the squared new
    assert _title_of(_entry(band=Band.UNKNOWN, is_fresh=True)) == (
        f"{SQUARED_NEW} CVE-2026-1234 in php: severity unknown"
    )


def test_band_high_gets_the_triangle_but_not_the_red_circle() -> None:
    assert _title_of(_entry(kind="band-high", band=Band.HIGH)).startswith(TRIANGLE)
    assert RED_CIRCLE not in _title_of(_entry(kind="band-high", band=Band.HIGH))


def test_band_critical_gets_both_marks_in_order() -> None:
    title = _title_of(_entry(kind="band-critical", band=Band.CRITICAL))
    assert title.startswith(f"{RED_CIRCLE}{TRIANGLE} ")


def test_band_none_and_low_rises_get_no_triangle_but_medium_does() -> None:
    # D1 (wixyvir/cvestream#51): 🔺 only for a rise to `medium` or above; the
    # `[UPDATE] … severity raised to …` words stay either way.
    for band in (Band.NONE, Band.LOW):
        title = _title_of(_entry(kind=f"band-{band.value}", band=band))
        assert title == (
            f"[UPDATE] CVE-2026-1234 in php: severity raised to {band.value}"
        )
    title = _title_of(_entry(kind="band-medium", band=Band.MEDIUM))
    assert title == (
        f"{TRIANGLE} [UPDATE] CVE-2026-1234 in php: severity raised to medium"
    )


def test_all_four_markers_together_in_order() -> None:
    # Not reachable in the domain (`kev` wins over `band-*` there), but the
    # marker order is a render contract: pin it with a hand-built entry.
    title = _title_of(
        _entry(
            kind="band-critical",
            band=Band.CRITICAL,
            is_known_exploited=True,
            is_fresh=True,
        )
    )
    assert title == (
        f"{FIRE}{RED_CIRCLE}{TRIANGLE}{SQUARED_NEW} [UPDATE] CVE-2026-1234"
        " in php: severity raised to critical, EXPLOITED"
    )


def test_is_fresh_none_or_false_gives_no_marker_and_no_leading_space() -> None:
    for fresh in (None, False):
        title = _title_of(_entry(band=Band.UNKNOWN, is_fresh=fresh))
        assert title == "CVE-2026-1234 in php: severity unknown"
        assert not title.startswith(" ")


# --- story 5.2: group items (stored group_guid rows collapse into one item) ---

TOMCAT_ENDPOINT = replace(
    ENDPOINT, id="team-tomcat", name="Tomcat team", products=(ProductEntry("tomcat"),)
)
GROUP_GUID = "cvestream:team-tomcat:group-new:0123456789abcdef"


def _tomcat_vulnerability(
    primary_id: str,
    score: float | None,
    versions: tuple[str, ...] = ("10.1.0-M1 ≤10.1.61",),
    *,
    kev: bool = False,
    with_record: bool = True,
    url: str | None = None,
) -> Vulnerability:
    records = (
        (
            RecordRef(
                "euvd", f"EUVD-{primary_id}", url or f"https://euvd.test/{primary_id}"
            ),
        )
        if with_record
        else ()
    )
    return Vulnerability(
        primary_id=primary_id,
        aliases=frozenset({primary_id}),
        records=records,
        scores=() if score is None else (_sourced(CvssScore(score)),),
        kev=(_sourced(KevEntry(KevCatalog.CISA_KEV)),) if kev else (),
        affected=tuple(
            _sourced(AffectedProduct("Apache Tomcat", "Apache Software Foundation", v))
            for v in versions
        ),
    )


def _member(
    primary_id: str,
    band: Band,
    score: float | None,
    group_guid: str = GROUP_GUID,
    *,
    kind: str = "new",
    fresh: bool | None = False,
    versions: tuple[str, ...] = ("10.1.0-M1 ≤10.1.61",),
    published_at: datetime = NOW,
    with_record: bool = True,
) -> FeedEntry:
    return FeedEntry(
        guid=f"cvestream:team-tomcat:{primary_id}:{kind}",
        kind=kind,
        primary_id=primary_id,
        band=band,
        is_known_exploited=False,
        products=("tomcat",),
        labels=frozenset(),
        published_at=published_at,
        vulnerability=_tomcat_vulnerability(
            primary_id, score, versions, with_record=with_record
        ),
        is_fresh=fresh,
        group_guid=group_guid,
    )


def _five_member_burst() -> list[FeedEntry]:
    return [
        _member(
            "CVE-2026-95517",
            Band.CRITICAL,
            9.1,
            fresh=True,
            versions=tuple(f"≤10.1.{i}" for i in range(61, 66)),
        ),
        _member(
            "CVE-2026-95518",
            Band.HIGH,
            7.5,
            versions=("11.0.0-M1 ≤11.0.27", "≤ 9.0.100"),
        ),
        _member("CVE-2026-95519", Band.HIGH, 7.4),
        _member("CVE-2026-95520", Band.HIGH, 7.0),
        _member("CVE-2026-95521", Band.MEDIUM, 5.0),
    ]


def _member_with_affected(
    primary_id: str,
    band: Band,
    score: float | None,
    affected: tuple[Sourced[AffectedProduct], ...],
    group_guid: str = GROUP_GUID,
) -> FeedEntry:
    """A group member whose affected label is not the Tomcat fixture's."""
    return FeedEntry(
        guid=f"cvestream:team-tomcat:{primary_id}:new",
        kind="new",
        primary_id=primary_id,
        band=band,
        is_known_exploited=False,
        products=("tomcat",),
        labels=frozenset(),
        published_at=NOW,
        vulnerability=Vulnerability(
            primary_id=primary_id,
            aliases=frozenset({primary_id}),
            records=(
                RecordRef(
                    "euvd", f"EUVD-{primary_id}", f"https://euvd.test/{primary_id}"
                ),
            ),
            scores=() if score is None else (_sourced(CvssScore(score)),),
            affected=affected,
        ),
        is_fresh=False,
        group_guid=group_guid,
    )


def _group_item(
    entries: list[FeedEntry], endpoint: Endpoint = TOMCAT_ENDPOINT
) -> ET.Element:
    items = _parse(render_feed(endpoint, entries)).findall("channel/item")
    assert len(items) == 1
    return items[0]


def _group_lines(item: ET.Element) -> list[str]:
    description = item.findtext("description")
    assert description is not None
    text = html.unescape(_TAG.sub("", description))
    return [line for line in text.split("\n") if line]


def test_the_golden_group_title_and_body() -> None:
    item = _group_item(_five_member_burst())
    assert item.findtext("title") == (
        f"{RED_CIRCLE}{SQUARED_NEW} Apache Software Foundation Apache Tomcat: "
        "5 new vulnerabilities, worst critical (1 critical, 3 high, 1 medium)"
    )
    assert _group_lines(item) == [
        "CVE-2026-95517 · critical 9.1 · exploited: no · ≤10.1.61 (+4 more)",
        "CVE-2026-95518 · high 7.5 · exploited: no · 11.0.0-M1 ≤11.0.27 (+1 more)",
        "CVE-2026-95519 · high 7.4 · exploited: no · 10.1.0-M1 ≤10.1.61",
        "CVE-2026-95520 · high 7.0 · exploited: no · 10.1.0-M1 ≤10.1.61",
        "CVE-2026-95521 · medium 5.0 · exploited: no · 10.1.0-M1 ≤10.1.61",
    ]
    description = item.findtext("description")
    assert description is not None
    # one <p> only, each ID linked to its member's first source link
    assert description.count("<p>") == 1
    assert description.startswith("<p>")
    for member in _five_member_burst():
        assert (
            f'<a href="https://euvd.test/{member.primary_id}">{member.primary_id}</a>'
        ) in description
    assert description.endswith("</p>")


def test_the_group_item_carries_the_group_guid_and_the_members_pubdate() -> None:
    item = _group_item(_five_member_burst())
    guid = item.find("guid")
    assert guid is not None
    assert guid.text == GROUP_GUID
    assert guid.attrib == {"isPermaLink": "false"}
    assert item.findtext("pubDate") == "Mon, 26 Oct 2026 20:00:00 GMT"
    assert item.findtext("link") == "https://euvd.test/CVE-2026-95517"


def test_the_golden_update_titles_with_part_numbering() -> None:
    # a burst of 9 band members (7 critical, 2 high) splits 5 + 4
    part1 = "cvestream:team-tomcat:group-band:aaaaaaaaaaaaaaaa"
    part2 = "cvestream:team-tomcat:group-band:bbbbbbbbbbbbbbbb"
    members = [
        *[
            _member(f"CVE-2026-{n}", Band.CRITICAL, 9.5, part1, kind="band-critical")
            for n in range(1, 6)
        ],
        *[
            _member(f"CVE-2026-{n}", Band.CRITICAL, 9.5, part2, kind="band-critical")
            for n in (6, 7)
        ],
        *[
            _member(f"CVE-2026-{n}", Band.HIGH, 8.0, part2, kind="band-high")
            for n in (8, 9)
        ],
    ]
    items = _parse(render_feed(TOMCAT_ENDPOINT, members)).findall("channel/item")
    assert len(items) == 2
    assert items[0].findtext("title") == (
        f"{RED_CIRCLE}{TRIANGLE} [UPDATE] Apache Software Foundation Apache Tomcat: "
        "5 vulnerabilities raised (5 critical), part 1 of 2"
    )
    assert items[1].findtext("title") == (
        f"{RED_CIRCLE}{TRIANGLE} [UPDATE] Apache Software Foundation Apache Tomcat: "
        "4 vulnerabilities raised (2 critical, 2 high), part 2 of 2"
    )
    assert [i.findtext("guid") for i in items] == [part1, part2]


def test_no_marker_gives_no_leading_space_and_one_fresh_member_gives_squared_new() -> (
    None
):
    members = [_member(f"CVE-2026-{n}", Band.HIGH, 7.0, fresh=None) for n in (1, 2, 3)]
    title = _group_item(members).findtext("title")
    assert title == (
        "Apache Software Foundation Apache Tomcat: "
        "3 new vulnerabilities, worst high (3 high)"
    )
    assert not title.startswith(" ")
    members[1] = replace(members[1], is_fresh=True)
    title = _group_item(members).findtext("title")
    assert title is not None
    assert title.startswith(f"{SQUARED_NEW} ")


def test_a_chunk_of_one_reads_singular() -> None:
    other = "cvestream:team-tomcat:group-new:cccccccccccccccc"
    members = [
        *[_member(f"CVE-2026-{n}", Band.HIGH, 7.0) for n in (1, 2, 3, 4, 5)],
        _member("CVE-2026-9", Band.LOW, 3.0, other),
    ]
    items = _parse(render_feed(TOMCAT_ENDPOINT, members)).findall("channel/item")
    assert len(items) == 2
    assert items[1].findtext("title") == (
        "Apache Software Foundation Apache Tomcat: "
        "1 new vulnerability, worst low (1 low), part 2 of 2"
    )


def test_members_given_in_any_order_render_the_same_xml() -> None:
    members = _five_member_burst()
    reference = render_feed(TOMCAT_ENDPOINT, members)
    for permuted in (list(reversed(members)), members[2:] + members[:2]):
        assert render_feed(TOMCAT_ENDPOINT, permuted) == reference


def test_a_group_between_two_single_items_keeps_its_position_and_counts_once() -> None:
    single_new = _entry(guid="single-new", published_at=NOW)
    single_old = _entry(guid="single-old", published_at=NOW - timedelta(days=2))
    burst = [
        replace(m, published_at=NOW - timedelta(hours=1))
        for m in _five_member_burst()[:3]
    ]
    entries = [single_new, *burst, single_old]
    assert count_items(entries) == 3
    channel = _parse(render_feed(ENDPOINT, entries)).find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert [i.findtext("guid") for i in items] == [
        "single-new",
        GROUP_GUID,
        "single-old",
    ]
    # the single items are unchanged
    assert items[0].findtext("title") == "CVE-2026-1234 in php: severity high"


def test_two_bursts_of_one_product_in_two_runs_never_number_each_others_parts() -> None:
    first = _five_member_burst()[:3]
    second_guid = "cvestream:team-tomcat:group-new:dddddddddddddddd"
    second = [
        replace(m, published_at=NOW - timedelta(days=1), group_guid=second_guid)
        for m in _five_member_burst()[:3]
    ]
    items = _parse(render_feed(TOMCAT_ENDPOINT, [*first, *second])).findall(
        "channel/item"
    )
    assert len(items) == 2
    for item in items:
        title = item.findtext("title") or ""
        assert "part" not in title
        assert title.endswith("(1 critical, 2 high)")


def test_member_line_unknown_score_versions_and_escaping() -> None:
    members = [
        _member("CVE-2026-1", Band.UNKNOWN, None),
        _member("CVE-2026-2", Band.HIGH, 7.0),
        _member("CVE-2026-3", Band.HIGH, 7.0, versions=()),
        _member("CVE-2026-<b>4", Band.HIGH, 7.0, versions=("< 9.0 & more",)),
        _member("CVE-2026-5", Band.HIGH, 7.0, with_record=False),
    ]
    item = _group_item(members)
    lines = _group_lines(item)
    assert "CVE-2026-2 · high 7.0 · exploited: no · 10.1.0-M1 ≤10.1.61" in lines
    # no structured affected data reads "versions unknown"
    assert "CVE-2026-3 · high 7.0 · exploited: no · versions unknown" in lines
    # markup in an ID or a version text shows literally to a reader
    assert "CVE-2026-<b>4 · high 7.0 · exploited: no · < 9.0 & more" in lines
    # an unknown score reads just "unknown"
    assert "CVE-2026-1 · unknown · exploited: no · 10.1.0-M1 ≤10.1.61" in lines
    # the raw HTML escapes the markup (the ID is an anchor's text, escaped)
    description = item.findtext("description") or ""
    assert "<b>4</b>" not in description
    assert "CVE-2026-&lt;b&gt;4" in description
    assert "&lt; 9.0 &amp; more" in description
    # a member without a source URL is not an anchor
    assert "https://euvd.test/CVE-2026-5" not in description
    assert ">CVE-2026-5</a>" not in description
    assert "CVE-2026-5 · high 7.0 · exploited: no · 10.1.0-M1 ≤10.1.61" in lines


def test_each_member_line_resolves_versions_under_its_own_label() -> None:
    # Members share the matched entry name, not the source's vendor/product
    # spelling: the first member's label must not hide the others' versions.
    members = [
        _member("CVE-2026-1", Band.HIGH, 7.5, versions=("9.0.0 ≤9.0.10",)),
        _member_with_affected(
            "CVE-2026-2",
            Band.HIGH,
            7.4,
            (_sourced(AffectedProduct("tomcat", None, "8.0.0")),),
        ),
        _member_with_affected(
            "CVE-2026-3",
            Band.HIGH,
            7.3,
            (_sourced(AffectedProduct("Apache Tomcat", None, "7.0.0")),),
        ),
    ]
    lines = _group_lines(_group_item(members))
    assert "CVE-2026-1 · high 7.5 · exploited: no · 9.0.0 ≤9.0.10" in lines
    assert "CVE-2026-2 · high 7.4 · exploited: no · 8.0.0" in lines
    assert "CVE-2026-3 · high 7.3 · exploited: no · 7.0.0" in lines


def test_member_lines_when_the_first_member_has_no_structured_hit() -> None:
    # The title falls back to the entry name; the other members still resolve
    # their versions under their own labels, not under the entry name.
    members = [
        _member_with_affected(
            "CVE-2026-1",
            Band.HIGH,
            7.5,
            (_sourced(AffectedProduct("Tomcat 9", "Apache", "9.0.0"), "cert_eu"),),
        ),
        _member("CVE-2026-2", Band.HIGH, 7.4, versions=("8.0.0",)),
        _member("CVE-2026-3", Band.HIGH, 7.3, versions=("7.0.0",)),
    ]
    lines = _group_lines(_group_item(members))
    assert "CVE-2026-1 · high 7.5 · exploited: no · versions unknown" in lines
    assert "CVE-2026-2 · high 7.4 · exploited: no · 8.0.0" in lines
    assert "CVE-2026-3 · high 7.3 · exploited: no · 7.0.0" in lines


def test_a_long_version_text_is_capped_in_a_group_member_line_only() -> None:
    long_version = "x" * 100
    members = [
        _member("CVE-2026-1", Band.HIGH, 7.5, versions=(long_version, "< 9.0")),
        _member("CVE-2026-2", Band.HIGH, 7.4),
        _member("CVE-2026-3", Band.HIGH, 7.3),
    ]
    lines = _group_lines(_group_item(members))
    capped = "x" * GROUP_MEMBER_VERSION_CHARS + "\N{HORIZONTAL ELLIPSIS}"
    # the cap keeps the (+N more) suffix after the ellipsis
    assert f"CVE-2026-1 · high 7.5 · exploited: no · {capped} (+1 more)" in lines
    # a single item shows the same source text verbatim
    assert long_version in "\n".join(
        _lines(_entry(vulnerability=members[0].vulnerability))
    )


def test_a_label_with_five_rows_reads_first_plus_four_more() -> None:
    member = _member(
        "CVE-2026-1",
        Band.HIGH,
        7.0,
        versions=tuple(f"≤ 9.{i}" for i in range(5)),
    )
    members = [
        member,
        _member("CVE-2026-2", Band.HIGH, 7.0),
        _member("CVE-2026-3", Band.HIGH, 7.0),
    ]
    lines = _group_lines(_group_item(members))
    assert lines[0].endswith("· ≤ 9.0 (+4 more)")


def test_the_five_member_golden_group_fits_the_slack_preview_window() -> None:
    item = _group_item(_five_member_burst())
    description = item.findtext("description")
    assert description is not None
    text = html.unescape(_TAG.sub("", description))
    preview = text[:SLACK_PREVIEW_CHARS]
    for member in _five_member_burst():
        assert member.primary_id in preview
