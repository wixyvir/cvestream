import re

from typer.testing import CliRunner

import cvestream
import cvestream.__main__ as entry
from cvestream.cli.main import app, main

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Rich help output is colored when CI sets GITHUB_ACTIONS or FORCE_COLOR."""
    return _ANSI.sub("", output)


def test_help_shows_usage() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = _plain(result.output)
    assert "Usage" in output
    assert "--debug" in output
    assert "--version" in output


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert _plain(result.output).strip() == cvestream.__version__


def test_no_args_prints_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage" in _plain(result.output)


def test_module_entry_point_imports_main() -> None:
    assert entry.main is main
