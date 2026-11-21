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

**Sizing choice (user request):** as few epics and stories as possible, each still small enough for one dev agent session and one PR. 3 epics, 11 stories, plus Stories 1.7 and 1.8 added by the 2026-11-01 course corrections (wixyvir/cvestream#8, wixyvir/cvestream#11). Epic 1 alone gives the trial a working, deployable filtered feed.

## Requirements Inventory

### Functional Requirements

- FR-1: Poll EUVD for new and updated source records.
- FR-2: Ingest CERT-EU advisories.
- FR-2b: *(Deferred after v1, sprint change proposal 2026-11-01 epic 2 scope.)* Ingest CERT-FR *alerte* items (configurable, off by default). Each revision is identified by reference + revision number, merges into the existing vulnerability and does not trigger a re-publish by itself.
- FR-3: Flag a vulnerability as known-exploited when it appears in CISA KEV or ENISA's EU KEV (via EUVD), show which list the flag came from, and show CISA's known ransomware use.
- FR-4: Configure a poll interval for each source.
- FR-5: *(Deferred after v1, sprint change proposal 2026-11-01 epic 2 scope.)* Add further RSS or JSON sources through configuration. Minimum contract: stable ID + text. Missing CVE ID → not merged; no product data → text-only matching; no score/version → unknown.
- FR-6: A failing source does not stop the others; feeds keep serving their last good content.
- FR-7: Merge source records into one vulnerability using CVE and EUVD ID aliases; keep every contributing source as a reference.
- FR-8: Identify a vulnerability by CVE, EUVD or advisory ID, never by URL.
- FR-9: Record which source supplied each value (score, CVSS version, affected products). Missing score or version data is *unknown*, never zero or low.
- FR-10: Support multiple endpoints, each with its own product list and rules.
- FR-11: A product entry is a name, optional minimum version (`php >= 8.2`), optional aliases and exclusion terms, can opt out of the description fallback, or require the whole product name (`exact`).
- FR-12: Match on structured affected-product data, and on the description only when there is none, never the title alone; case-insensitive whole-word matching; an exact entry matches the whole product name only (no vendor, no description); in the description, a name inside a file name or path does not count; an exclusion term prevents the match.
- FR-13: With a minimum version, match only if affected versions reach it; unusable version data matches anyway, labelled "versions unknown".
- FR-14: Per-endpoint minimum CVSS score; unscored vulnerabilities are never dropped by it.
- FR-15: Per-endpoint "known-exploited only" option.
- FR-16: A new endpoint starts with the vulnerabilities it would have matched in the last 7 days.
- FR-17: Serve each endpoint as a valid RSS 2.0 feed, no Slack-specific markup, 14-day item window.
- FR-18: Item title names the matched product and severity, with emoji markers (exploited, critical, severity raised, published today) beside the words; body shows CVE/EUVD IDs, first published and last updated dates, each source's CVSS score + version, KEV status with the date each list added it, affected versions, links to every source, leading with the facts a truncating reader must see.
- FR-19: Publish once per endpoint; re-publish as `[UPDATE]` (new GUID) only when it becomes known-exploited or its severity band rises (including unknown → rated).
- FR-20: Each feed URL contains an unguessable token; a token can be rotated, invalidating the old URL.
- FR-21: Configuration comes from a key-value datastore (preferred) or a YAML file.
- FR-22: CLI subcommands create, list, edit and delete endpoints, product entries, rules and tokens through the active provider; hand-editing YAML stays supported.
- FR-23: Configuration changes apply at the next poll cycle without restart; invalid configuration → keep the last valid one and log the error.
- FR-24: When sources disagree on CVSS, the highest score drives threshold, title severity and re-publish triggers; the item shows each source's score.
- FR-25: Recall wins over volume: rules let through what they cannot rule out.
- FR-26: A vulnerability that first matches an endpoint after late data arrives is published as new on that endpoint.
- FR-27: Opt-in per endpoint: a burst of 3 or more items of the same matched product and kind family in one cycle becomes one feed item (at most 5 members, most severe first); known-exploited vulnerabilities are never grouped.

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
- FR-2: Epic 2 (Story 2.1) - CERT-EU advisories, one record per CVE
- FR-2b: deferred after v1 - CERT-FR alerte revisions
- FR-3: Epic 1 (Story 1.4) - CISA KEV + EU KEV flags, ransomware
- FR-4: Epic 1 (Stories 1.1, 1.6) - per-source interval setting and scheduler
- FR-5: deferred after v1 - configured generic RSS/JSON sources
- FR-6: Epic 1 (Story 1.6) - source isolation, last good feed
- FR-7: Epic 1 (Story 1.5) - alias-based merge
- FR-8: Epic 1 (Stories 1.2, 1.5) - ID-based identity, never URL
- FR-9: Epic 1 (Story 1.2) - provenance and unknown values
- FR-10: Epic 1 (Story 1.3) - multiple endpoints
- FR-11: Epic 1 (Stories 1.2, 1.3, 1.7) - product entries, `match_description`
- FR-12: Epic 1 (Stories 1.2, 1.7) - whole-word matching, exclusions, description fallback precision; Epic 4 (Story 4.2) - exact product matching
- FR-13: Epic 1 (Stories 1.2, 1.8) - minimum version, "versions unknown", branch-wildcard and compound version ranges
- FR-14: Epic 1 (Story 1.2) - CVSS threshold; Epic 4 (Story 4.1) - require_score opt-out
- FR-15: Epic 1 (Story 1.2) - KEV-only
- FR-16: Epic 1 (Story 1.5) - 7-day backfill
- FR-17: Epic 1 (Story 1.6) - RSS 2.0 feed, 14-day window
- FR-18: Epic 1 (Story 1.6) - item title and body; Epic 5 (Story 5.1) - title markers, compact body
- FR-19: Epic 1 (Story 1.5) - publish once, `[UPDATE]` triggers
- FR-20: Epic 1 (Stories 1.3, 1.6) - token in URL; Epic 3 (Story 3.2) - rotation by CLI
- FR-21: Epic 1 (Story 1.3) - YAML provider; Epic 3 (Story 3.1) - Valkey provider
- FR-22: Epic 3 (Story 3.2) - CLI CRUD
- FR-23: Epic 1 (Stories 1.3, 1.6) - hot reload, keep last valid; Epic 3 (Story 3.1) - for Valkey
- FR-24: Epic 1 (Story 1.2) - highest score drives decisions
- FR-25: Epic 1 (Story 1.2) - recall first; Epic 4 (Story 4.1) - require_score opt-out
- FR-26: Epic 1 (Story 1.5) - late match published as new
- FR-27: Epic 5 (Story 5.2) - burst grouping

## Epic List

### Epic 1: Filtered vulnerability feed from EUVD and KEV

The operator writes an `endpoints.yaml`, starts one container, and each team subscribes its Slack channel to a feed URL that shows only EUVD vulnerabilities affecting its stack, flagged when known-exploited, posted once and updated only on KEV or severity rises. This is enough to start the one-month trial.
**FRs covered:** FR-1, FR-3, FR-4, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11, FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, FR-18, FR-19, FR-20 (token in URL), FR-21 (YAML), FR-23 (YAML), FR-24, FR-25, FR-26. NFR-1 to NFR-6.

### Epic 2: European advisory sources

Feeds also carry CERT-EU advisories, merged per CVE into the same vulnerabilities as EUVD, so a critical pre-announcement reaches the channel before the CVE is published (PHP2Shell: CERT-EU was ~7.5 h ahead of EUVD). Source API drift is detected nightly. This closes the PHP2Shell gap (SM-3: timeliness against hand-posted CERT-EU links).
**FRs covered:** FR-2. NFR-5 (drift detection). FR-2b and FR-5 are deferred after v1 (sprint change proposal 2026-11-01, epic 2 scope).

### Epic 3: Shared configuration store, operator CLI and release

Configuration moves to Valkey so other tools (BoM connectors, CD workers) can edit product lists; the operator manages endpoints, products and tokens from the CLI with either provider, and can inspect the store in an optional administration UI; versioned images and Kubernetes manifests are published.
**FRs covered:** FR-20 (rotation), FR-21 (Valkey), FR-22, FR-23 (Valkey). NFR-2 (release, Kubernetes).

### Epic 4: Matching and scoring enhancements

Enhancements to the matching engine and scoring rules based on real-world usage: optional `require_score` rule to drop unscored vulnerabilities (except KEV) and optional `exact` product matching to avoid false positives from generic names.
**FRs covered:** FR-12 (exact matching), FR-14 (scoring), FR-25 (recall with opt-out).

### Epic 5: Readable bursts

The team reads its channel faster: each Slack message shows the severity, the exploited status, the matched product and the start of the description before Slack cuts it; emoji in the title flag exploited, critical, raised and same-day vulnerabilities; an endpoint can group a burst of same-product items into one message; held-back items are counted; duplicate product names are rejected.
**FRs covered:** FR-18 (amended), FR-27. NFR-4 (held-back count).

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

### Story 1.8: Branch-wildcard and compound version ranges

Added by the 2026-11-01 course correction for wixyvir/cvestream#11, found while replaying PHP2Shell: the PHP CNA writes affected versions as `8.2.* <8.2.35`, which parses as unusable, so every PHP vulnerability in that format is labelled "versions unknown" and `php >= X` filters nothing.

As a channel subscriber,
I want the version ranges written by the PHP CNA, and other common range shapes, to be understood,
So that my team's minimum version filters these vulnerabilities, and an item reads "versions unknown" only when versions really are unknown.

**Acceptance Criteria:**

**Given** a lower bound written as a branch wildcard `B.*`, where `B` is a final release (`8.2.*`, `8.*`, `v8.2.*`)
**When** `domain/version.py` parses it
**Then** the range starts at `B` inclusive: `8.2.* <8.2.35` → `[8.2, 8.2.35)`, `8.2.* ≤8.2.35` → `[8.2, 8.2.35]`, `8.2.* <*` → `[8.2, ∞)`, and the upper bound is taken as written (`8.2.* <8.4` → `[8.2, 8.4)`) (FR-13)
**And** a bare `B.*` covers the branch: `8.2.*` → `[8.2, 8.3)`, `8.*` → `[8, 9)`, `8.2.1.*` → `[8.2.1, 8.2.2)`
**And** a pre-release branch (`8.2rc1.*`), `8.3.x`, an inverted range (`8.4.* <8.2`) and every row that is `None` today stay `None`

**Given** a compound range `>=X <Y`, `>=X <=Y`, `>X <Y` or `>X <=Y` (also with `≥`/`≤` and with or without spaces after the operators)
**When** it is parsed
**Then** it returns the matching range with the right inclusivity on each side, and an empty or inverted one returns `None`

**Given** a placeholder lower bound `* <X`, `* <=X` or `unspecified <X` (any case)
**When** it is parsed
**Then** the range is unbounded below (`* <8.2.29` → `v < 8.2.29`)

**Given** the recorded EUVD record of EUVD-2026-107260 (PHP2Shell) in `tests/fixtures/euvd/euvd_php_branch_wildcard.json`, captured from the live API, with its four `PHP` rows `8.2.* <8.2.35` to `8.5.* <8.5.12`
**When** it goes through the EUVD adapter and the matcher
**Then** `php >= 8.5` matches it through the `8.5.* <8.5.12` row with known versions (not `versions_unknown`), `php >= 8.2` and `php` match it, and `php >= 9.0` does not match it (FR-13)
**And** table tests in `tests/unit/domain/test_version.py` cover every shape above, including `reaches()` at the branch boundaries (`8.2.*` against `8.3`: false; `8.2.* <8.2.35` against `8.2.34`: true, against `8.2.35`: false)

**Given** the documentation
**When** the story is done
**Then** the `version.py` module docstring and `docs/sources.md` (EUVD products) list the understood version formats, `tests/fixtures/README.md` gives the capture command of the new fixture, and the Story 1.2 deferred item in `deferred-work.md` is struck through except for what remains (`<= *`, standalone `0`)
**And** publications already made are not retracted (unchanged publication rules)

## Epic 2: European advisory sources

Feeds also carry CERT-EU advisories, merged per CVE into the same vulnerabilities as EUVD, so a critical pre-announcement reaches the channel before the CVE is published (PHP2Shell: CERT-EU was ~7.5 h ahead of EUVD). Source API drift is detected nightly. FR-2b and FR-5 are deferred after v1 (sprint change proposal 2026-11-01, epic 2 scope).

### Story 2.1: CERT-EU adapter

As a channel subscriber,
I want CERT-EU advisories in my feed, merged with the EUVD record of each CVE they cover,
So that urgent European advisories (like PHP2Shell) reach my channel before anyone posts them by hand.

**Acceptance Criteria:**

**Given** the `cert_eu` adapter
**When** it fetches the advisories RSS (latest 10) and the JSON of each new or revised advisory
**Then** it emits one `SourceRecord` per CVE ID found in the advisory, with `record_id` `<serial>:<CVE>` and aliases = that CVE + the advisory alias `CERT-EU-SA<serial>`, so an advisory covering several CVEs never merges their vulnerabilities; an advisory without any CVE ID emits one record with the advisory alias only (FR-2, FR-7, FR-8)
**And** CVE IDs come from `content_markdown`; the `# Affected Products` bullets become affected products (free text, versions unknown unless `domain/version.py` parses them); the description is the JSON `description`; the link is the advisory page; XML is parsed without entity resolution
**And** a revised advisory (new RSS `pubDate`) is fetched again, so a CVE added by a revision gets its own record (CVE-2026-115560 in 2026-018 v1.2), and a revision alone never re-publishes (FR-19)
**And** `published_at` is the front-matter `original_date`, `updated_at` is the JSON `publish_date` read as UTC; the cursor is the latest RSS `pubDate` seen (`next_cursor` from source data)

**Given** recorded fixtures in `tests/fixtures/cert_eu/` (multi-CVE revised advisory 2026-018, single-CVE advisory, advisory without CVE, malformed payload)
**When** the contract tests run
**Then** each case is covered, each with a `live` variant
**And** a pipeline test replays PHP2Shell: the CERT-EU records publish one `new` item per CVE with an unknown score; the later EUVD records merge into them and publish `[UPDATE]` on the unknown → critical band rise; three CVEs stay three vulnerabilities; `publish.skipped` stays silent
**And** `docs/sources.md` (payload assumptions, UTC dates, per-CVE records) and `settings.example.yaml` document the source

### Story 2.2: Source-drift detection

As the operator,
I want to be told when a source API changes,
So that a silent upstream change never empties our feeds.

**Acceptance Criteria:**

**Given** `.github/workflows/source-drift.yml`
**When** it runs nightly or manually (`workflow_dispatch`)
**Then** it runs the `live` contract tests against the real APIs and, on failure, opens or updates a single issue labelled `source-drift` with the failing tests (NFR-5)
**And** the job has least-privilege `permissions:` (`contents: read`, `issues: write`), third-party actions pinned by commit SHA, and a `timeout-minutes`
**And** `docs/operations.md` explains what a `source-drift` issue means and how to close it

## Epic 3: Shared configuration store, operator CLI and release

Configuration moves to Valkey so other tools can edit product lists; the operator manages endpoints, products and tokens from the CLI with either provider, and can inspect the store in an optional administration UI; versioned images and Kubernetes manifests are published.

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
**And** options are kebab-case (`--min-cvss`, `--kev-only`, `--require-score`), `list` commands support `--json`, user errors exit 2 with one line, unexpected errors exit 1 with a trace only under `--debug`
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

### Story 3.4: Valkey administration UI

As the operator,
I want a web UI to browse and edit the Valkey configuration store, ready to use in the dev stack and available on Kubernetes when I enable it,
So that I can inspect and fix the configuration without typing `valkey-cli` commands with a user and password each time.

**Acceptance Criteria:**

**Given** `deploy/valkey/users.acl`
**When** Valkey starts with it
**Then** a `cvestream-ui` user (SHA-256 hash, dev password in `deploy/valkey/dev-secrets/cvestream-ui.password`) can read and write every `cvestream:v1:*` key, tokens included, and run the browse commands the chosen UI needs (`SCAN`, `TYPE`, `TTL`, ...)
**And** it cannot touch keys outside `cvestream:v1:*` or run `KEYS`, `FLUSHALL`, `FLUSHDB`, `CONFIG`, `ACL`, `DEBUG`, `MODULE` or scripting; a `valkey`-marked integration test proves each grant and each refusal
**And** the existing users are unchanged

**Given** the dev stack
**When** the operator runs `docker compose up` (no profile, no extra variable)
**Then** a `valkey-ui` service starts with a pinned web UI image (exact version, licence recorded in the story), already connected to `valkey` as `cvestream-ui` with the password read from a compose secret, and published on `127.0.0.1` only; telemetry is disabled when the image offers it
**And** Valkey itself still publishes no host port

**Given** `deploy/k8s/components/valkey-ui`
**When** an overlay lists it under `components:`
**Then** `kubectl kustomize` produces a `Deployment` admitted under the `restricted` Pod Security Standard, a ClusterIP `Service`, no `Ingress`, a `NetworkPolicy` (UI egress to Valkey and DNS only) and a patch admitting the UI pod in the Valkey `NetworkPolicy`, with the password from a placeholder Secret
**And** the example overlay does not enable it, CI builds an overlay that does, and `tests/unit/test_deploy_manifests.py` guards these properties

**Given** a write from the UI
**When** it skips the revision bump or writes an invalid value
**Then** the daemon applies it at the latest on its next forced reload, or rejects it with `config.rejected` and keeps the last valid configuration (FR-23), as already implemented; no daemon change
**And** `docs/operations.md` ("Valkey administration UI") explains access (`http://127.0.0.1:<port>`, `kubectl port-forward`), recommends the CLI for writes and `INCR cvestream:v1:revision` after a UI edit, and how to add `cvestream-ui` to a production `users.acl` with its own hash; `docs/config-contract.md` lists the user; `compose.yml`'s header and `README.md` mention the UI
**And** `architecture.md` and `project-context.md` are amended in the same PR (ACL users and rules, dev compose, Kubernetes component, project tree)

---

## Epic 4: Matching and scoring enhancements

Enhancements to the matching engine and scoring rules based on real-world usage feedback from the trial.

### Story 4.1: require_score endpoint rule

As a channel subscriber,
I want an optional endpoint rule to drop vulnerabilities without any score from any source,
so that my busy endpoint does not receive many "severity unknown" items that I cannot triage yet, while still keeping exploited vulnerabilities even without a score.

Added from GitHub issue wixyvir/cvestream#43. This addresses the real case: a BI platform endpoint with `min_cvss: 4.0` still gets every fresh OpenSSL record before EUVD scores it (e.g. EUVD-2026-126927, EUVD-2026-126928, EUVD-2026-126929, all "severity unknown" on the day they were published).

**Acceptance criteria:**

**Given** `domain/endpoint.Rules` and the v1 config contract
**When** the rule is added as `require_score: bool = False` after `kev_only`, and `EndpointConfig.require_score` is a strict `bool` defaulting to `False`
**Then** `Rules().require_score is False` (`Rules()` still means "no filtering"), `to_domain()` passes the field through, `require_score: "yes"` or `1` is rejected with the path `endpoints.0.require_score`, and `schemas/config-v1.json` is regenerated with `SCHEMA_VERSION` staying `1`

**Given** an endpoint with `require_score: true`
**When** a vulnerability has no score from any source (`[]`, `[None]`) and is not known-exploited
**Then** it is dropped, after product matching and `kev_only`, before `min_cvss`

**Given** an endpoint with `require_score: true`
**When** a vulnerability is known-exploited (CISA KEV or EU KEV) with no score
**Then** it is published; with `min_cvss` set it keeps the `score_unknown` label

**Given** an endpoint with `require_score: true`, with or without `min_cvss`
**When** a vulnerability has a score (a real `0.0` counts; with `min_cvss: 7.0`, a score of 9.8)
**Then** it is published

**Given** an endpoint with `require_score: false` (the default)
**When** a vulnerability has no score
**Then** it is published with the `score_unknown` label if `min_cvss` is set (FR-25 regression)

**Given** an endpoint with `require_score: true`
**When** a vulnerability initially has no score and later receives a score at or above `min_cvss`
**Then** it is published once, as `new` (GUID `cvestream:{endpoint_id}:{primary_id}:new`)

**Given** an endpoint with `require_score: true`
**When** a vulnerability initially has no score and later becomes known-exploited
**Then** it is published as `new`

### Story 4.2: Exact product matching

As a channel subscriber,
I want an optional product-entry field to require the whole affected-product name to match,
so that short, generic names like `php` or `python` do not pull in many unrelated products whose names or vendors contain them.

Added from GitHub issue wixyvir/cvestream#44. This addresses the measured problem: every EUVD record updated since 2026-08-01 that mentions `php` results in 75 matches with only 13 being actual PHP Group/PHP, and `python` results in 38 matches with only about 3 being CPython.

## Epic 5: Readable bursts

Built from the team's feedback canvas "CVEStream Retours" (sprint change proposal 2026-11-21, epic 5). Slack's RSS app shows about the first 495 characters of an item, as plain text, one message per item.

### Story 5.1: Compact item body and title markers

As a channel subscriber,
I want each Slack message to show the severity, the exploited status, the dates, the matched product and the start of the description, and the title to flag what matters with an emoji,
so that I can triage a message without opening it and spot the ones that matter while scrolling.

**Acceptance criteria:**

**Given** any feed item
**When** its description is rendered
**Then** the blocks come in this order:
1. a facts line, `<severity band> · CVSS <highest score> · exploited: <yes|no> · published <day> · updated <day>`, where the `updated` part is left out when it falls on the same UTC day as `published`, and an unknown value reads `unknown`;
2. the matched products line, `Matched: …`, with `(versions unknown)` when it applies;
3. the full description, never cut on the server;
4. the affected products;
5. then the IDs line, each source's CVSS score with its version, the KEV detail (each list, the day it added the vulnerability, ransomware use) when it is known-exploited, the sources and the references.

**Given** affected products from several sources, or several rows for one product
**When** the Affected line is rendered
**Then** each product label (vendor dropped when the product name already contains it) appears once, followed by its rows' version text **verbatim** (never parsed or rewritten), at most 3 per product, then `(+N more)`

**Given** the Tomcat fixture (CVE-2026-95517: five affected rows, CVSS 9.1, not exploited)
**When** the description is reduced to plain text (tags stripped, a line break counted as one character)
**Then** its first 495 characters contain the severity band, `exploited: no`, the matched product and the first 40 characters of the description; the budget is one named constant

**Given** an item's decision-time values
**When** its title is rendered
**Then** it starts with these Unicode emoji, in this order, all that apply, followed by one space:
- 🔥 when the stored `is_known_exploited` is true;
- 🔴 when the stored band is `critical`;
- 🔺 when the kind is `band-<band>` and the band is `medium` or above (review decision D1 of wixyvir/cvestream#51);
- 🆕 when the stored `is_fresh` is true.

Every other band and the unknown state get no emoji, and there is no "safe" marker. The existing words (`EXPLOITED`, `[UPDATE]`, `severity critical`) stay. No Slack shortcode (`:fire:`) is used.

**Given** a `new` decision
**When** the vulnerability's earliest `published_at` falls on the same UTC calendar day as the decision time from the injected `Clock`
**Then** `PublicationDecision.is_fresh` is true and stored in a new nullable `publication.is_fresh` column (Alembic migration); it is false when `published_at` is unknown or on another day, and false for every `kev` and `band-*` decision; existing rows are `NULL` and render without 🆕

**Given** a feed rendered twice from the same state
**When** the clock has moved past midnight in between
**Then** the XML and the ETag are identical (render stays clock-free)

**Given** short items whose body fits in the budget (the PHP posts of 2026-11-19 showed no `Description:` line in Slack)
**When** the story starts
**Then** the cause is investigated and either fixed or recorded in `deferred-work.md`

Examples (golden):
- `🔥🔴🆕 CVE-2026-95517 in Apache Software Foundation Apache Tomcat: severity critical, EXPLOITED`
- `🆕 CVE-2026-107494 in OpenSSL: severity unknown`
- `🔴🔺 [UPDATE] CVE-2026-118028 in Apache Software Foundation Apache Tomcat: severity raised to critical`
- `🔥 [UPDATE] CVE-2026-1234 in PHP Group PHP: now EXPLOITED, severity high`

Documentation: the story 1.6 rules in `architecture.md`, the item format in `docs/operations.md` (a golden example and the budget), README, CHANGELOG.

### Story 5.2: Group bursts by product

As a channel subscriber,
I want a burst of items about the same product to arrive as one message,
so that ten Tomcat CVEs published together do not fill my channel with ten tall messages.

**Acceptance criteria:**

**Given** the v1 config contract
**When** `group_by_product: bool = False` is added to `domain/endpoint.Rules` and `EndpointConfig` (strict bool)
**Then** `Rules()` keeps its "no filtering, no grouping" meaning, `group_by_product: "yes"` is rejected with the path `endpoints.0.group_by_product`, and `schemas/config-v1.json` is regenerated in the same commit with `SCHEMA_VERSION` staying `1`

**Given** an endpoint with `group_by_product: true` and one pipeline run
**When** at least 3 decisions share the matched entry name (`products[0]`) and the kind family (`new`, or `band-*`) and none of their vulnerabilities is known-exploited
**Then** they are grouped: a pure function in `domain/publication.py`, called by `pipeline/run.py` after every `decide()` of the run and inside the run's transaction, returns the groups, ordered by band descending then by primary ID (natural CVE order)

**Given** a group of more than 5 members
**When** it is formed
**Then** it is split in that order into items of at most 5 members (6 members → 5 + 1, and a chunk below 3 is still published as a group, because the burst reached the threshold)

**Given** a group or chunk
**When** it is recorded
**Then** its GUID `cvestream:{endpoint_id}:group-{family}:{h}`, where `h` is the first 16 hex characters of the SHA-256 of its sorted member GUIDs, is built only in `domain/publication.py` and stored once. Each member keeps its own `publication` row and GUID and stores the group GUID in a new nullable `publication.group_guid` column (Alembic migration, upgrade and downgrade tested)

**Given** a stored group
**When** the feed is rendered
**Then**:
- **Item:** one item replaces its members, with the group GUID, the members' `pubDate`, and the link of the most severe member.
- **Title:** `<emoji> <product label>: <n> new vulnerabilities, worst <band> (<count per band>)`, or `<emoji> [UPDATE] <product label>: <n> vulnerabilities raised (<count per new band>)`.
  - The emoji: 🔴 when any member is critical, 🔺 for the `band-*` family, 🆕 when **any** member is fresh.
  - A split group adds `, part <i> of <k>`.
- **Body:** one story-5.1 one-liner per member, most severe first, `<CVE> · <band> <score> · exploited: no · <first affected version text> (+N more)`, each CVE linked.

**Given** an endpoint with `group_by_product: false` (the default), 2 decisions of the same product, a mix of `new` and `band-*`, or a known-exploited member
**When** the run is decided
**Then** those decisions are published as single items exactly as today

**Given** a member of a published group
**When** it later becomes known-exploited or its band rises
**Then** it is decided by `decide()` as today and published on its own (or in a new group of its run, if not known-exploited)

**Given** a restart, or the same run replayed
**When** the feed is rendered again
**Then** no group or member is published twice, the group GUID is read from storage and never recomputed, and the GUID does not depend on the order of the input decisions

Documentation: architecture (publication rules, GUID table, `publication` table), `docs/configuration.md`, `docs/operations.md`, `endpoints.example.yaml`, README, CHANGELOG.

### Story 5.3: Held-back count in cycle.summary

As the operator,
I want the number of items an endpoint's `require_score` held back in each cycle,
so that a rule that silently drops CERT-EU-only and freshly published items is visible.

Added from GitHub issue wixyvir/cvestream#47 (decision D3 in the review of wixyvir/cvestream#45).

**Acceptance criteria:**

**Given** an endpoint with `require_score: true`
**When** a cycle matches a vulnerability on products and `kev_only` but drops it because no source has scored it
**Then** `cycle.summary` reports it in a per-endpoint `held_back` count next to `score_unknown` and `versions_unknown`; a vulnerability that does not match the products is not counted

**Given** `match()`
**When** it drops a vulnerability
**Then** the caller can tell `require_score` from every other reason (a drop reason, or the rule evaluated by the pipeline), and `domain` stays pure

Documentation: `docs/operations.md` (`cycle.summary` fields).

### Story 5.4: Reject duplicate product names within an endpoint

As the operator,
I want the configuration to reject two product entries with the same name in one endpoint,
so that the item title, and story 5.2's groups, never pick the wrong entry.

Added from GitHub issue wixyvir/cvestream#48 (decision D2 in the review of wixyvir/cvestream#46).

**Acceptance criteria:**

**Given** an endpoint whose `products` contain two entries whose names are equal after stripping and case-folding
**When** the configuration is validated (YAML now, Valkey later through the same `EndpointConfig`)
**Then** it is rejected with the path of the second entry and a message naming the duplicate, and `cvestream config validate` exits non-zero

**Given** distinct names, including an alias of one entry used as another entry's name
**When** validated
**Then** it is accepted (the workaround documented for exact entries keeps working)

Documentation: `docs/configuration.md` (remove the "not rejected yet" note), CHANGELOG (a contract tightening: configs that load today can be rejected).
