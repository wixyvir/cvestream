import hashlib
import itertools
from datetime import UTC, datetime, timedelta, timezone

from cvestream.domain.endpoint import ProductEntry
from cvestream.domain.matcher import MatchResult, PassThrough, ProductMatch
from cvestream.domain.publication import (
    BACKFILL_WINDOW,
    GROUP_MAX_MEMBERS,
    GROUP_MIN_MEMBERS,
    PUBLICATION_WINDOW,
    PastPublication,
    PublicationDecision,
    PublicationGroup,
    band_kind,
    decide,
    group_decisions,
    kind_family,
    member_order_key,
)
from cvestream.domain.severity import Band
from tests.unit.domain.conftest import VulnerabilityFactory

ENDPOINT = "team-php"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


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
    decision = decide(_match(), vulnerability, [], NOW)
    assert decision is not None
    assert decision.kind == "new"
    assert decision.guid == "cvestream:team-php:CVE-2026-1234:new"
    assert decision.band is Band.HIGH
    assert decision.is_known_exploited is False
    assert decision.endpoint_id == ENDPOINT


def test_first_match_is_new_even_when_exploited_and_scored(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    decision = decide(_match(), make_vulnerability(scores=[9.8], kev=True), [], NOW)
    assert decision is not None
    assert decision.kind == "new"
    assert decision.is_known_exploited is True


def test_becoming_exploited_is_kev(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(
        primary_id="CVE-2026-1234", scores=[7.5], kev=True
    )
    decision = decide(_match(), vulnerability, [_past("new", Band.HIGH)], NOW)
    assert decision is not None
    assert decision.kind == "kev"
    assert decision.guid == "cvestream:team-php:CVE-2026-1234:kev"


def test_already_published_as_exploited_is_not_kev_again(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[7.5], kev=True)
    history = [_past("new", Band.HIGH, kev=True)]
    assert decide(_match(), vulnerability, history, NOW) is None


def test_kev_published_earlier_blocks_a_second_kev(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[7.5], kev=True)
    history = [_past("new", Band.HIGH), _past("kev", Band.HIGH, kev=True)]
    assert decide(_match(), vulnerability, history, NOW) is None


def test_band_rise(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(scores=[7.5])
    decision = decide(_match(), vulnerability, [_past("new", Band.MEDIUM)], NOW)
    assert decision is not None
    assert decision.kind == "band-high"
    assert decision.band is Band.HIGH


def test_unknown_to_rated_is_a_rise(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(scores=[3.0])
    decision = decide(_match(), vulnerability, [_past("new", Band.UNKNOWN)], NOW)
    assert decision is not None
    assert decision.kind == "band-low"


def test_unknown_to_none_counts_as_a_rise(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[0.0])
    decision = decide(_match(), vulnerability, [_past("new", Band.UNKNOWN)], NOW)
    assert decision is not None
    assert decision.kind == "band-none"


def test_score_change_within_the_band_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[8.5])
    assert decide(_match(), vulnerability, [_past("new", Band.HIGH)], NOW) is None


def test_band_drop_is_nothing(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(scores=[7.5])
    assert decide(_match(), vulnerability, [_past("new", Band.CRITICAL)], NOW) is None


def test_lower_than_the_max_published_band_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[7.5])
    history = [_past("new", Band.MEDIUM), _past("band-critical", Band.CRITICAL)]
    assert decide(_match(), vulnerability, history, NOW) is None


def test_unscored_after_published_scored_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[None])
    assert decide(_match(), vulnerability, [_past("new", Band.HIGH)], NOW) is None


def test_unchanged_vulnerability_is_nothing(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[5.0], description="edited text")
    assert decide(_match(), vulnerability, [_past("new", Band.MEDIUM)], NOW) is None


def test_kev_and_band_rise_together_make_one_kev_decision(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(scores=[9.8], kev=True)
    history = [_past("new", Band.MEDIUM)]
    decision = decide(_match(), vulnerability, history, NOW)
    assert decision is not None
    assert decision.kind == "kev"
    assert decision.band is Band.CRITICAL
    # the band rise is absorbed: the next run at the same band decides nothing
    absorbed = [*history, _past("kev", Band.CRITICAL, kev=True)]
    assert decide(_match(), vulnerability, absorbed, NOW) is None


def test_guid_uses_the_current_primary_id_and_leaves_history_untouched(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    # published as EUVD-2026-5 before its CVE ID arrived
    history = [_past("new", Band.MEDIUM, primary_id="EUVD-2026-5")]
    vulnerability = make_vulnerability(
        primary_id="CVE-2026-1234", aliases=["EUVD-2026-5"], scores=[9.8]
    )
    decision = decide(_match("CVE-2026-1234"), vulnerability, history, NOW)
    assert decision is not None
    assert decision.guid == "cvestream:team-php:CVE-2026-1234:band-critical"
    assert history[0].guid == "cvestream:team-php:EUVD-2026-5:new"


def test_guid_format(make_vulnerability: VulnerabilityFactory) -> None:
    vulnerability = make_vulnerability(primary_id="CVE-2026-1234", scores=[9.1])
    decision = decide(_match(), vulnerability, [_past("new", Band.LOW)], NOW)
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
    assert decide(_match(), vulnerability, history, NOW) is None


def test_products_and_labels_come_from_the_match(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    labels = frozenset({PassThrough.VERSIONS_UNKNOWN})
    match = _match(products=("php", "nginx"), labels=labels)
    decision = decide(match, make_vulnerability(scores=[5.0]), [], NOW)
    assert decision is not None
    assert decision.products == ("php", "nginx")
    assert decision.labels == labels


# --- freshness (story 5.1): the decision time is an input, never a clock ---


def test_new_on_the_publication_day_is_fresh(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        scores=[7.5], published_at=datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    )
    decision = decide(_match(), vulnerability, [], NOW)
    assert decision is not None
    assert decision.is_fresh is True


def test_new_at_both_day_edges_is_fresh(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    for hour in (0, 23):
        vulnerability = make_vulnerability(
            scores=[7.5], published_at=datetime(2026, 11, 21, hour, 59, tzinfo=UTC)
        )
        decision = decide(_match(), vulnerability, [], NOW)
        assert decision is not None
        assert decision.is_fresh is True


def test_new_on_the_day_before_is_not_fresh(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        scores=[7.5], published_at=datetime(2026, 11, 20, 23, 59, tzinfo=UTC)
    )
    decision = decide(_match(), vulnerability, [], NOW)
    assert decision is not None
    assert decision.is_fresh is False


def test_a_non_utc_published_at_is_compared_in_utc(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    # 2026-11-20 23:30 at UTC-2 is 2026-11-21 01:30 UTC: the same UTC day.
    published = datetime(2026, 11, 20, 23, 30, tzinfo=timezone(timedelta(hours=-2)))
    vulnerability = make_vulnerability(scores=[7.5], published_at=published)
    decision = decide(_match(), vulnerability, [], NOW)
    assert decision is not None
    assert decision.is_fresh is True


def test_unknown_published_at_is_not_fresh(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    decision = decide(_match(), make_vulnerability(scores=[7.5]), [], NOW)
    assert decision is not None
    assert decision.is_fresh is False


def test_kev_on_the_publication_day_is_not_fresh(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        scores=[7.5],
        kev=True,
        published_at=datetime(2026, 11, 21, 8, 0, tzinfo=UTC),
    )
    decision = decide(_match(), vulnerability, [_past("new", Band.HIGH)], NOW)
    assert decision is not None
    assert decision.kind == "kev"
    assert decision.is_fresh is False


def test_a_band_rise_on_the_publication_day_is_not_fresh(
    make_vulnerability: VulnerabilityFactory,
) -> None:
    vulnerability = make_vulnerability(
        scores=[9.8], published_at=datetime(2026, 11, 21, 8, 0, tzinfo=UTC)
    )
    decision = decide(_match(), vulnerability, [_past("new", Band.MEDIUM)], NOW)
    assert decision is not None
    assert decision.kind == "band-critical"
    assert decision.is_fresh is False


# --- grouping (story 5.2, FR-27): a second pure step over one run's decisions ---


def _decision(
    primary_id: str,
    band: Band,
    *,
    kind: str = "new",
    product: str = "tomcat",
    known: bool = False,
    fresh: bool = False,
    endpoint: str = "team",
    products: tuple[str, ...] | None = None,
) -> PublicationDecision:
    return PublicationDecision(
        endpoint_id=endpoint,
        primary_id=primary_id,
        kind=kind,
        guid=f"cvestream:{endpoint}:{primary_id}:{kind}",
        band=band,
        is_known_exploited=known,
        products=products if products is not None else (product,),
        labels=frozenset(),
        is_fresh=fresh,
    )


def _cves(
    *numbers: int,
    band: Band = Band.HIGH,
    kind: str = "new",
    product: str = "tomcat",
    known: bool = False,
    products: tuple[str, ...] | None = None,
) -> list[PublicationDecision]:
    return [
        _decision(
            f"CVE-2026-{n}",
            band,
            kind=kind,
            product=product,
            known=known,
            products=products,
        )
        for n in numbers
    ]


def test_kind_family() -> None:
    assert kind_family("new") == "new"
    assert kind_family("band-high") == "band"
    assert kind_family("band-critical") == "band"
    assert kind_family("kev") is None
    assert kind_family("future") is None


def test_member_order_key_band_descending_then_natural_id() -> None:
    assert member_order_key(Band.CRITICAL, "CVE-2026-1") < member_order_key(
        Band.HIGH, "CVE-2026-1"
    )
    assert member_order_key(Band.HIGH, "CVE-2026-9999") < member_order_key(
        Band.HIGH, "CVE-2026-10000"
    )
    assert member_order_key(Band.LOW, "CVE-2026-1") < member_order_key(
        Band.UNKNOWN, "CVE-2026-1"
    )


def test_three_same_product_new_decisions_form_one_group() -> None:
    groups = group_decisions(_cves(1, 2, 3))
    assert len(groups) == 1
    group = groups[0]
    assert isinstance(group, PublicationGroup)
    assert group.family == "new"
    assert group.product == "tomcat"
    assert group.endpoint_id == "team"
    assert [m.primary_id for m in group.members] == [
        "CVE-2026-1",
        "CVE-2026-2",
        "CVE-2026-3",
    ]
    assert (group.part, group.parts) == (1, 1)
    assert group.guid.startswith("cvestream:team:group-new:")


def test_two_decisions_stay_single() -> None:
    assert group_decisions(_cves(1, 2)) == ()


def test_a_known_exploited_decision_is_removed_before_the_threshold() -> None:
    decisions = [
        *_cves(1, 2),
        _decision("CVE-2026-3", Band.HIGH, known=True),
    ]
    assert group_decisions(decisions) == ()


def test_a_kev_decision_never_joins() -> None:
    decisions = [*_cves(1, 2), _decision("CVE-2026-3", Band.HIGH, kind="kev")]
    assert group_decisions(decisions) == ()


def test_new_and_band_of_one_product_are_separate_families() -> None:
    decisions = [
        *_cves(1, 2, 3),
        *[_decision(f"CVE-2026-{n}", Band.HIGH, kind="band-high") for n in (4, 5, 6)],
    ]
    groups = group_decisions(decisions)
    assert sorted(g.family for g in groups) == ["band", "new"]
    assert all(g.guid.startswith(f"cvestream:team:group-{g.family}:") for g in groups)
    # 2 + 2: below the threshold in each family
    mixed = [
        *_cves(1, 2),
        *[_decision(f"CVE-2026-{n}", Band.HIGH, kind="band-high") for n in (3, 4)],
    ]
    assert group_decisions(mixed) == ()


def test_two_products_of_three_each_form_two_groups() -> None:
    decisions = [*_cves(1, 2, 3, product="tomcat"), *_cves(4, 5, 6, product="php")]
    groups = group_decisions(decisions)
    assert sorted(g.product for g in groups) == ["php", "tomcat"]


def test_products_zero_decides_the_bucket() -> None:
    decisions = _cves(1, 2, 3, products=("php", "tomcat"))
    groups = group_decisions(decisions)
    assert len(groups) == 1
    assert groups[0].product == "php"


def test_members_are_ordered_by_band_then_natural_id() -> None:
    decisions = [
        _decision("CVE-2026-10000", Band.HIGH),
        _decision("CVE-2026-9999", Band.HIGH),
        _decision("CVE-2026-5", Band.CRITICAL),
        _decision("CVE-2026-7", Band.UNKNOWN),
        _decision("CVE-2026-8", Band.MEDIUM),
    ]
    (group,) = group_decisions(decisions)
    assert [m.primary_id for m in group.members] == [
        "CVE-2026-5",  # critical first
        "CVE-2026-9999",  # then high, natural order (not string order)
        "CVE-2026-10000",
        "CVE-2026-8",  # medium
        "CVE-2026-7",  # unknown last
    ]


def test_a_group_splits_into_balanced_chunks() -> None:
    assert GROUP_MIN_MEMBERS == 3
    assert GROUP_MAX_MEMBERS == 5
    six = group_decisions(_cves(*range(1, 7)))
    assert [(g.part, g.parts, len(g.members)) for g in six] == [(1, 2, 3), (2, 2, 3)]
    seven = group_decisions(_cves(*range(1, 8)))
    assert [(g.part, g.parts, len(g.members)) for g in seven] == [(1, 2, 4), (2, 2, 3)]
    ten = group_decisions(_cves(*range(1, 11)))
    assert [(g.part, g.parts, len(g.members)) for g in ten] == [
        (1, 2, 5),
        (2, 2, 5),
    ]
    eleven = group_decisions(_cves(*range(1, 12)))
    assert [(g.part, g.parts, len(g.members)) for g in eleven] == [
        (1, 3, 4),
        (2, 3, 4),
        (3, 3, 3),
    ]
    sixteen = group_decisions(_cves(*range(1, 17)))
    assert [(g.part, g.parts, len(g.members)) for g in sixteen] == [
        (1, 4, 4),
        (2, 4, 4),
        (3, 4, 4),
        (4, 4, 4),
    ]


def test_every_chunk_of_a_burst_keeps_at_least_three_members() -> None:
    for count in range(GROUP_MIN_MEMBERS, 21):
        groups = group_decisions(_cves(*range(1, count + 1)))
        assert groups
        for group in groups:
            assert GROUP_MIN_MEMBERS <= len(group.members) <= GROUP_MAX_MEMBERS


def test_part_one_holds_the_most_severe_members() -> None:
    decisions = [
        _decision("CVE-2026-1", Band.CRITICAL),
        *_cves(2, 3, 4, 5, 6),  # all high
    ]
    first, second = group_decisions(decisions)
    assert first.members[0].band is Band.CRITICAL
    assert all(m.band is Band.HIGH for m in second.members)
    assert first.part == 1
    assert second.part == 2


def test_the_group_guid_is_the_documented_sha256() -> None:
    member_guids = [
        f"cvestream:team:CVE-2026-{n}:new" for n in (9998, 9999, 10000, 10001)
    ]
    expected = hashlib.sha256(
        "\n".join(sorted(member_guids)).encode("utf-8")
    ).hexdigest()[:16]
    assert expected == "f92dff6898666ea1"  # golden literal
    (group,) = group_decisions(_cves(9998, 9999, 10000, 10001))
    assert group.guid == f"cvestream:team:group-new:{expected}"
    assert len(group.guid.rsplit(":", 1)[1]) == 16
    assert group.guid.rsplit(":", 1)[1] == group.guid.rsplit(":", 1)[1].lower()


def test_the_group_guid_does_not_depend_on_the_input_order() -> None:
    decisions = _cves(1, 2, 3, 4)
    reference = group_decisions(decisions)
    for permutation in itertools.permutations(decisions):
        assert group_decisions(list(permutation)) == reference
    assert group_decisions(list(reversed(decisions))) == reference


def test_different_chunks_get_different_guids() -> None:
    groups = group_decisions(_cves(*range(1, 7)))
    assert groups[0].guid != groups[1].guid


def test_the_result_order_does_not_depend_on_the_input_order() -> None:
    decisions = [
        *_cves(1, 2, 3, product="tomcat"),
        *_cves(4, 5, 6, product="php"),
        *_cves(7, 8, 9, band=Band.CRITICAL),
    ]
    reference = group_decisions(decisions)
    assert group_decisions(list(reversed(decisions))) == reference
    rotated = decisions[4:] + decisions[:4]
    assert group_decisions(rotated) == reference
    # most severe burst first
    assert reference[0].members[0].band is Band.CRITICAL
