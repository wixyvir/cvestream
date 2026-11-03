---
baseline_commit: 0c1903dd54c6e23c5e0a7612f6f5ee4a8a7de0a6
---

# Story 2.1: CERT-EU adapter

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a channel subscriber,
I want CERT-EU advisories in my feed, merged with the EUVD record of each CVE they cover,
so that urgent European advisories (like PHP2Shell) reach my channel before anyone posts them by hand.

Scope set by the 2026-11-01 course correction (`_bmad-output/planning-artifacts/sprint-change-proposal-2026-11-01-epic-2-scope.md`): CERT-EU only. CERT-FR alerte (FR-2b) and configured generic sources (FR-5) are deferred after v1. In the PHP2Shell replay, CERT-EU 2026-018 was about 7.5 h ahead of EUVD (SM-3).

## TL;DR

- **New adapter `src/cvestream/sources/cert_eu.py`** (`NAME = "cert_eu"`): it reads the advisories RSS (latest 10), then fetches the JSON of each advisory whose RSS `pubDate` is newer than the cursor. It emits **one `SourceRecord` per CVE ID** found in `content_markdown`: `record_id` `<serial>:<CVE>`, aliases `(<CVE>, CERT-EU-SA<serial>)`. An advisory without CVE emits **one** record `CERT-EU-SA<serial>` whose only alias is itself.
- **Live payload facts drive the design** (verified 2026-11-03, see "Live API facts"). The RSS `pubDate` is written with **`CEST`/`CET`**, which `email.utils` does not understand (it returns a naive datetime): map them to `+0200`/`+0100`. The JSON `publish_date` is UTC. The "Affected Products" section is often prose, and its bullets often omit the product name (GitLab: `- 19.4 before 19.4.3`), so a bullets-only parser would miss them.
- **XML safety with the stdlib only:** `xml.etree.ElementTree.XMLParser` with a `TreeBuilder` whose `doctype()` raises. A document with a DOCTYPE is refused, so no entity can be declared or expanded. No new dependency (no `defusedxml`, no `feedparser`). `ruff` S314 is silenced on that one line, with the reason written next to it.
- **Two small changes outside the adapter:**
  - `settings.py` gets `sources.cert_eu` (enabled by default, 15 min) and `registry.py` gets its factory;
  - `pipeline/render.py` no longer names the title's product from CERT-EU prose: it falls back to the entry name. `cert_eu` is added to `_SOURCE_ORDER`.
- **Tests:**
  - recorded fixtures in `tests/fixtures/cert_eu/`: the RSS, 2026-018 (3 CVEs, revised v1.2), 2026-019 (single CVE, GitLab), 2025-029 (no CVE) and 2025-008 (one product block per CVE);
  - one more EUVD fixture for the second PHP2Shell CVE;
  - contract tests with `live` variants;
  - a PHP2Shell pipeline replay in `tests/integration/test_cert_eu_php2shell.py`.
- **Docs in the same PR:** `docs/sources.md` (new CERT-EU section), `docs/configuration.md`, `settings.example.yaml`, `README.md`, `tests/fixtures/README.md`, `architecture.md` (title rule) and `project-context.md` (XML parser line).
- **No** migration, no `config/models.py` change (so no schema regeneration), no change to `domain/`, `pipeline/correlate.py` or `pipeline/run.py`.

## Acceptance Criteria

1. **Per-CVE records (FR-2, FR-7, FR-8).** For each advisory it fetches, the `cert_eu` adapter emits one `SourceRecord` per distinct CVE ID found in the JSON `content_markdown`:
   - the CVE IDs are matched case-insensitively, upper-cased and de-duplicated, in order of first appearance;
   - `record_id` is `<serial>:<CVE>` (e.g. `2026-018:CVE-2026-109618`);
   - `aliases` is exactly `(<CVE>, "CERT-EU-SA<serial>")`.

   A multi-CVE advisory therefore never merges its vulnerabilities: 2026-018 gives 3 records and, through the pipeline, 3 vulnerabilities. An advisory without any CVE ID emits one record with `record_id` `CERT-EU-SA<serial>` and `aliases == ("CERT-EU-SA<serial>",)`, so its primary ID is `CERT-EU-SA<serial>`.
2. **Field mapping.**
   - **Description:** the JSON `description`, with `<br>` turned into line breaks and placeholders handled by `clean_text`.
   - **Title:** the JSON `title`.
   - **URL:** the advisory page, `https://cert.europa.eu/publications/security-advisories/<serial>/`. The URL is never part of the identity.
   - **References:** the `<https://…>` links of the `# References` section.
   - **Scores:** `scores == ()`. CERT-EU has no score field, so the score is unknown and never `0`. A CVSS value written in the prose is ignored.
   - **KEV:** `kev == ()`.
3. **Affected products.**
   - The section is the level-1 heading `Affected Products` or `Products Affected` (any case), up to the next level-1 heading. It becomes `AffectedProduct(product=<text>, vendor=None, version_text=None)` entries, following the block rule in Dev Notes:
     - a prose paragraph followed by a bullet list is one entry, `"<paragraph>: <bullet>; <bullet>"`;
     - a bullet list without a lead-in paragraph gives one entry per top-level bullet, with its nested bullets inline;
     - a paragraph without a list is one entry;
     - fenced code blocks are dropped.
   - Markdown emphasis, `[New]` markers and `[n]` reference markers are removed, and whitespace is collapsed (non-breaking spaces included).
   - An entry that mentions CVE IDs goes only to those CVEs' records. An entry that mentions none goes to every record of the advisory.
   - Versions are unknown: `domain/version.py` is not called and not changed.
   - An advisory without the section gives `affected == ()`.
4. **XML safety.** The RSS is parsed with the standard library and **no entity resolution**: any document with a DOCTYPE is refused with `SourcePayloadError`, so neither an external entity (XXE) nor an entity bomb ("billion laughs") is expanded. A body that is not well-formed XML is a `SourcePayloadError` too. No new dependency is added.
5. **Revisions and cursor.**
   - The cursor is the newest RSS `pubDate` seen, as ISO 8601 UTC (`2026-10-23T14:36:26Z`). It comes from source data and is capped at the fetch time.
   - Only items with `pubDate` strictly newer than the cursor have their JSON fetched. An unchanged `pubDate` triggers no JSON request.
   - A revised advisory (new `pubDate`) is fetched again, so a CVE added by a revision gets its own new record (CVE-2026-115560 in 2026-018 v1.2). The existing records keep their `record_id`, so a revision alone never re-publishes (FR-19).
   - With no cursor, the start is now − 14 days. A cursor that is invalid or in the future falls back to that start with `source.cursor_invalid`. A cursor older than that is clamped to it with `source.cursor_clamped`.
6. **Dates.**
   - `published_at` is the front-matter `original_date` (date only) as midnight UTC, or `None` when it is missing.
   - `updated_at` is the JSON `publish_date` (`DD-MM-YYYY HH:MM:SS`) read as UTC: `23-10-2026 14:36:26` gives `2026-10-23T14:36:26+00:00`, not 16:36. When `publish_date` cannot be read, the RSS `pubDate` converted to UTC is used.
   - An RSS `pubDate` in `CEST`/`CET` is converted with its real offset (`Fri, 23 Oct 2026 16:36:26 CEST` gives `14:36:26Z`). An RSS item whose date cannot be read becomes skipped.
7. **Failures (NFR-5).**
   - These raise a `SourceError` subclass, return nothing and leave the cursor unchanged:
     - RSS transport error, or a transport error on any advisory JSON: `SourceTransportError`;
     - RSS not XML, DTD present, or too large: `SourcePayloadError`;
     - RSS without `channel` or without `item`, or every item unusable: `SourceContractError`;
     - every fetched advisory unusable: `SourceContractError`.
   - An advisory whose JSON is not JSON, has an unexpected shape, or whose `serial_number` does not match the RSS serial is **skipped and counted**. The other advisories are still emitted, and the cursor stays below that advisory's `pubDate`, so the next fetch retries it.
8. **Registry and settings (FR-4, NFR-1).**
   - `SourcesSettings.cert_eu: SourceSettings` (enabled, 15 min by default) and a `cert_eu` factory in `ADAPTER_FACTORIES`.
   - `CVESTREAM_SOURCES__CERT_EU__INTERVAL=PT61M` is rejected (above 60 minutes).
   - `settings.example.yaml` and `docs/configuration.md` document the block and its `CVESTREAM_SOURCES__CERT_EU__*` variables.
9. **Fixtures and contract tests.**
   - **Recorded fixtures** in `tests/fixtures/cert_eu/`, captured from the live site and never hand-written. The provenance goes in `tests/fixtures/README.md`. The required cases are:
     - the RSS;
     - the multi-CVE revised advisory 2026-018;
     - a single-CVE advisory (2026-019);
     - an advisory without CVE (2025-029);
     - an advisory with per-CVE product blocks (2025-008).
   - **Malformed payloads** are derived in the tests from those files, as the fixtures README requires.
   - **Contract tests** in `tests/contract/cert_eu/` cover every case of AC 1 to 7.
   - **`live` variants** (deselected by default): the RSS shape, then 2026-018, 2026-019 and 2025-029 fetched by serial.
10. **PHP2Shell pipeline replay** (`tests/integration/test_cert_eu_php2shell.py`), with a `php` endpoint:
    - the CERT-EU records publish one `new` item per CVE (3 items), each with band `unknown`;
    - the later EUVD records for CVE-2026-109618 and CVE-2026-109619 merge into those vulnerabilities and publish one `[UPDATE]` each (`band-critical`). CVE-2026-115560 has no EUVD record and publishes nothing more;
    - the three CVEs stay three vulnerabilities, and nothing logs `publish.skipped`;
    - a second run with the same advisory revision publishes nothing (FR-19).
11. **Title product.** An item's title never names a product from CERT-EU affected-product text, which is prose. When only CERT-EU text matches, the title uses the matched entry's name (the existing fallback). Once EUVD data names the product, the title names it. The item body still lists the CERT-EU affected products.
12. **Documentation in the same PR.**
    - `docs/sources.md` gets a CERT-EU section: endpoints, RSS and JSON shapes, UTC and `CEST` dates, one record per CVE, the advisory-only record, revisions and cursor, the affected-products block rule and attribution, and the assumed risks.
    - `README.md` lists CERT-EU as a source.
    - `tests/fixtures/README.md` gives the capture commands.
    - `architecture.md` gets the title rule (AC 11), and `project-context.md` the XML parser line.
    - The existing gate stays green: 1090 tests passed at baseline `0c1903d`, and every existing test passes, apart from the registry and settings tests that list the sources, which are updated.

## Tasks / Subtasks

- [x] **Task 1: record the fixtures** (AC: 9)
  - [x] 1.1 Create `tests/fixtures/cert_eu/` and record, with the commands in "Fixture capture" below:
    - `cert_eu_rss.xml` (bytes as served, no reformatting);
    - `cert_eu_2026_018_multi_cve_revised.json`;
    - `cert_eu_2026_019_single_cve.json`;
    - `cert_eu_2025_029_no_cve.json`;
    - `cert_eu_2025_008_cve_blocks.json`.
  - [x] 1.2 Record `tests/fixtures/euvd/euvd_php2shell_second_cve.json` (EUVD-2026-107265, CVE-2026-109619) for the replay.
  - [x] 1.3 Add the `cert_eu/` table and the new EUVD row to `tests/fixtures/README.md`, with the recording date and the commands.
- [x] **Task 2: share the body fetch in `sources/base.py`** (AC: 4, 7)
  - [x] 2.1 Extract `async def get_bytes(http, source, url, *, params=None, max_bytes=DEFAULT_MAX_BYTES) -> bytes`. It maps `HttpResponseTooLargeError` to `SourcePayloadError` and every other `HttpError` to `SourceTransportError`. `get_json` then calls it and keeps its exact behaviour and messages. The existing `tests/unit/sources/test_base.py` must pass unchanged; add a test for `get_bytes`.
- [x] **Task 3: the adapter `src/cvestream/sources/cert_eu.py`** (AC: 1 to 7)
  - [x] 3.1 Constants: `NAME`, `RSS_URL`, `ADVISORY_URL`, `JSON_URL`, `INITIAL_WINDOW = timedelta(days=14)`, `MAX_RSS_BYTES = 1 MiB`, `MAX_ADVISORY_BYTES = 2 MiB`, and the patterns.
  - [x] 3.2 Safe RSS parsing: `_NoDoctypeBuilder(ET.TreeBuilder)`, whose `doctype()` raises a private `_DoctypeRefusedError(CvestreamError)`. `ET.ParseError` and `_DoctypeRefusedError` both become `SourcePayloadError`.
  - [x] 3.3 RSS items: the serial comes from the stripped `<link>` path (`/security-advisories/<serial>/`), with the title prefix `<serial>:` as fallback. `pubDate` goes through `_parse_pub_date`, which handles `CEST`/`CET`. Duplicates are removed by serial, keeping the newest `pubDate`.
  - [x] 3.4 Payload model `_Advisory` (pydantic, `extra="ignore"`): `serial_number: str`, `title: str | None`, `publish_date: str | None`, `description: str | None`, `content_markdown: str`.
  - [x] 3.5 Helpers:
    - `_cve_ids(markdown)`;
    - `_original_date(markdown)` (front matter; the first line can be `---` followed by spaces);
    - `_parse_publish_date(text)`;
    - `_references(markdown)`;
    - `_affected_entries(markdown) -> list[tuple[str, frozenset[str]]]` (text and the CVEs it names);
    - `_clean_markdown(text)`;
    - `_description(text)`.
  - [x] 3.6 `_records(advisory, serial, pub_date, observed_at) -> tuple[SourceRecord, ...]`: per-CVE records, or the advisory-only record, with the attribution rule.
  - [x] 3.7 `CertEuAdapter.fetch(cursor)`, in this order:
    1. start = cursor, floor or clamp, with the same rules and logs as `EuvdAdapter._start`;
    2. fetch and parse the RSS;
    3. select the items due (`pubDate > start`);
    4. fetch their JSON one by one, in RSS order;
    5. skip per advisory, with hold-back;
    6. raise the contract errors;
    7. compute `next_cursor`.

    Log `source.record_skipped` at debug, with `record_id=<serial>`.
- [x] **Task 4: settings and registry** (AC: 8)
  - [x] 4.1 `settings.py`: `cert_eu: SourceSettings = Field(default_factory=SourceSettings)` after `cisa_kev`.
  - [x] 4.2 `registry.py`: `"cert_eu": lambda http, clock, _settings: CertEuAdapter(http, clock)`.
  - [x] 4.3 Update `tests/unit/sources/test_registry.py` (source lists now `["euvd", "cisa_kev", "cert_eu"]`, `isinstance` checks) and `tests/unit/test_settings.py`:
    - `cert_eu` defaults;
    - `CVESTREAM_SOURCES__CERT_EU__INTERVAL=PT30M` is read;
    - `PT61M` is rejected;
    - `CVESTREAM_SOURCES__CERT_EU__ENABLED=false` disables it.
- [x] **Task 5: render title rule** (AC: 11)
  - [x] 5.1 `pipeline/render.py`:
    - `_title_product` builds its hits only from affected products whose `source` is not in a new `_PROSE_SOURCES = frozenset({"cert_eu"})` (filter `entry.vulnerability.affected` on `sourced.source` before taking the values);
    - add `"cert_eu": 3` to `_SOURCE_ORDER`;
    - `_affected` (the body) is unchanged.
  - [x] 5.2 Unit tests in `tests/unit/pipeline/test_render.py`:
    - a CERT-EU-only vulnerability gives a title with the entry name;
    - CERT-EU plus EUVD gives a title with the EUVD product;
    - the body still lists the CERT-EU text.
- [x] **Task 6: contract tests `tests/contract/cert_eu/test_cert_eu.py`** (+ `__init__.py`) (AC: 1 to 7, 9)
  - [x] 6.1 Write every case listed in "Contract test plan" below, with respx and the anyio plugin, network blocked.
  - [x] 6.2 Write the `live` variants (`@pytest.mark.live`, `@pytest.mark.enable_socket`, `@pytest.mark.anyio`): RSS shape, 2026-018, 2026-019 and 2025-029 by serial.
- [x] **Task 7: PHP2Shell replay `tests/integration/test_cert_eu_php2shell.py`** (AC: 10)
  - [x] 7.1 Run the real adapters on the fixtures through `pipeline.run.cycle` (or `run`), on a SQLite `State`, as `test_pipeline_end_to_end.py` does.
  - [x] 7.2 Assert:
    - 3 `new` decisions, band `unknown`, with GUIDs `cvestream:team-php:CVE-2026-109618:new`, and so on;
    - then 2 `band-critical` decisions;
    - 3 distinct vulnerabilities in the `vulnerability` table;
    - no `publish.skipped` (structlog `capture_logs`);
    - a rerun publishes nothing.
- [x] **Task 8: documentation** (AC: 12)
  - [x] 8.1 `docs/sources.md`: add a `## CERT-EU (cert_eu)` section; extend the "Common behaviour" error taxonomy with XML (`SourcePayloadError` also covers non-XML bodies and a DOCTYPE).
  - [x] 8.2 `docs/configuration.md`: list `cert_eu` in the sources table.
  - [x] 8.3 `settings.example.yaml`: add a `cert_eu` block, and change the "Polling settings per source" comment to `euvd, cisa_kev, cert_eu`.
  - [x] 8.4 `README.md`: CERT-EU is a source now (lines 3–4 and the status paragraph).
  - [x] 8.5 `architecture.md`, "Feed cache" rules (story 1.6 paragraph): the title names the affected product "from structured product data only; CERT-EU affected-product text is prose and never names the title (story 2.1)". `project-context.md`, tech stack: replace `feedparser <to pin>` with `XML in: stdlib xml.etree.ElementTree, DOCTYPE refused (story 2.1)`.
  - [x] 8.6 Module docstring of `cert_eu.py`: summarise the assumptions and point to `docs/sources.md`.
- [x] **Task 9: gates and story closure**
  - [x] 9.1 Run every gate command of `CLAUDE.md`. Report `docker build` honestly: in proxy mode it failed locally in stories 1.7 and 1.8 (`x509` when fetching the ghcr.io token). Never disable verification; CI runs that gate.
  - [x] 9.2 Fill in the Dev Agent Record, set the status to `review`, and set `2-1-cert-eu-adapter: review` in `sprint-status.yaml`.

### Review Findings

Code review 2026-11-03 (`bmad-code-review`, claude-opus-5-5, autonomous run) of `git diff origin/main...HEAD` on PR wixyvir/cvestream#20. The session had no subagent tool, so the three layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) ran one after the other in the same session, each with its own inputs; edge cases were probed with scratch scripts against the real adapter. Result: 2 decision-needed, 3 patch (applied), 4 defer, 7 dismissed. The story stays in `review` until the two decisions are taken.

- [ ] [Review][Decision] A missing advisory JSON blocks the whole source for up to 14 days — a final `4xx` on one advisory JSON is a `SourceTransportError` (AC 7), so the fetch fails, the cursor does not move, and no later advisory is fetched until the cursor falls behind the 14-day floor or the item leaves the RSS. Options: keep; skip-and-hold-back on a final `4xx` only (recommended); skip on any advisory transport error. Inline comment: https://github.com/wixyvir/cvestream/pull/20#discussion_r4458527147 [src/cvestream/sources/cert_eu.py:425]
- [ ] [Review][Decision] A commentary paragraph after the bullet list becomes a structured affected product — in 2026-018 the "Many commercial and open-source products use PHP ... NCSC-NL GitHub repository" entry gives a `github` endpoint a structured hit on the three PHP2Shell CVEs (checked with `affected_hits`). Options: keep (documented risk); drop a paragraph that is neither a lead-in nor in a list-free section (recommended); keep such text for display only. Inline comment: https://github.com/wixyvir/cvestream/pull/20#discussion_r4458527159 [src/cvestream/sources/cert_eu.py:328]
- [x] [Review][Patch] The affected-products heading with emphasis or a trailing colon (`# **Affected Products**`, `# Affected Products:`) hid the whole section [src/cvestream/sources/cert_eu.py:232] — fixed in `3529419`, parametrized contract test, `docs/sources.md` updated
- [x] [Review][Patch] `+` was not a bullet marker, so a `+` list collapsed into one paragraph [src/cvestream/sources/cert_eu.py:68] — fixed in `7d043b5`, contract test
- [x] [Review][Patch] README status still said epic 1 was in progress while this branch marks it done [README.md:9] — fixed in `884ac13`
- [x] [Review][Defer] Ordered lists and single-marker emphasis (`*Foo*`, `_Foo_`) stay raw [src/cvestream/sources/cert_eu.py:244] — deferred, not seen in the 59 advisories checked
- [x] [Review][Defer] A lead-in written right under a bullet, without a blank line, continues that bullet, so two per-CVE blocks can merge [src/cvestream/sources/cert_eu.py:298] — deferred, CommonMark-consistent and not seen in the recorded data
- [x] [Review][Defer] A CVE removed by a revision keeps its stale record and merge [src/cvestream/sources/cert_eu.py:360] — deferred, no retraction path for any source yet
- [x] [Review][Defer] An unclosed code fence drops the rest of the advisory, references included [src/cvestream/sources/cert_eu.py:222] — deferred, not seen in the recorded data

Dismissed (7):

- Billion-laughs and XXE: verified that the `doctype()` guard refuses the document in about 5 ms; expat's own amplification limit is a second line.
- CVE attribution reads the cleaned entry text, not the raw text (AC 3 wording): cleaning removes only `**`, backticks, `[New]` and `[n]` markers, so the CVE set is the same.
- `error_type="_InvalidRssItem"` in the RSS skip log names no real class: a label only, debug level.
- An unusable RSS item is counted in `skipped` at every poll while it stays in the RSS: specified (AC 6), bounded by 10 items.
- A future RSS `pubDate` is fetched again at every poll until the clock passes it: the cursor is capped at the fetch time as specified, and the unchanged content hash touches nothing.
- Strict `>` on second-resolution `pubDate`s misses a second advisory published in the same second as the cursor: theoretical at about two advisories a month.
- `RFC 2822` `-0000` and zone-less dates are refused (naive): intended, documented ("any other zone is unusable").

## Dev Notes

### Scope boundaries (read first)

- **Files changed in `src/`:**
  - `sources/cert_eu.py` (new);
  - `sources/base.py` (`get_bytes`, behaviour unchanged);
  - `sources/registry.py`;
  - `settings.py` (one field);
  - `pipeline/render.py` (title filter and `_SOURCE_ORDER` only).
- **Not in this story:**
  - `source-drift.yml` and `docs/operations.md`: story 2.2, developed in parallel on `story-2-2-source-drift-detection`. It will run the `live` tests written here through the `live` marker. Do not add a workflow.
  - CERT-FR alerte, generic sources, any CLI command.
  - Changes to `domain/` (no `version.py` support for "versions prior to X"), `pipeline/correlate.py`, `pipeline/run.py`, `state/`.
  - `config/models.py`, so no schema regeneration.
- **No new dependency.** `pyproject.toml` and `uv.lock` stay unchanged. Check it with `git diff --stat main -- pyproject.toml uv.lock`.
- **Parallel story 2.2:** it touches `docs/operations.md`, `.github/workflows/`, its own story file and the `sprint-status.yaml` lines. Stay out of those, apart from your own status line, to keep its rebase trivial.

### Live API facts (verified 2026-11-03 through the proxy, drive the design)

- **RSS** `GET https://cert.europa.eu/publications/security-advisories-rss`:
  - `200 text/xml; charset=utf-8`, about 10 KB, `ttl` 1440;
  - **no XML declaration**, RSS 2.0 `<rss version="2.0"><channel>…<item>…`, exactly 10 items, newest `pubDate` first. Do not rely on the order;
  - the client's `Accept: application/json` header returns the same body, checked byte for byte, so no header change is needed in `cvestream.http`.
  - Item:
    ```xml
    <item>
        <title>2026-018: Critical Vulnerabilities in PHP</title>
        <link>
            https://cert.europa.eu/publications/security-advisories/2026-018/
        </link>
        <description>On 20 October 2026, … (HTML-escaped, with &lt;br&gt;)</description>
        <pubDate>Fri, 23 Oct 2026 16:36:26 CEST</pubDate>
        <guid>security-advisories-10955</guid>
    </item>
    ```
  - `<link>` carries newlines and indentation: strip it.
  - `<guid>` is an internal counter, not the serial: **never use it**.
  - `pubDate` uses the zone abbreviations **`CEST`** (summer) and `CET` (winter). `email.utils.parsedate_to_datetime("… CEST")` returns a **naive** datetime on Python 3.14.4, which would be a silent 2-hour error. Replace a trailing ` CEST` with ` +0200` and ` CET` with ` +0100` before parsing. Keep `GMT`/`UT`/`Z`/numeric offsets as they are. A result that is still naive means unusable.
  - The revised 2026-018 shows `pubDate` 16:36:26 CEST, and its JSON shows `publish_date` `23-10-2026 14:36:26`: the JSON is UTC, the RSS is Paris time. Both give the last revision.
- **Advisory JSON** `GET https://cert.europa.eu/publications/security-advisories/<serial>/json` (`200 application/json`, 4–19 KB; an unknown serial gives `404 text/html`):
  - Keys: `file_item`, `title`, `serial_number` (`"2026-018"`, always `\d{4}-\d{3}`, equal to `url_title`), `publish_date` (`"23-10-2026 14:36:26"`, `DD-MM-YYYY HH:MM:SS`, UTC), `description` (plain text whose only markup is `<br>`, 127 occurrences in 59 advisories, no entity), `url_title`, `content_markdown`, `content_html`, `licence`.
  - `content_markdown` starts with a YAML front matter whose first line is `---    ` (trailing spaces):
    ```
    ---
    title: 'Critical Vulnerabilities in PHP'
    number: '2026-018'
    version: '1.2'
    original_date: '2026-10-20'
    date: '2026-10-21'
    ---
    ```
    All 59 advisories checked (2025-001 to 2025-040, 2026-001 to 2026-019) have the five keys. Use regexes, not a YAML parser (only `original_date` is needed). `date` is not `publish_date`: ignore it.
  - Headings: `# Affected Products` (51), `# Products Affected` (7), `# Affected products` (1). The section is missing in some advisories (2025-004, 2025-005, 2025-007, 2025-013, 2025-028, 2025-030), so they give `affected == ()`. 19 of 59 sections have **no bullet at all** (prose only, e.g. 2026-004: "The vulnerability affects Microsoft SharePoint Server Subscription Edition, …").
  - **Bullets often omit the product name**, which their lead-in paragraph names:
    - 2026-019: "The vulnerability **CVE-2026-110264** affects the following versions of GitLab Community and Enterprise Editions [1]:", then `- 19.4 before 19.4.3`;
    - 2026-016: "self-managed **GitLab CE/EE**", then `- before 19.4.2`;
    - 2026-017: "Cisco Secure Email Gateway", then `- versions 16.0`.

    A bullets-only parser would give a GitLab advisory affected products without "GitLab". Structured data decides in the matcher (`affected` non-empty), so a `gitlab` endpoint would **not match**, which goes against FR-25 and the epic goal.
  - Per-CVE blocks exist. In 2025-008, "The vulnerability **CVE-2023-48790** affects the following products and versions [2]:" is followed by FortiNDR bullets, then CVE-2024-45324 by FortiOS bullets, then CVE-2023-40723 by FortiSIEM bullets. In 2025-003, each top-level bullet names its CVE.
  - CVE IDs also appear in lower case in reference URLs (`cve-2025-24813`). Some CVEs appear only in the References section (2026-004), and older CVEs can be mentioned for context (2025-003 names CVE-2023-37936, an affected FortiSwitch CVE). The AC takes every CVE ID in `content_markdown`.
  - **2025-029** ("Possible Zero-Day Vulnerability in SonicWall Products") is the only advisory without any CVE ID among the 59 checked. It has a `# Products Affected` prose paragraph and no bullet.
  - 2026-018 v1.2 (`CERT-EU-SA2026-018`) lists CVE-2026-109618, CVE-2026-109619 and CVE-2026-115560. The last one was added by v1.2 on 23/10 (`_History:_` list). Its Affected Products are 5 bullets ("PHP 8.5 versions prior to 8.5.12" … "End-of-life branches (PHP 8.1 and older), which no longer receive updates from the PHP project [4]"), followed by a paragraph "Many commercial and open-source products use PHP …".
  - Text contains non-breaking spaces (`Vulnerability in\xa0SonicWall\xa0Products`): `" ".join(text.split())` collapses them.
- **EUVD for the replay:**
  - EUVD-2026-107260 (CVE-2026-109618) is already recorded in `tests/fixtures/euvd/euvd_php_branch_wildcard.json` (`dateUpdated` `Oct 30, 2026, 6:31:35 PM`, `baseScore` 9.2).
  - EUVD-2026-107265 (CVE-2026-109619) is live (`dateUpdated` `Oct 30, 2026, 6:31:36 PM`, 9.2): record it.
  - **CVE-2026-115560 has no EUVD record.** `/search?text=CVE-2026-115560` returns EUVD-2026-115560, an unrelated record that matches the number. Do not record it.

### Adapter design (prescriptive)

```python
NAME = "cert_eu"
BASE = "https://cert.europa.eu/publications/security-advisories"
RSS_URL = "https://cert.europa.eu/publications/security-advisories-rss"
ADVISORY_URL = BASE + "/{serial}/"        # record.url, display only
JSON_URL = BASE + "/{serial}/json"
ADVISORY_ALIAS = "CERT-EU-SA{serial}"
INITIAL_WINDOW = timedelta(days=14)
MAX_RSS_BYTES = 1024 * 1024               # RSS is ~10 KB
MAX_ADVISORY_BYTES = 2 * 1024 * 1024      # an advisory is 4-19 KB

_SERIAL = re.compile(r"\d{4}-\d{3,}")
_LINK_SERIAL = re.compile(r"/security-advisories/(?P<serial>\d{4}-\d{3,})/?$")
_TITLE_SERIAL = re.compile(r"(?P<serial>\d{4}-\d{3,}):")
_CVE = re.compile(r"(?<![\w-])CVE-\d{4}-\d{4,}(?![\w-])", re.IGNORECASE)
_PUBLISH_DATE = re.compile(r"(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2}):(\d{2})")
_CURSOR_FORMAT = "%Y-%m-%dT%H:%M:%SZ"   # same as euvd
_ZONES = {"CEST": "+0200", "CET": "+0100"}
```

- **Safe XML.** Ruff S314 fires on `ET.XMLParser` (checked); keep the `noqa` on that line only:
  ```python
  class _DoctypeRefusedError(CvestreamError):
      """The document declares a DTD: entities could be declared, so it is refused."""

  class _NoDoctypeBuilder(ET.TreeBuilder):
      def doctype(self, name: str, pubid: str, system: str) -> None:
          raise _DoctypeRefusedError(name)

  def _parse_rss(body: bytes) -> ET.Element:
      # A DOCTYPE is refused before any entity can be expanded, so neither XXE
      # nor an entity bomb can run; this is what defusedxml would do.
      parser = ET.XMLParser(target=_NoDoctypeBuilder())  # noqa: S314
      try:
          parser.feed(body)
          return parser.close()
      except (ET.ParseError, _DoctypeRefusedError) as exc:
          raise SourcePayloadError(NAME, f"GET {RSS_URL}: not usable XML ({type(exc).__name__})") from exc
  ```
  Verified with Python 3.14.4 (expat 2.7.4): the C `XMLParser` calls `target.doctype()` and propagates the exception before any entity is expanded. Without this guard, a billion-laughs prolog expands (`'lollollol'`). An undeclared `&x;` (XXE) is already a `ParseError`, but test both.
- **RSS items.** `channel = root.find("channel")`; `items = channel.findall("item")`. A missing `channel` or no `item` is a `SourceContractError`. Per item:
  - the serial comes from `_LINK_SERIAL.search((link.text or "").strip())`, else from `_TITLE_SERIAL.match((title.text or "").strip())`;
  - `pub = _parse_pub_date(pubDate.text)`;
  - an item without a serial or without a usable date is skipped and counted;
  - if no item is usable, raise `SourceContractError`;
  - keep the newest `pubDate` per serial.
- **`_parse_pub_date`:** strip; replace a trailing zone token from `_ZONES`; `email.utils.parsedate_to_datetime` in `try`/`except TypeError, ValueError:` (PEP 758 form, as in `http.py`); a naive result gives `None`; `astimezone(UTC)`.
- **Advisory fetch, for each item due** (`pub > start`, RSS order):
  - `data = await get_json(http, NAME, JSON_URL.format(serial=s), max_bytes=MAX_ADVISORY_BYTES)`;
  - catch **only `SourcePayloadError`** there (not JSON, too large). It is per advisory: skipped, counted, held back;
  - `SourceTransportError` propagates and fails the fetch;
  - then `_Advisory.model_validate(data)`. A `ValidationError`, a serial that does not fullmatch `_SERIAL`, or `advisory.serial_number.strip() != s` means skipped, counted, held back.
- **Contract errors:** at least one item is due and every due advisory was skipped.
- **Next cursor (hold-back):**
  ```python
  held = min((pub for pub in failed_pubs), default=None)
  usable = [pub for pub in item_pubs if held is None or pub < held]   # every usable RSS item
  newest = max(usable, default=start)
  next_start = min(max(start, newest), observed_at)
  ```
  It is encoded with `_CURSOR_FORMAT`. A failed advisory keeps the cursor strictly below its `pubDate`. Newer advisories are fetched again next time, which is harmless because an unchanged content hash touches nothing, and bounded by the 10 RSS items.
- **`_records`:**
  - `observed_at` comes from one `clock.now()` per fetch;
  - `description = Sourced(_description(advisory.description), NAME, observed_at)` or `None`;
  - `title = Sourced(clean title)` or `None`;
  - `references` and `url` as in AC 2;
  - `published_at = parse_calendar_date(_original_date(md))` (`sources.base` helper);
  - `updated_at = _parse_publish_date(advisory.publish_date) or pub_date`;
  - `affected` as in AC 3: entries attributed to a CVE go only to that CVE's record; unattributed entries go to every record, in section order;
  - `scores=()`, `kev=()`.
- **`_description`:** `re.sub(r"<br\s*/?>", "\n", text, flags=re.I)`, then strip each line, drop empty lines, join with `"\n"`, then `clean_text`. Do not strip other tags: none occur, and the renderer escapes everything.
- **Block rule for the affected-products section.** A prototype gave the expected results below. Read the lines between the heading and the next `^# ` heading, after dropping fenced blocks (```` ``` ````):
  - a blank line ends the current paragraph or list;
  - a non-bullet, non-blank line extends the current paragraph, or the current bullet when it is a continuation;
  - a line matching `^(\s*)[-*]\s+(.*)` is a bullet. A deeper indentation than the current top-level bullet makes it nested, appended to its parent as `"; <text>"`;
  - a paragraph whose next non-blank element is a bullet list is that list's **lead-in**, and the block becomes **one** entry: `"<lead-in>: <bullet>; <bullet>; …"`;
  - a list without a lead-in gives one entry per top-level bullet;
  - a paragraph without a list is one entry;
  - `_clean_markdown` applies to each part before joining: remove `**` and backticks, remove `[New]` (any case, also inside `**[New]**`), remove reference markers `\s*\[\d+(?:\s*,\s*\d+)*\]`, collapse whitespace, and strip trailing ` :;,.`. Do **not** remove `_`, because `mod_php` and `php_admin_value` are real names;
  - the CVEs of an entry are the `_CVE` matches in its raw text, upper-cased;
  - empty entries are dropped.

  Expected entries:
  - **2026-018:** 6 entries, none attributed:
    - `"PHP 8.5 versions prior to 8.5.12"`, `"PHP 8.4 versions prior to 8.4.27"`, `"PHP 8.3 versions prior to 8.3.36"`, `"PHP 8.2 versions prior to 8.2.35"`;
    - `"End-of-life branches (PHP 8.1 and older), which no longer receive updates from the PHP project"`;
    - the "Many commercial and open-source products use PHP …" paragraph.
  - **2026-019:** 1 entry attributed to CVE-2026-110264: `"The vulnerability CVE-2026-110264 affects the following versions of GitLab Community and Enterprise Editions: from 7.0 before 18.11.13; 19.0 before 19.0.10; …; 19.5 before 19.5.1"`.
  - **2025-029:** 1 entry: `"The vulnerability seems to be affecting Gen 7 SonicWall firewalls. The vendor is investigating, …"`.
  - **2025-008:** 3 entries, one per CVE, the FortiNDR / FortiOS… / FortiSIEM blocks. The CVE-2024-45324 record must not carry FortiSIEM.
- **Why `CERT-EU-SA<serial>` and not `<serial>` for the advisory-only `record_id`.** `pipeline/normalize.py` adds the record ID to the aliases, and `domain/identity.primary_id` picks the lexicographically smallest advisory alias. `"2025-029" < "CERT-EU-SA2025-029"`, so a bare serial would become the display ID and the GUID (`cvestream:<ep>:2025-029:new`). For per-CVE records the CVE always wins, and `2026-018:CVE-…` is a harmless advisory alias, never a merge key (`MERGE_KINDS` is CVE and EUVD only).

### How the existing pipeline handles these records (read, nothing to change)

- `pipeline/normalize.normalize` gives the aliases `{record_id, CVE, CERT-EU-SA…}`, and the primary ID is the CVE. `pipeline/correlate.merge_keys` keeps only the CVE, so the record merges with the EUVD vulnerability of that CVE and never with another CVE of the same advisory. For the advisory-only record, there is no merge key: it is never merged, and its primary ID is `CERT-EU-SA<serial>`.
- **Revision.** The same `record_id` gets new content, so `content_hash` changes and `update_source_record` touches the vulnerability. `domain/publication.decide` then finds a history, no KEV and no band rise, and decides nothing (FR-19). A CVE added by a revision is a new `record_id`, so a new vulnerability is published as `new`, or the record merges into an existing EUVD vulnerability, which is published as `new` on endpoints that had never matched it (FR-26).
- **Activity window.** `correlate.activity_time` uses `updated_at` (the last revision), so a revised advisory makes its vulnerabilities active again for 14 days. That publishes nothing new on endpoints that already have history.
- **Accepted consequence** (sprint change proposal, technical impact): a pre-announcement without CVE publishes once as `CERT-EU-SA<serial>`, and again per CVE when a revision adds them. Advisory IDs never merge, so there is at most one extra item per advisory. Recall over volume (FR-25). Document it in `docs/sources.md`.
- **Accepted risk:** a CVE mentioned only for context (old CVE, reference URL) gets a record. It can make an old vulnerability active again and publish it as `new` on an endpoint it matches. CERT-EU has about two advisories a month. Document it.
- **Matcher** (`domain/matcher.py`, unchanged): CERT-EU entries are affected products, so structured data decides. A `php` entry hits `"PHP 8.5 versions prior to 8.5.12"` on whole words. `version_text=None` means a `min_version` entry gets `versions_unknown` and still matches (FR-13, FR-25). An entry with `match_description: false` also matches, since this is structured data. After the EUVD merge, EUVD's `8.5.* <8.5.12` rows make the versions known: the matcher's `any(r is not None and r.reaches(...))` wins over the CERT-EU `None` rows.

### Existing files this story touches (current state → change → preserve)

| File | Current state | Change | Preserve |
|---|---|---|---|
| `src/cvestream/sources/base.py` | `get_json` does the HTTP-to-`SourceError` mapping and the JSON decode | Extract `get_bytes`; `get_json` calls it | Every message and exception type of `get_json`, `clean_text`, `parse_calendar_date`, `PLACEHOLDERS` |
| `src/cvestream/sources/registry.py` | `ADAPTER_FACTORIES` for `euvd`, `cisa_kev`; `build_sources` iterates `SourcesSettings.model_fields` | Add `cert_eu` | The "one factory per settings field" invariant (`test_every_settings_source_has_a_factory`) |
| `src/cvestream/settings.py` | `SourcesSettings(euvd: EuvdSourceSettings, cisa_kev: SourceSettings)` | `cert_eu: SourceSettings` after `cisa_kev` | `extra="forbid"`, interval bounds, env nesting (`CERT_EU` has one underscore and `__` splits levels: it works, but test it) |
| `src/cvestream/pipeline/render.py` | `_title_product` hits on every affected product; `_SOURCE_ORDER = {"euvd": 0, "eu_kev": 1, "cisa_kev": 2}` | Filter out `_PROSE_SOURCES` in `_title_product`; add `cert_eu: 3` | `_affected` body, `_product_label`, the entry-name fallback, every existing render test |
| `tests/unit/sources/test_registry.py`, `tests/unit/test_settings.py` | Assert two sources | Three sources, plus the `cert_eu` cases | Everything else |
| `docs/sources.md`, `docs/configuration.md`, `settings.example.yaml`, `README.md`, `tests/fixtures/README.md` | No CERT-EU | See Task 8 | Existing sections |
| `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md` | Title rule from story 1.6; `feedparser <to pin>` | Task 8.5 only | Everything else |

### Fixture capture (Task 1)

Behind the TLS-intercepting proxy, use `curl --cacert ~/ca.pem` in your shell only, and never commit the CA. `R=https://cert.europa.eu/publications/security-advisories`.

| Fixture | Command |
|---|---|
| `cert_eu/cert_eu_rss.xml` | `curl "https://cert.europa.eu/publications/security-advisories-rss" -o cert_eu_rss.xml` (bytes as served; 10 items, 2026-018 at `Fri, 23 Oct 2026 16:36:26 CEST`, 2026-019 at `Thu, 22 Oct 2026 11:14:22 CEST`) |
| `cert_eu/cert_eu_2026_018_multi_cve_revised.json` | `curl "$R/2026-018/json" \| jq .` (v1.2, 3 CVEs, `publish_date` `23-10-2026 14:36:26`) |
| `cert_eu/cert_eu_2026_019_single_cve.json` | `curl "$R/2026-019/json" \| jq .` (GitLab, CVE-2026-110264, lead-in names the product) |
| `cert_eu/cert_eu_2025_029_no_cve.json` | `curl "$R/2025-029/json" \| jq .` (no CVE, `# Products Affected` prose) |
| `cert_eu/cert_eu_2025_008_cve_blocks.json` | `curl "$R/2025-008/json" \| jq .` (3 CVEs, one product block each) |
| `euvd/euvd_php2shell_second_cve.json` | `curl "https://euvdservices.enisa.europa.eu/api/search?text=EUVD-2026-107265&size=10&page=0" \| jq .` (CVE-2026-109619, total 1) |

If the live payloads changed after 2026-11-03 (a 2026-018 v1.3, or a rotated RSS), record what is live, say so in the fixtures README and in the Debug Log, and adapt the expected values. Do not edit a fixture by value.

### Contract test plan (`tests/contract/cert_eu/test_cert_eu.py`)

Follow `tests/contract/cisa_kev/test_cisa_kev.py`: a `make_adapter` fixture with `HttpClient(sleep=no_sleep, random=lambda: 0.0, max_attempts=2)` and `FakeClock`, then `respx_mock.get(RSS_URL)` and `respx_mock.get(JSON_URL.format(serial=…))`. Set the clock to `2026-10-23T15:00:00Z` (or later) so the fixture items fall inside the 14-day window. Derive variants in test code (string or bytes replace on the recorded bytes, `json` deep copy); never write new fixture files for them.

1. The adapter satisfies `SourceAdapter`, and `name == "cert_eu"`.
2. 2026-018:
   - with a cursor just before its `pubDate`, three records, `record_id`s `2026-018:CVE-2026-109618`, `…109619` and `…115560`, each with `aliases == (cve, "CERT-EU-SA2026-018")`;
   - `url == "https://cert.europa.eu/publications/security-advisories/2026-018/"`;
   - `published_at == datetime(2026, 10, 20, tzinfo=UTC)`, `updated_at == datetime(2026, 10, 23, 14, 36, 26, tzinfo=UTC)`;
   - `scores == ()`, `kev == ()`;
   - the 6 affected entries above, each with `version_text is None` and `vendor is None`;
   - the description has no `<br>`;
   - the references contain `https://www.php.net/archive/2026.php#2026-10-20-1`.
3. Through `normalize` + `merge_keys` (or a mini correlation on the `state` fixture), the three records give three distinct merge-key sets: `{CVE-…618}`, `{…619}`, `{…560}`.
4. 2026-019 (one record) through `normalize` + `build_vulnerability` + `matcher.match`: `ProductEntry("gitlab")` matches with `structured=True`, and `ProductEntry("gitlab", min_version=Version("19.0"))` matches with `versions_unknown=True`.
5. 2025-029:
   - one record, `record_id == aliases[0] == "CERT-EU-SA2025-029"`, `len(aliases) == 1`;
   - `normalize(record).primary_id == "CERT-EU-SA2025-029"`;
   - one affected entry naming SonicWall.
6. 2025-008: three records; the CVE-2024-45324 record has the FortiOS block and no FortiSIEM text; each record has exactly its own block.
7. Cursor:
   - `None` gives the start now − 14 days (an item older than that is not fetched: respx route `call_count == 0`);
   - a cursor equal to the newest `pubDate` fetches no JSON at all;
   - `next_cursor == "2026-10-23T14:36:26Z"`;
   - a cursor that is invalid or in the future falls back to the floor and logs `source.cursor_invalid`;
   - a cursor older than the floor is clamped and logs `source.cursor_clamped`;
   - a future RSS `pubDate` is capped at the fetch time.
8. Revision:
   - derive v1.1 by deleting `CVE-2026-115560` from `content_markdown`: 2 records;
   - then the recorded v1.2, with the RSS `pubDate` bumped and the cursor set to the v1.1 date: 3 records, the two older ones keeping the same `record_id`;
   - an unchanged `pubDate` makes no JSON request.
9. Dates:
   - `CEST` and `CET` give the right UTC values (parametrize `Fri, 23 Oct 2026 16:36:26 CEST` → `14:36:26Z`, `Mon, 02 Nov 2026 10:00:00 CET` → `09:00:00Z`, `+0200`, `GMT`);
   - an unknown zone (`PDTX`) or a garbage date makes the item skipped and counted;
   - an unreadable `publish_date` falls back to the RSS `pubDate`;
   - a missing `original_date` gives `published_at is None`.
10. CVE extraction: a lower-case `cve-2026-…` in a URL counts and is upper-cased; duplicates are emitted once; `CVE-2026-1234567` stays whole; `XCVE-2026-1234` is not a match.
11. XML safety:
    - a billion-laughs prolog added in front of the recorded RSS gives `SourcePayloadError`;
    - an XXE `<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>` gives `SourcePayloadError`;
    - a truncated RSS gives `SourcePayloadError`;
    - in each case no JSON route is called.
12. Shape:
    - no `channel`, or `channel` without `item`: `SourceContractError`;
    - every item without a usable serial or date: `SourceContractError`.
13. Advisory failures:
    - an HTML body (`<!DOCTYPE html>…`) or a JSON without `content_markdown` for 2026-019 gives the 2026-018 records with `skipped == 1`, and `next_cursor` strictly below 2026-019's `pubDate`;
    - `serial_number` different from the RSS serial: skipped;
    - every due advisory failing: `SourceContractError`.
14. Transport:
    - RSS 500 (after retries) or 403: `SourceTransportError`;
    - advisory JSON 404: `SourceTransportError`, nothing returned;
    - RSS over `MAX_RSS_BYTES` (`Content-Length`): `SourcePayloadError`.
15. `live` (`@pytest.mark.live`, `@pytest.mark.enable_socket`, `@pytest.mark.anyio`, `HttpClient()` + `SystemClock()`):
    - `fetch(None)` returns `skipped == 0` and a `next_cursor` that parses as aware UTC; every RSS item in the window gives at least one record;
    - 2026-018 by serial (`get_json` + `_records`, or a small public helper): the record IDs include the three expected ones;
    - 2026-019 gives one record;
    - 2025-029 gives one record `CERT-EU-SA2025-029`.

### PHP2Shell replay design (`tests/integration/test_cert_eu_php2shell.py`)

- Endpoint `team-php` with `ProductEntry(name="php")`, and a `_Provider` as in `test_pipeline_end_to_end.py`.
- **Run 1**, clock at `2026-10-23T15:00:00Z`: the CERT-EU adapter on `cert_eu_rss.xml` and the 2026-018 JSON. Serve only 2026-018 (cursor just before its `pubDate`), or serve every due serial from the recorded files. Expected:
  - 3 decisions, `kind == "new"`, `band is Band.UNKNOWN`;
  - GUIDs `cvestream:team-php:CVE-2026-109618:new`, `…109619:new`, `…115560:new`.
- **Run 2**, clock at `2026-10-30T19:00:00Z`: the EUVD adapter on `euvd_php_branch_wildcard.json` and `euvd_php2shell_second_cve.json`. The KEV dump route can serve the recorded `euvd_kev_dump.json`; use a cursor just before `Oct 30, 2026, 6:31:35 PM`. Expected:
  - 2 decisions `band-critical` for CVE-2026-109618 and CVE-2026-109619, with titles starting `[UPDATE]` when rendered;
  - no decision for CVE-2026-115560.
- Assert 3 rows in `schema.vulnerability`, and `capture_logs()` with no `publish.skipped` event.
- **Run 3**, the same CERT-EU revision again: 0 decisions. Either the cursor is unchanged, so no JSON is fetched, or the same records are fed again, so the content hash is unchanged.
- Check the actual `cycle`/`run` signature in `pipeline/run.py` and follow `test_pipeline_end_to_end.py`, which calls `cycle(...)` with `SourceOutcome`s, for the call shape and the log capture.

### Architecture compliance

- Layering: `sources/cert_eu.py` imports only `domain.model`, `sources.base`, `clock`, `errors`, `http` (type), stdlib, `pydantic` and `structlog`, like `euvd.py`. `lint-imports` must stay green (7 contracts kept at baseline). `render.py` must not import `sources.cert_eu`: write the `"cert_eu"` literal in `_PROSE_SOURCES`.
- `httpx` is only used through `HttpClient` and the `sources.base` helpers. Retries stay in `cvestream.http` only; the adapter never retries.
- Unknown is `None`: no score, versions `None`, `published_at` `None` when missing. Never `0` or `""`.
- Time: one `clock.now()` per fetch. No `datetime.now()`. Every datetime is aware UTC (ruff `DTZ`). The cursor comes from source data and is capped at the fetch time.
- Exceptions: `_DoctypeRefusedError` (and any private invalid-advisory error) inherit `CvestreamError` and end in `Error`. No bare `except`, no `except Exception`.
- Identity: never key on a URL or on the RSS `<guid>`. The `record_id` is the serial (plus the CVE).
- Python 3.14: no `from __future__ import annotations`; `except A, B:` is the house style (PEP 758).
- One module per adapter, no `utils.py`. Tool config stays in `pyproject.toml`. The only new lint suppression is the `noqa: S314` line.

### Library / framework requirements

- No new dependency. stdlib `xml.etree.ElementTree` (C accelerator, expat 2.7.4 in the runtime Python 3.14.4), `email.utils.parsedate_to_datetime`, `re`. `pydantic` 2.14 for `_Advisory`, as `euvd.py` does. `respx` 0.23 for the HTTP mocks.
- Rejected:
  - `feedparser`: unpinned in `project-context.md`; it brings `sgmllib3k`, and its RFC 822 zone table has no `CET`/`CEST`, so it would mis-time `pubDate` exactly like `email.utils`;
  - `defusedxml`: last release 0.7.1 in 2021, and the stdlib guard above covers DOCTYPE, so it adds nothing.

  Record both in the Dev Notes / PR description.

### Testing requirements

- Network blocked by default; `live` deselected; anyio plugin (`@pytest.mark.anyio`); `FakeClock` from `tests/conftest.py`; no sleeps.
- Cover the unknown paths: no score, no affected section, no `original_date`, unreadable `publish_date`, unknown RSS zone.
- Prove red then green for the render title rule: the new render test must fail before the `_title_product` change.
- Full gate: every command in `CLAUDE.md`. Baseline `0c1903d`: **1090 passed, 2 deselected**.

### Previous story intelligence

- **1.8** (last merged): a recorded fixture plus a contract test through the real adapter and the matcher is the house pattern for "it really matches". Keep "every existing test passes unchanged" as a check. The deferred list of version formats that still give `None` grew; "X versions prior to Y" (CERT-EU prose) is a new shape, not handled here. Add it to `deferred-work.md` under a "Deferred from: story 2.1" heading, with the trigger "CERT-EU affected text fed to `version.py`".
- **1.7:** structured data decides, and `match_description: false` waits for EUVD. CERT-EU entries are structured, so such an entry matches CERT-EU-only items. That is the point of this story; document it in `docs/sources.md`.
- **1.4 review lessons** (apply them here):
  - every record rejected is drift, so raise `SourceContractError` instead of returning an empty success;
  - cap the cursor at the fetch time;
  - fall back on an invalid or future cursor;
  - clamp an old cursor;
  - validate IDs with full-match patterns;
  - `RecursionError` on deep JSON is already handled in `get_json`.
- **1.7/1.8:** `docker build --target runtime` fails locally in proxy mode (`x509: certificate signed by unknown authority` on the ghcr.io token). Report it; do not disable verification; CI runs it.

### Git intelligence

- Branch `story-2-1-cert-eu-adapter` from `main` `558b1c9` (merge of wixyvir/cvestream#18, story 1.8). Its first two commits are `98b5ffe chore(story-2.1): mark epic 1 done` and `0c1903d docs(story-2.1): add epic 2 stacked PR plan` (`_bmad-output/implementation-artifacts/epic-2-stack-plan.md`).
- Story 2.2 branches from `0c1903d` and will be rebased onto this branch after review. Keep this branch linear; never force-push without `--force-with-lease`.
- Commit style: `feat(story-2.1): …`, `test(story-2.1): …`, `docs(story-2.1): …`, `chore(story-2.1): …`, one concern per commit, never squashed.

### Commits (suggested, one concern each)

1. `test(story-2.1): record CERT-EU advisories and the second PHP2Shell EUVD record` (fixtures + README rows)
2. `refactor(story-2.1): share the body fetch of the source helpers` (`get_bytes`)
3. `feat(story-2.1): add the CERT-EU adapter` (`cert_eu.py` + contract tests)
4. `feat(story-2.1): register the CERT-EU source in settings and the registry` (settings, registry, their tests)
5. `fix(story-2.1): never name the title product from CERT-EU prose` (render + test)
6. `test(story-2.1): replay PHP2Shell from CERT-EU to EUVD through the pipeline`
7. `docs(story-2.1): document the CERT-EU source` (`sources.md`, `configuration.md`, `settings.example.yaml`, `README.md`, `architecture.md`, `project-context.md`, `deferred-work.md`)
8. `chore(story-2.1): mark story ready for review` (story file, sprint status)

Every commit message ends with the attribution line given by the session instructions.

### Decisions taken while creating this story (autonomous run, no user available)

These are the non-obvious choices. Each one is a candidate "decision needed" item for the review, which can reverse it.

1. **XML parser:** stdlib plus a refused DOCTYPE, no new dependency (AC 4). Alternatives: `defusedxml` (dependency, stale), `feedparser` (dependency, wrong `CEST` handling).
2. **Affected products = the whole section, by blocks, with CVE attribution** (AC 3). The epics AC says "the bullets become affected products". Taken literally, it would make GitLab (2026-016, 2026-019) and Cisco (2026-017) advisories unmatched, because the product name is in the lead-in, and it would miss the 19 prose-only sections. Attribution keeps one advisory's FortiSIEM block off its FortiOS CVE. The text stays unparsed for versions (`version_text=None`), and `domain/version.py` is unchanged.
3. **Title product never from CERT-EU prose** (AC 11, render change). Otherwise a title would read "CVE-2026-110264 in The vulnerability CVE-2026-110264 affects the following versions of GitLab …". Alternative: leave the render as it is and accept long titles.
4. **Advisory-only `record_id` = `CERT-EU-SA<serial>`**, so that the display ID and the GUID read as an advisory ID (see Adapter design).
5. **Cursor:** strict `>` on `pubDate`, the same floor and clamp as EUVD, and hold-back on a per-advisory payload failure. A transport failure is source-level. Assumption: fewer than 10 publications or revisions between two polls (CERT-EU publishes about 2 a month).
6. **`scores=()`**, not `(Sourced(None),)`: CERT-EU has no score field. The body then lists no CERT-EU score line; it still reads `unknown` overall when no source scores.
7. **Every CVE ID in `content_markdown` counts**, including the References section and context mentions (AC 1, literal epics AC). The risk is documented.
8. **`cert_eu` is enabled by default**, after `cisa_kev` in settings order, at the default 15-minute interval.

### Project Structure Notes

- New paths follow the architecture tree: `src/cvestream/sources/cert_eu.py`, `tests/contract/cert_eu/`, `tests/fixtures/cert_eu/`. The integration test goes in `tests/integration/`. No new package or top-level module.
- No conflict with the unified structure. `render.py` gains a source-name literal, which is precedent: `_SOURCE_ORDER` already names sources.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.1: CERT-EU adapter]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-01-epic-2-scope.md#1. Issue summary] (CERT-EU payload facts, design gap, technical impact)
- [Source: _bmad-output/implementation-artifacts/epic-2-stack-plan.md#3.1 Story 2.1: CERT-EU adapter] (test checklist the user will run)
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns] (source adapters, `cert_eu` per-CVE rule, cursor rule), [#Authentication & Security] (untrusted XML), [#Data Architecture] (correlation rules, feed cache title rule), [#Implementation Patterns & Consistency Rules]
- [Source: _bmad-output/project-context.md#Framework & Architecture Rules] (Sources), [#Critical Don't-Miss Rules] (identity, recall, security), [#Testing Rules]
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-2, FR-7, FR-8, FR-19, FR-25)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] (advisory revisions, version formats)
- [Source: src/cvestream/sources/euvd.py] (`_start`, cursor encode/decode, skip pattern), [src/cvestream/sources/cisa_kev.py] (all-rejected contract error), [src/cvestream/sources/base.py], [src/cvestream/pipeline/correlate.py] (`MERGE_KINDS`, `activity_time`), [src/cvestream/pipeline/normalize.py], [src/cvestream/domain/identity.py] (`primary_id`), [src/cvestream/domain/matcher.py] (`_match_entry`), [src/cvestream/pipeline/render.py] (`_title_product`, `_SOURCE_ORDER`)
- [Source: tests/contract/cisa_kev/test_cisa_kev.py], [tests/integration/test_pipeline_end_to_end.py] (test patterns)

## Dev Agent Record

### Agent Model Used

claude-sonnet-5-5 (`bmad-dev-story`, autonomous run, 2026-11-03).

### Debug Log References

- Live payloads on 2026-11-03 matched the story: RSS with 10 items (2026-018 at `Fri, 23 Oct 2026 16:36:26 CEST`), 2026-018 v1.2 with three CVEs. No fixture was adapted.
- First replay run failed on `EuvdAdapter`'s sort check ("no longer sorted by dateUpdated descending"): the two recorded EUVD items had to be fed newest first, as the real API sorts. Test-side fix only.
- The replay counts the PHP2Shell vulnerabilities by primary ID, not all rows: the recorded KEV dump adds 5 unrelated EU KEV vulnerabilities.
- `docker build --target runtime` fails locally in proxy mode (`x509: certificate signed by unknown authority` fetching the ghcr.io token), as in stories 1.7 and 1.8. Verification was not disabled; CI runs that gate.

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created (2026-11-03, `bmad-create-story`, claude-opus-5-5).
- Implemented `src/cvestream/sources/cert_eu.py` as specified (AC 1 to 7): RSS then per-advisory JSON, one record per CVE (`<serial>:<CVE>`, aliases `(<CVE>, CERT-EU-SA<serial>)`), advisory-only record `CERT-EU-SA<serial>`, `CEST`/`CET` mapping, stdlib XML with a refused DOCTYPE, hold-back cursor, per-advisory skips, contract errors.
- `sources/base.py`: `get_bytes` extracted, `get_json` unchanged in behaviour and messages (existing tests pass unchanged, 4 tests added).
- Settings and registry: `sources.cert_eu` (enabled, 15 min, at most 60) and its factory; registry and settings tests updated.
- `pipeline/render.py`: `_PROSE_SOURCES` keeps CERT-EU prose out of the title (red then green: 2 of the 4 new render tests failed before the change); `cert_eu` added to `_SOURCE_ORDER`.
- Tests: 57 contract tests (+4 `live`, run once against the real site, green), the PHP2Shell replay, render, settings, registry and `get_bytes` tests. Suite: 1159 passed, 6 deselected (baseline 1090 passed, 2 deselected).
- Docs: `docs/sources.md` CERT-EU section, `docs/configuration.md`, `settings.example.yaml`, `README.md`, `tests/fixtures/README.md`, `architecture.md` (title rule, XML rule), `project-context.md` (XML parser lines), `deferred-work.md`.
- No new dependency (`pyproject.toml`, `uv.lock` unchanged), no `config/models.py` change (schema check green), no change to `domain/`, `correlate.py` or `run.py`.
- Autonomous choices and deviations (none against the AC):
  - A blank line does not end a bullet list when the next non-blank line is another bullet (a loose list stays one list under its lead-in). The story's block rule did not cover it; a test pins it.
  - An item skipped for an unreadable RSS date or serial counts in `FetchResult.skipped`.
  - Only the equality `serial_number == RSS serial` is checked (the RSS serial already full-matches the serial pattern), so no separate `_SERIAL` pattern exists.
  - The replay feeds the EUVD search page as the two recorded items merged into one page (`total` 2, newest first), and the recorded KEV dump for the dump route.
  - The `live` tests use the private `_records`/`_Advisory` helpers for the by-serial checks (allowed by the contract test plan).
- Gate results: ruff check, ruff format --check, ty check, lint-imports (7 kept), pytest, `config schema --check` and `config validate endpoints.example.yaml` all green. `docker build` not verified locally (proxy TLS, see Debug Log); CI covers it.

### File List

- `README.md` (modified)
- `docs/sources.md` (modified)
- `docs/configuration.md` (modified)
- `settings.example.yaml` (modified)
- `src/cvestream/sources/cert_eu.py` (new)
- `src/cvestream/sources/base.py` (modified)
- `src/cvestream/sources/registry.py` (modified)
- `src/cvestream/settings.py` (modified)
- `src/cvestream/pipeline/render.py` (modified)
- `tests/contract/cert_eu/__init__.py` (new)
- `tests/contract/cert_eu/test_cert_eu.py` (new)
- `tests/integration/test_cert_eu_php2shell.py` (new)
- `tests/fixtures/README.md` (modified)
- `tests/fixtures/cert_eu/cert_eu_rss.xml` (new)
- `tests/fixtures/cert_eu/cert_eu_2026_018_multi_cve_revised.json` (new)
- `tests/fixtures/cert_eu/cert_eu_2026_019_single_cve.json` (new)
- `tests/fixtures/cert_eu/cert_eu_2025_029_no_cve.json` (new)
- `tests/fixtures/cert_eu/cert_eu_2025_008_cve_blocks.json` (new)
- `tests/fixtures/euvd/euvd_php2shell_second_cve.json` (new)
- `tests/unit/sources/test_base.py` (modified)
- `tests/unit/sources/test_registry.py` (modified)
- `tests/unit/test_settings.py` (modified)
- `tests/unit/pipeline/test_render.py` (modified)
- `_bmad-output/planning-artifacts/architecture.md` (modified)
- `_bmad-output/project-context.md` (modified)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified)
- `_bmad-output/implementation-artifacts/2-1-cert-eu-adapter.md` (modified)

### Change Log

- 2026-11-03: implemented story 2.1 (CERT-EU adapter, settings and registry, title rule, PHP2Shell replay, docs); status set to review.
- 2026-11-03: code review: 3 patches applied (heading, `+` bullets, README status), 4 items deferred, 2 decisions open on wixyvir/cvestream#20; status stays `review`.
