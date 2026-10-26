import os
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from cvestream.errors import CvestreamError
from cvestream.settings import Settings, SettingsError, _load, load_settings


class _SettingsWithSecret(Settings):
    """Test-only subclass: no `SecretStr` field exists in the real settings yet."""

    api_key: SecretStr | None = None


class _NestedSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: SecretStr | None = None


class _SettingsWithNestedSecret(Settings):
    """Test-only subclass shaped like the future `valkey.password` (story 3.1)."""

    valkey: _NestedSecret = Field(default_factory=_NestedSecret)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in [n for n in os.environ if n.startswith("CVESTREAM_")]:
        monkeypatch.delenv(name)
    # the default ./settings.yaml lookup must never see the developer's file
    monkeypatch.chdir(tmp_path)
    yield


def _env(**values: str) -> Mapping[str, str]:
    return values


def test_defaults() -> None:
    settings = load_settings({})
    assert settings.log.level == "info"
    assert settings.log.format == "json"
    assert settings.sources.euvd.enabled is True
    assert settings.sources.euvd.interval == timedelta(minutes=15)
    assert settings.sources.cisa_kev.interval == timedelta(minutes=15)


def test_empty_environ_does_not_fall_back_to_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVESTREAM_LOG__LEVEL", "debug")
    assert load_settings({}).log.level == "info"


def test_load_settings_defaults_to_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVESTREAM_LOG__LEVEL", "debug")
    assert load_settings().log.level == "debug"


def test_env_override_with_nesting() -> None:
    settings = load_settings(_env(CVESTREAM_SOURCES__EUVD__INTERVAL="PT30M"))
    assert settings.sources.euvd.interval == timedelta(minutes=30)
    assert settings.sources.cisa_kev.interval == timedelta(minutes=15)


def test_env_bool_and_literal_override() -> None:
    settings = load_settings(
        _env(
            CVESTREAM_SOURCES__CISA_KEV__ENABLED="false",
            CVESTREAM_LOG__FORMAT="console",
        )
    )
    assert settings.sources.cisa_kev.enabled is False
    assert settings.log.format == "console"


def test_interval_upper_bound_is_accepted() -> None:
    settings = load_settings(_env(CVESTREAM_SOURCES__EUVD__INTERVAL="PT60M"))
    assert settings.sources.euvd.interval == timedelta(minutes=60)


@pytest.mark.parametrize("value", ["PT61M", "PT0S", "-PT5M"])
def test_interval_out_of_bounds_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        load_settings(_env(CVESTREAM_SOURCES__EUVD__INTERVAL=value))


def test_invalid_literal_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_settings(_env(CVESTREAM_LOG__LEVEL="verbose"))


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "custom.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_yaml_file_values(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "log:\n  level: warning\nsources:\n  euvd:\n    interval: PT20M\n"
        "  cisa_kev:\n    interval: 1800\n",
    )
    settings = load_settings(_env(CVESTREAM_SETTINGS_FILE=str(path)))
    assert settings.log.level == "warning"
    assert settings.sources.euvd.interval == timedelta(minutes=20)
    assert settings.sources.cisa_kev.interval == timedelta(seconds=1800)


def test_default_yaml_path_is_cwd_settings_yaml(tmp_path: Path) -> None:
    (tmp_path / "settings.yaml").write_text("log:\n  level: error\n", encoding="utf-8")
    assert load_settings({}).log.level == "error"


def test_env_beats_yaml_and_values_deep_merge(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        "log:\n  level: warning\nsources:\n  euvd:\n    interval: PT20M\n",
    )
    settings = load_settings(
        _env(
            CVESTREAM_SETTINGS_FILE=str(path),
            CVESTREAM_LOG__LEVEL="debug",
            CVESTREAM_SOURCES__EUVD__ENABLED="false",
        )
    )
    assert settings.log.level == "debug"
    assert settings.sources.euvd.interval == timedelta(minutes=20)
    assert settings.sources.euvd.enabled is False


def test_explicit_missing_settings_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(SettingsError, match=r"nope\.yaml") as excinfo:
        load_settings(_env(CVESTREAM_SETTINGS_FILE=str(missing)))
    assert isinstance(excinfo.value, CvestreamError)


@pytest.mark.parametrize(
    ("content", "reason"),
    [("log: [\n", "cannot parse YAML"), ("- a\n", "must be a mapping")],
)
def test_malformed_yaml_raises_settings_error(
    tmp_path: Path, content: str, reason: str
) -> None:
    path = _write_yaml(tmp_path, content)
    with pytest.raises(SettingsError, match=reason) as excinfo:
        load_settings(_env(CVESTREAM_SETTINGS_FILE=str(path)))
    assert str(path) in str(excinfo.value)


def test_empty_yaml_file_gives_defaults(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "")
    assert load_settings(_env(CVESTREAM_SETTINGS_FILE=str(path))).log.level == "info"


def test_unknown_yaml_key_is_rejected(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "bogus: 1\n")
    with pytest.raises(ValidationError):
        load_settings(_env(CVESTREAM_SETTINGS_FILE=str(path)))


def test_unknown_nested_yaml_key_is_rejected(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "sources:\n  euvd:\n    colour: red\n")
    with pytest.raises(ValidationError):
        load_settings(_env(CVESTREAM_SETTINGS_FILE=str(path)))


def test_file_secret_is_read_and_trailing_newline_stripped(tmp_path: Path) -> None:
    secret = tmp_path / "api_key"
    secret.write_text("s3cret\n", encoding="utf-8")
    settings = _load(_SettingsWithSecret, _env(CVESTREAM_API_KEY_FILE=str(secret)))
    assert isinstance(settings, _SettingsWithSecret)
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(settings)


def test_nested_file_secret_is_read(tmp_path: Path) -> None:
    secret = tmp_path / "password"
    secret.write_text("pw\n", encoding="utf-8")
    settings = _load(
        _SettingsWithNestedSecret, _env(CVESTREAM_VALKEY__PASSWORD_FILE=str(secret))
    )
    assert isinstance(settings, _SettingsWithNestedSecret)
    assert settings.valkey.password is not None
    assert settings.valkey.password.get_secret_value() == "pw"


def test_nested_secret_and_secret_file_together_is_an_error(tmp_path: Path) -> None:
    secret = tmp_path / "password"
    secret.write_text("pw", encoding="utf-8")
    with pytest.raises(SettingsError, match="CVESTREAM_VALKEY__PASSWORD"):
        _load(
            _SettingsWithNestedSecret,
            _env(
                CVESTREAM_VALKEY__PASSWORD="plain",  # noqa: S106
                CVESTREAM_VALKEY__PASSWORD_FILE=str(secret),
            ),
        )


def test_secret_and_secret_file_together_is_an_error(tmp_path: Path) -> None:
    secret = tmp_path / "api_key"
    secret.write_text("s3cret", encoding="utf-8")
    with pytest.raises(SettingsError, match="CVESTREAM_API_KEY"):
        _load(
            _SettingsWithSecret,
            _env(CVESTREAM_API_KEY="plain", CVESTREAM_API_KEY_FILE=str(secret)),
        )


def test_unreadable_secret_file_names_variable_and_path_not_content(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "absent"
    with pytest.raises(SettingsError) as excinfo:
        _load(_SettingsWithSecret, _env(CVESTREAM_API_KEY_FILE=str(missing)))
    message = str(excinfo.value)
    assert "CVESTREAM_API_KEY_FILE" in message
    assert str(missing) in message


def test_file_variable_for_a_non_secret_field_is_left_alone(tmp_path: Path) -> None:
    # e.g. a future plain `endpoints_file` field must never be read as a secret
    other = tmp_path / "other"
    other.write_text("not a secret", encoding="utf-8")
    settings = load_settings(_env(CVESTREAM_ENDPOINTS_FILE=str(other)))
    assert settings.log.level == "info"


def test_plain_secret_from_env_still_works() -> None:
    settings = _load(_SettingsWithSecret, _env(CVESTREAM_API_KEY="plain"))
    assert isinstance(settings, _SettingsWithSecret)
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "plain"


def test_config_defaults() -> None:
    settings = load_settings({})
    assert settings.config_provider == "yaml"
    assert settings.endpoints_file == Path("endpoints.yaml")


def test_endpoints_file_from_environment_is_not_a_secret_file() -> None:
    settings = load_settings(_env(CVESTREAM_ENDPOINTS_FILE="/x/e.yaml"))
    assert settings.endpoints_file == Path("/x/e.yaml")


def test_endpoints_file_from_yaml(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "endpoints_file: conf/teams.yaml\n")
    settings = load_settings(_env(CVESTREAM_SETTINGS_FILE=str(path)))
    assert settings.endpoints_file == Path("conf/teams.yaml")


def test_config_provider_from_environment() -> None:
    settings = load_settings(_env(CVESTREAM_CONFIG_PROVIDER="yaml"))
    assert settings.config_provider == "yaml"


def test_valkey_config_provider_is_rejected_until_it_exists() -> None:
    with pytest.raises(ValidationError):
        load_settings(_env(CVESTREAM_CONFIG_PROVIDER="valkey"))


@pytest.mark.parametrize(
    "content",
    ["log: {level: 2026-13-45}\n", "x: !!int abc\n", "a: " + "[" * 100000 + "\n"],
)
def test_yaml_value_errors_become_settings_errors(tmp_path: Path, content: str) -> None:
    path = _write_yaml(tmp_path, content)
    with pytest.raises(SettingsError, match="cannot parse YAML") as excinfo:
        load_settings(_env(CVESTREAM_SETTINGS_FILE=str(path)))
    assert str(path) in str(excinfo.value)
