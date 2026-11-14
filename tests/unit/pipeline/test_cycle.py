from collections.abc import Mapping, MutableMapping
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from structlog.testing import capture_logs

from cvestream.config.provider import (
    ConfigSnapshot,
    ConfigUnavailableError,
    ConfigValidationError,
)
from cvestream.config.yaml_provider import YamlConfigProvider
from cvestream.domain.model import AffectedProduct, CvssScore, Sourced, SourceRecord
from cvestream.pipeline.run import SourceOutcome, cycle, reload_config
from cvestream.sources.base import FetchResult
from cvestream.state.db import State
from tests.conftest import FakeClock

TOKEN_A = "a" * 43
TOKEN_B = "b" * 43
TOKEN_SECRET = "s3cr3t" + "x" * 37


def _yaml(*endpoints: tuple[str, str, str], min_cvss: float | None = None) -> str:
    lines = ["version: 1", "endpoints:"]
    for endpoint_id, token, product in endpoints:
        lines += [
            f"  - id: {endpoint_id}",
            f"    name: {endpoint_id}",
            f"    token: {token}",
        ]
        if min_cvss is not None:
            lines.append(f"    min_cvss: {min_cvss}")
        lines += ["    products:", f"      - {product}"]
    return "\n".join(lines) + "\n"


@pytest.fixture
def endpoints_file(tmp_path: Path) -> Path:
    path = tmp_path / "endpoints.yaml"
    path.write_text(_yaml(("team-php", TOKEN_A, "php")), encoding="utf-8")
    return path


@pytest.fixture
def provider(endpoints_file: Path) -> YamlConfigProvider:
    return YamlConfigProvider(endpoints_file)


@pytest.fixture
def snapshot(provider: YamlConfigProvider) -> ConfigSnapshot:
    return provider.load()


def _record(clock: FakeClock, *, score: float | None = None) -> SourceRecord:
    now = clock.now()
    return SourceRecord(
        source="euvd",
        record_id="EUVD-2026-1",
        observed_at=now,
        aliases=("CVE-2026-1001",),
        description=Sourced("A flaw", "euvd", now),
        scores=(Sourced(CvssScore(score) if score is not None else None, "euvd", now),),
        affected=(Sourced(AffectedProduct("php"), "euvd", now),),
        updated_at=now - timedelta(hours=1),
    )


def _ok(clock: FakeClock, *, score: float | None = None) -> SourceOutcome:
    return SourceOutcome("euvd", FetchResult((_record(clock, score=score),), "c1"))


def _failed() -> SourceOutcome:
    return SourceOutcome("cisa_kev", None, "SourceTransportError")


def _events(logs: list[MutableMapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == name]


def test_the_same_revision_returns_the_same_snapshot(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot
) -> None:
    with capture_logs() as logs:
        assert reload_config(provider, snapshot) is snapshot
    assert logs == []


def test_a_valid_change_is_loaded_and_logged(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot, endpoints_file: Path
) -> None:
    endpoints_file.write_text(
        _yaml(("team-php", TOKEN_A, "php"), ("team-go", TOKEN_B, "go")),
        encoding="utf-8",
    )
    with capture_logs() as logs:
        reloaded = reload_config(provider, snapshot)
    assert reloaded is not snapshot
    assert [e.id for e in reloaded.endpoints] == ["team-php", "team-go"]
    (entry,) = _events(logs, "config.reloaded")
    assert entry["count"] == 2
    assert entry["log_level"] == "info"


def test_the_reloaded_event_carries_the_revision(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot, endpoints_file: Path
) -> None:
    endpoints_file.write_text(_yaml(("team-php", TOKEN_B, "php")), encoding="utf-8")
    with capture_logs() as logs:
        reloaded = reload_config(provider, snapshot)
    (entry,) = _events(logs, "config.reloaded")
    assert entry["revision"] == reloaded.revision != snapshot.revision


def test_a_forced_reload_of_an_unchanged_configuration_is_silent(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot
) -> None:
    with capture_logs() as logs:
        assert reload_config(provider, snapshot, force=True) is snapshot
    assert logs == []


def test_a_forced_reload_picks_up_a_change_the_revision_does_not_show(
    snapshot: ConfigSnapshot,
) -> None:
    changed = ConfigSnapshot(
        snapshot.revision,
        snapshot.endpoints,
        MappingProxyType({TOKEN_B: "team-php"}),
    )

    class ForgotToBump:
        def revision(self) -> str:
            return snapshot.revision

        def load(self) -> ConfigSnapshot:
            return changed

    with capture_logs() as logs:
        assert reload_config(ForgotToBump(), snapshot) is snapshot
        assert reload_config(ForgotToBump(), snapshot, force=True) is changed
    (entry,) = _events(logs, "config.reloaded")
    assert entry["revision"] == snapshot.revision


def test_a_forced_reload_keeps_the_snapshot_when_the_store_is_down(
    snapshot: ConfigSnapshot,
) -> None:
    class Down:
        def revision(self) -> str:
            return snapshot.revision

        def load(self) -> ConfigSnapshot:
            raise ConfigUnavailableError("down")

    with capture_logs() as logs:
        assert reload_config(Down(), snapshot, force=True) is snapshot
    assert len(_events(logs, "config.unavailable")) == 1


def test_an_outage_logs_one_warning_per_cycle_and_keeps_the_snapshot(
    snapshot: ConfigSnapshot,
) -> None:
    class Down:
        def revision(self) -> str:
            raise ConfigUnavailableError("down")

        def load(self) -> ConfigSnapshot:
            raise AssertionError

    with capture_logs() as logs:
        for _ in range(3):
            assert reload_config(Down(), snapshot) is snapshot
    assert len(_events(logs, "config.unavailable")) == 3


def test_a_rotated_token_serves_the_new_one_only(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot, endpoints_file: Path
) -> None:
    endpoints_file.write_text(_yaml(("team-php", TOKEN_B, "php")), encoding="utf-8")
    reloaded = reload_config(provider, snapshot)
    assert reloaded.endpoint_for_token(TOKEN_B) is not None
    assert reloaded.endpoint_for_token(TOKEN_A) is None


def test_an_invalid_change_keeps_the_last_valid_snapshot(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot, endpoints_file: Path
) -> None:
    endpoints_file.write_text(
        _yaml(("Team PHP", TOKEN_SECRET, "php")), encoding="utf-8"
    )
    with capture_logs() as logs:
        assert reload_config(provider, snapshot) is snapshot
    (entry,) = _events(logs, "config.rejected")
    assert entry["log_level"] == "warning"
    errors = entry["errors"]
    assert isinstance(errors, list)
    assert errors
    assert TOKEN_SECRET not in str(logs)
    assert not _events(logs, "config.reloaded")


def test_broken_yaml_keeps_the_last_valid_snapshot(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot, endpoints_file: Path
) -> None:
    endpoints_file.write_text("version: [1\n", encoding="utf-8")
    with capture_logs() as logs:
        assert reload_config(provider, snapshot) is snapshot
    assert len(_events(logs, "config.rejected")) == 1


def test_a_deleted_file_keeps_the_last_valid_snapshot(
    provider: YamlConfigProvider, snapshot: ConfigSnapshot, endpoints_file: Path
) -> None:
    endpoints_file.unlink()
    with capture_logs() as logs:
        assert reload_config(provider, snapshot) is snapshot
    (entry,) = _events(logs, "config.unavailable")
    assert entry["log_level"] == "warning"
    assert entry["error_type"] == "ConfigUnavailableError"


def test_a_file_that_vanishes_between_revision_and_load(
    snapshot: ConfigSnapshot,
) -> None:
    class Flaky:
        def revision(self) -> str:
            return "other"

        def load(self) -> ConfigSnapshot:
            raise ConfigUnavailableError("gone")

    with capture_logs() as logs:
        assert reload_config(Flaky(), snapshot) is snapshot
    assert len(_events(logs, "config.unavailable")) == 1


def test_a_rejected_snapshot_logs_the_field_errors_only(
    snapshot: ConfigSnapshot,
) -> None:
    class Rejecting:
        def revision(self) -> str:
            return "other"

        def load(self) -> ConfigSnapshot:
            raise ConfigValidationError("endpoints.yaml", ["endpoints[0].id: bad"])

    with capture_logs() as logs:
        assert reload_config(Rejecting(), snapshot) is snapshot
    (entry,) = _events(logs, "config.rejected")
    assert entry["errors"] == ["endpoints[0].id: bad"]


def test_a_cycle_logs_exactly_one_summary_with_a_failed_source(
    state: State,
    fake_clock: FakeClock,
    provider: YamlConfigProvider,
    snapshot: ConfigSnapshot,
) -> None:
    with capture_logs() as logs:
        result = cycle(
            state, provider, snapshot, [_ok(fake_clock), _failed()], fake_clock
        )
    (summary,) = _events(logs, "cycle.summary")
    assert summary["sources"] == {
        "euvd": {"result": "ok", "records": 1, "skipped": 0},
        "cisa_kev": {"result": "failed", "error_type": "SourceTransportError"},
    }
    assert summary["published"] == {"team-php": 1}
    assert summary["score_unknown"] == 0
    assert summary["versions_unknown"] == 0
    assert summary["count"] == 1
    assert isinstance(summary["duration_ms"], int)
    assert result.snapshot is snapshot
    assert len(result.run.decisions) == 1


def test_published_zero_is_reported_for_every_enabled_endpoint(
    state: State,
    fake_clock: FakeClock,
    provider: YamlConfigProvider,
    snapshot: ConfigSnapshot,
) -> None:
    with capture_logs() as logs:
        cycle(state, provider, snapshot, [_failed()], fake_clock)
    (summary,) = _events(logs, "cycle.summary")
    assert summary["published"] == {"team-php": 0}
    assert summary["count"] == 0


def test_a_failed_outcome_does_not_touch_the_stored_cursor(
    state: State,
    fake_clock: FakeClock,
    provider: YamlConfigProvider,
    snapshot: ConfigSnapshot,
) -> None:
    cycle(state, provider, snapshot, [_ok(fake_clock), _failed()], fake_clock)
    with state.transaction() as repository:
        assert repository.cursors() == {"euvd": "c1"}
    cycle(state, provider, snapshot, [_failed()], fake_clock)
    with state.transaction() as repository:
        assert repository.cursors() == {"euvd": "c1"}


def test_pass_throughs_are_counted_in_the_summary(
    state: State, fake_clock: FakeClock, tmp_path: Path
) -> None:
    path = tmp_path / "scored.yaml"
    path.write_text(_yaml(("team-php", TOKEN_A, "php"), min_cvss=7.0), encoding="utf-8")
    provider = YamlConfigProvider(path)
    with capture_logs() as logs:
        cycle(state, provider, provider.load(), [_ok(fake_clock)], fake_clock)
    (summary,) = _events(logs, "cycle.summary")
    assert summary["score_unknown"] == 1
    assert summary["published"] == {"team-php": 1}


def test_a_reloaded_endpoint_applies_to_the_same_cycle(
    state: State,
    fake_clock: FakeClock,
    provider: YamlConfigProvider,
    snapshot: ConfigSnapshot,
    endpoints_file: Path,
) -> None:
    endpoints_file.write_text(
        _yaml(("team-php", TOKEN_A, "php"), ("team-other", TOKEN_B, "php")),
        encoding="utf-8",
    )
    result = cycle(state, provider, snapshot, [_ok(fake_clock)], fake_clock)
    assert result.snapshot is not snapshot
    assert set(result.run.feeds) == {"team-php", "team-other"}
    assert {d.endpoint_id for d in result.run.decisions} == {"team-php", "team-other"}


def test_a_run_failure_propagates_without_a_summary(
    state: State,
    fake_clock: FakeClock,
    provider: YamlConfigProvider,
    snapshot: ConfigSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("cvestream.pipeline.run.run", boom)
    with capture_logs() as logs, pytest.raises(RuntimeError, match="disk full"):
        cycle(state, provider, snapshot, [_ok(fake_clock)], fake_clock)
    assert not _events(logs, "cycle.summary")
