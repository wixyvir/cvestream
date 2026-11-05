"""`cvestream config export` and `config import` on the YAML provider."""

import stat
from pathlib import Path

import pytest

from cvestream.config.yaml_provider import HEADER, YamlConfigProvider

from .conftest import Cli

TOKEN_A, TOKEN_B, TOKEN_C = "a" * 43, "b" * 43, "c" * 43
# Not sorted, with comments and a full entry: what an operator writes by hand.
HAND = f"""\
# my endpoints
version: 1
endpoints:
  - id: zeta
    name: Zeta
    token: {TOKEN_B}
    min_cvss: 7.5
    kev_only: true
    products:
      - php >= 8.2
      - name: GitLab
        aliases: [gl]
        min_version: "16.0"
  - id: alpha  # first by id
    name: Alpha
    token: {TOKEN_A}
    enabled: false
"""


@pytest.fixture
def seeded(cli: Cli) -> Cli:
    cli.path.write_text(HAND, encoding="utf-8")
    return cli


def _other(cli: Cli, content: str, name: str = "other.yaml") -> Path:
    path = cli.path.parent / name
    path.write_text(content, encoding="utf-8")
    return path


# --- export --------------------------------------------------------------------


def test_export_to_stdout_is_valid_sorted_and_complete(seeded: Cli) -> None:
    result = seeded.run("config", "export")
    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.startswith(HEADER)
    target = _other(seeded, result.stdout)
    assert seeded.run("config", "validate", str(target)).exit_code == 0
    document = YamlConfigProvider(target).document()
    assert [e.id for e in document.endpoints] == ["alpha", "zeta"]
    assert document.endpoints[1].token == TOKEN_B  # tokens are included
    assert document == YamlConfigProvider(seeded.path).document().model_copy(
        update={"endpoints": sorted(document.endpoints, key=lambda e: e.id)}
    )


def test_export_is_deterministic(seeded: Cli) -> None:
    first = seeded.run("config", "export").stdout
    assert seeded.run("config", "export").stdout == first


def test_export_to_a_new_file_has_mode_600(seeded: Cli, tmp_path: Path) -> None:
    target = tmp_path / "backup.yaml"
    result = seeded.run("config", "export", "--output", str(target))
    assert result.exit_code == 0
    assert result.stdout.strip() == f"{target}: configuration written"
    assert TOKEN_A not in result.stdout
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text(encoding="utf-8") == seeded.run("config", "export").stdout


def test_export_refuses_an_existing_file(seeded: Cli, tmp_path: Path) -> None:
    target = tmp_path / "backup.yaml"
    target.write_text("keep me", encoding="utf-8")
    result = seeded.run("config", "export", "-o", str(target))
    assert result.exit_code == 2
    assert seeded.stderr(result).strip() == f"{target}: already exists"
    assert target.read_text(encoding="utf-8") == "keep me"


def test_export_to_an_unwritable_place_is_one_line(seeded: Cli, tmp_path: Path) -> None:
    result = seeded.run("config", "export", "-o", str(tmp_path / "absent" / "x.yaml"))
    assert result.exit_code == 2
    assert "cannot write" in seeded.stderr(result)
    assert len(seeded.stderr(result).strip().splitlines()) == 1


def test_export_of_an_invalid_file_is_a_user_error(cli: Cli) -> None:
    cli.path.write_text("version: 2\n", encoding="utf-8")
    result = cli.run("config", "export")
    assert result.exit_code == 2
    assert result.stdout == ""


def test_export_does_not_touch_the_source(seeded: Cli) -> None:
    seeded.run("config", "export")
    assert seeded.path.read_text(encoding="utf-8") == HAND


# --- import --------------------------------------------------------------------


def test_import_replaces_the_whole_configuration(seeded: Cli) -> None:
    source = _other(
        seeded,
        "version: 1\nendpoints:\n"
        f"  - id: alpha\n    name: Renamed\n    token: {TOKEN_A}\n"
        f"  - id: new\n    name: New\n    token: {TOKEN_C}\n",
    )
    result = seeded.run("config", "import", str(source))
    assert result.exit_code == 0
    assert result.stdout.strip().startswith(
        "imported 2 endpoints (added 1, updated 1, removed 1), revision "
    )
    assert TOKEN_A not in result.stdout + seeded.stderr(result)
    document = YamlConfigProvider(seeded.path).document()
    assert [(e.id, e.name) for e in document.endpoints] == [
        ("alpha", "Renamed"),
        ("new", "New"),
    ]  # `zeta` is gone: import replaces


def test_import_summary_counts_unchanged_endpoints_as_neither(seeded: Cli) -> None:
    exported = seeded.run("config", "export").stdout
    result = seeded.run("config", "import", str(_other(seeded, exported)))
    assert "(added 0, updated 0, removed 0)" in result.stdout


def test_export_import_export_round_trip_between_two_files(
    seeded: Cli, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = seeded.run("config", "export").stdout
    backup = _other(seeded, first, "backup.yaml")
    second_file = tmp_path / "second.yaml"
    monkeypatch.setenv("CVESTREAM_ENDPOINTS_FILE", str(second_file))
    assert seeded.run("config", "import", str(backup)).exit_code == 0
    assert seeded.run("config", "export").stdout == first


def test_import_of_an_invalid_file_leaves_the_target_unchanged(seeded: Cli) -> None:
    source = _other(seeded, "version: 1\nendpoints:\n  - id: Bad Id\n")
    result = seeded.run("config", "import", str(source))
    assert result.exit_code == 2
    assert result.stdout == ""
    assert len(seeded.stderr(result).strip().splitlines()) == 1
    assert seeded.path.read_text(encoding="utf-8") == HAND


def test_import_of_a_missing_file(seeded: Cli, tmp_path: Path) -> None:
    result = seeded.run("config", "import", str(tmp_path / "nope.yaml"))
    assert result.exit_code == 2
    assert "cannot read" in seeded.stderr(result)
    assert seeded.path.read_text(encoding="utf-8") == HAND


def test_import_repairs_an_invalid_target(cli: Cli) -> None:
    cli.path.write_text("version: 2\nendpoints: nonsense\n", encoding="utf-8")
    source = _other(
        cli, f"version: 1\nendpoints:\n  - id: x\n    name: X\n    token: {TOKEN_A}\n"
    )
    result = cli.run("config", "import", str(source))
    assert result.exit_code == 0
    assert "the previous content could not be read" in result.stdout
    assert "imported 1 endpoints" in result.stdout
    assert cli.run("config", "validate").exit_code == 0


def test_import_into_a_missing_target(cli: Cli) -> None:
    source = _other(
        cli, f"version: 1\nendpoints:\n  - id: x\n    name: X\n    token: {TOKEN_A}\n"
    )
    assert cli.run("config", "import", str(source)).exit_code == 0
    assert stat.S_IMODE(cli.path.stat().st_mode) == 0o600
    assert [e.id for e in YamlConfigProvider(cli.path).document().endpoints] == ["x"]


def test_import_of_the_active_file_itself_is_refused(seeded: Cli) -> None:
    result = seeded.run("config", "import", str(seeded.path))
    assert result.exit_code == 2
    assert "nothing to import" in seeded.stderr(result)
    assert seeded.path.read_text(encoding="utf-8") == HAND


def test_import_through_another_name_for_the_active_file_is_refused(
    seeded: Cli,
) -> None:
    link = seeded.path.parent / "link.yaml"
    link.symlink_to(seeded.path)
    result = seeded.run("config", "import", str(link))
    assert result.exit_code == 2
    assert "nothing to import" in seeded.stderr(result)


def test_import_says_when_comments_were_dropped(seeded: Cli) -> None:
    source = _other(seeded, "version: 1\n")
    result = seeded.run("config", "import", str(source))
    assert "comments were not kept" in seeded.stderr(result)


def test_validate_still_has_its_one_line_output(seeded: Cli) -> None:
    result = seeded.run("config", "validate")
    assert result.stdout.strip() == f"{seeded.path}: valid (2 endpoints)"
