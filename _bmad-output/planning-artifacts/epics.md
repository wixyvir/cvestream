---
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md
  - _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/addendum.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/project-context.md
status: complete
---

# cvestream - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for cvestream, decomposing the requirements from the PRD and Architecture requirements into implementable stories. There is no UX design document: cvestream has no UI, and RSS is its only output contract.

**Sizing choice (user request):** as few epics and stories as possible, each still small enough for one dev agent session and one PR. 3 epics, 11 stories, plus Story 1.7 added by the 2026-11-01 course correction (wixyvir/cvestream#8). Epic 1 alone gives the trial a working, deployable filtered feed.

## Requirements Inventory

### Functional Requirements

- FR-1: Poll EUVD for new and updated source records.
- FR-2: Ingest CERT-EU advisories.
- FR-2b: Ingest CERT-FR *alerte* items (configurable, off by default). Each revision is identified by reference + revision number, merges into the existing vulnerability and does not trigger a re-publish by itself.
- FR-3: Flag a vulnerability as known-exploited when it appears in CISA KEV or ENISA's EU KEV (via EUVD), show which list the flag came from, and show CISA's known ransomware use.
- FR-4: Configure a poll interval for each source.
- FR-5: Add further RSS or JSON sources through configuration. Minimum contract: stable ID + text. Missing CVE ID → not merged; no product data → text-only matching; no score/version → unknown.
- FR-6: A failing source does not stop the others; feeds keep serving their last good content.
- FR-7: Merge source records into one vulnerability using CVE and EUVD ID aliases; keep every contributing source as a reference.
- FR-8: Identify a vulnerability by CVE, EUVD or advisory ID, never by URL.
- FR-9: Record which source supplied each value (score, CVSS version, affected products). Missing score or version data is *unknown*, never zero or low.
- FR-10: Support multiple endpoints, each with its own product list and rules.
- FR-11: A product entry is a name, optional minimum version (`php >= 8.2`), optional aliases and exclusion terms, and can opt out of the description fallback.
- FR-12: Match on structured affected-product data, and on the description only when there is none, never the title alone; case-insensitive whole-word matching; in the description, a name inside a file name or path does not count; an exclusion term prevents the match.
- FR-13: With a minimum version, match only if affected versions reach it; unusable version data matches anyway, labelled "versions unknown".
- FR-14: Per-endpoint minimum CVSS score; unscored vulnerabilities are never dropped by it.
- FR-15: Per-endpoint "known-exploited only" option.
- FR-16: A new endpoint starts with the vulnerabilities it would have matched in the last 7 days.
- FR-17: Serve each endpoint as a valid RSS 2.0 feed, no Slack-specific markup, 14-day item window.
- FR-18: Item title names the matched product and severity; body shows CVE/EUVD IDs, first published and last updated dates, each source's CVSS score + version, KEV status with the date each list added it, affected versions, links to every source.
- FR-19: Publish once per endpoint; re-publish as `[UPDATE]` (new GUID) only when it becomes known-exploited or its severity band rises (including unknown → rated).
- FR-20: Each feed URL contains an unguessable token; a token can be rotated, invalidating the old URL.
- FR-21: Configuration comes from a key-value datastore (preferred) or a YAML file.
- FR-22: CLI subcommands create, list, edit and delete endpoints, product entries, rules and tokens through the active provider; hand-editing YAML stays supported.
- FR-23: Configuration changes apply at the next poll cycle without restart; invalid configuration → keep the last valid one and log the error.
- FR-24: When sources disagree on CVSS, the highest score drives threshold, title severity and re-publish triggers; the item shows each source's score.
- FR-25: Recall wins over volume: rules let through what they cannot rule out.
- FR-26: A vulnerability that first matches an endpoint after late data arrives is published as new on that endpoint.

### NonFunctional Requirements

- NFR-1 Freshness: a new source record appears in matching feeds within 1 hour (poll interval ≤ 60 min).
- NFR-2 Deployment: container image; feeds served behind HTTPS (reverse proxy acceptable).
- NFR-3 Persistence: state and publication history survive restarts and upgrades; a restart never re-posts.
- NFR-4 Operability: per-poll logs with per-source result, per-endpoint item counts, pass-throughs on unknown score/versions; source failures visible in logs.
- NFR-5 Source resilience: one adapter per source, contract tests, unexpected payloads logged as source errors, never crash the daemon.
- NFR-6 Availability: best effort, no SLA (single replica).

### Additional Requirements

From architecture.md and project-context.md (these bind every story):

- **Starter (Epic 1 Story 1):** `uv init --app --build-backend uv_build --python 3.14 --name cvestream .`, then a multi-stage Dockerfile (builder / dev / runtime, non-root UID 999, no uv in the final image) following Astral's uv-docker-example, `compose.yml` with `develop.watch`, `.devcontainer/` pointing to it, `ci.yml`.
- Python 3.14, uv (not poetry), pinned versions from architecture.md; check online before adding any other dependency.
- Layering enforced by import-linter: `domain` is pure (stdlib + `packaging` only); only `sources/*` and `http.py` import httpx; only `state/` imports sqlalchemy/alembic; only `config/valkey_provider.py` imports valkey; only `app/` imports starlette/uvicorn; only `cli/` imports typer.
- `None` means unknown; every source value is a `Sourced[T]`; aware UTC datetimes through an injected `Clock`; versions parsed only in `domain/version.py`.
- State: SQLite (WAL) through SQLAlchemy 2.1 Core + Alembic (configured in code, batch mode), portable types, `UTCDateTime`, `_upsert()`; tables created only by the story that needs them; one `state.transaction()` per pipeline run.
- GUID `cvestream:{endpoint_id}:{primary_id}:{kind}` computed at decision time, stored in `publication.guid`, never recomputed.
- Configuration contract: Pydantic models (`extra="forbid"`) are the single source of truth; `schemas/config-v1.json` exported and checked for freshness in CI. Valkey key layout `cvestream:v1:*`, WATCH/MULTI/INCR revision write protocol, consistent snapshot read, ACL users per role.
- Runtime: one asyncio process, one loop per source → queue → one serialized pipeline worker in a thread → feed cache swap; public listener `:8080` (`/feeds/{token}.xml` only), private `:9090` (`/healthz`, `/readyz`, `/status`).
- First run: source cursor = now − 14 days; KEV dumps loaded in full.
- Outbound HTTP only through `cvestream.http`: `trust_env=False`, system trust store, timeouts, size limits, retries on timeout/5xx/429 only there.
- Error taxonomy: `CvestreamError` root, `SourceTransportError` / `SourcePayloadError` / `SourceContractError`; `except Exception` only in supervisors, always with `exc_info`.
- Logging: structlog, JSON in prod, `<area>.<event>` names, never log a token or full feed path, `cycle.summary` per run.
- Tests: pytest + anyio, respx, pytest-socket blocks network by default, recorded (never hand-written) fixtures in `tests/fixtures/<source>/`, markers `live` and `valkey`.
- CI/CD: GitHub Actions pinned by SHA, least-privilege permissions; `ci.yml` (lint, types, imports, tests, image build, `edge` push on main), `release.yml`, nightly `source-drift.yml`; GHCR multi-arch image; Dependabot.
- Deployment: Docker compose (dev + self-hosted), Kustomize base + example overlay (1 replica, `Recreate`, hardened securityContext, RWO PVC on block storage, exec probes via `cvestream healthcheck`).
- Delivery: one story = one branch `story-<epic>-<story>-<slug>` = one PR to `main`, commits separated by concern, merged only on green CI.
- Docs live in `docs/` (`configuration.md`, `config-contract.md`, `operations.md`, `sources.md`); README and CLAUDE.md are kept current by the story that changes what they describe.

### UX Design Requirements

None. cvestream has no user interface (architecture.md: "Frontend Architecture: not applicable"). Feed item content (FR-18) is covered as a functional requirement.

### FR Coverage Map

- FR-1: Epic 1 (Story 1.4) - EUVD polling
- FR-2: Epic 2 (Story 2.1) - CERT-EU advisories
- FR-2b: Epic 2 (Story 2.1) - CERT-FR alerte revisions
- FR-3: Epic 1 (Story 1.4) - CISA KEV + EU KEV flags, ransomware
- FR-4: Epic 1 (Stories 1.1, 1.6) - per-source interval setting and scheduler
- FR-5: Epic 2 (Story 2.2) - configured generic RSS/JSON sources
- FR-6: Epic 1 (Story 1.6) - source isolation, last good feed
- FR-7: Epic 1 (Story 1.5) - alias-based merge
- FR-8: Epic 1 (Stories 1.2, 1.5) - ID-based identity, never URL
- FR-9: Epic 1 (Story 1.2) - provenance and unknown values
- FR-10: Epic 1 (Story 1.3) - multiple endpoints
- FR-11: Epic 1 (Stories 1.2, 1.3, 1.7) - product entries, `match_description`
- FR-12: Epic 1 (Stories 1.2, 1.7) - whole-word matching, exclusions, description fallback precision
- FR-13: Epic 1 (Story 1.2) - minimum version, "versions unknown"
- FR-14: Epic 1 (Story 1.2) - CVSS threshold
- FR-15: Epic 1 (Story 1.2) - KEV-only
- FR-16: Epic 1 (Story 1.5) - 7-day backfill
- FR-17: Epic 1 (Story 1.6) - RSS 2.0 feed, 14-day window
- FR-18: Epic 1 (Story 1.6) - item title and body
- FR-19: Epic 1 (Story 1.5) - publish once, `[UPDATE]` triggers
- FR-20: Epic 1 (Stories 1.3, 1.6) - token in URL; Epic 3 (Story 3.2) - rotation by CLI
- FR-21: Epic 1 (Story 1.3) - YAML provider; Epic 3 (Story 3.1) - Valkey provider
- FR-22: Epic 3 (Story 3.2) - CLI CRUD
- FR-23: Epic 1 (Stories 1.3, 1.6) - hot reload, keep last valid; Epic 3 (Story 3.1) - for Valkey
- FR-24: Epic 1 (Story 1.2) - highest score drives decisions
- FR-25: Epic 1 (Story 1.2) - recall first
- FR-26: Epic 1 (Story 1.5) - late match published as new

## Epic List

### Epic 1: Filtered vulnerability feed from EUVD and KEV

The operator writes an `endpoints.yaml`, starts one container, and each team subscribes its Slack channel to a feed URL that shows only EUVD vulnerabilities affecting its stack, flagged when known-exploited, posted once and updated only on KEV or severity rises. This is enough to start the one-month trial.
**FRs covered:** FR-1, FR-3, FR-4, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11, FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, FR-18, FR-19, FR-20 (token in URL), FR-21 (YAML), FR-23 (YAML), FR-24, FR-25, FR-26. NFR-1 to NFR-6.

### Epic 2: European advisory sources

Feeds also carry CERT-EU advisories and (opt-in) CERT-FR alertes, merged into the same vulnerabilities, and the operator can add other RSS/JSON sources by configuration. Source API drift is detected nightly. This closes the PHP2Shell gap (SM-3: timeliness against hand-posted CERT-EU links).
**FRs covered:** FR-2, FR-2b, FR-5. NFR-5 (drift detection).

### Epic 3: Shared configuration store, operator CLI and release

Configuration moves to Valkey so other tools (BoM connectors, CD workers) can edit product lists; the operator manages endpoints, products and tokens from the CLI with either provider; versioned images and Kubernetes manifests are published.
**FRs covered:** FR-20 (rotation), FR-21 (Valkey), FR-22, FR-23 (Valkey). NFR-2 (release, Kubernetes).

## Epic 1: Filtered vulnerability feed from EUVD and KEV

The operator writes an `endpoints.yaml`, starts one container, and each team subscribes its Slack channel to a feed URL that shows only EUVD vulnerabilities affecting its stack, flagged when known-exploited, posted once and updated only on KEV or severity rises.

### Story 1.1: Project skeleton, container and CI

As the operator,
I want a buildable, tested container image and a containerized dev loop from day one,
So that every following story ships through the same quality gate and runs the same way under Docker, Kubernetes and locally.

**Acceptance Criteria:**

**Given** an empty repository apart from planning files
**When** the project is initialized with `uv init --app --build-backend uv_build --python 3.14 --name cvestream .`
**Then** `pyproject.toml`, `uv.lock`, `.python-version` (3.14) and `src/cvestream/` exist, and `uv run cvestream --help` prints the Typer root help (`cli/main.py`, `__main__.py`)
**And** runtime and dev dependencies are added with `uv add` at the versions pinned in architecture.md (only those this story uses), and every tool is configured in `pyproject.toml` (ruff with `DTZ`, `BLE`, `S`; ty; pytest with anyio, pytest-socket blocking the network, markers `live` and `valkey`; import-linter contracts for the layering in architecture.md, including `packaging` as the only third-party import allowed in `domain`)

**Given** the cross-cutting modules
**When** they are added
**Then** `errors.py` (`CvestreamError`), `clock.py` (`Clock` protocol, `SystemClock`), `log.py` (structlog JSON/console, token redaction) and `settings.py` (pydantic-settings, `CVESTREAM_*` env with `__` nesting over optional `settings.yaml`, `*_FILE` secrets, per-source `interval` defaulting to 15 min and rejected above 60 min for NFR-1) exist with unit tests, and `tests/conftest.py` provides a `FakeClock`

**Given** the Dockerfile
**When** `docker build` runs
**Then** it has `builder`, `dev` and `runtime` targets, the runtime image is `python:3.14-slim-trixie`, runs as UID 999 without uv, declares volume `/data`, `EXPOSE 8080 9090`, `ENTRYPOINT ["cvestream"]`, and `.dockerignore` excludes `_bmad/`, `_bmad-output/`, `.claude/`
**And** `compose.yml` runs the `dev` target with `develop.watch` (sync `src/`, rebuild on `uv.lock`) and `.devcontainer/devcontainer.json` points to it

**Given** a pull request
**When** `.github/workflows/ci.yml` runs
**Then** it executes `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports`, `pytest`, then builds the image (and pushes `ghcr.io/wixyvir/cvestream:edge` + `sha-<short>` on `main`), with every Action pinned by SHA and least-privilege `permissions:`
**And** `.github/dependabot.yml` covers `uv`, `github-actions` and `docker` weekly
**And** `README.md` (purpose, dev quick start) and `CLAUDE.md` (points to architecture.md and project-context.md, key MUSTs) exist

### Story 1.2: Vulnerability model and matching engine

As a channel subscriber,
I want vulnerabilities matched against my team's product list on whole words, versions and severity, with unknown data let through and labelled,
So that my channel shows only alerts that may affect our stack and never silently drops one it cannot rule out.

**Acceptance Criteria:**

**Given** the pure `domain` package (stdlib + `packaging` only)
**When** it is implemented
**Then** `model.py` defines `Sourced[T]`, `SourceRecord`, `NormalizedRecord`, `Vulnerability`, `AffectedProduct` as frozen slotted dataclasses, with `None` meaning unknown (FR-9)
**And** `identity.py` normalizes aliases to upper case (`CVE-…`, `EUVD-…`, advisory IDs) and picks the primary ID as CVE > EUVD > advisory, never using a URL (FR-8)
**And** `severity.py` provides the `Band` enum (`unknown`, `none`, `low`, `medium`, `high`, `critical`), `max_score()` over all `Sourced` scores and `band_of()`; with conflicting scores the highest wins and no score gives `unknown`, never `low` (FR-24)

**Given** free-text affected-version data (e.g. `< 8.2.29`, `8.1.0 - 8.3.4`, `n/a`, French free text)
**When** `domain/version.py` parses it
**Then** it returns a `VersionRange` when parseable and `None` otherwise, and a range comparison against a minimum version is correct for the recorded EUVD-style formats (table-driven tests)

**Given** an `Endpoint` with `ProductEntry`s (name, aliases, exclusions, optional `min_version`) and rules (`min_cvss`, `kev_only`) in `domain/endpoint.py`
**When** `matcher.match(vulnerability, endpoint)` runs
**Then** it matches on structured product data and the description, never the title alone, case-insensitively on whole words (`php` matches "PHP 8.3", not "phpMyAdmin"), and also on aliases (FR-11, FR-12)
**And** a vulnerability mentioning an exclusion term does not match that entry (FR-12)
**And** with `min_version`, it matches only if affected versions reach that version; unusable version data (missing, `n/a`, unparseable, or a description-only match) matches anyway with a `versions_unknown` label (FR-13)
**And** `min_cvss` drops only scored vulnerabilities below the threshold, using the highest score; unscored ones pass with a `score_unknown` label (FR-14, FR-24, FR-25)
**And** `kev_only` keeps only known-exploited vulnerabilities (FR-15)
**And** `MatchResult` names the matched product entry and the pass-through labels, so later stories can count them (NFR-4)

### Story 1.3: Endpoint configuration in YAML

As the operator,
I want to declare endpoints, product lists, rules and feed tokens in an `endpoints.yaml` validated against a published schema,
So that I can configure the trial without any extra service and other tools can validate configuration against the same contract.

**Acceptance Criteria:**

**Given** `config/models.py`
**When** the v1 contract models are written
**Then** they define endpoints (slug ID `[a-z0-9-]{1,40}`, name, enabled, `min_cvss`, `kev_only`, token) and product entries (`name`, `aliases[]`, `exclude[]`, `min_version`) with `extra="forbid"`, convert to the domain `Endpoint` objects from Story 1.2, and accept the `php >= 8.2` shorthand (FR-10, FR-11)
**And** `cvestream config schema` writes `schemas/config-v1.json`, the file is committed, and CI fails when it is stale

**Given** `config/provider.py` and `config/yaml_provider.py`
**When** the YAML provider loads `endpoints.yaml`
**Then** it returns a `ConfigSnapshot` whose revision is the SHA-256 of the file content (FR-21)
**And** when the file changes to an invalid configuration, `load()` raises a validation error that names the field, so the caller can keep the last valid snapshot and log `config.rejected` (FR-23)

**Given** a configuration file
**When** the operator runs `cvestream config validate <file>`
**Then** it exits 0 when valid and 2 with one plain error line when invalid
**And** `endpoints.example.yaml` documents every field, contains a token placeholder, and validates in CI
**And** `docs/configuration.md` describes `settings.yaml`, `CVESTREAM_*` variables and `endpoints.yaml`

### Story 1.4: EUVD and KEV ingestion

As a channel subscriber,
I want EUVD vulnerabilities and their CISA KEV / EU KEV exploitation status ingested reliably,
So that my feed is built from the European reference database and flags actively exploited issues.

**Acceptance Criteria:**

**Given** `cvestream.http`
**When** a source performs a GET
**Then** it uses httpx with `trust_env=False`, the system trust store, timeouts, a response size limit, and retries only on timeout/5xx/429 (honouring `Retry-After`) with capped exponential backoff and jitter

**Given** `sources/base.py`
**When** the adapter contract is defined
**Then** it provides the `SourceAdapter` protocol (`name`, `async fetch(cursor) -> FetchResult(records, next_cursor)`) and the `SourceError` family (`SourceTransportError`, `SourcePayloadError`, `SourceContractError`); a record that fails validation is skipped and counted, a source-level failure raises without advancing the cursor (NFR-5)

**Given** the `euvd` adapter
**When** it fetches with a cursor
**Then** it pages incrementally on `fromUpdatedDate`, converts timezone-less dates to UTC per a documented assumption, turns placeholders (`""`, `n/a`, unscored `0`) into `None`, converts EPSS ×100 to a fraction, and emits `SourceRecord`s with aliases, `Sourced` scores with CVSS version, affected products/versions and EU KEV status (FR-1, FR-3, FR-9)
**And** with no cursor the first fetch starts at now − 14 days

**Given** the `cisa_kev` adapter
**When** it fetches the KEV JSON
**Then** it loads the full catalogue and emits KEV records carrying `known_ransomware_use` (FR-3)

**Given** recorded payloads in `tests/fixtures/euvd/` and `tests/fixtures/cisa_kev/` (captured from the real APIs, named after what they show, e.g. `euvd_no_score.json`, `euvd_version_na.json`)
**When** `tests/contract/` runs with respx
**Then** normal, missing-score, `n/a`-version and malformed payloads behave as above, and each contract test has a `live` variant deselected by default
**And** `sources/registry.py` builds adapters from settings, with an interval per source (FR-4)
**And** `docs/sources.md` documents each source's timezone, placeholder and pagination assumptions

### Story 1.5: Correlation, state and publication decisions

As a channel subscriber,
I want each vulnerability posted once on my feed, merged across sources, re-posted only when it becomes exploited or more severe, and never re-posted after a restart,
So that my channel has no duplicates (SM-4) and no missed escalation.

**Acceptance Criteria:**

**Given** the `state` package
**When** it is created
**Then** `state/schema.py` declares only the tables this story needs (`vulnerability`, `alias`, `source_record`, `publication`, `source_cursor`) with the naming convention and `UTCDateTime`, `0001_create_schema.py` creates them through Alembic (configured in code, batch mode), migrations run at startup and via `cvestream db migrate`, and SQLite gets WAL, `foreign_keys=ON` and `busy_timeout` through a connect event
**And** every query lives in `state/repository.py`, upserts go through `_upsert()`, and `state.transaction()` is the only commit point

**Given** records from different sources sharing a CVE or EUVD ID
**When** `pipeline/normalize.py` and `pipeline/correlate.py` run
**Then** they merge into one vulnerability keyed by aliases, keep every contributing source and every `Sourced` value, and a source URL change never creates a duplicate (FR-7, FR-8, FR-9)

**Given** a match result and the publication history
**When** `domain/publication.decide()` runs
**Then** a first match publishes `new`; becoming known-exploited publishes `kev`; a band rise (including `unknown` → rated) publishes `band-{band}`; any other change publishes nothing (FR-19)
**And** the GUID `cvestream:{endpoint_id}:{primary_id}:{kind}` is computed at decision time and stored in `publication.guid`, never recomputed
**And** a vulnerability that first matches only after late data arrives is published as `new` (FR-26)
**And** an endpoint with no publication history is backfilled with what it would have matched in the last 7 days (FR-16)

**Given** `pipeline/run.py`
**When** one run executes
**Then** decisions, publication rows and source cursors are written in a single `state.transaction()`
**And** `tests/integration/test_restart_no_repost.py` proves that rerunning after a restart publishes nothing new (NFR-3)
**And** `cvestream db backup <file>` uses the SQLite online backup API

### Story 1.6: Feeds served by the daemon

As a channel subscriber,
I want a token-protected RSS 2.0 feed that a long-running daemon keeps up to date,
So that I can subscribe my Slack channel once and receive relevant alerts within an hour.

**Acceptance Criteria:**

**Given** `pipeline/render.py`
**When** an endpoint's feed is rendered
**Then** it is valid RSS 2.0 built with an XML serializer (source text escaped, no Slack markup) with the items of the last 14 days (FR-17)
**And** each item title names the matched product and severity band, updates are prefixed `[UPDATE]`, and the body shows CVE/EUVD IDs, the first published and last updated dates, each source's CVSS score with version, KEV status with its list, the date it was added and ransomware use, affected versions or "versions unknown", and links to every source; unknown values render as `unknown` (FR-18, FR-24)
**And** the rendered XML is stored in a new `feed_cache` table (migration `0002_add_feed_cache.py`) inside the run's transaction

**Given** `cvestream run`
**When** the daemon starts
**Then** it migrates the state, starts the private listener (`/healthz` ok, `/readyz` not ready), loads the configuration, loads `feed_cache` into memory, starts one loop per source with its interval and jitter (FR-4) and the public listener, then `/readyz` becomes ready
**And** source loops feed a queue consumed by one serialized pipeline worker via `asyncio.to_thread`; HTTP handlers only read the in-memory feed cache, swapped by reference
**And** `cvestream poll --once` runs one cycle and exits; SIGTERM finishes the current run and stops the servers

**Given** the public listener on `:8080`
**When** a client requests `GET`/`HEAD /feeds/{token}.xml`
**Then** a known token returns `application/rss+xml; charset=utf-8` with `ETag`, `Last-Modified`, `304` support and `Cache-Control: max-age=300`; an unknown token returns `404` with an empty body; access logs record the endpoint ID, never the token (FR-20)

**Given** a source that fails or returns unexpected payloads
**When** the pipeline runs
**Then** the other sources keep working, its cursor does not advance, feeds keep their last good content, a crashed loop is restarted by the supervisor with backoff, and `source.stale` is logged after 3 × interval without success (FR-6, NFR-5)

**Given** the YAML file changes between runs
**When** the next run starts
**Then** a valid change applies without restart and an invalid one keeps the last valid configuration and logs `config.rejected` (FR-23)

**Given** each pipeline run
**When** it completes
**Then** a `cycle.summary` log records per-source result, per-endpoint published count, and pass-throughs on unknown score and unknown versions (NFR-4)
**And** `/status` returns per-source last success/error/cursor and per-endpoint item count and last render
**And** `cvestream healthcheck [--ready]` probes `127.0.0.1:9090` with the stdlib only and is the Dockerfile `HEALTHCHECK`
**And** `deploy/compose/compose.yml` runs the GHCR image with a named `/data` volume, the YAML provider, `restart: unless-stopped`, only 8080 published, read-only root filesystem, all capabilities dropped (NFR-2)
**And** `tests/integration/test_pipeline_end_to_end.py` goes from recorded EUVD/KEV fixtures to a served feed
**And** `docs/operations.md` covers running, logs, `/status`, backup, and the warning that reverse-proxy logs contain the token; `README.md` gets the self-hosted quick start

### Story 1.7: Description fallback precision

Added by the 2026-11-01 course correction for wixyvir/cvestream#8 (fixes B and C), found while trialling Story 1.6 on live data: 9 of 16 `team-php` items were about other products.

As a channel subscriber,
I want a product name that only appears inside a file name or path not to count as a mention, and my team to be able to rely on structured product data alone for an entry,
So that my channel stops receiving vulnerabilities about other products, including KEV-only ones.

**Acceptance Criteria:**

**Given** a vulnerability without any affected-product data (an EUVD record whose product is `n/a`, or a CISA KEV-only record)
**When** `matcher.match()` falls back to its description
**Then** a name or alias hit preceded by `.`, `/` or `\`, or followed by `/` or `\`, does not count: `php` does not match `admin/modules/circulation/loan_history_maintenance.php`, `diagnostic.php`, `/usr/lib/php/` or `C:\php\`, and still matches "PHP 8.3", "php-fpm", "(PHP)", "PHP.", `php.ini`, "See the php.net advisory"; `node` still matches "Node.js" (FR-12)
**And** a description that also mentions the product outside a path still matches (`Upload a .php file to a PHP 8.3 server`)
**And** structured affected-product data and exclusion terms keep plain whole-word matching, unchanged
**And** table tests in `tests/unit/domain/test_matcher.py` cover the file-name descriptions of wixyvir/cvestream#8 (CVE-2026-107084, CVE-2026-52356, CVE-2026-50787, CVE-2026-107081), with vulnerabilities shaped like the recorded EUVD `n/a`-product and CISA KEV records

**Given** a full product entry with `match_description: false`
**When** a vulnerability has no affected-product data
**Then** that entry does not match it, whatever its description says (KEV-only CVE-2026-46300 mentioning "PHP webshells" does not match `php`); with the default `true` it still does, so the trade-off is covered by a test (FR-11, FR-12)
**And** when later EUVD data brings an affected product the entry names, the vulnerability is published as `new` (FR-26), proven by a pipeline test going from a KEV-only record to the merged record
**And** exclusion terms are still looked for in the description

**Given** `config/models.py` and `domain/endpoint.py`
**When** `ProductEntryConfig` gains the optional `match_description: bool` (default `true`) and `ProductEntry` carries it
**Then** the `php >= 8.2` shorthand keeps the default, the field is additive (still `version: 1`), and `schemas/config-v1.json` is regenerated in the same commit
**And** `docs/configuration.md` ("How an entry matches"), `endpoints.example.yaml` and the `matcher.py` docstring describe the path rule and `match_description`, including that a KEV-only vulnerability waits for EUVD data on such an entry
**And** publications already made are not retracted (unchanged publication rules)

## Epic 2: European advisory sources

Feeds also carry CERT-EU advisories and (opt-in) CERT-FR alertes, merged into the same vulnerabilities, and the operator can add other RSS/JSON sources by configuration. Source API drift is detected nightly.

### Story 2.1: CERT-EU and CERT-FR alerte adapters

As a channel subscriber,
I want CERT-EU advisories and CERT-FR alertes in my feed, merged with the EUVD record of the same vulnerability,
So that urgent European advisories (like PHP2Shell) reach my channel before anyone posts them by hand.

**Acceptance Criteria:**

**Given** the `cert_eu` adapter
**When** it fetches the advisories RSS (latest 10) and the JSON of each new advisory
**Then** it emits `SourceRecord`s with the advisory ID, CVE aliases, description, affected products and source link; XML is parsed without entity resolution (FR-2)
**And** records sharing a CVE with an EUVD record merge into the same vulnerability (Story 1.5)

**Given** the `certfr_alerte` adapter, off by default in settings
**When** it is enabled and fetches the alerte RSS + JSON
**Then** each revision is identified by reference + revision number (`CERTFR-2026-ALE-012` rev. N), merges into the existing vulnerability, and does not trigger a re-publish by itself (FR-2b)
**And** revisions of the same reference merge even when no CVE is known yet, so two vulnerabilities never share an advisory primary ID and never build the same publication GUID (review of wixyvir/cvestream#5); the `publish.skipped` safety net of `pipeline/run.py` stays silent in the contract and pipeline tests
**And** French free-text version data that cannot be parsed yields `None` (versions unknown), never a crash

**Given** recorded fixtures in `tests/fixtures/cert_eu/` and `tests/fixtures/certfr_alerte/`
**When** the contract tests run
**Then** normal, revised-alerte and malformed payloads are covered, each with a `live` variant
**And** `docs/sources.md` and `settings.example.yaml` document both sources

### Story 2.2: Configured sources and source-drift detection

As the operator,
I want to add any RSS or JSON vulnerability source through settings, and to be told when a source API changes,
So that vendor bulletins can be followed without code changes and a silent upstream change never empties our feeds.

**Acceptance Criteria:**

**Given** a `generic_rss` or `generic_json` source in settings (URL, ID path or regex, text fields, optional CVE regex, interval)
**When** the registry builds it
**Then** it emits records with a stable ID and text; settings without both are rejected at startup with a clear error (FR-5)
**And** without a CVE ID the record is not merged, without product data it is matched on text only, and missing score/version stay unknown (FR-5, FR-9)
**And** contract tests in `tests/contract/generic/` cover RSS and JSON examples, including the degraded cases

**Given** `.github/workflows/source-drift.yml`
**When** it runs nightly or manually
**Then** it runs the `live` contract tests against the real APIs and, on failure, opens or updates an issue labelled `source-drift` (NFR-5)
**And** `docs/sources.md` explains how to declare a generic source

## Epic 3: Shared configuration store, operator CLI and release

Configuration moves to Valkey so other tools can edit product lists; the operator manages endpoints, products and tokens from the CLI with either provider; versioned images and Kubernetes manifests are published.

### Story 3.1: Valkey configuration provider

As the operator,
I want cvestream to read its configuration from a Valkey service on a private network, with per-role access rules,
So that other tools (BoM connectors, CD workers) can maintain product lists through a documented, versioned contract.

**Acceptance Criteria:**

**Given** `config/keys.py` and `config/valkey_provider.py`
**When** the provider reads the configuration
**Then** it uses the `cvestream:v1:*` key layout from architecture.md and a consistent snapshot read (revision → pipelined read → revision, up to 3 retries, otherwise keep the last valid snapshot), validated with the Story 1.3 models (FR-21, FR-23)
**And** writes use `WATCH revision` → `MULTI` → writes → `INCR revision` → `EXEC`, retried on conflict

**Given** `config_provider: valkey`
**When** the daemon starts and Valkey is unreachable
**Then** it retries with backoff for `valkey.startup_timeout` (default 60 s), then exits with code 3 and logs `config.unavailable` (`tests/integration/test_startup_requires_valkey.py`)
**And** during an outage while running, the last valid configuration keeps being used and each run logs a `config.unavailable` warning

**Given** `deploy/valkey/valkey.conf` and `deploy/valkey/users.acl`
**When** Valkey starts with them
**Then** AOF is on (`appendfsync everysec`), `default` is disabled, `cvestream-daemon` is read-only on `cvestream:v1:*`, `cvestream-admin` is read/write, and `connector-example` can write only `endpoint:*:products` and `revision`, never `token:*`; an integration test (marker `valkey`) proves each restriction
**And** `compose.yml` adds a `valkey` service on a private network, CI runs `valkey` tests against a `valkey/valkey:9.1` service, and the password comes from `CVESTREAM_VALKEY__PASSWORD_FILE`
**And** `docs/config-contract.md` documents the key layout, write protocol, ACL examples and JSON Schema for third-party tools

### Story 3.2: Operator CLI for endpoints, products and tokens

As the operator,
I want CLI commands to manage endpoints, product entries, rules and tokens on whichever provider is active,
So that I never edit Valkey keys by hand and can rotate a leaked feed URL in one command.

**Acceptance Criteria:**

**Given** the active provider (YAML or Valkey)
**When** the operator runs `cvestream endpoint create|list|show|edit|delete`, `cvestream product add|remove|list` or `cvestream token rotate <endpoint>`
**Then** the change is written through that provider (YAML file rewritten preserving validity, Valkey through the write protocol), validated before writing, and applied by the daemon at its next run (FR-22, FR-23)
**And** `endpoint create` and `token rotate` generate the token with `secrets.token_urlsafe(32)` through an injectable function, and `endpoint show` prints the feed URL; after rotation the old URL returns `404` (FR-20)
**And** options are kebab-case (`--min-cvss`, `--kev-only`), `list` commands support `--json`, user errors exit 2 with one line, unexpected errors exit 1 with a trace only under `--debug`
**And** `cvestream config export|import` moves the whole configuration between YAML and the active provider
**And** hand-editing `endpoints.yaml` keeps working
**And** `docs/operations.md` covers token rotation and configuration backup

### Story 3.3: Release and Kubernetes deployment

As the operator,
I want versioned multi-arch images and Kubernetes manifests,
So that I can deploy a pinned release under Docker or Kubernetes with a hardened, single-replica setup.

**Acceptance Criteria:**

**Given** a `v*` tag
**When** `.github/workflows/release.yml` runs
**Then** it checks the tag matches the `pyproject.toml` version, builds amd64 + arm64, pushes `X.Y.Z`, `X.Y` and `latest` to GHCR with OCI labels, SBOM and provenance (`mode=max`) attestation, and creates a GitHub Release (NFR-2)

**Given** `deploy/k8s/base` and `deploy/k8s/overlays/example`
**When** `kubectl kustomize` builds them
**Then** they produce a `Deployment` (1 replica, `Recreate`, UID 999, read-only root filesystem, capabilities dropped, `terminationGracePeriodSeconds: 60`, exec probes via `cvestream healthcheck` with `--ready` for startup/readiness), an RWO PVC on `/data`, `cvestream-public` (8080) and `cvestream-private` (9090) Services, a `NetworkPolicy`, an example `Ingress` on the public Service only, an example Valkey `StatefulSet` and a secret example (NFR-2, NFR-6)
**And** CI validates the Kustomize build
**And** `docs/operations.md` and `README.md` cover Kubernetes deployment, the block-storage requirement for the SQLite volume, and the single-replica constraint
