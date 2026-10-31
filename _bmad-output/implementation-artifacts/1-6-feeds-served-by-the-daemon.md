---
baseline_commit: 12f6857c196a5ac9fb7114f70c317d284493f458
---

# Story 1.6: Feeds served by the daemon

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a channel subscriber,
I want a token-protected RSS 2.0 feed that a long-running daemon keeps up to date,
so that I can subscribe my Slack channel once and receive relevant alerts within an hour.

## Acceptance Criteria

1. **RSS 2.0 rendering (FR-17).** `pipeline/render.py` renders one endpoint's feed as valid RSS 2.0 built with `xml.etree.ElementTree` (never string concatenation): a `channel` with `title`, `link`, `description` and, when there are items, `lastBuildDate`; one `item` per publication of the last 14 days (`PUBLICATION_WINDOW`, measured on `publication.published_at`), newest first. Each item has `title`, `description`, `guid isPermaLink="false"` equal to the **stored** `publication.guid`, `pubDate` (RFC 822 through `email.utils.format_datetime(..., usegmt=True)`) and, when a source URL exists, `link`. Source text is escaped as plain text by the serializer, characters that XML 1.0 forbids are removed, and there is no Slack-specific markup. The function is pure and deterministic: the same inputs give the same bytes.
2. **Item content (FR-18, FR-24).** The title names the matched product (`products[0]`) and the severity band recorded at decision time, plus "known exploited" when it was; every kind other than `new` is prefixed `[UPDATE] `. The description shows, one fact per line: the CVE and EUVD IDs; the matched products, with "versions unknown" when the match carries that label; the severity band with the highest score; each source's CVSS score with its CVSS version and source name; the KEV status with its list (CISA KEV, EU KEV) and known ransomware use; the affected products with their versions or "versions unknown"; one description; links to every source record. A missing value renders as `unknown`, never `0`, `n/a` or an empty string.
3. **Feed cache table.** `state/schema.py` declares a new `feed_cache` table and `state/migrations/versions/0002_add_feed_cache.py` (revision `"0002"`, `down_revision = "0001"`) creates it. `pipeline/run.run()` renders every **enabled** endpoint of the snapshot after its decisions and upserts the XML into `feed_cache` **inside the run's single transaction**, with an ETag (quoted SHA-256 hex of the XML bytes), the item count, `rendered_at = now` and a `changed_at` that moves only when the ETag changes. `pipeline/run.load_feeds(state)` reads the stored feeds (read-only). The "metadata equals migrations" test stays green.
4. **Daemon startup (`cvestream run`).** In this order: configure logging from settings → migrate the state → start the private listener (`/healthz` 200, `/readyz` 503) → load the configuration (a missing or unreadable file logs `config.unavailable` and exits 3; an invalid file logs `config.rejected` and exits 2 with one stderr line) → load `feed_cache` into the in-memory feed cache → start the pipeline worker and one loop per enabled source → start the public listener → `/readyz` 200 and `app.ready` is logged. A settings or state error at startup is one stderr line and exit 2.
5. **Runtime model.** Each source loop fetches with its own interval plus jitter (FR-4; jitter from an injectable `random`), reading its cursor from the state before every fetch (through `pipeline.run.load_cursors` in a thread). Every fetch outcome (success or `SourceError`) goes onto one `asyncio.Queue` consumed by **one** serialized pipeline worker that runs `pipeline.run.cycle(...)` through `asyncio.to_thread`; the loop waits until the run that carries its outcome has finished before it sleeps. After a cycle, the worker builds a new feed cache and swaps it **by reference**. HTTP handlers read only that in-memory cache; no code on the event loop touches SQLite or the provider.
6. **Public listener (`:8080`, FR-20).** Only `GET` and `HEAD /feeds/{token}.xml`. A known token of an enabled endpoint returns `200` with `Content-Type: application/rss+xml; charset=utf-8`, `ETag`, `Last-Modified` (the feed's `changed_at`), `Cache-Control: max-age=300` and `Content-Length`; `HEAD` returns the same headers and no body. `If-None-Match` (taking precedence) or `If-Modified-Since` that matches returns `304` with `ETag`, `Last-Modified` and `Cache-Control` and no body. An unknown, revoked or disabled endpoint's token, and any other path, return `404` with an empty body; any other method on the feed path returns `405` with an empty body. Each request logs `feed.served` (`endpoint_id`, `method`, `status`, `duration_ms`) or `feed.not_found` (`method` only): never the token, never the path. uvicorn's own access log is off.
7. **Failure isolation (FR-6, NFR-5).** A `SourceError` logs `source.failed` (warning, `source`, `error_type`) and is reported to the worker; that source's cursor does not advance, the other sources keep running and every feed keeps its last content. An unexpected exception in a source loop is logged `source.crashed` (error, with `exc_info`) by its supervisor, which restarts the loop after a capped exponential backoff. An exception in a pipeline cycle is logged `pipeline.failed` (error, with `exc_info`); the run is rolled back, the worker keeps serving and the previous feed cache stays. `source.stale` (warning) is logged when `now - last_success_at > 3 × interval` (from the daemon start when the source has not succeeded yet).
8. **Hot reload (FR-23).** Every cycle compares `provider.revision()` with the active snapshot. A valid change is loaded (`config.reloaded`) and applies to that same cycle: endpoints, product lists, rules, enabled flags and tokens (a rotated token serves the new URL and the old one returns `404`). An invalid change logs `config.rejected` (warning, with the field errors, which never contain a token) and keeps the last valid snapshot; an unreadable file logs `config.unavailable` (warning) and keeps it too.
9. **`cycle.summary` (NFR-4).** Every cycle that runs logs exactly one `cycle.summary` (info) with: per source in this cycle, `result` `ok` with `records` and `skipped` (the adapter's count) or `failed` with `error_type`; per enabled endpoint, the published count (`0` included); the number of published items let through on unknown score (`score_unknown`) and on unknown versions (`versions_unknown`); `duration_ms`.
10. **Private listener (`:9090`).** `/healthz` returns `200 {"status": "ok"}` while the event loop responds. `/readyz` returns `200 {"status": "ready"}` once the configuration is loaded, the state is migrated and the feed cache is loaded, and `503 {"error": {"code": "not_ready", "message": "..."}}` before that and during shutdown; it does not depend on sources. `/status` returns, with no envelope, `sources` (per source: `interval_seconds`, `last_success_at`, `last_error` (`type`, `at`) or `null`, `cursor`, `stale`) and `endpoints` (per enabled endpoint id: `item_count`, `last_render_at`). Datetimes are ISO 8601 with `Z`; no token and no feed path appear anywhere. Unknown private paths return `404 {"error": {"code": "not_found", ...}}`.
11. **`poll --once` and shutdown.** `cvestream poll --once` migrates, loads the configuration (same exit codes as `run`), fetches every enabled source once, runs one cycle (one `cycle.summary`) and exits `0`, also when a source failed (the failure is in the summary). `cvestream poll` without `--once` is a user error (exit 2). SIGTERM or SIGINT makes `/readyz` return `503`, stops the source loops, lets the current pipeline run finish (never cancelled mid-run), stops both servers, closes the HTTP client and the state, logs `app.stopped` and exits `0`.
12. **`cvestream healthcheck [--ready]`.** Requests `http://127.0.0.1:<port>/healthz` (or `/readyz` with `--ready`; `--port` defaults to `9090`) with the standard library only (`urllib`, no proxy handler, 2 s timeout) and exits `0` on HTTP 200, `1` otherwise, printing nothing on success. `cli/healthcheck.py` imports only stdlib and `typer`; importing `cvestream.cli.main` does not import `cvestream.app`, `starlette`, `uvicorn` or `httpx` (the `run` and `poll` commands import the app inside their function body). The runtime image gets `HEALTHCHECK CMD ["cvestream", "healthcheck"]` and `CMD ["run"]`.
13. **Deployment files (NFR-2).** `deploy/compose/compose.yml` runs `ghcr.io/wixyvir/cvestream` with the YAML provider, the endpoints file mounted read-only from a **directory**, a named volume on `/data`, `restart: unless-stopped`, only port 8080 published, `read_only: true` with a `tmpfs` on `/tmp`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]` and `stop_grace_period: 60s`. The dev `compose.yml` and the `dev` image target run `watchfiles "cvestream run" src`.
14. **Tests.** `tests/integration/test_pipeline_end_to_end.py` goes from the recorded EUVD and CISA KEV fixtures (through the real adapters and respx) to a feed served by the public Starlette app (through `httpx.ASGITransport`, no TCP), including `304`, `HEAD`, an unknown token and a restart that serves the same bytes from `feed_cache`. `tests/integration/test_restart_no_repost.py` stays green. Unit tests cover the renderer, the repository additions, the cycle, the feed cache, both APIs, the scheduler and supervisor, the runtime startup and shutdown order, the healthcheck and the CLI exit codes, with `FakeClock`, injected randomness and no real sleeps.
15. **Settings, CLI errors and docs.** New settings `http.public.host`/`port` (default `0.0.0.0:8080`) and `http.private.host`/`port` (default `0.0.0.0:9090`), documented in `settings.example.yaml` and `docs/configuration.md`. An unexpected CLI error prints one line and exits 1, with the traceback only under `--debug` (deferred from 1.5). `docs/operations.md` is created (running, logs, `/status`, health, backup, token rotation, the reverse-proxy log warning, single replica); `README.md` gets the self-hosted quick start; `CLAUDE.md`, `architecture.md`, `endpoints.example.yaml` and `deferred-work.md` are amended as listed in Task 13.

## Tasks / Subtasks

- [x] **Task 1: Dependencies (AC: 5, 6, 10)**
  - [x] 1.1 `uv add "starlette>=1.10.0" "uvicorn>=0.54.0"` (runtime; plain `uvicorn`, **not** `uvicorn[standard]`). Commit `pyproject.toml` + `uv.lock` together: `chore(story-1.6): add starlette and uvicorn`. Never edit `uv.lock` by hand. No other dependency (no `httpx2`, no `fastapi`, no `feedparser` in this story).

- [x] **Task 2: Settings (AC: 4, 15)**: see "Settings design".
  - [x] 2.1 `ListenerSettings`, `HttpSettings`, `Settings.http` in `settings.py`.
  - [x] 2.2 `tests/unit/test_settings.py`: defaults, `CVESTREAM_HTTP__PUBLIC__PORT`, port bounds (0 and 65536 rejected), unknown key rejected.

- [x] **Task 3: `feed_cache` table and migration (AC: 3)**: see "Feed cache table".
  - [x] 3.1 Table in `state/schema.py`.
  - [x] 3.2 `state/migrations/versions/0002_add_feed_cache.py` (`revision = "0002"`, `down_revision = "0001"`), batch-safe `op.create_table`, `downgrade()` drops it. Never edit `0001`.
  - [x] 3.3 Update the tests pinned to revision `0001` (`tests/unit/state/test_db.py` lines 24 to 37, `tests/unit/cli/test_db.py` lines 44 and 94) and the table set in `tests/unit/state/test_schema.py` (line 25).

- [x] **Task 4: Repository additions (AC: 1, 3)**: see "Repository additions". `StoredPublication`, `StoredFeed`, `publications_since()`, `feeds()`, `save_feed()`.

- [x] **Task 5: `pipeline/render.py` (AC: 1, 2)**: see "Renderer design". `FeedEntry`, `RenderedFeed`, `render_feed()`, `etag_of()`.

- [x] **Task 6: `pipeline/run.py` and `pipeline/summary.py` (AC: 3, 8, 9)**: see "Pipeline additions".
  - [x] 6.1 `run()` renders and stores the feeds inside its transaction; `RunResult.feeds`.
  - [x] 6.2 `load_feeds()`, `SourceOutcome`, `reload_config()`, `cycle()`, `CycleResult`.
  - [x] 6.3 `pipeline/summary.py`: `SourceReport`, `CycleSummary`, `summarize()`, `log_summary()`.

- [x] **Task 7: `app/` package (AC: 4 to 11)**: see "App design".
  - [x] 7.1 `app/feed_cache.py`: `CachedFeed`, `FeedCache.build()`, `FeedCacheHolder`.
  - [x] 7.2 `app/public_api.py`: `build_public_app(holder)`.
  - [x] 7.3 `app/private_api.py`: `RuntimeStatus`, `SourceStatus`, `build_private_app(status, holder)`.
  - [x] 7.4 `app/servers.py`: `EmbeddedServer` (uvicorn `Server` without signal capture), `build_server(app, listener)`.
  - [x] 7.5 `app/scheduler.py`: `PipelineWorker`, `source_loop()`, `supervise()`.
  - [x] 7.6 `app/runtime.py`: `serve(...)` and `poll_once(...)` returning exit codes; SIGTERM/SIGINT handling.

- [x] **Task 8: CLI (AC: 4, 11, 12, 15)**: see "CLI design".
  - [x] 8.1 `cli/main.py`: `run`, `poll --once`, `healthcheck` registered; app imported lazily; `--debug` wired in `main()`.
  - [x] 8.2 `cli/healthcheck.py`: stdlib-only probe.

- [x] **Task 9: Image and compose (AC: 12, 13)**
  - [x] 9.1 `Dockerfile`: runtime `HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["cvestream", "healthcheck"]` and `CMD ["run"]` (remove the "story 1.6 switches to" comments); `dev` target `CMD ["watchfiles", "cvestream run", "src"]`.
  - [x] 9.2 `compose.yml` (dev): see "Compose files".
  - [x] 9.3 `deploy/compose/compose.yml` (self-hosted, see "Compose files"). Do not commit a `deploy/compose/config/` directory: add `/deploy/compose/config/` to `.gitignore` (an operator who copies `endpoints.yaml` there inside a clone must not commit real tokens) and describe the directory in `docs/operations.md`.

- [x] **Task 10: Tests (all ACs)**: see "Testing requirements". Includes `tests/integration/test_pipeline_end_to_end.py`.

- [x] **Task 11: `.github/workflows/ci.yml` (AC: 12)**: **no change**. It already triggers on every `pull_request` with no `branches:` filter (stacked PRs target story branches, not `main`) and pushes images only on push to `main`; keep it that way. Its smoke test `docker run --rm cvestream:ci --help` still works with `CMD ["run"]` (arguments replace `CMD`).

- [x] **Task 12: Gates (all ACs)**
  - [x] 12.1 `uv sync --locked && uv run ruff check && uv run ruff format --check && uv run ty check && uv run lint-imports && uv run pytest && uv run cvestream config schema --check && uv run cvestream config validate endpoints.example.yaml` all green. `schemas/config-v1.json` must not change (no config model change in this story).
  - [x] 12.2 Manual checks of `epic-1-stack-plan.md` §3.6 with `CVESTREAM_DATA_DIR=$(mktemp -d)` and an `endpoints.yaml` built from the example with a real token: `uv run cvestream run` (logs show `state.migrated`, the listeners, the source loops, `app.ready`); `curl -i localhost:9090/healthz`, `/readyz`, `/status`; `uv run cvestream healthcheck --ready; echo $?`; the feed with `curl -i`, `If-None-Match`, `curl -I`, a wrong token; `xmllint --noout` on the body if `xmllint` exists (else `uv run python -c "import xml.etree.ElementTree as ET, sys; ET.parse(sys.argv[1])" feed.xml` and say so); edit `endpoints.yaml` (valid, then invalid) and watch `config.reloaded` / `config.rejected`; `Ctrl-C` stops cleanly; `uv run cvestream poll --once`. Real network is needed for the sources: if it is unavailable, say so in the Completion Notes and do not claim the checks ran.
  - [x] 12.3 `docker build --target runtime -t cvestream:local .`, then `docker run --rm cvestream:local --help`, then `docker compose -f deploy/compose/compose.yml config` (valid) and, with a locally tagged image, `up -d` + `docker inspect --format '{{.State.Health.Status}}'` reaching `healthy`. If Docker is unavailable, say so; do not claim it ran.

- [x] **Task 13: Docs (AC: 15, same PR)**
  - [x] 13.1 `settings.example.yaml`: an `http` block (public and private listeners, env names, the note that only 8080 should be published and that the healthcheck uses `--port` when the private port changes).
  - [x] 13.2 `docs/configuration.md`: four rows in the keys table (`http.public.host`, `http.public.port`, `http.private.host`, `http.private.port`); "Create or upgrade" bullet: drop "(story 1.6)"; "Reloading" section: the daemon now does it (drop "(once the daemon exists, story 1.6)"), and say that a change applies at the next cycle (the next source fetch).
  - [x] 13.3 `docs/operations.md` (new): see "Operations doc outline".
  - [x] 13.4 `README.md`: "Status" paragraph says feeds are served by the daemon (drop "There are no feeds yet"); a "Self-hosted quick start" section (`deploy/compose/compose.yml`, `config/endpoints.yaml`, feed URL `http://<host>:8080/feeds/<token>.xml`, HTTPS through a reverse proxy, link to `docs/operations.md`); the dev quick start gets `uv run cvestream run` and `uv run cvestream poll --once`.
  - [x] 13.5 `endpoints.example.yaml` header: "Once the daemon exists, a change is applied at the next poll cycle" becomes "A change is applied at the next poll cycle, without restart".
  - [x] 13.6 `CLAUDE.md` "Key rules": one line "Only `pipeline/run.py` opens `State.transaction()`: `app/` reads state through `pipeline.run` functions in a thread, and HTTP handlers read only the in-memory feed cache."
  - [x] 13.7 `architecture.md`: (a) "Naming Patterns", migrations bullet: the example `0003_add_feed_cache.py` becomes `0002_add_feed_cache.py`; (b) "Feed cache" paragraph (Data Architecture): append the rules of this story (ETag = SHA-256 of the XML, `Last-Modified` = last content change, `lastBuildDate` = newest item, title from the decision-time values and body from the current merged data, a known token without a stored feed gets an empty channel); (c) "Runtime configuration" bullet: name the `http.public` / `http.private` listener settings; (d) "API & Communication Patterns", process model: one line "a source loop reads its cursor from the state before each fetch and waits for the run carrying its outcome; every fetch outcome, failed ones included, triggers one cycle".
  - [x] 13.8 `deferred-work.md`: strike through and mark resolved "`--debug` has no effect" (resolved by story 1.6) and "Tokens hidden in non-string log values" (resolved by story 1.6: the access log writes `endpoint_id` only and uvicorn's access log is off; the redaction gap itself remains for other callers, say so). Leave the others untouched.

### Review Findings

Code review of 2026-10-26 (Blind Hunter, Edge Case Hunter, Acceptance Auditor): 2 decision-needed (settled 2026-10-31: HTML with escaped source text, and option 2), 4 patch, 3 defer, 8 dismissed.

- [x] [Review][Decision] Source text in `<description>` is read as HTML by RSS readers — RSS 2.0 defines `description` as entity-encoded HTML. `render.py` sets source text once as XML text (AC 1, "escaped as plain text by the serializer"), so a source description containing `<img src=x onerror=...>` or `<b>` reaches the reader, after XML parsing, as markup that a reader rendering HTML (most desktop and web readers; Slack strips it) interprets. Options: (1) keep single escaping and rely on readers sanitising; (2) HTML-escape every source-derived line (`html.escape(..., quote=False)`) before setting the text, so readers show the literal text (and optionally join lines with `<br/>`, which also settles the "readers collapse line breaks" decision); (3) wrap the description in CDATA (forbidden by the story). Recommendation: (2), checked with the Slack RSS app during the trial (architecture M3). [src/cvestream/pipeline/render.py:250] — resolved 2026-10-31: answered (1) on the thread, then, after the trial screenshot, HTML structure with escaped source text (option 2 plus structure; raw source text would break the new markup): the description is an HTML fragment (bold labels, `<br/>`, source records and references as links), every value goes through `html.escape`, only `http`/`https` URLs become links (c16a0c3); AC 1's "escaped as plain text by the serializer" now reads "HTML-escaped, then XML-escaped by the serializer"
- [x] [Review][Decision] Crash backoff cap (300 s) is shorter than the source interval — a source loop that crashes on every attempt (an adapter bug raising something other than `SourceError` after its HTTP calls) is restarted every 5 minutes once the backoff is capped, three times more often than its 15-minute interval, so a bug makes cvestream poll a public API harder than configured. The cap is fixed by the story (`CRASH_BACKOFF_CAP = 300.0`). Options: (1) keep 300 s; (2) cap the backoff at the source interval (`max(300, interval)`), so a crashing source is never polled more often than a healthy one; (3) restart at the interval right away (no exponential phase). Recommendation: (2). [src/cvestream/app/scheduler.py:261] — resolved 2026-10-31: option (2), `supervise(..., cap=crash_backoff_cap(interval))` caps the backoff at `max(300 s, interval)` (b8152f0)
- [x] [Review][Patch] Trailing-slash redirects on both listeners: Starlette's default `redirect_slashes` answered `/feeds/<anything>.xml/` (any token, known or not) and `/healthz/` with a `307` to the path without the slash, built from the client's `Host` header, instead of the `404` of AC 6 and AC 10 (and logged nothing). Disabled on both routers; tests added. [src/cvestream/app/public_api.py:107, src/cvestream/app/private_api.py:124]
- [x] [Review][Patch] A crashing source loop never recorded `last_error` nor logged `source.stale` (AC 7, AC 10): the stale check ran only after a failed fetch or a rolled-back run, so a source whose loop kept crashing showed `last_error: null` in `/status` and never raised the alert. `supervise()` takes an `on_crash` callback; the runtime passes `record_crash`, which records the error type and time and logs `source.stale` when due. Tests added; `docs/operations.md` updated. [src/cvestream/app/scheduler.py:215]
- [x] [Review][Patch] `_FORBIDDEN` regex held literal U+D7FF, U+E000 (invisible) and U+FFFD characters in the source, and its test a literal U+FFFE: written as escapes, with a test pinning the edges of the allowed ranges. [src/cvestream/pipeline/render.py:41]
- [x] [Review][Patch] `cvestream healthcheck` let `http.client.HTTPException` escape (something that is not HTTP answers on the port, e.g. `BadStatusLine`): it surfaced as "unexpected error" through `main()` instead of the healthcheck's own one-line failure. Caught with the other connection errors; test added. [src/cvestream/cli/healthcheck.py:54]
- [x] [Review][Defer] Feeds have no item cap [src/cvestream/pipeline/run.py:186] — deferred, the story sizes a feed at "a few hundred items at most"; a broad product list could render thousands of items per endpoint at every cycle.
- [x] [Review][Defer] Configuration reload (token revocation included) waits for a source fetch [src/cvestream/pipeline/run.py:258] — deferred, spec-compliant (FR-23 "at the next poll cycle"); a leaked token stays valid up to one interval (60 min + jitter at most), and with every source disabled the configuration never reloads.
- [x] [Review][Defer] `cvestream healthcheck` always probes `127.0.0.1` [src/cvestream/cli/healthcheck.py:43] — deferred, a private listener bound to one specific non-loopback address (`http.private.host: 10.0.0.5`) makes the probe, and the image `HEALTHCHECK`, fail; document it or add `--host` when the Kubernetes manifests arrive (story 3.3).

### Trial feedback (2026-10-31)

Manual testing of wixyvir/cvestream#6 (`cvestream run` on an empty data dir, feed read in Liferea).

- [x] [Trial][Fix] `/status` could not tell whether a fetch was running (a long EUVD first fetch looked like a source that never ran): each source now shows `activity` (`idle`, `fetching`, `processing`, `crashed`), `activity_since` and `next_fetch_at` [src/cvestream/app/private_api.py] (53e3cad)
- [x] [Trial][Fix] EUVD's first fetch never succeeded: the EUVD gateway answers 403 past about 40 to 60 requests a minute, and paging at 0.5 s was above it; a 14-day first fetch (about 180 pages) was lost on every interval. Options 1 and 3 chosen: `PAGE_PAUSE` is 1.5 s (1385ce3) and configurable as `sources.euvd.page_pause` (57b03e2); option 2 (resume after a 403) is deferred [src/cvestream/sources/euvd.py]
- [x] [Trial][Fix] Filtering on `php` let WordPress plugins through (`Module.php`, "PHP Object Injection" in their descriptions): structured affected-product data now decides whenever a vulnerability has any, the description is a fallback only without it; FR-12 and FR-13 amended [src/cvestream/domain/matcher.py] (ae4c9e7)
- [x] [Trial][Fix] Titles named the endpoint's entry (`in php`) and the exploitation was easy to miss: a title now names the matched affected product with its vendor (`in PHP Group PHP`) and ends with `EXPLOITED` when known exploited; AC 2's "names the matched product (`products[0]`)" now reads "names the affected product that `products[0]` hits, or `products[0]` without one" [src/cvestream/pipeline/render.py] (84fea0d)
- [x] [Trial][Fix] The description showed as one paragraph without HTML: see the first review decision above (c16a0c3)

## Dev Notes

### Scope boundaries (read first)

- **In this story:** `pipeline/render.py`, `pipeline/summary.py`, additions to `pipeline/run.py`, the `feed_cache` table and migration `0002`, repository additions, the whole `app/` package (`runtime.py`, `scheduler.py`, `feed_cache.py`, `public_api.py`, `private_api.py`, `servers.py`), `cli/healthcheck.py` and the `run` / `poll` / `healthcheck` commands, the `http` settings, `Dockerfile` (`CMD`, `HEALTHCHECK`, dev `CMD`), `compose.yml`, `deploy/compose/compose.yml`, tests, docs.
- **Not in this story:** the Valkey provider, `test_startup_requires_valkey.py` and the `valkey` key layout (3.1); Kubernetes manifests, `release.yml`, `source-drift.yml` (3.3, 2.2); CERT-EU / CERT-FR and generic adapters (2.1, 2.2); Prometheus `/metrics`; raw-payload retention; splitting the EU KEV dump out of the `euvd` fetch (deferred item, keep it deferred); the two pending decisions of story 1.5 (advisory-only GUID collision, `unknown` to `none`): do **not** change `domain/publication.py`.
- **No change to** `domain/*`, `config/*`, `sources/*`, `schemas/config-v1.json`, `http.py`, `state/codec.py`, `state/backup.py`, `state/migrations/versions/0001_create_schema.py`, `.github/workflows/ci.yml`. Reuse them as they are.
- **Do not persist source status.** `last_success_at` / `last_error` live in memory (see decisions); no table, no column beyond `feed_cache`.

### Existing code this story builds on (exact names)

- `pipeline/run.py`: `SourceBatch(source, result: FetchResult)`, `RunResult(decisions, records, changed, skipped, touched)`, `load_cursors(state) -> dict[str, str | None]` (read-only transaction), `run(state, snapshot, batches, clock) -> RunResult` (reads `now = clock.now()` once; one `state.transaction()`; skips disabled endpoints; logs `publish.decided` and `pipeline.completed`; never catches).
- `pipeline/correlate.py`: `build_vulnerability(records: Sequence[NormalizedRecord]) -> Vulnerability`, `normalize_all(records) -> list[NormalizedRecord]`.
- `domain/publication.py`: `PUBLICATION_WINDOW = timedelta(days=14)`, `BACKFILL_WINDOW`, `NEW = "new"`, `KEV = "kev"`, `band_kind(band)`, `PublicationDecision(endpoint_id, primary_id, kind, guid, band, is_known_exploited, products, labels)`.
- `domain/model.py`: `Vulnerability(primary_id, aliases, records: tuple[RecordRef], titles, descriptions, scores: tuple[Sourced[CvssScore | None]], affected: tuple[Sourced[AffectedProduct]], kev: tuple[Sourced[KevEntry]], references: tuple[Sourced[str]], published_at)`, `is_known_exploited`; `CvssScore(score, version, vector)`; `AffectedProduct(product, vendor, version_text)`; `KevEntry(catalog: KevCatalog, known_ransomware_use: bool | None, added_at)`; `KevCatalog.CISA_KEV = "cisa_kev"`, `EU_KEV = "eu_kev"`; `RecordRef(source, record_id, url)`.
- `domain/severity.py`: `Band` (compare with `.rank`), `max_score(scores)`, `band_of(score)`. `domain/matcher.py`: `PassThrough.VERSIONS_UNKNOWN = "versions_unknown"`, `SCORE_UNKNOWN = "score_unknown"`. `domain/identity.py`: `alias_kind(alias) -> AliasKind` (`CVE`, `EUVD`, `ADVISORY`). `domain/endpoint.py`: `Endpoint(id, name, products, rules, enabled)`.
- `state/db.py`: `State(url)` (lazy engine), `migrate() -> str`, `transaction()` (the only commit point), `dispose()`, `display_url`; `StateError`. `state/repository.py`: `Repository` (every query), `_upsert(connection, table, values, *, keys, update=())`, `_chunks()`, `StoredRecord`, `load_records(ids) -> dict[int, list[SourceRecord]]`. `state/schema.py`: `metadata` with `NAMING_CONVENTION`, `UTCDateTime`, the five tables.
- `config/provider.py`: `ConfigSnapshot(revision, endpoints, tokens)` (`tokens` is `token -> endpoint id`, `repr=False`; `endpoint_for_token(token)`), `ConfigProvider` protocol (`revision()`, `load()`), `ConfigError`, `ConfigUnavailableError`, `ConfigValidationError(source, errors)` (`errors` never contain input values). `config/yaml_provider.py`: `YamlConfigProvider(path)`: revision = SHA-256 of the bytes.
- `sources/base.py`: `SourceAdapter` (`name`, `async fetch(cursor)`), `FetchResult(records, next_cursor, skipped)`, `SourceError(source, message)` and its three subclasses. `sources/registry.py`: `build_sources(settings.sources, http, clock) -> tuple[ScheduledSource(adapter, interval), ...]` (enabled sources only, settings order). Both adapters take `(http, clock)`; `EuvdAdapter` also `sleep=`.
- `http.py`: `HttpClient(*, sleep=..., random=..., ...)`, an async context manager; the runtime owns one instance and closes it at shutdown.
- `settings.py`: `load_settings()`, `SettingsError`, `state_url(settings)`, `Settings.log` (`level`, `format`), `Settings.sources`, `Settings.endpoints_file`, `Settings.config_provider: Literal["yaml"]`.
- `log.py`: `configure_logging(level, fmt)`; `redact_tokens` masks `token`/`*_token` keys and `/feeds/<token>` in strings (a safety net, not a licence).
- `cli/db.py`, `cli/config.py`: the CLI pattern (`_fail(message)` → one stderr line, exit 2; `load_settings()` inside the command).
- `tests/conftest.py`: `FakeClock` (`now`, `advance`, `set`), `fake_clock`, `anyio_backend` (`"asyncio"`), `state` (migrated SQLite in `tmp_path`). `tests/integration/test_restart_no_repost.py`: the respx setup for the recorded fixtures (`_fetch()`, `_no_sleep`, `CURSOR = "2026-10-20T00:00:00Z"`, `NOW = 2026-10-26T20:00Z`, endpoint `team-browser` with product `Chrome`).

### Layering and import-linter

- `app` → `pipeline`, `sources` (registry, base types), `config` (`YamlConfigProvider`, provider types), `state` (**only** `State` construction, `migrate()`, `dispose()`), `settings`, `http`, `log`, `clock`, `errors`. `cli` → `app` (lazily), `config`, `state`, `settings`.
- `starlette` and `uvicorn` are imported **only** in `cvestream/app/*` (the existing contract enforces it). `pipeline/render.py` uses only the stdlib (`xml.etree.ElementTree`, `email.utils`, `hashlib`) and `cvestream.domain`.
- **`.transaction(` is called only in `pipeline/run.py`** (`tests/unit/state/test_db.py::test_no_commit_outside_the_state_transaction` scans the source). So `app/` never opens a transaction: it calls `pipeline.run.load_cursors`, `load_feeds` and `cycle` through `asyncio.to_thread`.
- `app` may not import `sqlalchemy`; it never needs to (it never sees a `Repository`).
- No `from __future__ import annotations`; every public function annotated; `ty check` with `error-on-warning`.

### Settings design (`settings.py`)

```python
class ListenerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int = Field(ge=1, le=65535)

class HttpSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The container's network namespace is the boundary: only 8080 is published.
    public: ListenerSettings = Field(
        default_factory=lambda: ListenerSettings(host="0.0.0.0", port=8080))  # noqa: S104
    private: ListenerSettings = Field(
        default_factory=lambda: ListenerSettings(host="0.0.0.0", port=9090))  # noqa: S104

class Settings(BaseSettings):
    ...
    http: HttpSettings = Field(default_factory=HttpSettings)
```

- Env: `CVESTREAM_HTTP__PUBLIC__HOST`, `CVESTREAM_HTTP__PUBLIC__PORT`, `CVESTREAM_HTTP__PRIVATE__HOST`, `CVESTREAM_HTTP__PRIVATE__PORT`. Not secrets. Keep the `# noqa: S104` narrow (ruff flags binding all interfaces) and the comment above it.
- No other setting: jitter, backoff, timeouts and the 2 s healthcheck timeout are constants in `app/` and `cli/healthcheck.py`. Adding a setting means documenting it in `settings.example.yaml` and `docs/configuration.md`, so keep the list short.

### Feed cache table (`state/schema.py`, migration `0002`)

| Table | Columns (`NOT NULL`) | Constraints |
|---|---|---|
| `feed_cache` | `id` Integer PK; `endpoint_id` Text (config slug, **no** FK, like `publication.endpoint_id`); `xml` Text (the RSS document, UTF-8 decoded); `etag` Text (quoted, e.g. `"3f2a…"`); `item_count` Integer; `rendered_at` UTCDateTime (last render); `changed_at` UTCDateTime (last time the ETag changed: `Last-Modified`) | `ux_feed_cache_endpoint_id` (`UniqueConstraint("endpoint_id")`) |

- Portable types only (`Text`, not `LargeBinary`); names through the naming convention, never by hand.
- **Never store a token here.** The table is keyed by endpoint id; tokens live in the configuration.
- Migration: write it by hand or autogenerate it (`command.revision(cfg, autogenerate=True, rev_id="0002", message="add feed cache")` against a database at `0001`), then name the file `0002_add_feed_cache.py`; `op.create_table("feed_cache", ...)` with `op.f("pk_feed_cache")` and `op.f("ux_feed_cache_endpoint_id")`; `UTCDateTime` columns use `cvestream.state.schema.UTCDateTime()`. The existing "metadata equals migrations" test (`compare_metadata(...) == []`) is the guard.
- Rows of endpoints that are later removed or disabled stay (harmless): the in-memory cache is built from the current snapshot only.

### Repository additions (`state/repository.py`)

```python
@dataclass(frozen=True, slots=True)
class StoredPublication:
    vulnerability_id: int
    kind: str
    guid: str
    primary_id: str            # at decision time
    band: Band                 # at decision time
    is_known_exploited: bool   # at decision time
    products: tuple[str, ...]  # products_json, in stored order
    labels: frozenset[PassThrough]
    published_at: datetime

@dataclass(frozen=True, slots=True)
class StoredFeed:
    endpoint_id: str
    xml: str
    etag: str
    item_count: int
    rendered_at: datetime
    changed_at: datetime

class Repository:
    def publications_since(self, endpoint_ids: Iterable[str], since: datetime) -> dict[str, list[StoredPublication]]
        # published_at >= since, ordered by published_at DESC, id DESC (uses ix_publication_endpoint_id_published_at)
    def feeds(self) -> dict[str, StoredFeed]
    def save_feed(self, feed: StoredFeed) -> None
        # _upsert(feed_cache, ..., keys=["endpoint_id"], update=["xml", "etag", "item_count", "rendered_at", "changed_at"])
```

- `labels` come back from `labels_json` through `PassThrough(value)`; `band` through `Band(value)`. An unknown stored value raises (`ValueError` is fine: stored data is ours); do not silently skip.
- Chunk `IN (...)` lists with `_chunks()` like the existing methods. No raw SQL, no `RETURNING`, no logging, no commit.

### Renderer design (`pipeline/render.py`)

```python
CHANNEL_LINK = "https://github.com/wixyvir/cvestream"
MEDIA_TYPE = "application/rss+xml; charset=utf-8"   # the public API imports it

@dataclass(frozen=True, slots=True)
class FeedEntry:
    guid: str
    kind: str
    primary_id: str
    band: Band
    is_known_exploited: bool
    products: tuple[str, ...]
    labels: frozenset[PassThrough]
    published_at: datetime
    vulnerability: Vulnerability      # current merged data (build_vulnerability)

@dataclass(frozen=True, slots=True)
class RenderedFeed:
    endpoint_id: str
    xml: bytes
    etag: str
    item_count: int
    rendered_at: datetime
    changed_at: datetime

def render_feed(endpoint: Endpoint, entries: Sequence[FeedEntry]) -> bytes
def etag_of(xml: bytes) -> str          # '"' + sha256(xml).hexdigest() + '"'
```

- **Pure and deterministic.** No clock, no I/O, no logging. Entries are rendered in the given order (the caller sorts newest first). Same inputs → same bytes, so an unchanged feed keeps its ETag and readers get `304`.
- **Document:** `ET.Element("rss", version="2.0")` → `channel` → `title` = `f"cvestream: {endpoint.name}"`, `link` = `CHANNEL_LINK` (RSS 2.0 requires a channel link; the public base URL is unknown to the daemon), `description` = `f"Vulnerabilities matching the products of {endpoint.name}"`, `language` = `en`, `lastBuildDate` = `format_datetime(max(e.published_at), usegmt=True)` **only when there are items** (never the render time: it would change the ETag at every run). Serialize with `ET.tostring(root, encoding="utf-8", xml_declaration=True)`. No `atom:link` (it would need the public URL and the token), no `generator`.
- **Item:** `title`; `link` = the URL of the first `RecordRef` with a URL, records ordered `euvd`, `eu_kev`, `cisa_kev`, then any other source by name (omit `link` when no record has a URL); `description` (below); `guid` with attribute `isPermaLink="false"` = `entry.guid` (the stored value, never rebuilt); `pubDate` = `format_datetime(entry.published_at, usegmt=True)`.
- **Title** (decision-time values from the publication row, so the title says what was published):
  - `new`: `f"{primary_id} in {product}: severity {band}"` + `", known exploited"` when `is_known_exploited`.
  - `kev`: `f"[UPDATE] {primary_id} in {product}: now known exploited, severity {band}"`.
  - `band-<band>`: `f"[UPDATE] {primary_id} in {product}: severity raised to {band}"` + `", known exploited"` when `is_known_exploited`.
  - `product` = `products[0]`; `band` is the `Band` value (`unknown`, `none`, `low`, ...). Any other kind (future): `[UPDATE] ` prefix and the `new` wording.
- **Description**: plain text, one fact per line (`"\n".join`), built from `entry.vulnerability` (current merged data) and the entry's labels:
  1. `IDs: CVE-2026-1234, EUVD-2026-41234`: the vulnerability's CVE aliases, then EUVD aliases (each group sorted), through `alias_kind`; `IDs: unknown` when there are none (advisory-only, later stories).
  2. `Matched: php, nodejs` + `" (versions unknown)"` when `PassThrough.VERSIONS_UNKNOWN` is in the labels.
  3. `Severity: high (highest CVSS 8.1)`; with no score `Severity: unknown` (+ `" (no score; let through)"` when `SCORE_UNKNOWN` is in the labels). Recompute from the current scores with `band_of(max_score(...))`: the title keeps the decision-time band.
  4. `CVSS: 8.1 (CVSS 3.1, euvd); unknown (cisa_kev)`: one part per `Sourced` score in record order; `Sourced(None)` → `unknown (<source>)`; a score with no version → `8.1 (CVSS unknown, euvd)`; scores formatted with one decimal (`f"{score:.1f}"`). No score at all → `CVSS: unknown`.
  5. `Known exploited: yes, CISA KEV (ransomware use: known), EU KEV (ransomware use: unknown)` (one part per KEV entry, de-duplicated by catalog, CISA first; `known_ransomware_use` `True` → `known`, `None` → `unknown`); `Known exploited: no` when there is no entry.
  6. `Affected: Google Chrome < 141.0.7390.122; vendor product versions unknown`: one part per affected product (`vendor` omitted when `None`, `version_text` `None` → `versions unknown`), de-duplicated in order; `Affected: unknown` when there is none.
  7. `Description: <text>`: the first description in record order; `Description: unknown` when there is none.
  8. `Sources: euvd https://euvd.enisa.europa.eu/vulnerability/EUVD-…, cisa_kev https://www.cisa.gov/…`: every `RecordRef` in the link order above; a record without URL → `<source> <record_id>`.
  9. `References: <url>, <url>`: the vulnerability's references de-duplicated by value, at most 10, then `(+N more)`; the line is omitted when there are none.
- **Escaping:** set text with `element.text = value`; ElementTree escapes `&`, `<`, `>` (and quotes in attributes). Never build XML by string formatting, never use `CDATA`. Before setting any text, remove the characters XML 1.0 forbids (`[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]`, which also drops lone surrogates): ElementTree would otherwise write a document that no parser accepts. One private helper `_xml_text(value: str) -> str`.
- `etag_of` is the only ETag construction site; `run()` and the app's empty-feed path both use it.

### Pipeline additions (`pipeline/run.py`, `pipeline/summary.py`)

`run()` changes (same signature; add `feeds: Mapping[str, RenderedFeed]` to `RunResult`, keyed by endpoint id):

```
with state.transaction() as repository:
    ... (unchanged: correlate, cursors, decisions)
    publications = repository.publications_since([e.id for e in endpoints], now - PUBLICATION_WINDOW)
    missing = {p.vulnerability_id for ps in publications.values() for p in ps} - vulnerabilities.keys()
    vulnerabilities |= {id: build_vulnerability(normalize_all(records)) for id, records in repository.load_records(missing).items()}
    previous = repository.feeds()
    for endpoint in endpoints:                      # enabled only, config order
        entries = [FeedEntry(..., vulnerability=vulnerabilities[p.vulnerability_id]) for p in publications.get(endpoint.id, [])]
        xml = render_feed(endpoint, entries)
        etag = etag_of(xml)
        old = previous.get(endpoint.id)
        changed_at = old.changed_at if old is not None and old.etag == etag else now
        repository.save_feed(StoredFeed(endpoint.id, xml.decode("utf-8"), etag, len(entries), now, changed_at))
        feeds[endpoint.id] = RenderedFeed(endpoint.id, xml, etag, len(entries), now, changed_at)
```

- The decisions of this run are rendered in the same run (they were added before the query, in the same transaction).
- Every enabled endpoint is rendered at every run (cheap: a few hundred items at most), so the 14-day window slides even when nothing new is published, and a renamed endpoint gets its new channel title.
- `RunResult.feeds` holds the feeds of **this** run only; existing tests that build or compare `RunResult` keep working (add the field last, with no default needed since only `run()` constructs it; check `tests/unit/pipeline/test_run.py`).

New in `pipeline/run.py`:

```python
@dataclass(frozen=True, slots=True)
class SourceOutcome:
    source: str                      # adapter name
    result: FetchResult | None       # None: the fetch failed
    error_type: str | None = None    # SourceError subclass name when it failed

@dataclass(frozen=True, slots=True)
class CycleResult:
    snapshot: ConfigSnapshot         # the snapshot this cycle used (maybe reloaded)
    run: RunResult

def load_feeds(state: State) -> dict[str, RenderedFeed]           # read-only transaction; xml.encode("utf-8")
def reload_config(provider: ConfigProvider, current: ConfigSnapshot) -> ConfigSnapshot
def cycle(state: State, provider: ConfigProvider, snapshot: ConfigSnapshot,
          outcomes: Sequence[SourceOutcome], clock: Clock) -> CycleResult
```

- `reload_config`: `provider.revision()`; equal to `current.revision` → `current`. Otherwise `provider.load()` → log `config.reloaded` (info, `count=len(endpoints)`) and return it. `ConfigValidationError` → log `config.rejected` (warning, `errors=list(exc.errors)`) and return `current`. `ConfigUnavailableError` (also raised by `revision()`) → log `config.unavailable` (warning, `error_type`) and return `current`. Never log the snapshot or its tokens.
- `cycle`: `started = time.monotonic()`; `snapshot = reload_config(...)`; `batches = [SourceBatch(o.source, o.result) for o in outcomes if o.result is not None]`; `result = run(state, snapshot, batches, clock)` (it may raise: let it propagate, the worker logs it); `log_summary(summarize(outcomes, snapshot, result, duration_ms))`; return `CycleResult(snapshot, result)`. Synchronous only; called through `asyncio.to_thread` by the app and directly by `poll --once` (also through `to_thread`, it runs inside `asyncio.run`).
- Keep `pipeline.completed` as it is (1.5 tests may assert it); `cycle.summary` is the operator-facing event.

`pipeline/summary.py`:

```python
@dataclass(frozen=True, slots=True)
class SourceReport:
    result: Literal["ok", "failed"]
    records: int | None = None       # ok only: len(result.records)
    skipped: int | None = None       # ok only: FetchResult.skipped (the adapter's count)
    error_type: str | None = None    # failed only

@dataclass(frozen=True, slots=True)
class CycleSummary:
    sources: Mapping[str, SourceReport]
    published: Mapping[str, int]     # every enabled endpoint, 0 included, config order
    score_unknown: int               # decisions labelled PassThrough.SCORE_UNKNOWN
    versions_unknown: int            # decisions labelled PassThrough.VERSIONS_UNKNOWN
    duration_ms: int

def summarize(outcomes, snapshot, result, duration_ms) -> CycleSummary
def log_summary(summary: CycleSummary) -> None
    # log.info("cycle.summary", sources={name: {...only the non-None fields}}, published={...},
    #          score_unknown=..., versions_unknown=..., count=<total published>, duration_ms=...)
```

- Two outcomes of the same source in one cycle (a queue drained after a backlog): the last one wins in `sources`; the records of both are applied by the run.
- `cycle.summary` is the event name the architecture fixes (it is not in the `<area>` list; do not rename it).

### App design (`app/`)

**`app/feed_cache.py`**

```python
@dataclass(frozen=True, slots=True)
class CachedFeed:
    endpoint_id: str
    body: bytes
    etag: str
    last_modified: str        # format_datetime(changed_at, usegmt=True)
    changed_at: datetime
    item_count: int
    rendered_at: datetime | None    # None: empty placeholder, never rendered by a run

@dataclass(frozen=True, slots=True)
class FeedCache:
    by_token: Mapping[str, CachedFeed]       # MappingProxyType; field(repr=False): never print tokens
    by_endpoint: Mapping[str, CachedFeed]

    @classmethod
    def build(cls, snapshot: ConfigSnapshot, feeds: Mapping[str, RenderedFeed], now: datetime) -> FeedCache
    def get(self, token: str) -> CachedFeed | None

class FeedCacheHolder:
    """The single mutable reference; `current` is replaced, never mutated."""
    current: FeedCache
```

- `build`: for each `token, endpoint_id` in `snapshot.tokens`, take the endpoint; skip disabled ones; use `feeds[endpoint_id]` when present, else an **empty channel** rendered with `render_feed(endpoint, ())` and `etag_of`, `changed_at = now`, `item_count = 0`, `rendered_at = None`. Both maps are read-only (`MappingProxyType`). The empty path covers a new endpoint (or a first start) until its first run.
- A frozen dataclass with `repr=False` on `by_token` keeps tokens out of any `repr()` in logs and tracebacks.

**`app/public_api.py`**: `build_public_app(holder: FeedCacheHolder, clock: Clock) -> Starlette`

- One `Route("/feeds/{token}.xml", feed, methods=["GET"])`: Starlette adds `HEAD` to a `GET` route. Path parameter regex `[^/]+` then `.xml`: tokens have no dot.
- `exception_handlers={404: _empty(404), 405: _empty(405)}` returning `Response(status_code=...)` with no body (Starlette's defaults write "Not Found" / "Method Not Allowed"). Keep the `Allow` header on 405 (it is on `exc.headers`).
- Handler: `cached = holder.current.get(request.path_params["token"])`; `None` → `log.info("feed.not_found", method=...)`, `Response(status_code=404)`. Headers: `ETag`, `Last-Modified`, `Cache-Control: max-age=300`. Conditional: when `If-None-Match` is present, it alone decides (`*` matches; otherwise split on `,`, strip spaces and a `W/` prefix, compare with the ETag); else `If-Modified-Since` parsed with `email.utils.parsedate_to_datetime` (ignore unparsable values and naive results) matches when `changed_at.replace(microsecond=0) <= since`. Match → `Response(status_code=304, headers=...)`. Otherwise `200` with `media_type=MEDIA_TYPE`; for `HEAD`, an empty body with `Content-Length` set to `len(cached.body)` explicitly (uvicorn drops HEAD bodies, but `httpx.ASGITransport` in tests does not).
- Access log after the response is built: `log.info("feed.served", endpoint_id=cached.endpoint_id, method=request.method, status=..., duration_ms=...)` using `time.monotonic()`. Never log `request.url`, `request.path_params` or headers.
- No other route, no middleware, no `debug=True`.

**`app/private_api.py`**

```python
@dataclass
class SourceStatus:          # mutated only on the event loop
    name: str
    interval: timedelta
    last_success_at: datetime | None = None
    last_error_type: str | None = None
    last_error_at: datetime | None = None
    cursor: str | None = None

@dataclass
class RuntimeStatus:         # mutated only on the event loop
    started_at: datetime
    ready: bool = False
    sources: dict[str, SourceStatus] = field(default_factory=dict)

    def is_stale(self, source: SourceStatus, now: datetime) -> bool
        # now - (source.last_success_at or started_at) > 3 * source.interval

def build_private_app(status: RuntimeStatus, holder: FeedCacheHolder, clock: Clock) -> Starlette
```

- Routes `GET /healthz`, `/readyz`, `/status` (`JSONResponse`). 404 handler → `{"error": {"code": "not_found", "message": "no such endpoint"}}`; 405 → `{"error": {"code": "method_not_allowed", ...}}`; `/readyz` not ready → 503 `{"error": {"code": "not_ready", "message": "configuration or feed cache not loaded"}}` (during shutdown: `"shutting down"`).
- `/status` body: `{"sources": {name: {"interval_seconds": int, "last_success_at": iso|null, "last_error": {"type": str, "at": iso}|null, "cursor": str|null, "stale": bool}}, "endpoints": {endpoint_id: {"item_count": int, "last_render_at": iso|null}}}` with `endpoints` from `holder.current.by_endpoint`. One private `_iso(dt)` helper: `dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")`. No token, no feed path, no config revision.
- Every handler is synchronous-safe and reads in-memory objects only.

**`app/servers.py`**

```python
class EmbeddedServer(uvicorn.Server):
    """uvicorn without its signal handling: the runtime owns SIGTERM/SIGINT."""
    @contextmanager
    def capture_signals(self) -> Generator[None]:
        yield

def build_server(app: ASGIApp, listener: ListenerSettings) -> EmbeddedServer
    # uvicorn.Config(app, host=..., port=..., log_config=None, access_log=False,
    #                server_header=False, proxy_headers=False, lifespan="off",
    #                timeout_graceful_shutdown=5)
```

- Why: `uvicorn.Server.serve()` wraps itself in `capture_signals()`, which installs its own SIGINT/SIGTERM handlers and **re-raises the captured signal** after shutdown (uvicorn 0.54 `server.py`); with two embedded servers and our own handlers that would fight. Overriding `capture_signals` is the supported embedding pattern (verify the method name in `.venv/lib/python3.14/site-packages/uvicorn/server.py` and pin it with a unit test).
- `log_config=None`: uvicorn must not call `logging.config.dictConfig` (it would reconfigure logging). `access_log=False`: its access log writes the request path, which contains the token.
- Start: `task = asyncio.create_task(server.serve())`, then wait until `server.started` (poll with a short `asyncio.sleep`, bounded) or the task ends. On a bind failure uvicorn logs and calls `sys.exit(1)` **inside** `startup()`: the task ends with `SystemExit`; the runtime turns it into `app.failed` (error, `listener="public"|"private"`) and exit code 1. Stop: `server.should_exit = True`, then `await task`.

**`app/scheduler.py`**

```python
JITTER = 0.1                 # up to +10 % of the interval
CRASH_BACKOFF_BASE = 1.0     # seconds, doubled per consecutive crash
CRASH_BACKOFF_CAP = 300.0

class PipelineWorker:
    def __init__(self, state, provider, snapshot, holder, status, clock, *, run_cycle=cycle) -> None
    async def submit(self, outcome: SourceOutcome) -> bool       # waits for the run; True when it committed
    async def work(self, stop: asyncio.Event) -> None             # the worker loop (not `run`: that name is the pipeline function)

async def source_loop(scheduled: ScheduledSource, worker, state, status, clock, *,
                      random: Callable[[], float], sleep: Callable[[float], Awaitable[None]],
                      iteration_done: Callable[[], None]) -> None
async def supervise(name: str, loop_factory: Callable[[Callable[[], None]], Awaitable[None]], *,
                    sleep: Callable[[float], Awaitable[None]], stop: asyncio.Event) -> None
    # loop_factory(iteration_done) builds the source loop; the loop calls iteration_done() after each completed iteration
```

- **Source loop**, forever: `cursor = (await asyncio.to_thread(load_cursors, state)).get(name)`; set `status.cursor`; `result = await adapter.fetch(cursor)`. On `SourceError` → `log.warning("source.failed", source=..., error_type=..., message=str(exc))`, `status.last_error_type/at`, then `source.stale` when `status.is_stale(...)`, then `await worker.submit(SourceOutcome(name, None, error_type))`. On success → `log.info("source.fetched", source=..., count=len(records), skipped=result.skipped, duration_ms=...)`, `committed = await worker.submit(SourceOutcome(name, result))`; when `committed`: `last_success_at = clock.now()` and `status.cursor = result.next_cursor` if not `None`. Then `await sleep(interval_seconds * (1 + JITTER * random()))`. The first fetch happens right after startup.
- **Why the loop re-reads the cursor and waits for its run:** a cursor kept in memory would advance even when the run that stores it is rolled back, and the next fetch would skip the records of the failed run. Re-reading the stored cursor makes "the next cycle is the retry" true.
- **Worker loop:** wait for the first job or `stop`; drain every queued job (`get_nowait`) into one cycle; `result = await asyncio.to_thread(run_cycle, state, provider, snapshot, outcomes, clock)`; on success: `self.snapshot = result.snapshot`, `holder.current = FeedCache.build(result.snapshot, result.run.feeds, clock.now())`, set every job's future to `True`. `except Exception` (this is a supervisor): `log.error("pipeline.failed", exc_info=True, error_type=...)`, futures set to `False`, keep the previous snapshot and cache. **Never cancel the worker while a run is in its thread** (cancelling the awaiting task would not stop the thread, which could commit after the state is disposed): check `stop` only between runs, and let the runtime await the worker task.
- **Supervisor:** `while not stop.is_set(): try: await loop_factory() except asyncio.CancelledError: raise except Exception: log.error("source.crashed", source=name, exc_info=True); await sleep(backoff); backoff = min(backoff * 2, CRASH_BACKOFF_CAP)`. The supervisor passes an `iteration_done` callback into `loop_factory`; `source_loop` calls it after every completed iteration (fetch, submit and sleep), and the callback resets the backoff to `CRASH_BACKOFF_BASE`. `source_loop` therefore also takes `iteration_done: Callable[[], None]`.
- All waits and randomness are injected (`sleep`, `random`, `clock`): tests never sleep for real.

**`app/runtime.py`**

```python
async def serve(settings: Settings, *, clock: Clock = SystemClock(), stop: asyncio.Event | None = None,
                random: Callable[[], float] = random_module.random, ...) -> int
async def poll_once(settings: Settings, *, clock: Clock = SystemClock(), ...) -> int
```

- `serve`, in order (AC 4): `state = State(state_url(settings))`; `await asyncio.to_thread(state.migrate)`; start the private server; `provider = YamlConfigProvider(settings.endpoints_file)`; `snapshot = await asyncio.to_thread(provider.load)` (`ConfigUnavailableError` → `log.error("config.unavailable", ...)`, return 3; `ConfigValidationError` → `log.error("config.rejected", errors=...)`, return 2); `feeds = await asyncio.to_thread(load_feeds, state)`; `holder.current = FeedCache.build(snapshot, feeds, clock.now())`; open `HttpClient()`; `build_sources(settings.sources, http, clock)`; start the worker and one supervised loop per source (`log.info("app.started", sources=[...])`); start the public server; `status.ready = True`; `log.info("app.ready")`; wait for `stop`.
- Signals: `loop.add_signal_handler(signal.SIGTERM, stop.set)` and `SIGINT` (only when `stop` was not injected by a test); remove them on exit.
- Shutdown (AC 11): `status.ready = False`; `log.info("app.stopping")`; cancel the source loop tasks and await them; let the worker finish its current run and return; stop the public then the private server; close the `HttpClient`; `state.dispose()`; `log.info("app.stopped")`; return 0. Use `try/finally` so a startup failure also disposes the state and stops what was started.
- `poll_once`: migrate, load the config (same codes), `async with HttpClient()`: fetch each scheduled source once (sequentially; `SourceError` → failed outcome, logged `source.failed`), then `await asyncio.to_thread(cycle, state, provider, snapshot, outcomes, clock)`, dispose, return 0. A cycle exception propagates (exit 1 through the CLI).
- Settings or state errors raised here propagate as `SettingsError` / `StateError`: the CLI turns them into one line and exit 2.

### CLI design (`cli/main.py`, `cli/healthcheck.py`)

- `@app.command() def run() -> None`: `settings = load_settings()` (`SettingsError` → `_fail`), `configure_logging(settings.log.level, settings.log.format)`, then `from cvestream.app.runtime import serve` **inside the function**, `code = asyncio.run(serve(settings))` (`StateError` → `_fail`), `raise typer.Exit(code)` when non-zero.
- `@app.command() def poll(once: Annotated[bool, typer.Option("--once", help=...)] = False)`: without `--once` → `_fail("poll runs a single cycle: pass --once (the daemon is `cvestream run`)")`; else like `run` with `poll_once`.
- `asyncio.run` appears only here (CLI entry point).
- `healthcheck` lives in `cli/healthcheck.py` as a function registered with `app.command("healthcheck")(healthcheck)`: options `--ready` and `--port` (default 9090). `opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))`; `opener.open(f"http://127.0.0.1:{port}/{'readyz' if ready else 'healthz'}", timeout=2.0)`; status 200 → exit 0 silently; `HTTPError`, `URLError`, `OSError`, `TimeoutError` → `typer.echo(<one line: path + reason class>, err=True)` and exit 1. ruff `S310` flags `urlopen` with a variable URL: the scheme is fixed to `http://127.0.0.1`, so a narrow `# noqa: S310` with that reason is acceptable. Import only stdlib and `typer` in this module.
- **`--debug` (deferred from 1.5):** construct the root `typer.Typer(..., pretty_exceptions_enable=False)`; the root callback also records `debug` in a module-level `ContextVar[bool]`; `main()` wraps `app()`: `except Exception as exc` (the CLI's top-level supervisor) → re-raise when debug, else `typer.echo(f"unexpected error: {type(exc).__name__} (run with --debug for the traceback)", err=True)` and `raise SystemExit(1) from None`. Do not print the exception message (it may quote input). `typer.Exit` / `SystemExit` / click usage errors are not `Exception` subclasses caught here in a way that changes their codes: test exit codes 0, 1, 2 and 3.
- The existing `config` and `db` sub-apps stay as they are; their module-level imports (YAML provider, SQLAlchemy) are acceptable for `healthcheck` (no app, no HTTP stack). Pin the AC 12 import rule with a subprocess test: `python -c "import cvestream.cli.main, sys; print(sorted(m for m in ('cvestream.app', 'starlette', 'uvicorn', 'httpx') if m in sys.modules))"` prints `[]`.

### Compose files

`compose.yml` (dev; `docker compose watch`):

```yaml
services:
  app:
    build: {context: ., target: dev}
    command: ["watchfiles", "cvestream run", "src"]
    environment:
      CVESTREAM_ENDPOINTS_FILE: ${CVESTREAM_ENDPOINTS_FILE:-endpoints.example.yaml}
      CVESTREAM_LOG__FORMAT: console
    ports: ["127.0.0.1:8080:8080", "127.0.0.1:9090:9090"]
    develop: (unchanged watch rules, plus a sync of ./endpoints.example.yaml to /app/endpoints.example.yaml)
```

- `endpoints.example.yaml` is in the image (`.dockerignore` excludes only `endpoints.yaml`) and its placeholder tokens are valid: the dev stack serves `http://127.0.0.1:8080/feeds/REPLACE-ME-with-secrets-token-urlsafe-32-team-php.xml` out of the box. Keep the existing header comment ("Valkey joins in story 3.1").

`deploy/compose/compose.yml` (self-hosted):

```yaml
services:
  cvestream:
    image: ghcr.io/wixyvir/cvestream:edge   # pin a release tag (X.Y.Z) once release.yml publishes one (story 3.3)
    restart: unless-stopped
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    stop_grace_period: 60s                    # lets the current pipeline run finish
    ports: ["8080:8080"]                       # only the public listener; 9090 stays inside
    environment:
      CVESTREAM_CONFIG_PROVIDER: yaml
      CVESTREAM_ENDPOINTS_FILE: /config/endpoints.yaml
    volumes:
      - data:/data
      - ./config:/config:ro
volumes:
  data:
```

- Mount the **directory** `./config`, not the single file: editors replace a file on save (new inode) and a single-file bind mount keeps showing the old content, which would silently break hot reload. Say so in a comment and in `docs/operations.md`.
- The named volume `data` is initialised from the image's `/data` (owned by 999), so the daemon can write `cvestream.db` with a read-only root filesystem. The image's `HEALTHCHECK` applies; no `healthcheck:` block needed.
- Comments: HTTPS comes from the operator's reverse proxy in front of 8080; the reverse proxy logs contain the token.

### Operations doc outline (`docs/operations.md`)

TL;DR first (5 to 8 lines), then: **Run** (local `uv run cvestream run`, `poll --once`; container; `deploy/compose`); **Startup and shutdown** (order, exit codes 0/1/2/3, SIGTERM finishes the current run, grace period 60 s); **Health** (`/healthz`, `/readyz`, `cvestream healthcheck [--ready] [--port]`, the image `HEALTHCHECK`); **`/status`** (the JSON shape, staleness rule); **Logs** (JSON lines, the event names of this story with their fields, `cycle.summary` fields, `source.stale`, `config.rejected`); **Feeds** (URL, caching headers, `304`, 14-day window, `[UPDATE]` items, what the body shows, an empty channel until the first run); **Token rotation** (edit the token in `endpoints.yaml`; the old URL returns 404 after the next cycle; tell the team to resubscribe); **Security** (only 8080 public; tokens never logged by cvestream; **reverse-proxy and Ingress access logs contain the token: disable or mask path logging for `/feeds/`**; rate limiting belongs to the proxy); **Backup** (`docker compose exec cvestream cvestream db backup /data/backup-<date>.db`, then copy it out; restore = stop, replace `cvestream.db`, start); **Limits** (single replica only; never two daemons on one database; do not run `poll --once` against the database of a running daemon: both would write).

### Testing requirements

New test files (each new directory gets an `__init__.py`): `tests/unit/pipeline/test_render.py`, `tests/unit/pipeline/test_summary.py`, `tests/unit/pipeline/test_cycle.py` (or additions to `test_run.py`), `tests/unit/app/{__init__,test_feed_cache,test_public_api,test_private_api,test_servers,test_scheduler,test_runtime}.py`, `tests/unit/cli/test_healthcheck.py`, `tests/unit/cli/test_run.py` (run/poll exit codes, `--debug`), `tests/integration/test_pipeline_end_to_end.py`; additions to `tests/unit/state/test_repository.py`, `test_schema.py`, `test_db.py`, `tests/unit/test_settings.py`.

- **HTTP tests without sockets:** `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")` under `@pytest.mark.anyio`. Do **not** use `starlette.testclient.TestClient`: in Starlette 1.10 it wants `httpx2` and warns (`StarletteDeprecationWarning`) with plain `httpx`; adding `httpx2` is not in the architecture.
- **render:** a full entry (two scores incl. `Sourced(None)`, a score without version, CISA KEV with ransomware `True` and EU KEV with `None`, affected with and without vendor/version, references > 10, two record URLs) gives the exact expected description lines; `new` / `kev` / `band-high` titles and the `[UPDATE]` prefix; `unknown` everywhere data is missing (no score, no KEV, no affected, no description, no IDs); `guid` equals the input GUID and has `isPermaLink="false"`; `pubDate` is RFC 822 with `GMT`; items in input order; `lastBuildDate` absent for no items and equal to the newest item otherwise; same input → identical bytes; a description with `<script>&` round-trips as text after `ET.fromstring`; control characters (`\x00`, `\x0b`, `\x1f`, a lone surrogate) are removed and the output parses; the document parses with `ET.fromstring` and has `rss/@version == "2.0"` and the required channel elements; `etag_of` is quoted and changes when one byte changes.
- **repository / schema / migrations:** `feed_cache` naming (`pk_feed_cache`, `ux_feed_cache_endpoint_id`); migrate to `0002` from an empty database **and** from a database at `0001` with data (upgrade keeps existing rows); metadata equals migrations; `save_feed` insert then update; `feeds()`; `publications_since` window boundary (exactly at `since` included), per-endpoint grouping, newest first, labels and band round-trip.
- **run / cycle / summary:** after a run, every enabled endpoint has a `feed_cache` row and `RunResult.feeds` entry, disabled endpoints have none; an unchanged rerun keeps `etag` and `changed_at` and moves `rendered_at`; a new decision changes both; a publication older than 14 days drops out of the feed; a failure after rendering rolls back the feed rows too (monkeypatch `save_feed` to raise on the second endpoint); `load_feeds` returns what `run` stored; `reload_config` with the same revision returns the same object, with a valid change logs `config.reloaded`, with invalid YAML logs `config.rejected` and returns the old snapshot, with a deleted file logs `config.unavailable` (use a real `YamlConfigProvider` in `tmp_path`); `cycle` logs exactly one `cycle.summary` with the failed source, per-endpoint zeros and the pass-through counts (`structlog.testing.capture_logs()`).
- **feed_cache / public API:** 200 headers (exact content type, `Cache-Control: max-age=300`, ETag, `Last-Modified` in GMT); `HEAD` has the headers, `Content-Length` of the full body and an empty body; `If-None-Match` exact, weak (`W/"..."`), list and `*` → 304; non-matching ETag with a matching `If-Modified-Since` → 200 (ETag wins); `If-Modified-Since` alone, equal and later → 304, earlier and unparsable → 200; unknown token, disabled endpoint's token, `/`, `/feeds/x`, `/feeds/<token>.rss` → 404 with empty body; `POST` → 405 with empty body; a swapped holder serves the new content and a rotated token 404s; captured logs contain `endpoint_id` and never the token or `/feeds/` (assert on `str(event)` of every captured entry); `FeedCache.build` gives an empty channel for an endpoint without a stored feed and nothing for a disabled one; `repr()` of a `FeedCache` contains no token.
- **private API:** `/healthz` 200; `/readyz` 503 then 200 after `status.ready = True`; `/status` shape, `Z` datetimes, `stale` computed with `FakeClock`; unknown path → JSON 404; no token in any body.
- **servers:** `build_server` sets `access_log=False`, `log_config=None`, `server_header=False`, `proxy_headers=False`, `lifespan="off"`; `EmbeddedServer.capture_signals()` installs nothing (check `signal.getsignal(signal.SIGTERM)` before and after). Do not bind a port in unit tests.
- **scheduler (fake adapters, injected `sleep`/`random`, `FakeClock`, real `state` fixture):** a successful fetch submits one outcome, records `last_success_at` and the stored cursor; a `SourceError` logs `source.failed`, leaves the stored cursor unchanged and the other source still fetches; the cursor passed to the next fetch is the stored one, not the failed run's `next_cursor` (make `run_cycle` raise once); `source.stale` appears only past `3 × interval`; the sleep receives `interval × (1 + 0.1 × random())`; a loop raising `RuntimeError` is logged `source.crashed` with `exc_info` and restarted after 1, 2, 4 s (capped at 300) as seen by the injected sleep; the worker drains several queued outcomes into one cycle, resolves every future, swaps the holder on success and keeps it on failure (`pipeline.failed`).
- **runtime:** with a fake server factory (records start/stop order and lets the test check readiness at each step), fake adapters and an injected `stop` event: the order of AC 4; exit 3 for a missing endpoints file and exit 2 for an invalid one, with the private server stopped and the state disposed; `stop.set()` → `ready` false first, loops cancelled, a run in progress completes before the servers stop, exit 0; `poll_once` returns 0 and logs one `cycle.summary` with a failed source included.
- **CLI:** `healthcheck` against a fake opener (monkeypatch `urllib.request.build_opener` or inject the opener) → 0 on 200, 1 on 503, `URLError` and timeout, path `/readyz` with `--ready`, port from `--port`; the subprocess import test (AC 12); `poll` without `--once` → 2; `run` with a missing endpoints file → 3 (monkeypatch `serve` or use a real temp dir with `CVESTREAM_DATA_DIR`); an unexpected exception → 1, one stderr line without traceback, and a traceback with `--debug`.
- **integration `test_pipeline_end_to_end.py`:** reuse the respx setup of `test_restart_no_repost.py` (recorded `euvd_search_page0.json`, `euvd_kev_dump.json`, `cisa_kev_catalog.json`, `FakeClock` at `NOW`, endpoint with product `Chrome` and a 43-character token); `cycle()` with the two outcomes; `FeedCache.build(...)`; then through the public app: 200 RSS that parses, items whose `guid` set equals the stored publication GUIDs, a title naming `Chrome` and a band, a body with the CVE ID, `CVSS:` and `Known exploited:` lines; `If-None-Match` → 304; `HEAD`; unknown token → 404; restart (`state.dispose()`, new `State`, `load_feeds`, new `FeedCache`) → same bytes and ETag.
- Determinism: `FakeClock` everywhere, injected randomness, no `datetime.now()`, no real sleeps, no TCP. A real-socket smoke test is **not** required (the manual checks of Task 12 cover the listeners); if you add one, mark it `@pytest.mark.enable_socket` with `@pytest.mark.allow_hosts(["127.0.0.1"])` and bind port 0.

### Architecture compliance

- Feed cache and active config swapped by replacing the whole reference; the event loop never touches SQLite or the provider (`asyncio.to_thread` for `migrate`, `load`, `load_cursors`, `load_feeds`, `cycle`).
- One `State.transaction()` per run, opened only in `pipeline/run.py`; decisions, publication rows, cursors and `feed_cache` in it. No `commit()` anywhere.
- GUIDs are read from `publication.guid`, never rebuilt (`grep -rn "cvestream:{" src/` must still show one site, in `domain/publication.py`).
- `except Exception` only in the supervisors: source loop supervisor, pipeline worker, CLI `main()`; always logged with `exc_info` (the CLI prints one line instead and shows the traceback under `--debug`).
- Logs: `<area>.<past-participle>` (`source.fetched`, `source.failed`, `source.stale`, `source.crashed`, `pipeline.failed`, `config.reloaded`, `config.rejected`, `config.unavailable`, `feed.served`, `feed.not_found`, `app.started`, `app.ready`, `app.stopping`, `app.stopped`, `app.failed`) plus the fixed `cycle.summary`; standard fields (`source`, `endpoint_id`, `count`, `duration_ms`, `error_type`). Never `token`, never a feed path, never a request URL.
- Unknown is `None` in data and `unknown` in the feed and `null` in JSON.
- Public `404` empty body; private errors `{"error": {"code", "message"}}`; JSON `snake_case`; no custom headers.
- Modules at the architecture-tree paths (`app/runtime.py`, `scheduler.py`, `feed_cache.py`, `public_api.py`, `private_api.py`, `servers.py`, `pipeline/render.py`, `summary.py`, `cli/healthcheck.py`, `deploy/compose/compose.yml`, `docs/operations.md`); no `utils.py`.

### Library / framework requirements

| Package | Version | Use |
|---|---|---|
| Starlette | 1.10.0 (`>=1.10.0`; latest on PyPI, 2026-10-24; Python ≥ 3.11; depends on `anyio>=4,<5`) | `Starlette(routes=..., exception_handlers=...)`, `Route(path, endpoint, methods=["GET"])` (adds `HEAD`), `Request`, `Response`, `JSONResponse`, `HTTPException` in handlers. Not `TestClient` (needs `httpx2`). |
| uvicorn | 0.54.0 (`>=0.54.0`; latest, 2026-09-25; plain, pulls `click` and `h11`) | `uvicorn.Config(...)`, `uvicorn.Server` subclass with a no-op `capture_signals`, `server.serve()`, `server.started`, `server.should_exit`. Not `uvicorn[standard]` (no uvloop/httptools needed). |
| httpx | 0.28.1 (already) | `httpx.ASGITransport` in tests; `HttpClient` at runtime. |
| stdlib | Python 3.14 | `xml.etree.ElementTree`, `email.utils.format_datetime` / `parsedate_to_datetime`, `hashlib`, `urllib.request`, `signal`, `asyncio`. |

No other dependency: no `feedgen`, no `lxml`, no `defusedxml` (we only parse our own output, in tests), no `fastapi`, no `httpx2`, no `aiosqlite`.

### File structure requirements

New:

```
src/cvestream/pipeline/render.py
src/cvestream/pipeline/summary.py
src/cvestream/state/migrations/versions/0002_add_feed_cache.py
src/cvestream/app/runtime.py
src/cvestream/app/scheduler.py
src/cvestream/app/feed_cache.py
src/cvestream/app/public_api.py
src/cvestream/app/private_api.py
src/cvestream/app/servers.py
src/cvestream/cli/healthcheck.py
deploy/compose/compose.yml
docs/operations.md
tests/unit/pipeline/test_render.py, test_summary.py, test_cycle.py
tests/unit/app/__init__.py, test_feed_cache.py, test_public_api.py, test_private_api.py, test_servers.py, test_scheduler.py, test_runtime.py
tests/unit/cli/test_healthcheck.py, test_run.py
tests/integration/test_pipeline_end_to_end.py
```

Modified: `pyproject.toml`, `uv.lock`, `.gitignore` (`/deploy/compose/config/`), `src/cvestream/settings.py`, `src/cvestream/state/schema.py`, `src/cvestream/state/repository.py`, `src/cvestream/pipeline/run.py`, `src/cvestream/cli/main.py`, `src/cvestream/app/__init__.py` (docstring only), `Dockerfile`, `compose.yml`, `settings.example.yaml`, `endpoints.example.yaml`, `docs/configuration.md`, `README.md`, `CLAUDE.md`, `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/implementation-artifacts/deferred-work.md`, tests listed in Task 3.3, `tests/unit/state/test_repository.py`, `tests/unit/test_settings.py`, this story file, `sprint-status.yaml`.

### Existing files this story touches (current state → change → preserve)

- `pipeline/run.py`: `SourceBatch`, `RunResult`, `load_cursors`, `run`. Change: render and store feeds inside the transaction, `RunResult.feeds`, add `SourceOutcome`, `CycleResult`, `load_feeds`, `reload_config`, `cycle`. Preserve the decision logic, the single transaction, `pipeline.completed`, "never catches".
- `state/schema.py`: five tables. Change: add `feed_cache`. Preserve everything else (and `0001` unchanged).
- `state/repository.py`: correlation, decision and cursor queries. Change: add the three methods and two dataclasses. Preserve the "no commit, no logging, no raw SQL" rules.
- `settings.py`: add `ListenerSettings`, `HttpSettings`, `Settings.http`. Preserve the custom sources, their order, `extra="forbid"` and every existing field.
- `cli/main.py`: root callback (`--debug`, `--version`), `config` and `db` sub-apps. Change: `pretty_exceptions_enable=False`, the debug `ContextVar`, `run`, `poll`, `healthcheck`, `main()` wrapper. Preserve `--version`, `no_args_is_help`, `add_completion=False`.
- `Dockerfile`: runtime `ENV ... CVESTREAM_DATA_DIR=/data`, `VOLUME /data`, `EXPOSE 8080 9090`, `USER 999:999`, `ENTRYPOINT ["cvestream"]`, `CMD ["--help"]`; dev `CMD ["watchfiles", "cvestream --help", "src"]`. Change: `HEALTHCHECK`, `CMD ["run"]`, dev `CMD`. Preserve the builder stage, pins and the non-root setup.
- `compose.yml`: dev `app` service, `develop.watch` sync of `src` and rebuild on `uv.lock`/`pyproject.toml`. Change as in "Compose files".
- `docs/configuration.md`, `settings.example.yaml`, `endpoints.example.yaml`, `README.md`, `CLAUDE.md`, `architecture.md`, `deferred-work.md`: only the lines of Task 13.

### Decisions a reviewer may want to revisit (recorded here and in architecture.md)

- **Title from the decision, body from current data.** The title shows the band, product and KEV state recorded at decision time (what was published); the body shows the current merged vulnerability. An item already read by Slack never changes there (Slack de-duplicates on GUID); other RSS readers see the refreshed body.
- **Plain-text description, one fact per line.** No HTML, no Slack markup. Some readers collapse the line breaks. Alternative: an escaped HTML fragment with `<br/>`. To check with Slack's RSS app during the trial (architecture M3).
- **Readiness = config + state + feed cache loaded**, independent of sources (architecture). A known token whose endpoint has no stored feed yet (first start, new endpoint) gets a valid **empty channel** rather than `404` or `503`, until its first run.
- **Stable ETag:** `lastBuildDate` is the newest item's date and `Last-Modified` the last content change, never the render time, so an unchanged feed answers `304`.
- **Every fetch outcome triggers a cycle**, failed ones included (they still reload the config, slide the window and appear in `cycle.summary`). The worker drains a backlog into one run.
- **Source status lives in memory** (`last_success_at`, `last_error`): lost on restart; staleness counts from the daemon start until the first success. Persisting it would need a table the epic does not list.
- **Startup exit codes:** endpoints file missing or unreadable → 3 (`config.unavailable`, like the Valkey rule); invalid → 2 (`config.rejected`, a user error). Settings or state errors → 2.
- **`poll --once` exits 0 when a source failed** (the summary shows it); only an unexpected error exits 1.
- **Listeners bind `0.0.0.0` by default** (container); only 8080 is published by the compose file. The healthcheck takes `--port` when the private port changes.
- **Channel link = the project URL** (the daemon does not know its public base URL); no `atom:link rel="self"`.

### Delivery (stacked PR)

- Branch `story-1-6-feeds-served-by-the-daemon`, created from `story-1-5-correlation-state-and-publication-decisions`. Draft PR base: the 1.5 branch (`epic-1-stack-plan.md`).
- Commits separated by concern, never squashed, each ending with a blank line and the attribution line. Suggested sequence: `chore(story-1.6): add starlette and uvicorn`; `feat(story-1.6): add http listener settings`; `feat(story-1.6): add feed cache table and migration`; `feat(story-1.6): add feed queries to the state repository`; `feat(story-1.6): add RSS renderer`; `feat(story-1.6): render feeds in the pipeline run`; `feat(story-1.6): add pipeline cycle, config reload and cycle summary`; `feat(story-1.6): add in-memory feed cache and public feed API`; `feat(story-1.6): add private health and status API`; `feat(story-1.6): add source scheduler and pipeline worker`; `feat(story-1.6): add daemon runtime and embedded servers`; `feat(story-1.6): add run, poll and healthcheck commands`; `fix(story-1.6): show CLI tracebacks only with --debug`; `test(story-1.6): add pipeline end-to-end test`; `chore(story-1.6): run the daemon in the image and dev compose`; `feat(story-1.6): add self-hosted compose deployment`; `docs(story-1.6): document operations, listeners and the daemon`; `chore(story-1.6): mark story ready for review`. Tests may go with each `feat` commit.

### Previous story intelligence (1.1 to 1.5)

- Gate = `uv sync --locked`, `ruff check` (`E,F,W,I,UP,B,DTZ,BLE,S,RUF`), `ruff format --check`, `ty check` (`error-on-warning`), `lint-imports`, `pytest` (sockets blocked with `--allow-unix-socket`, `--strict-markers`, `-m "not live"`), `config schema --check`, `config validate endpoints.example.yaml`. `assert` only in `tests/**` (`S101` ignored there). Baseline on the 1.5 branch: **700 passed, 2 deselected**.
- 1.5: `State` builds its engine lazily; `State.migrate()` maps `DatabaseError` and directory errors to `StateError`; the source-scan test allows `.transaction(` only in `pipeline/run.py`; `StateError` lives in `state/schema.py` (re-exported by `state/db.py`); `Repository.create_vulnerability` stores a placeholder `primary_id` refreshed before commit; `ty` needed narrow typing in tests. 1.5 logged `state.migrated` through structlog's default configuration in `db migrate`; `run` / `poll` configure logging first.
- 1.5 pending decisions (GUID collision of advisory-only IDs, `unknown` → `none` re-publish) and deferred items (EU KEV dump blocking EUVD search; SQLite deferred transactions with a second writer) are not this story's; the deferred SQLite note is why `docs/operations.md` warns against `poll --once` on a running daemon's database.
- 1.4: adapters stamp one `observed_at` per fetch; the EUVD adapter re-emits records equal to the cursor (idempotent downstream); `FetchResult.skipped` counts record-level failures (report it per source in `cycle.summary`); `HttpClient` is an async context manager and must be closed; `EuvdAdapter` takes an injectable `sleep`.
- 1.3: error messages never quote input values (tokens); `ConfigSnapshot.tokens` has `repr=False`; `ConfigSnapshot` keeps disabled endpoints (the cache must skip them). The YAML revision changes on any byte (comments included).
- 1.1: `log.py` redaction is a safety net; deferred "tokens hidden in non-string log values" is addressed here by never passing request objects or URLs to the logger.

### Git intelligence

Recent commits on the base branch: `chore(story-1.5): record code review`, `fix(story-1.5): make db backup never overwrite and never leave a partial file`, `fix(story-1.5): report an invalid state URL or database as a user error`, `chore(story-1.5): mark story ready for review`, `docs(story-1.5): document state settings and publication rules`, `chore(story-1.5): set the data directory in the runtime image`, `test(story-1.5): add restart no-repost guardrail`, `feat(story-1.5): add db migrate and db backup commands`. Pattern: Conventional Commits scoped `(story-1.x)`, one concern per commit, dependency changes in a `chore` commit with `pyproject.toml` + `uv.lock`, image changes in `chore`, docs in `docs`, review fixes in `fix`.

### Latest technical information (checked 2026-10-26)

- **Starlette 1.10.0** (PyPI, 2026-10-24) and **uvicorn 0.54.0** (2026-09-25) are the latest releases and match the architecture's pins.
- Starlette 1.10 `starlette/testclient.py` imports `httpx2` and falls back to `httpx` with a `StarletteDeprecationWarning`: test through `httpx.ASGITransport` instead.
- Starlette `Route(..., methods=["GET"])` adds `HEAD` automatically; Starlette does not strip HEAD bodies (uvicorn's h11 protocol does), hence the explicit empty HEAD body. A non-matching path inside a `Starlette` app raises `HTTPException(404)`, so an exception handler keyed on `404` replaces the default "Not Found" body; same for `405`.
- uvicorn 0.54 `Server.serve()` runs inside `capture_signals()`, which installs SIGINT/SIGTERM handlers with `signal.signal` and re-raises captured signals after shutdown; on a bind error `startup()` logs and calls `sys.exit(1)`. `Config(log_config=None)` skips `dictConfig`; `access_log=False` disables the `uvicorn.access` logger.
- pytest-socket 0.8.1 registers the `enable_socket` and `allow_hosts` markers (only needed if a real-socket test is added).

### Project Structure Notes

- Aligned with the architecture tree: every new module is listed there (`app/*`, `pipeline/render.py`, `pipeline/summary.py`, `cli/healthcheck.py`, `deploy/compose/compose.yml`, `docs/operations.md`, `tests/integration/test_pipeline_end_to_end.py`).
- Variance: migration number `0002` (the architecture's naming example said `0003_add_feed_cache.py`; amended in Task 13.7). The `http` settings section is new and amended into "Runtime configuration".
- `app/feed_cache.py` holds the in-memory map; the `feed_cache` **table** is in `state/schema.py`. Do not confuse them.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.6: Feeds served by the daemon]
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-4, FR-6, FR-17, FR-18, FR-20, FR-23, FR-24, NFR-1, NFR-2, NFR-4, NFR-5)
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns] (process model, startup behaviour, routes, CLI, `cvestream healthcheck`)
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Architecture] (feed cache, logical tables)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] (feed access, interfaces, logs, container)
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (image, deployment targets, logging, delivery workflow)
- [Source: _bmad-output/planning-artifacts/architecture.md#Implementation Patterns & Consistency Rules] (logs as events, errors, async/sync, staleness, immutability)
- [Source: _bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries] (tree, component boundaries, integration points, data flow)
- [Source: _bmad-output/project-context.md] (HTTP, Logging, Security, Failure isolation, Runtime gotchas, Testing Rules)
- [Source: _bmad-output/implementation-artifacts/epic-1-stack-plan.md#3.6 Story 1.6] (manual checks the reviewer runs)
- [Source: _bmad-output/implementation-artifacts/1-5-correlation-state-and-publication-decisions.md] (state, run, decisions, review findings)
- [Source: _bmad-output/implementation-artifacts/1-4-euvd-and-kev-ingestion.md] (adapters, cursors, `HttpClient`)
- [Source: _bmad-output/implementation-artifacts/1-3-endpoint-configuration-in-yaml.md] (snapshot, tokens, YAML provider)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] (`--debug`, token redaction, EU KEV dump, SQLite second writer)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5.5 (claude-sonnet-5-5), run by the `bmad-dev-story` workflow.

### Debug Log References

- Baseline on the 1.5 branch: 700 passed. After this story: 919 passed, 2 deselected (`live`). Gates run from the worktree: `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports` (7 contracts kept), `pytest`, `config schema --check`, `config validate endpoints.example.yaml`, `docker build --target runtime`.
- Manual checks of Task 12.2 (local daemon, SQLite in a temporary directory, real network): `state.migrated`, `app.started`, `app.ready`; `/healthz` 200, `/readyz` 200, `/status` with the source and endpoint; `cvestream healthcheck --ready --port <port>` exits 0 and exits 1 against a closed port; the feed with `curl -i` (200, `ETag`, `Last-Modified`, `Cache-Control: max-age=300`, no `Server` header), `If-None-Match` gives 304, `curl -I` (HEAD) gives the headers and no body, a wrong token gives an empty 404, `POST` gives 405; rotating the token in `endpoints.yaml` served the new URL and returned 404 on the old one at the next cycle (`config.reloaded`); an invalid `endpoints.yaml` logged `config.rejected` and the feed kept being served; SIGINT logged `app.stopping` then `app.stopped` and exited cleanly; `poll --once` logged one `cycle.summary` and exited 0 although EUVD failed. `xmllint --noout` accepts the feed rendered from the recorded fixtures.
- **EUVD was not reachable from the development network** (`GET https://euvdservices.enisa.europa.eu/api/search: HTTP 403` after the retries), so no real EUVD item was served in the manual checks: that failure is itself the failure-isolation check (the CISA KEV source kept fetching, `poll --once` exited 0 with `euvd` as `failed` in `cycle.summary`). Feeds with items are covered by `tests/integration/test_pipeline_end_to_end.py` on the recorded EUVD and CISA KEV fixtures.
- Task 12.3: `docker build --target runtime -t cvestream:local .` passed; `docker run --rm cvestream:local --help` shows `run`, `poll` and `healthcheck`; `docker compose -f deploy/compose/compose.yml config` is valid; with the local image tagged as `ghcr.io/wixyvir/cvestream:edge` and a `config/endpoints.yaml`, `docker compose up -d` reached `healthy` (`docker inspect`), served the feed on the published port with the read-only root filesystem, `docker compose exec cvestream cvestream healthcheck --ready` exited 0, and `docker compose stop` stopped the container in under a second with exit code 0 (`app.stopping`, `app.stopped`).

### Completion Notes List

- **Implemented:** `pipeline/render.py` (RSS 2.0 with `xml.etree.ElementTree`, `etag_of`), `pipeline/summary.py` (`cycle.summary`), `pipeline/run.py` (feeds rendered and stored in the run's transaction, `load_feeds`, `reload_config`, `cycle`), the `feed_cache` table and migration `0002`, repository additions (`StoredPublication`, `StoredFeed`, `publications_since`, `feeds`, `save_feed`), the whole `app/` package (`feed_cache`, `public_api`, `private_api`, `servers`, `scheduler`, `runtime`), the `run`, `poll --once` and `healthcheck` commands, the `http` settings, `Dockerfile` (`CMD ["run"]`, `HEALTHCHECK`), the dev and self-hosted compose files, tests and docs (`docs/operations.md`, README quick start, settings and configuration docs, architecture amendments, `deferred-work.md`).
- **Tests:** unit tests for the renderer, summary, cycle and reload, repository, schema and migrations (including an upgrade from `0001` with data), settings, feed cache, both APIs (through `httpx.ASGITransport`), servers (plus two real-socket tests on `127.0.0.1` with port 0 and a taken port), scheduler and supervisor (injected `sleep` and `random`, `FakeClock`), runtime startup and shutdown order (fake listeners that probe `/readyz` at each step, a run held in its thread to prove it finishes before the servers stop, a real SIGTERM) and the CLI (exit codes, `--debug`, a subprocess check that `cvestream.cli.main` does not import `cvestream.app`, `starlette`, `uvicorn` or `httpx`). `tests/integration/test_pipeline_end_to_end.py` goes from the recorded fixtures to a served feed, with `304`, `HEAD`, an unknown token and a restart that serves the same bytes. `test_restart_no_repost.py` stays green.
- **Deferred items resolved** (`deferred-work.md`): `--debug` now works (pretty exceptions off, one line and exit 1, traceback only under `--debug`); the access log writes `endpoint_id` only (the redaction gap for other callers is noted as remaining).
- **Deviation 1, listener settings defaults:** the story's `ListenerSettings` had a required `host` and `port`, so overriding one key (`CVESTREAM_HTTP__PUBLIC__PORT` alone) failed with "field required". `ListenerSettings` is a base class and `PublicListenerSettings` / `PrivateListenerSettings` carry the default port (8080 / 9090); the host defaults to `0.0.0.0` for both. Behaviour is otherwise as specified.
- **Deviation 2, `PipelineWorker` has no `status` argument:** the story's constructor listed one but nothing in the worker uses it (the source loop owns the status). The constructor is `(state, provider, snapshot, holder, clock, *, run_cycle=cycle)`.
- **Deviation 3, `build_public_app(holder)`:** no `clock` argument (Task 7.2 says `build_public_app(holder)`, the App design said `(holder, clock)`): the handler needs no clock, durations use `time.monotonic()`.
- **Deviation 4, `ManagedServer` and `Listener`:** `app/servers.py` also has `ManagedServer` (a task around an `EmbeddedServer`) and a `Listener` protocol, so the runtime tests inject fake listeners. `ManagedServer` turns uvicorn's `sys.exit(1)` on a bind failure into `ListenerError` **inside the task**: a `SystemExit` raised in an asyncio task would otherwise tear the whole event loop down. A listener ending on its own makes the daemon exit 1 (`app.failed`), as does the pipeline worker ending.
- **Deviation 5, stderr lines are written by the runtime:** `serve` and `poll_once` write the one stderr line for an invalid (exit 2) or unavailable (exit 3) configuration themselves, since they are the ones that know why they return that code; the CLI only passes the code on.
- **Deviation 6, settings validation errors in `run` and `poll`:** `load_settings()` can raise a pydantic `ValidationError` (a bad port, for example), which the existing commands do not catch. The new commands turn it into one line (`settings: <path>: <message>`, no input value, since a setting may be a secret) and exit 2, as AC 4 requires. The existing `db` and `config` commands are unchanged.
- **Deviation 7, `source.stale` is also checked after a fetch whose run was rolled back** (not only after a `SourceError`), because that source did not succeed either.
- **Deviation 8, `TYPE_CHECKING` import in `pipeline/summary.py`:** `summarize()` takes `RunResult` and `SourceOutcome`, which live in `pipeline/run.py` (as the story says), and `run.py` imports `summary.py`. The cycle is broken with a `TYPE_CHECKING` import in `summary.py` (annotations are deferred in Python 3.14, so nothing is needed at runtime).
- **Behaviour to know:** a `config.rejected` warning repeats at every cycle until the file is fixed (the revision keeps differing from the active one); a `ransomware use` of `False` reads `no` (the adapters only produce `True` or `None`); `/status` keeps `last_error` after the source recovered.
- **Not covered by a real run:** EUVD items against the live API (blocked by HTTP 403 from the development network, see the Debug Log). The renderer and the pipeline are exercised on the recorded EUVD fixtures.

### File List

- `pyproject.toml`, `uv.lock` (modified: `starlette`, `uvicorn`)
- `.gitignore` (modified: `/deploy/compose/config/`)
- `src/cvestream/settings.py` (modified: `ListenerSettings`, `PublicListenerSettings`, `PrivateListenerSettings`, `HttpSettings`, `Settings.http`)
- `src/cvestream/state/schema.py` (modified: `feed_cache`), `src/cvestream/state/repository.py` (modified), `src/cvestream/state/migrations/versions/0002_add_feed_cache.py` (new)
- `src/cvestream/pipeline/render.py`, `summary.py` (new), `run.py` (modified)
- `src/cvestream/app/feed_cache.py`, `public_api.py`, `private_api.py`, `servers.py`, `scheduler.py`, `runtime.py` (new)
- `src/cvestream/cli/healthcheck.py` (new), `src/cvestream/cli/main.py` (modified)
- `Dockerfile`, `compose.yml` (modified), `deploy/compose/compose.yml` (new)
- `tests/unit/test_settings.py`, `tests/unit/state/test_db.py`, `test_schema.py`, `test_repository.py`, `tests/unit/cli/test_db.py`, `tests/unit/pipeline/test_run.py` (modified)
- `tests/unit/pipeline/test_render.py`, `test_summary.py`, `test_cycle.py` (new)
- `tests/unit/app/__init__.py`, `test_feed_cache.py`, `test_public_api.py`, `test_private_api.py`, `test_servers.py`, `test_scheduler.py`, `test_runtime.py` (new)
- `tests/unit/cli/test_healthcheck.py`, `test_run.py` (new)
- `tests/integration/test_pipeline_end_to_end.py` (new)
- `settings.example.yaml`, `endpoints.example.yaml`, `docs/configuration.md`, `docs/operations.md` (new), `README.md`, `CLAUDE.md` (modified)
- `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/implementation-artifacts/deferred-work.md` (modified)
- `_bmad-output/implementation-artifacts/1-6-feeds-served-by-the-daemon.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified)

### Change Log

- 2026-10-26: Implemented story 1.6 (RSS renderer, `feed_cache` table and migration `0002`, pipeline cycle with config hot reload and `cycle.summary`, the daemon runtime with its scheduler, supervisor, public and private APIs, the `run`, `poll --once` and `healthcheck` commands, the image `CMD` and `HEALTHCHECK`, the dev and self-hosted compose files, the end-to-end test, operations docs) and moved it to review.
- 2026-10-26: Code review (bmad-code-review): 4 patches applied (no trailing-slash redirects, crashes recorded in `/status` and `source.stale`, escaped XML character ranges, healthcheck on non-HTTP answers), 3 items deferred, 2 decisions pending (HTML semantics of `<description>`, crash backoff cap); status back to in-progress.
