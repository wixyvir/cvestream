---
baseline_commit: 3c08cdd37d8c7b53daab739dafae4349a6f9da51
---

# Story 5.1: Compact item body and title markers

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Created from the sprint change proposal 2026-11-21 (epic 5, readable bursts). -->

## Story

As a channel subscriber,
I want each Slack message to show the severity, the exploited status, the dates, the matched product and the start of the description, and the title to flag what matters with an emoji,
so that I can triage a message without opening it and spot the ones that matter while scrolling.

Built from the team's feedback canvas "CVEStream Retours" (canvas entries 3 and 4), see `_bmad-output/planning-artifacts/sprint-change-proposal-2026-11-21-epic-5-readable-bursts.md`. Slack's RSS app posts one message per item, shows the item `description` as plain text and cuts it with "..." after **492 to 498 characters** (measured on three long posts, a line break counted as one character). On Apache Tomcat items, the `Affected:` line repeated `Apache Software Foundation Apache Tomcat` five times and used up that space, so `Description:` never showed. Short PHP posts (about 208 visible characters, 2026-11-19) showed no `Description:` line either, for a reason still unknown.

## TL;DR

- **Body reorder (render only).** New order: a facts line (`critical · CVSS 9.1 · exploited: no · published 2026-11-19 · updated 2026-11-20`), `Matched:`, the full `Description:`, a compact `Affected:` line (each product label once, source version text verbatim, at most 3 per product, then `(+N more)`), then IDs, per-source CVSS, KEV detail (only when known-exploited), Sources and References.
- **Budget.** One named constant (495) in `pipeline/render.py`; a test on a recorded Tomcat fixture (CVE-2026-95517, 5 affected rows, CVSS 9.1) checks that the first 495 plain-text characters hold the band, `exploited: no`, the matched product and the first 40 characters of the description.
- **Title markers.** Unicode emoji before the existing words, built from decision-time values only: 🔥 known-exploited, 🔴 band `critical`, 🔺 kind `band-*`, 🆕 `is_fresh`. All that apply, in that order, then one space. No marker otherwise, no Slack shortcode.
- **Freshness (the only state change).** `decide()` gets the decision time and sets `PublicationDecision.is_fresh` on a `new` decision whose vulnerability was first published on the same UTC calendar day. Stored in a new nullable column `publication.is_fresh` (Alembic revision `0003`, **the only migration of wave 13**). Old rows are `NULL`, so no 🆕. Render stays clock-free.
- **Investigation.** First task: find why short items showed no description in Slack (leading hypothesis: Slack keeps only the first two `<p>` blocks). Fix it, or record it in `deferred-work.md`. The recommended layout (facts, Matched and Description in the first `<p>`) works whichever hypothesis holds.
- **Parallel wave 13:** stories 5.3 and 5.4 run at the same time on their own branches. Shared files: `docs/operations.md`, `CHANGELOG.md`, `README.md` (and, for 5.3, `pipeline/run.py` and `tests/unit/pipeline/test_run.py`). Story 5.2 (wave 14) builds on this story's one-liner and adds revision `0004` after `0003`.
- No GUID change, so nothing is posted to Slack again. Every feed's ETag changes once after deployment.

## Acceptance Criteria

1. **Body order (FR-18).** For any feed item, the description blocks come in this order:
   1. a facts line `<severity band> · CVSS <highest score> · exploited: <yes|no> · published <day> · updated <day>`. The `updated` part is left out when it falls on the same UTC day as `published`. An unknown value reads `unknown`;
   2. the matched products line, `Matched: …`, with `(versions unknown)` when it applies;
   3. the full description, never cut on the server;
   4. the affected products;
   5. then the IDs line, each source's CVSS score with its version, the KEV detail (each list, the day it added the vulnerability, ransomware use) **only when it is known-exploited**, the sources and the references.

2. **Compact Affected line.** With affected products from several sources, or several rows for one product, each product label appears once (vendor dropped when the product name already contains it, the `_product_label()` rule), followed by its rows' version text **verbatim** (never parsed or rewritten), at most 3 per product, then `(+N more)`.

3. **Budget on the Tomcat fixture.** On the recorded Tomcat fixture (CVE-2026-95517 / EUVD-2026-130760: five affected rows, CVSS 9.1, not exploited), the description reduced to plain text (tags stripped, entities decoded, a line break counted as one character) holds, in its first 495 characters, the severity band, `exploited: no`, the matched product and the first 40 characters of the description. The budget is one named constant.

4. **Title markers.** A title starts with these Unicode emoji, in this order, all that apply, followed by one space:
   - 🔥 when the stored `is_known_exploited` is true;
   - 🔴 when the stored band is `critical`;
   - 🔺 when the kind is `band-<band>`;
   - 🆕 when the stored `is_fresh` is true.

   Every other band and the unknown state get no emoji, and there is no "safe" marker. Without any marker the title has no leading space. The existing words (`EXPLOITED`, `[UPDATE]`, `severity critical`) stay. No Slack shortcode (`:fire:`).

5. **Golden titles.** These render exactly:
   - `🔥🔴🆕 CVE-2026-95517 in Apache Software Foundation Apache Tomcat: severity critical, EXPLOITED`
   - `🆕 CVE-2026-107494 in OpenSSL: severity unknown`
   - `🔴🔺 [UPDATE] CVE-2026-118028 in Apache Software Foundation Apache Tomcat: severity raised to critical`
   - `🔥 [UPDATE] CVE-2026-1234 in PHP Group PHP: now EXPLOITED, severity high`

6. **Freshness decided once.** For a `new` decision, when the vulnerability's earliest `published_at` falls on the same UTC calendar day as the decision time from the injected `Clock`, `PublicationDecision.is_fresh` is true. It is false when `published_at` is unknown or on another day, and false for every `kev` and `band-*` decision. It is stored in a new nullable `publication.is_fresh` column (Alembic migration, upgrade and downgrade tested). Existing rows are `NULL` and render without 🆕.

7. **Render stays clock-free.** A feed rendered twice from the same state, with the clock moved past midnight in between, gives identical XML and ETag (and `feed_cache.changed_at` does not move).

8. **Short items investigated.** For short items whose body fits in the budget (the PHP posts of 2026-11-19 showed no `Description:` line in Slack), the cause is investigated first and either fixed or recorded in `deferred-work.md`, with the evidence.

9. **Nothing else changes.** GUIDs, `pubDate`, `link`, item order, `lastBuildDate`, the 14-day window, escaping (every value HTML-escaped, only `http`/`https` links) and the publication rules are unchanged. `test_restart_no_repost.py` stays green.

10. **Documentation in the same PR.** `docs/operations.md` (item format: title markers, body order, the 495-character window, one golden example), `README.md`, `CHANGELOG.md` (`[Unreleased]`), the render module docstring, the fixtures README, and `architecture.md` / `project-context.md` only if the implementation departs from the rules already written there by the epic commit (see "Planning docs").

11. **Gates.** Every gate command of `AGENTS.md` passes.

## Tasks / Subtasks

- [x] **Task 1: Investigate the missing description on short items (AC: 8)**
  - [x] 1.1 Get the XML of a short PHP item posted on 2026-11-19 (it is still in the 14-day EUVD window on 2026-11-21): run `uv run cvestream poll --once` locally with a throwaway `endpoints.yaml` that has a `php` endpoint, and read the rendered feed from the local state (`feed_cache.xml`) or from `http://127.0.0.1:8080/feeds/<token>.xml`. Never commit that file, the data directory, or the token; never paste a token into logs, the story or the PR.
  - [x] 1.2 Compare it with the Slack message (ask Cyprien for the post text if needed; record that as a blocker in the Dev Agent Record if it is unavailable). Count the plain-text characters per `<p>` block with the AC 3 reduction.
  - [x] 1.3 Test the hypotheses in "Short items: investigation" below. Write the finding (evidence, conclusion) in the Dev Agent Record.
  - [x] 1.4 If the cause is the layout (H1 or H2), the Task 3 layout fixes it: say so and pin it with a test. If it cannot be determined or fixed in render, add an entry to `_bmad-output/implementation-artifacts/deferred-work.md` under `## Deferred from: story 5.1 (compact item body, 2026-11-21)` with the evidence and the trigger to revisit.

- [x] **Task 2: Freshness in the domain and the state (AC: 6, 9)**
  - [x] 2.1 `src/cvestream/domain/publication.py`: add `is_fresh: bool = False` as the **last** field of `PublicationDecision`; add a `now: datetime` parameter to `decide()` (required, after `history`); compute `is_fresh` with a private helper (see "Freshness design"). Update the module docstring (the decision time is an input; still no clock, no I/O).
  - [x] 2.2 `src/cvestream/pipeline/run.py`: pass `now` to `decide()`.
  - [x] 2.3 `src/cvestream/state/schema.py`: add `Column("is_fresh", Boolean, nullable=True)` to `publication`, after `is_known_exploited`, with a comment (`NULL` on rows older than the column).
  - [x] 2.4 New migration `src/cvestream/state/migrations/versions/0003_add_publication_is_fresh.py` (`revision = "0003"`, `down_revision = "0002"`): `op.batch_alter_table("publication")` + `add_column(sa.Column("is_fresh", sa.Boolean(), nullable=True))`; downgrade drops the column in batch mode. Copy the header style of `0002_add_feed_cache.py`.
  - [x] 2.5 `src/cvestream/state/repository.py`: `add_publication()` writes `is_fresh=decision.is_fresh`; `StoredPublication` gains `is_fresh: bool | None` (last field); `publications_since()` selects and maps it. `publication_history()` and `PastPublication` do not change.
  - [x] 2.6 Tests (see "Testing requirements"): `tests/unit/domain/test_publication.py` (every `decide()` call gets `now`; new freshness cases), `tests/unit/domain/conftest.py` (`make_vulnerability(published_at=...)`), `tests/unit/state/test_db.py` (head is `0003`; upgrade from `0002` keeps rows with `is_fresh` `NULL`; downgrade to `0002` drops the column), `tests/unit/state/test_repository.py` (round trip of `True`, `False` and a `NULL` row).
  - [x] 2.7 Commit: `feat(story-5.1): decide and store whether a new item is fresh`.

- [x] **Task 3: Body reorder and compact Affected line (AC: 1, 2, 3, 8, 9)**
  - [x] 3.1 Record the Tomcat fixture (see "Tomcat fixture") as `tests/fixtures/euvd/euvd_tomcat_five_affected_rows.json` and add its row to `tests/fixtures/README.md`.
  - [x] 3.2 `src/cvestream/pipeline/render.py`: add the budget constant, rewrite `_description()`, add `_facts()`, rewrite `_affected()`, drop the `Known exploited` line for vulnerabilities that are not known-exploited (see "Render design"). Keep `_h`, `_safe_url`, `_anchor`, `_sources`, `_references`, `_ordered_records`, `_link` unchanged.
  - [x] 3.3 Update the module docstring (order, budget, Slack window).
  - [x] 3.4 Tests in `tests/unit/pipeline/test_render.py`: rewrite the two golden body tests and the unknown-everywhere test for the new order; add the compact-Affected, facts-line, budget and full-description tests. Update `tests/integration/test_pipeline_end_to_end.py` (it asserts `<p><b>IDs:</b>` on line 0 and `<b>Published:</b>`).
  - [x] 3.5 Commit: `feat(story-5.1): lead the item body with the facts Slack shows`.

- [x] **Task 4: Title markers (AC: 4, 5, 7)**
  - [x] 4.1 `src/cvestream/pipeline/render.py`: `FeedEntry` gains `is_fresh: bool | None = None` as its last field; add `_markers(entry)` and prefix it in `_title()` (see "Render design").
  - [x] 4.2 `src/cvestream/pipeline/run.py`: build `FeedEntry(..., is_fresh=p.is_fresh)`.
  - [x] 4.3 Tests: the four golden titles, every marker alone and the order, `None`/`False` give no 🆕, no leading space without markers. In the existing `test_titles` cases, only the known-exploited ones change (they now start with `🔥 `); the others stay as they are. In `tests/unit/pipeline/test_run.py`: a vulnerability published on the run's day gets 🆕 in its title; a rerun after midnight keeps the XML, the ETag and `changed_at`. Check `tests/integration/test_cert_eu_php2shell.py` titles (its clock and CERT-EU publish dates may now give 🆕).
  - [x] 4.4 Commit: `feat(story-5.1): add emoji markers to item titles`.

- [x] **Task 5: User documentation (AC: 10)**
  - [x] 5.1 `docs/operations.md`, section "Feeds": rewrite the title bullet (markers, golden examples), the body bullet (new order, compact Affected, KEV detail only when exploited), the `pubDate` bullet (`under Published` becomes `in the facts line`), and add one bullet on the Slack window (about 495 plain-text characters, the constant's name, what is always in it). Add one golden plain-text example.
  - [x] 5.2 `README.md`: the intro says each item carries "CVSS score, known-exploited status, and affected versions": add the title emoji in one short clause. No other README change expected; confirm with `grep -n -i "title\|item" README.md`.
  - [x] 5.3 `CHANGELOG.md`, `[Unreleased]`: a `### Changed` entry (item body order, compact Affected line, title emoji markers; every feed's ETag changes once, no item is posted again) and the `publication.is_fresh` migration (applied at startup, existing items never get 🆕). Keep the existing `### Added` entries; 5.3 and 5.4 add theirs in parallel.
  - [x] 5.4 Check `AGENTS.md` (no item-format rule today, so no change expected) and `docs/configuration.md` (no change).
  - [x] 5.5 Commit: `docs(story-5.1): document the compact item format and title markers`.

- [x] **Task 6: Planning docs, only if needed (AC: 10)**
  - [x] 6.1 The epic commit `3c08cdd` already wrote the 5.1 rules into `architecture.md` (story 1.6 feed cache rules, story 1.5 publication rules, requirements mapping) and `project-context.md` ("Title markers come from decision-time values only"). Re-read them against the implementation. Amend them in the same PR only if something differs (for example, a rule about paragraphs found by Task 1, or the `decide()` signature if the architecture names it). Commit: `docs(story-5.1): amend the item format rules`.

- [x] **Task 7: Gates and delivery (AC: 11)**
  - [x] 7.1 Run every gate command of `AGENTS.md`. The local `docker build` may fail on a `ghcr.io` x509 error behind the corporate proxy; then rely on CI and say so in the Dev Agent Record.
  - [x] 7.2 Story file: tick the tasks, fill the Dev Agent Record, set Status to `review`; in `sprint-status.yaml`, set `5-1-compact-item-body-and-title-markers: review`. Commit: `chore(story-5.1): mark story ready for review`.
  - [x] 7.3 Push over SSH and mark the draft PR ready (`SSL_CERT_FILE=$HOME/ca.pem gh pr ready ...`). Update its body: the Task 1 finding, the open questions below with the decisions taken. GitHub references in full form (`wixyvir/cvestream#47`).

## Dev Notes

### Scope boundaries (read first)

- **In scope:** `pipeline/render.py`, `domain/publication.py` (`is_fresh`, `decide(now)`), `pipeline/run.py` (two call-site lines), `state/schema.py`, `state/repository.py`, migration `0003`, their tests, one recorded EUVD fixture, the docs listed in AC 10.
- **Out of scope:**
  - grouping, group GUIDs, `group_by_product`, `publication.group_guid` and the per-member one-liner (story 5.2, wave 14). Do not add a `group_guid` column "while at it": 5.2 owns revision `0004`;
  - `cycle.summary` / `held_back` (story 5.3) and duplicate product names (story 5.4);
  - any change to the config contract (`config/models.py`, `schemas/config-v1.json`): none in this story;
  - a "public exploit exists" marker: only KEV data exists, so 🔥 means "listed in CISA KEV or EU KEV" (proposal, canvas entry 4);
  - tables or Slack markup in the body (Slack shows the HTML as plain text and table cells run together);
  - truncating the description on the server (AC 1.3 forbids it);
  - backfilling `is_fresh` on existing rows (they stay `NULL`).

### Wave 13: parallel work with stories 5.3 and 5.4

- **5.1 owns the only Alembic revision of wave 13** (`0003_add_publication_is_fresh.py`). Stories 5.3 and 5.4 add no migration. Story 5.2 (wave 14) adds `0004` with `down_revision = "0003"`, so it must not start before this story is merged.
- Stories 5.3 (`held_back` in `cycle.summary`, wixyvir/cvestream#47) and 5.4 (duplicate product names, wixyvir/cvestream#48) run in parallel on their own branches, each with its own draft PR. All three branches share the base commit `3c08cdd docs(epic-5): add epic 5 by course correction`.
- Expected conflicts:
  - `docs/operations.md`: 5.3 edits the `cycle.summary` fields (around line 125); this story edits the "Feeds" section (lines 167 to 171). Different hunks.
  - `CHANGELOG.md`: all three add entries under `[Unreleased]`. Resolve by keeping every entry.
  - `README.md`: possibly.
  - `pipeline/run.py` and `tests/unit/pipeline/test_run.py`: 5.3 changes the code around `match()` in the same loop where this story passes `now` to `decide()`. Small, but adjacent.
  - `sprint-status.yaml`: each story flips its own line; adjacent lines can conflict textually. Keep every story's status.
- Whichever PR merges second rebases on `main` and re-runs every gate. Never merge `uv.lock` by hand (no dependency change is expected here anyway).

### Short items: investigation (Task 1)

Evidence from the proposal: Tomcat posts were cut at about 495 characters **inside** the second `<p>` block (the facts block, at `Affected:`); short PHP posts (about 208 visible characters) showed the IDs block and the facts block in full, and **no** `Description:` line, although it would have fitted.

Today's body is three or more `<p>` blocks: `<p>IDs</p>`, `<p>Published … Affected</p>` (with `<br/>`), `<p>Description</p>`, `<p>Sources</p>`, then the references list.

- **H1 (leading):** Slack's RSS app keeps only the first two `<p>` blocks (or the first two block-level elements). It fits both observations.
- **H2:** Slack keeps only the first N lines (a line per `<br/>` or block).
- **H3:** Slack drops a block after a block that ends with a long line, or the description block is missing from the feed itself (check the XML first: is the description empty, `unknown`, or absent?).
- **H4:** the items were CERT-EU-only or KEV-only at the time, with no description in any source. Check the vulnerability's sources at the post time.

The recommended layout (below) puts the facts line, `Matched:` and `Description:` in the **first** `<p>` block, as lines 1 to 3. That fixes H1 and H2 at once and costs nothing if H3 or H4 is the cause. If the evidence points to H3 or H4, record it in `deferred-work.md` (H4 is not a render bug). There is no way to test Slack in CI: pin the layout (first block holds facts, Matched, Description) with a unit test and describe the evidence in the Dev Agent Record.

### Render design (`src/cvestream/pipeline/render.py`)

**Budget constant.** A public module constant, for example `SLACK_PREVIEW_CHARS = 495`, with a comment: measured, not documented by Slack (492 to 498 on three posts, 2026-11-21); a later change is a one-line fix. The test imports it; `docs/operations.md` names it.

**Characters.** Write the markers and the separator with `\N{...}` escapes, as the module already does for awkward characters: `"\N{FIRE}"` (U+1F525), `"\N{LARGE RED CIRCLE}"` (U+1F534), `"\N{UP-POINTING RED TRIANGLE}"` (U+1F53A), `"\N{SQUARED NEW}"` (U+1F195), `"\N{MIDDLE DOT}"` (U+00B7). ruff runs `RUF` rules (`RUF001` flags ambiguous characters in strings, and the middle dot can be one of them); the escapes avoid the question. All of these pass `_FORBIDDEN` (astral and Latin-1 ranges are allowed).

**Proposed HTML layout** (the line breaks are part of the contract: `<br/>` and block ends are followed by `\n`, so a reader's line break counts as one character):

```html
<p>critical · CVSS 9.1 · exploited: no · published 2026-11-19 · updated 2026-11-20<br/>
<b>Matched:</b> tomcat<br/>
<b>Description:</b> Reliance on obfuscation … (full text)</p>
<p><b>Affected:</b> Apache Software Foundation Apache Tomcat 10.1.0-M1 ≤10.1.61, 11.0.0-M1 ≤11.0.27, 7.0.100 ≤7.0.109 (+2 more)</p>
<p><b>IDs:</b> CVE-2026-95517, EUVD-2026-130760<br/>
<b>CVSS:</b> 9.1 (CVSS 3.1, euvd)</p>
<p><b>Sources:</b> <a href="https://euvd.enisa.europa.eu/vulnerability/EUVD-2026-130760">euvd</a></p>
<p><b>References:</b></p>
<ul>…</ul>
```

For a known-exploited vulnerability, a `<b>Known exploited:</b> CISA KEV (added 2026-10-24, ransomware use: known), EU KEV (added unknown, ransomware use: unknown)` line follows `CVSS:` in the IDs block (the facts line already says `exploited: yes`, so drop the `yes, ` prefix of today's `_kev()` output). The facts line has no label; whether it is wrapped in `<b>` is your choice (Slack drops formatting).

**Facts line (`_facts(entry)`).** From the **current** merged data, like the rest of the body (the title keeps the decision-time band):
- band: `band_of(max_score(vulnerability.scores)).value`;
- `CVSS <score:.1f>`, or `CVSS unknown`; when `PassThrough.SCORE_UNKNOWN` is in `entry.labels`, keep today's hint: `CVSS unknown (no score; let through)` (open question 2);
- `exploited: yes|no` from `vulnerability.is_known_exploited` (never unknown: KEV presence is a fact);
- `published <_day(published_at)>`;
- ` · updated <_day(updated_at)>` unless `_day(updated_at) == _day(published_at)`. Comparing the rendered strings also drops `updated unknown` when both are unknown (open question 3).

**Matched line.** `_matched(entry)` unchanged (decision-time entry names, `(versions unknown)` suffix).

**Description.** `_summary()` unchanged: the first description, one line, never truncated. Add a test with a description longer than the budget that appears in full.

**Compact Affected (`_affected(vulnerability)`).** Group `vulnerability.affected` rows by label, in first-seen order:
- label = `_product_label(affected.product, affected.vendor)` (the title rule: the vendor is dropped when the product name already contains it, so `Apache Software Foundation Apache Tomcat` stays whole, and `PHP Group PHP` stays whole; `wild-apricot Wild Apricot Login` becomes `Wild Apricot Login`);
- versions = the distinct `_one_line(version_text)` values of that label's rows, in first-seen order. `_one_line` only removes forbidden characters and collapses whitespace: that is display hygiene, not parsing. Never call `domain/version.py` here;
- rendering: `<label> <v1>, <v2>, <v3> (+N more)` where N is the number of distinct version texts beyond 3; without `(+N more)` when there are 3 or fewer;
- a label whose rows have no version text at all reads `<label> versions unknown`; a row without version text next to rows with one adds nothing (open question 4);
- labels joined with `; `; no rows at all reads `unknown`;
- CERT-EU prose rows (`AffectedProduct(product=<prose>)`, no vendor, no version) keep today's rendering, `<prose> versions unknown`, so `test_the_body_still_lists_the_cert_eu_affected_text` still holds.

Today `_affected()` repeats the vendor before every row (`Apache Software Foundation Apache Tomcat 10.1.0-M1 ≤10.1.61; Apache Software Foundation Apache Tomcat 11.0.0-M1 ≤11.0.27; …`) and deduplicates only identical `name + versions` strings. That repetition is what used the Tomcat budget.

**Title markers (`_markers(entry) -> str`).** `FeedEntry` gains `is_fresh: bool | None = None` (last field, so current constructions keep working). Markers, in order: `\N{FIRE}` if `entry.is_known_exploited`; `\N{LARGE RED CIRCLE}` if `entry.band is Band.CRITICAL`; `\N{UP-POINTING RED TRIANGLE}` if `entry.kind.startswith("band-")`; `\N{SQUARED NEW}` if `entry.is_fresh is True`. `_title()` returns `f"{markers} {text}"` when there is at least one marker, `text` otherwise. Every value comes from `FeedEntry`'s decision-time fields; never from the clock and never from `entry.vulnerability`.

**What must not change:** GUID (read from storage), `pubDate`, `link`, `lastBuildDate`, item order, the channel elements, escaping (`_h` on every value, `_safe_url` for links), `_title_product()`, the `_PROSE_SOURCES` rule, the `References` list and its `and N more` cap, the "Unknown is `unknown`" rule.

### Freshness design (`src/cvestream/domain/publication.py`)

```python
def _is_fresh(vulnerability: Vulnerability, kind: str, now: datetime) -> bool:
    # Sources give day-level dates: a calendar day, not "the last 24 hours".
    published = vulnerability.published_at
    return (
        kind == NEW
        and published is not None
        and published.astimezone(UTC).date() == now.astimezone(UTC).date()
    )
```

- `decide(match, vulnerability, history, now)`: `now` is the run's `clock.now()`, the same value `run()` stores as `published_at`. The domain stays pure: it receives the time, it does not read a clock (`tests/unit/domain/test_purity.py`).
- `Vulnerability.published_at` is already the earliest publication any source gives (`domain/model.py`); do not recompute it.
- `kev` and `band-*` decisions are never fresh, even on the publication day.
- A backfilled `new` decision (new endpoint) is fresh only if the vulnerability was first published that day, like any other `new` decision.
- `PublicationDecision.is_fresh: bool = False` as the last field keeps the hand-built decisions in `tests/unit/state/test_repository.py`, `tests/unit/pipeline/test_summary.py` and `tests/unit/pipeline/test_correlate.py` valid.

### State (`state/schema.py`, `state/repository.py`, migration `0003`)

- Column `is_fresh`, `Boolean`, `nullable=True`, on `publication`, after `is_known_exploited`. Naming rule `is_*` respected. Portable type, no dialect SQL.
- `NULL` means "row older than the column", read as "no 🆕". `StoredPublication.is_fresh: bool | None`; `FeedEntry.is_fresh: bool | None`. The renderer tests `is True`.
- Migration file `0003_add_publication_is_fresh.py`: `batch_alter_table` for both directions (`render_as_batch=True` is set in `env.py`; SQLAlchemy 2.x `Boolean` creates no CHECK constraint by default, so `compare_metadata` stays empty). Never edit `0001` or `0002`.
- `tests/unit/state/test_db.py` hard-codes the head revision `"0002"` in several tests (`test_migrate_creates_the_schema_at_head`, `test_second_migrate_is_a_no_op`, `test_migrating_from_0001_keeps_the_existing_rows`): change them to `"0003"`. Add `test_the_is_fresh_migration_keeps_rows_and_can_be_downgraded` next to `test_the_feed_cache_migration_can_be_downgraded`, reusing the `_upgrade_to(state, revision)` helper of that file: migrate to `0002`, insert a publication row, upgrade to head, read `is_fresh` as `None`, downgrade to `0002`, check the column is gone and the row is still there.
- The write stays inside `State.transaction()` opened by `pipeline/run.py`; no new transaction anywhere.

### Tomcat fixture

Recorded and checked on 2026-11-21 (this story's analysis): `curl "https://euvdservices.enisa.europa.eu/api/search?text=CVE-2026-95517&size=10&page=0"` returns `total: 1`, `EUVD-2026-130760`, aliases `CVE-2026-95517` and `GHSA-mqvp-jwc5-xgv2`, `baseScore` 9.1 (`3.1`), `datePublished` `Nov 19, 2026, 9:23:22 PM`, `dateUpdated` `Nov 20, 2026, 6:42:41 PM`, no `exploitedSince`, a 772-character description, and five `Apache Software Foundation / Apache Tomcat` rows: `10.1.0-M1 ≤10.1.61`, `11.0.0-M1 ≤11.0.27`, `7.0.100 ≤7.0.109`, `9.0.13 ≤9.0.123`, `8.5.38 ≤8.5.100`.

- Record it as `tests/fixtures/euvd/euvd_tomcat_five_affected_rows.json` with `| jq .`, unedited (fixtures are recorded, never hand-written). Behind the TLS-intercepting proxy, add `--cacert ~/ca.pem` in your own shell only (the README rule).
- Add its row to `tests/fixtures/README.md` (`Recorded on 2026-11-21: curl "$B/search?text=CVE-2026-95517&size=10&page=0" | jq .` …, what it shows).
- Build the `Vulnerability` the way `tests/contract/euvd/test_euvd.py` does: serve the fixture with respx, `await adapter.fetch(...)` with the `euvd` adapter, then `normalize` + `build_vulnerability` on the records (that test imports both). The render test can do this once in a module-level helper (anyio test or a small `anyio.run` wrapper; follow whatever the contract test uses), or put the budget test in `tests/integration/` if it reads better there. Then render a `FeedEntry` for an endpoint with a `tomcat` entry.
- Expected Affected line: `Apache Software Foundation Apache Tomcat 10.1.0-M1 ≤10.1.61, 11.0.0-M1 ≤11.0.27, 7.0.100 ≤7.0.109 (+2 more)`.
- The golden title `🔥🔴🆕 … EXPLOITED` (AC 5) is a title test built from decision-time values (`is_known_exploited=True`, `band=CRITICAL`, `is_fresh=True`); the body of the same fixture says `exploited: no` because the body follows current data. Keep the two tests separate so this does not look like a contradiction.

### Testing requirements

**Plain-text reduction (AC 3).** One helper in `tests/unit/pipeline/test_render.py`: `html.unescape(_TAG.sub("", description))`, keeping every `\n` (do not drop empty lines, unlike `_lines()`), then `[:SLACK_PREVIEW_CHARS]`.

`tests/unit/pipeline/test_render.py`:
- `test_the_full_description_one_fact_per_line` and `test_the_description_is_an_html_fragment`: rewrite for the new order (they are the golden body tests).
- `test_unknown_everywhere_data_is_missing`: new expected lines, starting `unknown · CVSS unknown · exploited: no · published unknown`, no `Known exploited` line.
- `test_unknown_score_that_was_let_through_is_said`: now on the facts line.
- `test_the_severity_is_recomputed_from_current_scores`, `test_a_score_is_shown_with_one_decimal`: follow the facts line.
- New: `updated` left out on the same UTC day (including a non-UTC `updated_at` that lands on the same UTC day), kept on another day; compact Affected (one label, 3 versions, `(+N more)`, vendor dropping, several sources merged under one label, duplicates removed, versions-unknown rule, CERT-EU prose); no KEV line when not exploited, KEV detail line when exploited; the first `<p>` holds facts, Matched and Description; a description longer than the budget appears in full; the Tomcat budget test.
- Titles: the four golden titles; each marker alone; all four together in order; `is_fresh=None` and `False` give no 🆕; no marker means no leading space; the existing `EXPLOITED` cases of `test_titles` start with the fire marker; `band-high` gets 🔺 but not 🔴; `band-critical` gets both; a `critical` `new` item that is not exploited or fresh gets only 🔴.
- Escaping tests (`test_markup_in_source_text_is_escaped_text`, `test_markup_in_product_names_is_escaped_in_the_title_and_body`, …) must keep passing; adjust only their expected positions if they assert a full line.

`tests/unit/domain/test_publication.py`: add a fixed `NOW` and pass it to every `decide()` call. New cases: published the same UTC day (00:00 and 23:59 edges) is fresh; the day before is not; a non-UTC `published_at` compared in UTC; unknown `published_at` is not fresh; `kev` and `band-*` decisions on the publication day are not fresh. `tests/unit/domain/conftest.py`: add `published_at: datetime | None = None` to `make_vulnerability`.

`tests/unit/pipeline/test_run.py`: a stored row carries `is_fresh`; a feed item for a vulnerability published on the run's day has 🆕; the clock-free rerun (AC 7): run at 23:50 UTC, advance 20 minutes past midnight, rerun with no new data, same XML bytes, same ETag, same `changed_at`. Its `_euvd()` helper sets no `published_at`, so existing title assertions stay as they are.

Guardrails: `tests/integration/test_restart_no_repost.py` unchanged and green; `tests/unit/domain/test_purity.py` green.

### Documentation edits

- `docs/operations.md`, "Feeds" (current lines 168 to 171):
  - title bullet: add the markers with their meaning (🔥 in CISA KEV or EU KEV, not "a public exploit exists"; 🔴 critical; 🔺 severity raised; 🆕 first published the day cvestream published the item), their order, and that they never replace the words; keep the examples and add the golden ones;
  - body bullet: the new order, the compact Affected line, the KEV detail only when known-exploited;
  - new bullet: Slack's RSS app shows about the first 495 characters of the body as plain text (`SLACK_PREVIEW_CHARS` in `pipeline/render.py`), so the facts line, the matched product and the start of the description come first;
  - `pubDate` bullet: "its real age under `Published`" becomes "its real age in the facts line (`published …`)";
  - one golden plain-text example (Tomcat, shortened description with `…`).
- `README.md`: one clause in the intro (title emoji). Nothing else describes the item format.
- `CHANGELOG.md` `[Unreleased]`: `### Changed` (body order, compact Affected, title markers, ETag changes once, no re-post) and the migration note.
- `pipeline/render.py` and `domain/publication.py` module docstrings.

### Planning docs (Task 6)

Already written by the epic commit `3c08cdd`, so normally nothing to do:
- `architecture.md`, "Feed cache" rules (story 1.6): the markers and their order, the 495-character window, the body order and the compact Affected rule.
- `architecture.md`, "Correlation and publication rules" (story 1.5): "🆕 freshness (story 5.1)".
- `architecture.md`, "Requirements to Structure Mapping": `FR-18 title markers, compact body`.
- `project-context.md`, "Publish once": "Title markers come from decision-time values only …".
- PRD FR-18 and `epics.md` FR-18 are amended.

Amend only what the implementation changes (for example, a rule found by Task 1 such as "the facts, Matched and Description share the first paragraph because Slack keeps only the first two").

### Existing files this story touches (current state → change → preserve)

| File | Current state | Change | Preserve |
|---|---|---|---|
| `src/cvestream/pipeline/render.py` | body: IDs, then Published … Affected, Description, Sources, References; title without markers; `_affected()` repeats the vendor per row | facts line, new order, compact Affected, KEV line only when exploited, `_markers()`, `FeedEntry.is_fresh`, budget constant | purity (no clock, no I/O), escaping, `_title_product()`, GUID/pubDate/link/lastBuildDate, `etag_of()` |
| `src/cvestream/domain/publication.py` | `decide(match, vulnerability, history)`, no time input | `now` parameter, `is_fresh` field and helper | GUID construction site, `_kind()` rules, purity |
| `src/cvestream/pipeline/run.py` | `decide(matched, vulnerability, history)`; `FeedEntry(...)` without `is_fresh` | pass `now`; pass `p.is_fresh` | one transaction, `publication_guid_exists` safety net, render of every enabled endpoint |
| `src/cvestream/state/schema.py` | `publication` without `is_fresh` | nullable `Boolean` column | naming convention, other columns |
| `src/cvestream/state/repository.py` | `add_publication`, `StoredPublication`, `publications_since` | write, map and select `is_fresh` | `publication_history`, `PastPublication`, `merge_vulnerabilities` |
| `src/cvestream/state/migrations/versions/` | `0001`, `0002` | new `0003_add_publication_is_fresh.py` | never edit `0001`/`0002` |
| `tests/integration/test_pipeline_end_to_end.py` | asserts `<p><b>IDs:</b>` on line 0 and `<b>Published:</b>`/`<b>Updated:</b>`/`<b>Known exploited:</b>` lines | assert the facts line first and the IDs block later | the rest of the end-to-end flow |

### Architecture compliance

- `domain` stays pure: the decision time is a parameter; stdlib `datetime.UTC` only.
- Time is aware UTC from the injected `Clock` (only `run()` reads it); render reads no clock (AC 7).
- GUIDs are untouched and still built only in `domain/publication.py`; the renderer reads stored values.
- State is written only inside `State.transaction()`, opened only by `pipeline/run.py`.
- Portable column type, naming convention, Alembic batch mode, a new revision rather than an edited one.
- Unknown is `None` (`published_at`, `is_fresh` on old rows) and displays `unknown` (facts line).
- Free-text versions are never parsed outside `domain/version.py`: the Affected line shows source text verbatim.
- RSS output still built with `ElementTree`; source text escaped; no Slack-specific markup (emoji are plain Unicode text, not markup).
- Conventional Commits with `story-5.1`, one concern per commit, never squashed.

### Library / framework requirements

No new dependency. stdlib `html`, `re`, `datetime` (`UTC`), `xml.etree.ElementTree`; SQLAlchemy 2.1 Core `Boolean`; Alembic 1.20 `op.batch_alter_table`. Use `uv run` for everything, never pip or poetry.

### Previous story intelligence

- Story 4.2 (closest precedent in time): stories in this repo are judged in review on pinned trade-offs and on documentation following behaviour in the same PR; a review round can add commits (`fix(story-…)`, `docs(story-…)`, `chore(story-…): record the review fixes`). Keep the Dev Agent Record accurate (real commit hashes).
- Story 4.2 added a field as the **last** field of a frozen domain dataclass so that existing constructions keep working; do the same for `PublicationDecision.is_fresh` and `FeedEntry.is_fresh`.
- Story 1.6 created `render.py` and `feed_cache` (migration `0002`) and wrote the ETag and `changed_at` rules that AC 7 relies on; its tests in `test_run.py` (`test_an_unchanged_rerun_keeps_the_etag_and_moves_the_render_time`) are the model for the midnight test.
- Story 2.1 made CERT-EU affected rows prose (`_PROSE_SOURCES`): they never name the title, and they keep their `versions unknown` rendering in the body.
- Story 1.7 / 4.2 docs style: short bullets in `docs/operations.md`, examples in backticks, no Slack markup.

### Git intelligence

- `main` is at `8d9590a` (merge of wixyvir/cvestream#46, story 4.2). The branch adds `3c08cdd docs(epic-5): add epic 5 by course correction`, shared with the 5.3 and 5.4 branches; it disappears from the diff once one of them merges.
- The last changes to `render.py` were story 4.2 (one title test, no code) and story 2.1 (`_PROSE_SOURCES`). The last migration is `0002` (story 1.6). No open branch touches `render.py` or the state schema apart from this story.

### Latest technical information

No external API or library change is involved. The EUVD search endpoint and the Tomcat record were checked live on 2026-11-21 (see "Tomcat fixture"). Unicode names were checked with `unicodedata` (Python 3.14): `FIRE`, `LARGE RED CIRCLE`, `UP-POINTING RED TRIANGLE`, `SQUARED NEW`, `MIDDLE DOT`. The Slack truncation (about 495 characters) is a measurement, not a documented limit; it is pinned in one constant.

### Open questions (decided by default, to confirm in review)

1. **Short items without a description (AC 8).** The cause is unknown until Task 1. Default: adopt the "facts, Matched and Description in the first `<p>`" layout, which fixes the paragraph and line hypotheses; record anything else in `deferred-work.md`. Needs the Slack post text of 2026-11-19 from Cyprien if the local replay is not conclusive.
2. **`(no score; let through)` hint.** The old `Severity:` line said it for score pass-throughs (FR-25 transparency). Default: keep it on the facts line as `CVSS unknown (no score; let through)`. Alternative: drop it to save about 24 characters.
3. **Both dates unknown.** Default: the facts line reads `published unknown` and leaves `updated` out (the rendered days are equal). Alternative: always show `updated unknown` when unknown.
4. **A row without version text next to rows with versions.** Default: the label shows only the known version texts; `versions unknown` appears only when no row of that label has one. Alternative: add `versions unknown` as one more value.
5. **Grouping key of the Affected line.** Default: the exact label string from `_product_label()` (so `Apache Tomcat` from one source and `Apache Software Foundation Apache Tomcat` from another stay two labels). Alternative: a case-folded key. Measured data shows one vendor/product spelling per EUVD record, so the default is enough for now.
6. **Facts line styling.** Default: no label and no bold (Slack shows plain text; RSS readers show a plain first line). Bold is acceptable if review prefers it.
7. **Epic ordering.** Epic 5 starts while epic 3 is still `backlog`, as epic 4 did (sprint change proposal, item 2.5). Noted, not resolved here.

### Project Structure Notes

- One new file in `src/`: the migration `0003_add_publication_is_fresh.py` (named `NNNN_<verb>_<object>.py`). One new fixture in `tests/fixtures/euvd/`. No new module or package; helpers stay private in `render.py` and `publication.py` (no `utils.py`).
- Tests go where the tree says: `tests/unit/pipeline/`, `tests/unit/domain/`, `tests/unit/state/`, `tests/integration/`.
- No conflict with the unified structure detected.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.1: Compact item body and title markers]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-21-epic-5-readable-bursts.md] (sections 1, 2 "Technical impact", 4.3, 4.4, 5)
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Architecture] ("Feed cache" rules of story 1.6, "🆕 freshness (story 5.1)"), [#Naming Patterns] (database naming, migrations), [#Requirements to Structure Mapping] (FR-18)
- [Source: _bmad-output/project-context.md#Critical Don't-Miss Rules] ("Publish once", title markers rule), [#Framework & Architecture Rules] (State)
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-18 amended)
- [Source: _bmad-output/implementation-artifacts/sprint-status.yaml] (wave 13 caveats: shared docs, 5.1 owns the only migration)
- [Source: src/cvestream/pipeline/render.py] (`_title`, `_description`, `_affected`, `_kev`, `_product_label`, `_day`, `_FORBIDDEN`), [src/cvestream/domain/publication.py] (`decide`, `PublicationDecision`), [src/cvestream/pipeline/run.py] (`run`), [src/cvestream/state/schema.py] (`publication`), [src/cvestream/state/repository.py] (`add_publication`, `StoredPublication`, `publications_since`), [src/cvestream/state/migrations/versions/0002_add_feed_cache.py]
- [Source: tests/unit/pipeline/test_render.py], [tests/unit/state/test_db.py], [tests/integration/test_pipeline_end_to_end.py], [tests/fixtures/README.md]
- [Source: docs/operations.md#Feeds], [CHANGELOG.md], [README.md], [AGENTS.md] (gate commands)

## Dev Agent Record

### Agent Model Used

- Mistral Vibe (vibe@mistral.ai), BMAD dev-story workflow, 2026-11-21, single session.

### Debug Log References

- Task 1 replay: `CVESTREAM_DATA_DIR=/tmp/cv-task1/data CVESTREAM_ENDPOINTS_FILE=/tmp/cv-task1/endpoints.yaml CVESTREAM_SOURCES__CISA_KEV__ENABLED=false CVESTREAM_SOURCES__CERT_EU__ENABLED=false uv run cvestream poll --once` (throwaway php endpoint, EUVD only, 14-day window; scratch config and data never committed). 36 items rendered; per-`<p>` plain-text counts measured with the AC 3 reduction.
- Tomcat fixture recorded live: `curl --cacert ~/ca.pem "https://euvdservices.enisa.europa.eu/api/search?text=CVE-2026-95517&size=10&page=0" | jq .` (200, total 1, EUVD-2026-130760, baseScore 9.1/3.1, 772-char description, 5 Apache Tomcat rows) — matches the story's analysis exactly.

### Completion Notes List

- Story context created by the create-story workflow (2026-11-21): ultimate context engine analysis completed, comprehensive developer guide created from epic 5, the sprint change proposal of 2026-11-21, the architecture, the project context, the current code and a live check of the Tomcat EUVD record.
- **Task 1 finding (AC 8):** H1 confirmed as the cause — Slack's RSS app keeps only the first block-level elements. Evidence from the local replay (2026-11-21): every one of the 36 php items carries a non-empty `Description:` in the **third** `<p>` block (H4 refuted: the description is in the feed; the shortest item, CVE-2026-128717, is 480 plain chars with blocks 38/155/177/13/11). Under H1 the app shows the first two blocks (429 chars) and **no** `Description:` line even though the whole body fits the ~495 budget — exactly the 2026-11-19 symptom. H3 (missing block) is refuted by the same XML; H2 (first N lines) is indistinguishable and fixed by the same layout. Fix: the Task 3 layout puts facts, `Matched:` and `Description:` in the first `<p>`, pinned by `test_the_first_paragraph_holds_facts_matched_and_description`. No `deferred-work.md` entry needed (cause determined and fixed in render). The Slack post text from Cyprien was not needed: the local replay was conclusive. Recorded in architecture.md / project-context.md (Task 6).
- **Task 2:** `decide(match, vulnerability, history, now)` (required `now`, after `history`); `_is_fresh()` compares UTC calendar days; `PublicationDecision.is_fresh: bool = False` as last field; stored in nullable `publication.is_fresh` via Alembic `0003` (wave 13's only migration; upgrade/downgrade tested, old rows `NULL`). Commit `4c2f4cd`.
- **Task 3:** body reorder + compact Affected + `SLACK_PREVIEW_CHARS = 495` + KEV detail only when exploited; Tomcat fixture `euvd_tomcat_five_affected_rows.json` recorded live and pinned in the budget test. Commit `9fb5b24`.
- **Task 4:** `_markers()` (🔥 🔴 🔺 🆕, decision-time values only) prefixes `_title()`; `FeedEntry.is_fresh: bool | None = None`; four golden titles pinned; AC 7 midnight-rerun test keeps XML/ETag/`changed_at`. Commit `b15e283`.
- **Task 5:** `docs/operations.md` Feeds section rewritten (markers + goldens, body order, compact Affected, KEV-only-when-exploited, Slack window bullet with golden plain-text example), README intro clause, CHANGELOG `[Unreleased]` `### Changed`. Commit `fbb8c79`.
- **Task 6:** architecture.md / project-context.md amended with the Task 1 paragraph rule only; everything else matched. Commit `a8bcfcb`.
- **Open questions, decisions taken:** (1) short items without a description → cause = first-two-blocks rule (H1), fixed by the first-`<p>` layout, pinned by test; (2) keep `CVSS unknown (no score; let through)` on the facts line; (3) `updated` left out when both days are unknown (rendered days equal); (4) a row without version text next to rows with one adds nothing — `versions unknown` only when no row of the label has one; (5) Affected grouping key = the exact `_product_label()` string; (6) facts line unlabelled, not bold; (7) noted, not resolved here.
- **Gates (all run in the worktree, 2026-11-21):** `uv sync --locked` OK · `ruff check` OK · `ruff format --check` OK (129 files) · `ty check` OK · `lint-imports` 7 kept, 0 broken · `pytest` 1253 passed, 6 deselected · `cvestream config schema --check` OK · `cvestream config validate endpoints.example.yaml` valid · `docker build --target runtime -t cvestream:local .` **OK locally** (no ghcr.io x509 error this run; the corporate CA is in the system trust store), image `cvestream:local` built. CI remains the authoritative check.
- No HALT condition was hit. Deviation from the story file: none in scope; `test_titles`' `band-high` cases now also carry 🔺 (AC 4 / testing requirements require 🔺 for every `band-*` kind, which the "only the known-exploited ones change" note did not mention).

### File List

- `src/cvestream/domain/publication.py` — `decide(..., now)`, `_is_fresh()`, `PublicationDecision.is_fresh` (last field), docstring.
- `src/cvestream/pipeline/run.py` — pass `now` to `decide()`; `FeedEntry(..., is_fresh=p.is_fresh)`.
- `src/cvestream/pipeline/render.py` — `SLACK_PREVIEW_CHARS`, `_facts()`, rewritten `_affected()` (compact labels), `_kev()` without the `yes, ` prefix (only shown when exploited), rewritten `_description()` (first `<p>` = facts + Matched + Description), `FeedEntry.is_fresh`, `_markers()`, `_title()` prefix, docstring.
- `src/cvestream/state/schema.py` — nullable `publication.is_fresh` after `is_known_exploited`.
- `src/cvestream/state/repository.py` — `add_publication()` writes `is_fresh`; `StoredPublication.is_fresh: bool | None`; `publications_since()` selects/maps it.
- `src/cvestream/state/migrations/versions/0003_add_publication_is_fresh.py` — new revision `0003` (batch add/drop column).
- `tests/unit/domain/test_publication.py` — `NOW` passed to every `decide()`; 7 freshness cases.
- `tests/unit/domain/conftest.py` — `make_vulnerability(published_at=...)`.
- `tests/unit/state/test_db.py` — head `0003`; `test_the_is_fresh_migration_keeps_rows_and_can_be_downgraded`.
- `tests/unit/state/test_repository.py` — `is_fresh` round trip (True / False / NULL).
- `tests/unit/cli/test_db.py` — head revision assertions `0003`.
- `tests/unit/pipeline/test_render.py` — rewritten golden body tests, facts-line/compact-Affected/KEV/first-`<p>`/budget tests, Tomcat fixture budget test, golden titles and marker tests.
- `tests/unit/pipeline/test_run.py` — `_euvd(published_ago=...)`, stored `is_fresh`, 🆕 title, AC 7 midnight rerun; body assertion updated.
- `tests/integration/test_pipeline_end_to_end.py` — first block = facts/Matched/Description, IDs block later, no KEV line when not exploited.
- `tests/integration/test_cert_eu_php2shell.py` — 🔴🔺 on the band-critical update titles.
- `tests/fixtures/euvd/euvd_tomcat_five_affected_rows.json` — new recorded fixture (2026-11-21).
- `tests/fixtures/README.md` — fixture row.
- `docs/operations.md`, `README.md`, `CHANGELOG.md` — item format and markers documented.
- `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md` — Task 1 paragraph rule.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 5-1 → review.

## Change Log

- 2026-11-21: implemented story 5.1 end to end (tasks 1-6), all gates green, status set to review. Commits: `4c2f4cd` (freshness domain+state), `9fb5b24` (body reorder + budget), `b15e283` (title markers), `fbb8c79` (user docs), `a8bcfcb` (planning-doc amendment).
