# Sprint Change Proposal: epic 2 scope (CERT-EU and drift detection only)

- **Date:** 2026-11-01
- **Trigger:** replay of PHP2Shell (CVE-2026-109618, CVE-2026-109619) against the Epic 1 implementation, run during sprint status review
- **Decision:** keep Epic 2, reduced to the CERT-EU adapter (FR-2) and nightly source-drift detection (NFR-5). Defer the CERT-FR alerte adapter (FR-2b) and configured generic sources (FR-5) after v1.
- **Scope:** Moderate (backlog reorganization), executed by the Developer agent
- **Mode:** batch

## TL;DR

- **Recall is fine without new sources.** EUVD lists both PHP2Shell CVEs with the structured product `PHP`; the real EUVD adapter and matcher match them for every `php` endpoint shape (plain, `min_version`, `min_cvss`, `match_description: false`).
- **Timeliness is not.** EUVD published the CVEs on 2026-10-21 at 15:48 CEST; the CERT-EU 2026-018 link was posted by hand at 08:27 CEST (v1.0 of the advisory dates from 2026-10-20). cvestream would post ~7.5 h after the hand post, which fails SM-3.
- **CERT-EU closes that gap; CERT-FR alerte does not add to it.** ALE-014 arrived at 13:27 CEST, five hours after the CERT-EU hand post. Generic sources (FR-5) have no requesting user yet.
- **Story 2.1 becomes "CERT-EU adapter"** and gains one design rule: an advisory emits **one source record per CVE** (`2026-018:CVE-…`), because 2026-018 lists three CVEs and one record carrying all three aliases would fuse three vulnerabilities into one feed item.
- **Story 2.2 becomes "Source-drift detection"** (the nightly `live` workflow only).
- FR-2b and FR-5 stay in the PRD with their IDs, marked deferred. Side finding (PHP's `8.2.* <8.2.35` version format does not parse) goes to a separate GitHub issue, outside this proposal.

## 1. Issue summary

**Problem.** Epic 2 bundles four items of very different value: CERT-EU (FR-2), CERT-FR alertes (FR-2b, optional, off by default), configured generic RSS/JSON sources (FR-5) and drift detection (NFR-5). The question was whether Epic 2 is needed at all now that Epic 1 is done.

**Category:** strategic scope review (MVP trimming), with a design gap found on the way.

**Evidence** (collected 2026-11-01):

| When (Paris time) | Event | Source |
|---|---|---|
| 2026-10-20 | CERT-EU 2026-018 v1.0, pre-announcement (no technical details) | CERT-EU JSON, `content_markdown` history |
| 10-21 08:27 | Hand post of the CERT-EU link in #theme-security, with both CVE IDs and CVSS 9.2 | Slack |
| 10-21 13:27 | CERT-FR alerte CERTFR-2026-ALE-014 posted by the RSS bot | Slack |
| 10-21 15:48 | CVE published in EUVD, product `PHP`, CVSS 9.2 (`datePublished`, UTC) | EUVD `/api/enisaid` |
| 10-21 ~16:00–17:00 | cvestream would post it (EUVD + up to 60 min polling + unknown EUVD indexing lag) | estimate |
| 10-21 16:12 | php.net link posted by hand | Slack |
| 10-21 21:02 | cvefeed relays it | Slack |
| 10-26 | KEV listing (`exploitedSince`), cvestream would post an `[UPDATE]` | EUVD |

Matcher replay: `EUVD-2026-107260` and `EUVD-2026-107265` through `sources/euvd.py::_record` → `correlate.build_vulnerability` → `domain/matcher.match` give a structured match for `php`, `php >= 8.2`, `php >= 8.4 + min_cvss 7` and `php` with `match_description: false`.

**CERT-EU payload facts** (2026-018 JSON, live):

- No structured CVE or product fields. CVE IDs appear only in `content_markdown` (three: 109618, 109619 and 115560, the last one added by v1.2 on 10-23). Affected products are bullets under `# Affected Products` ("PHP 8.5 versions prior to 8.5.12").
- `publish_date` (`23-10-2026 14:36:26`) is the **last revision** in UTC (RSS shows 16:36:26 CEST). The first publication is `original_date` in the markdown front matter (`2026-10-20`, date only).
- The RSS lists the latest 10 advisories; about two a month, all critical.

**Design gap.** `pipeline/correlate.py` merges every CVE/EUVD alias of a record (`MERGE_KINDS`). A single CERT-EU record with three CVE aliases would merge three EUVD vulnerabilities into one, changing their primary ID and collapsing three feed items into one. Story 2.1 as written ("emits `SourceRecord`s with the advisory ID, CVE aliases …") does not prevent it.

## 2. Impact analysis

### Checklist

| Item | Status | Note |
|---|---|---|
| 1.1 Trigger | [x] | PHP2Shell replay against Epic 1 (no single story) |
| 1.2 Problem | [x] | Scope review; recall OK, timeliness fails SM-3 |
| 1.3 Evidence | [x] | Table above, EUVD and CERT-EU payloads, Slack timestamps |
| 2.1 Current epic | [x] | Epic 1 is done; unaffected |
| 2.2 Epic changes | [!] | Epic 2 reduced; stories 2.1 and 2.2 rewritten |
| 2.3 Future epics | [x] | Epic 3 unaffected (no dependency on FR-2b or FR-5) |
| 2.4 Obsolete / new epics | [x] | None; deferred items listed in `deferred-work.md` |
| 2.5 Order | [x] | Unchanged: Epic 2, then Epic 3 |
| 3.1 PRD | [!] | FR-2b, FR-5 marked deferred; Out of Scope, glossary, decision log |
| 3.2 Architecture | [!] | Source list, tree, mapping; new CERT-EU record rule; advisory-revision note |
| 3.3 UX | [N/A] | No UX document; RSS output unchanged |
| 3.4 Other | [!] | `project-context.md`, `sprint-status.yaml`, `deferred-work.md`, `README.md` |
| 4.1 Direct adjustment | Viable | Rewrite 2 stories, low effort, low risk |
| 4.2 Rollback | Not viable | Nothing to revert |
| 4.3 MVP review | Viable (partial) | FR-2b and FR-5 leave v1; core goals and SMs unchanged |
| 4.4 Path | [x] | Hybrid: MVP trim (4.3) + direct adjustment of the remaining stories (4.1) |

### Epic impact

- **Epic 2** keeps its number and its goal (close the PHP2Shell gap, SM-3) but drops FR-2b and FR-5. Two stories remain, still parallel (wave 7).
- **Epic 3** unchanged.

### Artifact impact

- **PRD:** FR-2b and FR-5 marked deferred in place (IDs kept, nothing renumbered); Out of Scope lists both; glossary describes a CERT-EU source record per CVE; decision-log entry.
- **Architecture:** F1 row, external dependencies, version-parsing concern, source adapter list, implementation sequence, tree, requirements mapping, external integrations; new CERT-EU per-CVE record rule; the "story 2.1 must make advisory revisions merge" sentence becomes a deferred note.
- **project-context.md:** source names, alias examples, the CERT-FR revision rule replaced by the CERT-EU per-CVE rule.
- **Code:** none in this proposal. Unit tests that use `CERTFR-…` IDs as generic advisory aliases (`test_identity.py`, `test_normalize.py`, `test_correlate.py`, `test_run.py`) stay valid: they test advisory-kind aliases, not an adapter.

### Technical impact

- The `publish.skipped` safety net (advisory-only GUID collision) becomes unreachable again: CERT-EU revisions keep the same `record_id`, so they update one source record instead of creating a second vulnerability. The net stays in place.
- A pre-announcement without any CVE ID would publish once as an advisory-only item, then again per CVE once the IDs appear (advisory IDs never merge). Accepted: recall over volume (FR-25), at most one extra item per advisory. 2026-018 already carried its CVE IDs when it was hand-posted.

## 3. Recommended approach

**Hybrid: trim the MVP and adjust the two remaining stories.**

- **Why keep CERT-EU:** it is the only source that would have beaten the hand post, and SM-3 is a trial success metric. Low volume (~2/month), one adapter.
- **Why keep drift detection:** the `live` contract tests for EUVD and CISA KEV already exist, but nothing runs them; EUVD has already broken older clients (NFR-5). Small CI-only story.
- **Why defer CERT-FR alerte:** adds no lead time once CERT-EU is ingested (PHP2Shell: +5 h after CERT-EU), ~14 items a year, French free-text versions, and it is the only source that needs advisory revisions to merge.
- **Why defer generic sources:** no requested source yet; largest item of the epic (mapping settings, degraded matching, contract tests). Re-enter when someone names a vendor bulletin to follow.
- **Risk:** low. Deferred items are additive later; no stored data or contract (`schemas/config-v1.json`) changes.

## 4. Detailed change proposals

### 4.1 Epics (`epics.md`)

**Requirements Inventory, FR-2b and FR-5**

OLD:
```
- FR-2b: Ingest CERT-FR *alerte* items (configurable, off by default). Each revision is identified by reference + revision number, merges into the existing vulnerability and does not trigger a re-publish by itself.
…
- FR-5: Add further RSS or JSON sources through configuration. Minimum contract: stable ID + text. Missing CVE ID → not merged; no product data → text-only matching; no score/version → unknown.
```
NEW:
```
- FR-2b: *(Deferred after v1, sprint change proposal 2026-11-01 epic 2 scope.)* Ingest CERT-FR *alerte* items (configurable, off by default). Each revision is identified by reference + revision number, merges into the existing vulnerability and does not trigger a re-publish by itself.
…
- FR-5: *(Deferred after v1, sprint change proposal 2026-11-01 epic 2 scope.)* Add further RSS or JSON sources through configuration. Minimum contract: stable ID + text. Missing CVE ID → not merged; no product data → text-only matching; no score/version → unknown.
```

**FR Coverage Map**

OLD:
```
- FR-2: Epic 2 (Story 2.1) - CERT-EU advisories
- FR-2b: Epic 2 (Story 2.1) - CERT-FR alerte revisions
…
- FR-5: Epic 2 (Story 2.2) - configured generic RSS/JSON sources
```
NEW:
```
- FR-2: Epic 2 (Story 2.1) - CERT-EU advisories, one record per CVE
- FR-2b: deferred after v1 - CERT-FR alerte revisions
…
- FR-5: deferred after v1 - configured generic RSS/JSON sources
```

**Epic List, Epic 2** (and the same description under `## Epic 2`)

OLD:
```
Feeds also carry CERT-EU advisories and (opt-in) CERT-FR alertes, merged into the same vulnerabilities, and the operator can add other RSS/JSON sources by configuration. Source API drift is detected nightly. This closes the PHP2Shell gap (SM-3: timeliness against hand-posted CERT-EU links).
**FRs covered:** FR-2, FR-2b, FR-5. NFR-5 (drift detection).
```
NEW:
```
Feeds also carry CERT-EU advisories, merged per CVE into the same vulnerabilities as EUVD, so a critical pre-announcement reaches the channel before the CVE is published (PHP2Shell: CERT-EU was ~7.5 h ahead of EUVD). Source API drift is detected nightly. This closes the PHP2Shell gap (SM-3: timeliness against hand-posted CERT-EU links).
**FRs covered:** FR-2. NFR-5 (drift detection). FR-2b and FR-5 are deferred after v1 (sprint change proposal 2026-11-01, epic 2 scope).
```

**Story 2.1** (replaces the whole story)

```
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
```

**Story 2.2** (replaces the whole story)

```
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
```

### 4.2 Sprint status (`sprint-status.yaml`)

OLD:
```
  2-1-cert-eu-and-cert-fr-alerte-adapters: backlog
  2-2-configured-sources-and-source-drift-detection: backlog
```
NEW:
```
  2-1-cert-eu-adapter: backlog
  2-2-source-drift-detection: backlog
```

Header comments: wave 7 caveat becomes "Wave 7 (2-1 | 2-2): few shared files (`docs/sources.md` vs `docs/operations.md`, possibly `README.md`)", plus a line `Epic 2 scope reduced (course correction 2026-11-01): FR-2b and FR-5 deferred`. Dependencies unchanged (`2-1: 1-4, 1-5 · 2-2: 1-4`).

### 4.3 PRD (`prds/prd-cvestream-2026-10-26/`)

- `prd.md` FR-2b and FR-5: prefix each with `*(Deferred after v1, 2026-11-01.)*`, text unchanged.
- `prd.md` Glossary, source record: `(an EUVD record, a CERT-EU advisory, a CERT-FR alerte revision, a KEV entry)` → `(an EUVD record, a CERT-EU advisory for one of its CVEs, a KEV entry)`.
- `prd.md` Out of Scope: add `- CERT-FR *alerte* (FR-2b) and configured sources (FR-5), deferred after v1: in the PHP2Shell replay, CERT-EU was ahead of CERT-FR alerte by 5 hours, and no configured source is requested yet.` Change the vendor-bulletin line to `… They would come through configured sources (FR-5, deferred).`
- `.decision-log.md`: add `- **2026-11-01, Epic 2 scope reduced (PHP2Shell replay).** EUVD alone matches PHP2Shell on structured product data but is ~7.5 h behind the hand-posted CERT-EU link (SM-3). Keep FR-2 and NFR-5 drift detection; defer FR-2b (no lead time over CERT-EU) and FR-5 (no requested source). A CERT-EU advisory yields one source record per CVE. Source: user.`

### 4.4 Architecture (`architecture.md`)

| Location | Change |
|---|---|
| F1 row (FR table) | `FR-1, 2, 2b, 3, 4, 5, 6` → `FR-1, 2, 3, 4, 6 (2b, 5 deferred)`; adapter list `(EUVD, CERT-EU, CISA KEV)`; generic-source sentence moved to "deferred: CERT-FR alerte (FR-2b) and generic RSS/JSON (FR-5) would be further adapters" |
| External dependencies | drop `CERT-FR alerte RSS + JSON` |
| Concern 9, version parsing | drop `(including French free text from CERT-FR)` |
| Correlation rules (story 1.5) | `story 2.1 must make advisory revisions merge so that this never happens in practice` → `this cannot happen with the built-in adapters (CERT-EU revisions keep their record ID); a CERT-FR alerte adapter (FR-2b, deferred) would have to make revisions merge first` |
| Source adapters | Built-in: `euvd`, `cert_eu`, `cisa_kev`. Remove the generic-adapter sentence (deferred, FR-5). Add: `The cert_eu adapter emits one record per (advisory, CVE) pair (record ID <serial>:<CVE>, aliases = the CVE + CERT-EU-SA<serial>) so that a multi-CVE advisory never merges vulnerabilities; an advisory without CVE emits one advisory-only record.` |
| Implementation sequence step 5 | `then CISA KEV, CERT-EU, CERT-FR alerte, generic RSS/JSON` → `then CISA KEV, CERT-EU` |
| Naming table | source names `euvd`, `cert_eu`, `cisa_kev`, `eu_kev`; alias example `CERTFR-2026-ALE-012` → `CERT-EU-SA2026-018` |
| Project tree | remove `certfr_alerte.py`, `generic_rss.py`, `generic_json.py`, `tests/contract/certfr_alerte/`, `tests/contract/generic/`, `tests/fixtures/certfr_alerte/` |
| Requirements mapping | `FR-1, 2, 2b, 3` → `FR-1, 2, 3` without `certfr_alerte.py`; FR-5 row → `deferred after v1` |
| External integrations | drop `CERT-FR alerte RSS + JSON, generic RSS/JSON from settings` |

### 4.5 Other artifacts

- **`project-context.md`:**
  - Sources: `A CERT-FR alerte revision = reference + revision number.` → `A CERT-EU advisory yields one record per CVE (<serial>:<CVE>), so a multi-CVE advisory never merges vulnerabilities.`
  - Identifiers: drop `certfr_alerte` from the source names; alias example `CERTFR-…-ALE-…` → `CERT-EU-SA…`.
  - Publish once: `or a CERT-FR revision` → `or an advisory revision`.
- **`deferred-work.md`:** the "Advisory revisions do not merge" entry: `story 2.1 carries an acceptance criterion to make the revisions merge` → `the acceptance criterion left with FR-2b (deferred, course correction 2026-11-01); still unreachable with the built-in adapters`. New section `## Deferred by course correction (2026-11-01, epic 2 scope)` listing FR-2b and FR-5 with their re-entry trigger (a CERT-FR alerte that beats CERT-EU in the trial; a named vendor bulletin to follow).
- **`README.md`:** `(EUVD, CISA KEV, and later CERT-EU and CERT-FR)` → `(EUVD, CISA KEV, and later CERT-EU)`.
- **Out of this proposal:** a GitHub issue on wixyvir/cvestream for the version parser (`8.2.* <8.2.35` parses as `None`, so every PHP item is "versions unknown" and `min_version` never filters).

## 5. Implementation handoff

- **Scope:** Moderate. Backlog reorganization, done by the Developer agent; no PM/Architect replan.
- **Planning edits (this proposal):** applied in one commit `docs(story-2.1): reduce epic 2 scope by course correction` as the first commit of branch `story-2-1-cert-eu-adapter`, like the story 1.7 course correction.
- **Next steps:**
  1. Open the version-parser issue on wixyvir/cvestream.
  2. `/bmad-create-story` for `2-1-cert-eu-adapter` and `2-2-source-drift-detection` (wave 7, parallel).
- **Success criteria:**
  - A CERT-EU advisory listing N CVEs produces N vulnerabilities, never one.
  - The PHP2Shell replay test posts a CERT-EU-sourced item before the EUVD record exists.
  - A failing `live` test opens or updates exactly one `source-drift` issue.
  - No remaining reference to `certfr_alerte` or generic sources outside "deferred" notes and the generic advisory-alias unit tests.
