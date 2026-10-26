import pytest

from cvestream.domain.identity import AliasKind, alias_kind, normalize_alias, primary_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" cve-2026-109618 ", "CVE-2026-109618"),
        ("euvd-2026-1", "EUVD-2026-1"),
        ("GHSA-gwjx-qpv6-g7jw", "GHSA-GWJX-QPV6-G7JW"),
        ("", None),
        ("   ", None),
        ("https://cert.ssi.gouv.fr/alerte/CERTFR-2026-ALE-012/", None),
        ("http://example.com/CVE-2026-1", None),
        ("cert.ssi.gouv.fr/alerte/x", None),
        ("CVE 2026 1", None),
    ],
    ids=[
        "cve-upper-and-strip",
        "euvd-upper",
        "ghsa-upper",
        "empty",
        "blank",
        "https-url",
        "http-url",
        "path-without-scheme",
        "inner-whitespace",
    ],
)
def test_normalize_alias(raw: str, expected: str | None) -> None:
    assert normalize_alias(raw) == expected


@pytest.mark.parametrize(
    ("alias", "kind"),
    [
        ("CVE-2026-0001", AliasKind.CVE),
        ("CVE-2026-109618", AliasKind.CVE),
        ("CVE-2026-1", AliasKind.ADVISORY),
        ("EUVD-2026-24185", AliasKind.EUVD),
        ("GHSA-GWJX-QPV6-G7JW", AliasKind.ADVISORY),
        ("CERTFR-2026-ALE-012", AliasKind.ADVISORY),
    ],
    ids=["cve", "cve-long", "cve-too-short", "euvd", "ghsa", "certfr"],
)
def test_alias_kind(alias: str, kind: AliasKind) -> None:
    assert alias_kind(alias) is kind


@pytest.mark.parametrize(
    ("aliases", "expected"),
    [
        (["EUVD-2026-5", "GHSA-AAAA-BBBB-CCCC", "cve-2026-1234"], "CVE-2026-1234"),
        (["GHSA-AAAA-BBBB-CCCC", "euvd-2026-5"], "EUVD-2026-5"),
        (["GHSA-ZZZZ", "CERTFR-2026-ALE-012"], "CERTFR-2026-ALE-012"),
        (["CVE-2026-10000", "CVE-2026-9999"], "CVE-2026-9999"),
        (["CVE-2027-1000", "CVE-2026-9999"], "CVE-2026-9999"),
        (["EUVD-2026-100", "EUVD-2026-20"], "EUVD-2026-20"),
        (["https://example.com/x", "euvd-2026-5"], "EUVD-2026-5"),
    ],
    ids=[
        "cve-wins",
        "euvd-over-advisory",
        "lexicographic-advisory",
        "numeric-not-lexical-cve",
        "year-first",
        "numeric-euvd",
        "url-ignored",
    ],
)
def test_primary_id(aliases: list[str], expected: str) -> None:
    assert primary_id(aliases) == expected


@pytest.mark.parametrize(
    "aliases", [[], ["", "https://example.com/x"]], ids=["empty", "unusable"]
)
def test_primary_id_without_usable_alias_is_a_programming_error(
    aliases: list[str],
) -> None:
    with pytest.raises(ValueError, match="alias"):
        primary_id(aliases)
