from typer.testing import CliRunner

import cvestream
import cvestream.__main__ as entry
from cvestream.cli.main import app, main

runner = CliRunner()


def test_help_shows_usage() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output
    assert "--debug" in result.output
    assert "--version" in result.output


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == cvestream.__version__


def test_no_args_prints_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output


def test_module_entry_point_imports_main() -> None:
    assert entry.main is main
