---
baseline_commit: f5f21b2b7c16020ef481f757dab1fbf2a08a38bf
---

# Story 1.8: Branch-wildcard and compound version ranges

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a channel subscriber,
I want the version ranges written by the PHP CNA, and other common range shapes, to be understood,
so that my team's minimum version filters these vulnerabilities, and an item reads "versions unknown" only when versions really are unknown.

Added by the 2026-11-01 course correction for wixyvir/cvestream#11 (`_bmad-output/planning-artifacts/sprint-change-proposal-2026-11-01-php-branch-wildcard.md`), found while replaying PHP2Shell: the PHP CNA writes affected versions as `8.2.* <8.2.35`, which parses as unusable, so every PHP vulnerability in that format is labelled "versions unknown" and `php >= X` filters nothing.

## TL;DR

- **One production file:** `src/cvestream/domain/version.py`. Three new regexes and a small helper inside `parse_range`. `VersionRange`, `reaches()`, `_build()`, `parse_version()` and every caller (`domain/matcher.py`, `config/models.py`) stay **unchanged**.
- **Three new shapes:**
  - **Branch wildcard:** `8.2.* <8.2.35` → `[8.2, 8.2.35)`. A bare `8.2.*` → `[8.2, 8.3)`.
  - **Compound:** `>=8.0 <8.3`, plus the `>`/`<=` variants → a two-sided range.
  - **Placeholder lower bound:** `* <8.2.29` or `unspecified <8.2.29` → `v < 8.2.29`.
- **Everything else stays `None`, as today:** every `None` row of the current `RANGES` table (`8.3.x`, `<3.16.0 <3.16.0`, …), a pre-release branch (`8.2rc1.*`), an inverted range, `<= *`. A standalone `0` stays `[0, 0]`.
- **Tests:**
  - table tests in `tests/unit/domain/test_version.py`;
  - a matcher test with the four PHP2Shell rows;
  - a **recorded** EUVD fixture `tests/fixtures/euvd/euvd_php_branch_wildcard.json` (capture command below), run through the real EUVD adapter and the matcher.
- **Docs in the same PR:**
  - the `version.py` docstring;
  - `docs/sources.md` (EUVD "Products");
  - one sentence in `docs/configuration.md` on `min_version`;
  - `tests/fixtures/README.md`;
  - `deferred-work.md` (strike the Story 1.2 item, keep what remains).

  Planning documents (PRD, epics, architecture, project-context) are **already amended** on `main`: do not edit them.
- **No** new dependency, config change, migration or retraction of past publications.
- **Delivery:** one branch `story-1-8-branch-wildcard-and-compound-version-ranges` from `main`, one PR to `main` that closes wixyvir/cvestream#11.

## Acceptance Criteria

1. **Branch wildcard with an upper bound (FR-13).** Given a lower bound written as a branch wildcard `B.*`, where `B` is a final release (digits and dots, optional `v`/`V` prefix: `8.2.*`, `8.*`, `v8.2.*`), when `parse_range` parses it, the range starts at `B` inclusive and the upper bound is taken as written, with no intersection with the branch:
   - `8.2.* <8.2.35` → `[8.2, 8.2.35)`
   - `8.2.* ≤8.2.35` (and `<=`) → `[8.2, 8.2.35]`
   - `8.2.* <*` (and `≤*`, `<=*`) → `[8.2, ∞)`
   - `8.2.* <8.4` → `[8.2, 8.4)`
2. **Bare branch.** A bare `B.*` covers the branch: `8.2.*` → `[8.2, 8.3)`, `8.*` → `[8, 9)`, `8.2.1.*` → `[8.2.1, 8.2.2)`, `v8.2.*` → `[8.2, 8.3)`. The end of the branch increments the **last** release component of `B` as written. `8.2.0.*` → `[8.2.0, 8.2.1)`.
3. **Still `None`:**
   - a pre-release branch (`8.2rc1.*`);
   - `8.3.x`;
   - an inverted or empty branch range (`8.4.* <8.2`, `8.2.* <8.2`);
   - **every row that is `None` in the current `RANGES` table** of `test_version.py`;
   - `<= *`.

   A standalone `0` keeps parsing to `[0, 0]`. These two last shapes stay deferred.
4. **Compound range.** `>=X <Y`, `>=X <=Y`, `>X <Y` and `>X <=Y`, also written with `≥`/`≤`, with or without spaces after the operators, and with whitespace between the two parts. Each returns the matching range with the right inclusivity on each side (`>` exclusive, `>=` inclusive, `<` exclusive, `<=` inclusive). An empty or inverted one returns `None`: `>=8.3 <8.0`, `>8.0 <8.0`, `>8.0 <=8.0`, `>=8.0 <8.0`. `>=8.0 <=8.0` is the single version `[8.0, 8.0]`.
5. **Placeholder lower bound.** `* <X`, `* <=X`, `unspecified <X` and `unspecified <=X` (any case: `Unspecified`, `UNSPECIFIED`; `≤` too) are unbounded below: `* <8.2.29` → `v < 8.2.29`, equal to `parse_range("<8.2.29")`. `unspecified` alone stays `None`.
6. **Unchanged API and rows.** `VersionRange`, `reaches()`, `parse_version()` and the signature of `parse_range()` are unchanged. Every existing row of `RANGES`, `REACHES` and `test_parse_version` in `test_version.py` passes **unchanged**, and so does every existing test in `test_matcher.py`.
7. **`reaches()` at the branch boundaries** (table tests):
   - `8.2.*` against `8.3`: false; against `8.2.99`: true.
   - `8.2.* <8.2.35` against `8.2.34`: true; against `8.2.35`: false.
   - `8.*` against `9.0`: false.
   - `8.2.* <*` against `99.0`: true.
   - `* <8.2.29` against `8.2`: true.
   - `>8.0 <8.3` against `8.3`: false.
   - `>=8.0 <=8.3` against `8.3`: true.
8. **PHP2Shell end to end (FR-13).** Given the recorded EUVD record of EUVD-2026-107260 (PHP2Shell, CVE-2026-109618) in `tests/fixtures/euvd/euvd_php_branch_wildcard.json`, captured from the live API with its four `PHP` rows `8.2.* <8.2.35`, `8.3.* <8.3.36`, `8.4.* <8.4.27` and `8.5.* <8.5.12`, when it goes through the **real EUVD adapter** (respx), `normalize()`, `build_vulnerability()` and `matcher.match()`:
   - `php >= 8.5` matches, structured, through the `8.5.* <8.5.12` row with `versions_unknown` false and no `PassThrough.VERSIONS_UNKNOWN` label;
   - `php >= 8.2` and `php` match, with no label;
   - `php >= 9.0` does not match (`None`).
9. **Docs.**
   - The `version.py` module docstring and `docs/sources.md` (EUVD, "Products") list the understood version formats.
   - `docs/configuration.md` says in one sentence that `min_version` is checked against the parsed ranges, with a link to that list.
   - `tests/fixtures/README.md` gives the capture command of the new fixture.
   - In `deferred-work.md`, the Story 1.2 item "CVE 5 placeholder lower bounds and compound ranges" is struck through, except what remains (`<= *`, standalone `0`).
10. **No retraction.** Publications already made are not retracted. `domain/publication.py`, `pipeline/*` and `state/*` are untouched.

## Tasks / Subtasks

- [x] **Task 1: Branch from `main`** (all ACs)
  - [x] `git switch main && git pull && git switch -c story-1-8-branch-wildcard-and-compound-version-ranges`. `main` already holds the course-correction commit `f5f21b2`.
  - [x] Baseline: `uv sync --locked && uv run pytest -q`, and note the passed count (story 1.7 ended at 1004 passed, 2 deselected).
- [x] **Task 2: Table tests first (red)** (AC 1–7)
  - [x] In `tests/unit/domain/test_version.py`, add a new list `BRANCH_AND_COMPOUND_RANGES` and its own parametrized test (see "Test tables"). Do not touch the existing `RANGES` rows.
  - [x] Add the new rows to a new `BRANCH_REACHES` list and its own parametrized `reaches()` test.
  - [x] Add the explicit rows for the standalone `0` (still `[0, 0]`) and `* <8.2.29 == <8.2.29` (see "Test tables"). The existing `test_parse_range` already pins every `None` row of `RANGES` (AC 3), so leave it untouched.
  - [x] Run the tests and confirm the new ones fail.
- [x] **Task 3: Implement in `domain/version.py`** (AC 1–6)
  - [x] Add the `_BRANCH`, `_BRANCH_ONLY`, `_BRANCH_TO`, `_COMPOUND` and `_PLACEHOLDER_BELOW` patterns and the `_branch_end()` helper (see "Parser design").
  - [x] Wire them into `parse_range` without changing the existing branches.
  - [x] Update the module docstring with the list of understood formats (AC 9).
  - [x] Green: `uv run pytest tests/unit/domain -q`.
- [x] **Task 4: Matcher unit test** (AC 6, 8)
  - [x] In `tests/unit/domain/test_matcher.py`, add `test_php_branch_wildcard_rows_have_known_versions`, parametrized on the entry (`php`, `php >= 8.2`, `php >= 8.5`, `php >= 9.0`), with `make_vulnerability(products=[...four PHP2Shell rows...])` (see "Test tables").
  - [x] Do not edit any existing matcher test.
- [x] **Task 5: Record the fixture and the end-to-end test** (AC 8)
  - [x] Capture `tests/fixtures/euvd/euvd_php_branch_wildcard.json` from the live API with the command in "Fixture capture". Never hand-write or edit it.
  - [x] Check that the four `PHP` rows are present (`jq '[.items[0].enisaIdProduct[] | .product_version]'`).
  - [x] In `tests/contract/euvd/test_euvd.py`, add `test_php_branch_wildcard_rows_filter_by_minimum_version` (see "End-to-end test design").
- [x] **Task 6: Documentation** (AC 9)
  - [x] Update `docs/sources.md` (EUVD, "Products" bullet) with the formats list.
  - [x] Add the `min_version` sentence to `docs/configuration.md`.
  - [x] Add a fixture row to `tests/fixtures/README.md`.
  - [x] Update `_bmad-output/implementation-artifacts/deferred-work.md`.
  - [x] Check `README.md` and `CLAUDE.md`. Neither describes version formats today, so no change is expected: say so in the completion notes.
- [x] **Task 7: Gates and PR** (all ACs)
  - [x] Run every gate command from `CLAUDE.md`. `docker build` may fail locally behind the TLS proxy (see "Previous story intelligence"); never disable verification, and let CI run it.
  - [x] Make one commit per concern (suggested split under "Commits"), Conventional Commits with `story-1.8`, never squashed.
  - [x] Open the PR to `main` with title `fix(story-1.8): understand branch-wildcard and compound version ranges` and a description that links this story file and says `Closes wixyvir/cvestream#11`.
  - [x] Set this story to `review` and update `sprint-status.yaml` (`1-8-…: review`).

### Review Findings

- [x] [Review][Patch] (decided: option 1, catch `(InvalidVersion, ValueError)` in `parse_version`, and add a test row for each of the three inputs) A very long digit run makes `parse_range` raise `ValueError` instead of returning `None` — `parse_version` catches only `InvalidVersion`, so `parse_range("9"*4301)` (pre-existing), `parse_range("1.* <" + "9"*5000)` (new entry point) and `parse_range("1." + "9"*4300 + ".*")` (new, `_branch_end` makes a 4301-digit string) raise `ValueError: Exceeds the limit (4300 digits)`. `matcher.py:127` calls `parse_range(hit.version_text)` without a guard, so one hostile or corrupt EUVD `product_version` aborts `match()` for the endpoint. Options: (1) catch `(InvalidVersion, ValueError)` in `parse_version`, which fixes every path but edits a function AC 6 says stays unchanged; (2) guard only `_branch_end` and leave the pre-existing paths; (3) defer the whole thing as a pre-existing robustness issue. [src/cvestream/domain/version.py:58-64] (sources: edge)
- [x] [Review][Patch] `deferred-work.md` list of still-`None` formats is incomplete — add the reversed compound `<8.3 >=8.0`, a compound with a wildcard upper bound `>=8.0 <8.3.*`, a wildcard in a one-sided bound (`<8.2.*`, `<=8.2.*`, `>=8.2.*`) and `unspecified <*`, all recall-safe `None` and not listed today [_bmad-output/implementation-artifacts/deferred-work.md:19] (sources: edge+auditor)
- [x] [Review][Patch] Missing table row for `8.2.* <=*`, which AC 1 lists; the table has `<*` and `≤*` only [tests/unit/domain/test_version.py:177-178] (sources: auditor)
- [x] [Review][Patch] Docs overstate the label: "otherwise the item passes labelled 'versions unknown'" is true only when no other row of the product reaches the minimum (`matcher.py:130-135`). Reword to say the item is never dropped on an unknown row, and is labelled only when no other row reaches the minimum [docs/configuration.md:85, docs/sources.md:107-116] (sources: auditor)

## Dev Notes

### Scope boundaries (read first)

- **In scope:**
  - `domain/version.py`;
  - new tests and one new recorded fixture;
  - the docs listed in AC 9;
  - `deferred-work.md`;
  - this story file and `sprint-status.yaml`.
- **Out of scope, stays `None`.** These are still recall-safe: they pass labelled "versions unknown".
  - `8.3.x` and other `x` wildcards;
  - pre-release branches (`8.2rc1.*`);
  - comma-separated ranges (`>=8.0, <8.3`);
  - a compound range without whitespace between its parts (`>=8.0<8.3`);
  - a compound with a wildcard upper bound (`>=8.0 <*`);
  - `* <*`;
  - `<= *`.

  A standalone `0` keeps parsing to `[0, 0]`.

  These stay in `deferred-work.md` until a recorded record shows them. Do not "improve" them in passing: each one changes what `min_version` drops.
- **Do not touch:**
  - `matcher.py` (logic and docstring: its version handling does not change);
  - the EUVD adapter (it already keeps `product_version` as is, see `sources/euvd.py:224`);
  - `config/*`, `schemas/config-v1.json`, `pipeline/*`, `state/*`, `app/*`;
  - the planning artifacts (`prd.md`, `epics.md`, `architecture.md`, `project-context.md`), which are already amended.
- **Behaviour change to state in the PR description.** Today a PHP item in this format is labelled "versions unknown" and passes every `php >= X` entry. With this story, an item whose product rows all parse and none of them reaches an entry's `min_version` is **dropped** for that entry. This is what FR-13 asks. Entries without `min_version` behave the same, apart from the label. Items already published are not retracted.

### Current state of `src/cvestream/domain/version.py` (the only file changed in `src/`)

- `_V = r"[^\s<>=*]+"` is one version token: no whitespace, no comparison operator, **no `*`**. That last exclusion is the cause of wixyvir/cvestream#11: a wildcard can only appear as an open upper bound (`_UPPER_OPEN = (?P<hi>_V|\*)`).
- `parse_range` normalizes whitespace (`" ".join(text.split())`) and turns `≤`/`≥` into `<=`/`>=`. It then tries, in order:
  1. `_UNUSABLE` (`""`, `n/a`, `na`, `-`, `unknown`, `unspecified`, compared lower-cased) → `None`;
  2. `"*"` → everything;
  3. `_SINGLE`;
  4. `_FROM_TO` (`lo <hi`, `lo <=hi`, `hi` may be `*`);
  5. `_BELOW`;
  6. `_ABOVE`;
  7. `_HYPHEN` (`lo - hi`, spaces required).

  Every pattern is applied with `fullmatch`.
- `_build(lower, upper, *, lower_inclusive=True, upper_inclusive=False)` parses both bounds with `parse_version`. It returns `None` when a bound text is unparseable, when `low > high`, or when `low == high` and the range is not inclusive on both sides. It reports an unbounded side as inclusive, so equal ranges compare equal. **Reuse it for every new shape**: it already gives the empty and inverted `None` of AC 3 and AC 4.
- `VersionRange.reaches(minimum)` looks only at the upper bound (`upper is None` or `upper > minimum`, or equal and inclusive). The lower bound of a branch therefore never changes a `min_version` outcome. The fix works through the **upper** bound, which used to be lost with the whole row.
- **Preserve:** the order and behaviour of every existing branch of `parse_range`, and `parse_version` (also used by `config/models.py` to validate `min_version`).

### Parser design (prescriptive)

Add to `domain/version.py`, next to the existing patterns:

```python
# A branch: a final release then ".*" (8.2.*, 8.*, v8.2.*). Not 8.2rc1.*, not 8.3.x.
_BRANCH = r"(?P<branch>[vV]?\d+(?:\.\d+)*)\.\*"
_BRANCH_ONLY = re.compile(_BRANCH)
# PHP CNA: "<branch>.* <fixed>" (and "<=", or an open "*" upper bound)
_BRANCH_TO = re.compile(rf"{_BRANCH}\s*(?P<op><=|<)\s*{_UPPER_OPEN}")
# ">=8.0 <8.3": two bounds separated by whitespace
_COMPOUND = re.compile(
    rf"(?P<lop>>=|>)\s*(?P<lo>{_V})\s+(?P<hop><=|<)\s*(?P<hi>{_V})"
)
# CVE 5 placeholder lower bound: "* <8.2.29", "unspecified <8.2.29"
_PLACEHOLDER_BELOW = re.compile(
    rf"(?:\*|unspecified)\s*(?P<op><=|<)\s*(?P<hi>{_V})", re.IGNORECASE
)


def _branch_end(branch: Version) -> Version:
    """First version after the branch: 8.2 → 8.3, 8 → 9, 8.2.1 → 8.2.2."""
    *head, last = branch.release
    return Version(".".join(str(part) for part in (*head, last + 1)))
```

In `parse_range`, after the existing `_HYPHEN` branch and before the final `return None`, add:

```python
    if found := _BRANCH_TO.fullmatch(normalized):
        upper = None if found["hi"] == "*" else found["hi"]
        return _build(found["branch"], upper, upper_inclusive=found["op"] == "<=")
    if found := _BRANCH_ONLY.fullmatch(normalized):
        start = parse_version(found["branch"])
        if start is None:  # unreachable with _BRANCH, kept for the type checker
            return None
        return _build(found["branch"], str(_branch_end(start)))
    if found := _COMPOUND.fullmatch(normalized):
        return _build(
            found["lo"],
            found["hi"],
            lower_inclusive=found["lop"] == ">=",
            upper_inclusive=found["hop"] == "<=",
        )
    if found := _PLACEHOLDER_BELOW.fullmatch(normalized):
        return _build(None, found["hi"], upper_inclusive=found["op"] == "<=")
```

Why these choices:

- **Placement after the existing branches.** None of the new patterns can match a string that an existing pattern matches, because `_V` excludes `*`, `<`, `>`, `=` and whitespace. Appending them keeps the old branches byte-for-byte identical, and the order among the new ones doesn't matter. Verified on `main`: `8.2.* <8.2.35`, `8.2.*`, `>=8.0 <8.3`, `* <8.2.29` and `unspecified <8.2.29` all return `None` today, and `8.3.x` returns `None` through `_SINGLE` plus `parse_version` (keep it that way).
- **`_UNUSABLE` still runs first**, so `unspecified` alone stays `None`. `_PLACEHOLDER_BELOW` needs an operator and a bound.
- **`packaging`'s `Version` accepts a `v`/`V` prefix** and drops it: `Version("v8.2").release == (8, 2)`. `_branch_end` works on `release`, so the prefix is never part of the arithmetic. Leading zeros normalize (`Version("08.2").release == (8, 2)`).
- **`Version("8.2") == Version("8.2.0")`.** So `8.2.* <8.2.35` → lower `8.2` is the same as `8.2.0`, and `_branch_end` of `8.2.0` is `8.2.1`, as AC 2 states.
- **The upper bound of a branch range is taken as written, with no intersection with the branch** (`8.2.* <8.4` → `[8.2, 8.4)`). The range is wider, and wider is recall-safe. It is the semantics Cyprien chose on 2026-11-01.
- **Inverted or empty ranges return `None`**, and `_build` already handles them. Do not add a separate check.
- **`domain` purity:** stdlib `re` and `packaging` only (import-linter contract). No new import is needed. Do not add `from __future__ import annotations` (Python 3.14 rule).

**Module docstring.** Replace the example sentence with a list of the understood formats. Keep the closing statement: `None` means unusable, and callers let the item through labelled "versions unknown" (FR-25). Formats, each with one example:

- a single version (`8.2.29`);
- `X <Y` and `X ≤Y`, where `Y` may be `*` (EUVD);
- `<Y`, `<=Y`, `>=X`, `>X`;
- `X - Y`;
- `*`;
- a branch `B.*` (`[B, next B)`) and `B.* <Y` (PHP CNA);
- a compound `>=X <Y` (and `>`, `<=` variants);
- a placeholder lower bound `* <Y` or `unspecified <Y`.

Also say what stays unusable: `8.3.x`, a pre-release branch, comma-separated ranges, `<= *`.

### Test tables (`tests/unit/domain/test_version.py`)

Reuse the existing `_range()` helper; it already reports unbounded sides as inclusive. Add a new list and test, and leave `RANGES`, `REACHES` and `test_parse_version` untouched (AC 6):

```python
# Story 1.8: PHP CNA branch wildcards (wixyvir/cvestream#11), compound ranges
# and CVE 5 placeholder lower bounds.
BRANCH_AND_COMPOUND_RANGES = [
    # branch wildcard + upper bound (the four PHP2Shell rows first)
    ("8.2.* <8.2.35", _range("8.2", "8.2.35")),
    ("8.3.* <8.3.36", _range("8.3", "8.3.36")),
    ("8.4.* <8.4.27", _range("8.4", "8.4.27")),
    ("8.5.* <8.5.12", _range("8.5", "8.5.12")),
    ("8.2.* ≤8.2.35", _range("8.2", "8.2.35", upper_inclusive=True)),
    ("8.2.* <=8.2.35", _range("8.2", "8.2.35", upper_inclusive=True)),
    ("8.2.*<8.2.35", _range("8.2", "8.2.35")),
    ("8.2.* <*", _range("8.2", None)),
    ("8.2.* ≤*", _range("8.2", None)),
    ("8.2.* <8.4", _range("8.2", "8.4")),
    ("v8.2.* <8.2.35", _range("8.2", "8.2.35")),
    ("8.* <8.4", _range("8", "8.4")),
    # bare branch
    ("8.2.*", _range("8.2", "8.3")),
    ("8.*", _range("8", "9")),
    ("8.2.1.*", _range("8.2.1", "8.2.2")),
    ("8.2.0.*", _range("8.2.0", "8.2.1")),
    ("v8.2.*", _range("8.2", "8.3")),
    ("V8.2.*", _range("8.2", "8.3")),
    ("8.9.*", _range("8.9", "8.10")),
    # compound
    (">=8.0 <8.3", _range("8.0", "8.3")),
    (">=8.0 <=8.3", _range("8.0", "8.3", upper_inclusive=True)),
    (">8.0 <8.3", _range("8.0", "8.3", lower_inclusive=False)),
    (">8.0 <=8.3", _range("8.0", "8.3", lower_inclusive=False, upper_inclusive=True)),
    ("≥8.0 ≤8.3", _range("8.0", "8.3", upper_inclusive=True)),
    (">= 8.0 < 8.3", _range("8.0", "8.3")),
    (">=8.0 <=8.0", _range("8.0", "8.0", upper_inclusive=True)),
    # placeholder lower bound
    ("* <8.2.29", _range(None, "8.2.29")),
    ("* <=8.2.29", _range(None, "8.2.29", upper_inclusive=True)),
    ("* ≤8.2.29", _range(None, "8.2.29", upper_inclusive=True)),
    ("unspecified <8.2.29", _range(None, "8.2.29")),
    ("Unspecified <=8.2.29", _range(None, "8.2.29", upper_inclusive=True)),
    ("UNSPECIFIED <8.2.29", _range(None, "8.2.29")),
    # still unusable
    ("8.2rc1.*", None),
    ("8.2.*.*", None),
    ("8.4.* <8.2", None),  # inverted
    ("8.2.* <8.2", None),  # empty
    ("8.2.* 8.2.35", None),  # no operator
    (">=8.3 <8.0", None),  # inverted
    (">=8.0 <8.0", None),  # empty
    (">8.0 <8.0", None),
    (">8.0 <=8.0", None),
    (">=8.0, <8.3", None),  # comma-separated: deferred
    (">=8.0<8.3", None),  # no whitespace between the parts: out of scope
    (">=8.0 <*", None),  # out of scope
    ("* <*", None),
    ("<= *", None),  # deferred
    ("unspecified", None),
    ("* <8.3.x", None),
]
```

Add one more assertion to pin the deferred standalone `0`: `parse_range("0") == _range("0", "0", upper_inclusive=True)`. It is already covered implicitly by `_SINGLE`; an explicit row makes the "still deferred" behaviour visible. Also assert that `parse_range("* <8.2.29") == parse_range("<8.2.29")` (AC 5).

`reaches()` rows (`BRANCH_REACHES`, same shape as `REACHES`):

```python
("8.2.*", "8.3", False),
("8.2.*", "8.2.99", True),
("8.2.* <8.2.35", "8.2.34", True),
("8.2.* <8.2.35", "8.2.35", False),
("8.2.* ≤8.2.35", "8.2.35", True),
("8.*", "9.0", False),
("8.*", "8.99", True),
("8.2.* <*", "99.0", True),
("* <8.2.29", "8.2", True),
("* <8.2.29", "8.2.29", False),
(">8.0 <8.3", "8.3", False),
(">=8.0 <=8.3", "8.3", True),
```

**Matcher unit test (`tests/unit/domain/test_matcher.py`).** Use the existing `make_vulnerability` fixture and the `_endpoint()` helper. The four rows in EUVD order (the order the live record lists them):

```python
_PHP2SHELL_ROWS = [
    ("PHP", "PHP Group", "8.2.* <8.2.35"),
    ("PHP", "PHP Group", "8.5.* <8.5.12"),
    ("PHP", "PHP Group", "8.4.* <8.4.27"),
    ("PHP", "PHP Group", "8.3.* <8.3.36"),
]
```

The vendor is "PHP Group" in the live record on 2026-11-03. Parametrize on the entry and assert:

| Entry | Expected result |
|---|---|
| `php` | match, `structured`, `versions_unknown` False, `labels` empty |
| `php >= 8.2` | match, `versions_unknown` False, `labels` empty |
| `php >= 8.5` | match, `versions_unknown` False, `labels` empty |
| `php >= 9.0` | `None` |

The same test **before** the fix gives a match with `versions_unknown` True for all three `min_version` entries. Note it in the debug log as the red phase.

### Fixture capture (`tests/fixtures/euvd/euvd_php_branch_wildcard.json`)

Checked on 2026-11-03: the EUVD full-text search returns exactly this record, in the `/search` page shape the adapter consumes:

```sh
B=https://euvdservices.enisa.europa.eu/api
curl "$B/search?text=EUVD-2026-107260&size=10&page=0" | jq . > tests/fixtures/euvd/euvd_php_branch_wildcard.json
```

- Result on 2026-11-03: `total` 1, one item.
  - `id` `EUVD-2026-107260`, aliases `CVE-2026-109618\n`, `baseScore` 9.2.
  - `dateUpdated` `Oct 30, 2026, 6:31:35 PM`.
  - `enisaIdProduct` holds four `PHP` rows: `8.2.* <8.2.35`, `8.5.* <8.5.12`, `8.4.* <8.4.27`, `8.3.* <8.3.36`.

  If the live record has changed, record whatever it holds. The test must read the dates and rows from the fixture rather than hard-code them, except the four version texts, which AC 8 pins.
- **Use `/search`, not `/enisaid?id=…`.** `/enisaid` returns a bare item with extra fields (`enisaIdAdvisory`, `enisaIdVulnerability`), not a page. The adapter's payload models are `extra="ignore"`, but the contract tests mock `/search` pages.
- **Behind the TLS-intercepting proxy,** use `curl --cacert ~/ca.pem` in your own shell only. Never `-k`, and never write the CA path into the repo or the README. The README already says this in its header.
- **Never edit the fixture by value** (`tests/fixtures/README.md` rule).
- **README row** for the `euvd/` table. Put the capture date in the row, because the README header says "Recorded on 2026-10-26":

  | `euvd_php_branch_wildcard.json` | Recorded on 2026-11-03: `curl "$B/search?text=EUVD-2026-107260&size=10&page=0" \| jq .` (PHP2Shell, CVE-2026-109618; total 1): four `PHP` rows in the PHP CNA branch-wildcard format `8.2.* <8.2.35` … `8.5.* <8.5.12` (wixyvir/cvestream#11) |

- The sibling record EUVD-2026-107265 (CVE-2026-109619) has the same four rows. One record is enough; do not add it.

### End-to-end test design (`tests/contract/euvd/test_euvd.py`)

Follow `test_normal_record_is_fully_mapped`: it uses `_mock_dump`, `_mock_pages` with `_whole(...)`, `make_adapter` and the `clock` fixture.

```python
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("entry", "matches"),
    [
        (ProductEntry(name="php"), True),
        (ProductEntry(name="php", min_version=Version("8.2")), True),
        (ProductEntry(name="php", min_version=Version("8.5")), True),
        (ProductEntry(name="php", min_version=Version("9.0")), False),
    ],
    ids=["php", "php-8.2", "php-8.5", "php-9.0"],
)
async def test_php_branch_wildcard_rows_filter_by_minimum_version(
    respx_mock, make_adapter, clock, entry, matches
) -> None:
    clock.set(datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC))  # after the record's dateUpdated
    _mock_dump(respx_mock)
    _mock_pages(respx_mock, {0: _whole(_raw("euvd_php_branch_wildcard.json"))})
    adapter, http = make_adapter(page_size=100)
    async with http:
        result = await adapter.fetch(None)  # first run: now - 14 days
    record = next(r for r in result.records if r.record_id == "EUVD-2026-107260")
    assert sorted(a.value.version_text for a in record.affected) == [...the four rows...]
    normalized = normalize(record)
    assert normalized is not None
    vulnerability = build_vulnerability([normalized])
    endpoint = Endpoint(id="team-php", name="PHP", products=(entry,))
    outcome = match(vulnerability, endpoint)
    if matches:
        assert outcome is not None
        assert outcome.matches[0].structured is True
        assert outcome.matches[0].versions_unknown is False
        assert outcome.labels == frozenset()
    else:
        assert outcome is None
```

- **Clock.** The shared `NOW` of this module is 2026-10-26. The record's `dateUpdated` (2026-10-30 at capture) is later than that. The adapter caps the *cursor* at fetch time but still emits the record (`test_future_update_date_does_not_park_the_cursor_ahead`). Set the clock after `dateUpdated` anyway, so the test reads like a real fetch: the fixture's own `dateUpdated` plus a few days, still inside the 14-day first-run window. If you recapture later and `dateUpdated` moves, move the clock with it.
- **Imports:** `from cvestream.pipeline.normalize import normalize`, `from cvestream.pipeline.correlate import build_vulnerability`, `from cvestream.domain.matcher import match` and `from cvestream.domain.endpoint import Endpoint, ProductEntry`, plus `Version` from `packaging.version`. Tests are not under the import-linter contracts (`source_modules` cover `cvestream` only). `tests/integration/test_pipeline_end_to_end.py` already imports `pipeline` from a test.
- `_mock_dump` serves the recorded KEV dump. PHP2Shell is not in it; that is fine, and the test doesn't depend on KEV.
- Type annotations: copy the fixture parameter types of `test_normal_record_is_fully_mapped` so `ty check` passes. They are `respx.MockRouter`, `Callable[..., tuple[EuvdAdapter, HttpClient]]` and `FakeClock`.

### Documentation edits

- **`docs/sources.md`, EUVD, "Products" bullet.** It currently reads "`product_version` (free text, kept as is: parsing belongs to the matcher)". Add a sentence listing the formats `domain/version.py` understands (the same list as the docstring). Name the PHP CNA branch format with its PHP2Shell example. State that anything else is an unknown version: it passes labelled "versions unknown" and is never dropped by `min_version`.
- **`docs/configuration.md`,** in the product-entry paragraph (line 85) or "How an entry matches" (line 87). Add one sentence: `min_version` is checked against the affected versions the source gives. An entry drops a vulnerability only when every row that names the product parses and none of them reaches the minimum; otherwise the item passes labelled "versions unknown". Link the format list in [`sources.md`](sources.md#euvd-euvd). Keep it to one sentence; this documents the existing rule and the new formats.
- **`tests/fixtures/README.md`:** the row above.
- **`_bmad-output/implementation-artifacts/deferred-work.md`:** strike through the Story 1.2 item "CVE 5 placeholder lower bounds and compound ranges", following the style of the other resolved items (`~~…~~` and then "Resolved by story 1.8: …"). Keep what remains as a live item: `<= *` (still `None`) and a standalone `0` (still `[0, 0]`, which a `min_version` drops). Mention the new out-of-scope shapes too: `8.3.x`, pre-release branches, comma-separated ranges, `>=X <*`.
- `README.md` and `CLAUDE.md`: no version-format content today. Check, and say "no change" in the completion notes.

### Existing files this story touches (current state → change → preserve)

| File | Current state | Change | Preserve |
|---|---|---|---|
| `src/cvestream/domain/version.py` | 7 patterns, `_build`, `parse_range`, `VersionRange.reaches` | 4 patterns + `_branch_end`, 4 new branches at the end of `parse_range`, docstring | every existing branch and its order, `VersionRange`, `reaches`, `parse_version` |
| `tests/unit/domain/test_version.py` | `RANGES`, `REACHES`, `test_parse_version` | new lists and tests | every existing row unchanged |
| `tests/unit/domain/test_matcher.py` | 1.2 and 1.7 tests | one new parametrized test | every existing test unchanged |
| `tests/contract/euvd/test_euvd.py` | adapter contract tests | one new parametrized test | helpers unchanged |
| `tests/fixtures/euvd/euvd_php_branch_wildcard.json` | does not exist | new, recorded | n/a |
| `tests/fixtures/README.md` | `euvd/` and `cisa_kev/` tables | one row | existing rows |
| `docs/sources.md` | EUVD "Products" bullet | format list | the rest |
| `docs/configuration.md` | product-entry paragraphs | one `min_version` sentence | the rest |
| `_bmad-output/implementation-artifacts/deferred-work.md` | open Story 1.2 item | struck through, remainder kept | other items |

No existing EUVD fixture has a `*`, `>=`, `>` or `unspecified` in `product_version` (checked on 2026-11-03: 21 distinct values). So `test_pipeline_end_to_end.py`, `test_restart_no_repost.py` and the other contract tests cannot change behaviour.

### Architecture compliance

- `domain/version.py` is the **only** module that parses versions (architecture "Versions", project-context "Versions"). Do not parse anywhere else, not even in tests' helpers beyond `Version(...)` literals.
- `domain` imports only stdlib and `packaging` (import-linter `domain` contract; `uv run lint-imports` must keep 7 contracts kept).
- Recall over precision (FR-25): anything not understood stays `None` → "versions unknown", never dropped.
- Fixtures recorded from real APIs, never hand-written, named after what they show (architecture "Testing", project-context "Testing Rules").
- Network blocked in tests (pytest-socket); respx mocks `/search` and `/kev/dump`.
- No pattern changes → no `architecture.md` amendment.

### Library / framework requirements

- `packaging` (already a dependency; version in `uv.lock`). Relevant API: `Version(text)` (raises `InvalidVersion`), `.release` (tuple of ints, `v` prefix and leading zeros normalized), comparisons (`Version("8.2") == Version("8.2.0")`). No new dependency, no `uv add`, `uv.lock` untouched.
- Stdlib `re` (`fullmatch`, `re.IGNORECASE` only on `_PLACEHOLDER_BELOW`).

### Testing requirements

- Pure domain tests: no database, no network, no mocks (`tests/unit/domain/`).
- Red first: record in the debug log that the new `test_version.py` rows and the matcher test failed before the change.
- The contract test uses the anyio plugin (`@pytest.mark.anyio`), not pytest-asyncio.
- Full gate: every command in `CLAUDE.md`. Expect the passed count to rise from the baseline by the number of new cases (about 75).

### Previous story intelligence (1.7)

- 1.7 changed `matcher.py` (path rule, `match_description`); do not revert or reformat it. Its docstring already says version handling is fail-open.
- 1.7 kept "every existing test passes unchanged" as an AC and proved red-then-green in its debug log. Do the same here.
- `docker build --target runtime` failed locally in 1.7 because the Docker daemon does not trust the TLS-intercepting proxy (`x509: certificate signed by unknown authority`). Verification was not disabled; CI ran that gate. This story changes no `Dockerfile`, `pyproject.toml` or `uv.lock`, so the same applies.
- 1.7 review: one decision recorded as an assumed risk with tests pinning it. Do the same for the out-of-scope shapes here: they are pinned as `None` rows, so a later change is a visible decision.

### Git intelligence

- `main` at `f5f21b2`: `docs(story-1.8): add story 1.8 to epic 1 by course correction` (epics, sprint status, change proposal). Before it: `449e7c6` (epic 2 scope), `5281fdb` (epic 1 marked done, now reopened), merge of PR wixyvir/cvestream#9 (story 1.7).
- Commit style of 1.7: `feat(story-1.7): …`, `test(story-1.7): …`, `docs(story-1.7): …`, `chore(story-1.7): mark story ready for review`.

### Commits (suggested, one concern each)

1. `test(story-1.8): record the PHP2Shell EUVD record with branch-wildcard versions` (fixture + README row)
2. `fix(story-1.8): parse branch-wildcard version ranges` (`_BRANCH*`, `_branch_end`, branch rows and reaches rows)
3. `fix(story-1.8): parse compound ranges and placeholder lower bounds` (`_COMPOUND`, `_PLACEHOLDER_BELOW`, their rows)
4. `test(story-1.8): filter PHP2Shell by minimum version through the adapter and the matcher` (matcher unit test + contract test)
5. `docs(story-1.8): list the understood version formats` (docstring, `sources.md`, `configuration.md`, `deferred-work.md`)
6. `chore(story-1.8): mark story ready for review` (story file, sprint status)

Every commit message ends with the attribution line from the session's system instructions.

### Latest technical information

- No library change. `packaging` behaviour used here was verified on 2026-11-03 with the locked version: `Version("v8.2").release == (8, 2)`, `Version("V8.2")` parses, `Version("08.2").release == (8, 2)`, `Version("8.2") == Version("8.2.0")`, `Version("8.2.0").release == (8, 2, 0)`.
- Live EUVD on 2026-11-03: `/search?text=<EUVD id>` returns the single record as a page (`total` 1); `/enisaid?id=` returns a bare item with two extra fields. Both answered 200 through the proxy with `--cacert ~/ca.pem`.

### Project Structure Notes

- All paths follow the architecture tree (`src/cvestream/domain/version.py`, `tests/unit/domain/`, `tests/contract/euvd/`, `tests/fixtures/euvd/`). No new module, no new package.
- No conflict detected with the unified structure.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.8: Branch-wildcard and compound version ranges]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-01-php-branch-wildcard.md#3. Recommended approach] (semantics, out-of-scope list)
- [Source: wixyvir/cvestream#11] (problem, cause, acceptance)
- [Source: _bmad-output/planning-artifacts/architecture.md] ("Versions" consistency rule; `domain` purity; testing layout)
- [Source: _bmad-output/project-context.md#Language-Specific Rules] ("Versions", "Unknown is None"), [#Testing Rules], [#Critical Don't-Miss Rules] (recall over volume)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md#Deferred from: code review of 1-2-vulnerability-model-and-matching-engine.md]
- [Source: _bmad-output/implementation-artifacts/1-7-description-fallback-precision.md#Dev Agent Record]
- [Source: src/cvestream/domain/version.py], [src/cvestream/domain/matcher.py#_match_entry], [src/cvestream/sources/euvd.py#_affected]

## Dev Agent Record

### Agent Model Used

claude-sonnet-5-5 (Claude Code, `bmad-dev-story`)

### Debug Log References

- Baseline on `main` (`f5f21b2`): 1010 passed, 2 deselected.
- Red phase, run twice:
  - the new `test_version.py` rows failed before each parser change (27 failed for the branch rows, then 18 failed for the compound and placeholder rows);
  - with `domain/version.py` restored to `f5f21b2`, the matcher test (`php-8.2`, `php-8.5`, `php-9.0`: versions unknown, so they matched with a label or were not dropped) and the EUVD contract test failed the same 3 cases each. `php` passed.
- **Deviation from the "Parser design" section:** `_PLACEHOLDER_BELOW` cannot sit after `_HYPHEN`. `unspecified <8.2.29` already matches the existing `_FROM_TO` (`unspecified` is a valid `_V` token), which then calls `_build`, fails to parse the bound and **returns `None` from inside that branch**, so the later branch is never reached. The placeholder check now runs just before `_FROM_TO`. This changes no existing input: only strings starting with `unspecified` or `*` followed by an operator reach it, and they were `None` before. `* <X` was never affected because `_V` excludes `*`. The new branch/compound branches stay after `_HYPHEN`.
- Final: 1080 passed, 2 deselected (70 new cases). `ruff check`, `ruff format --check`, `ty check`, `lint-imports` (7 kept), `config schema --check` and `config validate endpoints.example.yaml` pass.
- `docker build --target runtime` failed locally with `x509: certificate signed by unknown authority` while fetching the ghcr.io token (TLS-intercepting proxy, same as story 1.7). Verification was not disabled; CI runs that gate. No `Dockerfile`, `pyproject.toml` or `uv.lock` change.
- Fixture captured with `curl --cacert ~/ca.pem` after a plain `curl` hit a TLS error in proxy mode.

### Completion Notes List

- Branch wildcard (`8.2.* <8.2.35`, bare `8.2.*`), compound (`>=8.0 <8.3` and variants) and placeholder lower bounds (`* <X`, `unspecified <X`) now parse in `domain/version.py`. Every other shape stays `None`; the out-of-scope ones are pinned by `None` rows.
- `VersionRange`, `reaches()`, `_build()`, `parse_version()`, the signature of `parse_range()` and every existing `RANGES`, `REACHES` and matcher test are unchanged.
- PHP2Shell (EUVD-2026-107260) recorded from the live API on 2026-11-03: its four `PHP` rows go through the real EUVD adapter, `normalize()`, `build_vulnerability()` and `match()`. `php`, `php >= 8.2` and `php >= 8.5` match with no label; `php >= 9.0` does not match.
- Docs: `version.py` docstring, `docs/sources.md` (EUVD "Products"), one sentence in `docs/configuration.md`, `tests/fixtures/README.md`, `deferred-work.md` (Story 1.2 item struck through, remainder kept as a live item). `README.md` and `CLAUDE.md` describe no version formats: no change.
- Behaviour change for the PR description: an item whose product rows all parse and none reaches an entry's `min_version` is now dropped for that entry (FR-13). Items already published are not retracted.
- Ultimate context engine analysis completed - comprehensive developer guide created (2026-11-03).

### File List

- `src/cvestream/domain/version.py` (modified)
- `tests/unit/domain/test_version.py` (modified)
- `tests/unit/domain/test_matcher.py` (modified)
- `tests/contract/euvd/test_euvd.py` (modified)
- `tests/fixtures/euvd/euvd_php_branch_wildcard.json` (new)
- `tests/fixtures/README.md` (modified)
- `docs/sources.md` (modified)
- `docs/configuration.md` (modified)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified)
- `_bmad-output/implementation-artifacts/1-8-branch-wildcard-and-compound-version-ranges.md` (this story)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified)

### Change Log

- 2026-11-03: Implemented story 1.8: branch-wildcard, compound and placeholder-lower-bound version ranges, PHP2Shell end-to-end test, docs. Status set to review.
