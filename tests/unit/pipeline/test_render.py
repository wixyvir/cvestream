import html
import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.matcher import PassThrough
from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    RecordRef,
    Sourced,
    Vulnerability,
)
from cvestream.domain.severity import Band
from cvestream.pipeline.render import (
    CHANNEL_LINK,
    FeedEntry,
    etag_of,
    render_feed,
)

NOW = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
ENDPOINT = Endpoint(id="team-php", name="PHP team", products=(ProductEntry("php"),))
EUVD_URL = "https://euvd.enisa.europa.eu/vulnerability/EUVD-2026-41234"
CISA_URL = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"


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


def test_the_full_description_one_fact_per_line() -> None:
    assert _lines(_entry(labels=frozenset({PassThrough.VERSIONS_UNKNOWN}))) == [
        "IDs: CVE-2026-0999, CVE-2026-1234, EUVD-2026-41234",
        "Published: 2026-10-20",
        "Updated: 2026-10-26",
        "Matched: php, nodejs (versions unknown)",
        "Severity: high (highest CVSS 8.1)",
        "CVSS: 8.1 (CVSS 3.1, euvd); unknown (cisa_kev); 5.0 (CVSS unknown, other)",
        "Known exploited: yes, CISA KEV (added 2026-10-24, ransomware use: known),"
        " EU KEV (added unknown, ransomware use: unknown)",
        "Affected: Google Chrome < 141.0.7390.122; vendor product versions unknown;"
        " lonely versions unknown",
        "Description: First description with lines",
        "Sources: euvd, cisa_kev, custom X-1",
        "References:",
        *(f"https://example.test/ref/{i}" for i in range(10)),
        "and 2 more",
    ]


def test_the_description_is_an_html_fragment() -> None:
    assert _html(_entry()) == "\n".join(
        [
            "<p><b>IDs:</b> CVE-2026-0999, CVE-2026-1234, EUVD-2026-41234</p>",
            "<p><b>Published:</b> 2026-10-20<br/>",
            "<b>Updated:</b> 2026-10-26<br/>",
            "<b>Matched:</b> php, nodejs<br/>",
            "<b>Severity:</b> high (highest CVSS 8.1)<br/>",
            "<b>CVSS:</b> 8.1 (CVSS 3.1, euvd); unknown (cisa_kev);"
            " 5.0 (CVSS unknown, other)<br/>",
            "<b>Known exploited:</b> yes, CISA KEV (added 2026-10-24,"
            " ransomware use: known), EU KEV (added unknown,"
            " ransomware use: unknown)<br/>",
            "<b>Affected:</b> Google Chrome &lt; 141.0.7390.122;"
            " vendor product versions unknown; lonely versions unknown</p>",
            "<p><b>Description:</b> First description with lines</p>",
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
        "IDs: unknown",
        "Published: unknown",
        "Updated: unknown",
        "Matched: php, nodejs",
        "Severity: unknown",
        "CVSS: unknown",
        "Known exploited: no",
        "Affected: unknown",
        "Description: unknown",
        "Sources: unknown",
    ]


def test_unknown_score_that_was_let_through_is_said() -> None:
    lines = _lines(_bare(labels=frozenset({PassThrough.SCORE_UNKNOWN})))
    assert "Severity: unknown (no score; let through)" in lines


def test_no_description_line_ever_shows_zero_or_empty_values() -> None:
    for line in _lines(_bare()):
        assert not line.endswith(": ")
        assert ": 0" not in line
        assert "n/a" not in line


def test_the_severity_is_recomputed_from_current_scores() -> None:
    entry = _entry(band=Band.LOW)
    assert "Severity: high (highest CVSS 8.1)" in _lines(entry)
    assert _item(entry).findtext("title") == ("CVE-2026-1234 in php: severity low")


def test_a_score_is_shown_with_one_decimal() -> None:
    vulnerability = replace(
        _full_vulnerability(), scores=(_sourced(CvssScore(9.0, "4.0")),)
    )
    lines = _lines(_entry(vulnerability=vulnerability))
    assert "Severity: critical (highest CVSS 9.0)" in lines
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
        ("new", True, "CVE-2026-1234 in php: severity high, EXPLOITED"),
        (
            "kev",
            True,
            "[UPDATE] CVE-2026-1234 in php: now EXPLOITED, severity high",
        ),
        ("band-high", False, "[UPDATE] CVE-2026-1234 in php: severity raised to high"),
        (
            "band-high",
            True,
            "[UPDATE] CVE-2026-1234 in php: severity raised to high, EXPLOITED",
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
        f"CVE-2026-1234 in {name}: severity high, EXPLOITED"
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
        "<p><b>Description:</b> &lt;script&gt;alert(1)&lt;/script&gt;"
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
        "Known exploited: yes, CISA KEV (added 2026-08-30, ransomware use: known)"
    ) in _lines(_entry(vulnerability=vulnerability))


def test_a_ransomware_use_of_false_is_not_reported_as_known() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        kev=(
            _sourced(KevEntry(KevCatalog.EU_KEV, known_ransomware_use=False), "eu_kev"),
        ),
    )
    assert "Known exploited: yes, EU KEV (added unknown, ransomware use: no)" in _lines(
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
