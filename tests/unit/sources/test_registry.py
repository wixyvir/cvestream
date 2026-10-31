import dataclasses
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from cvestream.http import HttpClient
from cvestream.settings import SourceSettings, SourcesSettings, load_settings
from cvestream.sources.cisa_kev import CisaKevAdapter
from cvestream.sources.euvd import EuvdAdapter
from cvestream.sources.registry import (
    ADAPTER_FACTORIES,
    ScheduledSource,
    build_sources,
)

if TYPE_CHECKING:
    from tests.conftest import FakeClock


def test_both_sources_enabled_in_settings_order_with_their_own_interval(
    fake_clock: FakeClock,
) -> None:
    settings = load_settings(
        {
            "CVESTREAM_SOURCES__EUVD__INTERVAL": "PT30M",
            "CVESTREAM_SOURCES__CISA_KEV__INTERVAL": "PT10M",
        }
    ).sources
    sources = build_sources(settings, HttpClient(), fake_clock)
    assert [s.adapter.name for s in sources] == ["euvd", "cisa_kev"]
    assert [s.interval for s in sources] == [
        timedelta(minutes=30),
        timedelta(minutes=10),
    ]
    assert isinstance(sources[0].adapter, EuvdAdapter)
    assert isinstance(sources[1].adapter, CisaKevAdapter)


def test_defaults_enable_both_sources_every_fifteen_minutes(
    fake_clock: FakeClock,
) -> None:
    sources = build_sources(SourcesSettings(), HttpClient(), fake_clock)
    assert [s.adapter.name for s in sources] == ["euvd", "cisa_kev"]
    assert {s.interval for s in sources} == {timedelta(minutes=15)}


def test_a_disabled_source_is_absent(fake_clock: FakeClock) -> None:
    settings = SourcesSettings(euvd=SourceSettings(enabled=False))
    sources = build_sources(settings, HttpClient(), fake_clock)
    assert [s.adapter.name for s in sources] == ["cisa_kev"]


def test_all_sources_disabled_gives_no_adapters(fake_clock: FakeClock) -> None:
    settings = SourcesSettings(
        euvd=SourceSettings(enabled=False), cisa_kev=SourceSettings(enabled=False)
    )
    assert build_sources(settings, HttpClient(), fake_clock) == ()


def test_every_settings_source_has_a_factory() -> None:
    assert set(SourcesSettings.model_fields) <= set(ADAPTER_FACTORIES)
    assert set(ADAPTER_FACTORIES) == set(SourcesSettings.model_fields)


def test_adapter_name_equals_the_settings_key(fake_clock: FakeClock) -> None:
    http = HttpClient()
    for key, factory in ADAPTER_FACTORIES.items():
        assert factory(http, fake_clock).name == key


def test_a_settings_field_without_a_factory_is_an_error(
    fake_clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(ADAPTER_FACTORIES, "cisa_kev")
    with pytest.raises(LookupError, match="cisa_kev"):
        build_sources(SourcesSettings(), HttpClient(), fake_clock)


def test_adapters_share_the_given_http_client(fake_clock: FakeClock) -> None:
    http = HttpClient()
    sources = build_sources(SourcesSettings(), http, fake_clock)
    for source in sources:
        assert getattr(source.adapter, "_http") is http  # noqa: B009


def test_scheduled_source_is_frozen(fake_clock: FakeClock) -> None:
    source = build_sources(SourcesSettings(), HttpClient(), fake_clock)[0]
    assert isinstance(source, ScheduledSource)
    field = "interval"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(source, field, timedelta(minutes=1))
