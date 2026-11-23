import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvestream.cli.main import app
from cvestream.config.schema import render_schema

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

TOKEN = "k" * 43
VALID = f"""\
version: 1
endpoints:
  - id: team-php
    name: PHP team
    token: {TOKEN}
    products:
      - php >= 8.2
"""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # never read the developer's own settings.yaml / endpoints.yaml
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CVESTREAM_SETTINGS_FILE", raising=False)
    monkeypatch.delenv("CVESTREAM_ENDPOINTS_FILE", raising=False)


def _plain(output: str) -> str:
    return _ANSI.sub("", output)


def _file(tmp_path: Path, content: str, name: str = "endpoints.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_config_without_arguments_shows_help() -> None:
    result = runner.invoke(app, ["config"])
    output = _plain(result.output)
    assert "Usage" in output
    assert "validate" in output
    assert "schema" in output


def test_validate_valid_file(tmp_path: Path) -> None:
    path = _file(tmp_path, VALID)
    result = runner.invoke(app, ["config", "validate", str(path)])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"{path}: valid (1 endpoints)"
    assert result.stderr == ""
    assert TOKEN not in result.output


@pytest.mark.parametrize(
    ("content", "field"),
    [
        (
            VALID.replace("    name:", "    unknown_field: 1\n    name:"),
            "unknown_field",
        ),
        (VALID.replace("team-php", "My_Team"), "endpoints[0].id"),
        (
            VALID.replace("    products:", '    min_cvss: "high"\n    products:'),
            "min_cvss",
        ),
        (
            VALID + "      - php\n",
            "endpoints[0]: products[1]: duplicate product name 'php' "
            "(same as products[0])",
        ),
    ],
    ids=["unknown-field", "bad-slug", "min-cvss-high", "duplicate-product-name"],
)
def test_validate_invalid_file(tmp_path: Path, content: str, field: str) -> None:
    path = _file(tmp_path, content)
    result = runner.invoke(app, ["config", "validate", str(path)])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1
    assert field in result.stderr
    assert "Traceback" not in result.stderr
    assert TOKEN not in result.stderr


def test_validate_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "validate", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
    assert len(result.stderr.splitlines()) == 1
    assert "cannot read" in result.stderr


def test_validate_defaults_to_endpoints_file_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _file(tmp_path, VALID, "elsewhere.yaml")
    monkeypatch.setenv("CVESTREAM_ENDPOINTS_FILE", str(path))
    result = runner.invoke(app, ["config", "validate"])
    assert result.exit_code == 0
    assert str(path) in result.stdout


def test_validate_without_file_uses_default_path(tmp_path: Path) -> None:
    _file(tmp_path, VALID)
    result = runner.invoke(app, ["config", "validate"])
    assert result.exit_code == 0
    assert "endpoints.yaml: valid" in result.stdout


def test_validate_settings_error_is_a_user_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CVESTREAM_SETTINGS_FILE", str(tmp_path / "absent.yaml"))
    result = runner.invoke(app, ["config", "validate"])
    assert result.exit_code == 2
    assert len(result.stderr.splitlines()) == 1
    assert "Traceback" not in result.stderr


def test_schema_writes_the_default_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "schema"])
    assert result.exit_code == 0
    written = tmp_path / "schemas" / "config-v1.json"
    assert written.read_text(encoding="utf-8") == render_schema()


def test_schema_output_path(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "dir" / "schema.json"
    result = runner.invoke(app, ["config", "schema", "--output", str(target)])
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == render_schema()
    assert "\r" not in target.read_text(encoding="utf-8")


def test_schema_output_stdout() -> None:
    result = runner.invoke(app, ["config", "schema", "--output", "-"])
    assert result.exit_code == 0
    assert result.stdout == render_schema()


def test_schema_output_failure_is_one_line(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    result = runner.invoke(
        app, ["config", "schema", "--output", str(blocker / "schema.json")]
    )
    assert result.exit_code == 2
    assert len(result.stderr.splitlines()) == 1


def test_schema_check_fresh(tmp_path: Path) -> None:
    target = tmp_path / "schema.json"
    target.write_text(render_schema(), encoding="utf-8")
    result = runner.invoke(app, ["config", "schema", "--check", "-o", str(target)])
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == render_schema()


def test_schema_check_stale(tmp_path: Path) -> None:
    target = tmp_path / "schema.json"
    target.write_text("{}\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "schema", "--check", "-o", str(target)])
    assert result.exit_code == 2
    assert result.stderr.strip() == (f"{target} is stale; run: cvestream config schema")
    assert target.read_text(encoding="utf-8") == "{}\n"


def test_schema_check_missing(tmp_path: Path) -> None:
    target = tmp_path / "absent.json"
    result = runner.invoke(app, ["config", "schema", "--check", "-o", str(target)])
    assert result.exit_code == 2
    assert "is stale" in result.stderr
    assert not target.exists()


def test_schema_check_refuses_stdout() -> None:
    result = runner.invoke(app, ["config", "schema", "--check", "-o", "-"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1


def test_validate_bad_scalar_is_one_line(tmp_path: Path) -> None:
    path = _file(tmp_path, VALID.replace("PHP team", "2026-13-45"))
    result = runner.invoke(app, ["config", "validate", str(path)])
    assert result.exit_code == 2
    assert result.stderr == f"{path}: 4:11: invalid timestamp value\n"


def test_schema_check_defaults_to_committed_path(tmp_path: Path) -> None:
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "config-v1.json").write_text(
        render_schema(), encoding="utf-8"
    )
    assert runner.invoke(app, ["config", "schema", "--check"]).exit_code == 0
