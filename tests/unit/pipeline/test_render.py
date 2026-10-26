import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime, timedelta

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
            _sourced(KevEntry(KevCatalog.CISA_KEV, known_ransomware_use=True), "cisa"),
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


def _lines(entry: FeedEntry) -> list[str]:
    description = _item(entry).findtext("description")
    assert description is not None
    return description.split("\n")


def test_the_full_description_one_fact_per_line() -> None:
    assert _lines(_entry(labels=frozenset({PassThrough.VERSIONS_UNKNOWN}))) == [
        "IDs: CVE-2026-0999, CVE-2026-1234, EUVD-2026-41234",
        "Matched: php, nodejs (versions unknown)",
        "Severity: high (highest CVSS 8.1)",
        "CVSS: 8.1 (CVSS 3.1, euvd); unknown (cisa_kev); 5.0 (CVSS unknown, other)",
        "Known exploited: yes, CISA KEV (ransomware use: known),"
        " EU KEV (ransomware use: unknown)",
        "Affected: Google Chrome < 141.0.7390.122; vendor product versions unknown;"
        " lonely versions unknown",
        "Description: First description with lines",
        f"Sources: euvd {EUVD_URL}, cisa_kev {CISA_URL}, custom X-1",
        "References: "
        + ", ".join(f"https://example.test/ref/{i}" for i in range(10))
        + " (+2 more)",
    ]


def test_unknown_everywhere_data_is_missing() -> None:
    assert _lines(_bare()) == [
        "IDs: unknown",
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
    assert (
        _lines(_entry(vulnerability=vulnerability))[-1] == "References: https://a.test"
    )


@pytest.mark.parametrize(
    ("kind", "known", "title"),
    [
        ("new", False, "CVE-2026-1234 in php: severity high"),
        ("new", True, "CVE-2026-1234 in php: severity high, known exploited"),
        (
            "kev",
            True,
            "[UPDATE] CVE-2026-1234 in php: now known exploited, severity high",
        ),
        ("band-high", False, "[UPDATE] CVE-2026-1234 in php: severity raised to high"),
        (
            "band-high",
            True,
            "[UPDATE] CVE-2026-1234 in php: severity raised to high, known exploited",
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
    assert b"CDATA" not in xml
    description = _item(_entry(vulnerability=vulnerability)).findtext("description")
    assert description is not None
    assert "Description: <script>alert(1)</script> & <b>more</b>" in description


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
    description = item.findtext("description")
    assert description is not None
    assert "Description: abcdef\x7f" in description


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
    description = _item(_entry(vulnerability=vulnerability)).findtext("description")
    assert description is not None
    assert "Description: rocket \U0001f680 é" in description


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
            _sourced(KevEntry(KevCatalog.CISA_KEV), "cisa_kev"),
            _sourced(
                KevEntry(KevCatalog.CISA_KEV, known_ransomware_use=True), "cisa_kev"
            ),
            _sourced(
                KevEntry(KevCatalog.CISA_KEV, known_ransomware_use=None), "cisa_kev"
            ),
        ),
    )
    assert "Known exploited: yes, CISA KEV (ransomware use: known)" in _lines(
        _entry(vulnerability=vulnerability)
    )


def test_a_ransomware_use_of_false_is_not_reported_as_known() -> None:
    vulnerability = replace(
        _full_vulnerability(),
        kev=(
            _sourced(KevEntry(KevCatalog.EU_KEV, known_ransomware_use=False), "eu_kev"),
        ),
    )
    assert "Known exploited: yes, EU KEV (ransomware use: no)" in _lines(
        _entry(vulnerability=vulnerability)
    )
