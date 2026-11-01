# Sprint Change Proposal: branch-wildcard and compound version ranges (Story 1.8)

- **Date:** 2026-11-01
- **Trigger:** wixyvir/cvestream#11, found while replaying PHP2Shell against the Epic 1 implementation (course correction `sprint-change-proposal-2026-11-01-epic-2-scope.md`)
- **Decision:** add Story 1.8 to Epic 1. It teaches `domain/version.py` the branch wildcard (`8.2.* <8.2.35`, bare `8.2.*`), compound ranges (`>=8.0 <8.3`) and placeholder lower bounds (`* <X`, `unspecified <X`)
- **Scope:** Minor, direct implementation by the Developer agent
- **Mode:** batch

## TL;DR

- The PHP CNA writes affected versions as `<branch>.* <fixed>`. PHP2Shell (EUVD-2026-107260 / CVE-2026-109618, EUVD-2026-107265 / CVE-2026-109619) has four `PHP` rows, from `8.2.* <8.2.35` to `8.5.* <8.5.12`.
- `parse_range` returns `None` for all four rows. So every PHP vulnerability in this format is labelled **"versions unknown"**, and `php >= X` **filters nothing**: it lets every item through. Recall is safe (FR-25), but precision and the label are wrong.
- Cause: the version token `_V = r"[^\s<>=*]+"` excludes `*`, so a wildcard can only be an open upper bound.
- **Fix (Story 1.8):** a lower bound `B.*` means "from branch `B`": `8.2.* <8.2.35` → `[8.2, 8.2.35)`. A bare `B.*` means the whole branch: `8.2.*` → `[8.2, 8.3)`. The same story also parses `>=X <Y` (and `>`/`<=` variants), `* <X` and `unspecified <X` (unbounded below). These two shapes were deferred from Story 1.2.
- After the fix, `php >= 8.5` matches PHP2Shell through its `8.5.* <8.5.12` row with known versions, and `php >= 9.0` no longer matches it. Items already published are not retracted.
- No PRD or architecture change: FR-13 already says parseable data must be used. Only the epics, the sprint status and, later, the story's own docs and deferred-work entry change.
- Epic 1 goes back to `in-progress` until 1.8 is `done`. Epic 2 waits for it, because epics are never worked in parallel.

## 1. Issue summary

**Problem.** For a common, clean, structured format, the version parser returns "unusable":

```
'8.2.* <8.2.35'        -> None   (PHP CNA format, PHP2Shell)
'8.2.*'                -> None
'8.2.* <*'             -> None
'>=8.0 <8.3'           -> None   (deferred from story 1.2)
'* <8.2.29'            -> None   (deferred from story 1.2)
'unspecified <8.2.29'  -> None   (deferred from story 1.2)
'8.2.0 <8.2.35'        -> VersionRange(8.2.0 <= v < 8.2.35)
'<8.2.35'              -> VersionRange(v < 8.2.35)
```

(Checked against `main` at `449e7c6` on 2026-11-01.)

**Effect.** `matcher._match_entry` lets a product hit through as "versions unknown" when one of its ranges is `None`. With every PHP row `None`:

- every PHP vulnerability in this format reaches every `php >= X` endpoint;
- its matched product reads "versions unknown" even though the data is complete.

**Category:** a defect found in real use. The parser covered the EUVD formats seen in the 600-record sample from Story 1.2, but not this CNA's format.

**Evidence:** wixyvir/cvestream#11, which has the four PHP2Shell rows, the regex cause and the parse table above.

## 2. Impact analysis

### Checklist

| Item | Status | Notes |
|---|---|---|
| 1.1 Triggering story | [x] | PHP2Shell replay after Epic 1; the defect is in Story 1.2 code (`domain/version.py`) |
| 1.2 Core problem | [x] | Wildcard lower bounds and compound ranges are not parsed |
| 1.3 Evidence | [x] | wixyvir/cvestream#11, reproduced on `main` (table above) |
| 2.1 Current epic | [x] | Epic 1 is `done`; it reopens (`in-progress`) for 1.8 |
| 2.2 Epic-level change | [x] | Add Story 1.8 to Epic 1 |
| 2.3 Future epics | [x] | Epic 2: Story 2.1 (CERT-EU) gets parsed versions for free when its free text uses these shapes. Epic 3: no impact |
| 2.4 New or obsolete epics | [N/A] | None |
| 2.5 Epic order | [x] | 1.8 runs before Epic 2 (waves renumbered) |
| 3.1 PRD | [N/A] | FR-13 already requires parseable version data to be used; this is a parser gap, not a new requirement |
| 3.2 Architecture | [N/A] | No pattern change: `domain/version.py` stays the only parser, `packaging` the only third-party import |
| 3.3 UX | [N/A] | No UI |
| 3.4 Other artifacts | [!] | Story 1.8 updates the `version.py` docstring, `docs/sources.md` (EUVD products), `tests/fixtures/euvd/euvd_php_branch_wildcard.json`, `tests/fixtures/README.md` (capture command), and `deferred-work.md`. No schema, CI, deployment or monitoring change |
| 4.1 Direct adjustment | Viable | Effort low, risk low |
| 4.2 Rollback | Not viable | Nothing to revert |
| 4.3 MVP review | Not viable | MVP unchanged; precision improves for `min_version` entries |
| 4.4 Path | [x] | Direct adjustment |
| 6.4 sprint-status.yaml | [x] | `epic-1: in-progress`, `1-8-...: backlog`, wave 7, Epic 2 and Epic 3 shifted to waves 8 to 10 |

### Epic and story impact

- **Epic 1:** gains Story 1.8 and goes from `done` back to `in-progress` until it is `done`.
- **Story 1.2** (done) is not reopened. 1.8 extends its parser and tests. Its existing table rows stay unchanged, except the cases that are now meant to parse (none of the current `None` rows: `8.3.x`, `<3.16.0 <3.16.0`, … stay `None`).
- **Story 1.4** (done): a new recorded EUVD fixture, with no adapter change (the adapter already keeps `product_version` as is).
- **Story 2.1** (backlog): no edit. Its acceptance criterion already says "versions unknown unless `domain/version.py` parses them".

### Technical impact

- `domain/version.py` only: new patterns in `parse_range` and a helper that computes the end of a branch. The `VersionRange` API, `reaches()` and every caller stay unchanged.
- **Behaviour change for existing configurations:** an item whose rows all parse and none reaches an entry's `min_version` is now dropped for that entry, instead of passing as "versions unknown". This is what FR-13 asks for. Entries without `min_version` behave the same, apart from the label. Items already published are not retracted.

## 3. Recommended approach

**Direct adjustment: one new story in Epic 1.**

- The PHP fix is the core of the story. The other shapes use the same patterns and tests, and they close most of a Story 1.2 deferred item. Cyprien chose to include all three shapes on 2026-11-01.
- Semantics, chosen to stay recall-safe:
  - **Branch** `B.*` (with `B` a final release: digits and dots, an optional `v`): lower bound `B`, inclusive. `8.2.*` → `8.2`, and `8.2.0` compares equal to it.
  - **Bare branch** `B.*` → `[B, next(B))`, where `next` increments the last component: `8.2.*` → `[8.2, 8.3)`, `8.*` → `[8, 9)`, `8.2.1.*` → `[8.2.1, 8.2.2)`.
  - **Branch with an upper bound:** the upper bound is taken as written, with no intersection with the branch. `8.2.* <8.4` → `[8.2, 8.4)`, and `8.2.* <*` → `[8.2, ∞)`. Wider is recall-safe.
  - **Compound** `>=X <Y`, `>=X <=Y`, `>X <Y`, `>X <=Y` (separated by whitespace). An empty or inverted range stays `None`, as today.
  - **Placeholder lower bound** `* <X`, `* <=X`, `unspecified <X` (any case): unbounded below.
- **Out of scope** (still `None`, still recall-safe): `8.3.x`, a pre-release branch (`8.2rc1.*`), comma-separated ranges (`>=8.0, <8.3`), `<= *`, and the standalone `0` → `[0, 0]` case. These stay in `deferred-work.md` until a recorded record shows them.
- **Effort:** low. **Risk:** low (pure function, table tests). **Timeline:** one story before Epic 2.

## 4. Detailed change proposals

### PRD

No change (see 3.1).

### Architecture and project context

No change (see 3.2). The project-context rule "Versions: free-text version data is parsed only by `domain/version.py`" still holds as written.

### Epics (`epics.md`)

**Overview, sizing paragraph**

```
OLD: … 3 epics, 11 stories, plus Story 1.7 added by the 2026-11-01 course correction (wixyvir/cvestream#8). …
NEW: … 3 epics, 11 stories, plus Stories 1.7 and 1.8 added by the 2026-11-01 course corrections
     (wixyvir/cvestream#8, wixyvir/cvestream#11). …
```

**FR coverage map**

```
OLD: - FR-13: Epic 1 (Story 1.2) - minimum version, "versions unknown"
NEW: - FR-13: Epic 1 (Stories 1.2, 1.8) - minimum version, "versions unknown", branch-wildcard and compound version ranges
```

**New Story 1.8**, inserted after Story 1.7:

```markdown
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
```

Rationale: wixyvir/cvestream#11. The acceptance criteria of the issue are the fixture-based criterion. The other shapes are Cyprien's scope decision.

### Sprint status (`sprint-status.yaml`)

```
development_status:
  epic-1: done                     →  epic-1: in-progress
  (after 1-7)                      +  1-8-branch-wildcard-and-compound-version-ranges: backlog

PARALLEL WAVES:
  Epic 1
    Wave 6: 1-7 …
  + Wave 7: 1-8                   (course correction 2026-11-01, wixyvir/cvestream#11)
  Epic 2
    Wave 7: 2-1 | 2-2             →  Wave 8: 2-1 | 2-2
  Epic 3
    Wave 8: 3-1 | 3-3             →  Wave 9: 3-1 | 3-3
    Wave 9: 3-2                   →  Wave 10: 3-2

Dependencies:                      +  1-8: 1-2, 1-4
Caveats: "Wave 7 (2-1 | 2-2)" → "Wave 8", "Wave 8 (3-1 | 3-3)" → "Wave 9"
```

## 5. Implementation handoff

- **Scope:** Minor.
- **Developer agent:** `bmad-create-story` for 1.8, then `bmad-dev-story`, then `bmad-code-review`, on branch `story-1-8-branch-wildcard-and-compound-version-ranges`, with one PR to `main` that closes wixyvir/cvestream#11. This proposal and the planning edits are committed directly on `main`, so the story branch starts from them.
- **Success criteria:**
  - the acceptance criteria of Story 1.8 are met and all gate commands pass;
  - the four PHP2Shell rows parse, `php >= 8.5` matches with known versions, and `php >= 9.0` does not match;
  - every existing `test_version.py` and `test_matcher.py` case still passes unchanged.
- **After merge:** comment on wixyvir/cvestream#11 with the semantics actually implemented, close it, then set `epic-1` back to `done`.
