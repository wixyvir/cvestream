---
baseline_commit: 3b67025
---

# Story 4.1: require_score endpoint rule

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Created from GitHub issue wixyvir/cvestream#43. -->

## Story

As a channel subscriber,
I want an optional endpoint rule to drop vulnerabilities without any score from any source,
so that my busy endpoint does not receive many "severity unknown" items that I cannot triage yet, while still keeping exploited vulnerabilities even without a score.

Added from GitHub issue wixyvir/cvestream#43. The real case: a BI platform endpoint with `min_cvss: 4.0` still gets every fresh OpenSSL record before EUVD scores it (for example EUVD-2026-126927, EUVD-2026-126928 and EUVD-2026-126929, all "severity unknown" on the day they were published).

## TL;DR

- **New endpoint rule** `require_score: bool`, default `false`, in `domain/endpoint.Rules` and in the v1 config contract (`config/models.EndpointConfig`). The change is additive: no schema version bump, same as `match_description` in story 1.7.
- **When `true`**, `domain/matcher.match()` drops a vulnerability for which no source gives a score (`max_score(...) is None`). The check runs after `kev_only` and before `min_cvss`, and the score is computed once.
- **Exception:** a known-exploited vulnerability (`vulnerability.is_known_exploited`, CISA KEV **or** EU KEV) is never dropped for lack of a score. With `min_cvss` set it keeps the `score_unknown` label, as today.
- **Independent of `min_cvss`:** it works with or without a threshold. A real `0.0` score counts as a score.
- **Nothing is lost:** when a source scores a dropped vulnerability later, its record changes, the pipeline matches it again and `decide()` publishes it as `new`, because it has no publication history. This works today and needs no change in `pipeline/` or `publication.py`.
- **Default behaviour does not change** (FR-25, recall first). `require_score` is an opt-out, and `architecture.md` and `project-context.md` are amended in the same PR to say so.
- **Files touched:** `domain/endpoint.py`, `domain/matcher.py`, `config/models.py`, `schemas/config-v1.json` (regenerated), tests (`test_matcher.py`, `test_endpoint.py`, `test_models.py`, `test_run.py`), `docs/configuration.md`, `docs/operations.md`, `endpoints.example.yaml`, `CHANGELOG.md`, `architecture.md`, `project-context.md`, `epics.md`.

## Acceptance Criteria

1. **Domain rule.** `domain/endpoint.Rules` gains `require_score: bool = False`, declared after `kev_only`. `Rules()` still means "no filtering": `Rules().require_score is False`.

2. **Unscored is dropped, KEV is kept.** In `domain/matcher.match()`, with `endpoint.rules.require_score` `True`:
   - the highest score is computed **once** (`score = max_score(vulnerability.scores)`) and reused by the `min_cvss` check;
   - a vulnerability with `score is None` (no score, or every source says unscored: `[]`, `[None]`, `[None, None]`) returns `None` (no match);
   - **except** when `vulnerability.is_known_exploited` is true (a CISA KEV or EU KEV entry). Such a vulnerability passes the rule; with `min_cvss` set it keeps the `PassThrough.SCORE_UNKNOWN` label, and without `min_cvss` it carries no score label (same as today, see Open questions Q1);
   - a vulnerability with at least one score (`[None, 9.8]`, `[0.0]`) is not affected by the rule;
   - the check runs after product matching and `kev_only`, and before the `min_cvss` threshold. Product matching, `versions_unknown` labelling and the `MatchResult` shape do not change.

3. **No dependency on `min_cvss`.** The rule works the same with `min_cvss` set or `None`. Under `kev_only: true` it has no effect, because every vulnerability that reaches the check is known-exploited.

4. **Default behaviour is unchanged.** With `require_score` `False` (the default), every existing matcher, config and pipeline test passes unchanged. Unscored vulnerabilities still pass `min_cvss` with `score_unknown` (FR-14, FR-25).

5. **Late score is published as new.** A vulnerability dropped by `require_score` and later scored by a source (its record changes, so the run touches it) is matched again by `pipeline/run.py`. Because it has no publication history on that endpoint, `domain/publication.decide()` publishes it once, as `new` (GUID `cvestream:{endpoint_id}:{primary_id}:new`), if the score is at or above `min_cvss`. If an unscored vulnerability becomes known-exploited, it is published as `new` too. No change is needed in `pipeline/` or `domain/publication.py`; tests prove the behaviour.

6. **Config contract.** `config/models.EndpointConfig.require_score` is a strict `bool` (`_CONFIG` already sets `strict=True`), default `False`, with a `description` (the schema test requires one on every property). `EndpointConfig.to_domain()` passes it to `Rules(require_score=...)`. `require_score: "yes"` or `1` is rejected with the path `endpoints.0.require_score`. `schemas/config-v1.json` is regenerated with `uv run cvestream config schema`, in the same commit as the model change. `SCHEMA_VERSION` stays `1`: the field is optional, so every existing document stays valid.

7. **Tests** (details under "Testing requirements"):
   - matcher: unscored dropped with and without `min_cvss`; unscored CISA KEV and EU KEV kept (label as in AC 2); `[None, 9.8]` and `[0.0]` kept; scored vulnerabilities unaffected; default `False` changes nothing; `kev_only` with `require_score`;
   - domain endpoint: `Rules()` default;
   - config: default, mapping to the domain, strict bool rejection;
   - pipeline: unscored and then scored is published once, as `new`; unscored and then KEV is published as `new`.

8. **Documentation follows behaviour in the same PR:**
   - `docs/configuration.md`: a new table row, `require_score` in the YAML example, a new "Score rules" paragraph (see Dev Notes), and the strict-bool pitfall;
   - `docs/operations.md`: `score_unknown` in `cycle.summary` does not count items held back by `require_score`;
   - `endpoints.example.yaml`: the field with a comment;
   - `CHANGELOG.md`: an `### Added` entry under `[Unreleased]`;
   - `architecture.md` and `project-context.md`: FR-25 now has an opt-out (pattern change, amended in this PR as AGENTS.md requires);
   - `epics.md`: Story 4.1 gets its acceptance criteria, and Story 3.2 lists `--require-score`.

## Tasks / Subtasks

- [x] **Task 1: Domain rule** (AC: 1)
  - [x] 1.1 Add `require_score: bool = False` to `Rules` in `src/cvestream/domain/endpoint.py`, after `kev_only`, with a one-line comment: unscored means `max_score()` is `None`, and known-exploited is exempt.
  - [x] 1.2 `tests/unit/domain/test_endpoint.py`: extend `test_rules_default_to_no_filtering` with `assert rules.require_score is False`.

- [x] **Task 2: Matcher** (AC: 2, 3, 4)
  - [x] 2.1 In `match()` (`src/cvestream/domain/matcher.py`), after the `kev_only` check, compute `score = max_score(vulnerability.scores)` once. Add the drop: `if rules.require_score and score is None and not vulnerability.is_known_exploited: return None`. Then make the `min_cvss` block reuse `score` instead of calling `max_score` again.
  - [x] 2.2 Update the module docstring. It says unknown data "never drops it silently", which is no longer true without a qualifier. Add one sentence: an endpoint can opt out for scores with `require_score`, and a known-exploited vulnerability is still let through.
  - [x] 2.3 Do not touch `_match_entry`, `affected_hits`, `ProductMatch`, `MatchResult` or `PassThrough` (story 4.2 runs in parallel and edits `_match_entry` / `affected_hits`, see "Parallel story 4.2").

- [x] **Task 3: Config contract** (AC: 6)
  - [x] 3.1 Add `require_score: bool = Field(default=False, description=...)` to `EndpointConfig` in `src/cvestream/config/models.py`, right after `kev_only`. Suggested description: `"Drop vulnerabilities that no source has scored yet, except known-exploited ones (CISA KEV or EU KEV)."`
  - [x] 3.2 Pass it in `to_domain()`: `rules=Rules(min_cvss=self.min_cvss, kev_only=self.kev_only, require_score=self.require_score)`.
  - [x] 3.3 Run `uv run cvestream config schema`, commit `schemas/config-v1.json` with the model change, and check with `uv run cvestream config schema --check`.

- [x] **Task 4: Tests** (AC: 4, 5, 7)
  - [x] 4.1 `tests/unit/domain/test_matcher.py`: add a `require_score: bool = False` keyword to the `_endpoint()` helper and pass it to `Rules`. Add a `# --- require_score (FR-14, FR-25 opt-out) ---` section after the CVSS threshold section (cases under "Testing requirements").
  - [x] 4.2 `tests/unit/config/test_models.py`: add `assert endpoint.require_score is False` to `test_endpoint_defaults`. Add a mapping test, `_only_endpoint(require_score=True).to_domain().rules == Rules(require_score=True)`. Add `(_document(_endpoint(require_score="yes")), "endpoints.0.require_score")` and `(_document(_endpoint(require_score=1)), "endpoints.0.require_score")` to the `test_rejected` parametrization. That parametrization has an explicit `ids=[...]` list, so add one id for each new case (for example `"require-score-string"`, `"require-score-int"`) at the same position.
  - [x] 4.3 `tests/unit/pipeline/test_run.py`: add the two pipeline tests (unscored then scored → one `new`; unscored then KEV → `new`).
  - [x] 4.4 Keep every existing test unchanged (AC 4 is a regression guard).

- [x] **Task 5: Documentation** (AC: 8)
  - [x] 5.1 `docs/configuration.md`: add `require_score: false` to the YAML example after `kev_only`. Add a table row `| endpoints[].require_score | no | default false; drop vulnerabilities that no source has scored yet, except known-exploited ones |`. Add the "Score rules" paragraph after the table and before "A product entry has…". Extend the "YAML pitfalls" strict list with `require_score: "yes"`.
  - [x] 5.2 `docs/operations.md` (around line 125, the `score_unknown` bullet): add that items held back by `require_score` are not published, so they are not counted.
  - [x] 5.3 `endpoints.example.yaml`: in the `team-php` endpoint, after `kev_only`, add `require_score: false` with the comment block from Dev Notes.
  - [x] 5.4 `CHANGELOG.md`: under `## [Unreleased]`, add `### Added` and one entry for the `require_score` endpoint rule (`endpoints.yaml`, config contract v1, additive), referencing wixyvir/cvestream#43.
  - [x] 5.5 Check `README.md`: it does not list rules today, so no change is expected. Say so in the completion notes.

- [x] **Task 6: Planning artifacts (pattern change)** (AC: 8)
  - [x] 6.1 `_bmad-output/planning-artifacts/architecture.md`:
    - the F3 row of the Requirements Overview: "Filters: CVSS threshold and KEV-only" → "Filters: CVSS threshold, KEV-only and an opt-in `require_score` (drops unscored vulnerabilities except known-exploited ones). Recall wins over volume by default";
    - the Valkey key layout line `cvestream:v1:endpoint:{id} JSON {name, enabled, min_cvss, kev_only}` → add `require_score`, so story 3.1 picks it up;
    - the "Requirements to Structure Mapping" row `FR-14, 15, 25`: add "`require_score` opt-out".
  - [x] 6.2 `_bmad-output/project-context.md`, "Recall over volume (FR-25)": replace "No score → never dropped by `min_cvss`." with "No score → never dropped by `min_cvss`. Only the opt-in endpoint rule `require_score` drops an unscored vulnerability, and never a known-exploited one."
  - [x] 6.3 `_bmad-output/planning-artifacts/epics.md`: give Story 4.1 Given/When/Then acceptance criteria that summarize AC 1 to 6, and add `--require-score` to the Story 3.2 options example (line ~465: `--min-cvss`, `--kev-only`).

- [x] **Task 7: Gates and bookkeeping**
  - [x] 7.1 Run every gate command from `AGENTS.md`: `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`, `uv run lint-imports`, `uv run pytest`, `uv run cvestream config schema --check`, `uv run cvestream config validate endpoints.example.yaml`, `docker build --target runtime -t cvestream:local .`. The docker build fails behind the local proxy on a ghcr.io x509 error; if it does, rely on CI and say so in the completion notes.
  - [x] 7.2 Fill the Dev Agent Record and the File List, then set this story and `sprint-status.yaml` to `review`.

## Dev Notes

### Scope boundaries (read first)

- **In scope:** one boolean endpoint rule, its contract, the matcher check, tests, docs and the planning amendments.
- **Out of scope:**
  - counting held-back items in `cycle.summary` (Q2);
  - a CLI flag: the CLI CRUD commands do not exist yet (Story 3.2, backlog). Only the epics.md text is amended;
  - Valkey provider code: not implemented yet (Story 3.1). Only the key layout line in architecture.md is amended;
  - re-evaluating already-dropped vulnerabilities when the rule is switched off (Q3);
  - any change to `pipeline/`, `state/`, `render.py` or `publication.py`.
- **Do not** treat "unscored" as `0` under `min_cvss`. The issue rejected that alternative because it would silently change every existing endpoint that sets a threshold.

### Matcher design (`src/cvestream/domain/matcher.py`)

Current `match()` tail (after product matching):

```python
rules = endpoint.rules
if rules.kev_only and not vulnerability.is_known_exploited:
    return None
labels: set[PassThrough] = set()
if rules.min_cvss is not None:
    score = max_score(vulnerability.scores)
    if score is None:
        labels.add(PassThrough.SCORE_UNKNOWN)
    elif score < rules.min_cvss:
        return None
if all(m.versions_unknown for m in matches):
    labels.add(PassThrough.VERSIONS_UNKNOWN)
```

Target shape:

```python
rules = endpoint.rules
if rules.kev_only and not vulnerability.is_known_exploited:
    return None
score = max_score(vulnerability.scores)
# Opt-out of FR-25 for scores: an exploited vulnerability is never silenced.
if rules.require_score and score is None and not vulnerability.is_known_exploited:
    return None
labels: set[PassThrough] = set()
if rules.min_cvss is not None:
    if score is None:
        labels.add(PassThrough.SCORE_UNKNOWN)
    elif score < rules.min_cvss:
        return None
...
```

- `max_score()` (`domain/severity.py`) returns the highest score among sources that scored, or `None`. `Sourced(None)` means "this source says unscored". A `0.0` is a real CVSS "none" score: the EUVD adapter already turns EUVD's unscored `0.0` placeholder into `None` (`docs/sources.md`, "Unscored records"). So `[0.0]` is scored and kept.
- `Vulnerability.is_known_exploited` is `bool(self.kev)` (`domain/model.py`), true for **CISA KEV and EU KEV** entries. Issue wixyvir/cvestream#43 says "(CISA KEV)", and the `kev_only` field description also says only "CISA KEV". The code covers both catalogues, and so must the docs for this rule.
- The rule goes after product matching, so an unmatched vulnerability costs nothing extra. It goes before `min_cvss`, so a dropped item never gets a `score_unknown` label.
- `domain` stays pure: no new import (`max_score` is already imported).

### Why the pipeline needs no change (AC 5)

- `pipeline/run.py:run()` matches only `recent` (vulnerabilities touched in this run and active in the last 14 days) for an endpoint with publication history, or `backfill` (active in the last 7 days) for one without.
- When EUVD scores a record, its content changes, so `correlate()` marks the vulnerability `touched` and refreshes `activity_at` from the source's update time. The vulnerability is then a candidate again, `match()` now passes, and `decide()` sees an empty history and returns `NEW` (`domain/publication.py:_kind`).
- A KEV addition (CISA catalogue, or the EU KEV dump the `euvd` adapter loads at every fetch) also changes or adds a record, so the vulnerability is touched and published as `new`, since it has no history.
- Caveat for the tests: if the endpoint has no publication history, every run re-evaluates the 7-day backfill set anyway. To prove the "matched again when records change" path, the endpoint must already have one publication (see the pipeline test design).

### Domain entry (`src/cvestream/domain/endpoint.py`)

`Rules` is `@dataclass(frozen=True, slots=True)` with `min_cvss: float | None = None` and `kev_only: bool = False`. Append `require_score: bool = False`. Keep the field order, and keep frozen and slots. `Endpoint.rules` defaults to `Rules()`, so nothing else changes.

### Config contract (`src/cvestream/config/models.py`)

- `_CONFIG = ConfigDict(extra="forbid", strict=True)`: a strict `bool` rejects `"yes"`, `"true"` and `1` without a custom validator, the same way as `kev_only`, `enabled` and `match_description`.
- Field order in `EndpointConfig`: `id, name, enabled, min_cvss, kev_only, require_score, token, products`. The schema property order follows, which is fine because the file is regenerated.
- `tests/unit/config/test_schema.py::test_every_property_has_a_description` fails if the `Field` has no description. `test_committed_schema_is_fresh` fails if the schema is not regenerated.
- No change to `ConfigDocument`, `yaml_provider.py` or `provider.py`. The YAML provider validates through `ConfigDocument`, so `endpoints.yaml` accepts the key at once.

### Testing requirements

Matcher (`tests/unit/domain/test_matcher.py`). Use the `make_vulnerability` fixture from `tests/unit/domain/conftest.py` (`scores=[...]` takes `float | None`; `kev=True` adds a CISA KEV entry) and `PHP = ProductEntry(name="php")`, with `description="PHP flaw"`.

| Case | Endpoint | Scores / KEV | Expected |
|---|---|---|---|
| unscored dropped, no threshold | `require_score=True` | `[]`, `[None]`, `[None, None]` (parametrize) | `None` |
| unscored dropped, with threshold | `require_score=True, min_cvss=4.0` | `[]`, `[None]` | `None` |
| unscored CISA KEV kept, threshold | `require_score=True, min_cvss=4.0` | `[None]`, `kev=True` | match, labels `{SCORE_UNKNOWN}` |
| unscored CISA KEV kept, no threshold | `require_score=True` | `[]`, `kev=True` | match, labels `frozenset()` |
| unscored EU KEV kept | `require_score=True` | `[]`, EU KEV entry | match |
| one source scored | `require_score=True, min_cvss=7.0` | `[None, 9.8]` | match, no label |
| real zero score | `require_score=True` | `[0.0]` | match (a 0.0 is scored) |
| scored below threshold | `require_score=True, min_cvss=7.0` | `[5.0]` | `None` (threshold unchanged) |
| scored, no threshold | `require_score=True` | `[5.0]` | match, no label |
| default off | `Rules()` | `[]` | match (regression: today's behaviour) |
| with kev_only | `require_score=True, kev_only=True` | `[]`, `kev=True` | match; `kev=False` → `None` |
| versions label still set | `PHP_8_2`, `require_score=True` | `[9.8]`, description only | labels `{VERSIONS_UNKNOWN}` |

For the EU KEV case (`test_matcher.py` imports neither `KevCatalog`, `KevEntry`, `Sourced` nor `OBSERVED_AT` today: import the first three from `cvestream.domain.model` and use any aware UTC datetime, or import `OBSERVED_AT` from `tests.unit.domain.conftest`), build the vulnerability with `dataclasses.replace(make_vulnerability(description="PHP flaw"), kev=(Sourced(KevEntry(catalog=KevCatalog.EU_KEV), source="eu_kev", observed_at=OBSERVED_AT),))`. Or add a small `kev_catalog` parameter to the fixture, but keep the fixture's existing behaviour.

Config (`tests/unit/config/test_models.py`): use the existing helpers `_endpoint(**overrides)`, `_document()`, `_only_endpoint()` and the `test_rejected` parametrization, which takes `(data, path)` and checks the error path.

Pipeline (`tests/unit/pipeline/test_run.py`). Use the existing helpers: `_endpoint()`, `_snapshot()`, `_euvd(clock, score=..., record_id=..., cve=...)`, `_kev(clock, cve=...)`, `_batch()`, `_kinds()`, `fake_clock.advance(...)`. Build an endpoint with `rules=Rules(require_score=True)` (import `Rules` from `cvestream.domain.endpoint`), or with `replace(_endpoint().rules, require_score=True)` as `test_late_kev_status_on_an_unmatched_endpoint_is_new` does.

- `test_an_unscored_vulnerability_held_back_by_require_score_is_new_once_scored`:
  1. Run 1: a first, scored vulnerability (`record_id="EUVD-2026-0"`, `cve="CVE-2026-1000"`, `score=9.8`) is published as `new`, so the endpoint has history and the next runs take the `recent` path.
  2. Run 2: `_euvd(fake_clock, score=None)` for `CVE` gives `decisions == ()`.
  3. Run 3: the same record with `score=9.8` gives `_kinds(result) == [("team-php", "new")]`, and the stored GUID is `cvestream:team-php:CVE-2026-1001:new`.
  4. Run 4: rerunning the same batch decides nothing.
- `test_an_unscored_vulnerability_held_back_by_require_score_is_new_once_exploited`: same setup; then `_kev(fake_clock)` for `CVE` gives `[("team-php", "new")]` (not `kev`: there is no history).
- Optional, cheap: the same flow with `min_cvss=7.0` and a late score of `5.0` publishes nothing (threshold still applies).

Testing rules that apply: domain tests stay pure (no DB, no mocks); `FakeClock` only, no sleeps; network blocked by pytest-socket; anyio is not needed (everything is sync).

### Documentation edits

`docs/configuration.md`, new paragraph after the endpoint table (suggested wording, adjust to the surrounding style):

> **Score rules.** `min_cvss` compares the highest score any source gives. A vulnerability that no source has scored yet is never dropped by the threshold: it is published with "severity unknown" (recall over volume). Set `require_score: true` to hold such vulnerabilities back until a source scores them, with or without `min_cvss`. A known-exploited vulnerability (CISA KEV or EU KEV) is never held back for lack of a score. A held-back vulnerability is not lost: when a source scores it, it is published as new, if it reaches `min_cvss`. Trade-off: CERT-EU advisories carry no score, so an endpoint with `require_score` loses CERT-EU's early warning and waits for EUVD's score. Switching the rule off later does not bring back what it held back: such a vulnerability is published only when one of its records changes.

`endpoints.example.yaml`, after `kev_only: false` in `team-php`:

```yaml
    # Optional, default false. true holds back vulnerabilities that no source
    # has scored yet (published once a source scores them); known-exploited
    # ones (CISA KEV or EU KEV) are never held back.
    require_score: false
```

`docs/operations.md`, `score_unknown` bullet: append "Items held back by an endpoint's `require_score` are not published, so they are not counted."

`CHANGELOG.md`, under `## [Unreleased]`:

```markdown
### Added

- `require_score` endpoint rule: holds back vulnerabilities that no source has scored yet, except known-exploited ones, until a source scores them (wixyvir/cvestream#43). Additive change to the v1 configuration contract.
```

### Existing files this story touches (current state → change → preserve)

- `src/cvestream/domain/endpoint.py`: `ProductEntry`, `Rules`, `Endpoint`. Change: one field on `Rules`. Preserve frozen, slots and the existing field order.
- `src/cvestream/domain/matcher.py`: `match()` tail and module docstring. Change: score computed once, new drop check, docstring sentence. Preserve: product matching, the path rule, version logic, label semantics, `affected_hits()`.
- `src/cvestream/config/models.py`: `EndpointConfig` plus `to_domain()`. Change: one field and the mapping. Preserve `extra="forbid"`, `strict=True`, `repr=False` on the token, and error messages that never print the token.
- `schemas/config-v1.json`: regenerated, never hand-edited.
- `tests/unit/domain/test_matcher.py`, `tests/unit/domain/test_endpoint.py`, `tests/unit/config/test_models.py`, `tests/unit/pipeline/test_run.py`: additions only, plus the `require_score` keyword on the `_endpoint()` helper in `test_matcher.py`.
- `docs/configuration.md`, `docs/operations.md`, `endpoints.example.yaml`, `CHANGELOG.md`: the edits above.
- `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md`, `_bmad-output/planning-artifacts/epics.md`: Task 6.
- This story file and `sprint-status.yaml`.

Not touched: `pipeline/run.py`, `pipeline/render.py` (its `_severity()` shows "(no score; let through)" only with the `score_unknown` label, which stays correct), `pipeline/summary.py`, `domain/publication.py`, `domain/severity.py`, `config/yaml_provider.py`, `state/*`.

### Parallel story 4.2 (wave 12: `4-1 | 4-2`)

Story 4.2 (exact product matching, wixyvir/cvestream#44) is developed in parallel and touches the same files. It adds `exact` to `ProductEntry` and `ProductEntryConfig`, and changes `_match_entry` and `affected_hits` in the matcher.

Expect conflicts in `domain/endpoint.py`, `domain/matcher.py` (module docstring), `config/models.py`, `schemas/config-v1.json`, `docs/configuration.md`, `endpoints.example.yaml`, `CHANGELOG.md`, `architecture.md`, `epics.md` and the test files. The two stories edit different classes and functions: 4.1 edits `Rules`, `EndpointConfig` and `match()`; 4.2 edits `ProductEntry`, `ProductEntryConfig`, `_match_entry` and `affected_hits`. Stay out of the 4.2 code.

Whichever PR merges second: rebase it and **regenerate** `schemas/config-v1.json` with `uv run cvestream config schema`; never merge the JSON by hand. The `CHANGELOG.md` `### Added` section is shared, so keep both entries.

### Architecture compliance

- Pure domain: `domain/matcher.py` and `domain/endpoint.py` import only stdlib, `packaging` and the project's `domain` modules (`tests/unit/domain/test_purity.py` and import-linter check this).
- Unknown stays `None`: the rule reads `score is None` and never maps unknown to `0`.
- Validation at the boundary only: the domain does not re-validate `require_score`.
- `config/models.py` is a public contract: the schema is regenerated in the same commit, and the change is additive, so `SCHEMA_VERSION` stays 1 (precedent: `match_description`, story 1.7).
- Publish-once and GUIDs: untouched. The `new` GUID for a late-scored vulnerability is built by the existing `decide()`.
- **Pattern change:** FR-25 "recall wins" now has an explicit opt-out. AGENTS.md says that changing a pattern means amending `architecture.md` (and `project-context.md`) in the same PR, which is Task 6.

### Library / framework requirements

No new dependency, no `uv add`, no `uv.lock` change. Pydantic 2.14 strict `bool` is already used for `enabled`, `kev_only` and `match_description`. Python 3.14: no `from __future__ import annotations`.

### Previous story intelligence

This is the first story of epic 4. The closest precedent is **story 1.7** (`1-7-description-fallback-precision.md`), which added an optional boolean (`match_description`) through the same layers. Its file list matches this one: `endpoint.py`, `matcher.py`, `models.py`, the regenerated schema, `test_matcher.py`, `test_endpoint.py`, `test_models.py`, `test_run.py`, `docs/configuration.md`, `endpoints.example.yaml`. Learnings to reuse:

- The contract change and the regenerated schema go in one commit; docs go in a separate `docs(...)` commit; story bookkeeping goes in a `chore(...)` commit.
- Pipeline tests that prove a "late data → `new`" path already exist and are the model to copy: `test_late_product_data_is_published_as_new` and `test_late_kev_status_on_an_unmatched_endpoint_is_new` in `tests/unit/pipeline/test_run.py`.
- Ruff rules `E,F,W,I,UP,B,DTZ,BLE,S,RUF`; `ty` with `error-on-warning`; `assert` only in tests.
- `gh` needs `SSL_CERT_FILE=$HOME/ca.pem` behind the proxy; push through the SSH remote.

### Git intelligence

`main` is at `3b67025 feat: add Epic 4 for matching and scoring enhancements`, after `1dbc2ae chore(release): v0.1.0 pre-release` and a series of Dependabot merges. The code paths this story touches have not changed since story 1.7 and the 1.8 / 2.x merges. Commit style: Conventional Commits scoped `(story-4.1)`, one concern per commit, never squashed. Suggested commits:

1. `feat(story-4.1): add the require_score endpoint rule`: domain, matcher, contract, schema and tests.
2. `docs(story-4.1): document require_score`: `docs/`, the example file and the changelog.
3. `docs(story-4.1): amend FR-25 with the require_score opt-out`: `architecture.md`, `project-context.md`, `epics.md`.
4. `chore(story-4.1): ...`: story file and sprint status.

Every commit message ends with the attribution line.

### Latest technical information

Nothing version-sensitive: the story uses existing Pydantic features and the standard library, and adds no dependency.

### Project Structure Notes

- No new module or package. Test layout mirrors `src/` (`tests/unit/domain/`, `tests/unit/config/`, `tests/unit/pipeline/`).
- Branch `story-4-1-require-score-endpoint-rule`, one PR to `main`, title `feat(story-4.1): require_score endpoint rule`. The description links this story file and wixyvir/cvestream#43, and ends with the attribution line.

### Open questions (recorded instead of asked; defaults chosen)

- **Q1: label for an unscored KEV item when `min_cvss` is unset.** The default chosen follows the issue: the item carries no `score_unknown` label, because today that label means "let through by the threshold" and is only set when `min_cvss` is set. Alternative: also add `SCORE_UNKNOWN` when the KEV exemption of `require_score` lets an item through. That would show "(no score; let through)" in the feed and count it in `cycle.summary`. This needs a reviewer decision; the default keeps the current label semantics.
- **Q2: observability of held-back items.** `cycle.summary` counts only published pass-throughs. A count of items held back by `require_score` (NFR-4) would need `match()` to return a reason or the pipeline to evaluate the rule itself. This is deferred: if the review wants it, add it to `deferred-work.md` or open a follow-up issue.
- **Q3: switching `require_score` off.** Vulnerabilities held back while the rule was on are not re-evaluated until one of their records changes. An endpoint with publication history is not backfilled again, the same as for a product-list edit (architecture.md, story 1.5 rules). This is documented as a trade-off, not fixed here.
- **Q4: CERT-EU early warning.** CERT-EU advisories never carry a score, so `require_score` delays CERT-EU-only items on that endpoint until EUVD scores them, which goes against SM-3 for that endpoint. The default chosen is to accept and document it, since the rule is opt-in. Alternative: exempt CERT-EU-sourced vulnerabilities as well. That is not requested by wixyvir/cvestream#43.
- **Q5: PRD wording.** PRD FR-14 says "Vulnerabilities with no score are never dropped by this threshold", which stays true because `require_score` is a separate rule. FR-25 does not mention the opt-out. This story amends `architecture.md`, `project-context.md` and `epics.md` only; amending the PRD is left to the PM (`bmad-correct-course` if wanted).
- **Q6: `kev_only` description says "(CISA KEV)" but it also matches EU KEV.** This inaccuracy predates this story. It is not fixed here to keep the contract diff minimal; the new `require_score` description names both catalogues.

### References

- [Source: GitHub issue wixyvir/cvestream#43] (problem, proposed solution, touch points, rejected alternatives)
- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.1: require_score endpoint rule]
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 4: Matching and scoring enhancements] (FR-14 scoring, FR-25 recall with opt-out)
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-14, FR-24, FR-25)
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements Overview] (F3 row), [#Data Architecture] (Valkey key layout, correlation and publication rules), [#Requirements to Structure Mapping]
- [Source: _bmad-output/project-context.md#Critical Don't-Miss Rules] ("Recall over volume (FR-25)", "Publish once")
- [Source: src/cvestream/domain/matcher.py#match], [src/cvestream/domain/severity.py#max_score], [src/cvestream/domain/model.py#Vulnerability.is_known_exploited], [src/cvestream/domain/publication.py#_kind], [src/cvestream/pipeline/run.py#run]
- [Source: _bmad-output/implementation-artifacts/1-7-description-fallback-precision.md] (precedent for an additive boolean in the v1 contract)
- [Source: docs/sources.md] (EUVD unscored `0.0` → `None`; CERT-EU has no score)

## Dev Agent Record

### Agent Model Used

Mistral Vibe (Mistral AI coding agent)

### Debug Log References

- Review of wixyvir/cvestream#45, comment 7062178615 (decisions: D1=B, D2 and D3 follow the recommendations); fixes applied in the same session.

### Completion Notes List

- README.md does not list rules today, so no change was needed.
- All gates pass: ruff, ruff format, ty, lint-imports, pytest (1194 passed), config schema --check, config validate. The docker build was attempted and fails behind the local proxy (DNS resolution of PyPI); per this story, CI is the gate for it.
- Review fixes (wixyvir/cvestream#45): removed the committed `test-k8s/.generated/endpoints.yaml` (feed tokens) and ignored `/test-k8s/.generated/`; qualified the matcher docstring opt-out without reflowing the paragraph; strengthened the pipeline tests (run 1 result asserted, stored GUID checked, run 4 re-matches a changed record); matcher tests use the `Rules()` default with `min_cvss`, a description-only versions case and module-level imports; docs wording now names the feed window; epics.md acceptance criteria rewritten in Given/When/Then form.
- The two tokens that were committed must still be rotated in the minikube harness (`test-k8s/down.sh --all` on the `test-early-k8s` branch): no cluster is running in this environment.
- Story 4.1 is done (PR wixyvir/cvestream#45).

### File List

- `.gitignore`
- `CHANGELOG.md`
- `_bmad-output/implementation-artifacts/4-1-require-score-endpoint-rule.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/planning-artifacts/architecture.md`
- `_bmad-output/planning-artifacts/epics.md`
- `_bmad-output/project-context.md`
- `docs/configuration.md`
- `docs/operations.md`
- `endpoints.example.yaml`
- `schemas/config-v1.json`
- `src/cvestream/config/models.py`
- `src/cvestream/domain/endpoint.py`
- `src/cvestream/domain/matcher.py`
- `tests/unit/config/test_models.py`
- `tests/unit/domain/conftest.py`
- `tests/unit/domain/test_endpoint.py`
- `tests/unit/domain/test_matcher.py`
- `tests/unit/pipeline/test_run.py`

### Change Log

- Added `require_score` endpoint rule (wixyvir/cvestream#43)
- Review fixes for wixyvir/cvestream#45 (patches 1 to 8; decisions D1=B, D2/D3 follow the recommendations)
- All tests pass (1194 tests)
- All gates pass (ruff, ruff format, ty, lint-imports, schema, validate); the docker build fails behind the local proxy, CI is the gate

