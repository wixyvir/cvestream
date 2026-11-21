"""Match a vulnerability against an endpoint (FR-11 to FR-15, FR-24, FR-25).

Recall over precision: unknown data lets the item through with a label, it never
drops it silently, unless an endpoint opts out for scores with `require_score`;
a known-exploited vulnerability is still let through. Matching is case-insensitive
on whole words. Structured
affected-product data decides when a vulnerability has any: a description that
mentions `php` (`Module.php`, "PHP Object Injection" in a WordPress plugin) does
not make another product a PHP vulnerability. The descriptions are searched only
when there is no affected-product data at all, and then a name inside a file name
or path (`loan_history_maintenance.php`, `/usr/lib/php/`) does not count. An entry
with `match_description` false never uses the description, so a vulnerability
known only from CISA KEV waits for EUVD product data. An entry with `exact`
true matches only an affected product whose whole name equals its name or an
alias: vendor names and the description are never used for matching. Titles are
never searched. Exclusion terms are looked for in both.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cache

from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.domain.model import AffectedProduct, Vulnerability
from cvestream.domain.severity import max_score
from cvestream.domain.version import parse_range


class PassThrough(StrEnum):
    """Why an item got through although something could not be checked."""

    VERSIONS_UNKNOWN = "versions_unknown"
    SCORE_UNKNOWN = "score_unknown"


@dataclass(frozen=True, slots=True)
class ProductMatch:
    entry: ProductEntry
    # True: matched affected-product data; False: description only (the
    # vulnerability has no affected-product data).
    structured: bool
    # True only when `entry.min_version` is set and could not be checked.
    versions_unknown: bool


@dataclass(frozen=True, slots=True)
class MatchResult:
    endpoint_id: str
    # `vulnerability.primary_id` at match time (display and logs, not a GUID).
    vulnerability_id: str
    # In `endpoint.products` order; `matches[0]` names the item.
    matches: tuple[ProductMatch, ...]
    labels: frozenset[PassThrough]


def _words(term: str) -> str:
    return r"\s+".join(re.escape(word) for word in term.split())


@cache
def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){_words(term)}(?!\w)", re.IGNORECASE)


@cache
def _description_pattern(term: str) -> re.Pattern[str]:
    # A name inside a file name or path is not a mention: `x/maintenance.php`,
    # `/usr/lib/php/`, `C:\php\`. `php.ini`, `php.net` and "Node.js" still are.
    # Assumed risk: `Apache/2.4.49` and `Linux/Unix` read as paths too.
    return re.compile(rf"(?<![\w./\\]){_words(term)}(?![\w/\\])", re.IGNORECASE)


def _mentions(terms: tuple[str, ...], text: str | None) -> bool:
    return text is not None and any(_pattern(term).search(text) for term in terms)


def _mentioned_in_prose(terms: tuple[str, ...], text: str) -> bool:
    return any(_description_pattern(term).search(text) for term in terms)


def _hits(
    terms: tuple[str, ...], affected: list[AffectedProduct]
) -> list[AffectedProduct]:
    return [
        a for a in affected if _mentions(terms, a.product) or _mentions(terms, a.vendor)
    ]


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _is_named(terms: tuple[str, ...], product: str) -> bool:
    # Exact entries: the whole product name, not a word inside it.
    name = _normalized(product)
    return any(_normalized(term) == name for term in terms)


def _names_product(entry: ProductEntry, terms: tuple[str, ...], product: str) -> bool:
    return _is_named(terms, product) if entry.exact else _mentions(terms, product)


def _entry_hits(
    entry: ProductEntry, terms: tuple[str, ...], affected: list[AffectedProduct]
) -> list[AffectedProduct]:
    if entry.exact:
        # Vendor names never count: vendor `PHP` ships `frankenphp`.
        return [a for a in affected if _is_named(terms, a.product)]
    return _hits(terms, affected)


def affected_hits(
    entry: ProductEntry, affected: list[AffectedProduct]
) -> list[AffectedProduct]:
    """The affected products that `entry` names: product-name hits first.

    The same rule as matching, for display: the render names the product a
    vulnerability was matched on, not the endpoint's term. A vendor-only hit
    (another product of the vendor) comes after every product-name hit.
    """
    terms = (entry.name, *entry.aliases)
    hits = _entry_hits(entry, terms, affected)
    return sorted(hits, key=lambda a: not _names_product(entry, terms, a.product))


def _match_entry(
    entry: ProductEntry,
    affected: list[AffectedProduct],
    descriptions: list[str],
) -> ProductMatch | None:
    if entry.exclude and (
        _hits(entry.exclude, affected)
        or any(_mentions(entry.exclude, d) for d in descriptions)
    ):
        return None
    terms = (entry.name, *entry.aliases)
    hits = _entry_hits(entry, terms, affected)
    if not hits:
        # Structured data wins: the description is only a fallback for a
        # vulnerability without any affected product, and an entry can opt out.
        if (
            affected
            or entry.exact
            or not entry.match_description
            or not any(_mentioned_in_prose(terms, d) for d in descriptions)
        ):
            return None
    if entry.min_version is None:
        return ProductMatch(entry, structured=bool(hits), versions_unknown=False)
    if not hits:
        return ProductMatch(entry, structured=False, versions_unknown=True)
    # A vendor-only hit carries another product's versions: unusable here.
    ranges = [
        parse_range(hit.version_text)
        if _names_product(entry, terms, hit.product)
        else None
        for hit in hits
    ]
    if any(r is not None and r.reaches(entry.min_version) for r in ranges):
        return ProductMatch(entry, structured=True, versions_unknown=False)
    if any(r is None for r in ranges):
        return ProductMatch(entry, structured=True, versions_unknown=True)
    # Every product-name hit has known versions and none reaches the minimum:
    # structured data wins over a mention in the description.
    return None


def match(vulnerability: Vulnerability, endpoint: Endpoint) -> MatchResult | None:
    """Match `vulnerability` against `endpoint`; `None` when it does not match."""
    affected = [a.value for a in vulnerability.affected]
    descriptions = [d.value for d in vulnerability.descriptions]
    matches = tuple(
        m
        for entry in endpoint.products
        if (m := _match_entry(entry, affected, descriptions)) is not None
    )
    if not matches:
        return None

    rules = endpoint.rules
    if rules.kev_only and not vulnerability.is_known_exploited:
        return None
    score = max_score(vulnerability.scores)
    # Opt-out of FR-25 for scores: an exploited vulnerability is never silenced.
    if rules.require_score and score is None and not vulnerability.is_known_exploited:
        return None
    labels: set[PassThrough] = set()
    if rules.min_cvss is not None:
        if score is None:
            labels.add(PassThrough.SCORE_UNKNOWN)
        elif score < rules.min_cvss:
            return None
    if all(m.versions_unknown for m in matches):
        labels.add(PassThrough.VERSIONS_UNKNOWN)
    return MatchResult(
        endpoint_id=endpoint.id,
        vulnerability_id=vulnerability.primary_id,
        matches=matches,
        labels=frozenset(labels),
    )
