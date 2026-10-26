# Story 1.4: EUVD and KEV ingestion

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a channel subscriber,
I want EUVD vulnerabilities and their CISA KEV / EU KEV exploitation status ingested reliably,
so that my feed is built from the European reference database and flags actively exploited issues.

## Acceptance Criteria

1. **Outbound HTTP (`cvestream.http`).** When a source performs a GET, it goes through `cvestream.http`, which uses httpx with `trust_env=False` (proxy variables ignored), TLS verified against the system trust store (`ssl.create_default_context()`), explicit timeouts and a response size limit, and retries only on timeout, 5xx and 429 (honouring `Retry-After`) with capped exponential backoff and jitter. TLS verification is never disabled.
2. **Adapter contract (`sources/base.py`, NFR-5).** It provides the `SourceAdapter` protocol (`name`, `async fetch(cursor) -> FetchResult(records, next_cursor)`) and the `SourceError` family (`SourceTransportError`, `SourcePayloadError`, `SourceContractError`, all `CvestreamError`). A record that fails validation is skipped and counted (`FetchResult.skipped`); a source-level failure raises a `SourceError` subclass and returns no `FetchResult`, so the cursor does not advance.
3. **EUVD incremental fetch (FR-1, FR-9).** Given a cursor, the `euvd` adapter pages `/api/search` incrementally on `fromUpdatedDate`, converts timezone-less dates to UTC per the documented assumption, turns placeholders (`""`, `n/a`, `-`, unscored `0`) into `None`, converts EPSS ×100 to a 0–1 fraction, and emits `SourceRecord`s with aliases, `Sourced` scores with CVSS version, and affected products/versions.
4. **EUVD first fetch.** With no cursor, the first fetch starts at now − 14 days (from the injected `Clock`).
5. **EU KEV status (FR-3).** On every fetch the `euvd` adapter also loads EUVD's KEV dump (`/api/kev/dump`) in full and emits one record per EU KEV entry, carrying `KevEntry(catalog=EU_KEV)`, so the flag comes from the list it was found in.
6. **CISA KEV (FR-3).** The `cisa_kev` adapter loads the full CISA KEV JSON catalogue and emits one KEV record per entry carrying `known_ransomware_use` (`Known` → `True`, anything else → `None`, never `False`).
7. **Contract tests (NFR-5).** Recorded payloads in `tests/fixtures/euvd/` and `tests/fixtures/cisa_kev/` (captured from the real APIs, named after what they show, e.g. `euvd_no_score.json`, `euvd_version_na.json`) drive `tests/contract/` with respx: normal, missing-score, `n/a`-version and malformed payloads behave as above, and each contract test module has a `live` variant that is deselected by default.
8. **Registry (FR-4).** `sources/registry.py` builds the enabled adapters from settings, each with its own interval.
9. **Docs.** `docs/sources.md` documents each source's endpoints, timezone, placeholder, pagination, cursor and KEV assumptions.

## Tasks / Subtasks

- [ ] **Task 1: Dependencies (AC: 1, 7)**
  - [ ] 1.1 `uv add "httpx>=0.28.1,<1"` (runtime; 0.28.1 is the latest stable, 1.0 is only in `.dev` releases: the `<1` cap is an architecture rule).
  - [ ] 1.2 `uv add --dev "respx>=0.23.1"` (0.23.1 is the latest; requires `httpx>=0.25`).
  - [ ] 1.3 Commit `pyproject.toml` + `uv.lock` together (`chore(story-1.4): add httpx and respx`). Never edit `uv.lock` by hand.

- [ ] **Task 2: `src/cvestream/http.py` (AC: 1)**: see "HTTP client design".
  - [ ] 2.1 `HttpError(CvestreamError)` and subclasses `HttpTransportError`, `HttpStatusError` (carries `status_code`), `HttpResponseTooLargeError`.
  - [ ] 2.2 `HttpClient` (async context manager wrapping one `httpx.AsyncClient`) with `async get(url, *, params=None, max_bytes=DEFAULT_MAX_BYTES) -> bytes`.
  - [ ] 2.3 Retry loop (timeout / 5xx / 429 only), full-jitter capped backoff, `Retry-After` (seconds or HTTP date), injectable `sleep`, `random` and `clock`.
  - [ ] 2.4 Streaming size limit (checks `Content-Length` first, then counts decoded bytes).

- [ ] **Task 3: `src/cvestream/sources/base.py` (AC: 2)**: see "Adapter contract design".
  - [ ] 3.1 `Cursor` type alias, `FetchResult`, `SourceAdapter` protocol.
  - [ ] 3.2 `SourceError(CvestreamError)` with a `source` attribute, and `SourceTransportError`, `SourcePayloadError`, `SourceContractError`.
  - [ ] 3.3 `async get_json(http, source, url, *, params=None, max_bytes=...) -> object`: the single place that turns `HttpError` / JSON decoding errors into `SourceError`s.

- [ ] **Task 4: Domain model addition (AC: 3)**
  - [ ] 4.1 Append `epss: Sourced[float] | None = None` as the **last** field of `SourceRecord` in `domain/model.py` (defaulted fields last; no other model change). Add one test in `tests/unit/domain/test_model.py` (default `None`, set value round-trips).

- [ ] **Task 5: `src/cvestream/sources/euvd.py` (AC: 3, 4, 5)**: see "EUVD adapter design".
  - [ ] 5.1 Private Pydantic payload models (`extra="ignore"`, camelCase aliases) for a search page, a search item and a KEV dump entry.
  - [ ] 5.2 Locale-independent EUVD date parser (`"Oct 26, 2026, 6:36:49 PM"` → aware UTC).
  - [ ] 5.3 Item → `SourceRecord` mapping with every placeholder rule.
  - [ ] 5.4 Paging loop: `fromUpdatedDate`, `size`, `page`, descending-order check, early stop, page cap, pause between pages.
  - [ ] 5.5 KEV dump → `eu_kev` records.
  - [ ] 5.6 Cursor encode/decode and `next_cursor`.

- [ ] **Task 6: `src/cvestream/sources/cisa_kev.py` (AC: 6)**: see "CISA KEV adapter design".

- [ ] **Task 7: `src/cvestream/sources/registry.py` (AC: 8)**: see "Registry design".

- [ ] **Task 8: Fixtures (AC: 7)**: see "Fixtures: how to record them".
  - [ ] 8.1 Record from the real APIs into `tests/fixtures/euvd/` and `tests/fixtures/cisa_kev/`.
  - [ ] 8.2 `tests/fixtures/README.md`: one line per fixture with the recording date, URL and the `jq` selection command used (provenance).

- [ ] **Task 9: Tests (AC: all)**: see "Testing requirements".
  - [ ] 9.1 `tests/unit/test_http.py`.
  - [ ] 9.2 `tests/unit/sources/__init__.py`, `test_base.py`, `test_registry.py`.
  - [ ] 9.3 `tests/contract/__init__.py`, `tests/contract/euvd/__init__.py` + `test_euvd.py`, `tests/contract/cisa_kev/__init__.py` + `test_cisa_kev.py`, each with its `live` variant.

- [ ] **Task 10: Layering contracts (AC: 1)**
  - [ ] 10.1 In `[tool.importlinter]`: add `"cvestream.http"` to the `source_modules` of the `sqlalchemy and alembic only in state`, `valkey only in config`, `starlette and uvicorn only in app` and `typer only in cli` contracts; remove the parentheses around `cvestream.http` in the `layers` contract (it now exists); delete the comment line "Add cvestream.http to the non-owner lists in story 1.4 when it exists." Do not change anything else in `[tool.importlinter]`.

- [ ] **Task 11: Docs (AC: 9, same PR)**
  - [ ] 11.1 New `docs/sources.md` (content listed under "docs/sources.md content").
  - [ ] 11.2 `architecture.md`: (a) Identifiers table, "Source name" row: add `eu_kev` with a note "(record source of EU KEV entries, emitted by the `euvd` adapter)"; (b) API & Communication Patterns, "Source adapters" bullet: add that the cursor is an opaque string owned by the adapter, and that the `euvd` adapter also loads `/api/kev/dump` in full on every fetch and emits `eu_kev` records.
  - [ ] 11.3 `_bmad-output/project-context.md`: Identifiers bullet, source names: add `eu_kev` (same note). Sources bullet: add "the cursor is an opaque string; `next_cursor` comes from source data, never from the local clock".
  - [ ] 11.4 `CLAUDE.md` "Key rules": one line: "Outbound HTTP goes only through `cvestream.http` (`trust_env=False`, system trust store, retries live there and nowhere else)."
  - [ ] 11.5 `README.md`: add `docs/sources.md` next to the existing "Design:" link (one line). Keep the "Status" paragraph unchanged (there are still no feeds).

- [ ] **Task 12: Gates (all ACs)**
  - [ ] 12.1 `uv sync --locked && uv run ruff check && uv run ruff format --check && uv run ty check && uv run lint-imports && uv run pytest` all green; `uv run pytest --collect-only -m live -q tests/contract` lists the live tests and the default run deselects them.
  - [ ] 12.2 `grep -rn "import httpx\|from httpx" src/` shows only `src/cvestream/http.py` and `src/cvestream/sources/*`.
  - [ ] 12.3 `docker build --target runtime -t cvestream:local .` then `docker run --rm --entrypoint python cvestream:local -c "import ssl, cvestream.sources.registry; assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0"` exits 0 (httpx is in the runtime image and the system trust store is populated). If Docker is unavailable, say so in the Completion Notes; do not claim it ran.

## Dev Notes

### Scope boundaries (read first)

- **In this story:** `cvestream/http.py`, `sources/base.py`, `sources/euvd.py`, `sources/cisa_kev.py`, `sources/registry.py`, the `epss` field on `SourceRecord`, fixtures, unit and contract tests, `docs/sources.md`, the doc amendments listed in Task 11, the import-linter edits of Task 10.
- **Not in this story:** state tables, cursor persistence and merging records into a `Vulnerability` (1.5: `source_cursor`, `source_record`, `pipeline/normalize.py`, `pipeline/correlate.py`); source loops, intervals in action, jitter between polls, supervisors, `source.stale`, `cycle.summary` (1.6); CERT-EU / CERT-FR / generic adapters (2.1, 2.2); `source-drift.yml` (2.2: it will run the `live` tests written here); CLI commands. Do **not** add a CLI command, a setting, or a state table.
- **No change to `settings.py` or `settings.example.yaml`.** `SourcesSettings` already has `euvd` and `cisa_kev` (`enabled`, `interval`, default 15 min, max 60 min). Base URLs, page size, timeouts and size limits are module constants or constructor parameters, not settings. This keeps the rebase onto story 1.3 (which edits settings) conflict-free there.
- **No change to `domain/` apart from Task 4.** No change to `domain/version.py`: the adapter passes raw version text; parsing stays in 1.2's `parse_range` (the 1.2 deferred item about `unspecified <X`, `* <X`, `>=X <Y` and a standalone `0` was checked against 1,100 live EUVD records on 2026-10-26: none of those shapes occurred, so it stays deferred).

### Live API facts (verified 2026-10-26, drive the design)

EUVD, base `https://euvdservices.enisa.europa.eu/api`, GET only, no auth:

- `GET /search?fromUpdatedDate=YYYY-MM-DD&size=100&page=N` → `{"items": [...], "total": <int>}`.
  - `fromUpdatedDate` accepts **only `yyyy-MM-dd`** (day granularity). `2026-10-26T18:00:00` and `2026-10-26 18:00:00` return HTTP 400 `{"error": "Invalid 'fromUpdatedDate': ... Expected format: yyyy-MM-dd"}`. The day boundary is the UTC day (same clock as `dateUpdated`).
  - `size` max is 100 (101 silently gives 100). `page` starts at 0; a page past the end returns `items: []` with the same `total`.
  - Items are sorted by `dateUpdated` **descending** (newest first), checked over 196 pages.
  - Volume: about 2,100 records updated on a busy day; `fromUpdatedDate` 14 days back gave `total` 19,597 (196 pages, all reachable: no result-window cap at page 195). A page of 100 is 300–370 KB and answers in about 0.3 s.
  - Item keys: `id`, `enisaUuid`, `description`, `datePublished`, `dateUpdated`, `baseScore`, `baseScoreVersion`, `baseScoreVector`, `references`, `aliases`, `assigner`, `epss`, `enisaIdProduct[]`, `enisaIdVendor[]`, and `exploitedSince` only on exploited records.
  - `aliases` and `references` are newline-separated strings with a trailing `\n` (`"CVE-2026-110215\nGHSA-gwjx-qpv6-g7jw\n"`).
  - Dates: `"Oct 26, 2026, 6:36:49 PM"` (English month abbreviation, day without padding, 12-hour clock, `AM`/`PM`, ASCII space before it; no timezone). `exploitedSince` is `"Oct 13, 2026, 12:00:00 AM"`.
  - **Timezone is UTC**: EUVD-2026-99256 shows `datePublished "Oct 13, 2026, 6:50:03 PM"`, `dateUpdated "Oct 26, 2026, 6:36:49 PM"`; CVE.org's record for its alias CVE-2026-110215 shows `datePublished 2026-10-13T18:50:03.848Z`, `dateUpdated 2026-10-26T18:36:49.707Z`. Same wall clock on a CEST date (Oct 13) and a CET date (Oct 26): the naive dates are UTC. This is the documented assumption.
  - Unscored records: `baseScore` is `0.0` and the keys `baseScoreVersion` / `baseScoreVector` are **absent** (9 of 1,100 records). `?fromScore=0&toScore=0` selects them.
  - `epss` is a percentage (`9.81`, `86.27`, `0.41`); `0.0` on 79 of 1,100 records (mostly new CVEs not yet scored).
  - `enisaIdProduct[]` item: `{"id": "...", "product": {"name": "youlai-boot", "vendor": {"name": "youlaitech"}}, "product_version": "0 ≤2.21.1"}`. Placeholders seen: `product_version` `"-"` (22) and `"n/a"` (6); `product.name` `"n/a"` (6); 6 records had an empty `enisaIdProduct`.
  - Public record page: `https://euvd.enisa.europa.eu/vulnerability/{EUVD-ID}` (returns 200).
- `GET /kev/dump` → JSON **list** (about 260 KB, 1,772 entries): `{"cveId": "CVE-2023-3519", "euvdId": "EUVD-2023-44176", "dateAdded": "2023-07-19", "sources": ["cisa_kev", "eukev_kev"]}`, sometimes plus `vendorProject`, `product`, `vulnerabilityName`. `sources` distribution: `["cisa_kev"]` 1,694, `["cisa_kev","eukev_kev"]` 63, `["eukev_kev"]` 15.
  - **An EU KEV addition does not bump `dateUpdated`**: EUVD-2026-23571 was added to EU KEV on 2026-10-10 (`exploitedSince "Oct 10, 2026, 12:00:00 AM"`) but its `dateUpdated` is still `Aug 17, 2026`. So EU KEV status cannot come from incremental `/search` alone: the dump must be loaded in full on every fetch (architecture "First run": "KEV dumps loaded in full").
  - `exploitedSince` on a search record does not say which catalogue flagged it: ignore it (documented); the flag comes only from the catalogues.

CISA KEV, `GET https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json` (1.8 MB, `ETag` and `Last-Modified` sent):

- `{"title", "catalogVersion": "2026.10.23", "dateReleased": "2026-10-23T19:30:18.9209Z", "count": 1756, "vulnerabilities": [...]}`.
- Entry keys: `cveID` (capital `ID`), `vendorProject`, `product`, `vulnerabilityName`, `dateAdded` (`"2026-10-23"`), `shortDescription`, `requiredAction`, `dueDate`, `knownRansomwareCampaignUse` (`"Known"` 361 / `"Unknown"` 1,395), `notes`, `cwes[]`, `forensicTriage`.

### HTTP client design (`src/cvestream/http.py`)

```python
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
DEFAULT_MAX_BYTES = 16 * 1024 * 1024        # CISA KEV is ~1.8 MB, an EUVD page ~0.4 MB
MAX_ATTEMPTS = 4                             # 1 try + 3 retries
BACKOFF_BASE = 1.0                           # seconds
BACKOFF_CAP = 30.0                           # seconds, per wait
MAX_RETRY_AFTER = 60.0                       # longer Retry-After: give up, the next cycle retries

class HttpError(CvestreamError): ...
class HttpTransportError(HttpError): ...           # timeout after retries, connect/TLS/protocol error
class HttpStatusError(HttpError):                   # non-2xx final status
    status_code: int
class HttpResponseTooLargeError(HttpError): ...

class HttpClient:
    def __init__(self, *, timeout=DEFAULT_TIMEOUT, max_attempts=MAX_ATTEMPTS,
                 clock: Clock | None = None,                        # SystemClock() default; Retry-After dates
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 random: Callable[[], float] = random.random,       # jitter, injectable
                 transport: httpx.AsyncBaseTransport | None = None) -> None
    async def __aenter__(self) -> Self / async def __aexit__(...) / async def aclose(self) -> None
    async def get(self, url: str, *, params: Mapping[str, str | int] | None = None,
                  max_bytes: int = DEFAULT_MAX_BYTES) -> bytes
```

- `httpx.AsyncClient(trust_env=False, verify=ssl.create_default_context(), timeout=timeout, follow_redirects=True, headers={"User-Agent": f"cvestream/{__version__} (+https://github.com/wixyvir/cvestream)", "Accept": "application/json"}, transport=transport)`. httpx's default trust store is `certifi`, **not** the system store: passing `ssl.create_default_context()` is what makes it the system store (Debian `ca-certificates` in the image). Never pass `verify=False`, never read proxy variables. `__version__` comes from `cvestream` (`from cvestream import __version__`).
- **Retry policy** (only here; the pipeline and adapters never retry):
  - Retry on `httpx.TimeoutException` (connect, read, write, pool timeouts), status `>= 500`, and `429`. Nothing else: `httpx.ConnectError`, TLS errors, other `httpx.TransportError`s and every other 4xx fail at once (`HttpTransportError` / `HttpStatusError`). The next poll cycle is the retry for those.
  - Wait before retry *n* (n = 1, 2, 3): if the response has a usable `Retry-After` (integer seconds, or an HTTP date via `email.utils.parsedate_to_datetime`, minus `clock.now()`, floored at 0) wait exactly that, but when it is above `MAX_RETRY_AFTER` stop retrying and raise `HttpStatusError`. Otherwise full jitter: `random() * min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (n - 1))`.
  - After the last attempt: timeout → `HttpTransportError`; 5xx/429 → `HttpStatusError(status_code=...)`. Chain the cause (`raise ... from exc`).
  - Log each retry: `log.warning("http.retried", url=url, attempt=n, status=status_or_None, delay=..., error_type=...)` (structlog `get_logger`; outbound URLs never carry tokens). No other logging in this module.
  - `random.random` passed as a default is fine; if ruff `S311` flags a call, keep the call and add `# noqa: S311` with the reason "jitter, not security".
- **Size limit:** use `client.stream("GET", ...)`. If `Content-Length` is present and `> max_bytes`, raise `HttpResponseTooLargeError` without reading the body. Otherwise accumulate `aiter_bytes()` (decoded, so a compressed bomb is caught too) and raise as soon as the total exceeds `max_bytes`. A body of exactly `max_bytes` is accepted.
- Read the status **before** the body; a retried 5xx body is not read beyond what is needed (`await response.aclose()` through the context manager).
- `async` is allowed here (architecture: `async` only in `app/`, source `fetch()` and `cvestream.http`).

### Adapter contract design (`src/cvestream/sources/base.py`)

```python
type Cursor = str                     # opaque, owned by the adapter; 1.5 stores it as text

@dataclass(frozen=True, slots=True)
class FetchResult:
    records: tuple[SourceRecord, ...]
    next_cursor: Cursor | None        # None: the adapter keeps no cursor (KEV catalogues)
    skipped: int = 0                  # records dropped by record-level validation

class SourceAdapter(Protocol):
    @property
    def name(self) -> str: ...        # euvd | cisa_kev (registry key, cursor key, log field `source`)
    async def fetch(self, cursor: Cursor | None) -> FetchResult: ...

class SourceError(CvestreamError):
    def __init__(self, source: str, message: str) -> None   # keeps `.source`; str(exc) = message
class SourceTransportError(SourceError): ...  # no usable response: network, TLS, timeout, HTTP error status
class SourcePayloadError(SourceError): ...    # a response that is not data: not JSON, bad encoding, too large
class SourceContractError(SourceError): ...   # valid JSON in an unexpected shape: API drift

async def get_json(http: HttpClient, source: str, url: str, *, params: Mapping[str, str | int] | None = None,
                   max_bytes: int = DEFAULT_MAX_BYTES) -> object
```

- `get_json` is the **only** translation point: `HttpResponseTooLargeError` → `SourcePayloadError`; any other `HttpError` → `SourceTransportError`; `json.loads` failure (`ValueError`, which covers `JSONDecodeError` and `UnicodeDecodeError`) → `SourcePayloadError`. Always `raise ... from exc`. Adapters never catch `httpx` exceptions themselves.
- Top-level shape problems (missing `items`, `items` not a list, `vulnerabilities` missing, KEV dump not a list) → `SourceContractError`. Validate the top level with a small Pydantic model or `isinstance` checks, then validate **each item separately** so one bad item is skipped, not fatal.
- **Record-level failure** (an item that fails its Pydantic model, an unusable ID, an unparseable `dateUpdated`): skip it, `skipped += 1`, `log.debug("source.record_skipped", source=..., record_id=<id or None>, error_type=type(exc).__name__)`. Never `except Exception`; catch `pydantic.ValidationError` and the adapter's own parse errors only.
- **Source-level failure:** raise. `fetch` is all-or-nothing: no partial `FetchResult`, so the caller (1.5/1.6) never advances the cursor on failure. Do not log source failures in the adapter (the supervisor in 1.6 logs `source.failed`); do not swallow anything.
- If `ty` rejects a plain class attribute `name = "euvd"` against the protocol's read-only property, implement `name` as a property in the adapter too.
- Placing `get_json` in `base.py` is deliberate: it is the sources layer's shared module (no `utils.py`).

### EUVD adapter design (`src/cvestream/sources/euvd.py`)

```python
NAME = "euvd"
EU_KEV_SOURCE = "eu_kev"
BASE_URL = "https://euvdservices.enisa.europa.eu/api"
RECORD_URL = "https://euvd.enisa.europa.eu/vulnerability/{id}"
PAGE_SIZE = 100            # API maximum
PAGE_PAUSE = 0.5           # seconds between pages ("sequential pages, short pauses")
MAX_PAGES = 1000           # safety cap (14 days ≈ 200 pages)
INITIAL_WINDOW = timedelta(days=14)

class EuvdAdapter:
    def __init__(self, http: HttpClient, clock: Clock, *, page_size: int = PAGE_SIZE,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None
    name = "euvd"
    async def fetch(self, cursor: Cursor | None) -> FetchResult
```

**Fetch order:** (1) `GET /kev/dump` (small, all-or-nothing), (2) page `/search`. `observed_at = clock.now()` is read **once** at the start and used for every `Sourced` and `SourceRecord.observed_at` of this fetch.

**Cursor.** Encoded as ISO 8601 UTC with `Z` and second precision (`"2026-10-26T18:36:49Z"`).
- `start = decode(cursor)` or, when `cursor is None`, `clock.now() - INITIAL_WINDOW` (AC 4).
- An unparseable or naive cursor string: log `source.cursor_invalid` (warning, `source="euvd"`) and fall back to `clock.now() - INITIAL_WINDOW`. Never crash on our own stored data.
- Query `fromUpdatedDate = start.date().isoformat()` (UTC day), `size = page_size`, `page = 0, 1, ...`.
- `next_cursor = encode(max(start, max(updated_at of emitted search records)))`: it comes from **source data**, never from the local clock, so local clock skew cannot skip records. With no new record, `next_cursor` encodes `start` (for a `None` cursor that is now − 14 days, so the window does not drift).

**Paging loop**, for page `p = 0, 1, ...`:
1. `data = await get_json(...)`; top level must be an object with a list `items` and an int `total`, else `SourceContractError`.
2. Validate each item; skipped items are counted. Map valid ones (below).
3. **Order check:** the `updated_at` of the valid items, in response order and across pages, must be non-increasing; otherwise raise `SourceContractError("EUVD /search is no longer sorted by dateUpdated descending")`. This is what makes the early stop safe: if EUVD ever sorts ascending, an early stop would silently drop almost everything, so it must fail loudly instead (and the `live` test and source-drift job will show it).
4. Keep records with `updated_at >= start`; drop older ones (they belong to the part of the start day before the cursor). Records equal to `start` are re-emitted on purpose: downstream (1.5) is idempotent, a duplicate costs nothing, a miss is a lost alert.
5. **Stop** when: `items` is empty; or `len(items) < page_size`; or `(p + 1) * page_size >= total`; or the oldest valid item of the page is `< start`. Otherwise `await sleep(PAGE_PAUSE)` and fetch the next page.
6. Reaching `MAX_PAGES` without a stop condition → `SourceContractError` (the cursor must not advance past data it did not read).

- **De-duplication:** emit each EUVD `id` once per fetch, keeping the first occurrence (newest, since the order is descending). Concurrent updates during paging shift items down, which yields duplicates, never gaps, except in one case: a record updated *while* we page jumps to page 0 and the items behind it shift up by one, so one record at a page boundary can be missed until its next update. Accepted and documented (it needs an update during the few seconds of paging, and the next update re-delivers it).

**Item → `SourceRecord`** (`source="euvd"`, `record_id=id`):

| EUVD field | `SourceRecord` field | Rule |
|---|---|---|
| `id` | `record_id`, first alias, `url` | Required; must fullmatch `EUVD-\d{4}-\d+` after `strip()`, else the record is skipped. `url = RECORD_URL.format(id=...)`. |
| `aliases` | `aliases` | `(id, *split)`: split on `\n`, `strip()`, drop placeholders, de-duplicate keeping order. Raw values (case as given); normalization is 1.5's job (`identity.normalize_alias`). |
| `description` | `description` | `Sourced(text, "euvd", observed_at)`; placeholder → `None`. |
| — | `title` | `None` (EUVD has no title). |
| `baseScore`, `baseScoreVersion`, `baseScoreVector` | `scores` | Always exactly one entry: `(Sourced(value, "euvd", observed_at),)`. `value = None` (source says unscored) when `baseScore` is missing / `None` / placeholder, or when `baseScore == 0` and `baseScoreVersion` is missing or a placeholder (EUVD's "unscored", AC 3), or when it is outside `0.0..10.0`. Otherwise `CvssScore(score=round(float(baseScore), 1), version=<placeholder→None>, vector=<placeholder→None>)`. A `0.0` **with** a version is a real CVSS "none" and stays `0.0`. |
| `epss` | `epss` | `Sourced(epss / 100, "euvd", observed_at)` when `0 < epss <= 100`; `0`, missing, placeholder or out of range → `None` (unknown; EUVD shows `0.0` for CVEs not yet scored, and a real EPSS probability is never exactly 0). |
| `enisaIdProduct[]` | `affected` | One `Sourced(AffectedProduct(product=name, vendor=vendor or None, version_text=version or None), "euvd", observed_at)` per entry. An entry whose `product.name` is missing or a placeholder is dropped (the record is kept). `product_version` placeholder → `version_text=None`. Keep the raw version text otherwise (no parsing here). |
| `references` | `references` | Split on `\n`, `strip()`, drop empties. Display only, never identity. |
| `datePublished` | `published_at` | EUVD date → UTC; unparseable or missing → `None` (the record is kept). |
| `dateUpdated` | `updated_at` | EUVD date → UTC; **required** (the cursor depends on it): unparseable or missing → record skipped. |
| `exploitedSince`, `assigner`, `enisaUuid`, `enisaIdVendor` | — | Ignored (documented in `docs/sources.md`). Search records carry **no** `kev` entry: EU KEV comes only from the dump (single source of truth, no duplicate flags). |

**Placeholders** (every string field, after `strip()`, case-insensitive): `""`, `"n/a"`, `"na"`, `"-"`, `"unknown"` → `None`. One private helper `_text(value) -> str | None` used for every string field.

**EUVD date parser:** do **not** use `datetime.strptime` with `%b`/`%p` (locale-dependent). Use one `re.fullmatch` such as `(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?P<year>\d{4}), (?P<hour>\d{1,2}):(?P<min>\d{2}):(?P<sec>\d{2})\s(?P<ampm>AM|PM)` with a fixed English month table (`Jan` … `Dec`), and `\s` before AM/PM so a narrow no-break space (U+202F, used by recent Java date formatters) still parses. 12-hour clock: `12:xx AM` → hour 0, `12:xx PM` → hour 12, `1..11 PM` → +12. Build `datetime(..., tzinfo=UTC)` directly (no naive datetime is ever created: ruff `DTZ` stays green). Return `None` on any mismatch or invalid date (`ValueError` from `datetime(...)`).

**KEV dump → `eu_kev` records.** For each dump entry whose `sources` list contains `"eukev_kev"` (CISA-only entries are ignored here: `cisa_kev` is authoritative for them and adds ransomware use):
- `SourceRecord(source="eu_kev", record_id=<euvdId or, if missing, cveId>, observed_at, aliases=(euvdId, cveId) without missing ones, title=Sourced(vulnerabilityName, "eu_kev", ...) when present else None, kev=(Sourced(KevEntry(catalog=KevCatalog.EU_KEV, known_ransomware_use=None, added_at=<dateAdded midnight UTC or None>), "eu_kev", observed_at),), url=RECORD_URL for the EUVD ID when present)`.
- An entry with neither `euvdId` nor `cveId` → skipped and counted. `dateAdded` not `YYYY-MM-DD` → `added_at=None` (the flag is what matters).
- `source="eu_kev"` (not `"euvd"`) because 1.5 will key `source_record` on `(source, record_id)`: a KEV-only record with `source="euvd"` would overwrite the full EUVD search record of the same ID. The adapter `name` (registry, cursor, logs) stays `euvd`.
- The dump body is not a list, or holds no `eukev_kev` entry at all while being non-empty, is fine; an **empty list** is suspicious (the catalogue never shrinks to zero) → `SourceContractError`.
- Dump records go first in `FetchResult.records`, then search records in response order.

### CISA KEV adapter design (`src/cvestream/sources/cisa_kev.py`)

```python
NAME = "cisa_kev"
CATALOG_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CATALOG_PAGE = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"

class CisaKevAdapter:
    def __init__(self, http: HttpClient, clock: Clock) -> None
    name = "cisa_kev"
    async def fetch(self, cursor: Cursor | None) -> FetchResult   # cursor ignored: full load every time
```

- Top level must be an object with a list `vulnerabilities`, else `SourceContractError`. An empty list → `SourceContractError`.
- Per entry (Pydantic, `extra="ignore"`, alias `cveID`): `cveID` required and must fullmatch `CVE-\d{4}-\d{4,}` (after `strip()`, upper-case), else skipped and counted.
- `SourceRecord(source="cisa_kev", record_id=cveID, observed_at, aliases=(cveID,), title=Sourced(vulnerabilityName) or None, description=Sourced(shortDescription) or None, kev=(Sourced(KevEntry(catalog=KevCatalog.CISA_KEV, known_ransomware_use=<True if "Known" else None>, added_at=<dateAdded midnight UTC or None>), "cisa_kev", observed_at),), url=CATALOG_PAGE)`.
- `knownRansomwareCampaignUse`: `"Known"` (case-insensitive) → `True`; `"Unknown"`, missing or anything else → `None`. CISA only says "known" or "unknown", so `False` is never produced (unknown is `None`).
- **No `affected`** from CISA: `vendorProject` / `product` are coarse labels without versions ("Netlogon (ZeroLogon)"); as structured hits they would turn every KEV item into a `versions_unknown` match for entries with `min_version` (the matcher treats a hit without version text as unusable). The CISA `shortDescription` still feeds description matching. Recorded as a reviewer-visible decision in Project Structure Notes.
- `dueDate`, `requiredAction`, `notes`, `cwes`, `forensicTriage` are not mapped (not required by FR-3/FR-18; `KevEntry` has no fields for them). Documented.
- `next_cursor = catalogVersion` when it is a non-empty string, else `None` (informational for `/status` in 1.6; never used to skip a load).
- Dates: CISA `dateAdded` is a calendar date; `added_at = datetime(y, m, d, tzinfo=UTC)` (documented assumption: midnight UTC, display only).

### Registry design (`src/cvestream/sources/registry.py`)

```python
@dataclass(frozen=True, slots=True)
class ScheduledSource:
    adapter: SourceAdapter
    interval: timedelta

def build_sources(settings: SourcesSettings, http: HttpClient, clock: Clock) -> tuple[ScheduledSource, ...]
```

- One private factory per source name: `{"euvd": lambda http, clock: EuvdAdapter(http, clock), "cisa_kev": lambda http, clock: CisaKevAdapter(http, clock)}`.
- Iterate over `SourcesSettings.model_fields` in declaration order (`euvd`, `cisa_kev`); skip `enabled: false`; `interval` from that source's settings (FR-4). A settings field without a factory is a programming error: raise `KeyError`/`LookupError` (a unit test asserts every `SourcesSettings` field has a factory, so story 2.1/2.2 cannot forget one).
- Story 1.6 owns the `HttpClient` lifetime (`async with HttpClient() as http:` around the source loops) and passes it here. The registry does not open clients.
- Layering: `sources` may import `cvestream.settings`, `cvestream.http`, `cvestream.clock`, `cvestream.errors`, `cvestream.domain` (lower layers). It must **not** import `cvestream.config` (`config | sources | state` are independent siblings in the layers contract).

### docs/sources.md content

New file (the `docs/` directory does not exist yet on this branch; story 1.3 adds `docs/configuration.md` next to it, no conflict). Sections:
- **Common behaviour:** all outbound calls through `cvestream.http` (no proxy: `trust_env=False`; system trust store; timeouts; 16 MiB response limit; retries only on timeout/5xx/429 with `Retry-After` up to 60 s, 4 attempts, full-jitter backoff capped at 30 s); error taxonomy (transport / payload / contract) and "cursor advances only after a fully successful fetch"; record-level skips are counted; placeholders (`""`, `n/a`, `na`, `-`, `unknown`) become unknown.
- **EUVD (`euvd`):** endpoints; `fromUpdatedDate` is day-granular; page size 100; descending order assumption and the order check; early stop; 0.5 s pause; 1,000-page cap; first run = now − 14 days; cursor = newest `dateUpdated` seen (ISO 8601 `Z`); dates are UTC (with the CVE.org evidence above); unscored = `baseScore 0` without version; EPSS ÷ 100, `0` = unknown; product placeholders; ignored fields (`exploitedSince`, `assigner`, `enisaIdVendor`); the known paging gap.
- **EU KEV (record source `eu_kev`, fetched by `euvd`):** `/api/kev/dump` loaded in full on every EUVD fetch, because an EU KEV addition does not change `dateUpdated` (EUVD-2026-23571 evidence); only `eukev_kev` entries are used; `dateAdded` = midnight UTC.
- **CISA KEV (`cisa_kev`):** URL; full load every poll; `Known` → known ransomware use, otherwise unknown; no affected products (why); unmapped fields; `catalogVersion` as informational cursor. Disabling `cisa_kev` in settings removes CISA flags (EUVD's dump is not used for them).

### Fixtures: how to record them

Fixtures are **recorded from the real APIs, never hand-written** (project rule). Record them with `curl`, then select real items with `jq` when a payload is too big; never edit a value. Write each command in `tests/fixtures/README.md` (fixture name, date, URL, `jq` filter). Pretty-printing with `jq .` is fine. Suggested set (adapt dates to the recording day; choose a past, closed day for `toUpdatedDate` so pages are stable):

| Fixture | How |
|---|---|
| `euvd/euvd_search_page0.json`, `euvd_search_page1.json` | `GET /api/search?fromUpdatedDate=D&toUpdatedDate=D&size=3&page=0` then `page=1`, back to back (pagination and early stop with `page_size=3`) |
| `euvd/euvd_search_last_page.json` | same query with the last page number (`ceil(total/3) - 1`): fewer than 3 items or exactly the tail (stop condition) |
| `euvd/euvd_no_score.json` | `GET /api/search?fromScore=0&toScore=0&fromUpdatedDate=D&toUpdatedDate=D&size=3` (`baseScore 0.0`, no `baseScoreVersion`) |
| `euvd/euvd_version_na.json` | a recorded page reduced with `jq '.items \|= map(select(any(.enisaIdProduct[]?; .product_version == "n/a" or .product_version == "-")))'` |
| `euvd/euvd_no_product.json` | same, selecting `.enisaIdProduct == []` or a product named `n/a` |
| `euvd/euvd_epss_zero.json` | same, selecting `.epss == 0` (can be merged with another case if one record shows both) |
| `euvd/euvd_kev_dump.json` | `GET /api/kev/dump` reduced with `jq '[.[] \| select(...)]'` to about 6 real entries: CISA-only, both lists, EU-only, one with `vulnerabilityName` |
| `cisa_kev/cisa_kev_catalog.json` | `GET .../known_exploited_vulnerabilities.json` reduced with `jq '.vulnerabilities \|= map(select(.cveID == "A" or ...))'` to about 4 real entries with both `Known` and `Unknown` |

- Keep the `total` field as returned (do not "fix" it): tests use a small `page_size` so the stop conditions do not depend on it, except the dedicated `total` stop test, which reads `total` from the fixture.
- **Malformed payloads are built in the test** from a recorded fixture (load the JSON, then delete `items`, swap in a string, truncate the bytes, reverse the item order, change one `dateUpdated` to garbage, drop `id`). Do not commit hand-written malformed files.
- The dev environment may sit behind a TLS-intercepting proxy: use your shell's CA settings for `curl` only. Never commit CA files, proxy settings or machine paths, and never weaken TLS in code or tests.

### Testing requirements

- **Layout:** `tests/unit/test_http.py`; `tests/unit/sources/{__init__,test_base,test_registry}.py`; `tests/contract/{__init__}.py`, `tests/contract/euvd/{__init__,test_euvd}.py`, `tests/contract/cisa_kev/{__init__,test_cisa_kev}.py`. Every new test directory gets an `__init__.py` (1.1 convention, avoids basename collisions).
- **Async tests** use the anyio plugin: `@pytest.mark.anyio` + `async def`; the `anyio_backend` fixture (asyncio) is already in `tests/conftest.py`. Not pytest-asyncio.
- **HTTP mocking:** respx (`respx_mock` fixture from the respx pytest plugin, or `@respx.mock`). The network stays blocked by pytest-socket; respx intercepts at the httpx transport, so no socket is opened.
- **Determinism:** `FakeClock` (`fake_clock` fixture); pass `sleep=` an async recorder that stores delays and returns immediately, and `random=lambda: 1.0` (or `0.5`) to `HttpClient`; pass `sleep=` a no-op to `EuvdAdapter`. No real sleeps, no real time.
- **Live variants:** in each contract module, one or two tests marked `@pytest.mark.live` and `@pytest.mark.enable_socket` (pytest-socket's marker, so the socket block is lifted only for them) that run the real adapter with a real `HttpClient` and `SystemClock` against the real API (EUVD: cursor = now − 1 day; CISA: full catalogue) and assert shape-level facts (records non-empty, `record_id` patterns, aware UTC `updated_at`, at least one EU KEV / one `Known` ransomware entry). They are deselected by default (`addopts` has `-m "not live"`). They may fail on a developer machine behind a proxy (`trust_env=False` ignores proxies by design): that is expected; do not add proxy support. `source-drift.yml` (story 2.2) will run them.
- **Required cases** (the reviewer checks them, see `epic-1-stack-plan.md` §3.4):
  - `http`: client built with `trust_env=False` (assert `client.trust_env is False` on the wrapped `httpx.AsyncClient`, or equivalent); User-Agent sent; 200 returns the bytes; 503 → 200 retried once with the jitter delay `random() * BACKOFF_BASE`; 429 with `Retry-After: 7` waits 7; `Retry-After` as an HTTP date (with `FakeClock`); `Retry-After: 120` → no wait, `HttpStatusError(429)`; `httpx.ReadTimeout` retried then success; timeouts exhausted → `HttpTransportError` after `MAX_ATTEMPTS` calls; backoff cap respected (`random=lambda: 1.0` gives 1, 2, 4 for the three waits with the defaults, and the cap holds with a small cap); 404 and 400 not retried (one call, `HttpStatusError`); `httpx.ConnectError` not retried (`HttpTransportError`); `Content-Length` above the limit → `HttpResponseTooLargeError` with the body unread; a streamed body above the limit without `Content-Length` → same; exactly the limit → accepted; proxy variables in the environment (`monkeypatch.setenv("HTTPS_PROXY", ...)`) are ignored.
  - `sources/base`: hierarchy (`SourceTransportError` etc. are `SourceError` and `CvestreamError`, names end in `Error`, `.source` kept); `get_json` maps too-large → payload, status/transport → transport, invalid JSON / invalid UTF-8 → payload, valid JSON → the object; `FetchResult` is frozen.
  - `registry`: both enabled → two `ScheduledSource`s in order `euvd`, `cisa_kev` with their settings' intervals (e.g. `PT30M`); a disabled source is absent; every `SourcesSettings` field has a factory; adapters' `name` equals the settings key.
  - EUVD contract: one fully asserted normal record (id, aliases including the EUVD ID + CVE + GHSA in order, `description`, `CvssScore(8.7, "4.0", vector)` or whatever the recorded record shows, `epss` fraction, every `AffectedProduct`, `published_at`/`updated_at` aware UTC with the exact values, `url`, `references`, `source == "euvd"`, `observed_at == fake_clock.now()`); no score → `scores == (Sourced(None, ...),)`; `n/a` and `-` versions → `version_text is None` and the product kept; no product / `n/a` product → no `AffectedProduct` and the record kept; EPSS `0` → `epss is None`; date parsing table (`AM`/`PM`, `12:xx AM` → 0 h, `12:xx PM` → 12 h, one-digit day, U+202F before `PM`, garbage → `None`); no cursor → request has `fromUpdatedDate` = (fake now − 14 days).date() and `page=0`, `size=100`; cursor → `fromUpdatedDate` = its UTC day; records older than the cursor dropped; `next_cursor` = newest `dateUpdated`; no new record → `next_cursor` = the input cursor; invalid cursor string → falls back to now − 14 days; pagination over page0 + page1 with `page_size=3` and early stop (assert the exact number of requests); stop on a short page; stop on `total`; duplicates across pages emitted once; EU KEV dump → only `eukev_kev` entries, `source == "eu_kev"`, `KevCatalog.EU_KEV`, `added_at` midnight UTC, aliases; malformed: search body not JSON → `SourcePayloadError`; `items` missing → `SourceContractError`; reversed item order → `SourceContractError`; one item with a garbage `dateUpdated` and one without `id` → `skipped == 2`, the others emitted; dump not a list or empty → `SourceContractError`; search 503 exhausted → `SourceTransportError` (and the dump records are not returned: no `FetchResult` at all).
  - CISA KEV contract: mapping of one entry (aliases, title, description, `KevEntry(CISA_KEV, True, added_at)` for a `Known` entry), `Unknown` → `known_ransomware_use is None`, `next_cursor == catalogVersion`, cursor argument ignored (same request with or without), entry without `cveID` or with a bad one → skipped and counted, `vulnerabilities` missing → `SourceContractError`, empty list → `SourceContractError`, invalid JSON → `SourcePayloadError`, too-large response (use a small `max_bytes` only if you expose it; otherwise test via `get_json` in `test_base.py`).
  - `domain/test_model.py`: the new `epss` field (Task 4).
- `tests/unit/domain/test_purity.py` must stay green unchanged (Task 4 adds no import to `domain`).

### Architecture compliance

- Library ownership: `httpx` only in `cvestream/http.py` and `cvestream/sources/*` (import-linter contract "httpx only in sources and http" already exists; Task 10 extends the others). Pydantic in adapters is fine (boundary models), never in `domain`.
- Error taxonomy: every new exception inherits from `CvestreamError` and ends in `Error`. No bare `except:`, no `except Exception` (adapters and `http.py` are not supervisors).
- Time: only the injected `Clock`; build aware datetimes with `tzinfo=UTC`; never `datetime.now()`, `utcnow()`, `strptime` into a naive datetime (ruff `DTZ`).
- Unknown is `None`; keep every `Sourced` value; `CvssScore.score` rounded to one decimal; `cvss_version` is a string; EPSS is a 0–1 fraction.
- Identity is never a URL: `record_id` is the EUVD ID or the CVE ID; URLs live only in `url` / `references`.
- `async` only in `http.py` and adapters' `fetch` (plus private async helpers they call). No `asyncio.run` anywhere in this story.
- Every public function fully annotated, `X | None` unions; `ty check` runs with `error-on-warning = true`. No `from __future__ import annotations`.
- Logs: `<area>.<past-participle>` names (`http.retried`, `source.record_skipped`, `source.cursor_invalid`), standard fields (`source`, `error_type`, `count`), never a token.
- Modules are singular nouns at the architecture-tree paths. No `utils.py` / `helpers.py`.

### Library / framework requirements

| Package | Version | Use |
|---|---|---|
| httpx | 0.28.1 (`>=0.28.1,<1`; latest stable on PyPI 2026-10-26; 1.0 exists only as `1.0.dev6`) | `AsyncClient(trust_env=False, verify=ssl.create_default_context(), ...)`, `client.stream("GET", ...)`, `response.aiter_bytes()`, `httpx.TimeoutException`, `httpx.TransportError`, `httpx.Timeout`. Passing an `SSLContext` to `verify` is the 0.28 way (string paths for `verify` are deprecated in 0.28). |
| respx | 0.23.1 (dev; latest, 2026-04-08; `httpx>=0.25`) | `respx_mock` fixture / `@respx.mock`, `respx.get(url, params=...)` / `params__contains=`, `side_effect=[httpx.Response(503), httpx.Response(200, ...)]` or `side_effect=httpx.ReadTimeout`, `route.call_count`, `route.calls.last.request`. |
| pydantic | 2.14.1 (already a dependency) | private payload models in adapters (`ConfigDict(extra="ignore")`, `Field(alias="dateUpdated")`, `ValidationError`). |
| anyio | 4.15.1 (already dev; httpx also pulls it at runtime) | pytest plugin for async tests. |
| pytest-socket | already dev | `@pytest.mark.enable_socket` on `live` tests only. |
| stdlib | Python 3.14 | `ssl`, `asyncio`, `random`, `json`, `re`, `email.utils.parsedate_to_datetime`, `datetime`, `dataclasses`, `typing.Protocol`/`Self` |

No other dependency (no `tenacity`, no `backoff`, no `python-dateutil`, no `certifi` usage, no `pytest-asyncio`, no `requests`).

### File structure requirements

New:

```
src/cvestream/http.py
src/cvestream/sources/base.py
src/cvestream/sources/euvd.py
src/cvestream/sources/cisa_kev.py
src/cvestream/sources/registry.py
docs/sources.md
tests/unit/test_http.py
tests/unit/sources/__init__.py
tests/unit/sources/test_base.py
tests/unit/sources/test_registry.py
tests/contract/__init__.py
tests/contract/euvd/__init__.py
tests/contract/euvd/test_euvd.py
tests/contract/cisa_kev/__init__.py
tests/contract/cisa_kev/test_cisa_kev.py
tests/fixtures/README.md
tests/fixtures/euvd/*.json
tests/fixtures/cisa_kev/*.json
```

Modified: `pyproject.toml` (deps + import-linter), `uv.lock`, `src/cvestream/domain/model.py` (one field), `tests/unit/domain/test_model.py`, `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md`, `CLAUDE.md`, `README.md`, this story file, `sprint-status.yaml`.

`src/cvestream/sources/__init__.py` keeps only its docstring (no re-exports). Do not create `cert_eu.py`, `certfr_alerte.py`, `generic_*.py` (epic 2).

### Existing files this story touches

- `pyproject.toml`: today runtime deps `packaging`, `pydantic`, `pydantic-settings[yaml]`, `structlog`, `typer`; dev deps `anyio`, `import-linter`, `pytest`, `pytest-socket`, `ruff`, `ty`, `watchfiles`; pytest `addopts = ["--disable-socket", "--allow-unix-socket", "-m", "not live", "--strict-markers"]` with markers `live` and `valkey`; import-linter: a `layers` contract with `(cvestream.http)` optional, a "domain is pure" contract (already forbids `httpx` and `cvestream.http` in domain), and one forbidden contract per library family. Change: `uv add` (Task 1) and Task 10 only. Preserve: every other `[tool.*]` setting, the `addopts`, the markers.
- `src/cvestream/domain/model.py`: `SourceRecord` ends with `url`, `published_at`, `updated_at` (all defaulted). Change: append `epss` after `updated_at`. Preserve: every existing field, order and default; `NormalizedRecord` and `Vulnerability` unchanged.
- `src/cvestream/settings.py`: read-only for this story. `SourcesSettings(euvd: SourceSettings, cisa_kev: SourceSettings)`, `SourceSettings(enabled: bool = True, interval: timedelta = 15 min, 0 < interval <= 60 min)`, `extra="forbid"`.
- `src/cvestream/clock.py`: `Clock` protocol (`now() -> datetime`), `SystemClock`. Reuse; do not add another clock.
- `src/cvestream/errors.py`: only `CvestreamError`. Do not move the source errors there (architecture puts them in `sources/base.py`).
- `src/cvestream/log.py`: structlog setup and token redaction; nothing to change. Use `structlog.get_logger()` in the new modules.
- `tests/conftest.py`: `FakeClock` (`now`, `advance`, `set`) and `fake_clock`, `anyio_backend` fixtures. Reuse; do not duplicate.
- `CLAUDE.md`, `README.md`, `architecture.md`, `project-context.md`: only the lines listed in Task 11.
- `.github/workflows/ci.yml`: **not touched.** It already triggers on every `pull_request` with no `branches:` filter (stacked PRs target story branches, not `main`) and pushes images only on push to `main`; keep it that way. CI runs `uv run pytest`, which deselects `live` tests.

### Delivery (stacked PR)

- Branch `story-1-4-euvd-and-kev-ingestion`, created from `story-1-2-vulnerability-model-and-matching-engine` in parallel with 1.3 (sprint wave 3). The draft PR's base is the 1.2 branch for now; per `epic-1-stack-plan.md`, it will be rebased onto `story-1-3-endpoint-configuration-in-yaml` to keep the stack linear. Expected conflicts then: `pyproject.toml` (dependency lists, import-linter), `uv.lock` (take 1.3's, then `uv lock`; never merge by hand), `CLAUDE.md`, `README.md`, `architecture.md`, `project-context.md`. Keeping `settings.py` and `cli/main.py` untouched avoids the other two conflicts listed in `sprint-status.yaml`.
- Commits separated by concern, never squashed, each ending with the attribution line, for example: `chore(story-1.4): add httpx and respx`; `feat(story-1.4): add outbound HTTP client`; `feat(story-1.4): add source adapter contract`; `feat(story-1.4): add EPSS to source records`; `feat(story-1.4): add EUVD adapter with EU KEV records`; `feat(story-1.4): add CISA KEV adapter`; `feat(story-1.4): add source registry`; `test(story-1.4): add recorded EUVD and CISA KEV fixtures`; `test(story-1.4): add adapter contract tests` (or tests alongside each feat commit); `chore(story-1.4): extend import-linter contracts to cvestream.http`; `docs(story-1.4): document source assumptions`; `chore(story-1.4): mark story ready for review`.

### Previous story intelligence (1.1, 1.2)

- Gate = `uv sync --locked`, `ruff check` (`E,F,W,I,UP,B,DTZ,BLE,S,RUF`), `ruff format --check`, `ty check` (`error-on-warning`), `lint-imports`, `pytest` (sockets blocked, `--strict-markers`). `assert` only in `tests/**` (`S101` ignored there). Ruff excludes `_bmad*`, `.agents`, `.claude`.
- 1.2 domain API to reuse, with exact names: `Sourced[T](value, source, observed_at)`, `CvssScore(score, version=None, vector=None)`, `AffectedProduct(product, vendor=None, version_text=None)`, `KevCatalog.CISA_KEV` / `.EU_KEV` (values `"cisa_kev"`, `"eu_kev"`), `KevEntry(catalog, known_ransomware_use=None, added_at=None)`, `SourceRecord(source, record_id, observed_at, aliases=(), title=None, description=None, scores=(), affected=(), kev=(), references=(), url=None, published_at=None, updated_at=None)`. All frozen, slotted, tuples only (never lists) so records stay hashable: build `tuple(...)` in the adapters.
- 1.2's `identity.normalize_alias` / `primary_id` and `version.parse_range` are **not** called by the adapters (normalization is 1.5's `pipeline/normalize.py`; parsing happens in the matcher). Do not duplicate them; the adapter's ID regexes only decide whether a record is usable.
- 1.2 learnings: `ty` was strict about narrowing in tests (`hasattr` + assignment failed; `setattr` through a variable worked). Test modules declare their own type aliases rather than importing from `conftest.py`. Prefer absolute imports (`from cvestream.domain.model import SourceRecord`).
- 1.2 review deferred: CVE 5 placeholder lower bounds. Checked here (none in 1,100 live records): stays deferred, no `version.py` change.
- 1.1 review deferred: tokens hidden in non-string log values. Not affected (no tokens in this story's logs).
- `structlog.testing.capture_logs()` is the simplest way to assert log events in tests if needed.

### Git intelligence

Recent commits on the base branch: `chore(story-1.2): record code review`, `chore(story-1.2): mark story ready for review`, `docs(story-1.2): document version parsing rule and matcher signature`, `feat(story-1.2): add vulnerability matcher`, `feat(story-1.2): add endpoint rules and product entries`. Pattern: Conventional Commits scoped `(story-1.x)`, one concern per commit, dependency changes in a `chore(...)` commit with `pyproject.toml` + `uv.lock`, docs in `docs(...)`, story-file bookkeeping in `chore(...)`.

### Latest technical information (checked 2026-10-26)

- httpx 0.28.1 is still the latest stable release; `1.0.dev6` (2026-08-31) is a pre-release: uv will not pick it with `<1`. httpx defaults to `certifi`; the architecture requires the system store, hence `verify=ssl.create_default_context()`.
- respx 0.23.1 (2026-04-08) supports httpx 0.28.
- EUVD and CISA KEV facts: see "Live API facts". The EUVD API is unversioned and already broke older clients (`/api/vulnerabilities` now 404): the order check, top-level contract checks and `live` tests are the drift alarms.

### Project Structure Notes

- Aligned with the architecture tree: `http.py`, `sources/base.py`, `registry.py`, `euvd.py`, `cisa_kev.py`, `tests/contract/<source>/`, `tests/fixtures/<source>/`, `docs/sources.md` are all listed there.
- Variances and decisions a reviewer may want to revisit (each is amended in `architecture.md` / documented in `docs/sources.md` in this PR):
  - **`eu_kev` record source** emitted by the `euvd` adapter (new identifier, Task 11.2). Alternative: a separate `eu_kev` adapter with its own settings entry (rejected: more settings, and the epic places EU KEV in the `euvd` adapter).
  - **Search records carry no KEV entry**; `exploitedSince` is ignored. EU KEV flags come from `eu_kev` records, CISA flags from `cisa_kev` records; 1.5 merges them by aliases.
  - **No `affected` products from CISA KEV** (precision of `min_version` entries); description and title are kept.
  - **`Cursor` is an opaque string**; EUVD's is the newest `dateUpdated` seen (source time), the KEV adapters' is informational.
  - **Response limit 16 MiB**, retries 4 attempts, `Retry-After` honoured up to 60 s: constants, not settings.
  - **EPSS `0` is unknown** (EUVD shows 0 before the first EPSS score).
- `tests/fixtures/README.md` is new (fixture provenance); the architecture tree does not list it, but it supports the "recorded, never hand-written" rule.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4: EUVD and KEV ingestion]
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-1, FR-3, FR-4, FR-9, NFR-5)
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/addendum.md#EUVD] (endpoints, record fields, data quality, exploited signal)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] (Outbound: `trust_env=False`, system trust store, timeouts, size limits)
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns] (First run, Source adapters, Error taxonomy)
- [Source: _bmad-output/planning-artifacts/architecture.md#Process Patterns] (Errors, Retries, Async and sync, Testability)
- [Source: _bmad-output/planning-artifacts/architecture.md#Format Patterns] (unknown = `None`, Provenance, Time, Numbers)
- [Source: _bmad-output/planning-artifacts/architecture.md#Structure Patterns] and [#Complete Project Directory Structure] (tests layout, fixtures, markers, module paths)
- [Source: _bmad-output/project-context.md] (Testing Rules, Sources, HTTP, Anti-patterns)
- [Source: _bmad-output/implementation-artifacts/epic-1-stack-plan.md#3.4 Story 1.4] (manual checks the reviewer runs) and §1 (rebase onto 1.3)
- [Source: _bmad-output/implementation-artifacts/1-2-vulnerability-model-and-matching-engine.md] (domain model, deferred version shapes)
- [Source: _bmad-output/implementation-artifacts/1-1-project-skeleton-container-and-ci.md] (tooling, CI, test conventions)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created.

### File List
