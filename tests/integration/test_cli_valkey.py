"""The operator CLI against a real Valkey server with the repo's ACL file.

Needs `VALKEY_TEST_ADDRESS` (see `valkey_support`); skipped without it, a
failure on CI. The provider's write protocol and its races are covered by
`test_valkey_provider.py`; these tests are about the commands.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result
from valkey import Valkey

from cvestream.cli.context import CliState
from cvestream.cli.main import app
from cvestream.config import keys
from tests.integration import valkey_support as support

pytestmark = [pytest.mark.valkey, pytest.mark.allow_hosts(support.ALLOWED_HOSTS)]

TOKENS = ["".join([c] * 43) for c in "tuvwxyz"]
OLD = "k" * 43


class Cli:
    def __init__(self, user: str, password: str | None = None) -> None:
        self.user = user
        self.password = password
        self.tokens = iter(TOKENS)

    def run(self, *args: str) -> Result:
        env = {
            "CVESTREAM_CONFIG_PROVIDER": "valkey",
            "CVESTREAM_VALKEY__URL": support.url(self.user),
        }
        if self.password is None:
            file = support.SECRETS / f"{self.user}.password"
            env["CVESTREAM_VALKEY__PASSWORD_FILE"] = str(file)
        else:
            env["CVESTREAM_VALKEY__PASSWORD"] = self.password
        state = CliState(new_token=lambda: next(self.tokens))
        return CliRunner().invoke(app, list(args), obj=state, env=env)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # never the developer's settings.yaml
    for name in ("CVESTREAM_SETTINGS_FILE", "CVESTREAM_ENDPOINTS_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def as_admin(admin: Valkey) -> Cli:
    return Cli(support.ADMIN)


@pytest.fixture
def as_daemon(admin: Valkey) -> Cli:
    return Cli(support.DAEMON)


def _revision(admin: Valkey) -> int:
    value = support.get(admin, keys.REVISION)
    return int(value) if value else 0


def _ok(result: Result) -> Result:
    assert result.exit_code == 0, result.stderr
    return result


def _seed(cli: Cli) -> None:
    _ok(
        cli.run(
            "endpoint", "create", "team-php", "--name", "PHP team", "--min-cvss", "7"
        )
    )
    _ok(cli.run("product", "add", "team-php", "php >= 8.2"))
    _ok(cli.run("product", "add", "team-php", "symfony", "--alias", "sf"))
    _ok(cli.run("endpoint", "create", "other", "--name", "Other", "--disabled"))


def test_create_on_an_unseeded_store_seeds_it(as_admin: Cli, admin: Valkey) -> None:
    result = _ok(as_admin.run("endpoint", "create", "x", "--name", "X"))
    assert result.stdout.splitlines() == [
        "created endpoint x",
        f"feed: http://localhost:8080/feeds/{TOKENS[0]}.xml",
    ]
    assert support.get(admin, keys.SCHEMA) == b"1"
    assert _revision(admin) == 1
    assert support.members(admin, keys.ENDPOINTS) == {b"x"}
    assert support.get(admin, keys.token("x")) == TOKENS[0].encode()
    assert json.loads(support.get(admin, keys.endpoint("x")) or b"") == {
        "enabled": True,
        "kev_only": False,
        "min_cvss": None,
        "name": "X",
    }
    assert json.loads(support.get(admin, keys.products("x")) or b"") == []


def test_every_write_command_grows_the_revision_by_exactly_one(
    as_admin: Cli, admin: Valkey
) -> None:
    commands = [
        ("endpoint", "create", "team-php", "--name", "PHP team"),
        ("product", "add", "team-php", "php >= 8.2"),
        ("product", "add", "team-php", "go"),
        ("endpoint", "edit", "team-php", "--kev-only", "--min-cvss", "7.5"),
        ("token", "rotate", "team-php"),
        ("product", "remove", "team-php", "go"),
        ("endpoint", "create", "other", "--name", "Other"),
        ("endpoint", "delete", "other", "--yes"),
    ]
    for expected, command in enumerate(commands, start=1):
        _ok(as_admin.run(*command))
        assert _revision(admin) == expected, command


def test_a_failed_command_leaves_the_keys_and_the_revision_alone(
    as_admin: Cli, admin: Valkey
) -> None:
    _seed(as_admin)
    revision = _revision(admin)
    before = {
        key: support.get(admin, key)
        for key in (
            keys.endpoint("team-php"),
            keys.products("team-php"),
            keys.token("team-php"),
        )
    }
    for command in (
        ("endpoint", "create", "team-php", "--name", "dup"),
        ("endpoint", "create", "x", "--name", "X", "--min-cvss", "11"),
        ("endpoint", "edit", "team-php"),
        ("product", "add", "team-php", "PHP"),
        ("product", "remove", "team-php", "nope"),
        ("token", "rotate", "nope"),
    ):
        result = as_admin.run(*command)
        assert result.exit_code == 2, command
        assert len(result.stderr.strip().splitlines()) == 1
    assert _revision(admin) == revision
    assert before == {key: support.get(admin, key) for key in before}


def test_keys_follow_the_documented_layout(as_admin: Cli, admin: Valkey) -> None:
    _seed(as_admin)
    assert support.members(admin, keys.ENDPOINTS) == {b"team-php", b"other"}
    assert json.loads(support.get(admin, keys.endpoint("team-php")) or b"") == {
        "enabled": True,
        "kev_only": False,
        "min_cvss": 7.0,
        "name": "PHP team",
    }
    products = json.loads(support.get(admin, keys.products("team-php")) or b"")
    assert products[0] == "php >= 8.2"
    assert products[1]["name"] == "symfony"
    assert products[1]["aliases"] == ["sf"]
    token = support.get(admin, keys.token("team-php"))
    assert token is not None
    assert len(token) == 43


def test_delete_removes_the_set_member_and_the_three_keys(
    as_admin: Cli, admin: Valkey
) -> None:
    _seed(as_admin)
    _ok(as_admin.run("endpoint", "delete", "team-php", "--yes"))
    assert support.members(admin, keys.ENDPOINTS) == {b"other"}
    for key in (
        keys.endpoint("team-php"),
        keys.products("team-php"),
        keys.token("team-php"),
    ):
        assert support.get(admin, key) is None
    assert support.get(admin, keys.endpoint("other")) is not None


def test_list_show_and_products_work_as_the_daemon_user(
    as_admin: Cli, as_daemon: Cli
) -> None:
    _seed(as_admin)
    listed = json.loads(_ok(as_daemon.run("endpoint", "list", "--json")).stdout)
    assert [e["id"] for e in listed] == ["other", "team-php"]
    shown = json.loads(
        _ok(as_daemon.run("endpoint", "show", "team-php", "--json")).stdout
    )
    assert shown["feed_url"].endswith(f"/feeds/{TOKENS[0]}.xml")
    assert [p["name"] for p in shown["products"]] == ["php", "symfony"]
    products = json.loads(
        _ok(as_daemon.run("product", "list", "team-php", "--json")).stdout
    )
    assert products[1]["aliases"] == ["sf"]
    assert TOKENS[0] not in _ok(as_daemon.run("endpoint", "list")).stdout
    assert TOKENS[0] in _ok(as_daemon.run("config", "export")).stdout


def test_a_write_as_the_daemon_user_is_refused_in_one_line(
    as_admin: Cli, as_daemon: Cli, admin: Valkey
) -> None:
    _seed(as_admin)
    revision = _revision(admin)
    for command in (
        ("endpoint", "create", "x", "--name", "X"),
        ("endpoint", "edit", "team-php", "--name", "New"),
        ("token", "rotate", "team-php"),
        ("product", "add", "team-php", "go"),
    ):
        result = as_daemon.run(*command)
        assert result.exit_code == 2, command
        assert result.stdout == ""
        lines = result.stderr.strip().splitlines()
        assert len(lines) == 1
        assert support.DAEMON in lines[0]
        assert "Traceback" not in result.stderr
    assert _revision(admin) == revision


def test_wrong_credentials_are_one_line(admin: Valkey) -> None:
    result = Cli(support.ADMIN, password="not-the-password").run("endpoint", "list")
    assert result.exit_code == 2
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert support.ADMIN in lines[0]
    assert "not-the-password" not in result.stderr


def test_the_token_is_only_in_the_feed_url_lines(as_admin: Cli) -> None:
    out = _ok(as_admin.run("endpoint", "create", "x", "--name", "X")).stdout
    out += _ok(as_admin.run("product", "add", "x", "go")).stdout
    assert out.count(TOKENS[0]) == 1
    assert OLD not in out


def test_export_import_export_round_trip_from_yaml_to_valkey(
    as_admin: Cli, tmp_path: Path
) -> None:
    # Hand-written, unsorted, with defaults spelled out and a comment.
    source = tmp_path / "source.yaml"
    source.write_text(
        "# hand written\n"
        "version: 1\n"
        "endpoints:\n"
        f"  - id: team-php\n    name: PHP team\n    token: {TOKENS[0]}\n"
        "    enabled: true\n    min_cvss: 7.5\n"
        "    products:\n"
        "      - php >= 8.2\n"
        '      - {name: symfony, aliases: [sf], min_version: "6.4"}\n'
        f"  - id: other\n    name: Other\n    token: {TOKENS[1]}\n    enabled: false\n",
        encoding="utf-8",
    )
    # First export: the YAML provider (the default), reading `source.yaml`.
    yaml_export = CliRunner().invoke(
        app, ["config", "export"], env={"CVESTREAM_ENDPOINTS_FILE": str(source)}
    )
    assert yaml_export.exit_code == 0, yaml_export.stderr
    # Import into Valkey, then export from Valkey: the same bytes.
    exported_file = tmp_path / "exported.yaml"
    exported_file.write_text(yaml_export.stdout, encoding="utf-8")
    _ok(as_admin.run("config", "import", str(exported_file)))
    assert _ok(as_admin.run("config", "export")).stdout == yaml_export.stdout


def test_import_replaces_and_grows_the_revision_by_one(
    as_admin: Cli, admin: Valkey, tmp_path: Path
) -> None:
    _seed(as_admin)
    source = tmp_path / "source.yaml"
    source.write_text(
        f"version: 1\nendpoints:\n  - id: team-go\n    name: Go\n    token: {OLD}\n",
        encoding="utf-8",
    )
    revision = _revision(admin)
    result = _ok(as_admin.run("config", "import", str(source)))
    assert "(added 1, updated 0, removed 2)" in result.stdout
    assert _revision(admin) == revision + 1
    assert support.members(admin, keys.ENDPOINTS) == {b"team-go"}
    assert support.get(admin, keys.token("team-php")) is None
    assert support.get(admin, keys.token("team-go")) == OLD.encode()


def test_import_seeds_an_unseeded_store(
    as_admin: Cli, admin: Valkey, tmp_path: Path
) -> None:
    source = tmp_path / "source.yaml"
    source.write_text(
        f"version: 1\nendpoints:\n  - id: x\n    name: X\n    token: {OLD}\n",
        encoding="utf-8",
    )
    result = _ok(as_admin.run("config", "import", str(source)))
    assert "the previous content could not be read" in result.stdout
    assert support.get(admin, keys.SCHEMA) == b"1"
    assert _revision(admin) == 1


def test_import_repairs_a_store_with_invalid_products_json(
    as_admin: Cli, admin: Valkey, tmp_path: Path
) -> None:
    _seed(as_admin)
    admin.set(keys.products("team-php"), "{not json")
    admin.sadd(keys.ENDPOINTS, "Not A Slug")
    # Every edit refuses the invalid store ...
    broken = as_admin.run("endpoint", "edit", "team-php", "--name", "New")
    assert broken.exit_code == 2
    assert len(broken.stderr.strip().splitlines()) == 1
    # ... and the import repairs it.
    source = tmp_path / "source.yaml"
    source.write_text(
        f"version: 1\nendpoints:\n  - id: team-php\n    name: PHP\n    token: {OLD}\n"
        "    products: [php]\n",
        encoding="utf-8",
    )
    _ok(as_admin.run("config", "import", str(source)))
    assert support.members(admin, keys.ENDPOINTS) == {b"team-php"}
    assert json.loads(support.get(admin, keys.products("team-php")) or b"") == ["php"]
    assert support.get(admin, keys.endpoint("other")) is None
    _ok(as_admin.run("endpoint", "edit", "team-php", "--name", "Fixed"))
