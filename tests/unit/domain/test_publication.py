from cvestream.domain.endpoint import ProductEntry
from cvestream.domain.matcher import MatchResult, PassThrough, ProductMatch
from cvestream.domain.publication import (
    BACKFILL_WINDOW,
    PUBLICATION_WINDOW,
    PastPublication,
    band_kind,
    decide,
)
from cvestream.domain.severity import Band
from tests.unit.domain.conftest import VulnerabilityFactory

ENDPOINT = "team-php"


def _match(
    primary_id: str = "CVE-2026-1234",
    products: tuple[str, ...] = ("php",),
    labels: frozenset[PassThrough] = frozenset(),
) -> MatchResult:
    return MatchResult(
        endpoint_id=ENDPOINT,
        vulnerability_id=primary_id,
        matches=tuple(
            ProductMatch(ProductEntry(name=p), structured=True, versions_unknown=False)
            for p in products
        ),
        labels=labels,
    )


def _past(
    kind: str = "new",
    band: Band = Band.UNKNOWN,
    *,
    kev: bool = False,
    primary_id: str = "CVE-2026-1234",
) -> PastPublication:
    return PastPublication(
        kind=kind,
        guid=f"cvestream:{ENDPOINT}:{primary_id}:{kind}",
        band=band,
        is_known_exploited=kev,
    )


def test_windows() -> None:
    assert BACKFILL_WINDOW.days == 7
    assert PUBLICATION_WINDOW.days == 14


def test_band_kind() -> None:
    assert band_kind(Band.HIGH) == "band-high"


def test_first_match_is_new(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(primary_id="CVE-2026-1234", scores=[7.5])
    decision = decide(_match(), vulnerability, [])
    assert decision is not None
    assert decision.kind == "new"
    assert decision.guid == "cvestream:team-php:CVE-2026-1234:new"
    assert decision.band is Band.HIGH
    assert decision.is_known_exploited is False
    assert decision.endpoint_id == ENDPOINT


def test_first_match_is_new_even_when_exploited_and_scored(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    decision = decide(_match(), make_vulnerability(scores=[9.8], kev=True), [])
    assert decision is not None
    assert decision.kind == "new"
    assert decision.is_known_exploited is True


def test_becoming_exploited_is_kev(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(
        primary_id="CVE-2026-1234", scores=[7.5], kev=True
    )
    decision = decide(_match(), vulnerability, [_past("new", Band.HIGH)])
    assert decision is not None
    assert decision.kind == "kev"
    assert decision.guid == "cvestream:team-php:CVE-2026-1234:kev"


def test_already_published_as_exploited_is_not_kev_again(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[7.5], kev=True)
    history = [_past("new", Band.HIGH, kev=True)]
    assert decide(_match(), vulnerability, history) is None


def test_kev_published_earlier_blocks_a_second_kev(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[7.5], kev=True)
    history = [_past("new", Band.HIGH), _past("kev", Band.HIGH, kev=True)]
    assert decide(_match(), vulnerability, history) is None


def test_band_rise(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(scores=[7.5])
    decision = decide(_match(), vulnerability, [_past("new", Band.MEDIUM)])
    assert decision is not None
    assert decision.kind == "band-high"
    assert decision.band is Band.HIGH


def test_unknown_to_rated_is_a_rise(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(scores=[3.0])
    decision = decide(_match(), vulnerability, [_past("new", Band.UNKNOWN)])
    assert decision is not None
    assert decision.kind == "band-low"


def test_unknown_to_none_counts_as_a_rise(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[0.0])
    decision = decide(_match(), vulnerability, [_past("new", Band.UNKNOWN)])
    assert decision is not None
    assert decision.kind == "band-none"


def test_score_change_within_the_band_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[8.5])
    assert decide(_match(), vulnerability, [_past("new", Band.HIGH)]) is None


def test_band_drop_is_nothing(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(scores=[7.5])
    assert decide(_match(), vulnerability, [_past("new", Band.CRITICAL)]) is None


def test_lower_than_the_max_published_band_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[7.5])
    history = [_past("new", Band.MEDIUM), _past("band-critical", Band.CRITICAL)]
    assert decide(_match(), vulnerability, history) is None


def test_unscored_after_published_scored_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[None])
    assert decide(_match(), vulnerability, [_past("new", Band.HIGH)]) is None


def test_unchanged_vulnerability_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[5.0], description="edited text")
    assert decide(_match(), vulnerability, [_past("new", Band.MEDIUM)]) is None


def test_kev_and_band_rise_together_make_one_kev_decision(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[9.8], kev=True)
    history = [_past("new", Band.MEDIUM)]
    decision = decide(_match(), vulnerability, history)
    assert decision is not None
    assert decision.kind == "kev"
    assert decision.band is Band.CRITICAL
    # the band rise is absorbed: the next run at the same band decides nothing
    absorbed = [*history, _past("kev", Band.CRITICAL, kev=True)]
    assert decide(_match(), vulnerability, absorbed) is None


def test_guid_uses_the_current_primary_id_and_leaves_history_untouched(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    # published as EUVD-2026-5 before its CVE ID arrived
    history = [_past("new", Band.MEDIUM, primary_id="EUVD-2026-5")]
    vulnerability = make_vulnerability(
        primary_id="CVE-2026-1234", aliases=["EUVD-2026-5"], scores=[9.8]
    )
    decision = decide(_match("CVE-2026-1234"), vulnerability, history)
    assert decision is not None
    assert decision.guid == "cvestream:team-php:CVE-2026-1234:band-critical"
    assert history[0].guid == "cvestream:team-php:EUVD-2026-5:new"


def test_guid_format(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(primary_id="CVE-2026-1234", scores=[9.1])
    decision = decide(_match(), vulnerability, [_past("new", Band.LOW)])
    assert decision is not None
    assert decision.guid == "cvestream:team-php:CVE-2026-1234:band-critical"


def test_a_guid_already_in_history_is_never_decided_twice(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(primary_id="CVE-2026-1234", scores=[9.1])
    # e.g. after a merge: the band-critical row exists but its band was recorded low
    history = [
        _past("new", Band.LOW),
        PastPublication(
            "band-critical",
            "cvestream:team-php:CVE-2026-1234:band-critical",
            Band.LOW,
            False,
        ),
    ]
    assert decide(_match(), vulnerability, history) is None


def test_products_and_labels_come_from_the_match(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    labels = frozenset({PassThrough.VERSIONS_UNKNOWN})
    match = _match(products=("php", "nginx"), labels=labels)
    decision = decide(match, make_vulnerability(scores=[5.0]), [])
    assert decision is not None
    assert decision.products == ("php", "nginx")
    assert decision.labels == labels
