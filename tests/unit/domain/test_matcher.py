from collections.abc import Callable

import pytest
from packaging.version import Version

from cvestream.domain.endpoint import Endpoint, ProductEntry, Rules
from cvestream.domain.matcher import MatchResult, PassThrough, match
from cvestream.domain.model import KevCatalog, Vulnerability

type VulnerabilityFactory = Callable[..., Vulnerability]


def _endpoint(
    *entries: ProductEntry,
    min_cvss: float | None = None,
    kev_only: bool = False,
    require_score: bool = False,
) -> Endpoint:
    return Endpoint(
        id="team-a",
        name="Team A",
        products=entries,
        rules=Rules(min_cvss=min_cvss, kev_only=kev_only, require_score=require_score),
    )


PHP = ProductEntry(name="php")
PHP_8_2 = ProductEntry(name="php", min_version=Version("8.2"))


def _names(result: MatchResult | None) -> list[str]:
    assert result is not None
    return [m.entry.name for m in result.matches]


# --- product matching (FR-11, FR-12) -------------------------------------------


@pytest.mark.parametrize(
    ("description", "matches"),
    [
        ("A flaw in PHP 8.3 allows remote code execution", True),
        ("A flaw in php allows remote code execution", True),
        ("The php-fpm service crashes", True),
        ("See the php.net advisory", True),
        ("A flaw in phpMyAdmin allows XSS", False),
        ("A flaw in PHP8 allows XSS", False),
        ("A flaw in php_src allows XSS", False),
        ("Nothing relevant here", False),
    ],
    ids=[
        "upper-case",
        "lower-case",
        "hyphen-boundary",
        "dot-boundary",
        "phpmyadmin",
        "glued-digit",
        "underscore",
        "unrelated",
    ],
)
def test_whole_word_matching_in_description(
    make_vulnerability: VulnerabilityFactory, description: str, matches: bool
) -> None:
    result = match(make_vulnerability(description=description), _endpoint(PHP))
    assert (result is not None) is matches


def test_term_with_special_characters_is_escaped(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    endpoint = _endpoint(ProductEntry(name="c++"))
    assert match(make_vulnerability(description="a c++ library"), endpoint)
    assert match(make_vulnerability(description="a c library"), endpoint) is None


def test_multi_word_term_tolerates_whitespace_runs(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    endpoint = _endpoint(ProductEntry(name="Apache  HTTP Server"))
    vulnerability = make_vulnerability(description="Apache\nHTTP   Server flaw")
    assert match(vulnerability, endpoint) is not None


def test_matches_on_alias(make_vulnerability: VulnerabilityFactory) -> None:
    endpoint = _endpoint(ProductEntry(name="php", aliases=("php-src",)))
    result = match(make_vulnerability(description="php-src heap overflow"), endpoint)
    assert _names(result) == ["php"]


def test_matches_on_structured_product_with_unrelated_description(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        description="Remote code execution", products=[("PHP", "PHP Group")]
    )
    result = match(vulnerability, _endpoint(PHP))
    assert result is not None
    assert result.matches[0].structured is True


def test_matches_on_structured_vendor_only(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(products=[("Zend Engine", "PHP")])
    result = match(vulnerability, _endpoint(PHP))
    assert result is not None
    assert result.matches[0].structured is True


def test_structured_whole_word_only(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(products=[("phpMyAdmin", "phpMyAdmin")])
    assert match(vulnerability, _endpoint(PHP)) is None


def test_description_only_match_is_not_structured(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    result = match(make_vulnerability(description="PHP 8.3 flaw"), _endpoint(PHP))
    assert result is not None
    assert result.matches[0].structured is False


@pytest.mark.parametrize(
    ("products", "description"),
    [
        (
            [("Wild Apricot Login", "wild-apricot", "0 ≤1.0.18")],
            "The Wild Apricot Login plugin for WordPress falls back to a lookup in"
            " `Module.php` to resolve a WordPress user.",
        ),
        (
            [("PHPOK", "PHPOK", "4.5.000 <5.4.081")],
            "PHPOK, a PHP content management system, allows SQL injection.",
        ),
        (
            [("Kirki", "Themeum", "n/a")],
            "The Kirki plugin for WordPress is vulnerable to PHP Object Injection.",
        ),
    ],
    ids=["php-file-name", "php-made-product", "php-object-injection"],
)
def test_a_description_mention_does_not_match_another_product(
    make_vulnerability: VulnerabilityFactory,
    products: list[tuple[str, str, str]],
    description: str,
) -> None:
    vulnerability = make_vulnerability(description=description, products=products)
    assert match(vulnerability, _endpoint(PHP)) is None
    assert match(vulnerability, _endpoint(PHP_8_2)) is None


@pytest.mark.parametrize(
    "products",
    [
        [("PHP", "PHP Group", "8.1.0 <8.2.29")],
        [("php-fpm", "PHP Group", "8.1.0 <8.2.29")],
        [("Zend Engine", "PHP", "8.1.0 <8.2.29")],
    ],
    ids=["product", "sub-product", "vendor"],
)
def test_php_and_its_sub_products_still_match(
    make_vulnerability: VulnerabilityFactory, products: list[tuple[str, str, str]]
) -> None:
    vulnerability = make_vulnerability(
        description="A flaw in a WordPress plugin", products=products
    )
    result = match(vulnerability, _endpoint(PHP))
    assert result is not None
    assert result.matches[0].structured is True


def test_the_description_is_a_fallback_without_affected_data(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="A flaw in PHP 8.3")
    result = match(vulnerability, _endpoint(PHP_8_2))
    assert result is not None
    assert result.matches[0].structured is False
    assert result.labels == frozenset({PassThrough.VERSIONS_UNKNOWN})


def test_title_alone_never_matches(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(
        title="PHP remote code execution", description="Remote code execution"
    )
    assert match(vulnerability, _endpoint(PHP)) is None


def test_no_data_does_not_match(make_vulnerability: VulnerabilityFactory) -> None:
    assert match(make_vulnerability(), _endpoint(PHP)) is None


def test_endpoint_without_products_never_matches(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    assert match(make_vulnerability(description="PHP flaw"), _endpoint()) is None


# --- description fallback precision (FR-12) ---------------------------------------


@pytest.mark.parametrize(
    ("description", "matches"),
    [
        ("admin/modules/circulation/loan_history_maintenance.php is vulnerable", False),
        ("The diagnostic.php endpoint is vulnerable", False),
        ("Files under /usr/lib/php/ are writable", False),
        ("Files under C:\\php\\ are writable", False),
        ("A flaw in PHP 8.3 allows XSS", True),
        ("The php-fpm service crashes", True),
        ("A flaw in the language (PHP) allows XSS", True),
        ("A flaw in PHP. Another sentence follows", True),
        ("Reading php.ini discloses secrets", True),
        ("See the php.net advisory", True),
        ("Upload a .php file to a PHP 8.3 server", True),
    ],
    ids=[
        "path-file",
        "file-name",
        "unix-dir",
        "windows-dir",
        "version",
        "hyphen",
        "parentheses",
        "full-stop",
        "php-ini",
        "php-net",
        "path-then-prose",
    ],
)
def test_a_name_inside_a_path_is_not_a_mention_in_the_description(
    make_vulnerability: VulnerabilityFactory, description: str, matches: bool
) -> None:
    vulnerability = make_vulnerability(description=description)
    assert (match(vulnerability, _endpoint(PHP)) is not None) is matches


def test_node_still_matches_node_js(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(description="A flaw in Node.js allows XSS")
    assert match(vulnerability, _endpoint(ProductEntry(name="node"))) is not None


@pytest.mark.parametrize(
    ("entry", "description"),
    [
        (ProductEntry(name="apache"), "Apache/2.4.49 allows path traversal"),
        (ProductEntry(name="linux"), "A Linux/Unix kernel flaw allows root"),
    ],
    ids=["name-slash-version", "name-slash-name"],
)
def test_a_slash_next_to_a_name_reads_as_a_path(
    make_vulnerability: VulnerabilityFactory, entry: ProductEntry, description: str
) -> None:
    # Assumed risk of the path rule: such prose is not a mention in the fallback.
    vulnerability = make_vulnerability(description=description)
    assert match(vulnerability, _endpoint(entry)) is None


@pytest.mark.parametrize(
    ("entry", "description", "matches"),
    [
        (
            ProductEntry(name="php", aliases=("php-src",)),
            "A flaw in ext/php-src/main.c allows XSS",
            False,
        ),
        (
            ProductEntry(name="php", aliases=("php-src",)),
            "A flaw in php-src allows XSS",
            True,
        ),
        (
            ProductEntry(name="Zend Engine"),
            "A flaw in lib/Zend Engine/ allows XSS",
            False,
        ),
        (
            ProductEntry(name="Zend Engine"),
            "A flaw in the Zend  Engine allows XSS",
            True,
        ),
    ],
    ids=["alias-in-path", "alias-in-prose", "multi-word-in-path", "multi-word-prose"],
)
def test_the_path_rule_applies_to_aliases_and_multi_word_names(
    make_vulnerability: VulnerabilityFactory,
    entry: ProductEntry,
    description: str,
    matches: bool,
) -> None:
    vulnerability = make_vulnerability(description=description)
    assert (match(vulnerability, _endpoint(entry)) is not None) is matches


@pytest.mark.parametrize(
    "description",
    [
        "Slims (Senayan Library Management Systems) 9 Bulian 9.8.0 allows"
        " cross-site request forgery (CSRF) against"
        " admin/modules/circulation/loan_history_maintenance.php because the"
        " action=run GET parameter performs privileged, destructive operations"
        " with no CSRF token or POST confirmation, leading to data loss of loan"
        " history records.",
        "An OS command injection vulnerability in the /htdocs/web/diagnostic.php"
        " endpoint of D-Link DAP1160L A1_FW101WWb04 allows attackers to execute"
        " arbitrary commands via injecting arbitrary shell metacharacters.",
        "e-SIC Livre (esiclivre) v0.2.2 and earlier contains an uncontrolled"
        " resource consumption vulnerability in restrito/inc/captcha.php. The"
        " application uses user-supplied parameters (l, a, tf, ql) without"
        " validation to generate CAPTCHA images, allowing remote attackers to"
        " cause denial of service via excessive memory and CPU consumption.",
        "SQL injection via identifier injection in the tableName and tableFields"
        " POST parameters of admin AJAX endpoints (admin/AJAX_lookup_handler.php,"
        " admin/AJAX_check_id.php, admin/AJAX_vocabolary_control.php) in SENAYAN"
        " Library Management System (SLiMS) Bulian 9.8.0 allows any authenticated"
        " staff user to extract arbitrary database contents.",
    ],
    ids=["CVE-2026-107084", "CVE-2026-52356", "CVE-2026-50787", "CVE-2026-107081"],
)
@pytest.mark.parametrize("kev", [False, True], ids=["euvd-n-a", "kev-only"])
def test_file_names_in_euvd_descriptions_do_not_match_php(
    make_vulnerability: VulnerabilityFactory, description: str, kev: bool
) -> None:
    # An EUVD record whose product is "n/a" or a CISA KEV-only record: no
    # affected product survives.
    vulnerability = make_vulnerability(description=description, kev=kev)
    assert match(vulnerability, _endpoint(PHP)) is None
    assert match(vulnerability, _endpoint(PHP_8_2)) is None


_KEV_ONLY_DESCRIPTION = (
    "Linux Kernel contains a missing initialization of a variable vulnerability"
    " that could allow for unintended modification of page-cache-backed data via"
    " failure to preserve the shared-fragment marker during socket buffer"
    " coalescing, causing ESP input processing to decrypt shared fragments in"
    " place. This vulnerability can be chained with CVE-2026-109618 and"
    " CVE-2026-109619 to implant PHP webshells and run commands as root."
)


def test_opting_out_of_description_matching_drops_a_kev_only_vulnerability(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    # CVE-2026-46300: CISA KEV lists no product, the description mentions PHP.
    vulnerability = make_vulnerability(
        primary_id="CVE-2026-46300", description=_KEV_ONLY_DESCRIPTION, kev=True
    )
    default = match(vulnerability, _endpoint(PHP))
    assert default is not None
    assert default.matches[0].structured is False
    strict = ProductEntry(name="php", match_description=False)
    assert match(vulnerability, _endpoint(strict)) is None


def test_opting_out_of_description_matching_keeps_structured_matches(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        description="A flaw in a WordPress plugin",
        products=[("PHP", "PHP Group", "8.1.0 <8.2.29")],
    )
    strict = ProductEntry(name="php", match_description=False)
    result = match(vulnerability, _endpoint(strict))
    assert result is not None
    assert result.matches[0].structured is True
    strict_min = ProductEntry(
        name="php", min_version=Version("8.2"), match_description=False
    )
    result = match(vulnerability, _endpoint(strict_min))
    assert result is not None
    assert result.matches[0].versions_unknown is False
    too_new = ProductEntry(
        name="php", min_version=Version("9.0"), match_description=False
    )
    assert match(vulnerability, _endpoint(too_new)) is None


def test_exclusions_still_read_the_description_without_description_matching(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        description="A flaw in a WordPress plugin written in PHP",
        products=[("PHP", "PHP Group")],
    )
    entry = ProductEntry(name="php", exclude=("wordpress",), match_description=False)
    assert match(vulnerability, _endpoint(entry)) is None


# --- exclusions (FR-12) ----------------------------------------------------------


def test_exclusion_blocks_the_entry(make_vulnerability: VulnerabilityFactory) -> None:
    entry = ProductEntry(name="php", exclude=("wordpress",))
    vulnerability = make_vulnerability(description="WordPress plugin written in PHP")
    assert match(vulnerability, _endpoint(entry)) is None


def test_exclusion_in_structured_data_blocks_the_entry(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    entry = ProductEntry(name="php", exclude=("wordpress",))
    vulnerability = make_vulnerability(
        description="PHP flaw", products=[("PHP", "WordPress")]
    )
    assert match(vulnerability, _endpoint(entry)) is None


def test_exclusion_is_whole_word(make_vulnerability: VulnerabilityFactory) -> None:
    entry = ProductEntry(name="php", exclude=("press",))
    vulnerability = make_vulnerability(description="WordPress plugin written in PHP")
    assert match(vulnerability, _endpoint(entry)) is not None


def test_exclusion_blocks_one_entry_but_another_still_matches(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    blocked = ProductEntry(name="php", exclude=("wordpress",))
    other = ProductEntry(name="wordpress")
    vulnerability = make_vulnerability(description="WordPress plugin written in PHP")
    assert _names(match(vulnerability, _endpoint(blocked, other))) == ["wordpress"]


def test_exclusion_ignores_titles(make_vulnerability: VulnerabilityFactory) -> None:
    entry = ProductEntry(name="php", exclude=("wordpress",))
    vulnerability = make_vulnerability(
        title="WordPress plugin flaw", description="PHP flaw"
    )
    assert match(vulnerability, _endpoint(entry)) is not None


def test_matches_keep_endpoint_order(make_vulnerability: VulnerabilityFactory) -> None:
    entries = (
        ProductEntry(name="redis"),
        ProductEntry(name="nginx"),
        ProductEntry(name="php"),
    )
    vulnerability = make_vulnerability(description="php, then nginx, then redis")
    assert _names(match(vulnerability, _endpoint(*entries))) == [
        "redis",
        "nginx",
        "php",
    ]


# --- minimum version (FR-13) -------------------------------------------------------


@pytest.mark.parametrize(
    ("version_text", "reached"),
    [
        ("8.1.0 <8.2.29", True),
        ("8.2.29", True),
        ("8.0 <8.2", False),  # exact boundary, exclusive
        ("8.0 ≤8.2", True),  # exact boundary, inclusive
        ("7.4.0 <7.4.33", False),
    ],
    ids=[
        "range-above",
        "single-above",
        "boundary-exclusive",
        "boundary-inclusive",
        "below",
    ],
)
def test_min_version_with_usable_versions(
    make_vulnerability: VulnerabilityFactory, version_text: str, reached: bool
) -> None:
    vulnerability = make_vulnerability(products=[("PHP", None, version_text)])
    result = match(vulnerability, _endpoint(PHP_8_2))
    if reached:
        assert result is not None
        assert result.matches[0].versions_unknown is False
        assert result.labels == frozenset()
    else:
        assert result is None


@pytest.mark.parametrize(
    "version_text",
    [None, "n/a", "-", "8.3.x", "versions antérieures à 8.2.29", "0 <2.0.84.Final"],
    ids=["missing", "n/a", "dash", "wildcard", "french", "unparseable-bound"],
)
def test_min_version_with_unusable_versions_passes_labelled(
    make_vulnerability: VulnerabilityFactory, version_text: str | None
) -> None:
    vulnerability = make_vulnerability(products=[("PHP", None, version_text)])
    result = match(vulnerability, _endpoint(PHP_8_2))
    assert result is not None
    assert result.matches[0].versions_unknown is True
    assert result.labels == frozenset({PassThrough.VERSIONS_UNKNOWN})


def test_min_version_with_description_only_match_passes_labelled(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    result = match(make_vulnerability(description="PHP 8.3 flaw"), _endpoint(PHP_8_2))
    assert result is not None
    assert result.matches[0].structured is False
    assert result.matches[0].versions_unknown is True
    assert result.labels == frozenset({PassThrough.VERSIONS_UNKNOWN})


def test_mixed_hits_one_reaching_one_unparseable_has_no_label(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        products=[("PHP", None, "n/a"), ("PHP", None, "8.0 <8.2.29")]
    )
    result = match(vulnerability, _endpoint(PHP_8_2))
    assert result is not None
    assert result.matches[0].versions_unknown is False
    assert result.labels == frozenset()


def test_mixed_hits_none_reaching_one_unparseable_is_unknown(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        products=[("PHP", None, "n/a"), ("PHP", None, "7.0 <7.4")]
    )
    result = match(vulnerability, _endpoint(PHP_8_2))
    assert result is not None
    assert result.matches[0].versions_unknown is True


REDIS_7 = ProductEntry(name="redis", min_version=Version("7.0"))


@pytest.mark.parametrize(
    ("products", "versions_unknown"),
    [
        ([("RediSearch", "Redis", "2.0 <2.8.10")], True),
        (
            [("RediSearch", "Redis", "2.0 <2.8.10"), ("Redis", "Redis", "7.0 <7.2.5")],
            False,
        ),
        (
            [("RediSearch", "Redis", "2.0 <2.8.10"), ("Redis", "Redis", "6.0 <6.2.14")],
            True,
        ),
    ],
    ids=["vendor-only", "product-reaching", "product-below"],
)
def test_vendor_only_hit_versions_are_unusable(
    make_vulnerability: VulnerabilityFactory,
    products: list[tuple[str, str, str]],
    versions_unknown: bool,
) -> None:
    result = match(make_vulnerability(products=products), _endpoint(REDIS_7))
    assert result is not None
    assert result.matches[0].structured is True
    assert result.matches[0].versions_unknown is versions_unknown


def test_structured_versions_win_over_a_description_mention(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        description="PHP flaw fixed in 7.4.33", products=[("PHP", None, "7.0 <7.4.33")]
    )
    assert match(vulnerability, _endpoint(PHP_8_2)) is None


def test_missing_versions_without_min_version_is_not_a_pass_through(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(products=[("PHP", None, "n/a")])
    result = match(vulnerability, _endpoint(PHP))
    assert result is not None
    assert result.matches[0].versions_unknown is False
    assert result.labels == frozenset()


def test_versions_unknown_label_only_when_every_match_is_unknown(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    redis = ProductEntry(name="redis", min_version=Version("7.0"))
    vulnerability = make_vulnerability(
        products=[("PHP", None, "8.0 <8.2.29"), ("Redis", None, "n/a")]
    )
    result = match(vulnerability, _endpoint(PHP_8_2, redis))
    assert result is not None
    assert [m.versions_unknown for m in result.matches] == [False, True]
    assert result.labels == frozenset()

    only_unknown = match(make_vulnerability(description="redis flaw"), _endpoint(redis))
    assert only_unknown is not None
    assert only_unknown.labels == frozenset({PassThrough.VERSIONS_UNKNOWN})


# The four rows of PHP2Shell (EUVD-2026-107260) in the PHP CNA branch-wildcard
# format, in the order the live record lists them (wixyvir/cvestream#11).
_PHP2SHELL_ROWS = [
    ("PHP", "PHP Group", "8.2.* <8.2.35"),
    ("PHP", "PHP Group", "8.5.* <8.5.12"),
    ("PHP", "PHP Group", "8.4.* <8.4.27"),
    ("PHP", "PHP Group", "8.3.* <8.3.36"),
]


@pytest.mark.parametrize(
    ("entry", "matches"),
    [
        (PHP, True),
        (PHP_8_2, True),
        (ProductEntry(name="php", min_version=Version("8.5")), True),
        (ProductEntry(name="php", min_version=Version("9.0")), False),
    ],
    ids=["php", "php-8.2", "php-8.5", "php-9.0"],
)
def test_php_branch_wildcard_rows_have_known_versions(
    make_vulnerability: VulnerabilityFactory, entry: ProductEntry, matches: bool
) -> None:
    vulnerability = make_vulnerability(products=_PHP2SHELL_ROWS)
    result = match(vulnerability, _endpoint(entry))
    if matches:
        assert result is not None
        assert result.matches[0].structured is True
        assert result.matches[0].versions_unknown is False
        assert result.labels == frozenset()
    else:
        assert result is None


# --- CVSS threshold (FR-14, FR-24, FR-25) ----------------------------------------


@pytest.mark.parametrize(
    ("scores", "min_cvss", "kept"),
    [
        ([9.8], 7.0, True),
        ([5.0, 9.8], 7.0, True),  # highest wins
        ([9.8, 5.0], 7.0, True),
        ([6.9], 7.0, False),
        ([5.0, 6.5], 7.0, False),
        ([7.0], 7.0, True),  # equal to the threshold passes
        ([0.0], 7.0, False),  # a real 0.0 is scored
    ],
    ids=[
        "above",
        "conflict",
        "conflict-reversed",
        "below",
        "all-below",
        "equal",
        "zero",
    ],
)
def test_min_cvss_on_scored_vulnerabilities(
    make_vulnerability: VulnerabilityFactory,
    scores: list[float | None],
    min_cvss: float,
    kept: bool,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=scores)
    result = match(vulnerability, _endpoint(PHP, min_cvss=min_cvss))
    assert (result is not None) is kept
    if result is not None:
        assert result.labels == frozenset()


@pytest.mark.parametrize(
    "scores", [[], [None], [None, None]], ids=["empty", "none", "nones"]
)
def test_unscored_passes_with_score_unknown(
    make_vulnerability: VulnerabilityFactory, scores: list[float | None]
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=scores)
    result = match(vulnerability, _endpoint(PHP, min_cvss=7.0))
    assert result is not None
    assert result.labels == frozenset({PassThrough.SCORE_UNKNOWN})


def test_unscored_without_min_cvss_has_no_label(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    result = match(make_vulnerability(description="PHP flaw"), _endpoint(PHP))
    assert result is not None
    assert result.labels == frozenset()


def test_one_scoring_source_is_enough_when_another_says_unscored(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=[None, 9.8])
    result = match(vulnerability, _endpoint(PHP, min_cvss=7.0))
    assert result is not None
    assert result.labels == frozenset()


def test_labels_combine(make_vulnerability: VulnerabilityFactory) -> None:
    result = match(
        make_vulnerability(description="PHP flaw"), _endpoint(PHP_8_2, min_cvss=7.0)
    )
    assert result is not None
    assert result.labels == frozenset(
        {PassThrough.VERSIONS_UNKNOWN, PassThrough.SCORE_UNKNOWN}
    )


# --- require_score (FR-14, FR-25 opt-out) ----------------------------------------


@pytest.mark.parametrize(
    "scores", [[], [None], [None, None]], ids=["empty", "none", "nones"]
)
def test_unscored_dropped_with_require_score(
    make_vulnerability: VulnerabilityFactory, scores: list[float | None]
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=scores)
    result = match(vulnerability, _endpoint(PHP, require_score=True))
    assert result is None


@pytest.mark.parametrize("scores", [[], [None]], ids=["empty", "none"])
def test_unscored_dropped_with_require_score_and_threshold(
    make_vulnerability: VulnerabilityFactory, scores: list[float | None]
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=scores)
    result = match(vulnerability, _endpoint(PHP, require_score=True, min_cvss=4.0))
    assert result is None


@pytest.mark.parametrize("scores", [[], [None]], ids=["empty", "none"])
def test_unscored_cisa_kev_kept_with_require_score_and_threshold(
    make_vulnerability: VulnerabilityFactory, scores: list[float | None]
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=scores, kev=True)
    result = match(vulnerability, _endpoint(PHP, require_score=True, min_cvss=4.0))
    assert result is not None
    assert result.labels == frozenset({PassThrough.SCORE_UNKNOWN})


@pytest.mark.parametrize("scores", [[], [None]], ids=["empty", "none"])
def test_unscored_cisa_kev_kept_with_require_score_no_threshold(
    make_vulnerability: VulnerabilityFactory, scores: list[float | None]
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=scores, kev=True)
    result = match(vulnerability, _endpoint(PHP, require_score=True))
    assert result is not None
    assert result.labels == frozenset()


def test_unscored_eu_kev_kept_with_require_score(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        description="PHP flaw", scores=[], kev=True, kev_catalog=KevCatalog.EU_KEV
    )
    result = match(vulnerability, _endpoint(PHP, require_score=True))
    assert result is not None
    assert result.labels == frozenset()


def test_one_source_scored_kept_with_require_score(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=[None, 9.8])
    result = match(vulnerability, _endpoint(PHP, require_score=True, min_cvss=7.0))
    assert result is not None
    assert result.labels == frozenset()


def test_real_zero_score_kept_with_require_score(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=[0.0])
    result = match(vulnerability, _endpoint(PHP, require_score=True))
    assert result is not None
    assert result.labels == frozenset()


def test_scored_below_threshold_dropped_with_require_score(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=[5.0])
    result = match(vulnerability, _endpoint(PHP, require_score=True, min_cvss=7.0))
    assert result is None


def test_scored_no_threshold_kept_with_require_score(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=[5.0])
    result = match(vulnerability, _endpoint(PHP, require_score=True))
    assert result is not None
    assert result.labels == frozenset()


def test_default_off_keeps_unscored(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw")
    result = match(vulnerability, _endpoint(PHP, min_cvss=7.0))
    assert result is not None
    assert result.labels == frozenset({PassThrough.SCORE_UNKNOWN})


def test_with_kev_only_and_require_score(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", kev=True)
    result = match(vulnerability, _endpoint(PHP, require_score=True, kev_only=True))
    assert result is not None
    result = match(
        make_vulnerability(description="PHP flaw", kev=False),
        _endpoint(PHP, require_score=True, kev_only=True),
    )
    assert result is None


def test_versions_label_still_set_with_require_score(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", scores=[9.8])
    result = match(vulnerability, _endpoint(PHP_8_2, require_score=True))
    assert result is not None
    assert result.labels == frozenset({PassThrough.VERSIONS_UNKNOWN})


# --- KEV only (FR-15) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("kev", "kept"), [(True, True), (False, False)], ids=["exploited", "not-exploited"]
)
def test_kev_only(
    make_vulnerability: VulnerabilityFactory, kev: bool, kept: bool
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", kev=kev)
    result = match(vulnerability, _endpoint(PHP, kev_only=True))
    assert (result is not None) is kept


def test_kev_is_ignored_when_not_required(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(description="PHP flaw", kev=False)
    assert match(vulnerability, _endpoint(PHP)) is not None


def test_kev_only_and_min_cvss_both_apply(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    endpoint = _endpoint(PHP, min_cvss=7.0, kev_only=True)
    low = make_vulnerability(description="PHP flaw", scores=[3.0], kev=True)
    assert match(low, endpoint) is None


# --- result (NFR-4) -----------------------------------------------------------------


def test_result_identifies_endpoint_vulnerability_and_entries(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        primary_id="CVE-2026-4242", description="PHP flaw"
    )
    result = match(vulnerability, _endpoint(PHP))
    assert result is not None
    assert result.endpoint_id == "team-a"
    assert result.vulnerability_id == "CVE-2026-4242"
    assert result.matches[0].entry is PHP
