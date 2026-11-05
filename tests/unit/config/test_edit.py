from typing import Any

import pytest

from cvestream.config.edit import (
    UNSET,
    add_product,
    create_endpoint,
    delete_endpoint,
    edit_endpoint,
    find_endpoint,
    product_name,
    remove_product,
    set_token,
)
from cvestream.config.models import ConfigDocument, EndpointConfig
from cvestream.config.provider import ConfigEditError, ConfigValidationError

TOKEN_A = "a" * 43
TOKEN_B = "b" * 43
TOKEN_C = "c" * 43


def _doc() -> ConfigDocument:
    return ConfigDocument.model_validate(
        {
            "version": 1,
            "endpoints": [
                {
                    "id": "team-php",
                    "name": "PHP team",
                    "token": TOKEN_A,
                    "min_cvss": 7.0,
                    "products": [
                        "php >= 8.2",
                        {"name": "GitLab", "aliases": ["gl"], "min_version": "16.0"},
                    ],
                },
                {"id": "other", "name": "Other", "token": TOKEN_B},
            ],
        }
    )


def _snapshot(document: ConfigDocument) -> dict[str, Any]:
    return document.model_dump(mode="json")


def _ids(document: ConfigDocument) -> list[str]:
    return [e.id for e in document.endpoints]


# --- find_endpoint -------------------------------------------------------------


def test_find_endpoint() -> None:
    assert find_endpoint(_doc(), "other").name == "Other"


def test_find_unknown_endpoint() -> None:
    with pytest.raises(ConfigEditError, match="unknown endpoint 'nope'"):
        find_endpoint(_doc(), "nope")


# --- create_endpoint -----------------------------------------------------------


def test_create_appends_an_endpoint() -> None:
    document = _doc()
    before = _snapshot(document)
    new = create_endpoint(
        document,
        id="team-go",
        name="Go team",
        token=TOKEN_C,
        enabled=False,
        min_cvss=5.5,
        kev_only=True,
    )
    assert _ids(new) == ["team-php", "other", "team-go"]
    created = find_endpoint(new, "team-go")
    assert (created.enabled, created.min_cvss, created.kev_only) == (False, 5.5, True)
    assert created.token == TOKEN_C
    assert created.products == []
    assert _snapshot(document) == before


def test_create_defaults() -> None:
    created = find_endpoint(
        create_endpoint(_doc(), id="x", name="X", token=TOKEN_C), "x"
    )
    assert (created.enabled, created.min_cvss, created.kev_only) == (True, None, False)


def test_create_duplicate_id() -> None:
    with pytest.raises(ConfigEditError, match="endpoint 'other' already exists"):
        create_endpoint(_doc(), id="other", name="X", token=TOKEN_C)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"id": "Not A Slug"}, "id"),
        ({"id": ""}, "id"),
        ({"name": "  "}, "name"),
        ({"min_cvss": 11.0}, "min_cvss"),
        ({"min_cvss": -1.0}, "min_cvss"),
        ({"token": "short"}, "token"),
    ],
)
def test_create_validates_through_the_models(
    overrides: dict[str, Any], field: str
) -> None:
    values: dict[str, Any] = {"id": "x", "name": "X", "token": TOKEN_C}
    values.update(overrides)
    with pytest.raises(ConfigValidationError) as excinfo:
        create_endpoint(_doc(), **values)
    assert excinfo.value.errors[0].startswith(f"{field}: ")
    assert "\n" not in str(excinfo.value)
    assert "short" not in str(excinfo.value)


# --- edit_endpoint -------------------------------------------------------------


def test_edit_changes_only_the_given_fields() -> None:
    document = _doc()
    before = _snapshot(document)
    new = edit_endpoint(document, "team-php", name="Renamed", kev_only=True)
    edited = find_endpoint(new, "team-php")
    assert (edited.name, edited.kev_only, edited.min_cvss) == ("Renamed", True, 7.0)
    assert edited.token == TOKEN_A
    assert len(edited.products) == 2
    assert _ids(new) == ["team-php", "other"]
    assert _snapshot(document) == before


def test_edit_can_clear_the_threshold() -> None:
    new = edit_endpoint(_doc(), "team-php", min_cvss=None)
    assert find_endpoint(new, "team-php").min_cvss is None


def test_edit_can_disable() -> None:
    new = edit_endpoint(_doc(), "team-php", enabled=False)
    assert find_endpoint(new, "team-php").enabled is False


def test_edit_nothing_to_change() -> None:
    with pytest.raises(ConfigEditError, match="nothing to change"):
        edit_endpoint(_doc(), "team-php")


def test_edit_unknown_endpoint() -> None:
    with pytest.raises(ConfigEditError, match="unknown endpoint"):
        edit_endpoint(_doc(), "nope", name="x")


def test_edit_validates_values() -> None:
    with pytest.raises(ConfigValidationError, match="min_cvss"):
        edit_endpoint(_doc(), "team-php", min_cvss=11.0)
    with pytest.raises(ConfigValidationError, match="name"):
        edit_endpoint(_doc(), "team-php", name=" ")


def test_unset_is_not_none() -> None:
    assert UNSET is not None
    assert repr(UNSET) == "UNSET"


# --- delete_endpoint -----------------------------------------------------------


def test_delete_removes_the_endpoint() -> None:
    document = _doc()
    new = delete_endpoint(document, "team-php")
    assert _ids(new) == ["other"]
    assert _ids(document) == ["team-php", "other"]


def test_delete_unknown_endpoint() -> None:
    with pytest.raises(ConfigEditError, match="unknown endpoint 'nope'"):
        delete_endpoint(_doc(), "nope")


# --- add_product ---------------------------------------------------------------


def test_add_a_shorthand_is_kept_as_given() -> None:
    document = _doc()
    before = _snapshot(document)
    new = add_product(document, "team-php", "node >= 20")
    assert find_endpoint(new, "team-php").products[-1] == "node >= 20"
    assert _snapshot(document) == before


def test_add_an_entry() -> None:
    new = add_product(
        _doc(),
        "other",
        {"name": "redis", "aliases": ["valkey"], "min_version": "7.2"},
    )
    (item,) = find_endpoint(new, "other").products
    assert not isinstance(item, str)
    assert (item.name, item.aliases, item.min_version) == ("redis", ["valkey"], "7.2")


def test_add_keeps_existing_items_as_they_were() -> None:
    new = add_product(_doc(), "team-php", "node")
    assert find_endpoint(new, "team-php").products[0] == "php >= 8.2"


@pytest.mark.parametrize("item", ["PHP", " php ", "php >= 9", {"name": "Php"}])
def test_add_duplicate_name(item: Any) -> None:
    with pytest.raises(ConfigEditError, match="already has product"):
        add_product(_doc(), "team-php", item)


def test_add_duplicate_name_is_case_insensitive_on_entries() -> None:
    with pytest.raises(ConfigEditError, match="already has product 'gitlab'"):
        add_product(_doc(), "team-php", "gitlab")


@pytest.mark.parametrize(
    "item",
    ["php >= banana", "", {"name": ""}, {"name": "x", "min_version": "nope"}],
)
def test_add_validates_the_item(item: Any) -> None:
    with pytest.raises(ConfigValidationError):
        add_product(_doc(), "other", item)


def test_add_unknown_endpoint() -> None:
    with pytest.raises(ConfigEditError, match="unknown endpoint"):
        add_product(_doc(), "nope", "php")


# --- remove_product ------------------------------------------------------------


def test_remove_a_product() -> None:
    document = _doc()
    new, removed = remove_product(document, "team-php", " GITLAB ")
    assert removed == 1
    assert find_endpoint(new, "team-php").products == ["php >= 8.2"]
    assert len(find_endpoint(document, "team-php").products) == 2


def test_remove_every_match() -> None:
    document = add_product(_doc(), "other", "php")
    document = document.model_copy(
        update={
            "endpoints": [
                e.model_copy(update={"products": ["php", "PHP >= 8", "node"]})
                if e.id == "other"
                else e
                for e in document.endpoints
            ]
        }
    )
    new, removed = remove_product(document, "other", "php")
    assert removed == 2
    assert find_endpoint(new, "other").products == ["node"]


def test_remove_ignores_aliases() -> None:
    with pytest.raises(ConfigEditError, match="has no product 'gl'"):
        remove_product(_doc(), "team-php", "gl")


def test_remove_missing_product() -> None:
    with pytest.raises(ConfigEditError, match="endpoint 'other' has no product 'php'"):
        remove_product(_doc(), "other", "php")


def test_remove_unknown_endpoint() -> None:
    with pytest.raises(ConfigEditError, match="unknown endpoint"):
        remove_product(_doc(), "nope", "php")


# --- set_token -----------------------------------------------------------------


def test_set_token() -> None:
    document = _doc()
    new = set_token(document, "team-php", TOKEN_C)
    assert find_endpoint(new, "team-php").token == TOKEN_C
    assert find_endpoint(new, "other").token == TOKEN_B
    assert find_endpoint(document, "team-php").token == TOKEN_A


def test_set_token_validates_the_token_without_quoting_it() -> None:
    with pytest.raises(ConfigValidationError) as excinfo:
        set_token(_doc(), "team-php", "not a token!")
    assert "not a token" not in str(excinfo.value)


def test_set_token_unknown_endpoint() -> None:
    with pytest.raises(ConfigEditError, match="unknown endpoint"):
        set_token(_doc(), "nope", TOKEN_C)


# --- helpers -------------------------------------------------------------------


def test_product_name() -> None:
    entry = EndpointConfig.model_validate(
        {
            "id": "x",
            "name": "X",
            "token": TOKEN_A,
            "products": [" a >= 1", {"name": " b "}],
        }
    )
    assert [product_name(p) for p in entry.products] == ["a", "b"]


def test_no_message_carries_a_token() -> None:
    messages: list[str] = []
    for call in (
        lambda: create_endpoint(_doc(), id="other", name="X", token=TOKEN_C),
        lambda: set_token(_doc(), "nope", TOKEN_C),
        lambda: edit_endpoint(_doc(), "nope", name="x"),
    ):
        with pytest.raises(ConfigEditError) as excinfo:
            call()
        messages.append(str(excinfo.value))
    assert all(t not in m for m in messages for t in (TOKEN_A, TOKEN_B, TOKEN_C))
