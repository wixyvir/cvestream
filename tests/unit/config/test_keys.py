"""The key layout is a public contract: the exact strings are asserted."""

import re

import pytest
from pydantic import TypeAdapter, ValidationError

from cvestream.config import keys
from cvestream.config.models import SCHEMA_VERSION, EndpointId

_SLUG = re.compile(r"^[a-z0-9-]{1,40}$")


def test_fixed_keys() -> None:
    assert keys.PREFIX == "cvestream:v1:"
    assert keys.SCHEMA == "cvestream:v1:schema"
    assert keys.REVISION == "cvestream:v1:revision"
    assert keys.ENDPOINTS == "cvestream:v1:endpoints"


def test_schema_value_is_the_model_version() -> None:
    assert keys.SCHEMA_VALUE == SCHEMA_VERSION == 1


def test_per_endpoint_keys() -> None:
    assert keys.endpoint("team-php") == "cvestream:v1:endpoint:team-php"
    assert keys.products("team-php") == "cvestream:v1:endpoint:team-php:products"
    assert keys.token("team-php") == "cvestream:v1:token:team-php"


@pytest.mark.parametrize("endpoint_id", ["a", "team-php", "0", "x-1-y", "a" * 40])
def test_only_the_token_builder_reaches_the_token_namespace(endpoint_id: str) -> None:
    assert _SLUG.match(endpoint_id)
    token_prefix = f"{keys.PREFIX}token:"
    assert keys.token(endpoint_id).startswith(token_prefix)
    for other in (
        keys.SCHEMA,
        keys.REVISION,
        keys.ENDPOINTS,
        keys.endpoint(endpoint_id),
        keys.products(endpoint_id),
    ):
        assert not other.startswith(token_prefix)


def test_every_key_is_under_the_prefix() -> None:
    for key in (
        keys.SCHEMA,
        keys.REVISION,
        keys.ENDPOINTS,
        keys.endpoint("a"),
        keys.products("a"),
        keys.token("a"),
    ):
        assert key.startswith(keys.PREFIX)


@pytest.mark.parametrize(
    "value",
    ["a", "team-php", "0", "-", "a" * 40, "a" * 41, "", "A", "a_b", "a:b", "a*"],
)
def test_endpoint_id_pattern_agrees_with_the_model(value: str) -> None:
    accepted = TypeAdapter(EndpointId).validate_python  # strict like the models
    try:
        accepted(value)
        model_accepts = True
    except ValidationError:
        model_accepts = False
    assert (keys.ENDPOINT_ID.fullmatch(value) is not None) is model_accepts
