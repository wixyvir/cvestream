import json

import pytest

from cvestream.config.models import ProductEntryConfig
from cvestream.config.yaml_provider import YamlConfigProvider

from .conftest import Cli

EXISTING = f"""\
version: 1
endpoints:
  - id: team-php
    name: PHP team
    token: {"k" * 43}
    products:
      - php >= 8.2
      - name: GitLab
        aliases: [gl]
        exclude: [ce]
        min_version: "16.0"
        match_description: false
  - id: empty
    name: Empty
    token: {"m" * 43}
"""


@pytest.fixture
def seeded(cli: Cli) -> Cli:
    cli.path.write_text(EXISTING, encoding="utf-8")
    return cli


def _products(
    cli: Cli, endpoint_id: str = "team-php"
) -> list[str | ProductEntryConfig]:
    document = YamlConfigProvider(cli.path).document()
    return next(e for e in document.endpoints if e.id == endpoint_id).products


def _entry(item: str | ProductEntryConfig) -> ProductEntryConfig:
    assert isinstance(item, ProductEntryConfig)
    return item


# --- add -----------------------------------------------------------------------


def test_add_a_plain_name_is_stored_as_given(seeded: Cli) -> None:
    result = seeded.run("product", "add", "team-php", "node")
    assert result.exit_code == 0
    assert result.stdout.strip() == "added product to endpoint team-php"
    assert _products(seeded)[-1] == "node"


def test_add_a_shorthand_is_stored_as_given(seeded: Cli) -> None:
    assert seeded.run("product", "add", "empty", "node >= 20").exit_code == 0
    assert _products(seeded, "empty") == ["node >= 20"]
    assert "node >= 20" in seeded.path.read_text(encoding="utf-8")


def test_add_with_options_is_stored_as_an_entry(seeded: Cli) -> None:
    result = seeded.run(
        "product",
        "add",
        "empty",
        "redis",
        "--alias",
        "valkey",
        "--alias",
        "keydb",
        "--exclude",
        "sentinel",
        "--min-version",
        "7.2",
        "--no-match-description",
    )
    assert result.exit_code == 0
    entry = _entry(_products(seeded, "empty")[0])
    assert entry.name == "redis"
    assert entry.aliases == ["valkey", "keydb"]
    assert entry.exclude == ["sentinel"]
    assert entry.min_version == "7.2"
    assert entry.match_description is False


def test_add_the_shorthand_version_becomes_min_version_with_options(
    seeded: Cli,
) -> None:
    assert (
        seeded.run("product", "add", "empty", "redis >= 7.2", "--alias", "v").exit_code
        == 0
    )
    entry = _entry(_products(seeded, "empty")[0])
    assert (entry.name, entry.min_version, entry.aliases) == ("redis", "7.2", ["v"])


def test_add_a_shorthand_version_and_min_version_conflict(seeded: Cli) -> None:
    before = seeded.path.read_bytes()
    result = seeded.run("product", "add", "empty", "redis >= 7", "--min-version", "8")
    assert result.exit_code == 2
    assert len(seeded.stderr(result).strip().splitlines()) == 1
    assert seeded.path.read_bytes() == before


def test_add_only_match_description(seeded: Cli) -> None:
    assert (
        seeded.run("product", "add", "empty", "redis", "--match-description").exit_code
        == 0
    )
    assert _entry(_products(seeded, "empty")[0]).match_description is True


@pytest.mark.parametrize("name", ["php", "PHP", "gitlab"])
def test_add_a_duplicate_name(seeded: Cli, name: str) -> None:
    before = seeded.path.read_bytes()
    result = seeded.run("product", "add", "team-php", name)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "already has product" in seeded.stderr(result)
    assert seeded.path.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [
        ["php >= banana"],
        ["x", "--min-version", "nope"],
        [" "],
        ["x >= 1 >= 2"],
    ],
)
def test_add_an_invalid_product(seeded: Cli, args: list[str]) -> None:
    before = seeded.path.read_bytes()
    result = seeded.run("product", "add", "empty", *args)
    assert result.exit_code == 2
    assert len(seeded.stderr(result).strip().splitlines()) == 1
    assert seeded.path.read_bytes() == before


def test_add_to_an_unknown_endpoint(seeded: Cli) -> None:
    result = seeded.run("product", "add", "nope", "php")
    assert result.exit_code == 2
    assert seeded.stderr(result).strip() == "unknown endpoint 'nope'"


def test_add_keeps_the_file_valid(seeded: Cli) -> None:
    seeded.run("product", "add", "empty", "redis", "--alias", "v")
    assert seeded.run("config", "validate").exit_code == 0


# --- remove --------------------------------------------------------------------


def test_remove_a_product(seeded: Cli) -> None:
    result = seeded.run("product", "remove", "team-php", "gitlab")
    assert result.exit_code == 0
    assert result.stdout.strip() == "removed 1 product from endpoint team-php"
    assert _products(seeded) == ["php >= 8.2"]


def test_remove_every_match(seeded: Cli) -> None:
    seeded.path.write_text(
        EXISTING.replace("      - php >= 8.2\n", "      - php >= 8.2\n      - PHP\n"),
        encoding="utf-8",
    )
    result = seeded.run("product", "remove", "team-php", " php ")
    assert result.stdout.strip() == "removed 2 products from endpoint team-php"
    assert len(_products(seeded)) == 1


def test_remove_by_alias_does_not_match(seeded: Cli) -> None:
    before = seeded.path.read_bytes()
    result = seeded.run("product", "remove", "team-php", "gl")
    assert result.exit_code == 2
    assert seeded.stderr(result).strip() == "endpoint 'team-php' has no product 'gl'"
    assert seeded.path.read_bytes() == before


def test_remove_from_an_unknown_endpoint(seeded: Cli) -> None:
    result = seeded.run("product", "remove", "nope", "php")
    assert result.exit_code == 2
    assert seeded.stderr(result).strip() == "unknown endpoint 'nope'"


# --- list ----------------------------------------------------------------------


def test_list_text(seeded: Cli) -> None:
    result = seeded.run("product", "list", "team-php")
    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "NAME    MIN_VERSION  ALIASES  EXCLUDE  MATCH_DESCRIPTION",
        "php     8.2          -        -        true",
        "GitLab  16.0         gl       ce       false",
    ]


def test_list_json_has_one_shape_whatever_the_storage_form(seeded: Cli) -> None:
    data = json.loads(seeded.run("product", "list", "team-php", "--json").stdout)
    assert data == [
        {
            "name": "php",
            "aliases": [],
            "exclude": [],
            "min_version": "8.2",
            "match_description": True,
        },
        {
            "name": "GitLab",
            "aliases": ["gl"],
            "exclude": ["ce"],
            "min_version": "16.0",
            "match_description": False,
        },
    ]


def test_list_of_an_endpoint_without_products(seeded: Cli) -> None:
    assert json.loads(seeded.run("product", "list", "empty", "--json").stdout) == []
    assert seeded.run("product", "list", "empty").stdout.startswith("NAME")


def test_list_unknown_endpoint(seeded: Cli) -> None:
    result = seeded.run("product", "list", "nope")
    assert result.exit_code == 2
    assert result.stdout == ""


def test_no_command_writes_a_token_to_stdout(seeded: Cli) -> None:
    out = seeded.run("product", "list", "team-php").stdout
    out += seeded.run("product", "add", "empty", "x").stdout
    out += seeded.run("product", "remove", "empty", "x").stdout
    assert "k" * 43 not in out
    assert "m" * 43 not in out
