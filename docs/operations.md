# Operations

## TL;DR

- `cvestream run` is the daemon: it polls the sources, decides what each team gets and serves one RSS feed per endpoint on `:8080` (public). Health and status are on `:9090` (private, never publish it).
- Self-hosted: `deploy/compose/compose.yml` with your `config/endpoints.yaml`. One replica only.
- Feed URL: `http://<host>:8080/feeds/<token>.xml`. The token is the access control: put an HTTPS reverse proxy in front and **turn off its path logging for `/feeds/`**.
- Health: `cvestream healthcheck [--ready] [--port N]`, `/healthz`, `/readyz`, `/status`. The image already has a `HEALTHCHECK`.
- Logs are JSON lines on stdout. `cycle.summary` says what each cycle did; `source.failed`, `source.stale`, `config.rejected` and `pipeline.failed` are the ones to alert on.
- Edit `endpoints.yaml` and the daemon picks it up at the next cycle, no restart. Rotate a token by editing it.
- Back up the state with `cvestream db backup <file>`.

## Run

| How | Command |
|---|---|
| Locally | `uv run cvestream run` (needs `endpoints.yaml`, writes `./data/cvestream.db`) |
| One cycle, then exit | `uv run cvestream poll --once` |
| Container | `docker run -v data:/data -v ./config:/config:ro -e CVESTREAM_ENDPOINTS_FILE=/config/endpoints.yaml -p 8080:8080 ghcr.io/wixyvir/cvestream:edge` |
| Self-hosted compose | `cd deploy/compose && mkdir config && cp ../../endpoints.example.yaml config/endpoints.yaml`, edit it, then `docker compose up -d` |
| Dev stack | `docker compose watch` (serves the placeholder tokens of `endpoints.example.yaml`) |

`deploy/compose/compose.yml` runs `ghcr.io/wixyvir/cvestream`, with a read-only root filesystem (`/tmp` is a `tmpfs`), no capabilities, `no-new-privileges`, a named volume on `/data` and only port 8080 published. `config/` is gitignored: never commit it, it holds your tokens.

**Mount the configuration directory, not the file.** An editor that saves by replacing the file gives it a new inode, and a single-file bind mount keeps showing the old content: hot reload would silently stop working. The compose file mounts `./config` on `/config`, read-only.

`poll --once` is a one-shot: fetch each enabled source once, run one cycle, log one `cycle.summary` and exit `0`, also when a source failed (the failure is in the summary). Do not run it against the database of a running daemon: both would write (see Limits).

## Startup and shutdown

Startup order: logging, state migrated (`state.migrated`), private listener up (`/healthz` 200, `/readyz` 503), configuration loaded, stored feeds loaded into memory, worker and one loop per source started (`app.started`), public listener up, `/readyz` 200 (`app.ready`). The first fetch happens right away.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | clean stop (SIGTERM or SIGINT), or a successful `poll --once` |
| `1` | unexpected error, or a listener could not start or died (`app.failed`) |
| `2` | user error: invalid settings, invalid state URL or database, invalid `endpoints.yaml` (`config.rejected`) |
| `3` | `endpoints.yaml` missing or unreadable (`config.unavailable`) |

A startup failure prints one line on stderr. The traceback of an unexpected error is shown only with `--debug` (`cvestream --debug run`).

SIGTERM or SIGINT: `/readyz` turns 503 (`app.stopping`), the source loops stop, the pipeline run in progress **finishes** (it is never cancelled), both listeners stop, the HTTP client and the state close (`app.stopped`). Give the container a grace period of 60 s (`stop_grace_period: 60s`, `terminationGracePeriodSeconds: 60`).

## Health

All on the private listener (`http.private`, default `:9090`):

| Path | Answer |
|---|---|
| `/healthz` | `200 {"status": "ok"}` while the event loop responds |
| `/readyz` | `200 {"status": "ready"}` once the configuration, the state and the feed cache are loaded; `503 {"error": {"code": "not_ready", ...}}` before that and during shutdown. It does not depend on the sources: a source outage never makes the daemon unready |
| `/status` | per source and per endpoint, see below |

`cvestream healthcheck` asks `/healthz` on `127.0.0.1:9090` with the standard library only, prints nothing and exits `0` on HTTP 200, `1` otherwise (2 s timeout, no proxy). `--ready` asks `/readyz`; `--port` changes the port when you changed `http.private.port`. The runtime image runs `cvestream healthcheck` as its `HEALTHCHECK`; the image has no `curl`, so orchestrator probes use the same command (`docker compose exec cvestream cvestream healthcheck --ready`).

## `/status`

No envelope. Datetimes are ISO 8601 with `Z`. No token and no feed path appears in it.

```json
{
  "sources": {
    "euvd": {
      "interval_seconds": 900,
      "last_success_at": "2026-10-26T20:15:00Z",
      "last_error": {"type": "SourceTransportError", "at": "2026-10-26T20:00:00Z"},
      "cursor": "2026-10-26T20:10:00Z",
      "stale": false
    }
  },
  "endpoints": {
    "team-php": {"item_count": 4, "last_render_at": "2026-10-26T20:15:02Z"}
  }
}
```

- `stale` is true when the source has had no success for more than three intervals; for a source that has not succeeded yet, the time is counted from the start of the daemon. The daemon logs `source.stale` (warning) at the same moment.
- `last_success_at` and `last_error` live in memory: they are lost at restart. `last_error` keeps the last failure, even after the source recovered.
- `endpoints` lists the enabled endpoints. `last_render_at` is `null` for an endpoint that no run has rendered yet.

## Logs

One JSON object per line on stdout (`CVESTREAM_LOG__FORMAT=console` for humans). Events are named `<area>.<past-participle>`; the standard fields are `source`, `endpoint_id`, `vuln_id`, `count`, `duration_ms`, `error_type`.

| Event | Level | When | Fields |
|---|---|---|---|
| `app.started` / `app.ready` / `app.stopping` / `app.stopped` | info | lifecycle | `sources` (started) |
| `app.failed` | error | a listener could not start or died | `listener` or `component`, `error_type` |
| `state.migrated` | info | startup | `revision` |
| `source.fetched` | info | a fetch succeeded | `source`, `count`, `skipped`, `duration_ms` |
| `source.failed` | warning | a fetch failed (`SourceError`): its cursor does not advance, the other sources are unaffected | `source`, `error_type`, `message` |
| `source.stale` | warning | no success for more than 3 intervals | `source`, `last_success_at` |
| `source.crashed` | error | an unexpected exception in a source loop (with traceback); the loop restarts after 1, 2, 4... s, capped at 300 s | `source` |
| `pipeline.failed` | error | a cycle raised (with traceback): the run is rolled back, the previous feeds keep being served | `error_type` |
| `pipeline.completed` | info | a run committed | `count`, `records`, `changed`, `touched`, `duration_ms` |
| `publish.decided` | info | an item was published to an endpoint | `endpoint_id`, `vuln_id`, `kind` |
| `config.reloaded` | info | a changed `endpoints.yaml` was loaded | `count` |
| `config.rejected` | warning (error at startup) | the new content is invalid; the last valid configuration stays | `errors` (field errors, never a token) |
| `config.unavailable` | warning (error at startup) | the file cannot be read; the last valid configuration stays | `error_type` |
| `feed.served` | info | a feed request for a known token | `endpoint_id`, `method`, `status`, `duration_ms` |
| `feed.not_found` | info | any other `GET`/`HEAD` | `method` |

`cycle.summary` (info) is logged once per cycle:

```json
{"event": "cycle.summary", "sources": {"euvd": {"result": "ok", "records": 8, "skipped": 0}, "cisa_kev": {"result": "failed", "error_type": "SourceTransportError"}}, "published": {"team-php": 0, "team-go": 2}, "score_unknown": 1, "versions_unknown": 0, "count": 2, "duration_ms": 740}
```

- `sources`: each source of the cycle, `ok` with `records` (what the adapter returned) and `skipped` (records the adapter dropped), or `failed` with `error_type`.
- `published`: items published per enabled endpoint, `0` included. A permanent `0` for every endpoint is worth a look.
- `score_unknown` and `versions_unknown`: published items let through because their score or their versions are unknown (recall over volume).

A fetch outcome, failed ones included, triggers a cycle: a failed source still reloads the configuration, slides the 14-day window and appears in the summary. The cvestream logs never contain a token or a feed path.

## Feeds

- URL: `http://<host>:8080/feeds/<token>.xml`. Only `GET` and `HEAD`; any other method on that path is `405`. There is no trailing-slash redirect: `/feeds/<token>.xml/` is a `404` like any other path.
- An unknown, revoked or disabled endpoint's token, and any other path, return `404` with an empty body (never `401` or `403`).
- Headers: `Content-Type: application/rss+xml; charset=utf-8`, `ETag`, `Last-Modified`, `Cache-Control: max-age=300`. `If-None-Match` (which wins) or `If-Modified-Since` that matches returns `304` with no body.
- The `ETag` is the SHA-256 of the document and `Last-Modified` is the last time it changed, so an unchanged feed answers `304` to well-behaved readers.
- Window: the last 14 days of publications, newest first. A vulnerability that becomes known-exploited or whose severity band rises is published again as a new item prefixed `[UPDATE]`.
- An item shows, one fact per line: the CVE and EUVD IDs, the matched products (`versions unknown` when the versions are not known), the severity band and the highest CVSS score, each source's score with its CVSS version, the known-exploited status (CISA KEV, EU KEV, ransomware use), the affected products and versions, the description and links to every source record. A missing value reads `unknown`, never `0`. The title keeps the severity recorded when the item was published; the body follows the current merged data.
- The text is plain: no HTML, no Slack-specific markup. Some readers collapse the line breaks.
- A configured, enabled endpoint that no run has rendered yet (first start, new endpoint) serves a valid empty channel until its first cycle.

## Token rotation

1. Generate a new token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Replace the endpoint's `token` in `endpoints.yaml`.
3. At the next cycle the daemon logs `config.reloaded`; the new URL serves the feed and the old one returns `404`.
4. Give the team the new URL: they must resubscribe their channel.

## Security

- Publish only port 8080. `/healthz`, `/readyz` and `/status` are private; the compose file does not publish 9090.
- The token is the only access control. cvestream never logs it (`feed.served` carries the `endpoint_id`), and uvicorn's own access log is off.
- **Reverse-proxy and Ingress access logs contain the token** (it is in the request path). Disable or mask path logging for `/feeds/`, and restrict who can read those logs.
- Rate limiting belongs to the reverse proxy.
- The container runs as UID 999 with a read-only root filesystem and no capabilities.

## Backup

`cvestream db backup <file>` copies the SQLite state with the online backup API, a consistent snapshot while the daemon runs. It refuses to overwrite an existing file.

```sh
docker compose exec cvestream cvestream db backup /data/backup-2026-10-26.db
docker compose cp cvestream:/data/backup-2026-10-26.db .
```

Restore: stop the daemon, replace `cvestream.db` in the data volume with the backup, start the daemon. Without the state, nothing remembers what was published: every endpoint would be backfilled with the last 7 days and the team would see those items again.

## Limits

- **Single replica.** Never two daemons on one state database, and never `poll --once` against the database of a running daemon: SQLite allows one writer, and a second one can fail with `SQLITE_BUSY` (see `_bmad-output/implementation-artifacts/deferred-work.md`).
- SQLite needs block storage (a local disk or a block volume), never NFS, SMB or CIFS.
- Source status (`last_success_at`, `last_error`) is not persisted.
