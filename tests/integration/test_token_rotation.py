"""Rotating a token with the CLI: the old feed URL stops serving after a reload.

No server: the YAML provider, the daemon's own reload and feed cache, and the
public app driven through ASGI.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from cvestream.app.feed_cache import FeedCache, FeedCacheHolder
from cvestream.app.public_api import build_public_app
from cvestream.cli.context import CliState
from cvestream.cli.main import app
from cvestream.config.yaml_provider import YamlConfigProvider
from cvestream.pipeline.run import reload_config

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
FIRST = "f" * 43
SECOND = "s" * 43


def _cli(*args: str, token: str) -> str:
    result = CliRunner().invoke(app, list(args), obj=CliState(new_token=lambda: token))
    assert result.exit_code == 0, result.stderr
    return result.stdout


@pytest.mark.anyio
async def test_a_rotated_token_stops_serving_at_the_next_reload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CVESTREAM_SETTINGS_FILE", raising=False)
    path = tmp_path / "endpoints.yaml"
    monkeypatch.setenv("CVESTREAM_ENDPOINTS_FILE", str(path))

    created = _cli("endpoint", "create", "team-php", "--name", "PHP", token=FIRST)
    assert f"/feeds/{FIRST}.xml" in created
    _cli("product", "add", "team-php", "php >= 8.2", token="x" * 43)

    # The daemon starts: snapshot, cache, public app.
    provider = YamlConfigProvider(path)
    snapshot = provider.load()
    holder = FeedCacheHolder(FeedCache.build(snapshot, {}, NOW))
    transport = httpx.ASGITransport(app=build_public_app(holder))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        assert (await http.get(f"/feeds/{FIRST}.xml")).status_code == 200

        rotated = _cli("token", "rotate", "team-php", token=SECOND)
        assert rotated == f"feed: http://localhost:8080/feeds/{SECOND}.xml\n"

        # Nothing changes until the daemon reloads (next cycle).
        assert (await http.get(f"/feeds/{FIRST}.xml")).status_code == 200
        assert (await http.get(f"/feeds/{SECOND}.xml")).status_code == 404

        # The next cycle: reload the configuration, rebuild the cache.
        reloaded = reload_config(provider, snapshot)
        assert reloaded.revision != snapshot.revision
        holder.current = FeedCache.build(reloaded, {}, NOW)

        assert (await http.get(f"/feeds/{FIRST}.xml")).status_code == 404
        new = await http.get(f"/feeds/{SECOND}.xml")
        assert new.status_code == 200
        assert b"<rss" in new.content
