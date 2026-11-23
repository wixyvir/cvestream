import copy
from typing import Any

import pytest
from packaging.version import Version
from pydantic import ValidationError

from cvestream.config.models import (
    SCHEMA_VERSION,
    ConfigDocument,
    EndpointConfig,
    ProductEntryConfig,
)
from cvestream.domain.endpoint import Endpoint, ProductEntry, Rules

Raw = dict[str, Any]

TOKEN_A = "a" * 43
TOKEN_B = "b" * 43


def _endpoint(**overrides: Any) -> Raw:
    base: Raw = {"id": "team-php", "name": "PHP team", "token": TOKEN_A}
    base.update(overrides)
    return base


def _document(*endpoints: Raw) -> Raw:
    return {"version": 1, "endpoints": list(endpoints)}


def _validate(data: Raw) -> ConfigDocument:
    return ConfigDocument.model_validate(copy.deepcopy(data))


def _message(data: Raw) -> str:
    with pytest.raises(ValidationError) as excinfo:
        _validate(data)
    errors = excinfo.value.errors(include_input=False, include_url=False)
    return "\n".join(str(error["msg"]) for error in errors)


def _only_endpoint(**overrides: Any) -> EndpointConfig:
    return _validate(_document(_endpoint(**overrides))).endpoints[0]


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


def test_minimal_document() -> None:
    document = _validate({"version": 1, "endpoints": []})
    assert document.endpoints == []


def test_endpoints_default_to_empty() -> None:
    assert _validate({"version": 1}).endpoints == []


def test_endpoint_defaults() -> None:
    endpoint = _only_endpoint()
    assert endpoint.enabled is True
    assert endpoint.kev_only is False
    assert endpoint.min_cvss is None
    assert endpoint.require_score is False
    assert endpoint.group_by_product is False
    assert endpoint.products == []


def test_full_endpoint() -> None:
    endpoint = _only_endpoint(
        enabled=False,
        min_cvss=7.5,
        kev_only=True,
        products=["php >= 8.2", {"name": "gitlab", "aliases": ["gitlab-ce"]}],
    )
    assert endpoint.enabled is False
    assert endpoint.min_cvss == 7.5
    assert endpoint.kev_only is True
    assert len(endpoint.products) == 2


def test_int_min_cvss_is_accepted_as_float() -> None:
    endpoint = _only_endpoint(min_cvss=7)
    assert endpoint.min_cvss == 7.0
    assert isinstance(endpoint.min_cvss, float)


@pytest.mark.parametrize(
    ("text", "name", "min_version"),
    [
        ("php >= 8.2", "php", Version("8.2")),
        ("php>=8.2", "php", Version("8.2")),
        ("php", "php", None),
        ("  php  ", "php", None),
        ("apache http server >= 2.4", "apache http server", Version("2.4")),
        ("node.js >= v20.1.0-rc.1", "node.js", Version("20.1.0rc1")),
    ],
    ids=["spaced", "compact", "bare", "padded", "multi-word", "prerelease"],
)
def test_shorthand_expands_to_domain(
    text: str, name: str, min_version: Version | None
) -> None:
    domain = _only_endpoint(products=[text]).to_domain()
    assert domain.products == (ProductEntry(name=name, min_version=min_version),)


def test_full_product_object_to_domain() -> None:
    domain = _only_endpoint(
        products=[
            {
                "name": "gitlab",
                "aliases": ["gitlab-ce", "gitlab-ee"],
                "exclude": ["gitlab-runner"],
                "min_version": "17.0",
            }
        ]
    ).to_domain()
    assert domain.products == (
        ProductEntry(
            name="gitlab",
            aliases=("gitlab-ce", "gitlab-ee"),
            exclude=("gitlab-runner",),
            min_version=Version("17.0"),
        ),
    )


def test_terms_are_stripped() -> None:
    domain = _only_endpoint(
        products=[{"name": "  php ", "aliases": [" php-fpm"], "exclude": ["x "]}]
    ).to_domain()
    entry = domain.products[0]
    assert (entry.name, entry.aliases, entry.exclude) == ("php", ("php-fpm",), ("x",))


def test_to_domain_shape() -> None:
    domain = _only_endpoint(
        enabled=False, min_cvss=7, kev_only=True, products=["php"]
    ).to_domain()
    assert isinstance(domain, Endpoint)
    assert domain.id == "team-php"
    assert domain.name == "PHP team"
    assert domain.enabled is False
    assert domain.rules == Rules(min_cvss=7.0, kev_only=True)
    assert isinstance(domain.products, tuple)
    assert not hasattr(domain, "token")


def test_product_entry_config_to_domain_defaults() -> None:
    entry = ProductEntryConfig.model_validate({"name": "php"})
    assert entry.aliases == []
    assert entry.exclude == []
    assert entry.min_version is None
    assert entry.match_description is True
    assert entry.exact is False


def test_exact_maps_to_the_domain() -> None:
    domain = _only_endpoint(
        products=[{"name": "php", "exact": True}, {"name": "gitlab"}, "nodejs >= 20"]
    ).to_domain()
    assert [e.exact for e in domain.products] == [True, False, False]


def test_exact_accepts_match_description_omitted_or_false() -> None:
    omitted = (
        _only_endpoint(products=[{"name": "php", "exact": True}])
        .to_domain()
        .products[0]
    )
    assert omitted == ProductEntry(name="php", exact=True)
    explicit = (
        _only_endpoint(
            products=[{"name": "php", "exact": True, "match_description": False}]
        )
        .to_domain()
        .products[0]
    )
    assert explicit == ProductEntry(name="php", exact=True, match_description=False)


def test_exact_contradiction_message() -> None:
    data = _document(
        _endpoint(products=[{"name": "a", "exact": True, "match_description": True}])
    )
    assert "exact: true contradicts match_description: true" in _message(data)


def test_match_description_maps_to_the_domain() -> None:
    domain = _only_endpoint(
        products=[{"name": "php", "match_description": False}, {"name": "gitlab"}]
    ).to_domain()
    assert [e.match_description for e in domain.products] == [False, True]


def test_require_score_maps_to_the_domain() -> None:
    domain = _only_endpoint(require_score=True).to_domain()
    assert domain.rules == Rules(require_score=True)


def test_group_by_product_maps_to_the_domain() -> None:
    domain = _only_endpoint(group_by_product=True).to_domain()
    assert domain.rules == Rules(group_by_product=True)


def test_repr_hides_token() -> None:
    endpoint = _only_endpoint()
    assert TOKEN_A not in repr(endpoint)


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({"version": 1, "endpoints": [], "extra": 1}, "extra"),
        (_document(_endpoint(unknown_field=1)), "endpoints.0.unknown_field"),
        (
            _document(_endpoint(products=["php", {"name": "x", "unknown": 1}])),
            "endpoints.0.products.1.entry.unknown",
        ),
        ({"endpoints": []}, "version"),
        ({"version": 2}, "version"),
        ({"version": "1"}, "version"),
        ({"version": True}, "version"),
        (_document(_endpoint(id="My_Team")), "endpoints.0.id"),
        (_document(_endpoint(id="team_php")), "endpoints.0.id"),
        (_document(_endpoint(id="a" * 41)), "endpoints.0.id"),
        (_document(_endpoint(id="")), "endpoints.0.id"),
        (_document(_endpoint(min_cvss="high")), "endpoints.0.min_cvss"),
        (_document(_endpoint(min_cvss="7.0")), "endpoints.0.min_cvss"),
        (_document(_endpoint(min_cvss=-0.1)), "endpoints.0.min_cvss"),
        (_document(_endpoint(min_cvss=10.1)), "endpoints.0.min_cvss"),
        (_document(_endpoint(kev_only="yes")), "endpoints.0.kev_only"),
        (_document(_endpoint(require_score="yes")), "endpoints.0.require_score"),
        (_document(_endpoint(require_score=1)), "endpoints.0.require_score"),
        (_document(_endpoint(group_by_product="yes")), "endpoints.0.group_by_product"),
        (_document(_endpoint(group_by_product=1)), "endpoints.0.group_by_product"),
        (_document(_endpoint(name="  ")), "endpoints.0.name"),
        (
            _document(_endpoint(products=[{"name": "a", "aliases": ["  "]}])),
            "endpoints.0.products.0.entry.aliases.0",
        ),
        (
            _document(_endpoint(products=[{"name": "a", "exclude": [""]}])),
            "endpoints.0.products.0.entry.exclude.0",
        ),
        (
            _document(_endpoint(products=[{"name": " "}])),
            "endpoints.0.products.0.entry.name",
        ),
        (
            _document(_endpoint(products=[{"name": "a", "min_version": "8.x"}])),
            "endpoints.0.products.0.entry.min_version",
        ),
        (
            _document(_endpoint(products=[{"name": "a", "match_description": "no"}])),
            "endpoints.0.products.0.entry.match_description",
        ),
        (
            _document(_endpoint(products=[{"name": "a", "exact": "yes"}])),
            "endpoints.0.products.0.entry.exact",
        ),
        (
            _document(
                _endpoint(
                    products=[{"name": "a", "exact": True, "match_description": True}]
                )
            ),
            "endpoints.0.products.0.entry",
        ),
        (
            _document(_endpoint(products=["php > 8.2"])),
            "endpoints.0.products.0.shorthand",
        ),
        (_document(_endpoint(products=[">= 8.2"])), "endpoints.0.products.0.shorthand"),
        (
            _document(_endpoint(products=["php >= 8.x"])),
            "endpoints.0.products.0.shorthand",
        ),
        (_document(_endpoint(products=[12])), "endpoints.0.products.0.entry"),
        (_document(_endpoint(products=[["php"]])), "endpoints.0.products.0.entry"),
        (_document(_endpoint(token="a" * 31)), "endpoints.0.token"),
        (_document(_endpoint(token="a" * 129)), "endpoints.0.token"),
        (_document(_endpoint(token="a" * 42 + ".")), "endpoints.0.token"),
        (_document(_endpoint(token="a" * 42 + "/")), "endpoints.0.token"),
    ],
    ids=[
        "unknown-top-level",
        "unknown-endpoint-key",
        "unknown-product-key",
        "missing-version",
        "version-2",
        "version-string",
        "version-bool",
        "slug-uppercase",
        "slug-underscore",
        "slug-41-chars",
        "slug-empty",
        "cvss-high",
        "cvss-string",
        "cvss-negative",
        "cvss-above-10",
        "kev-string",
        "require-score-string",
        "require-score-int",
        "group-by-product-string",
        "group-by-product-int",
        "blank-endpoint-name",
        "blank-alias",
        "blank-exclude",
        "blank-product-name",
        "bad-min-version",
        "match-description-string",
        "exact-string",
        "exact-with-match-description",
        "shorthand-gt",
        "shorthand-no-name",
        "shorthand-bad-version",
        "product-number",
        "product-list",
        "token-short",
        "token-long",
        "token-dot",
        "token-slash",
    ],
)
def test_rejected(data: Raw, path: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _validate(data)
    locations = [".".join(str(p) for p in e["loc"]) for e in excinfo.value.errors()]
    assert path in locations


def test_float_min_version_asks_for_quotes() -> None:
    data = _document(_endpoint(products=[{"name": "a", "min_version": 8.1}]))
    assert "quoted string" in _message(data)


def test_bad_shorthand_message_is_not_a_raw_regex() -> None:
    message = _message(_document(_endpoint(products=["php > 8.2"])))
    assert 'expected "<name>" or "<name> >= <version>"' in message


def test_duplicate_ids_rejected() -> None:
    data = _document(_endpoint(), _endpoint(token=TOKEN_B))
    message = _message(data)
    assert "endpoints[1].id: duplicate endpoint id 'team-php'" in message


def test_duplicate_tokens_rejected_without_printing_the_token() -> None:
    data = _document(_endpoint(), _endpoint(id="other"))
    message = _message(data)
    assert "endpoints[1].token: same token as endpoint 'team-php'" in message
    assert TOKEN_A not in message


# --- unique product names (wixyvir/cvestream#48) ---


@pytest.mark.parametrize(
    ("products", "expected"),
    [
        (
            [{"name": "php", "exact": True}, {"name": "php"}],
            "products[1]: duplicate product name 'php' (same as products[0])",
        ),
        (
            ["php", {"name": "PHP"}],
            "products[1]: duplicate product name 'PHP' (same as products[0])",
        ),
        (
            ["php >= 8.2", "php"],
            "products[1]: duplicate product name 'php' (same as products[0])",
        ),
        (
            ["apache http server", {"name": "Apache  HTTP\tServer"}],
            "products[1]: duplicate product name 'Apache  HTTP\\tServer' "
            "(same as products[0])",
        ),
        (
            ["php", "nodejs", {"name": "PHP"}],
            "products[2]: duplicate product name 'PHP' (same as products[0])",
        ),
    ],
    ids=["two-entries", "case", "shorthand-version", "whitespace", "third-collides"],
)
def test_duplicate_product_names_rejected(products: list[Any], expected: str) -> None:
    # `_message` keeps pydantic's `Value error, ` prefix; `_format_error` strips it.
    message = _message(_document(_endpoint(products=products))).removeprefix(
        "Value error, "
    )
    assert message == expected


def test_duplicate_product_name_location_is_the_endpoint() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _validate(_document(_endpoint(products=["php", "PHP"])))
    errors = excinfo.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("endpoints", 0)
    with pytest.raises(ValidationError) as excinfo:
        _validate(
            _document(
                _endpoint(products=["php"]),
                _endpoint(id="other", token=TOKEN_B, products=["php", "PHP"]),
            )
        )
    errors = excinfo.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("endpoints", 1)


def test_duplicate_product_names_rejected_on_a_standalone_endpoint() -> None:
    with pytest.raises(ValidationError) as excinfo:
        EndpointConfig.model_validate(_endpoint(products=["php", "PHP"]))
    (error,) = excinfo.value.errors(include_input=False, include_url=False)
    assert (
        "products[1]: duplicate product name 'PHP' (same as products[0])"
        in error["msg"]
    )


@pytest.mark.parametrize(
    ("products", "entries"),
    [
        (
            [
                {
                    "name": "php",
                    "aliases": ["php-src"],
                    "min_version": "8.2",
                    "exact": True,
                },
                {"name": "php-src", "match_description": False},
            ],
            [
                ProductEntry(
                    name="php",
                    aliases=("php-src",),
                    min_version=Version("8.2"),
                    exact=True,
                ),
                ProductEntry(name="php-src", match_description=False),
            ],
        ),
        (
            [{"name": "a", "aliases": ["x"]}, {"name": "b", "aliases": ["x"]}],
            [
                ProductEntry(name="a", aliases=("x",)),
                ProductEntry(name="b", aliases=("x",)),
            ],
        ),
        (
            ["php", "php-fpm", "phpmyadmin"],
            [
                ProductEntry(name="php"),
                ProductEntry(name="php-fpm"),
                ProductEntry(name="phpmyadmin"),
            ],
        ),
    ],
    ids=["alias-as-name", "shared-alias", "near-names"],
)
def test_distinct_product_names_accepted(
    products: list[Any], entries: list[ProductEntry]
) -> None:
    domain = _only_endpoint(products=products).to_domain()
    assert list(domain.products) == entries


def test_same_product_name_in_two_endpoints_accepted() -> None:
    document = _validate(
        _document(
            _endpoint(products=["php"]),
            _endpoint(id="other", token=TOKEN_B, products=["php"]),
        )
    )
    assert [endpoint.id for endpoint in document.endpoints] == ["team-php", "other"]
    domains = [endpoint.to_domain() for endpoint in document.endpoints]
    assert [[entry.name for entry in domain.products] for domain in domains] == [
        ["php"],
        ["php"],
    ]


def test_disabled_endpoint_is_checked_too() -> None:
    data = _document(_endpoint(enabled=False, products=["php", "PHP"]))
    assert "products[1]: duplicate product name 'PHP'" in _message(data)
