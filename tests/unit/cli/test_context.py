from pathlib import Path

import pytest
import typer
from typer.core import TyperCommand

from cvestream.cli.context import CliState, default_token, open_store, state_of
from cvestream.config.provider import ConfigUnavailableError
from cvestream.config.yaml_provider import YamlConfigProvider


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("CVESTREAM_SETTINGS_FILE", "CVESTREAM_ENDPOINTS_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_default_token_is_url_safe_and_unique() -> None:
    first, second = default_token(), default_token()
    assert first != second
    assert len(first) == 43
    assert first.replace("-", "").replace("_", "").isalnum()


def test_state_defaults() -> None:
    state = CliState()
    assert state.debug is False
    assert len(state.new_token()) == 43


def test_state_of_keeps_an_injected_object() -> None:
    state = CliState(new_token=lambda: "x" * 43)
    ctx = typer.Context(TyperCommand("x"), obj=state)
    assert state_of(ctx) is state


def test_open_store_yields_the_active_provider() -> None:
    with open_store() as session:
        assert isinstance(session.store, YamlConfigProvider)
        assert session.settings.endpoints_file == Path("endpoints.yaml")


def test_open_store_turns_a_config_error_into_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit) as excinfo, open_store():
        raise ConfigUnavailableError("endpoints.yaml: cannot read: OSError")
    assert excinfo.value.exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "endpoints.yaml: cannot read: OSError\n"


def test_open_store_reports_a_bad_setting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CVESTREAM_HTTP__PUBLIC__PORT", "0")
    with pytest.raises(typer.Exit) as excinfo, open_store():
        pytest.fail("must not open")
    assert excinfo.value.exit_code == 2
    assert capsys.readouterr().err.startswith("settings: http.public.port")


def test_open_store_closes_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []
    monkeypatch.setattr(
        "cvestream.cli.context.open_provider",
        lambda _: (object(), lambda: closed.append(True)),
    )
    with open_store():
        pass
    assert closed == [True]
