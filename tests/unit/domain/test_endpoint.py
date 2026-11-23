import dataclasses

import pytest
from packaging.version import Version

from cvestream.domain.endpoint import Endpoint, ProductEntry, Rules


def test_product_entry_defaults() -> None:
    entry = ProductEntry(name="PHP")
    assert entry.aliases == ()
    assert entry.exclude == ()
    assert entry.min_version is None
    assert entry.match_description is True
    assert entry.exact is False


def test_rules_default_to_no_filtering() -> None:
    rules = Rules()
    assert rules.min_cvss is None
    assert rules.kev_only is False
    assert rules.require_score is False
    assert rules.group_by_product is False


def test_endpoint_defaults_and_nesting() -> None:
    entry = ProductEntry(
        name="PHP",
        aliases=("php-src",),
        exclude=("wordpress",),
        min_version=Version("8.2"),
    )
    endpoint = Endpoint(id="team-a", name="Team A", products=(entry,))
    assert endpoint.rules == Rules()
    assert endpoint.enabled is True
    assert endpoint.products[0].min_version == Version("8.2")


def test_endpoint_carries_no_token() -> None:
    names = {f.name for f in dataclasses.fields(Endpoint)}
    assert not {n for n in names if "token" in n}


def test_endpoint_is_frozen_slotted_and_hashable() -> None:
    endpoint = Endpoint(
        id="team-a",
        name="Team A",
        products=(ProductEntry(name="PHP"),),
        rules=Rules(min_cvss=7.0, kev_only=True),
    )
    assert isinstance(hash(endpoint), int)
    assert "__dict__" not in dir(endpoint)
    field = "enabled"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(endpoint, field, False)
