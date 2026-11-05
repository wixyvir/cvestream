"""Fixtures of the operator CLI tests: a YAML provider in `tmp_path`, fixed tokens."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from cvestream.cli.context import CliState
from cvestream.cli.main import app

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# The tokens the CLI generates, in order: `t`*43, `u`*43, ...
GENERATED = ["".join([c] * 43) for c in "tuvwxyz"]


@dataclass
class Cli:
    path: Path
    runner: CliRunner = field(default_factory=CliRunner)
    _tokens: Iterator[str] = field(default_factory=lambda: iter(GENERATED))

    def run(self, *args: str, input: str | None = None) -> Result:
        state = CliState(new_token=lambda: next(self._tokens))
        return self.runner.invoke(app, list(args), obj=state, input=input)

    def stderr(self, result: Result) -> str:
        return _ANSI.sub("", result.stderr)


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Cli:
    # never read the developer's own settings.yaml / endpoints.yaml
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CVESTREAM_SETTINGS_FILE", raising=False)
    path = tmp_path / "endpoints.yaml"
    monkeypatch.setenv("CVESTREAM_ENDPOINTS_FILE", str(path))
    return Cli(path)
