---
project_name: 'cvestream'
user_name: 'Cyprien'
date: '2026-10-26'
sections_completed:
  ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
existing_patterns_found: 26
status: 'complete'
rule_count: 87
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

_Versions verified online on 2026-10-26 (architecture.md). Before adding any dependency not listed here, check online that it exists and pick its current version._

- **Python 3.14** (`.python-version`, `requires-python`). Annotations are deferred by default: never add `from __future__ import annotations`. Python 3.15 is deferred.
- **uv 0.13.5** manages the project. This project uses uv, **not poetry** (this overrides any global poetry preference). Use `uv add`, `uv sync --locked` and `uv run`. Never `pip install`, never edit `uv.lock` by hand. Build backend `uv_build`, `src/cvestream/` layout, console script `cvestream`.
- **Runtime:** pydantic 2.14 · pydantic-settings 2.16 · SQLAlchemy 2.1.6 (**Core only, no ORM**, sync engine) · Alembic 1.20.1 (configured in code, no `alembic.ini`) · valkey-py 6.2.0 (server `valkey/valkey:9.1`) · Starlette 1.10.0 · uvicorn 0.54.0 · httpx 0.28.1 (pinned `>=0.28,<1`) · Typer 0.27.3 · structlog 26.1 · packaging (version comparison, the only third-party import allowed in `domain`) · XML in: stdlib `xml.etree.ElementTree`, DOCTYPE refused (story 2.1)
- **Dev:** ruff 0.17.2 · ty 0.0.92 (beta; fallback mypy 2.4.0 in the same CI step) · import-linter 2.15 · pytest 9.1.1 + anyio plugin · respx 0.23.1 · pytest-socket 0.8.1 · watchfiles 1.3.0
- **Images:** builder `ghcr.io/astral-sh/uv:<pinned>-python3.14-trixie-slim`, runtime `python:3.14-slim-trixie`. Always pin exact tags, never `latest`. Third-party GitHub Actions are pinned by commit SHA, with the version in a comment.
- **Rejected, do not introduce:** FastAPI, Granian, APScheduler, aiohttp (fallback only if httpx breaks), `redis`, `valkey-glide`, SQLAlchemy ORM, the Valkey JSON module, Litestream, Helm, NVD as a source.
- **PostgreSQL later:** it will be an optional `psycopg` extra plus a URL change. Never write code that works only on SQLite.

## Critical Implementation Rules

### Language-Specific Rules (Python)

- **Typing:** every public function is fully annotated; `ty check` must pass. Write unions as `X | None`.
- **Unknown is `None`:** never `0`, `""`, `"n/a"` or `-1`. Adapters turn every placeholder into `None`, including EUVD's `0.0` when it means "unscored". The renderer displays `unknown`, logs write `null`.
- **Domain model:** `@dataclass(frozen=True, slots=True)`, changed only through `dataclasses.replace`. Pydantic is for boundaries only (config models, per-source payload models inside adapters), never in `domain`.
- **Time:** aware UTC datetimes only, from the injected `Clock` (`clock.now()`). Never `datetime.now()`, `datetime.utcnow()` or naive datetimes (ruff `DTZ`). JSON and logs use ISO 8601 with `Z`. RSS `pubDate` uses `email.utils.format_datetime`. Source dates without a timezone (EUVD) are converted to UTC in the adapter, and the assumption is documented in `docs/sources.md`.
- **Randomness:** tokens (`secrets.token_urlsafe(32)`) and jitter come from injectable functions, so tests can be deterministic.
- **Async/sync:** `async` only in `app/`, source `fetch()` methods and `cvestream.http`. Everything else is sync. Blocking I/O from async code goes through `asyncio.to_thread`. `asyncio.run` only at the CLI entry point.
- **Exceptions:** every exception of ours inherits from `CvestreamError` and its name ends in `Error`. Source failures raise `SourceTransportError`, `SourcePayloadError` or `SourceContractError` (all `SourceError`). No bare `except:`. `except Exception` only in supervisors (source loop, pipeline worker, HTTP handler), always logged with `exc_info`. Never swallow an error.
- **Numbers:** a CVSS score is a `float` with one decimal place. `cvss_version` is a string (`"3.1"`, `"4.0"`). EPSS is a 0–1 fraction (EUVD's ×100 value is converted in the adapter).
- **Versions:** free-text version data is parsed only by `domain/version.py` → `VersionRange | None`. No other module parses versions.

### Framework & Architecture Rules

**Layering** (import-linter is the CI gate; "Component boundaries" in architecture.md is authoritative):
- `domain` imports nothing from the project. Only stdlib and `packaging`.
- `config`, `sources`, `state` → `domain` (+ `http`, `settings`, `errors`, `clock`).
- `pipeline` → `domain`, `state`, `config` (provider protocol only), `sources/base.py` types. Synchronous orchestration.
- `app` → `pipeline`, `sources`, `config`, `state` (open/migrate only), `settings`. Owns asyncio, loops and servers.
- `cli` → `app` (for `run`), `config`, `state`, `settings`.
- Library ownership: `httpx` only in `sources/*` and `cvestream.http` · `sqlalchemy`/`alembic` only in `state/` · `valkey` only in `config/valkey_provider.py` · `starlette`/`uvicorn` only in `app/` · `typer` only in `cli/`.

**Pipeline:** a chain of pure functions over frozen dataclasses: `SourceRecord → NormalizedRecord → Vulnerability → MatchResult → PublicationDecision`. No event bus. The pipeline never retries: the next cycle is the retry. Validation happens only at boundaries; the domain never re-validates.

**State (SQLAlchemy Core):**
- Tables only in `state/schema.py`; every query only in `state/repository.py`.
- Exactly one `with state.transaction():` per pipeline run, covering decisions, publication rows, cursors and `feed_cache`. Opened only by `pipeline/run.py` (and `cli db`). No `commit()` anywhere else.
- Portable types only: `Boolean`, `JSON`, `Text`, `Integer`, `UTCDateTime` (rejects naive datetimes). No dialect SQL (`INSERT OR REPLACE`, `PRAGMA`, unchecked `RETURNING`). Upserts only via `_upsert()`. SQLite pragmas only in the SQLite connect event.
- Alembic revisions `state/migrations/versions/NNNN_<verb>_<object>.py`, `render_as_batch=True`. Never edit a merged migration.
- A source cursor advances only after a successful fetch. Initial cursor = now − 14 days; an incremental cursor older than that is clamped to it (`source.cursor_clamped`). Paging stops on `total`, never on a short page.

**Configuration (Valkey / YAML):**
- `config/models.py` (Pydantic, `extra="forbid"`) is the single source of truth, shared by both providers and the CLI.
- Product entry names are unique per endpoint (whitespace runs collapsed, case-folded; a shorthand counts by its name), checked by `EndpointConfig` so every provider and the CLI get the rule (story 5.4). Code may look an entry up by its name.
- Valkey keys only through `config/keys.py`, under `cvestream:v1:*`. Values are plain JSON strings. A new key must fit the ACL patterns in `deploy/valkey/users.acl`. Tokens stay under `cvestream:v1:token:*`, never under a pattern a connector can read.
- Write protocol: `WATCH cvestream:v1:revision → MULTI → writes → INCR revision → EXEC`, retry on conflict.
- Read protocol: revision → pipelined read → revision again, at most 3 retries, otherwise keep the last valid config. An invalid snapshot keeps the last valid config and logs `config.rejected`. The daemon never persists config. YAML revision = SHA-256 of the file.
- Valkey down at startup: retry for `valkey.startup_timeout` (60 s), then log `config.unavailable` and exit with code 3. Down while running: keep the in-memory config and warn on each run.

**HTTP (Starlette + two embedded `uvicorn.Server`):**
- Public `:8080`: only `GET`/`HEAD /feeds/{token}.xml`, `application/rss+xml; charset=utf-8`, `ETag`/`Last-Modified`/`304`, `Cache-Control: max-age=300`. Unknown token → `404` with an empty body.
- Private `:9090`: `/healthz`, `/readyz`, `/status`. No envelope on success; errors are `{"error": {"code": "...", "message": "..."}}`.
- Handlers read only the in-memory feed cache, never SQLite or Valkey. The feed cache and the active config are swapped by replacing the whole reference, never mutated.
- Outbound only through `cvestream.http`: timeouts, size limits, retries (timeout/5xx/429 + `Retry-After`, backoff + jitter, capped), `trust_env=False`, TLS always verified against the system trust store.

**Sources:** `SourceAdapter` protocol (`name`, `async fetch(cursor) -> FetchResult(records, next_cursor)`), registered in `sources/registry.py`. A record-level failure is skipped and counted. Identity is the CVE, EUVD or advisory ID, never a URL. A CERT-EU advisory yields one record per CVE (`<serial>:<CVE>`), so a multi-CVE advisory never merges vulnerabilities. The cursor is an opaque string; `next_cursor` comes from source data, never from the local clock (the fetch time only caps it).

**CLI (Typer):** kebab-case, noun then verb (`endpoint create --min-cvss 7.0`). A user error prints one line to stderr and exits with code 2. An unexpected error exits with code 1 and shows the traceback only with `--debug`. `cvestream healthcheck` uses only the stdlib `urllib` and imports neither the app nor the providers.

**Settings:** pydantic-settings, prefix `CVESTREAM_`, nesting with `__`, secrets through `*_FILE` variables. Source interval ≤ 60 min (NFR-1). `sources.euvd.page_pause` (default 1.5 s, at most 60 s) paces EUVD paging below its gateway rate limit.

**Logging (structlog):** events named `<area>.<past-participle>`, area ∈ {`app`, `source`, `pipeline`, `config`, `publish`, `feed`, `http`, `state`}. Standard fields: `source`, `endpoint_id`, `vuln_id`, `count`, `duration_ms`, `error_type`. One `cycle.summary` per run (token rule: see Security below).

### Testing Rules

- **Layout:** `tests/unit/` mirrors `src/cvestream/` · `tests/contract/<source>/` holds adapter contract tests · `tests/integration/` covers flows across SQLite and Valkey · `tests/fixtures/<source>/<case>.json|xml`.
- **Fixtures are recorded from the real APIs, never hand-written.** Name them after what they show (`euvd_no_score.json`, `euvd_version_na.json`). Every adapter change adds or updates a fixture. Raw payloads kept 30 days in state are the capture source.
- **Network blocked by default** (pytest-socket, with `--allow-unix-socket` so anyio tests can run). Mock HTTP with respx. Real network only under the `live` marker.
- **Markers:** `live` (deselected by default, run nightly by `source-drift.yml`), `valkey` (needs a server; CI provides a `valkey/valkey:9.1` service).
- **Async tests** use the anyio pytest plugin, not pytest-asyncio.
- **No real time, no sleeps:** use `FakeClock` and injected randomness from `tests/conftest.py`, which also provides the tmp SQLite and provider fixtures.
- **Domain tests are pure:** no database, no network, no mocks of the layers below.
- **Guardrail tests that must stay green:** `test_restart_no_repost.py` (NFR-3 / SM-4, no re-post after restart) and `test_startup_requires_valkey.py` (exit code 3 after `startup_timeout`).
- **Cover the unknown paths:** for each adapter and the matcher, test missing score, missing or `n/a` versions, and naive source dates, not only the happy path.
- **Configuration contract:** CI fails if `schemas/config-v1.json` is stale. Regenerate it whenever `config/models.py` changes.
- pytest config lives in `pyproject.toml` (`[tool.pytest.ini_options]`), never in `pytest.ini`.

### Code Quality & Style Rules

- **All tool config in `pyproject.toml`** (`[tool.ruff]`, `[tool.ty]`, `[tool.pytest.ini_options]`, `[tool.importlinter]`). No `ruff.toml`, `.importlinter` or `pytest.ini`.
- **CI gate, run locally before pushing:** `uv run ruff check` (with `DTZ`, `BLE`, `S`), `uv run ruff format --check`, `uv run ty check`, `uv run lint-imports`, `uv run pytest`, plus the schema freshness check. Pre-commit is optional; CI is the gate.
- **Naming:** PEP 8. Modules are singular nouns (`matcher.py`, not `matchers.py`). Private helpers start with `_`. Exceptions end in `Error`.
- **Organization:** follow the tree in architecture.md down to file names; no new top-level packages without amending it. No `utils.py` or `helpers.py`: a helper lives in the layer that owns it. One module per source adapter.
- **Database naming:** tables singular `snake_case` · PK `id` (`Integer`, autoincrement) · FK `<table>_id` · timestamps `<verb>_at` (`UTCDateTime`) · booleans `is_*`/`has_*` · JSON columns end in `_json` · constraint names come only from the `MetaData` naming convention (`pk_`, `fk_`, `ix_`, `ux_`, `ck_`), never hand-written.
- **Identifiers:** endpoint ID slug `[a-z0-9-]{1,40}` · source names `euvd`, `cert_eu`, `cisa_kev`, `eu_kev` (record source of EU KEV entries, emitted by the `euvd` adapter) · aliases upper-case and normalized (`CVE-…`, `EUVD-…`, `CERT-EU-SA…`) · primary display ID precedence CVE > EUVD > advisory · severity band enum `unknown|none|low|medium|high|critical`.
- **JSON** (config contract, `/status`, logs): `snake_case` fields. **HTTP paths:** lower-case, plural, no custom headers.
- **Documentation follows behaviour in the same PR:** new or changed setting → `docs/configuration.md` + `settings.example.yaml` · adapter assumption (timezone, placeholders, pagination) → `docs/sources.md` · contract change → `docs/config-contract.md` · operational change → `docs/operations.md` · always check `README.md` and `CLAUDE.md`. Specs stay in `_bmad-output/`.

### Development Workflow Rules

- **Repository:** `github.com/wixyvir/cvestream`, default branch `main`, image `ghcr.io/wixyvir/cvestream`.
- **One story = one branch = one PR to `main`.** Branch `story-<epic>-<story>-<slug>` (e.g. `story-1-1-project-init`), never a `/` in branch names. No direct push to `main` once story 1 is merged (protected, required `ci` check). Merge only when `ci.yml` is green.
- **PR:** title in Conventional Commit style with the story ID (`feat(story-1.1): initialize project skeleton`). The description links the story file and ends with the Claude Code attribution line. Write GitHub references in full form (`wixyvir/cvestream#12`), never `#12`.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`), one concern per commit, not squashed.
- **Changing a pattern:** amend `architecture.md` in the same PR. A violation found in review becomes an issue labelled `architecture`.
- **Dev loop in a container:** `docker compose watch` or the devcontainer (`app` service, `dev` target, `watchfiles "cvestream run" src`, Valkey with `deploy/valkey/users.acl`). A `uv.lock` change triggers an image rebuild. Without containers: `uv run cvestream run` with the YAML provider and `./data`.
- **Never commit runtime files:** `settings.yaml`, `endpoints.yaml` and `data/` are gitignored. Commit and update `settings.example.yaml` / `endpoints.example.yaml` instead.
- **Release:** tag `v*` must equal the `pyproject.toml` version. `release.yml` builds amd64 and arm64, pushes to GHCR with tags `X.Y.Z`, `X.Y` and `latest`, and creates the provenance attestation and the GitHub Release. `main` pushes `edge` and `sha-<short>`.
- **GitHub Actions:** third-party actions pinned by commit SHA, least-privilege `permissions:` per job. Dependabot runs weekly for `uv`, `github-actions` and `docker`.
- **Deployment invariants:** single replica, Kubernetes `strategy: Recreate`, RWO PVC on block storage (never NFS/SMB/CIFS), `terminationGracePeriodSeconds: 60`. Only port 8080 is public. Container runs as UID 999 with a read-only root FS (writable `/data`, `/tmp`) and all capabilities dropped. Probes use `cvestream healthcheck [--ready]`; the image has no `curl`.

### Critical Don't-Miss Rules

**Publish once (NFR-3 / SM-4):**
- GUID = `cvestream:{endpoint_id}:{primary_id}:{kind}` with `kind` = `new`, `kev` or `band-{band}`; a group item's GUID is `cvestream:{endpoint_id}:group-{family}:{h}` (story 5.2). Both are computed **once** at decision time, in `domain/publication.py` only, stored (`publication.guid`, `publication.group_guid`), and **never recomputed**. The renderer reads stored GUIDs. A CVE ID arriving later changes the primary ID, not the GUID.
- Grouping is a pure step after `decide()` (`group_decisions()` in `domain/publication.py`), split into near-equal chunks of at most 5 members, each of at least 3 (6 → 3 + 3, never a trailing 5 + 1); stored groups always render as groups, never regrouped at render time or when the rule changes.
- The publication decision and its row are written in the same transaction.
- A GUID is unique across all endpoints and vulnerabilities. `pipeline/run.py` skips a decision whose GUID is already stored (`publish.skipped`, `reason=guid_collision`) rather than letting the unique index abort the run.
- Re-publish **only** when the vulnerability becomes known-exploited or its severity band rises (including `unknown` → rated, `none` included). Nothing else re-publishes it: not a score change within the band, a new source, a description edit, or an advisory revision. An update is a new item with its own GUID and an `[UPDATE]` title prefix.
- First match after late data arrives (FR-26) → published as `new`, not as an update. New endpoint → backfill of the last 7 days. Feed window: 14 days.
- Only vulnerabilities active in the last 14 days are decided; an endpoint without any publication is backfilled with the last 7 days.
- Title markers come from decision-time values only (🔥 🔴 🔺 🆕, in that order, beside the words; 🔺 only on a `band-*` rise to `medium` or above); never derive a title from the clock or from current merged data. The body leads with the facts Slack's ~495-character window must show, and the facts line, `Matched:` and `Description:` share the first `<p>` because Slack's RSS app appears to keep only the first block-level elements (inferred from a local replay and the ~208-character 2026-11-19 posts, not measured against Slack); source version text is shown verbatim, never reformatted — except in a group item's member lines, where the first version text is capped at 60 characters plus `…` (single items stay verbatim).

**Recall over volume (FR-25): when in doubt, let it through.**
- No score → never dropped by `min_cvss`. Only the opt-in endpoint rule `require_score` drops an unscored vulnerability, and never a known-exploited one.
- Unusable version data (missing, `n/a`, unparsable, or a match found only in the description of a vulnerability without affected-product data) → matches anyway, labelled "versions unknown".
- The highest available score drives the threshold, the title severity and the re-publish triggers. Every source's score is kept (`Sourced`) and shown; `max_score()`/`band()` compute from them, never overwrite.
- Pass-throughs on unknown data are counted in `cycle.summary`.

**Matching (FR-12):** case-insensitive and whole-word (`php` must not match `phpMyAdmin`). Structured affected-product data decides whenever a vulnerability has any; the description is searched only when it has none (so `php` does not match a WordPress plugin mentioning `Module.php`). In that fallback, a hit preceded by `.`, `/` or `\`, or followed by `/` or `\`, is part of a file name or path and does not count (`x/maintenance.php`, `/usr/lib/php/`); `php.ini`, `php.net` and "Node.js" still count. A product entry with `match_description: false` skips the fallback entirely. A product entry with `exact: true` matches only an affected product whose whole name equals its name or an alias (case-insensitive, whitespace collapsed): vendor names and the description are never used; `exact: true` with an explicit `match_description: true` is rejected. Structured data and exclusion terms keep plain whole-word matching. Never match on the title. Aliases count as names. An exclusion term found in the vulnerability → that entry does not match.

**Identity (FR-8):** never key anything on a URL (a source changing its domain must not create a duplicate). Merge via CVE/EUVD aliases. A record without a CVE ID is not merged. Merge keys are CVE and EUVD aliases only; aliases are never removed.

**Security:**
- Never log a feed token or a full feed path; access logs carry `endpoint_id`. Unknown or revoked token → `404`, never `401`/`403`.
- XML in: stdlib `xml.etree.ElementTree` with a DOCTYPE refused (no entity is declared or expanded), never feedparser or defusedxml (story 2.1). XML out: built with a serializer, source text escaped as plain text, no Slack-specific markup.
- Never disable TLS verification, never honour proxy environment variables.
- Valkey: the `default` user stays disabled, the daemon user is read-only, and ACLs are never widened to cover `cvestream:v1:token:*`.

**Failure isolation (FR-6):** a failing source never stops the others and never blanks a feed. Its cursor isn't advanced, and the last good feed is served from `feed_cache` (persisted, survives restarts). Log `source.stale` when `now - last_success_at > 3 × interval`.

**Runtime gotchas:**
- Never touch SQLite from the event loop. One serialized pipeline worker runs via `asyncio.to_thread`.
- Never scale beyond one replica: two daemons double-poll and race on publication.

**Anti-patterns:**

```python
score = raw.get("baseScore", 0)            # ✗ unknown becomes 0
datetime.now()                             # ✗ naive time, not injected
guid = make_guid(v)                        # ✗ recomputed at render time
except Exception: pass                     # ✗ swallowed
import httpx  # in domain/matcher.py       # ✗ breaks the layering
log.info("feed.served", path=request.url.path)  # ✗ logs the token
```

---

## Usage Guidelines

**For AI Agents:**

- Read this file before implementing any code. `_bmad-output/planning-artifacts/architecture.md` is the authoritative source; this file is its condensed rule set.
- Follow ALL rules exactly as documented. When in doubt, prefer the more restrictive option.
- If a rule must change, amend `architecture.md` and this file in the same PR.

**For Humans:**

- Keep this file lean and focused on agent needs.
- Update when the technology stack or a pattern changes.
- Remove rules that become obvious over time.

Last Updated: 2026-10-26
