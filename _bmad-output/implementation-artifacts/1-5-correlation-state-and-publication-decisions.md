---
baseline_commit: 78549530f80893add3da4992a735cf91da347f28
---

# Story 1.5: Correlation, state and publication decisions

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a channel subscriber,
I want each vulnerability posted once on my feed, merged across sources, re-posted only when it becomes exploited or more severe, and never re-posted after a restart,
so that my channel has no duplicates (SM-4) and no missed escalation.

## Acceptance Criteria

1. **State package and migrations.** `state/schema.py` declares only the tables this story needs (`vulnerability`, `alias`, `source_record`, `publication`, `source_cursor`) on a `MetaData` with the project naming convention (`pk_`, `fk_`, `ix_`, `ux_`, `ck_`) and the `UTCDateTime` type (aware UTC only, naive datetimes rejected). `state/migrations/versions/0001_create_schema.py` creates them through Alembic configured in code (no `alembic.ini`, `render_as_batch=True`). `State.migrate()` upgrades to head (the daemon of story 1.6 calls it at startup) and `cvestream db migrate` runs it; a second run is a no-op. SQLite gets `journal_mode=WAL`, `foreign_keys=ON` and `busy_timeout` through a connect event, only for the SQLite dialect.
2. **Single commit point.** Every query lives in `state/repository.py`, upserts go through `_upsert()` (dialect `insert().on_conflict_do_update()` / `do_nothing()`), and `State.transaction()` is the only commit point: it yields a `Repository` bound to one connection, commits on success and rolls back everything on an exception.
3. **Correlation (FR-7, FR-8, FR-9).** Given records from different sources sharing a CVE or EUVD ID, `pipeline/normalize.py` and `pipeline/correlate.py` merge them into one vulnerability keyed by those aliases, keep every contributing source (`RecordRef`) and every `Sourced` value, and a change of a record's URL never creates a duplicate (identity is `(source, record_id)` plus CVE/EUVD aliases, never a URL). Two existing vulnerabilities that a record links are merged into one, with their aliases, source records and publication history.
4. **Publication decisions (FR-19).** `domain/publication.decide()` returns `new` for a first match on an endpoint, `kev` when the vulnerability becomes known-exploited after it was published, `band-{band}` when its severity band rises above every band it was published with (including `unknown` → rated), and nothing for any other change (score within the band, new source, description edit, band drop).
5. **GUIDs (NFR-3).** The GUID `cvestream:{endpoint_id}:{primary_id}:{kind}` is computed once, in `domain/publication.py`, at decision time, stored in `publication.guid` (unique), and never recomputed: a later change of primary ID leaves stored GUIDs untouched.
6. **Late data (FR-26).** A vulnerability that first matches an endpoint only after late data arrives (product data, description, score or KEV status added later) is published as `new` on that endpoint, not as an update.
7. **Backfill (FR-16).** An enabled endpoint with no publication history is offered every vulnerability active in the last 7 days, and publishes what it matches as `new`.
8. **One transaction per run.** `pipeline/run.py` writes the source records, the decisions' publication rows and the source cursors of one run in a single `State.transaction()`; a failure anywhere rolls all of it back (no publication without its cursor, no cursor without its publications).
9. **No re-post after restart (NFR-3, SM-4).** `tests/integration/test_restart_no_repost.py` proves that rerunning the same input after a restart (new `State` on the same database file) publishes nothing new, and that a later KEV addition publishes exactly one `kev` update, once.
10. **Backup.** `cvestream db backup <file>` copies the SQLite database with the SQLite online backup API; a non-SQLite URL, a missing database or an existing target file is a user error (one line on stderr, exit 2).
11. **Settings and docs.** `data_dir` (default `data`) and `state.url` (secret, default `sqlite:///<data_dir>/cvestream.db`) are new settings, documented in `settings.example.yaml` and `docs/configuration.md`; the runtime image sets `CVESTREAM_DATA_DIR=/data`; `README.md`, `CLAUDE.md`, `architecture.md` and `project-context.md` are amended as listed in Task 13.

## Tasks / Subtasks

- [x] **Task 1: Dependencies (AC: 1)**
  - [x] 1.1 `uv add "sqlalchemy>=2.1.6" "alembic>=1.20.1"` (runtime). Both are the latest on PyPI (2026-10-21 and 2026-10-22); Alembic pulls `Mako`. Commit `pyproject.toml` + `uv.lock` together (`chore(story-1.5): add sqlalchemy and alembic`). Never edit `uv.lock` by hand.

- [x] **Task 2: import-linter contracts (AC: 2)**: see "Layering and import-linter".
  - [x] 2.1 Add `allow_indirect_imports = true` to the five library-ownership contracts (`httpx`, `sqlalchemy and alembic`, `valkey`, `starlette and uvicorn`, `typer`), with one comment above the first one. Do not touch the `layers` contract or the "domain is pure" contract (it keeps indirect checks).
  - [x] 2.2 Commit separately: `chore(story-1.5): check library ownership on direct imports only`.

- [x] **Task 3: Settings (AC: 11)**: see "Settings design".
  - [x] 3.1 `data_dir: Path = Path("data")`, `StateSettings(url: SecretStr | None = None)`, `state: StateSettings`, and `state_url(settings) -> str` in `settings.py`.
  - [x] 3.2 Deferred item from 1.3 (`deferred-work.md`): `_YamlSource._read_file` also turns `ValueError` and `RecursionError` into `SettingsError` (message: file + exception type only). Add the test; mark the item resolved in `deferred-work.md` (strike-through + "Resolved by story 1.5").
  - [x] 3.3 `tests/unit/test_settings.py`: defaults, `CVESTREAM_DATA_DIR`, `CVESTREAM_STATE__URL`, `CVESTREAM_STATE__URL_FILE`, `state_url()` for relative and absolute `data_dir`, the URL never appears in `repr(settings)`.

- [x] **Task 4: `state/schema.py` (AC: 1)**: see "Schema design".
  - [x] 4.1 `NAMING_CONVENTION`, `metadata`, `UTCDateTime`, `format_utc()` / `parse_utc()`.
  - [x] 4.2 The five tables, exactly as specified.

- [x] **Task 5: `state/codec.py` (AC: 3)**: see "Record codec". `record_to_json`, `record_from_json`, `content_hash`.

- [x] **Task 6: `state/db.py`, migrations, `state/backup.py` (AC: 1, 2, 10)**: see "Database and migrations".
  - [x] 6.1 `StateError`, `State` (engine, SQLite connect event, `transaction()`, `migrate()`, `current_revision()`, `dispose()`, `display_url`).
  - [x] 6.2 `state/migrations/env.py`, `script.py.mako`, `versions/0001_create_schema.py` (revision `"0001"`, `down_revision = None`).
  - [x] 6.3 `state/backup.py`: `backup(state, target)`.

- [x] **Task 7: `state/repository.py` (AC: 2, 3, 7, 8)**: see "Repository API". `_upsert()` and the `Repository` class.

- [x] **Task 8: `domain/publication.py` (AC: 4, 5, 6)**: see "Publication decision design".

- [x] **Task 9: `pipeline/normalize.py` and `pipeline/correlate.py` (AC: 3)**: see "Correlation design".

- [x] **Task 10: `pipeline/run.py` (AC: 6, 7, 8)**: see "Pipeline run design".

- [x] **Task 11: CLI `cvestream db migrate|backup` (AC: 1, 10)**: `cli/db.py` (`db_app`), registered in `cli/main.py` with `app.add_typer(db_app, name="db")`. See "CLI design".

- [x] **Task 12: Tests (all ACs)**: see "Testing requirements". Includes `tests/integration/test_restart_no_repost.py`.

- [x] **Task 13: Docs and image (AC: 11, same PR)**
  - [x] 13.1 `settings.example.yaml`: `data_dir` and `state.url` blocks (env names, default, the secret-file form `CVESTREAM_STATE__URL_FILE`); the header line "Secrets (none yet)" becomes "Secrets (state.url for now)".
  - [x] 13.2 `docs/configuration.md`: two rows in the keys table (`data_dir`, `state.url`), the "Secrets" bullet names `state.url` as the first secret field, and a short "State database" section: SQLite file `<data_dir>/cvestream.db`, WAL mode, block storage only (never NFS/SMB/CIFS), `cvestream db migrate`, `cvestream db backup <file>`, PostgreSQL later.
  - [x] 13.3 `Dockerfile` runtime stage: `ENV CVESTREAM_DATA_DIR=/data` (on the existing `ENV PATH=...` line or next to it). Nothing else in the Dockerfile.
  - [x] 13.4 `README.md`: the "Status" paragraph mentions the state store and publication decisions (still "no feeds yet"); quick start gets `uv run cvestream db migrate` (creates `./data/cvestream.db`).
  - [x] 13.5 `CLAUDE.md` "Key rules": one line "Publication GUIDs are built only in `domain/publication.py`, stored in `publication.guid` and never recomputed; state is written only inside `State.transaction()`."
  - [x] 13.6 `architecture.md`: (a) project tree: add `codec.py` under `state/` ("SourceRecord ⇄ JSON, content hash"); (b) "Layered dependencies" bullet "Only `sources/*` and `cvestream.http` import `httpx`...": append "Ownership is about direct imports: the import-linter ownership contracts set `allow_indirect_imports = true` (a `pipeline` module may import `state`, which imports `sqlalchemy`)"; (c) Data Architecture, "State store", after "Logical tables": one bullet "Correlation and publication rules" summarizing the decisions of this story (merge keys CVE/EUVD only, aliases append-only, unchanged content does not touch a vulnerability, 14-day publication window, backfill while an endpoint has no publication, `kev` wins over a band rise in the same run, product-list edits do not backfill).
  - [x] 13.7 `_bmad-output/project-context.md`: "Identity (FR-8)" bullet: add "merge keys are CVE and EUVD aliases only; aliases are never removed"; "Publish once" bullets: add "only vulnerabilities active in the last 14 days are decided; an endpoint without any publication is backfilled with the last 7 days".

- [x] **Task 14: Gates (all ACs)**
  - [x] 14.1 `uv sync --locked && uv run ruff check && uv run ruff format --check && uv run ty check && uv run lint-imports && uv run pytest && uv run cvestream config schema --check && uv run cvestream config validate endpoints.example.yaml` all green.
  - [x] 14.2 Manual checks of `epic-1-stack-plan.md` §3.5 with `CVESTREAM_DATA_DIR=$(mktemp -d)`: `db migrate` twice (second is a no-op), `sqlite3 <dir>/cvestream.db '.tables'` and `PRAGMA journal_mode;` (`wal`), `db backup`, `grep -rn "cvestream:{" src/` (one construction site), `grep -rn "\.commit()\|\.transaction(" src/cvestream` (only the definition in `state/db.py` and the calls in `pipeline/run.py`). If `sqlite3` is not installed, use `uv run python -c "import sqlite3; ..."` and say so.
  - [x] 14.3 `docker build --target runtime -t cvestream:local .` then `docker run --rm -v "$(mktemp -d):/data" --user "$(id -u)" cvestream:local db migrate` exits 0 (alembic and the migration files are in the image). If Docker is unavailable, say so in the Completion Notes; do not claim it ran.

### Review Findings

Code review of 2026-10-26 (Blind Hunter, Edge Case Hunter, Acceptance Auditor; diff `story-1-4-euvd-and-kev-ingestion...story-1-5-correlation-state-and-publication-decisions`). 2 decision-needed (settled: options 1 + 3, and option 1), 3 patched, 2 deferred, 9 dismissed.

- [x] [Review][Decision] GUID collision between two vulnerabilities sharing an advisory-only primary ID aborts every run — `publication.guid` is unique, but `decide()` only checks the GUID against the history of the same (endpoint, vulnerability). Advisory IDs are not merge keys, so two records with no CVE/EUVD alias that share an advisory reference (CERT-FR alerte revisions `CERTFR-2026-ALE-012` rev. 1 and rev. 2 before a CVE exists, story 2.1) become two vulnerabilities with the same primary ID; both decide `new` with the same GUID, the insert violates `ux_publication_guid`, the transaction rolls back, and the next cycle replays the same input: the whole pipeline is stuck. Unreachable with EUVD and CISA KEV (every record carries an EUVD ID or a CVE). It also contradicts FR-2b (a revision "merges into the existing vulnerability"). Options: (1) defer to story 2.1 and add an acceptance criterion there (advisory revisions of the same reference merge; no GUID collision); (2) make advisory references merge keys now (changes the "CVE and EUVD only" rule recorded in architecture.md); (3) add a defensive global GUID check in `pipeline/run.py` that skips and logs a colliding decision (keeps the pipeline alive but drops an item); (4) include the internal vulnerability id in the GUID of advisory-only vulnerabilities (changes the GUID contract). Recommendation: (1), plus (3) as a safety net when 2.1 lands. — resolved 2026-10-31: options (1) + (3), `pipeline/run.py` skips a decision whose GUID is already stored (`Repository.publication_guid_exists()`) and logs `publish.skipped` (warning, `reason=guid_collision`); story 2.1 in `epics.md` gains an acceptance criterion that advisory revisions merge, and the item is recorded in `deferred-work.md`
- [x] [Review][Decision] `unknown` to `none` (CVSS 0.0) re-publishes as `band-none` — `_kind()` treats any rated band above `unknown` as a rise, so a vulnerability published unscored is posted again as an `[UPDATE]` whose news is "severity none". The story lists this literal reading of FR-19 for review. Options: (1) keep it (literal FR-19, recall over volume); (2) count a rise only from `low` upwards (`unknown` to `none` publishes nothing; a later `low` or higher still does). Recommendation: (2): a CVSS 0.0 update carries no actionable news for a channel. — resolved 2026-10-31: option (1), kept as is (literal FR-19, recall over volume); no code change
- [x] [Review][Patch] A malformed `state.url` (unparsable, non-numeric port) crashed `cvestream db migrate|backup` with a traceback and exit 1 instead of a one-line user error; `State()` now raises `StateError("state.url is not a valid database URL")`, never quoting the URL and dropping the parser exception from the chain [src/cvestream/state/db.py:54]
- [x] [Review][Patch] `State.migrate()` mapped only `OperationalError` to `StateError`; a file that is not a SQLite database (`DatabaseError`) crashed with a traceback. It now maps `DatabaseError` (which covers `OperationalError`) [src/cvestream/state/db.py:111]
- [x] [Review][Patch] `db backup` was not atomic about "never overwrite" (a file created between the CLI's existence check and `sqlite3.connect` was overwritten), left a partial file after a failed copy (the retry then said "already exists"), and `cli/db.py` imported `sqlite3` (the story keeps it inside `state/`). `backup()` now creates the target exclusively, removes it on failure and raises `StateError` for SQLite errors [src/cvestream/state/backup.py:11]
- [x] [Review][Defer] `--debug` has no effect: an unexpected CLI error always prints a Rich traceback (seen with an invalid `state.url` before the patch) [src/cvestream/cli/main.py:32] — deferred, pre-existing
- [x] [Review][Defer] SQLite runs use deferred transactions: a run reads before it writes, so if another writer commits in between, the upgrade to a write lock fails at once with `SQLITE_BUSY` (the busy timeout does not apply to a WAL snapshot upgrade) [src/cvestream/state/db.py:36] — deferred, no second writer today (single daemon, `db backup` only reads); revisit with the daemon in story 1.6

## Dev Notes

### Scope boundaries (read first)

- **In this story:** `state/` (`schema.py`, `codec.py`, `db.py`, `repository.py`, `backup.py`, `migrations/`), `domain/publication.py`, `pipeline/normalize.py`, `pipeline/correlate.py`, `pipeline/run.py`, `cli/db.py`, the two settings, the Dockerfile `ENV`, tests, docs.
- **Not in this story (1.6):** `feed_cache` table and migration `0002_add_feed_cache.py`, `pipeline/render.py`, `pipeline/summary.py` and the `cycle.summary` log, the daemon (`cvestream run`, `poll --once`), source loops and their `HttpClient`, config hot reload and `config.rejected`, `/status`, `source.stale`, last success/error per source (1.6 adds columns or a table in its own migration). Do **not** add a column "for later": add only what this story reads or writes.
- **Not in this story:** raw-payload retention (architecture: "raw payloads kept 30 days"): adapters emit `SourceRecord`s, not raw payloads, so there is nothing to keep yet. Record it in the Completion Notes as not done on purpose.
- **No change to** `domain/model.py`, `domain/matcher.py`, `domain/identity.py`, `domain/severity.py`, `domain/version.py`, `config/*`, `sources/*`, `schemas/config-v1.json`, `.github/workflows/ci.yml`. Reuse them as they are.
- **`.github/workflows/ci.yml` is not touched.** It already triggers on every `pull_request` with no `branches:` filter (stacked PRs target story branches, not `main`) and pushes images only on push to `main`; keep it that way. `uv run pytest` already runs `tests/integration/` (no marker needed: SQLite in `tmp_path`, no network).

### Existing code this story builds on (exact names)

- `domain/model.py`: `Sourced[T](value, source, observed_at)`, `CvssScore(score, version=None, vector=None)`, `AffectedProduct(product, vendor=None, version_text=None)`, `KevCatalog.CISA_KEV|EU_KEV` (values `"cisa_kev"`, `"eu_kev"`), `KevEntry(catalog, known_ransomware_use=None, added_at=None)`, `RecordRef(source, record_id, url=None)`, `SourceRecord(source, record_id, observed_at, aliases=(), title=None, description=None, scores=(), affected=(), kev=(), references=(), url=None, published_at=None, updated_at=None, epss=None)`, `NormalizedRecord(record, aliases: frozenset[str], primary_id)`, `Vulnerability(primary_id, aliases: frozenset[str], records=(), titles=(), descriptions=(), scores=(), affected=(), kev=(), references: tuple[Sourced[str], ...]=(), published_at=None)` with `is_known_exploited` (any `kev` entry). All frozen, slotted, tuples only.
- `domain/identity.py`: `normalize_alias(raw) -> str | None` (upper-case; `None` for empty, whitespace or `/`, so a URL is never an alias), `alias_kind(alias) -> AliasKind` (`CVE`, `EUVD`, `ADVISORY`), `primary_id(aliases) -> str` (CVE > EUVD > advisory, lowest number first; raises `ValueError` when none is usable).
- `domain/severity.py`: `Band` (`UNKNOWN`, `NONE`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`, compare with `.rank`, never as strings), `max_score(scores) -> float | None`, `band_of(score) -> Band`.
- `domain/matcher.py`: `match(vulnerability, endpoint) -> MatchResult | None`; `MatchResult(endpoint_id, vulnerability_id, matches: tuple[ProductMatch, ...], labels: frozenset[PassThrough])`; `ProductMatch(entry: ProductEntry, structured, versions_unknown)`; `PassThrough.VERSIONS_UNKNOWN|SCORE_UNKNOWN`. `matches[0]` names the item.
- `domain/endpoint.py`: `Endpoint(id, name, products, rules=Rules(), enabled=True)`. The matcher ignores `enabled`: **the pipeline skips disabled endpoints**.
- `config/provider.py`: `ConfigSnapshot(revision, endpoints: tuple[Endpoint, ...], tokens)`. The pipeline reads `snapshot.endpoints` only, never `tokens`.
- `sources/base.py`: `Cursor = str`, `FetchResult(records: tuple[SourceRecord, ...], next_cursor: Cursor | None, skipped: int = 0)`. Adapter names: `euvd`, `cisa_kev`; record sources: `euvd`, `eu_kev` (EU KEV entries emitted by the `euvd` adapter, `record_id` = EUVD ID or CVE), `cisa_kev` (`record_id` = CVE). EUVD search records carry no KEV entry; KEV flags come only from `eu_kev` / `cisa_kev` records, which this story merges by alias.
- `settings.py`: `Settings` (`log`, `sources`, `config_provider`, `endpoints_file`), `load_settings(environ=None)`, `SettingsError`, the `_FileSecretsSource` that already supports nested `SecretStr` fields through `CVESTREAM_<A>__<B>_FILE`.
- `clock.py`: `Clock` protocol, `SystemClock`. `tests/conftest.py`: `FakeClock` (`now`, `advance`, `set`), `fake_clock`, `anyio_backend`. `tests/unit/domain/conftest.py`: `make_vulnerability(...)` factory (domain tests only).
- `cli/config.py`: the CLI pattern to copy (`typer.Typer(name=..., no_args_is_help=True, help=...)`, `_fail(message)` → stderr + exit 2, `load_settings()` inside the command, never at import time).

### Layering and import-linter

- Allowed: `pipeline` → `domain`, `state`, `config` (`ConfigSnapshot`), `sources.base` (`FetchResult`, `Cursor`), `clock`; `state` → `domain`, `settings`, `errors`, `clock`; `cli` → `state`, `settings`, `config`.
- `sqlalchemy` / `alembic` are imported **only** in `cvestream/state/*`. `pipeline` never imports `sqlalchemy`, not even for a type: it receives a `Repository` from `State.transaction()` and calls its methods.
- **Why Task 2 is required:** import-linter's `forbidden` contracts check indirect imports by default (`allow_indirect_imports` defaults to `False`, see `.venv/lib/python3.14/site-packages/importlinter/contracts/forbidden.py`). `cvestream.pipeline.run → cvestream.state.repository → sqlalchemy` is an indirect import, so the existing "sqlalchemy and alembic only in state" contract would fail as soon as `pipeline` or `cli` imports `state`. The architecture's rule is about direct imports ("Only `state/` imports sqlalchemy"); the `layers` contract still forbids upward imports. Set the option on all five ownership contracts now (1.6 needs it for `app` → `sources` → `httpx` and `app` → `starlette`), with this comment above the first one: `# Ownership means "imports directly": a higher layer may import the owner (pipeline -> state -> sqlalchemy).`
- `domain/publication.py` imports only stdlib and `cvestream.domain.*`; `tests/unit/domain/test_purity.py` must stay green unchanged.

### Settings design (`settings.py`)

```python
class StateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # SQLAlchemy URL. Secret: a PostgreSQL URL carries a password (CVESTREAM_STATE__URL_FILE).
    url: SecretStr | None = None

class Settings(BaseSettings):
    ...
    data_dir: Path = Path("data")
    state: StateSettings = Field(default_factory=StateSettings)

DATABASE_FILE = "cvestream.db"

def state_url(settings: Settings) -> str:
    """`state.url`, or the SQLite file `<data_dir>/cvestream.db`."""
```

- Default: `f"sqlite:///{settings.data_dir / DATABASE_FILE}"`. Relative `data` gives `sqlite:///data/cvestream.db` (relative to the working directory); absolute `/data` gives `sqlite:////data/cvestream.db` (architecture's `CVESTREAM_STATE__URL=sqlite:////data/cvestream.db`).
- `data_dir` is not created here (settings never touch the disk): `State.migrate()` creates the SQLite file's parent directory.
- `SecretStr` keeps the URL out of `repr(settings)` and logs; `_FileSecretsSource` makes `CVESTREAM_STATE__URL_FILE` work with no extra code (test it).
- No other setting (no pool size, no busy timeout, no retention): constants in `state/db.py`.

### Schema design (`state/schema.py`)

```python
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "ux_%(table_name)s_%(column_0_N_name)s",   # unique constraints are named ux_*
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

- Declare uniqueness with `UniqueConstraint(...)` (→ `ux_*`) and lookups with `Index(None, ...)` or `index=True` (→ `ix_*`). Never write a constraint name by hand.
- **`UTCDateTime(TypeDecorator[datetime])`**, `impl = String`, `cache_ok = True`. `load_dialect_impl`: `DateTime(timezone=True)` on `postgresql`, `String(32)` otherwise. `process_bind_param`: `None` → `None`; naive → raise `NaiveDatetimeError(StateError)` (message names no value); aware → on PostgreSQL the UTC datetime, elsewhere `format_utc(value)`. `process_result_value`: text → `parse_utc(text)`, datetime → `.astimezone(UTC)`.
- **`format_utc(dt)`** = `dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")`, always 6 microsecond digits, so that text comparison equals time order on SQLite (the 7-day and 14-day window queries compare text). `parse_utc(text)` = `datetime.fromisoformat(text).astimezone(UTC)` (accepts the trailing `Z` since Python 3.11 and returns an aware datetime; do not use `strptime`, which ruff `DTZ007` flags without `%z`); a naive result raises `StateError`. The codec (Task 5) reuses both functions.
- Portable types only: `Integer`, `Text`, `String`, `Boolean`, `JSON`, `UTCDateTime`. Primary keys `Column("id", Integer, primary_key=True)`.

Tables (column order as listed; `NOT NULL` unless marked `null`):

| Table | Columns | Constraints and indexes |
|---|---|---|
| `vulnerability` | `id`; `primary_id` Text (current display ID, recomputed when aliases change); `activity_at` UTCDateTime (max `source_record.activity_at`); `created_at` UTCDateTime; `changed_at` UTCDateTime (last content change of any of its records) | `ix_vulnerability_primary_id`, `ix_vulnerability_activity_at` |
| `alias` | `id`; `alias` Text (normalized CVE or EUVD ID: merge keys only); `vulnerability_id` → `vulnerability.id` | `ux_alias_alias`, `ix_alias_vulnerability_id` |
| `source_record` | `id`; `vulnerability_id` → `vulnerability.id`; `source` Text; `record_id` Text; `record_json` JSON (Task 5); `content_hash` Text (sha256 hex); `activity_at` UTCDateTime; `first_seen_at`, `changed_at`, `last_seen_at` UTCDateTime | `ux_source_record_source_record_id` on (`source`, `record_id`), `ix_source_record_vulnerability_id` |
| `publication` | `id`; `endpoint_id` Text (config slug, **no** FK: endpoints live in the config provider); `vulnerability_id` → `vulnerability.id`; `kind` Text (`new`, `kev`, `band-<band>`); `guid` Text; `primary_id` Text (at decision time, display); `band` Text (band at decision time); `is_known_exploited` Boolean (at decision time); `products_json` JSON (matched entry names, in `MatchResult.matches` order); `labels_json` JSON (sorted pass-through labels); `published_at` UTCDateTime | `ux_publication_guid`, `ix_publication_endpoint_id_vulnerability_id`, `ix_publication_endpoint_id_published_at` (the 14-day render query of 1.6) |
| `source_cursor` | `id`; `source` Text (adapter name: `euvd`, `cisa_kev`); `cursor` Text `null`; `updated_at` UTCDateTime | `ux_source_cursor_source` |

- No `ON DELETE CASCADE`: a merge re-points rows explicitly before deleting the absorbed vulnerability (Task 7), and `foreign_keys=ON` catches mistakes.
- No uniqueness on `(endpoint_id, vulnerability_id, kind)`: after a merge, both former vulnerabilities may each carry a `new` row. `guid` is the unique key.

### Record codec (`state/codec.py`)

New module (add it to the architecture tree, Task 13.6): the only place that turns a `SourceRecord` into JSON and back. Pure functions, no SQLAlchemy.

```python
CODEC_VERSION = 1
def record_to_json(record: SourceRecord) -> dict[str, object]
def record_from_json(data: Mapping[str, object]) -> SourceRecord
def content_hash(record: SourceRecord) -> str
```

- Layout: `{"v": 1, "source", "record_id", "observed_at", "aliases": [...], "title": <sourced>|null, "description": <sourced>|null, "scores": [<sourced CvssScore|null>], "affected": [<sourced AffectedProduct>], "kev": [<sourced KevEntry>], "references": [...], "url", "published_at", "updated_at", "epss": <sourced float>|null}`; a `<sourced>` is `{"value": ..., "source": ..., "observed_at": ...}`; `CvssScore` → `{"score", "version", "vector"}`; `AffectedProduct` → `{"product", "vendor", "version_text"}`; `KevEntry` → `{"catalog": "cisa_kev"|"eu_kev", "known_ransomware_use": true|null, "added_at"}`. Datetimes through `format_utc` / `parse_utc`. `None` stays `null` (unknown is never `0` or `""`).
- `record_from_json(record_to_json(r)) == r` for every field (tuples rebuilt as tuples, `KevCatalog(value)`). An unknown `v` raises `StateError` (never silently misread stored data).
- **`content_hash`** = sha256 hex of `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` where `payload` is `record_to_json(record)` with every `observed_at` key removed (the record's and every `Sourced`'s). Adapters stamp `observed_at = clock.now()` on every fetch, so without this every poll would look like a change: the full CISA KEV catalogue (≈1,800 entries) arrives every 15 minutes.

### Database and migrations (`state/db.py`, `state/migrations/`)

```python
BUSY_TIMEOUT_MS = 5000
MIGRATIONS = "cvestream.state:migrations"     # Alembic package-resource script_location

class StateError(CvestreamError): ...
class NaiveDatetimeError(StateError): ...      # may live in schema.py next to UTCDateTime

class State:
    def __init__(self, url: str) -> None                 # stores make_url(url); the engine is created lazily
    @property
    def dialect(self) -> str                             # "sqlite", "postgresql"
    @property
    def display_url(self) -> str                         # engine.url.render_as_string(hide_password=True)
    @property
    def sqlite_path(self) -> Path | None                 # file path for a file-based SQLite URL, else None
    def migrate(self) -> str                             # upgrade to head, return the head revision
    def current_revision(self) -> str | None
    @contextmanager
    def transaction(self) -> Iterator[Repository]       # engine.begin(); commit on success, rollback on error
    def dispose(self) -> None
```

- **Lazy engine:** `__init__` only parses the URL (`sqlalchemy.make_url`); `dialect` comes from `url.get_backend_name()` and `display_url` from the parsed URL, and the engine is created on first use (`transaction()`, `migrate()`, backup). `create_engine("postgresql+psycopg://...")` imports the driver at once, and `psycopg` is not installed: `db backup` must refuse a PostgreSQL URL and `display_url` must work without ever creating an engine.
- **SQLite connect event** (`event.listens_for(engine, "connect")`, registered only when `engine.dialect.name == "sqlite"`), in this order on the raw `sqlite3.Connection`: `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000` (through a cursor, then close it), **then** `dbapi_connection.autocommit = False`. Order matters: with `autocommit=False` the sqlite3 driver keeps a transaction open at all times, and `journal_mode=WAL` cannot be changed inside a transaction. `autocommit = False` (Python 3.12+) is SQLAlchemy 2.1's documented way out of the sqlite3 driver's legacy transaction mode, in which a `SELECT` before the first write runs outside the transaction (see "Latest technical information"). Do not pass `connect_args={"autocommit": False}` instead: the driver would open the transaction before the pragmas run.
- `migrate()`: for a SQLite file, `sqlite_path.parent.mkdir(parents=True, exist_ok=True)` first. Then `cfg = alembic.config.Config()`, `cfg.set_main_option("script_location", MIGRATIONS)`, `with self._engine.begin() as connection: cfg.attributes["connection"] = connection; command.upgrade(cfg, "head")`. Log `state.migrated` (info, `revision=`). Never set `sqlalchemy.url` on the config (the connection is passed in), never call `logging.fileConfig`.
- `env.py`: `connection = context.config.attributes["connection"]`; `context.configure(connection=connection, target_metadata=schema.metadata, render_as_batch=True, compare_type=True)`; `with context.begin_transaction(): context.run_migrations()`. Online mode only: offline mode raises `StateError("offline migrations are not supported")`.
- `script.py.mako`: Alembic's default template, plus `import cvestream.state.schema` so autogenerated revisions can reference `UTCDateTime`.
- `0001_create_schema.py`: `revision = "0001"`, `down_revision = None`, `op.create_table(...)` for the five tables with columns, FKs, unique constraints and indexes identical to `schema.py`, names through `op.f("...")` matching the convention; `UTCDateTime` columns use `cvestream.state.schema.UTCDateTime()`. `downgrade()` drops the tables in reverse order. Writing it with autogenerate is fine (`command.revision(cfg, autogenerate=True, rev_id="0001", message="create schema")` against an empty database), then rename the file to `0001_create_schema.py`; the test "metadata equals migrations" (below) is the guard either way.
- Alembic's `script_location` accepts a `package:path` resource (verified in the Alembic `Config` docs); the files ship with the package because the image installs `src/` (editable) and `uv_build` includes non-Python files under `src/cvestream/`. Task 14.3 checks it in the image.
- `transaction()`: `with self._engine.begin() as connection: yield Repository(connection)`. **This is the only commit point.** No `connection.commit()` anywhere else in `src/`.

### Repository API (`state/repository.py`)

```python
def _upsert(connection, table, values: Mapping[str, object], *, keys: Sequence[str],
            update: Sequence[str] = ()) -> None
    # sqlite: sqlalchemy.dialects.sqlite.insert; postgresql: sqlalchemy.dialects.postgresql.insert
    # update empty -> on_conflict_do_nothing(index_elements=keys)
    # else -> on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in update})
    # any other dialect -> StateError

@dataclass(frozen=True, slots=True)
class StoredRecord:
    id: int
    vulnerability_id: int
    content_hash: str

class Repository:
    def __init__(self, connection: Connection) -> None
    # correlation
    def vulnerability_ids_for_aliases(self, aliases: Iterable[str]) -> set[int]
    def find_source_record(self, source: str, record_id: str) -> StoredRecord | None
    def create_vulnerability(self, now: datetime) -> int
    def merge_vulnerabilities(self, survivor: int, absorbed: Iterable[int]) -> None
    def add_aliases(self, vulnerability_id: int, aliases: Iterable[str]) -> None
    def insert_source_record(self, vulnerability_id: int, record: SourceRecord, activity_at: datetime, now: datetime) -> None
    def update_source_record(self, stored: StoredRecord, vulnerability_id: int, record: SourceRecord, activity_at: datetime, now: datetime) -> None
    def mark_seen(self, stored: StoredRecord, now: datetime) -> None
    def refresh_vulnerability(self, vulnerability_id: int, primary_id: str, now: datetime, *, changed: bool) -> None
    # decisions
    def load_records(self, vulnerability_ids: Iterable[int]) -> dict[int, list[SourceRecord]]
    def active_since(self, since: datetime) -> list[int]
    def filter_active_since(self, vulnerability_ids: Iterable[int], since: datetime) -> list[int]
    def endpoints_with_publications(self, endpoint_ids: Iterable[str]) -> set[str]
    def publication_history(self, endpoint_ids: Iterable[str], vulnerability_ids: Iterable[int]) -> dict[tuple[str, int], list[PastPublication]]
    def add_publication(self, vulnerability_id: int, decision: PublicationDecision, published_at: datetime) -> None
    # cursors
    def cursors(self) -> dict[str, str | None]
    def set_cursor(self, source: str, cursor: Cursor | None, now: datetime) -> None
```

- `Connection` is `sqlalchemy.Connection`; the type stays inside `state/`.
- `merge_vulnerabilities`: `UPDATE alias / source_record / publication SET vulnerability_id = survivor WHERE vulnerability_id IN absorbed`, then `DELETE FROM vulnerability WHERE id IN absorbed`. Survivor = the smallest id (oldest vulnerability). Log `state.vulnerabilities_merged` (info, `vuln_id=<survivor primary_id>`, `count=len(absorbed)`) from the pipeline, not here (the repository logs nothing).
- `insert_source_record` / `update_source_record` store `record_to_json(record)`, `content_hash(record)` and `activity_at`; insert sets `first_seen_at = changed_at = last_seen_at = now`; update sets `changed_at = last_seen_at = now` and keeps `first_seen_at`. `mark_seen` updates `last_seen_at` only (unchanged content: the stored JSON, with its original `observed_at`, is kept).
- `refresh_vulnerability`: `activity_at = MAX(source_record.activity_at)` of its records (a correlated subquery or a separate `SELECT max(...)`), `primary_id`, and `changed_at = now` when `changed`.
- `set_cursor` uses `_upsert(source_cursor, ..., keys=["source"], update=["cursor", "updated_at"])`; `add_aliases` uses `_upsert(alias, ..., keys=["alias"])` with no update (do nothing).
- `IN (...)` lists: chunk ids in groups of 500 (SQLite's variable limit is high, but chunking keeps PostgreSQL and very large backfills safe).
- `load_records` returns records in `(source, record_id)` order per vulnerability (deterministic merge).
- No raw SQL strings, no `text()` with dialect syntax, no `RETURNING` (use `result.inserted_primary_key` after `insert()` for `create_vulnerability`).

### Publication decision design (`domain/publication.py`)

```python
NEW = "new"
KEV = "kev"
BACKFILL_WINDOW = timedelta(days=7)        # FR-16
PUBLICATION_WINDOW = timedelta(days=14)    # feed window (FR-17); first-run cursor is also now - 14 days

def band_kind(band: Band) -> str           # "band-high"

@dataclass(frozen=True, slots=True)
class PastPublication:
    kind: str
    guid: str
    band: Band
    is_known_exploited: bool

@dataclass(frozen=True, slots=True)
class PublicationDecision:
    endpoint_id: str
    primary_id: str
    kind: str
    guid: str
    band: Band
    is_known_exploited: bool
    products: tuple[str, ...]              # matched entry names, matches[0] first
    labels: frozenset[PassThrough]

def decide(match: MatchResult, vulnerability: Vulnerability,
           history: Sequence[PastPublication]) -> PublicationDecision | None
```

Rules, in this order (`band = band_of(max_score(vulnerability.scores))`, `kev = vulnerability.is_known_exploited`):

1. `history` empty → `NEW` (first match on this endpoint, FR-19; late data, FR-26; backfill, FR-16).
2. `kev` and no `h.is_known_exploited` in `history` → `KEV`.
3. `band is not Band.UNKNOWN` and `band.rank > max(h.band.rank for h in history)` → `band_kind(band)`. `unknown` → any rated band counts as a rise (FR-19 "including unknown → rated"); this includes `unknown` → `none` (CVSS 0.0), literal reading, listed for review below.
4. Otherwise → `None` (score change within the band, band drop, new source, description edit, CERT-FR revision later).
- One decision per (endpoint, vulnerability) per run at most. When rules 2 and 3 both hold, `KEV` wins and the decision records the **current** band, so the band rise is absorbed (the `[UPDATE]` item shows the current severity anyway) and not published again later.
- `guid = f"cvestream:{match.endpoint_id}:{vulnerability.primary_id}:{kind}"`. This f-string is the **only** GUID construction site in `src/` (the stack plan greps for it). If that GUID is already in `history` (only possible after unusual merges), return `None`: never insert a duplicate GUID.
- `primary_id`, `band`, `is_known_exploited` are recorded at decision time; `products = tuple(m.entry.name for m in match.matches)`; `labels = match.labels`.
- Pure: no clock (the pipeline stamps `published_at`), no I/O, no logging. Windows are constants here because they are publication rules; the pipeline applies them.

### Correlation design (`pipeline/normalize.py`, `pipeline/correlate.py`)

`normalize.py`:

```python
def normalize(record: SourceRecord) -> NormalizedRecord | None
```

- `aliases = frozenset(a for raw in (record.record_id, *record.aliases) if (a := normalize_alias(raw)) is not None)`; empty → `None` (the record is skipped and counted: no identity). `primary_id = identity.primary_id(aliases)`. The record itself is kept unchanged (`NormalizedRecord.record`).

`correlate.py`:

```python
MERGE_KINDS = frozenset({AliasKind.CVE, AliasKind.EUVD})

def merge_keys(record: NormalizedRecord) -> frozenset[str]          # aliases of kind CVE or EUVD
def activity_time(record: SourceRecord) -> datetime | None           # see below
def build_vulnerability(records: Sequence[NormalizedRecord]) -> Vulnerability   # pure

@dataclass(frozen=True, slots=True)
class CorrelationResult:
    touched: frozenset[int]     # vulnerability ids whose content changed (or that were merged)
    stored: int                 # records processed
    changed: int                # records inserted or with a new content hash
    skipped: int                # records without any usable alias

def correlate(repository: Repository, records: Iterable[SourceRecord], now: datetime) -> CorrelationResult
```

For each record, in order:

1. `normalized = normalize(record)`; `None` → `skipped += 1`, `log.debug("pipeline.record_skipped", source=..., record_id=...)`.
2. `stored = repository.find_source_record(record.source, record.record_id)`; `keys = merge_keys(normalized)`.
3. Candidate vulnerabilities = `repository.vulnerability_ids_for_aliases(keys)` ∪ `{stored.vulnerability_id}` when `stored`. None → `create_vulnerability(now)`. One → that one. Several → survivor = `min(ids)`, `merge_vulnerabilities(survivor, others)`, all of them are touched (log `pipeline.vulnerabilities_merged`, info, `count`).
4. `add_aliases(vulnerability_id, keys)`.
5. `new_hash = content_hash(record)`. `stored is None` → insert, changed. `stored.content_hash != new_hash` or `stored.vulnerability_id != vulnerability_id` → update, changed. Else → `mark_seen`, unchanged.
6. When changed or merged: add the vulnerability (and the merge survivor) to `touched`.
7. After the last record: for each touched vulnerability, exactly once, `records = repository.load_records([id])`, `primary_id = identity.primary_id(union of normalize(r).aliases)`, then `refresh_vulnerability(id, primary_id, now, changed=True)`. Decisions are made only after this refresh.

Rules and why:

- **Merge keys are CVE and EUVD aliases only** (FR-7 says "using CVE ID and EUVD ID aliases"). GHSA and advisory IDs are kept in the record (and in `Vulnerability.aliases`, for the primary ID and display) but never merge two vulnerabilities, and the `alias` table holds merge keys only. A record without any CVE/EUVD alias is found again only through its `(source, record_id)` and is never merged (FR-5's "without a CVE ID the record is not merged").
- **Identity never uses a URL**: `RecordRef.url` and `SourceRecord.url` are display data; a URL change only changes `record_json` (content change → touched, but no new vulnerability, no new `source_record` row).
- **Aliases are append-only**: when a source drops an alias from a record, the `alias` row stays and the vulnerability is never split (splitting would orphan publication history). `Vulnerability.aliases` is rebuilt from the current records, so the display follows the source.
- **Unchanged content does not touch a vulnerability**: re-delivering the same record (new `observed_at` only) updates `last_seen_at` and nothing else, so the full KEV catalogues re-sent every poll cost no decisions.
- **`activity_time(record)`** = `record.updated_at`, else `record.published_at`, else the latest `kev.value.added_at`, else `None`; `None` → the repository stores `now` (first seen). It is a **source** time: a CISA KEV entry added in 2021 has activity 2021 even when it is first loaded today, so the first full KEV load does not look like 1,800 fresh vulnerabilities.
- `build_vulnerability(records)`: records sorted by `(source, record_id)`; `aliases` = union; `primary_id` = `identity.primary_id(aliases)`; `records` = `RecordRef(source, record_id, url)` each; `titles`, `descriptions` = the non-`None` `Sourced` values in record order; `scores`, `affected`, `kev` = concatenation (keep `Sourced(None)` scores: "this source says unscored"); `references` = `Sourced(ref, record.source, record.observed_at)` per reference, de-duplicated on `(value, source)` keeping the first; `published_at` = earliest non-`None` `published_at`. Never drop or overwrite a `Sourced` value (FR-9, FR-24).

### Pipeline run design (`pipeline/run.py`)

```python
@dataclass(frozen=True, slots=True)
class SourceBatch:
    source: str              # adapter name (cursor key): "euvd", "cisa_kev"
    result: FetchResult

@dataclass(frozen=True, slots=True)
class RunResult:
    decisions: tuple[PublicationDecision, ...]
    records: int
    changed: int
    skipped: int
    touched: int

def run(state: State, snapshot: ConfigSnapshot, batches: Sequence[SourceBatch], clock: Clock) -> RunResult
def load_cursors(state: State) -> dict[str, str | None]      # read-only; story 1.6 calls it before fetching
```

`run()`, with `now = clock.now()` read **once**:

```
with state.transaction() as repository:                      # the only transaction of the run
    correlation = correlate(repository, (r for b in batches for r in b.result.records), now)
    for batch in batches: if batch.result.next_cursor is not None: repository.set_cursor(batch.source, batch.result.next_cursor, now)
    endpoints = [e for e in snapshot.endpoints if e.enabled]
    with_history = repository.endpoints_with_publications(e.id for e in endpoints)
    recent = repository.filter_active_since(correlation.touched, now - PUBLICATION_WINDOW)
    backfill = repository.active_since(now - BACKFILL_WINDOW) if any(e.id not in with_history for e in endpoints) else []
    vulnerabilities = {id: build_vulnerability([normalize(r) ...]) for id in recent ∪ backfill}   # via load_records
    history = repository.publication_history([e.id ...], vulnerabilities.keys())
    for endpoint in endpoints:                                 # config order
        candidates = backfill if endpoint.id not in with_history else recent
        for vulnerability_id in sorted(candidates):            # deterministic
            match_result = match(vulnerabilities[vulnerability_id], endpoint)
            decision = decide(match_result, vulnerability, history.get((endpoint.id, vulnerability_id), [])) if match_result else None
            if decision: repository.add_publication(vulnerability_id, decision, now); log publish.decided
```

- **Candidates.** An endpoint with history decides only the vulnerabilities this run changed **and** that are active in the last 14 days (`PUBLICATION_WINDOW`): without the window, a cosmetic change to an old KEV entry (renamed `vulnerabilityName`) would publish a 2015 CVE as `new`. An endpoint with **no** publication row at all is backfilled (FR-16): it decides every vulnerability active in the last 7 days, at every run, until its first publication (bounded query; volume ≈ 2,000 EUVD updates a day). On the first run every endpoint is in that case, so the first run publishes the last 7 days (not 14), as FR-16 intends.
- `endpoints_with_publications` is read **before** any decision of the run, so an endpoint whose first publications happen in this run is treated as a backfill for the whole run.
- `published_at = now` for every decision, backfill included.
- Disabled endpoints are skipped. Endpoints removed from the config keep their rows (harmless; tokens are 1.6's concern).
- A record batch is processed in order; a `FetchResult` with `next_cursor is None` leaves the cursor untouched. `skipped` in `RunResult` is `correlation.skipped` (records without identity), not the adapters' `FetchResult.skipped` (1.6 logs those per source).
- Logging: `publish.decided` (info, `endpoint_id`, `vuln_id=decision.primary_id`, `kind`) per decision, after the row is added; `pipeline.completed` (info, `count=len(decisions)`, `records`, `changed`, `touched`, `duration_ms`) once per run, after the transaction commits. Never log a token (the pipeline never reads `snapshot.tokens`).
- **The pipeline never retries and never catches.** Any exception propagates, the transaction rolls back (no publication rows, no cursors), and the next cycle is the retry (1.6's worker logs it).
- `load_cursors(state)` opens its own `state.transaction()` to read `repository.cursors()` and writes nothing; it lives here so that `pipeline/run.py` stays the only module (with `cli/db.py`) that opens transactions.
- Synchronous code only (no `async`, no threads); 1.6 calls `run()` through `asyncio.to_thread`.

### CLI design (`cli/db.py`)

- `db_app = typer.Typer(name="db", no_args_is_help=True, help="State database: migrate and back up.")`.
- `cvestream db migrate`: `settings = load_settings()`, `State(state_url(settings)).migrate()`, then one line on stdout: `"<display_url>: at revision 0001"`. `SettingsError`, `StateError` → `_fail(str(exc))` (exit 2). Never print the URL with a password (`display_url` hides it).
- `cvestream db backup <file>`: `State(state_url(settings))`; `state.dialect != "sqlite"` → `_fail("db backup supports SQLite only; use pg_dump for PostgreSQL")`; `state.sqlite_path` missing or not a file → `_fail("<path>: no state database; run: cvestream db migrate")`; target exists → `_fail("<file>: already exists")`; target equal to the database file → `_fail(...)`. Then `state/backup.py: backup(state, target)`, then `"<file>: backup written"`. `OSError` / `sqlite3.Error` while writing → `_fail("<file>: cannot write backup: <ExceptionType>")`.
- `state/backup.py`: `def backup(state: State, target: Path) -> None`: `raw = state.raw_sqlite_connection()` (a `State` helper using `engine.raw_connection()` and its `driver_connection`), `with closing(sqlite3.connect(target)) as destination: raw.backup(destination)`; always close the raw connection. The backup API copies a consistent snapshot including the WAL content. The CLI imports `backup` from `cvestream.state.backup`; `sqlite3` stays inside `state/`.
- `--debug` and the exit-1 path stay as in the root app (nothing to add here).

### Testing requirements

Layout (every new test directory gets an `__init__.py`): `tests/unit/state/{__init__,test_schema,test_codec,test_db,test_repository,test_backup}.py`, `tests/unit/domain/test_publication.py`, `tests/unit/pipeline/{__init__,test_normalize,test_correlate,test_run}.py`, `tests/unit/cli/test_db.py`, `tests/integration/{__init__,test_restart_no_repost}.py`, plus additions to `tests/unit/test_settings.py`.

Shared fixture in `tests/conftest.py` (architecture: "conftest provides tmp SQLite"):

```python
@pytest.fixture
def state(tmp_path: Path) -> Iterator[State]:
    s = State(f"sqlite:///{tmp_path / 'state.db'}")
    s.migrate()
    yield s
    s.dispose()
```

Test records: build `SourceRecord`s in the tests with a small local factory (module-level helper, not a fixture in `tests/conftest.py`); the restart test uses the **recorded** fixtures through the adapters (below). No hand-written JSON files.

Required cases (the reviewer checks them, see `epic-1-stack-plan.md` §3.5):

- **schema:** constraint and index names follow the convention (`pk_publication`, `ux_publication_guid`, `ux_source_record_source_record_id`, `fk_publication_vulnerability_id_vulnerability`, `ix_publication_endpoint_id_published_at`); `UTCDateTime` rejects a naive datetime (`NaiveDatetimeError`), round-trips an aware datetime as UTC, converts a `+02:00` datetime to UTC; `format_utc` always has 6 microsecond digits and its text order equals time order (`00:00:00.000000Z` < `00:00:00.500000Z` < `00:00:01.000000Z`).
- **migrations:** `migrate()` creates the five tables plus `alembic_version` at `0001`; a second `migrate()` is a no-op; **metadata equals migrations**: `alembic.autogenerate.compare_metadata(MigrationContext.configure(connection), schema.metadata) == []` after `migrate()` (guards drift between `schema.py` and `0001`); `migrate()` creates a missing parent directory.
- **db:** `PRAGMA journal_mode` → `wal`, `PRAGMA foreign_keys` → `1`, `PRAGMA busy_timeout` → `5000` on a pooled connection; an exception inside `transaction()` rolls back every write of the block (insert a vulnerability then raise, check nothing remains); a `SELECT` then a write in one `transaction()` are in the same transaction (the `autocommit=False` setup); `display_url` hides a password (`postgresql+psycopg://u:secret@h/db` → no `secret`; build the `State` without connecting); a module-scan test over `src/cvestream`: no `.commit(` anywhere, and `.transaction(` appears only in `pipeline/run.py` (calls) and `state/db.py` (its definition).
- **codec:** full round trip of a record with every field set (title, description, two scores incl. `Sourced(None)`, affected with `None` vendor/version, CISA and EU KEV entries with `known_ransomware_use` `True` and `None`, references, url, dates, epss); minimal record round trip; `content_hash` ignores every `observed_at` and changes when a score, a version text, a KEV entry, an alias or the URL changes; unknown `v` → `StateError`.
- **repository:** `_upsert` insert then update (cursor), do-nothing (alias already present); `vulnerability_ids_for_aliases`; `merge_vulnerabilities` re-points aliases, source records **and publications**, deletes the absorbed rows, keeps foreign keys valid; `active_since` / `filter_active_since` boundaries (exactly at `since` is included); `publication_history` groups by `(endpoint_id, vulnerability_id)`; `add_publication` twice with the same GUID raises (unique); `cursors` / `set_cursor` (`None` cursor stored as `NULL`).
- **publication (domain, pure, table-driven):** empty history → `new`; history `new` + now KEV → `kev`; history already KEV → no `kev`; band `medium` → `high` → `band-high`; `unknown` → `low` → `band-low`; `unknown` → `none` → `band-none`; `high` → `high` (score 7.1 → 8.5) → `None`; `critical` → `high` → `None`; history max `critical` (published `band-critical` earlier), now `high` → `None`; KEV and band rise together → one `kev` decision recording the current band, and a following run at the same band → `None`; GUID format `cvestream:team-php:CVE-2026-1234:band-critical`; the GUID uses the **current** primary ID while history GUIDs are untouched; computed GUID already in history → `None`; `products` order and `labels` copied from the match. Build vulnerabilities with `make_vulnerability` from `tests/unit/domain/conftest.py`.
- **normalize:** aliases normalized and including `record_id`; URL-like and blank aliases dropped; no usable alias → `None`; primary ID CVE > EUVD > advisory.
- **correlate:** EUVD record (EUVD-X + CVE-Y) then CISA record (CVE-Y) → one vulnerability, two `source_record` rows, one `RecordRef` each, KEV flag present, every score kept; reverse order gives the same; same record with a different `url` → one row, no new vulnerability; GHSA shared by two records with different CVEs → **not** merged; EUVD-only record later gaining a CVE that another vulnerability already holds → the two are merged, publications re-pointed, survivor = older id; same content re-delivered with a later `observed_at` → not touched, `last_seen_at` updated, stored JSON unchanged; changed score → touched; record without usable alias → skipped; `activity_time` cases (updated, published only, KEV `added_at` only, nothing → `None`); `build_vulnerability` keeps all `Sourced` values, de-duplicates references, earliest `published_at`.
- **run:** first match → one `new` row with the stored GUID and `published_at == fake_clock.now()`; rerun of the same batches → no decision; KEV record for a published vulnerability → one `kev`; score rise across a band → one `band-*`; score change within the band, a new source without KEV, a description edit → none; **FR-26**: a vulnerability stored without matching data (no product, description not mentioning the product) → none; a later record adding the product → `new`; **FR-16**: endpoint A with history and endpoint B added later (new snapshot) → B gets `new` for vulnerabilities active ≤ 7 days ago that it matches, nothing older (activity 8 days ago), A gets nothing; disabled endpoint → nothing; touched vulnerability active 20 days ago (old KEV entry with a changed name) → nothing for an endpoint with history; cursors written for batches with a `next_cursor`, untouched for `None`; **atomicity**: make `add_publication` (monkeypatched `Repository` method) raise on the second decision → no publication, no source record, no cursor persisted; `load_cursors` returns what `run` stored. Use `FakeClock`.
- **cli:** `db migrate` exit 0 and idempotent (`CVESTREAM_DATA_DIR=tmp_path/data` through `monkeypatch.setenv`, `monkeypatch.chdir(tmp_path)` and `delenv` of `CVESTREAM_SETTINGS_FILE`/`CVESTREAM_STATE__URL` as in `tests/unit/cli/test_config.py`); `db backup` writes a file whose tables and row counts match (open it with `sqlite3`, `PRAGMA integrity_check` = `ok`); missing database, existing target and a `postgresql+psycopg://...` URL → exit 2 with one stderr line (the PostgreSQL case must not try to connect: `psycopg` is not installed; check the dialect from the URL before creating an engine, or build the engine lazily); `db` without arguments shows help.
- **integration `test_restart_no_repost.py` (guardrail, must stay green):** build `FetchResult`s by running `EuvdAdapter` and `CisaKevAdapter` against the **recorded** fixtures with respx (reuse the request setup of `tests/contract/euvd/test_euvd.py`: fixture paths, a no-op `sleep`, `FakeClock`), pick an endpoint whose product list matches at least one recorded EUVD record (read the fixture to choose it, e.g. a product name from `euvd_search_page0.json`), then: run 1 → at least one `new`; `state.dispose()`, new `State` on the same file (the restart) → run 2 with the same batches → zero decisions and the same publication rows (compare `(guid, kind)` sets); advance the clock one day → run 3 → zero; then a batch with one KEV record for an already published CVE (take a real `cveID` from `cisa_kev_catalog.json` that the endpoint matches, or the EU KEV dump; if no recorded KEV entry shares a CVE with the recorded EUVD records, build the KEV `SourceRecord` in the test for a published CVE and say so in a comment) → exactly one `kev`; restart again → rerun → zero.

Determinism: `FakeClock` everywhere, no `datetime.now()`, no sleeps. `structlog.testing.capture_logs()` to assert `publish.decided` if needed.

### Architecture compliance

- Tables only in `state/schema.py`; queries only in `state/repository.py`; one `State.transaction()` per run, opened only by `pipeline/run.py` (`cli/db.py` goes through `State.migrate()` and `state/backup.py`, which own their connection inside `state/`). No `commit()` elsewhere.
- Portable SQL only: no `INSERT OR REPLACE`, no `PRAGMA` outside the SQLite connect event, no `RETURNING`, no SQLite-only types; upserts only through `_upsert()`. The code must work on PostgreSQL once `psycopg` is added (not tested in this story).
- Migration file naming `NNNN_<verb>_<object>.py`; never edit `0001` once merged (1.6 adds `0002_add_feed_cache.py`).
- GUID computed once, stored, never recomputed; decision and its row in the same transaction.
- Unknown is `None`; every `Sourced` value kept; `band`/`max_score` computed, never stored as truth (the stored `band` is the history of what was published).
- Time from the injected `Clock` only; aware UTC; ruff `DTZ` stays green (`datetime.fromisoformat` with `Z`, never naive `strptime`).
- Exceptions: `StateError(CvestreamError)` family; no bare `except:`, no `except Exception` (nothing here is a supervisor).
- Logs: `<area>.<past-participle>` (`state.migrated`, `pipeline.record_skipped`, `pipeline.vulnerabilities_merged`, `publish.decided`, `pipeline.completed`), standard fields; never a token, never a database password.
- Typing: every public function annotated, `X | None`, no `from __future__ import annotations`; `ty check` with `error-on-warning`.
- Modules are singular nouns at the architecture-tree paths; `state/codec.py` is the only addition and is amended into the tree (Task 13.6). No `utils.py`.

### Library / framework requirements

| Package | Version | Use |
|---|---|---|
| SQLAlchemy | 2.1.6 (`>=2.1.6`; latest, 2026-10-21; requires Python ≥ 3.11) | Core only (no ORM, no `Session`): `create_engine`, `MetaData(naming_convention=...)`, `Table`, `Column`, `UniqueConstraint`, `Index`, `ForeignKey`, `TypeDecorator`, `JSON`, `select`, `update`, `delete`, `func.max`, `event.listens_for(engine, "connect")`, `Connection`, `engine.begin()`, `engine.raw_connection()`, `sqlalchemy.dialects.sqlite.insert` / `postgresql.insert` with `on_conflict_do_update` / `on_conflict_do_nothing`, `URL.render_as_string(hide_password=True)`, `make_url`. |
| Alembic | 1.20.1 (`>=1.20.1`; latest, 2026-10-22; pulls `Mako`) | `alembic.config.Config()` without a file, `set_main_option("script_location", "cvestream.state:migrations")`, `config.attributes["connection"]`, `alembic.command.upgrade(cfg, "head")`, `context.configure(..., render_as_batch=True)`, `MigrationContext`, `ScriptDirectory`, `alembic.autogenerate.compare_metadata` (tests). |
| sqlite3 | stdlib (Python 3.14) | `Connection.autocommit`, `Connection.backup()`; only inside `state/`. |
| typer, pydantic, pydantic-settings, structlog | already dependencies | CLI, settings (`SecretStr`), logging. |

No other dependency: no `psycopg` (later), no `sqlmodel`, no ORM, no `aiosqlite`, no `alembic.ini`.

### File structure requirements

New:

```
src/cvestream/state/schema.py
src/cvestream/state/codec.py
src/cvestream/state/db.py
src/cvestream/state/repository.py
src/cvestream/state/backup.py
src/cvestream/state/migrations/env.py
src/cvestream/state/migrations/script.py.mako
src/cvestream/state/migrations/versions/0001_create_schema.py
src/cvestream/domain/publication.py
src/cvestream/pipeline/normalize.py
src/cvestream/pipeline/correlate.py
src/cvestream/pipeline/run.py
src/cvestream/cli/db.py
tests/unit/state/__init__.py, test_schema.py, test_codec.py, test_db.py, test_repository.py, test_backup.py
tests/unit/domain/test_publication.py
tests/unit/pipeline/__init__.py, test_normalize.py, test_correlate.py, test_run.py
tests/unit/cli/test_db.py
tests/integration/__init__.py, test_restart_no_repost.py
```

- `state/migrations/` and `state/migrations/versions/` are **not** Python packages (no `__init__.py`): Alembic loads them by path. Check that ruff and ty still pass on them (they are under `src/`; `env.py` uses `alembic.context`, which ty may type loosely: if ty reports `alembic.context` attributes as unknown, prefer `from alembic import context` and a narrow `# ty: ignore[<rule>]` with the reason, never a blanket ignore).
- `state/__init__.py`, `pipeline/__init__.py` keep only their docstrings (no re-exports).

Modified: `pyproject.toml` (deps, import-linter), `uv.lock`, `src/cvestream/settings.py`, `src/cvestream/cli/main.py` (one `add_typer`), `tests/conftest.py` (`state` fixture), `tests/unit/test_settings.py`, `Dockerfile` (one `ENV`), `settings.example.yaml`, `docs/configuration.md`, `README.md`, `CLAUDE.md`, `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md`, `_bmad-output/implementation-artifacts/deferred-work.md`, this story file, `sprint-status.yaml`.

### Existing files this story touches (current state → change → preserve)

- `pyproject.toml`: runtime deps `httpx`, `packaging`, `pydantic`, `pydantic-settings[yaml]`, `structlog`, `typer`; 7 import-linter contracts. Change: Task 1 and Task 2 only. Preserve everything else (`addopts`, markers, ruff selection, per-file ignores, the layers contract).
- `src/cvestream/settings.py`: `Settings(log, sources, config_provider: Literal["yaml"], endpoints_file)`, custom sources. Change: Task 3. Preserve the source order, `extra="forbid"`, the `_environ` ContextVar mechanism and every existing field.
- `src/cvestream/cli/main.py`: root callback (`--debug`, `--version`), `app.add_typer(config_app, name="config")`. Change: `from cvestream.cli.db import db_app` and `app.add_typer(db_app, name="db")`. Preserve the rest.
- `tests/conftest.py`: `FakeClock`, `fake_clock`, `anyio_backend`. Change: add the `state` fixture (imports `cvestream.state.db.State`). Preserve the rest.
- `Dockerfile`: runtime stage `ENV PATH=... PYTHONUNBUFFERED=1`, `VOLUME /data`, `CMD ["--help"]`. Change: `CVESTREAM_DATA_DIR=/data` only; `CMD`/`HEALTHCHECK` stay for 1.6.
- `docs/configuration.md`, `settings.example.yaml`, `README.md`, `CLAUDE.md`, `architecture.md`, `project-context.md`: only the lines of Task 13.

### Decisions a reviewer may want to revisit (recorded here and in architecture.md)

- **Merge keys = CVE and EUVD only** (FR-7); GHSA and advisory IDs never merge. CERT-EU (2.1) records merge through their CVE aliases.
- **Aliases are append-only** (no split when a source drops an alias).
- **Publication window of 14 days** for vulnerabilities changed by a run, measured on the **source** activity time (`updated_at` / `published_at` / KEV `added_at`). An old CVE newly added to KEV today is inside the window (its `added_at` is today) and is published.
- **Backfill while an endpoint has no publication** (re-scans the 7-day window each run until the first publication). An edited product list on an existing endpoint does **not** backfill: it applies to vulnerabilities changed from then on. Alternatives: an `endpoint_state` table recording the backfill (not among the five tables the epic lists), or backfilling on every product-list change (needs a config fingerprint per endpoint).
- **`kev` wins over a band rise in the same run**, absorbing the band.
- **`unknown` → `none`** (CVSS 0.0) counts as a band rise (literal FR-19). Confirmed by the review decision on wixyvir/cvestream#5.
- **`state.url` is a secret** (`SecretStr`), so a future PostgreSQL password never reaches logs or `repr()`.
- **KEV removals are not modelled**: a source record never seen again is kept, so a vulnerability removed from a KEV catalogue keeps its flag.

### Delivery (stacked PR)

- Branch `story-1-5-correlation-state-and-publication-decisions`, created from `story-1-4-euvd-and-kev-ingestion` (which already sits on 1.3, so 1.5 has both). Draft PR base: the 1.4 branch (`epic-1-stack-plan.md`).
- Commits separated by concern, never squashed, each ending with a blank line and the attribution line. Suggested sequence: `chore(story-1.5): add sqlalchemy and alembic`; `chore(story-1.5): check library ownership on direct imports only`; `feat(story-1.5): add data_dir and state.url settings` (+ the deferred `ValueError` fix as `fix(story-1.5): report bad settings.yaml scalars as settings errors`); `feat(story-1.5): add state schema and UTC datetime type`; `feat(story-1.5): add source record codec`; `feat(story-1.5): add state database, migrations and backup`; `feat(story-1.5): add state repository`; `feat(story-1.5): add publication decisions`; `feat(story-1.5): add record normalization and correlation`; `feat(story-1.5): add pipeline run`; `feat(story-1.5): add db migrate and db backup commands`; `test(story-1.5): add restart no-repost guardrail`; `chore(story-1.5): set the data directory in the runtime image`; `docs(story-1.5): document state settings and publication rules`; `chore(story-1.5): mark story ready for review`. Tests may go with each `feat` commit.

### Previous story intelligence (1.1 to 1.4)

- Gate = `uv sync --locked`, `ruff check` (`E,F,W,I,UP,B,DTZ,BLE,S,RUF`), `ruff format --check`, `ty check` (`error-on-warning`), `lint-imports`, `pytest` (sockets blocked, `--strict-markers`, `-m "not live"`), `config schema --check`, `config validate endpoints.example.yaml`. `assert` only in `tests/**` (`S101` ignored there). 1.4 ended at 414 tests passing.
- 1.4: adapters stamp one `observed_at` per fetch on every `Sourced`; EUVD re-emits records equal to the cursor on purpose ("downstream is idempotent, a duplicate costs nothing"): that is why `content_hash` and the GUID uniqueness matter here. `eu_kev` records exist precisely so that `(source, record_id)` does not collide with the EUVD search record of the same EUVD ID. CISA records carry no `affected` (description only).
- 1.4 left two open decisions on EUVD paging (short-page stop, stale cursor beyond the page cap): not this story's concern, but this story stores the cursor the adapter returns, unchanged.
- 1.3: error messages never quote input values (tokens); the CLI pattern (`_fail`, one stderr line, exit 2) and the test isolation (`monkeypatch.chdir(tmp_path)`, `delenv` of `CVESTREAM_*`). `ConfigSnapshot` keeps disabled endpoints: filter them in the pipeline.
- 1.3 deferred: `settings.yaml` loader leaks a bare `ValueError` (fixed here, Task 3.2, because this story touches `settings.py`).
- 1.2: `ty` is strict about narrowing in tests (`hasattr` + assignment failed; `setattr` through a variable worked); test modules declare their own type aliases; absolute imports. The matcher's open decision (vendor-only hit and `min_version`) does not change this story's code.
- 1.1 deferred: tokens hidden in non-string log values; this story never logs tokens or URLs with passwords.

### Git intelligence

Recent commits on the base branch: `chore(story-1.4): record code review`, `docs(story-1.4): document review hardening of sources`, `fix(story-1.4): harden source adapters against drift and concurrent updates`, `fix(story-1.4): map deeply nested JSON to a payload error`, `fix(story-1.4): refuse HTTPS to HTTP redirects`, `chore(story-1.4): mark story ready for review`, `feat(story-1.4): add source registry`. Pattern: Conventional Commits scoped `(story-1.x)`, one concern per commit, dependency changes in a `chore` commit with `pyproject.toml` + `uv.lock`, docs in `docs(...)`, story bookkeeping in `chore(...)`.

### Latest technical information (checked 2026-10-26)

- SQLAlchemy 2.1.6 (PyPI, 2026-10-21) and Alembic 1.20.1 (2026-10-22) are the latest releases and match the architecture's pinned versions. import-linter 2.15 is installed; its `forbidden` contract has `allow_indirect_imports` (default `False`).
- SQLAlchemy 2.1 SQLite docs ("Transactions with SQLite and the sqlite3 driver"): the sqlite3 driver's legacy transaction control (default until Python 3.16) does not emit `BEGIN` before `SELECT` or DDL; on Python 3.12+ the recommended fix is `sqlite3.Connection.autocommit = False`, either via `connect_args` or in a `connect` event. This story uses the event (pragmas first, then `autocommit = False`) because `PRAGMA journal_mode=WAL` cannot run inside the transaction that `autocommit=False` keeps open.
- Alembic `Config` docs: `Config()` without a file plus `set_main_option("script_location", "myapp:migrations")` is the supported programmatic setup; `env.py` must not call `logging.fileConfig` in that case.

### Project Structure Notes

- Aligned with the architecture tree: `state/db.py`, `schema.py`, `repository.py`, `backup.py`, `migrations/env.py`, `script.py.mako`, `versions/0001_create_schema.py`, `domain/publication.py`, `pipeline/normalize.py`, `correlate.py`, `run.py`, `cli/db.py`, `tests/integration/test_restart_no_repost.py` are all listed there.
- Variance: `state/codec.py` (new, amended into the tree in Task 13.6). Alternative: private functions in `repository.py` (rejected: the codec is pure, testable on its own, and `repository.py` would mix SQL with serialization).
- Variance: import-linter ownership contracts check direct imports only (Task 2), amended into architecture.md.
- `pipeline/summary.py` and `pipeline/render.py` are 1.6's.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5: Correlation, state and publication decisions]
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-7, FR-8, FR-9, FR-16, FR-19, FR-24, FR-26, NFR-3, SM-4)
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Architecture] (state store, SQLite WAL, SQLAlchemy Core, Alembic, `UTCDateTime`, `_upsert()`, logical tables, block storage)
- [Source: _bmad-output/planning-artifacts/architecture.md#Naming Patterns] (database naming, GUID, identifiers)
- [Source: _bmad-output/planning-artifacts/architecture.md#Process Patterns] (transactions, errors, retries, async/sync)
- [Source: _bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries] (tree, component boundaries, data boundaries, requirements mapping)
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns] (first run, process model, CLI `db migrate|backup`)
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (backups, delivery workflow, runtime configuration)
- [Source: _bmad-output/project-context.md] (State, Publish once, Identity, Testing Rules, Anti-patterns)
- [Source: _bmad-output/implementation-artifacts/epic-1-stack-plan.md#3.5 Story 1.5] (manual checks the reviewer runs)
- [Source: _bmad-output/implementation-artifacts/1-4-euvd-and-kev-ingestion.md] (adapter output, `eu_kev`, cursors, fixtures)
- [Source: _bmad-output/implementation-artifacts/1-3-endpoint-configuration-in-yaml.md] (snapshot, CLI pattern)
- [Source: _bmad-output/implementation-artifacts/1-2-vulnerability-model-and-matching-engine.md] (domain API)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] (settings `ValueError` leak)

## Dev Agent Record

### Agent Model Used

claude-sonnet-5-5 (bmad-dev-story, autonomous run)

### Debug Log References

- `uv run pytest`: 692 passed, 2 deselected (`live`). Story 1.4 ended at 414; this story adds 278 tests.
- Gates all green locally: `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports` (7 contracts kept), `pytest`, `config schema --check`, `config validate endpoints.example.yaml`.
- `docker build --target runtime -t cvestream:local .` passed; `docker run --rm -v <tmp>:/data --user $(id -u) cvestream:local db migrate` exits 0 and writes `/data/cvestream.db` (Alembic and the migration files are in the image).
- Manual checks of `epic-1-stack-plan.md` section 3.5 with `CVESTREAM_DATA_DIR=$(mktemp -d)`: `db migrate` twice (second is a no-op), `sqlite3 .tables` lists the five tables plus `alembic_version`, `PRAGMA journal_mode` is `wal`, `db backup` writes a copy and refuses to overwrite it (exit 2), `grep -rn "cvestream:{" src/` finds one construction site (`domain/publication.py`), `.commit()` appears nowhere and `.transaction(` is called only in `pipeline/run.py` (plus its definition in `state/db.py`).

### Completion Notes List

- **Implemented:** `state/` (schema with `UTCDateTime`, codec, `State` with lazy engine and the SQLite connect event, repository with `_upsert()`, backup, Alembic in code and revision `0001`), `domain/publication.py` (`decide()`, the only GUID construction site, window constants), `pipeline/normalize.py`, `pipeline/correlate.py`, `pipeline/run.py` (one `State.transaction()` per run), `cli/db.py` (`db migrate`, `db backup`), the `data_dir` and `state.url` settings, the `CVESTREAM_DATA_DIR=/data` image variable, tests and docs.
- **Tests:** unit tests for every module, `tests/integration/test_restart_no_repost.py` (real EUVD and CISA KEV adapters on the recorded fixtures, a restart between runs, then a late KEV addition publishes exactly one `kev`, once). The "metadata equals migrations" test (`compare_metadata`) guards drift between `schema.py` and `0001`. A source-scan test (AST based) checks that nothing calls `.commit()` and that `.transaction()` is only called from `pipeline/run.py`.
- **Deferred item resolved:** the `settings.yaml` loader now turns `ValueError` and `RecursionError` into `SettingsError` (`deferred-work.md` item struck through).
- **Deviation 1, `StateError` and `NaiveDatetimeError` live in `state/schema.py`** and are re-exported by `state/db.py` (the story put `StateError` in `db.py`). `schema.py` and `codec.py` need them and `db.py` imports the repository, which imports the schema: defining them in `db.py` would be circular.
- **Deviation 2, `State` helpers:** `State._get_engine()` creates the engine lazily; `raw_sqlite_connection()` is a context manager (yields the `sqlite3.Connection`, returns it to the pool). `State.migrate()` turns an unopenable database or an uncreatable directory into `StateError`, and a missing driver (`psycopg` is not installed) into `StateError("no database driver for ...")`, so the CLI reports one line and exits 2 instead of showing a traceback.
- **Deviation 3, `repository.py` does not import `sources.base.Cursor`:** `state` and `sources` are independent siblings in the `layers` contract, so `set_cursor` takes `str | None` (what `Cursor` is).
- **Deviation 4, `Repository.create_vulnerability()` stores `primary_id = ""`** as a placeholder (the signature in the story has no ID); `refresh_vulnerability()` sets it before the transaction ends, because every created vulnerability receives a record and is therefore touched.
- **Not done on purpose:** raw-payload retention (architecture: "raw payloads kept 30 days"). Adapters emit `SourceRecord`s, not raw payloads, so there is nothing to keep yet.
- **Known limitation (recorded in the story decisions):** KEV removals are not modelled; a vulnerability removed from a KEV catalogue keeps its flag.
- **Log noise:** `state.migrated` is logged through structlog's default configuration, so `cvestream db migrate` prints that line before its own summary line (the CLI does not configure logging yet; the daemon of story 1.6 will).
- **Review notes:** `unknown` to `none` (CVSS 0.0) counts as a band rise (literal FR-19). A product-list edit on an existing endpoint does not backfill (see the decisions section). The first run publishes the last 7 days, not 14.

### File List

- `pyproject.toml`, `uv.lock` (modified: `sqlalchemy`, `alembic`, import-linter ownership contracts check direct imports only)
- `src/cvestream/settings.py` (modified: `data_dir`, `StateSettings`, `state_url()`, YAML `ValueError`/`RecursionError`)
- `src/cvestream/state/schema.py`, `codec.py`, `db.py`, `repository.py`, `backup.py` (new)
- `src/cvestream/state/migrations/env.py`, `script.py.mako`, `versions/0001_create_schema.py` (new)
- `src/cvestream/domain/publication.py` (new)
- `src/cvestream/pipeline/normalize.py`, `correlate.py`, `run.py` (new)
- `src/cvestream/cli/db.py` (new), `src/cvestream/cli/main.py` (modified)
- `tests/conftest.py` (modified: `state` fixture), `tests/unit/test_settings.py` (modified)
- `tests/unit/state/__init__.py`, `test_schema.py`, `test_codec.py`, `test_db.py`, `test_repository.py`, `test_backup.py` (new)
- `tests/unit/domain/test_publication.py` (new)
- `tests/unit/pipeline/__init__.py`, `test_normalize.py`, `test_correlate.py`, `test_run.py` (new)
- `tests/unit/cli/test_db.py` (new)
- `tests/integration/__init__.py`, `test_restart_no_repost.py` (new)
- `Dockerfile` (modified: `CVESTREAM_DATA_DIR=/data`)
- `settings.example.yaml`, `docs/configuration.md`, `README.md`, `CLAUDE.md`, `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md` (modified)
- `_bmad-output/implementation-artifacts/deferred-work.md`, `_bmad-output/implementation-artifacts/1-5-correlation-state-and-publication-decisions.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified)

### Change Log

- 2026-10-26: Implemented story 1.5 (state store, migrations and backup, source record codec, correlation, publication decisions, pipeline run, `db migrate` and `db backup`, restart no-repost guardrail, docs) and moved it to review.
- 2026-10-26: Code review: 3 patches applied (invalid `state.url` and non-database file are user errors, atomic `db backup` without partial files), 2 items deferred, 2 decisions pending; status back to in-progress.
- 2026-10-31: Review decisions settled: a decision whose GUID is already stored is skipped with a `publish.skipped` warning instead of aborting the run, and story 2.1 must merge advisory revisions (options 1 + 3); `unknown` to `none` still publishes `band-none` (option 1). Status set to done.
