import pytest
from typer.testing import CliRunner

from cvestream.cli.main import app
from cvestream.config.yaml_provider import YamlConfigProvider

from .conftest import GENERATED, Cli

T1 = GENERATED[0]
OLD_A, OLD_B = "k" * 43, "m" * 43
EXISTING = f"""\
version: 1
endpoints:
  - id: team-php
    name: PHP team
    token: {OLD_A}
    products: [php]
  - id: other
    name: Other
    token: {OLD_B}
"""


@pytest.fixture
def seeded(cli: Cli) -> Cli:
    cli.path.write_text(EXISTING, encoding="utf-8")
    return cli


def _tokens(cli: Cli) -> dict[str, str]:
    document = YamlConfigProvider(cli.path).document()
    return {e.id: e.token for e in document.endpoints}


def test_rotate_prints_the_new_feed_url(seeded: Cli) -> None:
    result = seeded.run("token", "rotate", "team-php")
    assert result.exit_code == 0
    assert result.stdout == f"feed: http://localhost:8080/feeds/{T1}.xml\n"
    assert _tokens(seeded) == {"team-php": T1, "other": OLD_B}


def test_rotate_says_when_the_old_url_stops(seeded: Cli) -> None:
    result = seeded.run("token", "rotate", "team-php")
    message = seeded.stderr(result)
    assert len(message.strip().splitlines()) == 1
    assert "old URL stops working once the daemon reloads" in message
    assert OLD_A not in message
    assert T1 not in message


def test_rotate_keeps_everything_else(seeded: Cli) -> None:
    seeded.run("token", "rotate", "team-php")
    document = YamlConfigProvider(seeded.path).document()
    assert document.endpoints[0].products == ["php"]
    assert document.endpoints[0].name == "PHP team"
    assert seeded.run("config", "validate").exit_code == 0


def test_rotate_changes_the_revision(seeded: Cli) -> None:
    provider = YamlConfigProvider(seeded.path)
    before = provider.revision()
    seeded.run("token", "rotate", "other")
    assert provider.revision() != before


def test_rotate_twice_gives_two_different_tokens(seeded: Cli) -> None:
    seeded.run("token", "rotate", "team-php")
    seeded.run("token", "rotate", "team-php")
    assert _tokens(seeded)["team-php"] == GENERATED[1]


def test_rotate_unknown_endpoint(seeded: Cli) -> None:
    result = seeded.run("token", "rotate", "nope")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert seeded.stderr(result).strip() == "unknown endpoint 'nope'"
    assert seeded.path.read_text(encoding="utf-8") == EXISTING


def test_rotate_to_a_token_another_endpoint_has_is_refused(seeded: Cli) -> None:
    # A collision is practically impossible with 256 bits: force one.
    seeded.tokens = iter([OLD_B])
    result = seeded.run("token", "rotate", "team-php")
    assert result.exit_code == 2
    assert "same token as endpoint 'team-php'" in seeded.stderr(result)
    assert OLD_B not in seeded.stderr(result)
    assert seeded.path.read_text(encoding="utf-8") == EXISTING


def test_rotate_has_no_token_option(seeded: Cli) -> None:
    assert seeded.run("token", "rotate", "team-php", "--token", "a" * 43).exit_code == 2


def test_the_default_generator_makes_url_safe_256_bit_tokens(seeded: Cli) -> None:
    result = CliRunner().invoke(app, ["token", "rotate", "team-php"])
    assert result.exit_code == 0
    token = _tokens(seeded)["team-php"]
    assert len(token) == 43
    assert token not in (OLD_A, T1)
    assert token in result.stdout
