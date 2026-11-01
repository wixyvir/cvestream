from datetime import UTC, datetime
from types import MappingProxyType

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.config.provider import ConfigSnapshot
from cvestream.domain.endpoint import Endpoint, ProductEntry
from cvestream.pipeline.render import RenderedFeed, etag_of, render_feed

NOW = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
CHANGED = datetime(2026, 10, 26, 18, 30, 15, 123456, tzinfo=UTC)
TOKEN_PHP = "p" * 43
TOKEN_GO = "g" * 43
TOKEN_OFF = "o" * 43
PHP = Endpoint("team-php", "PHP team", (ProductEntry("php"),))
GO = Endpoint("team-go", "Go team", (ProductEntry("go"),))
OFF = Endpoint("off", "Off", (ProductEntry("php"),), enabled=False)


def _snapshot() -> ConfigSnapshot:
    return ConfigSnapshot(
        revision="r",
        endpoints=(PHP, GO, OFF),
        tokens=MappingProxyType(
            {TOKEN_PHP: "team-php", TOKEN_GO: "team-go", TOKEN_OFF: "off"}
        ),
    )


def _feed(endpoint: Endpoint, *, items: int = 3) -> RenderedFeed:
    xml = render_feed(endpoint, ())
    return RenderedFeed(endpoint.id, xml, etag_of(xml), items, NOW, CHANGED)


def test_a_stored_feed_is_served_as_it_was_rendered() -> None:
    feed = _feed(PHP)
    cache = FeedCache.build(_snapshot(), {"team-php": feed}, NOW)
    cached = cache.get(TOKEN_PHP)
    assert cached is not None
    assert cached.endpoint_id == "team-php"
    assert cached.body == feed.xml
    assert cached.etag == feed.etag
    assert cached.item_count == 3
    assert cached.rendered_at == NOW
    assert cached.changed_at == CHANGED
    assert cached.last_modified == "Mon, 26 Oct 2026 18:30:15 GMT"
    assert cache.by_endpoint["team-php"] is cached


def test_an_endpoint_without_a_stored_feed_gets_an_empty_channel() -> None:
    cache = FeedCache.build(_snapshot(), {}, NOW)
    cached = cache.get(TOKEN_GO)
    assert cached is not None
    assert cached.body == render_feed(GO, ())
    assert cached.etag == etag_of(cached.body)
    assert cached.item_count == 0
    assert cached.rendered_at is None
    assert cached.changed_at == NOW
    assert cached.last_modified == "Mon, 26 Oct 2026 20:00:00 GMT"


def test_a_disabled_endpoint_has_no_entry() -> None:
    cache = FeedCache.build(_snapshot(), {"off": _feed(OFF)}, NOW)
    assert cache.get(TOKEN_OFF) is None
    assert set(cache.by_endpoint) == {"team-php", "team-go"}
    assert set(cache.by_token) == {TOKEN_PHP, TOKEN_GO}


def test_an_unknown_token_has_no_entry() -> None:
    assert FeedCache.build(_snapshot(), {}, NOW).get("x" * 43) is None


def test_the_stored_feed_of_a_removed_endpoint_is_ignored() -> None:
    cache = FeedCache.build(_snapshot(), {"gone": _feed(PHP)}, NOW)
    assert "gone" not in cache.by_endpoint


def test_the_maps_are_read_only() -> None:
    cache = FeedCache.build(_snapshot(), {}, NOW)
    assert isinstance(cache.by_token, MappingProxyType)
    assert isinstance(cache.by_endpoint, MappingProxyType)


def test_repr_never_shows_a_token() -> None:
    cache = FeedCache.build(_snapshot(), {}, NOW)
    assert TOKEN_PHP not in repr(cache)
    assert TOKEN_GO not in repr(cache)


def test_the_holder_starts_empty_and_swaps_by_reference() -> None:
    holder = FeedCacheHolder()
    assert holder.current.get(TOKEN_PHP) is None
    first = FeedCache.build(_snapshot(), {}, NOW)
    holder.current = first
    assert holder.current is first
    held = holder.current
    holder.current = FeedCache.build(_snapshot(), {"team-php": _feed(PHP)}, NOW)
    # a reader holding the old cache keeps a consistent view
    assert held.by_endpoint["team-php"].item_count == 0
    assert holder.current.by_endpoint["team-php"].item_count == 3
