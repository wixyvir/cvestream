---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md
  - _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/addendum.md
  - _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/reconcile-brief.md
  - _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/review-rubric.md
  - _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/.decision-log.md
  - _bmad-output/planning-artifacts/briefs/brief-cvestream-2026-10-26/brief.md
  - _bmad-output/planning-artifacts/briefs/brief-cvestream-2026-10-26/addendum.md
  - _bmad-output/planning-artifacts/briefs/brief-cvestream-2026-10-26/.decision-log.md
workflowType: 'architecture'
lastStep: 8
status: 'complete'
completedAt: '2026-10-26'
project_name: 'cvestream'
user_name: 'Cyprien'
date: '2026-10-26'
userStatedConstraints:
  - 'Runtime targets: the same container image must run under Docker (incl. compose), Kubernetes, and locally.'
  - 'Source hosting and CI/CD: GitHub and GitHub Actions.'
  - 'Key-value datastore technology: architect to propose options (decision in step 4).'
  - 'Config KV store must be a standard, separate network service so that other (future) tools can edit cvestream configuration, e.g. BoM-tool connectors or CD workers.'
  - 'The config store lives on a private interface, independent of the public interface that serves the RSS feeds.'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements (27, in 5 groups):**

| Group | FRs | Architectural meaning |
|---|---|---|
| F1 Source ingestion | FR-1, 2, 3, 4, 6 (2b, 5 deferred) | One adapter per source (EUVD, CERT-EU, CISA KEV). Each has its own schedule and its own failure isolation. Incremental polling (EUVD `fromUpdatedDate`) needs a cursor per source that survives restarts. Deferred after v1: a CERT-FR alerte adapter (FR-2b) and generic RSS/JSON adapters (FR-5) would be further adapters. |
| F2 Correlation and identity | FR-7, 8, 9, 24 | A canonical vulnerability, keyed by CVE/EUVD/advisory ID aliases and never by URL. Every value records which source supplied it. "Unknown" is a value in its own right. The highest score wins for decisions, but every source's score is kept. |
| F3 Endpoints and filtering | FR-10 to 16, 25 | A matching engine: case-insensitive whole-word matching on structured product data, the description only as a fallback for a vulnerability without affected-product data (ignoring names inside file names and paths, and skipped for entries with `match_description: false`); an entry with `exact: true` instead matches the whole affected-product name only (no vendor, no description). Aliases, exclusion terms, minimum versions parsed from free text, and fail-open labels ("versions unknown"). Filters: CVSS threshold, KEV-only and an opt-in `require_score` (drops unscored vulnerabilities except known-exploited ones). Product entry names are unique per endpoint (story 5.4). Recall wins over volume by default. A new endpoint is backfilled with 7 days of matches. |
| F4 RSS output | FR-17 to 20, 26, 27 | A publication engine that decides new, update (`[UPDATE]` with its own GUID) or nothing, per vulnerability and endpoint, and, on an endpoint with `group_by_product`, groups a same-product burst of one run into one item (story 5.2). It needs history: last severity band and last KEV state. An RSS 2.0 renderer with a 14-day window and no Slack-specific markup. Feed URLs carry a token that can be rotated. |
| F5 Configuration | FR-21, 22, 23 | Pluggable providers (Valkey or YAML). A CLI that performs create/list/edit/delete through the active provider. Changes apply at the next poll. Invalid configuration means keeping the last valid one and logging the error. |

**Non-Functional Requirements that drive the architecture:**

- **NFR-1 Freshness (≤ 1 h):** sets the upper bound on poll intervals. Slack's own polling delay comes on top and is outside our control.
- **NFR-3 Persistence (no re-post on restart):** the publication decision and its record must be atomic. This is the strongest constraint on the state store.
- **NFR-4 Operability:** structured logs per poll (result per source, item counts per endpoint, items let through because data was unknown).
- **NFR-5 Source resilience:** adapters isolated from the core model, contract tests against recorded payloads, unexpected payloads become source errors and never crash the daemon.
- **NFR-2 Deployment:** container image, HTTPS through a reverse proxy.
- **NFR-6 Availability:** best effort. Allows a single instance.

**Scale and complexity:**

- Primary domain: backend data-pipeline daemon with an HTTP feed server and an admin CLI
- Complexity level: medium-low (single tenant, low volume: around 100–300 EUVD updates a day and a handful of endpoints, no real-time needs, no regulatory constraints)
- Estimated architectural components: about 11. They are the source adapters, scheduler/poller, normalizer, correlator/merger, state store, matcher, publication engine, RSS renderer + HTTP server, configuration providers + validator, CLI, and logging/observability.

### Technical Constraints & Dependencies

- **Language and tooling:** Python, managed with `uv`. Hot reload during development. All development happens in a container (dev container or compose-based loop).
- **Runtime targets:** the same image must run under Docker (incl. compose), Kubernetes, and locally.
- **Source hosting and CI/CD:** GitHub, GitHub Actions.
- **Configuration store, starting position:**
  - The configuration that other tools edit (endpoints, product entries, rules, tokens) lives in a **standard, separate network service on a private interface**, so future tools (BoM connectors, CD workers) can edit it. Candidate: Valkey (to confirm in the decisions step).
  - Static runtime configuration (sources, poll intervals, listening address, store connection) comes from a file and environment variables, following the Prometheus split between what the daemon runs and what it watches.
  - The YAML provider remains for simple, single-tool setups.
  - The configuration schema is a **public, versioned contract**.
  - The daemon's internal state is kept apart from the shared configuration store (exact store to decide).
- **External dependencies:** EUVD API (unversioned, no auth, non-ISO dates with no timezone, free-text versions), CISA KEV JSON, CERT-EU Security advisories (RSS of the latest 10 + JSON per advisory).
- **Consumer:** Slack's RSS app is the reference consumer. It is assumed to de-duplicate on GUID.
- **Out of scope that limits the design:** no Slack app, no GUI, no authentication on feeds beyond the URL token, no NVD.

### Cross-Cutting Concerns Identified

1. **Identity and idempotency:** stable IDs (never URLs), GUIDs per endpoint and per update, publish-once that survives restarts.
2. **Provenance and "unknown" semantics:** every value carries its source. Unknown is never zero. This affects the model, the matcher, the renderer and the logs.
3. **Isolating source changes:** an adapter boundary, a normalized internal model, contract tests, error containment.
4. **Configuration as a contract:** schema versioning, validation on read, keeping the last valid configuration, concurrent writers, a revision counter for change detection.
5. **Separating public and private interfaces:** only the feed paths are public. The configuration store, the CLI and any health or metrics endpoint stay private.
6. **Time handling:** all timestamps normalized to UTC. Sources' dates have no timezone. Feed windows and the backfill depend on it.
7. **Single active poller:** two daemon instances would poll twice and race on publication. Kubernetes runs one replica, or we use a lock (to decide).
8. **Observability:** structured logs per poll cycle, visible source failures, counts of items let through on unknown data.
9. **Version parsing:** free-text, multi-format version ranges feed into a fail-open comparison.
10. **Portability across Docker, Kubernetes and local runs:** configuration through environment and file, health probes, graceful shutdown, no assumptions about the host.

## Starter Template Evaluation

### Primary Technology Domain

Python backend daemon: a polling pipeline, an HTTP server for the RSS feeds and an admin CLI, packaged as one container image. It talks to Valkey on a private network. There is no frontend.

### Starter Options Considered

| Option | Status (checked 2026-10-26) | What it brings | Why it fits or doesn't |
|---|---|---|---|
| **`uv init` (Astral, official)** | uv 0.13.5, released 2026-10-22 | `pyproject.toml`, `src/` layout, a CLI entry point, `uv_build` backend, `uv.lock`, `.python-version` | Minimal and always current. Nothing to strip out. The tooling is added in story 1 |
| **Astral `uv-docker-example`** (reference, not a generator) | Active (last update 2026-10-21) | Multi-stage Dockerfile (non-root user, `UV_COMPILE_BYTECODE`, cache mounts, a final image without uv), `compose.yml` with `develop.watch` (sync + rebuild on `uv.lock` change) | The official pattern for containers, and for hot reload in compose. Used as the model for the Dockerfile and compose file |
| `osprey-oss/cookiecutter-uv` (formerly fpgmaas) | 1.3k stars, last update 2026-10-10 | GitHub Actions, pre-commit, ruff, mypy/ty, deptry, pytest, tox-uv, Dockerfile, devcontainer, docs, PyPI publishing | Built for libraries. Weak Dockerfile (root, single stage, Python 3.12, `uv:latest`). Devcontainer on Python 3.11 with no compose. Useful parts are cheap to add by hand |
| `pawamoy/copier-uv` | 158 stars, last update 2026-10-20 | ruff, ty, pytest, Zensical, the `duty` task runner. Copier supports updating projects from the template | The README says it is mainly for the author's own use. An unusual task runner |
| FastAPI full-stack template | n/a | Postgres, React, auth | Far too much. Not considered further |

### Selected Starter: `uv init` + Astral's uv-docker-example patterns

**Rationale for Selection:**

- The pieces that matter most for this project are the multi-stage non-root image, the compose-based dev loop with Valkey, portability across Docker, Kubernetes and local runs, and GitHub Actions. No template provides them in a usable form, so they are written once, following Astral's official patterns.
- No library-oriented leftovers (PyPI publishing, tox matrices, codecov) to remove.
- Always tracks the current uv.

**Initialization Command:**

```bash
uv init --app --build-backend uv_build --python 3.14 --name cvestream .
```

`--build-backend` implies `--package`, which gives a `src/cvestream/` layout and a `cvestream` console entry point.

**Architectural Decisions Provided by Starter:**

**Language & Runtime:** Python 3.14 (pinned in `.python-version` and `requires-python`). Base images `python:3.14-slim-trixie` at runtime and `ghcr.io/astral-sh/uv` (Python 3.14 variant, tag pinned to a fixed uv version) for the build. Python 3.15 (3.15.0 released 2026-10-09) is deferred until the dependencies ship 3.15 wheels.

**Styling Solution:** n/a (no UI).

**Build Tooling:** `uv_build` backend, `uv.lock` committed, `uv sync --locked`. Multi-stage Dockerfile: the builder stage runs `uv sync --locked --no-dev` with cache mounts. The final image has no uv, a non-root user, and `PYTHONUNBUFFERED=1`.

**Testing Framework:** not provided by the starter. Added in story 1 (pytest, chosen in step 4).

**Code Organization:** `src/cvestream/` package with an entry point in `[project.scripts]`. Internal module layout is decided in step 6.

**Development Experience:**

- `compose.yml` with `develop.watch`: changed source files are synced into the container (hot reload), and the image is rebuilt when `uv.lock` changes. A Valkey service is part of the dev stack.
- A `.devcontainer/devcontainer.json` that points to that compose file, so all development happens in a container.
- Locally without containers: `uv run cvestream …`.

**Note:** Project initialization with this command, followed by the Dockerfile, compose file, devcontainer and CI workflow skeleton, should be the first implementation story.

Sources: [uv on PyPI](https://pypi.org/project/uv/), [Python releases](https://www.python.org/downloads/), [osprey-oss/cookiecutter-uv](https://github.com/osprey-oss/cookiecutter-uv), [pawamoy/copier-uv](https://github.com/pawamoy/copier-uv), [astral-sh/uv-docker-example](https://github.com/astral-sh/uv-docker-example), [uv project init docs](https://docs.astral.sh/uv/concepts/projects/init/).

## Core Architectural Decisions

_All versions verified online on 2026-10-26._

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**

- Configuration store: Valkey 9.1, with a public, versioned key layout (`cvestream:v1:*`) and a write protocol (WATCH/MULTI/INCR revision).
- State store: SQLite (WAL) on a volume, accessed through SQLAlchemy 2.1 Core behind a repository, migrations with Alembic. Written to stay portable to PostgreSQL.
- Runtime model: one asyncio process: source loops, one serialized pipeline worker run in a thread, two embedded uvicorn servers (public/private).
- Feed identity and access: `/feeds/{token}.xml`, token stored in plaintext in its own key namespace, unknown token → `404`.
- Source adapter contract (`SourceAdapter.fetch(cursor) -> FetchResult`) and error taxonomy.

**Important Decisions (Shape Architecture):**

- Pydantic v2 at boundaries (config, source payloads), frozen dataclasses for the domain model. Config JSON Schema exported from Pydantic.
- Starlette + uvicorn, httpx (pinned `<1.0`, wrapped), Typer CLI, asyncio scheduling.
- Valkey ACL users per role. Separate public/private listeners.
- GHCR multi-arch image, GitHub Actions (CI, release, nightly source-drift), Kustomize manifests, compose for dev and self-hosting.
- structlog JSON logs with a `cycle.summary` event (NFR-4). `/status` endpoint for staleness.

**Deferred Decisions (Post-MVP):**

- Prometheus `/metrics` (the private listener is ready for it).
- Image vulnerability scanning (Trivy/Grype), image signing (cosign).
- Litestream or continuous SQLite replication.
- PostgreSQL state backend (the state layer is written to be portable; enabling it = driver + URL + CI matrix).
- Helm chart (Kustomize first).
- HTTP-pull configuration provider (third provider, for connectors that prefer serving a list).
- Python 3.15 (when the dependencies ship 3.15 wheels).

### Data Architecture

**Configuration store: Valkey 9.1.2** (`valkey/valkey:9.1` image), client **`valkey` (valkey-py) 6.2.0**.

- Rationale: standard, separate network service on a private interface that other tools (BoM connectors, CD workers) can write to. Redis protocol, clients in every language, ACLs restricted by key pattern, BSD licence. `valkey-glide` (Rust core) and `redis` were considered and rejected as unnecessary or tied to Redis Inc.'s roadmap.
- Key layout (public contract, v1):

```
cvestream:v1:schema                   "1"
cvestream:v1:revision                 integer, INCR by every writer
cvestream:v1:endpoints                SET of endpoint ids
cvestream:v1:endpoint:{id}            JSON {name, enabled, min_cvss, kev_only, require_score, group_by_product}
cvestream:v1:endpoint:{id}:products   JSON [{name, aliases[], exclude[], min_version, match_description, exact}]
cvestream:v1:token:{id}               feed token (plaintext; own namespace so connector ACLs do not cover it)
```

- Write protocol: `WATCH cvestream:v1:revision` → `MULTI` → writes → `INCR cvestream:v1:revision` → `EXEC`. Retry on conflict.
- Read protocol (daemon): check `revision` at each pipeline run. If it changed, read a consistent snapshot: read `revision`, read all keys in one pipelined batch, read `revision` again; if it moved, retry (up to 3 times), otherwise keep the last valid configuration. Validate the whole snapshot. If invalid, keep the last valid configuration and log `config.rejected` (FR-23). Full reload every N cycles as a safety net. YAML provider: revision = SHA-256 of the file content.
- The configuration is never persisted by the daemon: the provider is the only source of truth (see startup behaviour under API & Communication Patterns).
- Values are plain JSON strings (no Valkey JSON module). Persistence: AOF (`appendonly yes`, `appendfsync everysec`).
- Schema: Pydantic models are the single source of truth. JSON Schema is exported to `schemas/config-v1.json`. `cvestream config validate` is available for other tools' CI. The YAML provider (`endpoints.yaml`) uses the same models. The document is `version: 1` plus `endpoints: [...]`, each endpoint carrying its feed `token` inline and a product list where an item is a full entry or the shorthand `name >= version`.

**State store: SQLite (WAL)** by default, `CVESTREAM_STATE__URL=sqlite:////data/cvestream.db`. **Designed to move to PostgreSQL later** with a driver and a URL change.

- Rationale: works in YAML mode without Valkey. Atomic "decide + record publication" transaction (NFR-3, SM-4). Real queries for the 14-day window and the 7-day backfill. Keeps private state apart from the shared configuration contract. A second Valkey instance was rejected. PostgreSQL is too much for v1 but must stay a cheap move (user decision).
- Access: **SQLAlchemy 2.1.6 Core** (no ORM) with a sync engine, behind `cvestream.state` repository functions. Tables declared once in `state/schema.py` (`MetaData` with a naming convention matching the patterns). Portable types only: `Boolean`, `JSON`, `Text`, `Integer`, and a `UTCDateTime` `TypeDecorator` (ISO 8601 text on SQLite, `timestamptz` on PostgreSQL; rejects naive datetimes). Upserts go through one `_upsert()` helper that picks the dialect's `insert().on_conflict_do_update()`. SQLite-specific settings (WAL, `foreign_keys=ON`, `busy_timeout`) are applied in a connect event, only for the SQLite dialect.
- Migrations: **Alembic 1.20.1**, scripts in `state/migrations/versions/`, `render_as_batch=True` so `ALTER`s work on SQLite. Run at startup and by `cvestream db migrate`.
- PostgreSQL later: add `psycopg` as an optional extra, set `CVESTREAM_STATE__URL=postgresql+psycopg://…`, add PostgreSQL to the CI matrix. No repository or migration rewrite expected.
- Logical tables: `vulnerability`, `alias`, `source_record`, `publication`, `source_cursor`, `feed_cache`.
- Correlation and publication rules (story 1.5): merge keys are CVE and EUVD aliases only (GHSA and advisory IDs never merge, a record without either is never merged); aliases are append-only (a source dropping an alias never splits a vulnerability); a record whose content is unchanged (hash ignoring `observed_at`) does not touch its vulnerability; only vulnerabilities active in the last 14 days (source activity time) are decided; an endpoint without any publication is backfilled with the last 7 days at every run until its first publication, and a product-list edit on an existing endpoint does not backfill; `kev` wins over a band rise in the same run (the decision records the current band); `unknown` to any rated band, `none` included, counts as a band rise. A decision whose GUID is already stored for another vulnerability (two advisory-only vulnerabilities sharing a primary ID) is skipped with a `publish.skipped` warning instead of aborting the run; this cannot happen with the built-in adapters (CERT-EU revisions keep their record ID); a CERT-FR alerte adapter (FR-2b, deferred) would have to make revisions merge first.
- 🆕 freshness (story 5.1): `decide()` sets `is_fresh` on a `new` decision when the vulnerability's earliest `published_at` falls on the decision's UTC calendar day. It is stored, never recomputed, and `NULL` on rows older than the column.
- Grouping (story 5.2, FR-27), on an endpoint with `group_by_product`: after every `decide()` of the run, `domain/publication.py` groups decisions that share the matched entry name and the kind family (`new`, `band-*`), with at least 3 members and none known-exploited. A group is ordered by band descending, then primary ID, and split into items of at most 5. Each group or chunk gets the GUID `cvestream:{endpoint_id}:group-{family}:{sha256(sorted member GUIDs)[:16]}`, stored in each member's `publication.group_guid`. A member's later `kev` or `band-*` decision is independent of its group.
- Storage: the SQLite volume must be block storage (not NFS/SMB/CIFS, whose file locking is unreliable for SQLite).
- Retention: vulnerabilities and publications kept indefinitely (late KEV additions). Raw payloads kept 30 days (configurable) for debugging and fixture capture.

**Validation and model:** Pydantic 2.14 at boundaries (config models, per-source payload models inside adapters; validation failure → source error). Internal domain model as `@dataclass(frozen=True, slots=True)`.

**Feed cache:** after each pipeline run, the RSS XML for each endpoint is rendered, stored in `feed_cache` and swapped into memory. Served with `ETag`/`Last-Modified`. The last good content survives source failures (FR-6) and restarts.
Rules (story 1.6): every enabled endpoint is rendered at every run, inside the run's transaction, so the 14-day window slides even when nothing is published. The ETag is the quoted SHA-256 of the XML bytes; `Last-Modified` is the last time the ETag changed (`feed_cache.changed_at`), never the render time; the channel `lastBuildDate` is the newest item's date and is absent without items, so an unchanged feed keeps its ETag and readers get `304`. An item's title takes the band and the known-exploited flag recorded at decision time, and names the affected product (vendor and product, from the current merged data) that the first matched entry hits, product-name hits before vendor-only ones, or the entry's name when none does, from structured product data only: CERT-EU affected-product text is prose and never names the title (story 2.1); known-exploited reads `EXPLOITED`. The title starts with Unicode markers built from decision-time values only, in this order: 🔥 known-exploited, 🔴 band `critical`, 🔺 kind `band-*`, 🆕 `publication.is_fresh`. All that apply are shown, each beside its word, and nothing marks other bands or the unknown state (story 5.1). Its body comes from the current merged data of the vulnerability (dates included: the earliest `published_at` and the newest `updated_at` of the sources, and each KEV list's earliest `added_at`, as UTC days), as an HTML fragment (RSS 2.0 `description` is entity-encoded HTML). Slack's RSS app shows about the first 495 plain-text characters, so the body leads with what a reader needs (story 5.1): a facts line (band · highest CVSS · exploited · published · updated when it is another day), the matched products, the full description, the affected products (each label once, source version text verbatim, at most 3 rows per product, then `(+N more)`), then the IDs, each source's CVSS, the KEV detail, the source records and the references as links; every value is HTML-escaped before the tags are added and only `http`/`https` URLs become links, so source text never injects markup. A known token whose endpoint has no stored feed yet (first start, new endpoint) gets an empty channel until its first run.

### Authentication & Security

- **Feed access (FR-20):** URL `/feeds/{token}.xml`. The token alone resolves the endpoint. Token = `secrets.token_urlsafe(32)` (256 bits), stored in plaintext under `cvestream:v1:token:{id}` (user decision: the operator must be able to retrieve the URL with `cvestream endpoint show`). Rotation takes effect at the next config reload. Unknown or revoked token → `404`. Rate limiting is left to the reverse proxy.
- **Interfaces:** public listener `:8080` serves only `GET`/`HEAD /feeds/{token}.xml`. Private listener `:9090` serves `/healthz`, `/readyz`, `/status`. Valkey is on a private network only. The CLI has no network endpoint.
- **Valkey ACL users:** `default` disabled. `cvestream-daemon`: read-only on `cvestream:v1:*`. `cvestream-admin` (CLI): read/write on `cvestream:v1:*`. `connector-<name>`: read on `cvestream:v1:schema`, `cvestream:v1:revision`, `cvestream:v1:endpoint*`, write on `cvestream:v1:endpoint:*:products` and `cvestream:v1:revision`. Exact ACL lines are written and tested in the Valkey provider story (`%R~`/`%W~` syntax confirmed in the Valkey docs). Credentials from env or a mounted secret file. TLS (`rediss://`) optional, recommended across hosts.
- **Outbound:** direct connections only, no proxy support (`trust_env=False`, so proxy variables in the environment are ignored). TLS verification always on, against the image's system trust store (`ssl.create_default_context()`, Debian `ca-certificates`). Timeouts, response size limits, per-source backoff.
- **Untrusted input:** source payloads validated by Pydantic. XML parsed with the standard library (`xml.etree.ElementTree`), a DOCTYPE is refused so no entity is resolved (story 2.1). RSS output built with an XML serializer, source text escaped as plain text.
- **Container:** UID 999, read-only root filesystem (writable `/data`, `/tmp`), all capabilities dropped, no privilege escalation.
- **Logs:** tokens never logged; access logs record the endpoint ID. Docs warn that reverse-proxy logs contain the token.

### API & Communication Patterns

- **Process model:** one asyncio process (`cvestream run`):
  - one loop per source (async fetch, own interval + jitter) → pipeline queue;
  - one pipeline worker, serialized, executed with `asyncio.to_thread`: reload config if revision changed → normalize → correlate → match → decide publications → SQLite transaction → render → swap in-memory feed cache;
  - embedded `uvicorn.Server` × 2 (public, private) in the same loop.
  - HTTP handlers only read the in-memory feed cache, so asyncio code never touches SQLite.
  - a source loop reads its cursor from the state before each fetch and waits for the run that carries its outcome; every fetch outcome, failed ones included, triggers one cycle (the worker drains a backlog into a single run).
  - `cvestream poll --once` runs one cycle and exits. SIGTERM: stop loops, finish the current pipeline run, stop servers.
- **Startup behaviour:** migrate the state store → start the private listener (`/healthz` OK, `/readyz` not ready) → load the configuration from the provider. With the Valkey provider, the daemon **only runs if Valkey is available**: it retries with backoff for `valkey.startup_timeout` (default 60 s), then exits with code 3 and logs `config.unavailable`. Nothing is persisted as a fallback. Once the configuration is loaded: start the source loops and the public listener, `/readyz` becomes ready.
- **Valkey outage while running:** the in-memory last valid configuration keeps being used, polling and feeds continue, and each pipeline run logs a `config.unavailable` warning until Valkey is back.
- **First run:** the initial cursor for each source is now − 14 days (the feed window), so the 7-day backfill (FR-16) works from day one. KEV dumps are loaded in full. An incremental cursor older than the feed window (long outage) is clamped to now − 14 days with a `source.cursor_clamped` warning, so a backlog never exceeds the page cap and the source heals itself.
- **Libraries:** Starlette 1.10.0, uvicorn 0.54.0, httpx 0.28.1 (pinned `>=0.28,<1`, wrapped in `cvestream.http`; httpx 1.0 is in development), Typer 0.27.3, watchfiles 1.3.0 (dev reload). Rejected: FastAPI (OpenAPI unnecessary), Granian (hard to embed), APScheduler (unnecessary), aiohttp (kept as a fallback for httpx).
- **Routes:**

| Listener | Route | Behaviour |
|---|---|---|
| public | `GET`/`HEAD /feeds/{token}.xml` | `application/rss+xml; charset=utf-8`, `ETag`, `Last-Modified`, `304`, `Cache-Control: max-age=300`, unknown token → `404` |
| private | `/healthz` | liveness (event loop responds) |
| private | `/readyz` | configuration loaded from the provider and state store open; independent of sources |
| private | `/status` | JSON: per source activity (`idle`, `fetching`, `processing`, `crashed`) with its start and the next fetch, last success/error, cursor; per endpoint item count, last render |

- **Source adapters:** `SourceAdapter` protocol (`name`, `async fetch(cursor) -> FetchResult(records, next_cursor)`). Built-in: `euvd`, `cert_eu`, `cisa_kev` (generic RSS/JSON adapters, FR-5, are deferred after v1). The `cert_eu` adapter emits one record per (advisory, CVE) pair (record ID `<serial>:<CVE>`, aliases = the CVE + `CERT-EU-SA<serial>`) so that a multi-CVE advisory never merges vulnerabilities; an advisory without CVE emits one advisory-only record. Sequential pages, short pauses, exponential backoff. The cursor is an opaque string owned by the adapter (`next_cursor` comes from source data, never from the local clock; the fetch time only caps it, so a future-dated source record cannot park it ahead). The `euvd` adapter also loads `/api/kev/dump` in full on every fetch and emits `eu_kev` records (an EU KEV addition does not change `dateUpdated`).
- **Error taxonomy:** `SourceTransportError`, `SourcePayloadError`, `SourceContractError` (source level: logged, cursor not advanced, others continue). Record-level failure: skipped and counted. `SourceTransportError.status_code` carries the final HTTP status; an adapter that fetches one document per record (`cert_eu`) treats a final 4xx other than 429 on that document as a record-level skip, while a 5xx, a 429 or a network error stays source level (story 2.1 review). Invalid config: last valid kept. Unexpected exception in a loop: supervisor logs and restarts the loop with backoff. The process never crashes because of a source.
- **CLI (Typer):** `run`, `poll --once`, `healthcheck [--ready]`, `endpoint create|list|show|edit|delete`, `product add|remove|list`, `token rotate`, `config validate|schema|export|import`, `db migrate|backup`.
- **`cvestream healthcheck`:** calls `http://127.0.0.1:9090/healthz` (or `/readyz` with `--ready`) with the standard library (`urllib`, no proxy handler, 2 s timeout) and exits 0 when healthy, 1 otherwise. It is used by the Docker `HEALTHCHECK` and by the Kubernetes `exec` probes, so the image needs no `curl`. It imports neither the app nor the providers, so it stays fast.
- **Inter-service communication:** Valkey only (configuration). No message queue.

### Frontend Architecture

Not applicable: no UI. RSS is the only output contract.

### Infrastructure & Deployment

- **Image:** multi-stage `Dockerfile`: builder `ghcr.io/astral-sh/uv:<pinned>-python3.14-trixie-slim`, runtime `python:3.14-slim-trixie`, UID 999, volume `/data`, `EXPOSE 8080 9090`, `HEALTHCHECK CMD ["cvestream", "healthcheck"]`, `ENTRYPOINT ["cvestream"]`, `CMD ["run"]`. A `dev` target adds dev dependencies and watchfiles. Multi-arch (amd64, arm64). Published to `ghcr.io/wixyvir/cvestream` with tags `X.Y.Z`, `X.Y`, `latest` (release), `edge` (main), `sha-<short>`. OCI labels, SBOM and provenance (`mode=max`).
- **Source repository:** [github.com/wixyvir/cvestream](https://github.com/wixyvir/cvestream), default branch `main`.
- **Delivery workflow:**
  - Planning artifacts (brief, PRD, architecture, epics) were committed directly to `main` while bootstrapping the repository.
  - From the first story on, **each story is delivered as one pull request** to `main`, from a branch named `story-<epic>-<story>-<slug>` (no `/` in branch names, e.g. `story-1-1-project-init`).
  - PR title: Conventional Commit style with the story ID (`feat(story-1.1): initialize project skeleton`). The PR description links the story file and ends with the Claude Code attribution line.
  - The stories of an epic may be delivered as a **linear stack of PRs**: each PR's base is the previous story's branch, and it is retargeted to `main` when its parent merges. `ci.yml` runs on every `pull_request`, whatever its base. A change to a lower PR is propagated by rebasing the upper branches in order (`uv.lock` regenerated with `uv lock`, never merged by hand) and pushing them with `git push --force-with-lease`.
  - Commits inside a PR stay separated by concern and are not squashed.
  - A PR is merged only when `ci.yml` is green. Once story 1 has added CI, `main` is protected: PRs only, required `ci` status check.
- **GitHub Actions** (versions at decision time: checkout v7.0.1, setup-uv v10.3.2, build-push-action v7.4.0, metadata-action v6.2.0, login-action v4.7.0, setup-buildx-action v4.4.1, setup-qemu-action v4.4.0, attest-build-provenance v4.2.2):

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | PR (any base), push to `main` | `ci` job: `uv sync --locked` → ruff check + format check → ty → import-linter → pytest (unit, contract on fixtures, provider tests against a `valkey/valkey:9.1` service). then build the runtime image (amd64, no push) and smoke-test `--help`, all in the one job, so `ci` stays the only required check and a broken Dockerfile cannot merge. Then `publish` (push to `main`: amd64 + arm64, push `edge` and `sha-<short>`, `packages: write`) |
| `release.yml` | tag `v*` | tag = `pyproject.toml` version check → multi-arch build → push GHCR → provenance attestation → GitHub Release |
| `source-drift.yml` | nightly + manual | contract tests against the live APIs; on failure opens/updates an issue labelled `source-drift` |

  All third-party Actions pinned by commit SHA (version in a comment). Least-privilege `permissions:` per job. Dependabot for `uv`, `github-actions`, `docker`, weekly.
- **Deployment targets:**
  - Dev: `compose.yml` (`app` target `dev` with `develop.watch`, `valkey` with `deploy/valkey/users.acl`, private network) + `.devcontainer/` pointing to it.
  - Docker self-hosted: `deploy/compose/compose.yml` (GHCR image, named volumes, `restart: unless-stopped`, only 8080 published; reverse proxy and HTTPS provided by the operator).
  - Kubernetes: `deploy/k8s/` Kustomize base + example overlay: `Deployment` (replicas 1, `strategy: Recreate`, hardened `securityContext`, `terminationGracePeriodSeconds: 60` so the current pipeline run can finish), RWO `PersistentVolumeClaim` on `/data` (block storage, not NFS/SMB), `Service` `cvestream-public` (8080) and `cvestream-private` (9090), example `Ingress` for the public Service only, `NetworkPolicy`, `exec` probes using `cvestream healthcheck` (`startupProbe` and `readinessProbe` with `--ready`, `livenessProbe` without), minimal example Valkey `StatefulSet` (or an existing Valkey).
  - Local: `uv run cvestream run` with the YAML provider and `./data`.
- **Backups:** Valkey RDB snapshot + `cvestream config export` (YAML, can live in git). SQLite: `cvestream db backup <file>` (SQLite online backup API; SQLite only, PostgreSQL would use `pg_dump`).
- **Scaling:** single replica by design (documented). RWO volume prevents two writers.
- **Runtime configuration:** pydantic-settings 2.16: `CVESTREAM_*` env vars override an optional `settings.yaml` (`CVESTREAM_SETTINGS_FILE`). Static settings: listeners (`http.public.host`/`port`, default `0.0.0.0:8080`, and `http.private.host`/`port`, default `0.0.0.0:9090`), `data_dir`, `state.url` (SQLAlchemy URL, default SQLite under `data_dir`), `config_provider` (`valkey` | `yaml`), `valkey.url` + password file + `valkey.startup_timeout`, sources with intervals (default 15 min, max 60 for NFR-1), adapter settings, log level/format. `endpoints.yaml` (YAML provider) is a separate file.
- **Logging:** structlog 26.1, JSON in production, console in dev, dot-separated event names (`poll.completed`, `source.failed`, `publish.decided`, `config.rejected`). `cycle.summary` per run: per-source result, per-endpoint published count, pass-throughs on unknown score and unknown versions, and items held back by `require_score` (NFR-4, story 5.3).
- **Code quality and tests:** ruff 0.17.2, ty 0.0.92 (beta; fallback mypy 2.4.0 with the same CI step), pytest 9.1.1 + anyio plugin, respx 0.23.1. Recorded payloads in `tests/fixtures/<source>/`. Pre-commit optional; CI is the gate.

### Decision Impact Analysis

**Implementation Sequence:**

1. Project initialization (`uv init`), Dockerfile, compose + devcontainer, CI skeleton.
2. Static settings (pydantic-settings) and structured logging.
3. Config models + JSON Schema + YAML provider + `config validate`.
4. State store (SQLAlchemy Core schema, `UTCDateTime`, Alembic, repository) on SQLite.
5. Source adapter contract + EUVD adapter + fixtures and contract tests; then CISA KEV, CERT-EU.
6. Normalization, correlation and provenance (FR-7/8/9/24).
7. Matcher (FR-11 to 15, 25) and version parsing.
8. Publication engine (FR-16, 19, 26) with atomic recording.
9. RSS renderer, feed cache, public and private listeners.
10. Runtime loop (`run`, `poll --once`, supervisor, graceful shutdown).
11. Valkey provider, ACL files, CLI write protocol.
12. CLI CRUD commands, token rotation, export/import.
13. Release workflow, source-drift workflow, deployment manifests (compose, Kustomize).

**Cross-Component Dependencies:**

- The config models (3) are shared by the YAML provider, the Valkey provider (11), the CLI (12) and the published JSON Schema: any change is a contract change and needs a schema version decision.
- The publication engine (8) depends on the state store's transaction boundary (4) and on the correlation output (6). The no-duplicate guarantee (NFR-3, SM-4) lives at this junction.
- The feed cache (9) decouples the HTTP listeners from SQLite and from the pipeline. It is the mechanism behind FR-6.
- The single-writer constraint (SQLite, one pipeline worker) is what forces one replica and `Recreate` in Kubernetes.
- Valkey key layout and ACLs (11) must stay aligned: a new key must be placed in a namespace that the intended ACLs cover, and tokens must never fall under a connector pattern.
- httpx is confined to `cvestream.http` and the adapters (5), so a move to httpx 1.0 or aiohttp only affects that boundary.

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

**Critical conflict points identified:** 26 areas where AI agents could make different choices. Python idioms (PEP 8) settle the rest.

### Naming Patterns

**Database naming (portable: SQLite now, PostgreSQL later):**

- Tables: singular `snake_case` (`vulnerability`, `source_record`, `publication`), declared only in `state/schema.py`.
- Primary key `id` (`Integer`, autoincrement). Foreign keys `<table>_id` (`vulnerability_id`).
- Timestamps: `<verb>_at` columns of type `UTCDateTime` (ISO 8601 UTC text with `Z` on SQLite, `timestamptz` on PostgreSQL).
- Booleans: SQLAlchemy `Boolean`, named `is_*`/`has_*` (`is_known_exploited`).
- JSON columns: SQLAlchemy `JSON`, suffix `_json` (`products_json`).
- Constraint names come from the `MetaData` naming convention: `pk_<table>`, `fk_<table>_<col>_<ref>`, `ix_<table>_<col>`, `ux_<table>_<col>`, `ck_<table>_<name>`.
- Portability rules: no raw dialect-specific SQL in the repository (no `INSERT OR REPLACE`, no `PRAGMA`, no `RETURNING` without a check), no SQLite-only types. Upserts only through `_upsert()`.
- Migrations: Alembic revisions in `state/migrations/versions/`, named `NNNN_<verb>_<object>.py` (`0002_add_feed_cache.py`), with batch operations. Never edit a migration that has been merged.

**Identifiers:**

| Thing | Format | Example |
|---|---|---|
| Endpoint ID | slug `[a-z0-9-]{1,40}` | `team-gitlab` |
| Source name | `snake_case` | `euvd`, `cert_eu`, `cisa_kev`, `eu_kev` (record source of EU KEV entries, emitted by the `euvd` adapter) |
| Vulnerability aliases | upper-case, normalized | `CVE-2026-109618`, `EUVD-2026-41234`, `CERT-EU-SA2026-018` |
| Primary display ID | CVE > EUVD > advisory ID | the first available, in that order |
| RSS GUID | `cvestream:{endpoint_id}:{primary_id}:{kind}` with `kind` = `new`, `kev`, `band-{band}` | `cvestream:team-gitlab:CVE-2026-109618:kev` |
| Severity band | lower-case enum | `unknown`, `none`, `low`, `medium`, `high`, `critical` |
| Valkey keys | lower-case, `:` separators, under `cvestream:v1:` | `cvestream:v1:endpoint:team-gitlab:products` |

The GUID is **computed once when the item is published, then stored in `publication.guid`**. It is never recomputed, so a later change of primary ID (for example, when a CVE ID arrives) never produces a new GUID.

**HTTP naming:** paths are lower-case and plural (`/feeds/{token}.xml`). Probes: `/healthz`, `/readyz`, `/status`. No custom headers.

**Code naming:** PEP 8. Modules and functions `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE`. Modules are singular nouns (`matcher.py`, not `matchers.py`). Private helpers start with `_`. Exceptions end in `Error`.

**CLI:** sub-commands and options in `kebab-case` (`cvestream endpoint create --min-cvss 7.0`). Nouns first, then verbs (`endpoint create`, not `create-endpoint`).

**Environment variables:** prefix `CVESTREAM_`, nested settings joined with `__` (`CVESTREAM_VALKEY__URL`, `CVESTREAM_SOURCES__EUVD__INTERVAL`). Secrets come from `*_FILE` variables (`CVESTREAM_VALKEY__PASSWORD_FILE`).

### Structure Patterns

**Layered dependencies (enforced by import-linter 2.15 in CI):**

```
cli ──────────▶ app ──────────▶ pipeline ──────────▶ domain
 │               │                 │                    ▲
 │               ├──▶ sources ─────┼──(base types)      │
 │               │                 │                    │
 ├───────────────┴──▶ config ◀─────┤ (provider protocol)│
 └───────────────────▶ state ◀─────┘                    │
          config · sources · state ─────────────────────┘
```

The "Component boundaries (allowed imports)" list in the Project Structure section is the authoritative rule. This diagram illustrates it.

- `domain`: pure (no I/O, no third-party imports apart from the standard library and `packaging`, the only allowed exception, used by `domain/version.py` and whitelisted in the import-linter contract). It holds the model, matching, version parsing, bands and the publication decision.
- Only `sources/*` and `cvestream.http` import `httpx`. Only `state/` imports `sqlalchemy` and `alembic`. Only `config/valkey_provider.py` imports `valkey`. Only `app/` imports `starlette`/`uvicorn`. Only `cli/` imports `typer`. Ownership is about direct imports: the import-linter ownership contracts set `allow_indirect_imports = true` (a `pipeline` module may import `state`, which imports `sqlalchemy`); the `layers` contract still forbids upward imports.
- `pipeline` orchestrates synchronously. `app` owns asyncio, the loops and the servers.

**Tests:**

- `tests/unit/` mirrors `src/cvestream/`. `tests/contract/<source>/` holds the adapter contract tests. `tests/integration/` covers SQLite and Valkey end to end.
- `tests/fixtures/<source>/<case>.json|xml` holds payloads recorded from the real APIs, never hand-written. They are named after what they show (`euvd_no_score.json`, `euvd_version_na.json`).
- Markers: `live` (real network, deselected by default, run by `source-drift.yml`), `valkey` (needs a server).
- By default, **pytest-socket** blocks the network (`--disable-socket --allow-unix-socket`: the asyncio event loop needs a Unix socketpair, TCP and DNS stay blocked). HTTP is mocked with respx.

**Files:** static settings `settings.yaml`, YAML provider `endpoints.yaml`, JSON Schema `schemas/config-v1.json`, Valkey ACLs `deploy/valkey/users.acl`. Docs live in `docs/`. Specs stay in `_bmad-output/`.

### Format Patterns

**Missing and unknown values:**

- `None` **means unknown**. Typed as `X | None`. Never 0, `""`, `"n/a"` or `-1`. Adapters convert every placeholder they see (`"n/a"`, `""`, `0.0` when the source uses it to mean "unscored") to `None`.
- Display: the renderer writes `unknown`. Logs write `null`.

**Provenance:** every value that comes from a source is a `Sourced[T]` (`value`, `source`, `observed_at`). Merging keeps all `Sourced` values. Decision helpers (`max_score()`, `band()`) compute from them and never overwrite them.

**Time:**

- Always aware `datetime` in UTC (`datetime.now(UTC)` through the injected `Clock`). Naive datetimes are forbidden (ruff `DTZ` rules).
- JSON and logs use ISO 8601 with `Z`. RSS `pubDate` is RFC 822 via `email.utils.format_datetime`.
- Adapters convert source dates that have no timezone (EUVD) to UTC, following a documented assumption per source.

**JSON (configuration contract, `/status`, logs):** `snake_case` fields. Booleans `true`/`false`. Configuration models use `extra="forbid"`. No envelope on success (`/status` returns the object directly). Errors on the private API: `{"error": {"code": "snake_case_code", "message": "..."}}`. The public `404` has an empty body.

**Numbers:** CVSS score is a `float` with one decimal place. `cvss_version` is a string (`"3.1"`, `"4.0"`). EPSS (if displayed) is a fraction between 0 and 1. The ×100 value from EUVD is converted in the adapter.

**Versions:** all parsing of free text goes through `domain/version.py`, which returns `VersionRange | None`. `packaging.version` is used where the format allows. Nothing else parses versions.

### Communication Patterns

- **No internal event bus.** The pipeline is a chain of pure functions that pass immutable dataclasses: `SourceRecord → NormalizedRecord → Vulnerability → MatchResult → PublicationDecision`.
- **Immutability:** domain objects are frozen. Updates go through `dataclasses.replace`. The feed cache and the active configuration are swapped by **replacing the whole reference**, never mutated in place.
- **Logs as events:**
  - Names are `<area>.<event>`, with `area` from a fixed list (`app`, `source`, `pipeline`, `config`, `publish`, `feed`, `http`, `state`) and the event as a past participle (`source.fetched`, `source.failed`, `config.rejected`, `publish.decided`, `cycle.completed`).
  - Standard fields: `source`, `endpoint_id`, `vuln_id`, `count`, `duration_ms`, `error_type`.
  - Never `token`, never a full feed path.
  - Levels: `info` for each cycle and each publication, `warning` for a source failure or a rejected configuration, `error` for an unexpected exception (with `exc_info`), `debug` for detail per record.

### Process Patterns

**Errors:**

- Every exception of ours inherits from `CvestreamError`. Source errors inherit from `SourceError` (`SourceTransportError`, `SourcePayloadError`, `SourceContractError`).
- No bare `except:`. `except Exception` is allowed **only** in supervisors (the source loop, the pipeline worker, an HTTP handler) and always logs with `exc_info`. Nothing is swallowed silently.
- CLI: a user error prints one plain line to stderr with exit code 2. An unexpected error exits with code 1 and shows the stack trace only with `--debug`.

**Retries:**

- Retries happen only in `cvestream.http`: idempotent GETs, retried on timeout, 5xx and 429 (honouring `Retry-After`), with exponential backoff plus jitter and a cap on attempts.
- The pipeline never retries: the next cycle is the retry. The source cursor only moves forward after a successful fetch.

**Validation timing:** only at the boundaries (configuration load, adapter parsing, CLI input). The domain assumes valid input and never re-validates.

**Transactions:** a single `with state.transaction():` per pipeline run, covering the publication decisions, the publication rows, the cursors and `feed_cache`. No `commit()` anywhere else.

**Async and sync:** `async` only in `app/`, the `fetch` methods in `sources/`, and `cvestream.http`. Everything else is synchronous. No blocking I/O in async code (go through `asyncio.to_thread`). No `asyncio.run` outside the CLI's entry point.

**Testability:** time comes from an injected `Clock`, never from calling `datetime.now()` directly. Randomness (tokens, jitter) comes from injectable functions.

**Staleness (replaces "loading states", since there's no UI):** each source exposes `last_success_at` and `last_error` in `/status`, plus its current `activity` (`idle`, `fetching`, `processing`, `crashed`), `activity_since` and `next_fetch_at`, so a long first fetch is told apart from a source that never ran. A `source.stale` warning is logged when `now - last_success_at > 3 × interval`.

**Typing:** all public functions are fully annotated. Don't add `from __future__ import annotations` (annotations are deferred by default in Python 3.14). `ty check` passes in CI.

**Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`). One concern per commit.

**Branches and PRs:** one story = one branch `story-<epic>-<story>-<slug>` = one PR to `main`, or to the previous story's branch when an epic is delivered as a stack of PRs (see "Delivery workflow" under Infrastructure & Deployment). No `/` in branch names. No direct pushes to `main` once story 1 is merged.

### Enforcement Guidelines

**All AI agents MUST:**

- Respect the layered dependencies (import-linter is the gate).
- Represent unknown as `None` and keep every `Sourced` value.
- Use aware UTC datetimes through `Clock`.
- Store GUIDs and never recompute them.
- Write state only inside `state.transaction()`.
- Parse versions only through `domain/version.py`.
- Add or update a recorded fixture with every adapter change.
- Treat any change to the configuration models as a **contract change**: regenerate `schemas/config-v1.json` and bump the schema version if it isn't backward compatible.

**Pattern enforcement:**

- CI: `ruff check` (with `DTZ`, `BLE`, `S` rules enabled), `ruff format --check`, `ty check`, `lint-imports`, `pytest` with sockets blocked, plus a check that `schemas/config-v1.json` is up to date.
- Violations found in review are written down as an issue labelled `architecture`. A pattern changes only by amending this document in the same PR.

### Pattern Examples

**Good:**

```python
# adapter: placeholder → None, date without timezone → UTC, provenance kept
score = Sourced(value=None if raw.baseScore in (None, "", 0) else float(raw.baseScore),
                source="euvd", observed_at=clock.now())

# publication: GUID stored at decision time
row = PublicationRow(endpoint_id=ep.id, vulnerability_id=v.id, kind="kev",
                     guid=f"cvestream:{ep.id}:{v.primary_id}:kev", published_at=clock.now())

log.info("publish.decided", endpoint_id=ep.id, vuln_id=v.primary_id, kind="kev")
```

**Anti-patterns:**

```python
score = raw.get("baseScore", 0)            # ✗ unknown becomes 0
datetime.now()                             # ✗ naive time, not injected
guid = make_guid(v)                        # ✗ recomputed at render time
except Exception: pass                     # ✗ swallowed
import httpx  # in domain/matcher.py       # ✗ breaks the layering
log.info("feed.served", path=request.url.path)  # ✗ logs the token
```

## Project Structure & Boundaries

### Complete Project Directory Structure

```
cvestream/
├── README.md                         # what it is, quick start (compose, k8s, local)
├── CLAUDE.md                         # agent rules: points to architecture.md, key MUSTs
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml                    # deps; [tool.ruff] [tool.ty] [tool.pytest.ini_options] [tool.importlinter]
├── uv.lock
├── .python-version                   # 3.14
├── .gitignore
├── .dockerignore
├── .pre-commit-config.yaml           # optional: ruff, ty, uv lock --check
├── Dockerfile                        # stages: builder → dev | runtime
├── compose.yml                       # dev: app (target dev, develop.watch) + valkey
├── settings.example.yaml             # static settings, every key documented
├── endpoints.example.yaml            # YAML provider example
├── .devcontainer/
│   ├── devcontainer.json             # dockerComposeFile: ../compose.yml + compose.devcontainer.yml, service: app
│   └── compose.devcontainer.yml      # bind-mounts the repository (compose-based devcontainers ignore workspaceMount)
├── .github/
│   ├── dependabot.yml                # uv, github-actions, docker
│   └── workflows/
│       ├── ci.yml
│       ├── release.yml
│       └── source-drift.yml
├── schemas/
│   └── config-v1.json                # generated from config/models.py; freshness checked in CI
├── deploy/
│   ├── valkey/
│   │   ├── valkey.conf               # appendonly yes, appendfsync everysec, aclfile
│   │   └── users.acl                 # default off; cvestream-daemon, cvestream-admin, connector-example
│   ├── compose/
│   │   └── compose.yml               # self-hosted: GHCR image, named volumes, only 8080 published
│   └── k8s/
│       ├── base/
│       │   ├── kustomization.yaml
│       │   ├── configmap-settings.yaml
│       │   ├── deployment.yaml       # replicas 1, Recreate, hardened securityContext, probes on 9090
│       │   ├── pvc.yaml              # RWO, /data
│       │   ├── service-public.yaml   # 8080
│       │   ├── service-private.yaml  # 9090
│       │   └── networkpolicy.yaml
│       └── overlays/example/
│           ├── kustomization.yaml
│           ├── ingress.yaml          # public Service only
│           ├── valkey-statefulset.yaml
│           └── secret.example.yaml
├── docs/
│   ├── configuration.md              # settings.yaml + CVESTREAM_* variables
│   ├── config-contract.md            # for third-party tools: key layout v1, write protocol, ACL examples, JSON Schema
│   ├── operations.md                 # deploy, backup/restore, token rotation, logs, /status
│   └── sources.md                    # per-source behaviour and assumptions (timezones, placeholders, pagination)
├── src/cvestream/
│   ├── __init__.py                   # __version__
│   ├── __main__.py                   # python -m cvestream → cli.main
│   ├── errors.py                     # CvestreamError (root of every exception of ours)
│   ├── clock.py                      # Clock protocol, SystemClock
│   ├── log.py                        # structlog setup (JSON / console), redaction
│   ├── settings.py                   # pydantic-settings: Settings, SourceSettings, ValkeySettings
│   ├── http.py                       # httpx wrapper: timeouts, size limits, retries, system trust store, no proxy
│   ├── domain/                       # pure: stdlib + packaging only
│   │   ├── __init__.py
│   │   ├── model.py                  # Sourced[T], SourceRecord, NormalizedRecord, Vulnerability, AffectedProduct
│   │   ├── identity.py               # alias normalization, primary-ID precedence
│   │   ├── severity.py               # Band enum, band_of(), max_score()
│   │   ├── version.py                # VersionRange parsing and comparison (the only parser)
│   │   ├── endpoint.py               # Endpoint, ProductEntry, Rules (domain objects)
│   │   ├── matcher.py                # match(vulnerability, endpoint) → MatchResult | None
│   │   └── publication.py            # decide(...) → PublicationDecision; GUID construction
│   ├── config/
│   │   ├── __init__.py
│   │   ├── models.py                 # Pydantic contract models v1 (extra="forbid") → domain objects
│   │   ├── provider.py               # ConfigProvider protocol, ConfigSnapshot, ConfigError family, build_snapshot()
│   │   ├── keys.py                   # Valkey key layout constants and builders
│   │   ├── yaml_provider.py
│   │   ├── valkey_provider.py        # snapshot read; WATCH/MULTI/INCR write protocol
│   │   └── schema.py                 # JSON Schema export
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py                   # SourceAdapter protocol, FetchResult, Cursor, SourceError family
│   │   ├── registry.py               # name → adapter factory, built from settings
│   │   ├── euvd.py
│   │   ├── cisa_kev.py
│   │   └── cert_eu.py
│   ├── state/
│   │   ├── __init__.py
│   │   ├── db.py                     # engine from state.url, SQLite connect pragmas, transaction(), migrate()
│   │   ├── schema.py                 # MetaData + naming convention, tables, UTCDateTime
│   │   ├── codec.py                  # SourceRecord ⇄ JSON, content hash
│   │   ├── repository.py             # every query lives here; _upsert() per dialect
│   │   ├── backup.py                 # SQLite online backup API
│   │   └── migrations/               # Alembic, configured in code (no alembic.ini)
│   │       ├── env.py
│   │       ├── script.py.mako
│   │       └── versions/
│   │           └── 0001_create_schema.py
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── normalize.py              # SourceRecord → NormalizedRecord
│   │   ├── correlate.py              # merge into Vulnerability via aliases
│   │   ├── render.py                 # RSS 2.0 XML per endpoint (pure)
│   │   ├── summary.py                # CycleSummary → cycle.summary log
│   │   └── run.py                    # one pipeline run: reload config → … → transaction → render
│   ├── app/
│   │   ├── __init__.py
│   │   ├── runtime.py                # starts everything, supervisor, SIGTERM handling
│   │   ├── scheduler.py              # per-source loop with interval + jitter → pipeline queue
│   │   ├── feed_cache.py             # immutable token → rendered feed map, swapped by reference
│   │   ├── public_api.py             # Starlette: GET/HEAD /feeds/{token}.xml
│   │   ├── private_api.py            # /healthz /readyz /status
│   │   └── servers.py                # two embedded uvicorn.Server instances
│   └── cli/
│       ├── __init__.py
│       ├── main.py                   # Typer root: run, poll --once, --debug
│       ├── endpoint.py
│       ├── product.py
│       ├── token.py
│       ├── config.py                 # validate, schema, export, import
│       ├── db.py                     # migrate, backup
│       ├── healthcheck.py            # stdlib-only probe of 127.0.0.1:9090 (Docker HEALTHCHECK, k8s exec probes)
│       └── output.py                 # table / --json output helpers
└── tests/
    ├── conftest.py                   # FakeClock, tmp SQLite, provider fixtures, socket blocking
    ├── unit/
    │   ├── domain/                   # test_version.py, test_matcher.py, test_publication.py, …
    │   ├── config/
    │   ├── state/
    │   ├── pipeline/
    │   ├── app/
    │   └── cli/
    ├── contract/                     # one folder per source; `live` variants hit the real APIs
    │   ├── euvd/
    │   ├── cisa_kev/
    │   └── cert_eu/
    ├── integration/
    │   ├── test_pipeline_end_to_end.py
    │   ├── test_restart_no_repost.py # NFR-3 / SM-4
    │   ├── test_startup_requires_valkey.py  # marker valkey: exit code 3 after startup_timeout
    │   └── test_valkey_provider.py   # marker valkey
    └── fixtures/
        ├── euvd/
        ├── cisa_kev/
        └── cert_eu/
```

The existing `_bmad/`, `_bmad-output/` and `.claude/` folders stay where they are and are excluded from the image (`.dockerignore`).

### Architectural Boundaries

**API boundaries:**

| Boundary | Exposure | Who uses it |
|---|---|---|
| `app/public_api.py`: `/feeds/{token}.xml` | public (Ingress / reverse proxy) | Slack's RSS app, any RSS reader |
| `app/private_api.py`: `/healthz`, `/readyz`, `/status` | private (cluster / host) | probes, the operator |
| Valkey `cvestream:v1:*` (documented in `docs/config-contract.md`) | private network | the daemon (read), the CLI (read/write), connectors (restricted by access rules) |
| Outbound to sources | egress | `sources/*` only, through `http.py` |

**Component boundaries (allowed imports):**

- `domain` imports nothing from the project.
- `config`, `sources`, `state` import `domain` (plus `http`, `settings`, `errors`, `clock`).
- `pipeline` imports `domain`, `state`, `config` (provider protocol only), `sources/base.py` types.
- `app` imports `pipeline`, `sources`, `config`, `state` (open/migrate only), `settings`.
- `cli` imports `app` (for `run`), `config`, `state`, `settings`.
- All of this is written as import-linter contracts in `pyproject.toml`.

**Service boundaries:** one cvestream process and one Valkey. No other service. A reverse proxy or Ingress in front of the public port is provided by the operator.

**Data boundaries:**

- **Valkey:** shared configuration only. It's the contract that other tools write to. Never internal state.
- **State database (SQLite now, PostgreSQL possible later):** private state only. Only `state/repository.py` contains queries. Only `pipeline/run.py` opens the write transaction, apart from `cli db`. The configuration is never stored here.
- **In-memory:** `app/feed_cache.py` (rendered feeds) and the active `ConfigSnapshot`. Both are swapped by reference.

### Requirements to Structure Mapping

| FR / NFR | Location |
|---|---|
| FR-1, 2, 3 sources | `sources/euvd.py`, `cert_eu.py`, `cisa_kev.py` · `tests/contract/*` |
| FR-4 poll interval | `settings.py` (`SourceSettings.interval`, max 60 min), `app/scheduler.py` |
| FR-2b, FR-5 | deferred after v1 |
| FR-6 isolation, last good feed | `app/runtime.py` (supervisor), `app/feed_cache.py`, `state` `feed_cache` table |
| FR-7, 8 correlation, identity | `pipeline/correlate.py`, `domain/identity.py`, table `alias` |
| FR-9, 24 provenance, highest score | `domain/model.py` (`Sourced`), `domain/severity.py` |
| FR-10, 11 endpoints, product entries | `domain/endpoint.py`, `config/models.py` |
| FR-12, 13 matching, versions | `domain/matcher.py`, `domain/version.py` |
| FR-14, 15, 25 thresholds, KEV-only, `require_score` opt-out, recall first | `domain/matcher.py` |
| FR-16 7-day backfill | `domain/publication.py` + `state/repository.py` (window query) |
| FR-17, 18 RSS 2.0, item content | `pipeline/render.py` |
| FR-19, 26 updates, late match | `domain/publication.py`, table `publication` |
| FR-18 title markers, compact body | `pipeline/render.py`, `domain/publication.py` (`is_fresh`) |
| FR-27 burst grouping | `domain/publication.py`, `pipeline/run.py`, `pipeline/render.py`, table `publication` |
| FR-20 tokens | `config/keys.py`, `cli/token.py`, `app/public_api.py` |
| FR-21 providers | `config/provider.py`, `yaml_provider.py`, `valkey_provider.py` |
| FR-22 CLI | `cli/*` |
| FR-23 hot reload, keep last valid | `pipeline/run.py` (revision check), `config/provider.py` |
| NFR-1 freshness | `settings.py` validation, `app/scheduler.py` |
| NFR-2 container, HTTPS | `Dockerfile`, `deploy/*` |
| NFR-3 persistence, no re-post | `state/db.py` `transaction()`, `tests/integration/test_restart_no_repost.py` |
| NFR-4 operability | `pipeline/summary.py`, `log.py`, `app/private_api.py` `/status` |
| NFR-5 source resilience | `sources/base.py`, `tests/contract/*`, `.github/workflows/source-drift.yml` |
| NFR-6 best effort | single replica, `deploy/k8s/base/deployment.yaml` |

**Cross-cutting concerns:**

| Concern | Location |
|---|---|
| Time | `clock.py` (every module receives a `Clock`) |
| Errors | `errors.py`, `sources/base.py` |
| Logging and redaction | `log.py` |
| Outbound HTTP, retries, TLS (system trust store) | `http.py` |
| Health probes | `app/private_api.py`, `cli/healthcheck.py` |
| Configuration contract | `config/models.py`, `config/keys.py`, `schemas/config-v1.json`, `docs/config-contract.md` |
| Security (access rules, network) | `deploy/valkey/users.acl`, `deploy/k8s/base/networkpolicy.yaml`, `Dockerfile` (non-root) |

### Integration Points

**Internal communication:** source loops → `asyncio.Queue` → pipeline worker (`asyncio.to_thread(pipeline.run, …)`) → returns rendered feeds → `feed_cache.swap()`. HTTP handlers read only `feed_cache`.

**External integrations:** EUVD (`/api/search`, `/api/kev/dump`), CISA KEV JSON, CERT-EU advisories RSS + JSON, Valkey (RESP, optionally TLS), Slack's RSS app (consumer).

**Data flow:**

```
sources/* ──fetch──▶ SourceRecord ─▶ normalize ─▶ correlate ─▶ Vulnerability
                                                               │
          ConfigSnapshot (Valkey | YAML) ─▶ endpoints ─▶ matcher ─▶ MatchResult
                                                               │
                         state.publication (history) ─▶ publication.decide ─▶ PublicationDecision
                                                               │
                                    ┌── SQLite transaction (publication rows, cursors, feed_cache)
                                    └── render ─▶ feed_cache.swap() ─▶ GET /feeds/{token}.xml
```

### File Organization Patterns

- **Configuration files:** `pyproject.toml` holds every tool's configuration (no `ruff.toml`, `pytest.ini` or `.importlinter` files). Runtime examples sit at the root (`settings.example.yaml`, `endpoints.example.yaml`). Deployment configuration lives under `deploy/`.
- **Source organization:** grouped by layer, then by capability. One module per source adapter. No `utils.py`: a helper goes in the layer that owns it.
- **Test organization:** `unit/` mirrors `src/cvestream/`. `contract/` is organized by source. `integration/` covers flows across components. `fixtures/` holds recorded payloads.
- **Assets:** none (no UI). The generated `schemas/config-v1.json` is the only artefact that is committed.

### Development Workflow Integration

- **Development:** `docker compose watch` (or the devcontainer). The `app` service runs `watchfiles "cvestream run" src` on the `dev` target. `src/` changes are synced, `uv.lock` changes trigger a rebuild. Valkey runs with `deploy/valkey/users.acl`. Locally: `uv run cvestream run` with `settings.example.yaml` and the YAML provider.
- **Build:** `uv sync --locked` → `uv build` (wheel via `uv_build`) is used in the image's builder stage. The `runtime` target holds only `.venv` + `src`.
- **Deployment:** the same image runs as `deploy/compose/compose.yml` (Docker) or `deploy/k8s/overlays/<env>` (Kubernetes). Settings come from a ConfigMap or a mounted file plus `CVESTREAM_*` variables, and secrets from mounted files.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** Python 3.14 is supported by every chosen dependency (Pydantic 2.14, pydantic-settings 2.16, SQLAlchemy 2.1, Alembic 1.20, Starlette 1.10, uvicorn 0.54, httpx 0.28, valkey-py 6.2, Typer 0.27, structlog 26.1). valkey-py speaks RESP to Valkey 9.1. The single-process asyncio model, the single pipeline worker and SQLite's single writer reinforce one another and justify the single replica with `Recreate`. Keeping Valkey (shared contract) apart from the state database (private state) is consistent with the requirement that YAML mode works without Valkey. SQLAlchemy Core with portable types keeps PostgreSQL a configuration-level change.

**Pattern Consistency:** naming (snake_case JSON, `<area>.<event>` logs, `cvestream:v1:*` keys, GUIDs stored once, constraint naming convention) is applied the same way to the configuration contract, the logs, `/status` and the database. The process patterns (retries only in `http`, a single transaction per run, validation at the boundaries) follow the error taxonomy and the runtime model.

**Structure Alignment:** every package in the tree corresponds to a layer in the patterns. The allowed imports ("Component boundaries") are the authoritative reference for import-linter. Integration points (queue → worker in a thread → feed cache swap) appear both in the data flow and in the modules.

### Requirements Coverage Validation ✅

**Feature coverage:** F1 (sources), F2 (correlation), F3 (filtering), F4 (RSS), F5 (configuration) each have a dedicated package and tests (see "Requirements to Structure Mapping").

**Functional Requirements Coverage:** 27/27 FRs mapped to a decision and a file. FR-6 is covered by source isolation and the persisted feed cache. FR-16 works from the first run thanks to the 14-day initial cursor. FR-23 is covered by the consistent snapshot read and the in-memory last valid configuration.

**Non-Functional Requirements Coverage:**
- NFR-1: interval ≤ 60 min, validated in `settings.py`.
- NFR-2: multi-arch image, reverse proxy / Ingress, `HEALTHCHECK` and probes through `cvestream healthcheck`.
- NFR-3: single transaction, GUIDs stored, `test_restart_no_repost.py`.
- NFR-4: `cycle.summary` and `/status`.
- NFR-5: adapters, contract tests and `source-drift.yml`.
- NFR-6: single replica, best effort. With the Valkey provider, the daemon only starts when Valkey is available.
- Security (FR-20 + constraints): tokens, two listeners, Valkey access control, a hardened container, TLS against the system trust store.

### Implementation Readiness Validation ✅

**Decision Completeness:** every critical decision carries a version checked on 2026-10-26, a rationale and the alternatives rejected.

**Structure Completeness:** the tree is complete down to the file, including deployment, docs, fixtures, migrations and workflows.

**Pattern Completeness:** 26 conflict points covered, with good and bad examples, and enforcement in CI (ruff, ty, import-linter, pytest-socket, schema freshness check).

### Gap Analysis Results

- **Critical:** none.
- **Important (resolved, see below):** I1 to I6.
- **Minor (open):**
  - M1: project licence not chosen.
  - ~~M2: GHCR owner and repository name~~: resolved. Repository `github.com/wixyvir/cvestream`, image `ghcr.io/wixyvir/cvestream`.
  - M3: Slack RSS app behaviour (polling cadence, GUID de-duplication) is still a PRD `[ASSUMPTION]`, to check during the trial. The design copes either way (each update has its own GUID). Also check how Slack shows the HTML item description (expected: text without formatting).
  - M4: deferred items: Prometheus metrics, image scanning/signing, Litestream, Helm, HTTP configuration provider, PostgreSQL backend, Python 3.15.

### Validation Issues Addressed

- **I1, configuration unavailable at startup (user decision):** nothing is persisted as a fallback. With the Valkey provider, the daemon only runs if Valkey is available: it retries for `valkey.startup_timeout` (default 60 s), then exits with code 3 and logs `config.unavailable`. During a Valkey outage while running, the in-memory last valid configuration is used and a `config.unavailable` warning is logged each run. New `cvestream healthcheck [--ready]` command for the Docker `HEALTHCHECK` and the Kubernetes `exec` probes.
- **I2, consistent reads:** revision → pipelined read → revision again, retried up to 3 times, otherwise the last valid configuration is kept. YAML revision = SHA-256 of the file.
- **I3, first run:** initial cursor = now − 14 days for each source. KEV dumps loaded in full.
- **I4, state storage (user decision):** SQLite stays the default, on block storage (not NFS/SMB/CIFS), with `terminationGracePeriodSeconds: 60`. The state layer uses SQLAlchemy 2.1 Core + Alembic with portable types and naming so that PostgreSQL can be added later (driver + URL + CI matrix).
- **I5, outbound TLS (user decision):** no proxy support (`trust_env=False`), TLS verified against the system trust store.
- **I6, layering:** the patterns diagram is aligned with the "Component boundaries" list, which is authoritative.

### Architecture Completeness Checklist

**Requirements Analysis**

- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed
- [x] Technical constraints identified
- [x] Cross-cutting concerns mapped

**Architectural Decisions**

- [x] Critical decisions documented with versions
- [x] Technology stack fully specified
- [x] Integration patterns defined
- [x] Performance considerations addressed (low volume; in-memory feed cache, ETag/304, pipeline off the event loop)

**Implementation Patterns**

- [x] Naming conventions established
- [x] Structure patterns defined
- [x] Communication patterns specified
- [x] Process patterns documented

**Project Structure**

- [x] Complete directory structure defined
- [x] Component boundaries established
- [x] Integration points mapped
- [x] Requirements to structure mapping complete

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** high. The remaining open items are minor and don't affect the structure.

**Key Strengths:**

- A clear split between the shared configuration contract (Valkey, versioned, access-controlled) and private state (SQLite, portable to PostgreSQL).
- Publish-once guarantee built in: a single transaction, GUIDs stored and never recomputed.
- Resilience to source changes, checked by nightly drift tests against the real APIs.
- A pure, testable domain, with layering enforced automatically.
- The same image for Docker, Kubernetes and local runs, with a built-in healthcheck command.

**Areas for Future Enhancement:** Prometheus metrics, image scanning and signing, continuous SQLite replication or the PostgreSQL backend, Helm chart, HTTP configuration provider (BoM connectors), Python 3.15, daily digest mode (PRD v2).

### Implementation Handoff

**AI Agent Guidelines:**

- Follow all architectural decisions exactly as documented.
- Use implementation patterns consistently across all components.
- Respect project structure and boundaries (import-linter is the gate).
- Refer to this document for all architectural questions. Change a pattern only by amending this document in the same PR.

**First Implementation Priority:**

```bash
uv init --app --build-backend uv_build --python 3.14 --name cvestream .
```

Then: Dockerfile (targets builder / dev / runtime, `HEALTHCHECK`), `compose.yml` with Valkey and `develop.watch`, `.devcontainer/`, and the `ci.yml` skeleton (ruff, ty, import-linter, pytest).
