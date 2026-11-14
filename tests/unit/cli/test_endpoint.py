import json

import pytest

from cvestream.cli.endpoint import endpoint_app
from cvestream.config.yaml_provider import YamlConfigProvider

from .conftest import GENERATED, Cli

T1, T2 = GENERATED[0], GENERATED[1]
EXISTING = f"""\
version: 1
endpoints:
  - id: team-php
    name: PHP team
    token: {"k" * 43}
    min_cvss: 7.0
    products:
      - php >= 8.2
      - name: gitlab
        aliases: [gl]
        min_version: "16.0"
  - id: other
    name: Other
    token: {"m" * 43}
    enabled: false
"""


def _seed(cli: Cli) -> None:
    cli.path.write_text(EXISTING, encoding="utf-8")


def _one_line(text: str) -> None:
    assert len(text.strip().splitlines()) == 1


def _ids(cli: Cli) -> list[str]:
    return [e.id for e in YamlConfigProvider(cli.path).document().endpoints]


def test_help_lists_every_command(cli: Cli) -> None:
    result = cli.run("endpoint", "--help")
    assert result.exit_code == 0
    for command in ("create", "list", "show", "edit", "delete"):
        assert command in result.stdout
    assert endpoint_app.info.name == "endpoint"


# --- create --------------------------------------------------------------------


def test_create_writes_the_endpoint_and_prints_the_feed_url(cli: Cli) -> None:
    result = cli.run("endpoint", "create", "team-go", "--name", "Go team")
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines == [
        "created endpoint team-go",
        f"feed: http://localhost:8080/feeds/{T1}.xml",
    ]
    assert result.stderr == ""
    endpoint = YamlConfigProvider(cli.path).document().endpoints[0]
    assert (endpoint.id, endpoint.name, endpoint.token) == ("team-go", "Go team", T1)
    assert (endpoint.enabled, endpoint.min_cvss, endpoint.kev_only) == (
        True,
        None,
        False,
    )
    assert cli.run("config", "validate").exit_code == 0


def test_create_with_options(cli: Cli) -> None:
    result = cli.run(
        "endpoint",
        "create",
        "x",
        "--name",
        "X",
        "--min-cvss",
        "7.5",
        "--kev-only",
        "--disabled",
    )
    assert result.exit_code == 0
    endpoint = YamlConfigProvider(cli.path).document().endpoints[0]
    assert (endpoint.enabled, endpoint.min_cvss, endpoint.kev_only) == (
        False,
        7.5,
        True,
    )


def test_create_uses_the_public_base_url(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CVESTREAM_HTTP__PUBLIC__BASE_URL", "https://feeds.example.org/")
    result = cli.run("endpoint", "create", "x", "--name", "X")
    assert (
        result.stdout.splitlines()[-1]
        == f"feed: https://feeds.example.org/feeds/{T1}.xml"
    )


def test_create_keeps_a_hand_edit_and_appends(cli: Cli) -> None:
    _seed(cli)
    cli.path.write_text(EXISTING.replace("PHP team", "Hand edited"), encoding="utf-8")
    assert cli.run("endpoint", "create", "x", "--name", "X").exit_code == 0
    document = YamlConfigProvider(cli.path).document()
    assert [e.id for e in document.endpoints] == ["team-php", "other", "x"]
    assert document.endpoints[0].name == "Hand edited"


def test_create_duplicate_id(cli: Cli) -> None:
    _seed(cli)
    before = cli.path.read_bytes()
    result = cli.run("endpoint", "create", "other", "--name", "X")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert cli.stderr(result).strip() == "endpoint 'other' already exists"
    assert cli.path.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [
        ["Not A Slug", "--name", "X"],
        ["x", "--name", " "],
        ["x", "--name", "X", "--min-cvss", "11"],
    ],
    ids=["slug", "blank-name", "min-cvss"],
)
def test_create_invalid_value_leaves_the_file_alone(cli: Cli, args: list[str]) -> None:
    _seed(cli)
    before = cli.path.read_bytes()
    result = cli.run("endpoint", "create", *args)
    assert result.exit_code == 2
    assert result.stdout == ""
    _one_line(cli.stderr(result))
    assert cli.path.read_bytes() == before


def test_create_without_a_name_is_a_usage_error(cli: Cli) -> None:
    assert cli.run("endpoint", "create", "x").exit_code == 2


def test_create_has_no_token_option(cli: Cli) -> None:
    assert (
        cli.run("endpoint", "create", "x", "--name", "X", "--token", "a" * 43).exit_code
        == 2
    )


def test_create_refuses_an_invalid_file_and_names_the_field(cli: Cli) -> None:
    cli.path.write_text("version: 2\n", encoding="utf-8")
    result = cli.run("endpoint", "create", "x", "--name", "X")
    assert result.exit_code == 2
    _one_line(cli.stderr(result))
    assert cli.path.read_text(encoding="utf-8") == "version: 2\n"


def test_a_rewrite_that_drops_comments_says_so_once(cli: Cli) -> None:
    cli.path.write_text("# my notes\n" + EXISTING, encoding="utf-8")
    first = cli.run("endpoint", "create", "x", "--name", "X")
    assert f"{cli.path}: comments were not kept" in cli.stderr(first)
    second = cli.run("endpoint", "create", "y", "--name", "Y")
    assert cli.stderr(second) == ""


def test_a_token_is_not_in_stderr(cli: Cli) -> None:
    result = cli.run("endpoint", "create", "x", "--name", "X")
    assert T1 not in cli.stderr(result)


# --- list ----------------------------------------------------------------------


def test_list_table_sorted_by_id_without_tokens(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "list")
    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ID        NAME      ENABLED  MIN_CVSS  KEV_ONLY  PRODUCTS",
        "other     Other     false    -         false     0",
        "team-php  PHP team  true     7.0       false     2",
    ]
    assert "k" * 43 not in result.stdout
    assert "http" not in result.stdout


def test_list_json(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "list", "--json")
    assert json.loads(result.stdout) == [
        {
            "id": "other",
            "name": "Other",
            "enabled": False,
            "min_cvss": None,
            "kev_only": False,
            "product_count": 0,
        },
        {
            "id": "team-php",
            "name": "PHP team",
            "enabled": True,
            "min_cvss": 7.0,
            "kev_only": False,
            "product_count": 2,
        },
    ]
    assert "k" * 43 not in result.stdout


def test_list_of_a_file_without_endpoints(cli: Cli) -> None:
    cli.path.write_text("version: 1\n", encoding="utf-8")
    assert cli.run("endpoint", "list", "--json").stdout.strip() == "[]"
    assert cli.run("endpoint", "list").stdout.startswith("ID")


def test_list_of_a_missing_file_is_a_user_error(cli: Cli) -> None:
    result = cli.run("endpoint", "list")
    assert result.exit_code == 2
    assert "cannot read" in cli.stderr(result)
    _one_line(cli.stderr(result))


def test_list_does_not_write(cli: Cli) -> None:
    _seed(cli)
    cli.run("endpoint", "list")
    assert cli.path.read_text(encoding="utf-8") == EXISTING


# --- show ----------------------------------------------------------------------


def test_show_text(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "show", "team-php")
    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "id:        team-php",
        "name:      PHP team",
        "enabled:   true",
        "min_cvss:  7.0",
        "kev_only:  false",
        "products:  2",
        "  - php >= 8.2",
        "  - gitlab >= 16.0 (aliases: gl)",
        f"feed:      http://localhost:8080/feeds/{'k' * 43}.xml",
    ]


def test_show_json(cli: Cli) -> None:
    _seed(cli)
    data = json.loads(cli.run("endpoint", "show", "team-php", "--json").stdout)
    assert list(data) == [
        "id",
        "name",
        "enabled",
        "min_cvss",
        "kev_only",
        "products",
        "feed_url",
    ]
    assert data["products"] == [
        {
            "name": "php",
            "aliases": [],
            "exclude": [],
            "min_version": "8.2",
            "match_description": True,
        },
        {
            "name": "gitlab",
            "aliases": ["gl"],
            "exclude": [],
            "min_version": "16.0",
            "match_description": True,
        },
    ]
    assert data["feed_url"] == f"http://localhost:8080/feeds/{'k' * 43}.xml"


def test_show_without_threshold_prints_a_dash_and_null(cli: Cli) -> None:
    _seed(cli)
    assert "min_cvss:  -" in cli.run("endpoint", "show", "other").stdout
    data = json.loads(cli.run("endpoint", "show", "other", "--json").stdout)
    assert data["min_cvss"] is None
    assert data["products"] == []


def test_show_unknown_endpoint(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "show", "nope")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert cli.stderr(result).strip() == "unknown endpoint 'nope'"


# --- edit ----------------------------------------------------------------------


def test_edit_changes_the_given_fields(cli: Cli) -> None:
    _seed(cli)
    result = cli.run(
        "endpoint", "edit", "team-php", "--name", "New", "--kev-only", "--disable"
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "updated endpoint team-php"
    endpoint = YamlConfigProvider(cli.path).document().endpoints[0]
    assert (endpoint.name, endpoint.kev_only, endpoint.enabled) == ("New", True, False)
    assert endpoint.min_cvss == 7.0
    assert endpoint.token == "k" * 43
    assert len(endpoint.products) == 2


def test_edit_the_tri_state_options(cli: Cli) -> None:
    _seed(cli)
    cli.run("endpoint", "edit", "other", "--kev-only")
    cli.run("endpoint", "edit", "other", "--enable")
    cli.run("endpoint", "edit", "other", "--min-cvss", "5")
    other = YamlConfigProvider(cli.path).document().endpoints[1]
    assert (other.kev_only, other.enabled, other.min_cvss) == (True, True, 5.0)
    cli.run("endpoint", "edit", "other", "--no-kev-only")
    assert not YamlConfigProvider(cli.path).document().endpoints[1].kev_only


def test_edit_can_remove_the_threshold(cli: Cli) -> None:
    _seed(cli)
    assert cli.run("endpoint", "edit", "team-php", "--no-min-cvss").exit_code == 0
    assert YamlConfigProvider(cli.path).document().endpoints[0].min_cvss is None


def test_edit_with_no_option_has_nothing_to_change(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "edit", "team-php")
    assert result.exit_code == 2
    assert cli.stderr(result).strip() == "nothing to change"
    assert cli.path.read_text(encoding="utf-8") == EXISTING


def test_edit_min_cvss_and_no_min_cvss_conflict(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "edit", "team-php", "--min-cvss", "5", "--no-min-cvss")
    assert result.exit_code == 2
    _one_line(cli.stderr(result))
    assert cli.path.read_text(encoding="utf-8") == EXISTING


def test_edit_unknown_endpoint(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "edit", "nope", "--name", "x")
    assert result.exit_code == 2
    assert cli.stderr(result).strip() == "unknown endpoint 'nope'"


def test_edit_invalid_value_leaves_the_file_alone(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "edit", "team-php", "--min-cvss", "11")
    assert result.exit_code == 2
    _one_line(cli.stderr(result))
    assert cli.path.read_text(encoding="utf-8") == EXISTING


def test_edit_has_no_token_option(cli: Cli) -> None:
    _seed(cli)
    assert cli.run("endpoint", "edit", "team-php", "--token", "a" * 43).exit_code == 2


# --- delete --------------------------------------------------------------------


def test_delete_with_yes(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "delete", "team-php", "--yes")
    assert result.exit_code == 0
    assert result.stdout.strip() == "deleted endpoint team-php"
    assert _ids(cli) == ["other"]
    assert cli.run("config", "validate").exit_code == 0


def test_delete_without_a_tty_and_without_yes_does_not_hang(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "delete", "team-php")
    assert result.exit_code == 2
    assert cli.stderr(result).strip() == "pass --yes to delete without a prompt"
    assert _ids(cli) == ["team-php", "other"]


def test_delete_asks_for_confirmation(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(cli)
    monkeypatch.setattr("cvestream.cli.endpoint.is_interactive", lambda: True)
    declined = cli.run("endpoint", "delete", "team-php", input="n\n")
    assert declined.exit_code == 2
    assert _ids(cli) == ["team-php", "other"]
    default = cli.run("endpoint", "delete", "team-php", input="\n")
    assert default.exit_code == 2  # the default answer is No
    accepted = cli.run("endpoint", "delete", "team-php", input="y\n")
    assert accepted.exit_code == 0
    assert _ids(cli) == ["other"]


def test_delete_unknown_endpoint(cli: Cli) -> None:
    _seed(cli)
    result = cli.run("endpoint", "delete", "nope", "--yes")
    assert result.exit_code == 2
    assert cli.stderr(result).strip() == "unknown endpoint 'nope'"
    assert cli.path.read_text(encoding="utf-8") == EXISTING


def test_the_daemon_applies_a_cli_change_without_restart(cli: Cli) -> None:
    _seed(cli)
    provider = YamlConfigProvider(cli.path)
    before = provider.revision()
    cli.run("endpoint", "edit", "team-php", "--name", "New")
    assert provider.revision() != before
    assert provider.load().endpoints[0].name == "New"
