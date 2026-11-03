import pytest
from packaging.version import Version

from cvestream.domain.version import VersionRange, parse_range, parse_version

V = Version


def _range(
    lower: str | None,
    upper: str | None,
    *,
    lower_inclusive: bool = True,
    upper_inclusive: bool = False,
) -> VersionRange:
    # An unbounded side is reported as inclusive.
    return VersionRange(
        lower=V(lower) if lower is not None else None,
        lower_inclusive=lower_inclusive or lower is None,
        upper=V(upper) if upper is not None else None,
        upper_inclusive=upper_inclusive or upper is None,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8.2", V("8.2")),
        (" 8.2.29 ", V("8.2.29")),
        ("v4.04", V("4.4")),
        ("0.2.0-rc.0", V("0.2.0rc0")),
        ("10.0.0-beta", V("10.0.0b0")),
        ("1.78a", V("1.78a0")),
        ("2.0.84.Final", None),
        ("8.3.x", None),
        ("11.0.0-rc.2.26504.105", None),
        ("0:4.18.0-513.5.1.el8_9", None),
        ("", None),
        ("n/a", None),
    ],
    ids=[
        "plain",
        "strip",
        "v-prefix",
        "rc",
        "beta",
        "alpha-suffix",
        "final-suffix",
        "wildcard",
        "long-prerelease",
        "rpm-epoch",
        "empty",
        "na",
    ],
)
def test_parse_version(text: str, expected: Version | None) -> None:
    assert parse_version(text) == expected


# Real strings observed in the EUVD API on 2026-10-26, plus the story examples.
RANGES = [
    # EUVD: affected from X, fixed in Y
    ("10.0.17763.0 <10.0.17763.9376", _range("10.0.17763.0", "10.0.17763.9376")),
    ("3.8.50 <3.8.52", _range("3.8.50", "3.8.52")),
    ("0 <1.78a", _range("0", "1.78a0")),
    ("1.0 <v4.04", _range("1.0", "4.4")),
    ("10.0.0-beta <10.0.1037-beta", _range("10.0.0b0", "10.0.1037b0")),
    # EUVD: affected from X up to and including Y
    ("0 ≤2.21.1", _range("0", "2.21.1", upper_inclusive=True)),
    ("8.0.0 ≤8.3.0", _range("8.0.0", "8.3.0", upper_inclusive=True)),
    ("0 ≤*", _range("0", None)),
    ("0 <*", _range("0", None)),
    ("0 <=*", _range("0", None)),
    # single version
    ("8.3", _range("8.3", "8.3", upper_inclusive=True)),
    ("1.0.0", _range("1.0.0", "1.0.0", upper_inclusive=True)),
    ("0.2.0-rc.0", _range("0.2.0rc0", "0.2.0rc0", upper_inclusive=True)),
    ("1.0.0-rc.1", _range("1.0.0rc1", "1.0.0rc1", upper_inclusive=True)),
    # one-sided
    ("< 1.5.1", _range(None, "1.5.1")),
    ("<1.5.1", _range(None, "1.5.1")),
    ("<= 8.2", _range(None, "8.2", upper_inclusive=True)),
    ("≤8.2", _range(None, "8.2", upper_inclusive=True)),
    ("< 8.2.29", _range(None, "8.2.29")),
    (">= 8.2", _range("8.2", None)),
    ("≥8.2", _range("8.2", None)),
    ("> 8.2", _range("8.2", None, lower_inclusive=False)),
    # hyphen range (spaces on both sides)
    ("8.1.0 - 8.3.4", _range("8.1.0", "8.3.4", upper_inclusive=True)),
    # everything
    ("*", _range(None, None, upper_inclusive=True)),
    # unusable
    (None, None),
    ("", None),
    ("   ", None),
    ("n/a", None),
    ("N/A", None),
    ("na", None),
    ("-", None),
    ("unknown", None),
    ("Unspecified", None),
    ("patch: 0.15.3", None),
    ("<3.16.0 <3.16.0", None),
    ("4.16.0 <4.16.1 <4.16.1", None),
    ("16.0.1 <https://aka.ms/OfficeSecurityReleases", None),
    ("4.8.1 <2.0.50727.9188 AND 3.0.30729.9171 AND 4.8.9363.0", None),
    ("18.12.0 <18.12 Insiders 3", None),
    ("0 <2.0.84.Final", None),
    ("8.3.x", None),
    ("versions antérieures à 8.2.29", None),
    ("5.0.0.0 <3.0.1", None),
    ("1.0.0b3 <1.0.0b3", None),
    ("11.0.0 <11.0.0-rc.2", None),
    ("8.4 - 8.2", None),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    RANGES,
    ids=[repr(text) for text, _ in RANGES],
)
def test_parse_range(text: str | None, expected: VersionRange | None) -> None:
    assert parse_range(text) == expected


def test_parse_range_collapses_whitespace() -> None:
    assert parse_range("8.1.0   -\t8.3.4") == _range(
        "8.1.0", "8.3.4", upper_inclusive=True
    )


REACHES = [
    # (range text, minimum, expected)
    ("< 8.2", "8.2", False),  # exact boundary, exclusive
    ("<= 8.2", "8.2", True),  # exact boundary, inclusive
    ("8.0 <8.2.29", "8.2", True),
    ("8.0 <8.2.29", "8.2.29", False),
    ("8.0 <8.2.29", "8.3", False),
    ("8.0 ≤8.2.29", "8.2.29", True),
    ("8.0 ≤8.2.29", "8.2.30", False),
    ("8.2", "8.2.0", True),  # 8.2 == 8.2.0
    ("8.1", "8.2", False),
    ("1.0 <8.2.0rc1", "8.2", False),  # a prerelease is below the release
    ("0 ≤*", "99.0", True),  # unbounded above
    (">= 8.2", "99.0", True),
    ("*", "99.0", True),
    ("< 1.5.1", "1.5.1", False),
    ("< 1.5.1", "1.5", True),
    ("8.1.0 - 8.3.4", "8.3.4", True),
    ("8.1.0 - 8.3.4", "8.3.5", False),
]


@pytest.mark.parametrize(
    ("text", "minimum", "expected"),
    REACHES,
    ids=[f"{text} min {minimum}" for text, minimum, _ in REACHES],
)
def test_reaches(text: str, minimum: str, expected: bool) -> None:
    version_range = parse_range(text)
    assert version_range is not None
    assert version_range.reaches(V(minimum)) is expected


# Story 1.8: PHP CNA branch wildcards (wixyvir/cvestream#11), compound ranges
# and CVE 5 placeholder lower bounds.
BRANCH_AND_COMPOUND_RANGES = [
    # branch wildcard + upper bound (the four PHP2Shell rows first)
    ("8.2.* <8.2.35", _range("8.2", "8.2.35")),
    ("8.3.* <8.3.36", _range("8.3", "8.3.36")),
    ("8.4.* <8.4.27", _range("8.4", "8.4.27")),
    ("8.5.* <8.5.12", _range("8.5", "8.5.12")),
    ("8.2.* ≤8.2.35", _range("8.2", "8.2.35", upper_inclusive=True)),
    ("8.2.* <=8.2.35", _range("8.2", "8.2.35", upper_inclusive=True)),
    ("8.2.*<8.2.35", _range("8.2", "8.2.35")),
    ("8.2.* <*", _range("8.2", None)),
    ("8.2.* ≤*", _range("8.2", None)),
    ("8.2.* <8.4", _range("8.2", "8.4")),
    ("v8.2.* <8.2.35", _range("8.2", "8.2.35")),
    ("8.* <8.4", _range("8", "8.4")),
    # bare branch
    ("8.2.*", _range("8.2", "8.3")),
    ("8.*", _range("8", "9")),
    ("8.2.1.*", _range("8.2.1", "8.2.2")),
    ("8.2.0.*", _range("8.2.0", "8.2.1")),
    ("v8.2.*", _range("8.2", "8.3")),
    ("V8.2.*", _range("8.2", "8.3")),
    ("8.9.*", _range("8.9", "8.10")),
    # compound
    (">=8.0 <8.3", _range("8.0", "8.3")),
    (">=8.0 <=8.3", _range("8.0", "8.3", upper_inclusive=True)),
    (">8.0 <8.3", _range("8.0", "8.3", lower_inclusive=False)),
    (">8.0 <=8.3", _range("8.0", "8.3", lower_inclusive=False, upper_inclusive=True)),
    ("≥8.0 ≤8.3", _range("8.0", "8.3", upper_inclusive=True)),
    (">= 8.0 < 8.3", _range("8.0", "8.3")),
    (">=8.0 <=8.0", _range("8.0", "8.0", upper_inclusive=True)),
    # placeholder lower bound
    ("* <8.2.29", _range(None, "8.2.29")),
    ("* <=8.2.29", _range(None, "8.2.29", upper_inclusive=True)),
    ("* ≤8.2.29", _range(None, "8.2.29", upper_inclusive=True)),
    ("unspecified <8.2.29", _range(None, "8.2.29")),
    ("Unspecified <=8.2.29", _range(None, "8.2.29", upper_inclusive=True)),
    ("UNSPECIFIED <8.2.29", _range(None, "8.2.29")),
    # still unusable
    ("8.2rc1.*", None),
    ("8.2.*.*", None),
    ("8.4.* <8.2", None),  # inverted
    ("8.2.* <8.2", None),  # empty
    ("8.2.* 8.2.35", None),  # no operator
    (">=8.3 <8.0", None),  # inverted
    (">=8.0 <8.0", None),  # empty
    (">8.0 <8.0", None),
    (">8.0 <=8.0", None),
    (">=8.0, <8.3", None),  # comma-separated: deferred
    (">=8.0<8.3", None),  # no whitespace between the parts: out of scope
    (">=8.0 <*", None),  # out of scope
    ("* <*", None),
    ("<= *", None),  # deferred
    ("unspecified", None),
    ("* <8.3.x", None),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    BRANCH_AND_COMPOUND_RANGES,
    ids=[repr(text) for text, _ in BRANCH_AND_COMPOUND_RANGES],
)
def test_parse_range_branch_and_compound(
    text: str, expected: VersionRange | None
) -> None:
    assert parse_range(text) == expected


def test_placeholder_lower_bound_equals_the_one_sided_range() -> None:
    assert parse_range("* <8.2.29") == parse_range("<8.2.29")


def test_standalone_zero_is_still_the_single_version_zero() -> None:
    assert parse_range("0") == _range("0", "0", upper_inclusive=True)


BRANCH_REACHES = [
    # (range text, minimum, expected)
    ("8.2.*", "8.3", False),
    ("8.2.*", "8.2.99", True),
    ("8.2.* <8.2.35", "8.2.34", True),
    ("8.2.* <8.2.35", "8.2.35", False),
    ("8.2.* ≤8.2.35", "8.2.35", True),
    ("8.*", "9.0", False),
    ("8.*", "8.99", True),
    ("8.2.* <*", "99.0", True),
    ("* <8.2.29", "8.2", True),
    ("* <8.2.29", "8.2.29", False),
    (">8.0 <8.3", "8.3", False),
    (">=8.0 <=8.3", "8.3", True),
]


@pytest.mark.parametrize(
    ("text", "minimum", "expected"),
    BRANCH_REACHES,
    ids=[f"{text} min {minimum}" for text, minimum, _ in BRANCH_REACHES],
)
def test_reaches_branch_and_compound(text: str, minimum: str, expected: bool) -> None:
    version_range = parse_range(text)
    assert version_range is not None
    assert version_range.reaches(V(minimum)) is expected
