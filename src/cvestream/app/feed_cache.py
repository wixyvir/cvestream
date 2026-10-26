"""The in-memory feed cache: what the public listener serves.

Built from a config snapshot and the rendered feeds, then swapped into the
`FeedCacheHolder` by replacing the whole reference. A cache is never mutated, so
a handler that read `holder.current` keeps a consistent view. This is not the
`feed_cache` table: that one is the persisted copy the cache is loaded from.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from types import MappingProxyType

from cvestream.config.provider import ConfigSnapshot
from cvestream.pipeline.render import RenderedFeed, etag_of, render_feed


@dataclass(frozen=True, slots=True)
class CachedFeed:
    endpoint_id: str
    body: bytes
    etag: str
    # RFC 822, from `changed_at`.
    last_modified: str
    changed_at: datetime
    item_count: int
    # `None`: an empty placeholder that no run has rendered yet.
    rendered_at: datetime | None


def _cached(feed: RenderedFeed, rendered_at: datetime | None) -> CachedFeed:
    return CachedFeed(
        endpoint_id=feed.endpoint_id,
        body=feed.xml,
        etag=feed.etag,
        last_modified=format_datetime(feed.changed_at.astimezone(UTC), usegmt=True),
        changed_at=feed.changed_at,
        item_count=feed.item_count,
        rendered_at=rendered_at,
    )


@dataclass(frozen=True, slots=True)
class FeedCache:
    # Tokens are keys: `repr=False` keeps them out of logs and tracebacks.
    by_token: Mapping[str, CachedFeed] = field(repr=False)
    by_endpoint: Mapping[str, CachedFeed]

    @classmethod
    def build(
        cls,
        snapshot: ConfigSnapshot,
        feeds: Mapping[str, RenderedFeed],
        now: datetime,
    ) -> FeedCache:
        """One entry per enabled endpoint; an endpoint no run has rendered yet
        gets an empty channel until its first run."""
        endpoints = {e.id: e for e in snapshot.endpoints if e.enabled}
        by_endpoint: dict[str, CachedFeed] = {}
        for endpoint in endpoints.values():
            stored = feeds.get(endpoint.id)
            if stored is not None:
                by_endpoint[endpoint.id] = _cached(stored, stored.rendered_at)
                continue
            xml = render_feed(endpoint, ())
            by_endpoint[endpoint.id] = _cached(
                RenderedFeed(endpoint.id, xml, etag_of(xml), 0, now, now), None
            )
        by_token = {
            token: by_endpoint[endpoint_id]
            for token, endpoint_id in snapshot.tokens.items()
            if endpoint_id in by_endpoint
        }
        return cls(
            by_token=MappingProxyType(by_token),
            by_endpoint=MappingProxyType(by_endpoint),
        )

    def get(self, token: str) -> CachedFeed | None:
        return self.by_token.get(token)


class FeedCacheHolder:
    """The single mutable reference: `current` is replaced, never mutated."""

    def __init__(self, current: FeedCache | None = None) -> None:
        self.current: FeedCache = current or FeedCache(
            by_token=MappingProxyType({}), by_endpoint=MappingProxyType({})
        )
