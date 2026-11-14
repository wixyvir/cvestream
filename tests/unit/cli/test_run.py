import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

import cvestream.app.runtime as runtime
from cvestream.cli.main import app, main
from cvestream.settings import Settings

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in [n for n in os.environ if n.startswith("CVESTREAM_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("CVESTREAM_DATA_DIR", str(tmp_path / "data"))


def _plain(output: str) -> str:
    return _ANSI.sub("", output)


class _Calls:
    def __init__(self) -> None:
        self.settings: list[Settings] = []


def _fake(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    outcome: int | BaseException,
) -> _Calls:
    calls = _Calls()

    async def fake(settings: Settings) -> int:
        calls.settings.append(settings)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(runtime, name, fake)
    return calls


@pytest.mark.parametrize("command", [["run"], ["poll", "--once"]])
@pytest.mark.parametrize("code", [0, 2, 3])
def test_the_exit_code_of_the_runtime_is_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, command: list[str], code: int
) -> None:
    calls = _fake(monkeypatch, "serve" if command[0] == "run" else "poll_once", code)
    result = runner.invoke(app, command)
    assert result.exit_code == code
    assert len(calls.settings) == 1
    assert calls.settings[0].data_dir.name == "data"


def test_poll_without_once_is_a_user_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, "poll_once", 0)
    result = runner.invoke(app, ["poll"])
    assert result.exit_code == 2
    assert calls.settings == []
    assert "--once" in _plain(result.stderr)
    assert len(result.stderr.splitlines()) == 1


@pytest.mark.parametrize("command", [["run"], ["poll", "--once"]])
def test_a_state_error_is_one_line_and_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: list[str]
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("CVESTREAM_DATA_DIR", str(blocker / "data"))
    result = runner.invoke(app, command)
    assert result.exit_code == 2
    assert len(result.stderr.splitlines()) == 1
    assert "cannot create directory" in result.stderr


@pytest.mark.parametrize("command", [["run"], ["poll", "--once"]])
def test_a_bad_setting_is_one_line_and_exit_2(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    monkeypatch.setenv("CVESTREAM_HTTP__PUBLIC__PORT", "70000")
    result = runner.invoke(app, command)
    assert result.exit_code == 2
    assert result.stderr.startswith("settings: http.public.port")
    assert len(result.stderr.splitlines()) == 1
    assert "70000" not in result.stderr


def test_a_valkey_url_with_a_password_is_never_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVESTREAM_CONFIG_PROVIDER", "valkey")
    monkeypatch.setenv("CVESTREAM_VALKEY__URL", "valkey://u:hunter2-secret@valkey:6379")
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert result.stderr.startswith("settings: valkey.url")
    assert len(result.stderr.splitlines()) == 1
    assert "hunter2" not in result.stderr


def test_a_settings_error_is_one_line_and_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CVESTREAM_SETTINGS_FILE", str(tmp_path / "missing.yaml"))
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "settings file not found" in result.stderr
    assert len(result.stderr.splitlines()) == 1


def test_run_with_a_missing_endpoints_file_exits_3_through_the_real_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The runtime binds nothing before it needs the configuration... except the
    # private listener: replace the factory so no socket is opened.
    class _Quiet:
        name = "quiet"
        task = None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    original = runtime.serve

    async def serve(settings: Settings) -> int:
        return await original(settings, server_factory=lambda *_args: _Quiet())

    monkeypatch.setattr(runtime, "serve", serve)
    monkeypatch.setenv("CVESTREAM_ENDPOINTS_FILE", str(tmp_path / "nope.yaml"))
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 3
    assert len(result.stderr.splitlines()) == 1
    assert "nope.yaml" in result.stderr


def _entry_point(monkeypatch: pytest.MonkeyPatch, *argv: str) -> Callable[[], None]:
    monkeypatch.setattr(sys, "argv", ["cvestream", *argv])
    return main


def test_an_unexpected_error_is_one_line_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake(monkeypatch, "serve", RuntimeError("secret detail"))
    with pytest.raises(SystemExit) as raised:
        _entry_point(monkeypatch, "run")()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert err.splitlines() == [
        "unexpected error: RuntimeError (run with --debug for the traceback)"
    ]
    assert "secret detail" not in err
    assert "Traceback" not in err


def test_debug_shows_the_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, "serve", RuntimeError("secret detail"))
    with pytest.raises(RuntimeError, match="secret detail"):
        _entry_point(monkeypatch, "--debug", "run")()


def test_the_debug_flag_does_not_leak_into_the_next_invocation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake(monkeypatch, "serve", RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        _entry_point(monkeypatch, "--debug", "run")()
    with pytest.raises(SystemExit):
        _entry_point(monkeypatch, "run")()
    assert "unexpected error" in capsys.readouterr().err


@pytest.mark.parametrize("code", [0, 2])
def test_the_entry_point_keeps_the_exit_codes_of_commands(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    _fake(monkeypatch, "serve", code)
    with pytest.raises(SystemExit) as raised:
        _entry_point(monkeypatch, "run")()
    assert raised.value.code == code


def test_a_usage_error_keeps_its_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SystemExit) as raised:
        _entry_point(monkeypatch, "no-such-command")()
    assert raised.value.code == 2
