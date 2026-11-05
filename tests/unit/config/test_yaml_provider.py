import errno
import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cvestream.config.models import ConfigDocument, EndpointConfig
from cvestream.config.provider import (
    ConfigConflictError,
    ConfigError,
    ConfigProvider,
    ConfigUnavailableError,
    ConfigValidationError,
)
from cvestream.config.yaml_provider import HEADER, YamlConfigProvider, dump_document

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


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("    name: 2026-13-45\n", "4:11: invalid timestamp value"),
        (f"    name: !!int {TOKEN}\n", "4:11: invalid int value"),
    ],
    ids=["impossible-date", "bad-tagged-scalar"],
)
def test_bad_scalar_is_a_validation_error_without_its_value(
    tmp_path: Path, line: str, problem: str
) -> None:
    # PyYAML raises a bare ValueError here, quoting the value.
    content = VALID.replace("    name: PHP team\n", line)
    error = _load_error(tmp_path, content)
    assert str(error).endswith(f": {problem}")
    assert TOKEN not in str(error)
    assert "2026-13-45" not in str(error)


def test_too_deep_nesting_is_a_validation_error(tmp_path: Path) -> None:
    content = "version: 1\nx: " + "[" * 5000 + "]" * 5000 + "\n"
    error = _load_error(tmp_path, content)
    assert error.errors == ("nesting is too deep",)


def test_merge_keys_are_supported(tmp_path: Path) -> None:
    content = f"""\
version: 1
endpoints:
  - &base
    id: team-php
    name: PHP team
    kev_only: true
    token: {TOKEN}
  - <<: *base
    id: team-php-two
    kev_only: false
    token: {"u" * 43}
"""
    snapshot = YamlConfigProvider(_write(tmp_path, content)).load()
    second = snapshot.endpoints[1]
    assert (second.id, second.name) == ("team-php-two", "PHP team")
    assert second.rules.kev_only is False


def test_duplicate_key_next_to_merge_key(tmp_path: Path) -> None:
    content = "a: &x {b: 1}\nc:\n  <<: *x\n  d: 1\n  d: 2\n"
    error = _load_error(tmp_path, content)
    assert "duplicate key 'd'" in str(error)


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


# --- writing -------------------------------------------------------------------

TOKEN_2 = "u" * 43


def _add_endpoint(
    endpoint_id: str, token: str = TOKEN_2
) -> Callable[[ConfigDocument], ConfigDocument]:
    def change(document: ConfigDocument) -> ConfigDocument:
        endpoint = EndpointConfig(id=endpoint_id, name="Other", token=token)
        return document.model_copy(
            update={"endpoints": [*document.endpoints, endpoint]}
        )

    return change


def _leftovers(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.iterdir() if p.name != "endpoints.yaml")


def test_document_is_the_validated_content(tmp_path: Path) -> None:
    provider = YamlConfigProvider(_write(tmp_path, VALID))
    document = provider.document()
    assert [e.id for e in document.endpoints] == ["team-php"]
    assert document.endpoints[0].token == TOKEN


def test_document_of_a_missing_file_is_unavailable(tmp_path: Path) -> None:
    provider = YamlConfigProvider(tmp_path / "absent.yaml")
    with pytest.raises(ConfigUnavailableError, match="cannot read"):
        provider.document()


def test_document_of_an_invalid_file(tmp_path: Path) -> None:
    provider = YamlConfigProvider(_write(tmp_path, "version: 2\n"))
    with pytest.raises(ConfigValidationError):
        provider.document()


def test_update_writes_a_valid_file_and_returns_the_revision(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    revision = provider.update(_add_endpoint("other"))
    assert revision == provider.revision()
    assert revision == hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot = provider.load()
    assert [e.id for e in snapshot.endpoints] == ["team-php", "other"]
    assert path.read_text(encoding="utf-8").startswith(HEADER)
    assert _leftovers(tmp_path) == []


def test_update_keeps_a_hand_edit(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    path.write_text(VALID.replace("PHP team", "Hand edited"), encoding="utf-8")
    provider.update(_add_endpoint("other"))
    assert provider.document().endpoints[0].name == "Hand edited"


def test_update_of_a_missing_file_creates_it_with_mode_600(tmp_path: Path) -> None:
    path = tmp_path / "endpoints.yaml"
    provider = YamlConfigProvider(path)
    provider.update(_add_endpoint("other"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [e.id for e in provider.document().endpoints] == ["other"]


def test_update_keeps_the_file_mode(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    path.chmod(0o640)
    YamlConfigProvider(path).update(_add_endpoint("other"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_update_writes_through_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.yaml"
    real.write_text(VALID, encoding="utf-8")
    link = tmp_path / "endpoints.yaml"
    link.symlink_to(real)
    YamlConfigProvider(link).update(_add_endpoint("other"))
    assert link.is_symlink()
    assert "other" in real.read_text(encoding="utf-8")


def test_update_refuses_an_invalid_file_and_leaves_it_alone(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 2\n")
    with pytest.raises(ConfigValidationError):
        YamlConfigProvider(path).update(_add_endpoint("other"))
    assert path.read_text(encoding="utf-8") == "version: 2\n"
    assert _leftovers(tmp_path) == []


def test_update_validates_the_result_before_writing(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    before = path.read_bytes()
    with pytest.raises(ConfigValidationError, match="same token as endpoint"):
        YamlConfigProvider(path).update(_add_endpoint("other", token=TOKEN))
    with pytest.raises(ConfigValidationError, match="duplicate endpoint id"):
        YamlConfigProvider(path).update(_add_endpoint("team-php"))
    assert path.read_bytes() == before
    assert _leftovers(tmp_path) == []


def test_a_change_error_reaches_the_caller_and_writes_nothing(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    before = path.read_bytes()

    def change(_: ConfigDocument) -> ConfigDocument:
        msg = "nope"
        raise ConfigError(msg)

    with pytest.raises(ConfigError, match="nope"):
        YamlConfigProvider(path).update(change)
    assert path.read_bytes() == before
    assert _leftovers(tmp_path) == []


def test_replace_works_on_an_invalid_and_on_a_missing_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 2\n")
    provider = YamlConfigProvider(path)
    document = ConfigDocument(
        version=1, endpoints=[EndpointConfig(id="x", name="X", token=TOKEN)]
    )
    assert provider.replace(document) == provider.revision()
    assert [e.id for e in provider.document().endpoints] == ["x"]
    path.unlink()
    provider.replace(document)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [e.id for e in provider.document().endpoints] == ["x"]


def test_replace_removes_what_the_document_does_not_hold(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    provider.replace(ConfigDocument(version=1, endpoints=[]))
    assert provider.document().endpoints == []
    assert "endpoints: []" in path.read_text(encoding="utf-8")


def test_a_read_only_directory_is_one_unavailable_line(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    folder = tmp_path / "ro"
    folder.mkdir()
    path = folder / "endpoints.yaml"
    path.write_text(VALID, encoding="utf-8")
    folder.chmod(0o500)
    try:
        with pytest.raises(ConfigUnavailableError) as excinfo:
            YamlConfigProvider(path).update(_add_endpoint("other"))
    finally:
        folder.chmod(0o700)
    assert "cannot write" in str(excinfo.value)
    assert "\n" not in str(excinfo.value)
    assert path.read_text(encoding="utf-8") == VALID


def test_a_failed_replace_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, VALID)

    def boom(*_: object) -> None:
        raise OSError(errno.EBUSY, "busy")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(ConfigUnavailableError, match="cannot write: OSError"):
        YamlConfigProvider(path).update(_add_endpoint("other"))
    assert path.read_text(encoding="utf-8") == VALID
    assert _leftovers(tmp_path) == []


def test_a_change_between_read_and_rename_starts_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    original = provider._unchanged
    calls: list[int] = []

    def racing(expected: bytes | None) -> bool:
        calls.append(1)
        if len(calls) == 1:  # a hand edit lands after our read
            path.write_text(VALID.replace("PHP team", "Edited"), encoding="utf-8")
        return original(expected)

    monkeypatch.setattr(provider, "_unchanged", racing)
    provider.update(_add_endpoint("other"))
    assert len(calls) == 2
    document = provider.document()
    assert document.endpoints[0].name == "Edited"
    assert [e.id for e in document.endpoints] == ["team-php", "other"]


def test_endless_conflicts_give_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, VALID)
    provider = YamlConfigProvider(path)
    monkeypatch.setattr(provider, "_unchanged", lambda _: False)
    with pytest.raises(ConfigConflictError, match="gave up after 5 attempts"):
        provider.update(_add_endpoint("other"))
    assert path.read_text(encoding="utf-8") == VALID
    assert _leftovers(tmp_path) == []


def test_a_file_created_during_an_update_of_a_missing_file_is_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "endpoints.yaml"
    provider = YamlConfigProvider(path)
    original = provider._unchanged
    calls: list[int] = []

    def racing(expected: bytes | None) -> bool:
        calls.append(1)
        if len(calls) == 1:
            path.write_text(VALID, encoding="utf-8")
        return original(expected)

    monkeypatch.setattr(provider, "_unchanged", racing)
    provider.update(_add_endpoint("other"))
    assert [e.id for e in provider.document().endpoints] == ["team-php", "other"]


TRICKY = [
    ("dash-token", {"token": "-" + "a" * 42}),
    ("digit-token", {"token": "1" * 43}),
    ("digit-id", {"id": "123"}),
    ("name-colon", {"name": "A: B"}),
    ("name-hash", {"name": "A #1"}),
    ("name-quote", {"name": 'it\'s "x"'}),
    ("name-unicode", {"name": "Équipe PHP ☃"}),
    ("name-yes", {"name": "yes"}),
    ("name-null", {"name": "null"}),
    ("min-cvss", {"min_cvss": 7.5, "kev_only": True, "enabled": False}),
]


@pytest.mark.parametrize(
    "overrides", [t[1] for t in TRICKY], ids=[t[0] for t in TRICKY]
)
def test_dump_round_trips(tmp_path: Path, overrides: dict[str, Any]) -> None:
    values: dict[str, Any] = {"id": "x", "name": "X", "token": TOKEN}
    values.update(overrides)
    values["products"] = [
        "php >= 8.10",
        "123",
        {"name": "gitlab", "min_version": "8.10", "aliases": ["gl"], "exclude": ["ce"]},
        {"name": "node", "match_description": False},
    ]
    document = ConfigDocument(version=1, endpoints=[EndpointConfig(**values)])
    path = tmp_path / "endpoints.yaml"
    YamlConfigProvider(path).replace(document)
    assert YamlConfigProvider(path).document() == document


def test_dump_omits_defaults_and_keeps_the_required_fields() -> None:
    document = ConfigDocument(
        version=1, endpoints=[EndpointConfig(id="x", name="X", token=TOKEN)]
    )
    text = dump_document(document)
    assert (
        text
        == f"{HEADER}version: 1\nendpoints:\n- id: x\n  name: X\n  token: {TOKEN}\n"
    )


def test_a_comment_in_the_previous_file_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, "# my notes\n" + VALID)
    provider = YamlConfigProvider(path)
    provider.update(_add_endpoint("other"))
    assert provider.dropped_comments is True
    assert "my notes" not in path.read_text(encoding="utf-8")
    provider.update(_add_endpoint("third", token="v" * 43))  # header only now
    assert provider.dropped_comments is False


def test_an_inline_comment_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID.replace("PHP team", "PHP team # the team"))
    provider = YamlConfigProvider(path)
    provider.update(_add_endpoint("other"))
    assert provider.dropped_comments is True


def test_a_hash_inside_a_value_is_not_a_comment(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID.replace("PHP team", "'PHP team #1'"))
    provider = YamlConfigProvider(path)
    provider.update(_add_endpoint("other"))
    assert provider.dropped_comments is False
    # The rewritten file quotes the name again: still no comment to report.
    assert "#1" in path.read_text(encoding="utf-8")
    provider.update(_add_endpoint("third", token="v" * 43))
    assert provider.dropped_comments is False


def test_a_hash_in_a_block_scalar_is_not_a_comment(tmp_path: Path) -> None:
    content = VALID.replace("name: PHP team", "name: |-\n      PHP # team")
    path = _write(tmp_path, content)
    provider = YamlConfigProvider(path)
    provider.update(_add_endpoint("other"))
    assert provider.dropped_comments is False


def test_replace_reports_dropped_comments(tmp_path: Path) -> None:
    path = _write(tmp_path, "# notes\n" + VALID)
    provider = YamlConfigProvider(path)
    provider.replace(ConfigDocument(version=1, endpoints=[]))
    assert provider.dropped_comments is True
