import hashlib
from pathlib import Path

import pytest

from cvestream.config.provider import (
    ConfigProvider,
    ConfigUnavailableError,
    ConfigValidationError,
)
from cvestream.config.yaml_provider import YamlConfigProvider

TOKEN = "t" * 43
VALID = f"""\
version: 1
endpoints:
  - id: team-php
    name: PHP team
    token: {TOKEN}
    products:
      - php >= 8.2
"""


def _write(tmp_path: Path, content: str | bytes) -> Path:
    path = tmp_path / "endpoints.yaml"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _load_error(tmp_path: Path, content: str | bytes) -> ConfigValidationError:
    provider = YamlConfigProvider(_write(tmp_path, content))
    with pytest.raises(ConfigValidationError) as excinfo:
        provider.load()
    return excinfo.value


def test_satisfies_protocol(tmp_path: Path) -> None:
    provider: ConfigProvider = YamlConfigProvider(_write(tmp_path, VALID))
    assert provider.load().endpoints


def test_revision_is_sha256_of_content(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    expected = hashlib.sha256(VALID.encode()).hexdigest()
    assert provider.revision() == expected
    assert provider.revision() == provider.revision()
    assert provider.load().revision == expected


def test_one_byte_change_changes_revision(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    before = provider.revision()
    path.write_text(VALID + "#", encoding="utf-8")
    assert provider.revision() != before
    assert provider.load().revision == provider.revision()


def test_load_builds_snapshot(tmp_path: Path) -> None:
    snapshot = YamlConfigProvider(_write(tmp_path, VALID)).load()
    endpoint = snapshot.endpoint_for_token(TOKEN)
    assert endpoint is not None
    assert endpoint.id == "team-php"
    assert endpoint.products[0].name == "php"


def test_valid_change_gives_new_snapshot(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    first = provider.load()
    path.write_text(VALID.replace("PHP team", "PHP squad"), encoding="utf-8")
    second = provider.load()
    assert second.revision != first.revision
    assert second.endpoints[0].name == "PHP squad"
    assert first.endpoints[0].name == "PHP team"


def test_invalid_change_keeps_earlier_snapshot_usable(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    last_valid = provider.load()
    path.write_text(VALID.replace("team-php", "My_Team"), encoding="utf-8")
    with pytest.raises(ConfigValidationError) as excinfo:
        provider.load()
    assert "endpoints[0].id" in str(excinfo.value)
    assert last_valid.endpoints[0].id == "team-php"
    assert last_valid.endpoint_for_token(TOKEN) is not None


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_unreadable_is_unavailable(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "endpoints.yaml"
    if kind == "directory":
        path.mkdir()
    provider = YamlConfigProvider(path)
    with pytest.raises(ConfigUnavailableError) as excinfo:
        provider.load()
    assert str(path) in str(excinfo.value)
    assert "cannot read" in str(excinfo.value)
    with pytest.raises(ConfigUnavailableError):
        provider.revision()


@pytest.mark.parametrize(
    "content", ["", "# only a comment\n", "- a\n- b\n", "just a string\n"]
)
def test_top_level_must_be_a_mapping(tmp_path: Path, content: str) -> None:
    error = _load_error(tmp_path, content)
    assert "top level must be a mapping" in str(error)


def test_non_utf8_bytes(tmp_path: Path) -> None:
    error = _load_error(tmp_path, b"version: 1\nname: \xff\xfe\n")
    assert "UTF-8" in str(error)


def test_syntax_error_has_position_and_no_source_text(tmp_path: Path) -> None:
    error = _load_error(tmp_path, f"version: 1\nendpoints: [\n  token: {TOKEN}: x\n")
    message = str(error)
    assert TOKEN not in message
    assert "\n" not in message
    assert ":" in message
    assert any(char.isdigit() for char in message)


def test_syntax_error_line_and_column(tmp_path: Path) -> None:
    error = _load_error(tmp_path, f"a: 1\nb: c: {TOKEN}\n")
    assert str(error).startswith(f"{tmp_path / 'endpoints.yaml'}: 2:5: ")
    assert TOKEN not in str(error)


def test_duplicate_key_top_level(tmp_path: Path) -> None:
    error = _load_error(tmp_path, "version: 1\nversion: 1\n")
    assert "duplicate key 'version'" in str(error)
    assert ": 2:1: " in str(error)


def test_duplicate_key_nested(tmp_path: Path) -> None:
    content = VALID + "    min_cvss: 7\n    min_cvss: 9\n"
    error = _load_error(tmp_path, content)
    assert "duplicate key 'min_cvss'" in str(error)
    assert ": 9:5: " in str(error)


def test_unquoted_version_asks_for_quotes(tmp_path: Path) -> None:
    content = VALID + "      - name: gitlab\n        min_version: 8.10\n"
    error = _load_error(tmp_path, content)
    assert "quoted string" in str(error)
    assert "endpoints[0].products[1].min_version" in str(error)


def test_validation_error_has_no_token(tmp_path: Path) -> None:
    content = VALID.replace(TOKEN, TOKEN + "/")
    error = _load_error(tmp_path, content)
    assert TOKEN not in str(error)
    assert all(TOKEN not in line for line in error.errors)


def test_example_file_is_valid() -> None:
    path = Path(__file__).parents[3] / "endpoints.example.yaml"
    snapshot = YamlConfigProvider(path).load()
    assert len(snapshot.endpoints) >= 2
    assert {e.enabled for e in snapshot.endpoints} == {True, False}
    assert all("REPLACE-ME" in token for token in snapshot.tokens)
