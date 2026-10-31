"""Builds the enabled source adapters from settings (FR-4).

The caller owns the `HttpClient` lifetime and passes it in; the registry never
opens or closes a client.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from cvestream.clock import Clock
from cvestream.http import HttpClient
from cvestream.settings import SourceSettings, SourcesSettings
from cvestream.sources.base import SourceAdapter
from cvestream.sources.cisa_kev import CisaKevAdapter
from cvestream.sources.euvd import EuvdAdapter

# A factory gets every source's settings and reads its own entry.
type AdapterFactory = Callable[[HttpClient, Clock, SourcesSettings], SourceAdapter]

# One factory per `SourcesSettings` field; a unit test keeps the two in step.
ADAPTER_FACTORIES: dict[str, AdapterFactory] = {
    "euvd": lambda http, clock, settings: EuvdAdapter(
        http, clock, page_pause=settings.euvd.page_pause.total_seconds()
    ),
    "cisa_kev": lambda http, clock, _settings: CisaKevAdapter(http, clock),
}


@dataclass(frozen=True, slots=True)
class ScheduledSource:
    adapter: SourceAdapter
    interval: timedelta


def build_sources(
    settings: SourcesSettings, http: HttpClient, clock: Clock
) -> tuple[ScheduledSource, ...]:
    """Enabled adapters in settings order, each with its own interval."""
    scheduled: list[ScheduledSource] = []
    for name in SourcesSettings.model_fields:
        source_settings: SourceSettings = getattr(settings, name)
        if not source_settings.enabled:
            continue
        try:
            factory = ADAPTER_FACTORIES[name]
        except KeyError:
            msg = f"no adapter factory for the settings source {name!r}"
            raise LookupError(msg) from None
        scheduled.append(
            ScheduledSource(factory(http, clock, settings), source_settings.interval)
        )
    return tuple(scheduled)
