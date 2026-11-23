# Operations

## TL;DR

- `cvestream run` is the daemon: it polls the sources, decides what each team gets and serves one RSS feed per endpoint on `:8080` (public). Health and status are on `:9090` (private, never publish it).
- Self-hosted: `deploy/compose/compose.yml` with your `config/endpoints.yaml`. One replica only.
- Feed URL: `http://<host>:8080/feeds/<token>.xml`. The token is the access control: put an HTTPS reverse proxy in front and **turn off its path logging for `/feeds/`**.
- Health: `cvestream healthcheck [--ready] [--port N]`, `/healthz`, `/readyz`, `/status`. The image already has a `HEALTHCHECK`.
- Logs are JSON lines on stdout. `cycle.summary` says what each cycle did; `source.failed`, `source.stale`, `config.rejected` and `pipeline.failed` are the ones to alert on.
- Edit `endpoints.yaml` and the daemon picks it up at the next cycle, no restart. Rotate a token by editing it.
- Back up the state with `cvestream db backup <file>`.
- A nightly GitHub workflow (`source-drift`) runs the live contract tests against the real source APIs and opens or updates one `source-drift` issue when a source changed or was unreachable.

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
      "activity": "idle",
      "activity_since": "2026-10-26T20:15:02Z",
      "next_fetch_at": "2026-10-26T20:31:20Z",
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

- `activity` says what the source loop is doing now, since `activity_since`:
  - `fetching`: a fetch is running (an EUVD first fetch pages for several minutes);
  - `processing`: the fetch is over, successful or not, and its pipeline run is pending or running;
  - `idle`: waiting for the next fetch at `next_fetch_at` (the interval plus jitter); also the state before the first fetch, with `activity_since` `null`;
  - `crashed`: the loop crashed and the supervisor restarts it at `next_fetch_at`.

  `next_fetch_at` is `null` unless the source is `idle` or `crashed`. With `last_success_at` `null`, `fetching` tells a first fetch still in progress from one that never started.
- `stale` is true when the source has had no success for more than three intervals; for a source that has not succeeded yet, the time is counted from the start of the daemon. The daemon logs `source.stale` (warning) at the same moment.
- `activity`, `last_success_at` and `last_error` live in memory: they are lost at restart. `last_error` keeps the last failure (a `SourceError`, or the exception type of a crash of the source loop), even after the source recovered.
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
| `source.stale` | warning | no success for more than 3 intervals, checked after a failed fetch, a rolled-back run or a crash | `source`, `last_success_at` |
| `source.crashed` | error | an unexpected exception in a source loop (with traceback); the loop restarts after 1, 2, 4... s, capped at 300 s or the source interval, whichever is longer, so a crashing source never polls more often than a healthy one | `source` |
| `pipeline.failed` | error | a cycle raised (with traceback): the run is rolled back, the previous feeds keep being served | `error_type` |
| `pipeline.completed` | info | a run committed | `count`, `records`, `changed`, `touched`, `duration_ms` |
| `publish.decided` | info | an item was published to an endpoint | `endpoint_id`, `vuln_id`, `kind`, `group_guid` when the item is a group member |
| `publish.grouped` | info | a group item was published (3 or more members of one run, one product and kind, at most 5 per item) | `endpoint_id`, `family` (`new` or `band`), `count`, `part`, `parts`, `group_guid` |
| `publish.skipped` | warning | a decision was dropped because its GUID is already stored for another vulnerability (two advisory-only vulnerabilities sharing a primary ID); the run goes on | `reason` (`guid_collision`), `endpoint_id`, `vuln_id`, `kind` |
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
- `published`: items published per enabled endpoint, `0` included — each group member counts, so a burst of 5 renders as one feed item but counts 5 here. A permanent `0` for every endpoint is worth a look.
- `score_unknown` and `versions_unknown`: published items let through because their score or their versions are unknown (recall over volume). Items held back by an endpoint's `require_score` are not published, so they are not counted.

A fetch outcome, failed ones included, triggers a cycle: a failed source still reloads the configuration, slides the 14-day window and appears in the summary. The cvestream logs never contain a token or a feed path.

## Source drift

`.github/workflows/source-drift.yml` runs every night (04:17 UTC) and on demand, from `main`. It runs `uv run pytest -m live tests/contract`: every contract test marked `live` (EUVD, CISA KEV, CERT-EU and each later source), against the real APIs, with the same HTTP client as the daemon. Tests are selected by marker, so a new source's live test runs without editing the workflow. It uses a few Actions minutes per night (private repository) and only the `GITHUB_TOKEN`.

What a `source-drift` issue means: a live test failed. Either the source changed its API or payload (fields renamed, type changed, ordering, pagination, URL), which would make the daemon skip records or fail the source, or the source was down, slow or rate-limiting that night.

- The workflow opens **one** issue labelled `source-drift`, then adds a comment on each later failing night while the issue stays open. It never opens a second one and never closes it. It creates the label itself on its first failure.
- The issue (or comment) links the run, names the commit tested and lists the failing tests (the `FAILED` and `ERROR` lines of pytest).
- A run that fails before the tests (checkout, install) also reports, with "No test result" instead of a list. A run where pytest prints no `FAILED` or `ERROR` line (no `live` test collected, the test step timed out, pytest crashed) reports "No failing-test line".
- A green run opens nothing. A run that hits the 20-minute job timeout is cancelled and reports nothing: look at the Actions tab. A hung API fails the test step at 15 minutes and does report.

Triage:

1. Open the run linked in the issue and read the failing assertion.
2. Reproduce locally from a direct connection: `uv run pytest -m live tests/contract -v -rfE`. Behind a TLS-intercepting proxy the live tests fail by design (the client ignores proxy settings).
3. Compare the live payload with the recorded fixtures in `tests/fixtures/<source>/` (how each was recorded: `tests/fixtures/README.md`).
4. Check the daemon side: `source.failed` with `SourceContractError` or `SourcePayloadError` in the logs, and `last_error` and `stale` in `/status` (see Logs and `/status` above).
5. One failing night followed by green ones is a transient outage: close the issue with a note.

Fix: update the adapter in `src/cvestream/sources/`, re-record the fixtures (never edit them by hand), update the contract tests and `docs/sources.md`, in a pull request.

Close: by hand, once the fix is on `main` and a run is green (next night, or run it by hand).

Run it by hand: Actions tab, `source-drift`, "Run workflow", or

```sh
gh workflow run source-drift.yml --repo wixyvir/cvestream [--ref <branch>]
gh run watch --repo wixyvir/cvestream
```

A `--ref` other than `main` runs that branch's copy of the workflow (useful to test a change), but the workflow file must already exist on `main`.

## Feeds

- URL: `http://<host>:8080/feeds/<token>.xml`. Only `GET` and `HEAD`; any other method on that path is `405`. There is no trailing-slash redirect: `/feeds/<token>.xml/` is a `404` like any other path.
- An unknown, revoked or disabled endpoint's token, and any other path, return `404` with an empty body (never `401` or `403`).
- Headers: `Content-Type: application/rss+xml; charset=utf-8`, `ETag`, `Last-Modified`, `Cache-Control: max-age=300`. `If-None-Match` (which wins) or `If-Modified-Since` that matches returns `304` with no body.
- The `ETag` is the SHA-256 of the document and `Last-Modified` is the last time it changed, so an unchanged feed answers `304` to well-behaved readers.
- Window: the last 14 days of publications, newest first. A vulnerability that becomes known-exploited or whose severity band rises is published again as a new item prefixed `[UPDATE]`.
- An item's title names the CVE (or EUVD ID), the affected product it matched as the source names it (`CVE-2026-1234 in PHP Group PHP`, or your entry's name when the vulnerability lists no matching product), the severity band and `EXPLOITED` when it is in CISA KEV or EU KEV: `🔥🔴 CVE-2026-1234 in PHP Group PHP: severity critical, EXPLOITED`. An update reads `🔥 [UPDATE] … now EXPLOITED, severity high` or `🔴🔺 [UPDATE] … severity raised to critical`. The title is flagged with Unicode emoji built from the values recorded when the item was published, all that apply in this order, then one space: 🔥 in CISA KEV or EU KEV (not "a public exploit exists"), 🔴 severity band `critical`, 🔺 the item is a `band-*` update raised to `medium` or above (a rise to `none` or `low` keeps its words but gets no 🔺), 🆕 first published on the UTC day cvestream published the item. Every other band and the unknown state get no emoji. The emoji never replace the words: `🔥🔴🆕 CVE-2026-95517 in Apache Software Foundation Apache Tomcat: severity critical, EXPLOITED`, `🆕 CVE-2026-107494 in OpenSSL: severity unknown`, `🔴🔺 [UPDATE] CVE-2026-118028 in Apache Software Foundation Apache Tomcat: severity raised to critical`, `🔥 [UPDATE] CVE-2026-1234 in PHP Group PHP: now EXPLOITED, severity high`. Without any marker the title has no leading space.
- An item's body leads with what a reader must see even when the text is cut (see the Slack window below): a facts line `critical · CVSS 9.1 · exploited: no · published 2026-11-19 · updated 2026-11-20` (the `updated` part is left out when it falls on the same UTC day as `published`; an unknown value reads `unknown`), then the matched products (`versions unknown` when the versions are not known), then the full description, never cut on the server. After that: the affected products, each product label once (the vendor is dropped when the product name already contains it) with the source's version text verbatim, at most 3 per product then `(+N more)`; then the CVE and EUVD IDs, each source's CVSS score with its version, and — only when the vulnerability is known-exploited — the KEV detail (CISA KEV, EU KEV, the day each list added it, ransomware use); then links to every source record and the references. A missing value reads `unknown`, never `0`. The title keeps the values recorded when the item was published; the body follows the current merged data, so the same item can read `EXPLOITED` in the title and `exploited: no` in the facts line.
- Slack's RSS app posts one message per item and shows only about the first 495 plain-text characters of the body (the `SLACK_PREVIEW_CHARS` constant in `src/cvestream/pipeline/render.py`; measured 492 to 498 on three long posts, not documented by Slack), keeping only the first block-level elements (consistent with the paragraph and line hypotheses, inferred from a local replay and the ~208-character posts of 2026-11-19 that showed no `Description:` line; not measured against Slack). That is why the facts line, the matched products and the start of the description come first, in the first paragraph. Golden plain-text example (description shortened): `critical · CVSS 9.1 · exploited: no · published 2026-11-19 · updated 2026-11-20` / `Matched: tomcat` / `Description: Reliance on obfuscation or encryption …`.
- The item date (`pubDate`, what readers show) is when cvestream published the item, not when the vulnerability was disclosed: an old CVE that just became known-exploited shows today's date and its real age in the facts line (`published …`).
- With `group_by_product: true`, a burst of 3 or more items of one run that share a matched product and kind arrives as one group item: `🔴🆕 Apache Software Foundation Apache Tomcat: 5 new vulnerabilities, worst critical (1 critical, 3 high, 1 medium)`, or for severity raises `🔴🔺 [UPDATE] Apache Software Foundation Apache Tomcat: 4 vulnerabilities raised (2 critical, 2 high), part 2 of 2` (a burst of more than 5 splits into near-equal chunks of at most 5, `part i of k`; 6 → 3 + 3, so every chunk keeps at least 3 members). Its body is one line per member in a single paragraph, most severe first, each ID linked to its source: `CVE-2026-95517 · critical 9.1 · exploited: no · 10.1.0-M1 ≤10.1.61 (+4 more)`. Each line resolves its versions under that member's own title-product label and caps the first version text at 60 characters plus `…` (single items always show the source version text verbatim). The title's per-band counts use the stored bands while a member line shows the current band and score, so after a rescore within a band the counts and a line can disagree and the lines may not look sorted. The item's GUID is `cvestream:{endpoint_id}:group-{family}:{16 hex}` (`group-new` or `group-band`) with `isPermaLink="false"`; the members keep their own publication rows and GUIDs. Known-exploited vulnerabilities are never grouped, and a member's later KEV or severity update is its own item. Grouping is decided once at publish time: switching `group_by_product` on or off later never changes items already published, so nothing is posted twice. `/status` `item_count` counts a group as one item.
- The description is simple HTML, the standard for RSS: bold labels, line breaks, the source records and the references as links (only `http` and `https` URLs become links). Text from the sources is escaped, so markup in a vulnerability description shows as text and cannot inject anything. There is no Slack-specific markup (the emoji are plain Unicode text); Slack's RSS app shows the text without the formatting.
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
