# Sprint Change Proposal: description fallback precision (Story 1.7)

- **Date:** 2026-11-01
- **Trigger:** wixyvir/cvestream#8, found while trialling Story 1.6 (wixyvir/cvestream#6) on live EUVD and CISA KEV data
- **Decision:** add Story 1.7 to Epic 1, implementing fixes B and C of the issue
- **Scope:** Minor, direct implementation by the Developer agent
- **Mode:** batch

## TL;DR

- `team-php` (`php >= 8.2`) receives items about other products: **9 of 16** items in one test database are false positives.
- Cause: when a vulnerability has **no** affected-product data (EUVD `n/a` product, or a CISA KEV-only record), the matcher falls back to the description, and whole-word matching hits `php` inside file names (`loan_history_maintenance.php`) or in sentences that only mention PHP.
- **Fix B:** in the description fallback, a hit preceded by `.`, `/` or `\`, or followed by `/` or `\`, is part of a path or file name and does not count. This is narrower than the rule in the issue: the "followed by `.` + letter" case is dropped, so `php.ini`, `php.net` and "Node.js" keep matching (it would have broken an existing test and dotted product names). It still fixes the 6 file-name cases.
- **Fix C:** product entries get an optional `match_description: bool = true`; `false` means structured data only. This fixes the remaining 3 cases for teams that choose it, KEV-only included.
- New Story 1.7 in Epic 1, one PR on `main` (1.1 to 1.6 are merged). Planning documents are amended by this proposal; code, schema and user docs are changed by the story.
- No rollback, no MVP change. Epic 2 waits for 1.7, since epics are never worked in parallel.

## 1. Issue summary

**Problem.** The FR-12 description fallback, meant for sparse records, also catches:

1. product names inside file names and paths (`.php`, and likewise `.js`, `.py`, …);
2. descriptions that mention a technology without being about it ("PHP webshells", "PHP object injection");
3. CISA KEV-only vulnerabilities, which carry no products by design and always take the fallback until EUVD data arrives.

**Category:** a failed approach seen in real use. The rule is correct for sparse records but too broad in practice.

**Evidence** (from wixyvir/cvestream#8): `EUVD-2026-104552` (CVE-2026-107084, Slims CSRF) has `affected: []` because the EUVD product `n/a` is a placeholder. `_pattern("php")` is `(?<!\w)php(?!\w)`, so `…maintenance.php` matches. Breakdown of the 16 `team-php` items: 8 structured (correct, apart from 1 Linux kernel item published while KEV-only), 6 file names, 2 PHP applications. CVE-2026-46300 (Linux kernel) was published as `new` from its KEV-only description six minutes before EUVD brought 18 `Linux` products.

## 2. Impact analysis

### Checklist

| Item | Status | Notes |
|---|---|---|
| 1.1 Triggering story | [x] | Story 1.6 trial; the defect is in Story 1.2 code (`domain/matcher.py`) |
| 1.2 Core problem | [x] | Description fallback too broad (see above) |
| 1.3 Evidence | [x] | wixyvir/cvestream#8: payloads, counts, CVE IDs |
| 2.1 Current epic | [x] | Epic 1 is functionally done; it can close once 1.7 is done |
| 2.2 Epic-level change | [x] | Add Story 1.7 to Epic 1 |
| 2.3 Future epics | [x] | Epic 2: CERT-EU and generic sources (FR-5, text-only matching) benefit from fix B with no extra work. Epic 3: the CLI (Story 3.2) must expose `match_description` when it manages product entries; the Valkey provider reuses the same models |
| 2.4 New or obsolete epics | [N/A] | None |
| 2.5 Epic order | [x] | 1.7 runs before Epic 2 (sprint waves renumbered) |
| 3.1 PRD | [!] → done | FR-11 and FR-12 amended |
| 3.2 Architecture | [!] → done | F3 summary amended; project-context.md matching rule amended |
| 3.3 UX | [N/A] | No UI |
| 3.4 Other artifacts | [!] | Story 1.7 updates `schemas/config-v1.json`, `docs/configuration.md`, `endpoints.example.yaml`, matcher docstring. No CI, deployment or monitoring change |
| 4.1 Direct adjustment | Viable | Effort low, risk low |
| 4.2 Rollback | Not viable | Nothing to revert: the 1.2 rule is right for structured data |
| 4.3 MVP review | Not viable | MVP unchanged; this improves precision without lowering recall by default |
| 4.4 Path | [x] | Direct adjustment |
| 6.4 sprint-status.yaml | [x] | `1-7-description-fallback-precision: backlog`, wave 6, epics 2 and 3 shifted to waves 7 to 9 |

### Epic and story impact

- **Epic 1:** gains Story 1.7. `epic-1` stays `in-progress` until 1.7 is `done`.
- **Story 1.2** (done) is not reopened. 1.7 changes its matcher and adds tests.
- **Story 1.3** (done) contract gains one additive v1 field, through 1.7.
- **Story 3.2** (backlog): `cvestream product add` should accept `--no-match-description`. Its story file picks this up from the contract when it is created; no edit to its epic text is needed now.

### Technical impact

- `domain/matcher.py`: path-context check for description hits only; skip the fallback when `entry.match_description` is false.
- `domain/endpoint.py`: `ProductEntry.match_description: bool = True`.
- `config/models.py`: `ProductEntryConfig.match_description: bool = True`, mapped in `_to_product()`; schema regenerated.
- Behaviour for existing configurations: fix B applies to every endpoint (description-only matches inside paths disappear); fix C changes nothing until an entry sets `false`.
- Items already published are not retracted.

## 3. Recommended approach

**Direct adjustment: one new story in Epic 1.**

- B is a bug fix: a file extension or a path segment is not a product mention.
- C leaves the precision/recall trade-off to each team, without changing the default. FR-25 (recall first) still holds by default.
- Not chosen: A (exclusions cannot target `.php`), D (revisits the CISA KEV product decision in `docs/sources.md`; labels are coarse), E (a "possible match" title label, which may follow later as a separate story).
- **Rule B narrowed** (decided with Cyprien on 2026-11-01): the issue also ignored hits followed by `.` + a word character. That would flip the existing test "See the php.net advisory" and stop `node` matching "Node.js" or `asp` matching "ASP.NET". The narrow rule fixes all 6 measured cases, which are all `x.php` (preceded by `.`). `php.ini` keeps matching, which is reasonable: it is about PHP configuration.
- **Effort:** low. **Risk:** low (pure domain function with table tests; additive contract field). **Timeline:** one story before Epic 2.

## 4. Detailed change proposals

### PRD (`prds/prd-cvestream-2026-10-26/prd.md`)

```
FR-11
OLD: … It can also list aliases (e.g. `php-src`) and exclusion terms (e.g. `wordpress`).
NEW: … It can also list aliases (e.g. `php-src`) and exclusion terms (e.g. `wordpress`), and it can
     opt out of the description fallback (FR-12) so that only structured affected-product data matches it.

FR-12 (inserted after the WordPress example)
NEW: In the description, a name that is part of a file name or path (`loan_history_maintenance.php`,
     `/usr/lib/php/`) is not a mention of the product.
```

Rationale: wixyvir/cvestream#8 evidence; the default recall behaviour is unchanged.

### Epics (`epics.md`)

- Overview: note that Story 1.7 was added by this course correction.
- Requirements inventory FR-11, FR-12: same meaning as the PRD edits.
- FR coverage map: FR-11 gains Story 1.7 (`match_description`), FR-12 gains Story 1.7 (description fallback precision).
- **New Story 1.7: Description fallback precision**, after Story 1.6 (full text in `epics.md`). Acceptance criteria in short:
  - path rule in the description fallback, with positive and negative examples, structured data and exclusions unchanged;
  - table tests built from the file-name cases of wixyvir/cvestream#8;
  - `match_description: false` matches through structured data only; KEV-only CVE-2026-46300 does not match it; a later EUVD record publishes as `new` (FR-26);
  - additive v1 field, schema regenerated in the same commit, `docs/configuration.md`, `endpoints.example.yaml` and the matcher docstring updated;
  - no retraction of past publications.

### Architecture (`architecture.md`)

```
§ Requirements Overview, F3 row
OLD: … the description only as a fallback for a vulnerability without affected-product data, aliases, …
NEW: … the description only as a fallback for a vulnerability without affected-product data (ignoring
     names inside file names and paths, and skipped for entries with `match_description: false`), aliases, …
```

### Project context (`project-context.md`)

The **Matching (FR-12)** rule gains the path rule (with the narrow boundary characters and the `php.ini` / `php.net` / "Node.js" counter-examples) and `match_description: false`. It also states that structured data and exclusions keep plain whole-word matching.

### Sprint status (`sprint-status.yaml`)

`1-7-description-fallback-precision: backlog`; wave 6 = 1-7; Epic 2 and Epic 3 waves become 7, 8 and 9; dependency `1-7: 1-2, 1-3, 1-6`.

## 5. Implementation handoff

- **Scope:** Minor.
- **Developer agent:** `bmad-create-story` for 1.7, then `bmad-dev-story`, then `bmad-code-review`, on branch `story-1-7-description-fallback-precision`, with one PR to `main` that closes wixyvir/cvestream#8. This proposal and the planning edits are committed directly on `main`, so the story branch starts from them.
- **Success criteria:**
  - the acceptance criteria of Story 1.7 are met and all gate commands pass (including `cvestream config schema --check`);
  - replaying the trial database (or the recorded shapes) leaves `team-php` with no file-name false positives, and with none of the 9 when `match_description: false` is set on `php`;
  - every existing matcher test still passes unchanged, `php.net` included.
- **After merge:** comment on wixyvir/cvestream#8 with the rule actually implemented (narrow B), then close it.
