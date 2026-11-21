from typing import Any

import pytest

from cvestream.config.models import EndpointConfig
from cvestream.config.provider import (
    ConfigError,
    ConfigProvider,
    ConfigUnavailableError,
    ConfigValidationError,
    build_snapshot,
)
from cvestream.errors import CvestreamError

TOKEN_A = "a" * 43
TOKEN_B = "b" * 43


def _data(*endpoints: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "endpoints": list(endpoints)}


def _endpoint(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "team-php", "name": "PHP", "token": TOKEN_A}
    base.update(overrides)
    return base


def _errors(data: object) -> tuple[str, ...]:
    with pytest.raises(ConfigValidationError) as excinfo:
        build_snapshot(data, revision="r", source="endpoints.yaml")
    return excinfo.value.errors


def test_validation_error_str_is_one_line() -> None:
    error = ConfigValidationError("f.yaml", ["a: bad", "b: worse", "c: worst"])
    assert str(error) == "f.yaml: a: bad (+2 more)"
    assert "\n" not in str(error)


def test_validation_error_single_has_no_suffix() -> None:
    error = ConfigValidationError("f.yaml", ["a: bad"])
    assert str(error) == "f.yaml: a: bad"
    assert error.source == "f.yaml"
    assert error.errors == ("a: bad",)


def test_validation_error_without_errors() -> None:
    assert str(ConfigValidationError("f.yaml", [])) == "f.yaml: invalid configuration"


def test_exception_family() -> None:
    for cls in (ConfigError, ConfigUnavailableError, ConfigValidationError):
        assert issubclass(cls, CvestreamError)
        assert cls.__name__.endswith("Error")
    assert issubclass(ConfigUnavailableError, ConfigError)
    assert issubclass(ConfigValidationError, ConfigError)


def test_build_snapshot_maps_tokens_to_endpoints() -> None:
    snapshot = build_snapshot(
        _data(_endpoint(), _endpoint(id="team-js", token=TOKEN_B, enabled=False)),
        revision="abc",
        source="endpoints.yaml",
    )
    assert snapshot.revision == "abc"
    assert [e.id for e in snapshot.endpoints] == ["team-php", "team-js"]
    assert dict(snapshot.tokens) == {TOKEN_A: "team-php", TOKEN_B: "team-js"}


def test_endpoint_for_token_hit_and_miss() -> None:
    snapshot = build_snapshot(_data(_endpoint()), revision="r", source="endpoints.yaml")
    hit = snapshot.endpoint_for_token(TOKEN_A)
    assert hit is not None
    assert hit.id == "team-php"
    assert snapshot.endpoint_for_token(TOKEN_B) is None
    assert snapshot.endpoint_for_token("") is None


def test_disabled_endpoints_are_kept() -> None:
    snapshot = build_snapshot(
        _data(_endpoint(enabled=False)), revision="r", source="endpoints.yaml"
    )
    assert snapshot.endpoints[0].enabled is False
    assert snapshot.endpoint_for_token(TOKEN_A) is not None


def test_snapshot_is_immutable() -> None:
    snapshot = build_snapshot(_data(_endpoint()), revision="r", source="endpoints.yaml")
    with pytest.raises(AttributeError):
        snapshot.revision = "other"  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        snapshot.tokens["x"] = "y"  # ty: ignore[invalid-assignment]


def test_repr_never_contains_a_token() -> None:
    snapshot = build_snapshot(_data(_endpoint()), revision="r", source="endpoints.yaml")
    assert TOKEN_A not in repr(snapshot)
    assert TOKEN_A not in repr(EndpointConfig.model_validate(_endpoint()))


def test_paths_drop_tag_segments_and_use_indexes() -> None:
    errors = _errors(_data(_endpoint(products=["php", {"name": "x", "unknown": 1}])))
    assert errors == (
        "endpoints[0].products[1].unknown: Extra inputs are not permitted",
    )


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (_endpoint(entry=1), "endpoints[0].entry: Extra inputs are not permitted"),
        (
            _endpoint(products=[{"name": "x", "shorthand": 1}]),
            "endpoints[0].products[0].shorthand: Extra inputs are not permitted",
        ),
    ],
    ids=["endpoint-key", "product-key"],
)
def test_keys_named_like_tags_stay_in_paths(
    endpoint: dict[str, Any], expected: str
) -> None:
    assert _errors(_data(endpoint)) == (expected,)


def test_shorthand_error_path() -> None:
    errors = _errors(_data(_endpoint(products=["php > 8.2"])))
    assert errors == (
        'endpoints[0].products[0]: expected "<name>" or "<name> >= <version>"',
    )


def test_exact_contradiction_error_path() -> None:
    errors = _errors(
        _data(
            _endpoint(
                products=[{"name": "a", "exact": True, "match_description": True}]
            )
        )
    )
    assert errors == (
        "endpoints[0].products[0]: exact: true contradicts match_description: true; "
        "omit match_description or set it to false",
    )


def test_value_error_prefix_is_stripped() -> None:
    errors = _errors(_data(_endpoint(products=[{"name": "x", "min_version": "8.x"}])))
    assert errors == ("endpoints[0].products[0].min_version: invalid version '8.x'",)


def test_invalid_version_message_stays_on_one_line() -> None:
    product = {"name": "x", "min_version": "8.2\nfoo"}
    (error,) = _errors(_data(_endpoint(products=[product])))
    assert error == "endpoints[0].products[0].min_version: invalid version '8.2\\nfoo'"
    assert "\n" not in error


def test_top_level_and_empty_loc() -> None:
    assert _errors({"version": 2, "endpoints": []}) == ("version: Input should be 1",)
    assert _errors([]) == (
        "Input should be a valid dictionary or instance of ConfigDocument",
    )


def test_duplicate_token_message_has_no_token() -> None:
    errors = _errors(_data(_endpoint(), _endpoint(id="other")))
    assert errors == ("endpoints[1].token: same token as endpoint 'team-php'",)
    assert all(TOKEN_A not in line for line in errors)


def test_bad_token_message_has_no_token() -> None:
    bad = "secret/" + "z" * 40
    errors = _errors(_data(_endpoint(token=bad)))
    assert errors
    assert all(bad not in line for line in errors)


def test_error_is_raised_from_none() -> None:
    with pytest.raises(ConfigValidationError) as excinfo:
        build_snapshot(
            _data(_endpoint(token="x" * 5)), revision="r", source="endpoints.yaml"
        )
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert str(excinfo.value).startswith("endpoints.yaml: endpoints[0].token:")


def test_duplicate_product_name_error_line() -> None:
    errors = _errors(_data(_endpoint(products=["php", {"name": "PHP", "exact": True}])))
    assert errors == (
        "endpoints[0]: products[1]: duplicate product name 'PHP' (same as products[0])",
    )


def test_duplicate_product_name_error_line_in_second_endpoint() -> None:
    errors = _errors(
        _data(
            _endpoint(),
            _endpoint(id="other", token=TOKEN_B, products=["php", {"name": "PHP"}]),
        )
    )
    assert errors == (
        "endpoints[1]: products[1]: duplicate product name 'PHP' (same as products[0])",
    )


def test_protocol_is_structural() -> None:
    class _Fake:
        def revision(self) -> str:
            return "r"

        def load(self) -> Any:
            return None

    provider: ConfigProvider = _Fake()
    assert provider.revision() == "r"
