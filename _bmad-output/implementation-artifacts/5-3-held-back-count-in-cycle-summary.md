---
baseline_commit: 3c08cdd
---

# Story 5.3: Held-back count in cycle.summary

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Created from GitHub issue wixyvir/cvestream#47 (decision D3 in the review of wixyvir/cvestream#45, story 4.1 open question Q2). -->

## Story

As the operator,
I want the number of items an endpoint's `require_score` held back in each cycle,
so that a rule that silently drops CERT-EU-only and freshly published items is visible.

Added from GitHub issue wixyvir/cvestream#47 (decision D3 in the review of wixyvir/cvestream#45). Today `match()` returns `None` both for "the product does not match" and for "held back by `require_score`", and `pipeline/run.py` just `continue`s: this is the only drop on unknown data that leaves no trace (no log, label or counter). It hurts most for CERT-EU-only vulnerabilities, which never carry a score.

## TL;DR

- **Domain:** `domain/matcher.py` gains a `DropReason` enum (`no_product`, `kev_only`, `require_score`, `min_cvss`) and a pure function `match_outcome(vulnerability, endpoint) -> MatchResult | DropReason`. It holds the current body of `match()`, with each `return None` replaced by its reason. `match()` stays, with the same signature and behaviour, as a thin wrapper. The ~150 matcher tests and the contract tests that call `match()` do not change.
- **Pipeline:** `pipeline/run.py` calls `match_outcome()`. It counts `DropReason.REQUIRE_SCORE` per endpoint in a new `RunResult.held_back: Mapping[str, int]` and skips every other `DropReason`, as today.
- **Summary:** `CycleSummary` gets `held_back: Mapping[str, int]`, and `cycle.summary` logs `"held_back": {"team-php": 3}` after `versions_unknown`. Keys: every enabled endpoint with `require_score: true`, in config order, `0` included. A missing key means the rule is off. `{}` when no endpoint uses it. `count` stays the published total.
- **Semantics:** the count is per cycle. A vulnerability is counted each time a cycle evaluates it and the rule holds it back. A product mismatch, a `kev_only` drop and a known-exploited vulnerability are never counted.
- **No schema, config, state or render change.** No Alembic revision: story 5.1 owns the wave's only migration. No new dependency.
- **Docs:** `docs/operations.md` (the `cycle.summary` sample and bullets), one sentence in `docs/configuration.md` ("Score rules"), `CHANGELOG.md`. The `matcher.py` line of the source tree in `architecture.md` and the FR-25 counting rule in `project-context.md` also change, because a new domain function is a pattern change.
- **Parallel wave 13:** stories 5.1 and 5.4 run at the same time. Shared files: `docs/operations.md`, `CHANGELOG.md`, `README.md` and `sprint-status.yaml`.

## Acceptance Criteria

1. **A drop reason from the domain.** `src/cvestream/domain/matcher.py` defines `class DropReason(StrEnum)` with `NO_PRODUCT = "no_product"` (no product entry matched, exclusions and version bounds included), `KEV_ONLY = "kev_only"`, `REQUIRE_SCORE = "require_score"` and `MIN_CVSS = "min_cvss"`. It also defines `match_outcome(vulnerability: Vulnerability, endpoint: Endpoint) -> MatchResult | DropReason`:
   - it runs the checks in today's order: products, then `kev_only`, then `require_score`, then `min_cvss`;
   - it returns the reason of the **first** check that fails, so a non-KEV, unscored vulnerability on a `kev_only` + `require_score` endpoint is `KEV_ONLY`, not `REQUIRE_SCORE`;
   - it returns the same `MatchResult` as `match()` (same matches, same labels) when nothing fails.

   `domain` stays pure: no new import beyond stdlib and project `domain` modules.

2. **`match()` unchanged.** `match(vulnerability, endpoint) -> MatchResult | None` keeps its signature and behaviour: it returns the `MatchResult` from `match_outcome()`, and `None` for any `DropReason`. Every existing test in `tests/unit/domain/test_matcher.py`, `tests/contract/euvd/test_euvd.py` and `tests/contract/cert_eu/test_cert_eu.py` passes unchanged.

3. **Counted per endpoint in the run.** `pipeline/run.RunResult` gains `held_back: Mapping[str, int]` (read-only, `MappingProxyType`, like `feeds`):
   - keys: every enabled endpoint whose `rules.require_score` is true, in config order, initialised to `0`;
   - `run()` calls `match_outcome()` and adds 1 to `held_back[endpoint.id]` for each candidate vulnerability that returns `DropReason.REQUIRE_SCORE`;
   - every other `DropReason` is skipped silently, as today;
   - endpoints without the rule and disabled endpoints have no key.

4. **Reported in `cycle.summary`.** `pipeline/summary.CycleSummary` gains `held_back: Mapping[str, int]`, declared after `versions_unknown`, copied from `result.held_back` by `summarize()`. `log_summary()` writes `held_back=dict(summary.held_back)` right after `versions_unknown`: always present, `{}` when no enabled endpoint uses `require_score`. `count` stays `sum(published)`, and held-back items are not added to it.

5. **What is counted, and what is not.** With an endpoint that has `require_score: true`:
   - an unscored, non-KEV vulnerability that matches the endpoint's products is counted (`held_back == {"<id>": 1}`) and not published;
   - a vulnerability that does not match the products is **not** counted;
   - under `kev_only: true`, a non-KEV vulnerability is dropped by `kev_only` and **not** counted;
   - an unscored known-exploited vulnerability (CISA KEV or EU KEV) is published, not counted;
   - a scored vulnerability below `min_cvss` is dropped by the threshold and **not** counted.

   On the same run, an endpoint without the rule publishes the same unscored vulnerability as today (with `score_unknown` when it sets `min_cvss`).

6. **Per-cycle semantics, pinned by tests.** The count is per cycle, neither cumulative nor unique:
   - on an endpoint with publication history, a held-back vulnerability is counted again only in a cycle where it is touched again (one of its records changed), and rerunning an unchanged batch counts `0`;
   - an endpoint without any publication re-evaluates its 7-day backfill at every run, so the same held-back vulnerability is counted at every cycle until the endpoint publishes for the first time.

   Both behaviours are tested and documented (see Open questions Q1).

7. **Nothing else changes.** No change to `config/models.py`, `schemas/config-v1.json`, `state/*`, Alembic revisions, `domain/publication.py`, `pipeline/render.py` or the feeds. Every existing pipeline, summary and cycle test passes after the mechanical updates listed in Task 3: the new `held_back` argument in the `RunResult` and `CycleSummary` test constructors.

8. **Documentation follows behaviour in the same PR:**
   - `docs/operations.md`: `held_back` in the `cycle.summary` JSON sample, a new `held_back` bullet, and the `score_unknown` bullet points to it (wording under Dev Notes);
   - `docs/configuration.md`: one sentence at the end of the "Score rules" paragraph, pointing to `held_back`;
   - `CHANGELOG.md`: one `### Added` entry under `[Unreleased]`, referencing wixyvir/cvestream#47;
   - `_bmad-output/planning-artifacts/architecture.md`: the `matcher.py` line of the source tree names `match_outcome`; the logging line (`cycle.summary` … items held back by `require_score`) is already amended by the epic 5 course correction;
   - `_bmad-output/project-context.md`: the FR-25 "Pass-throughs on unknown data are counted in `cycle.summary`" bullet also names `held_back`;
   - `README.md`: checked. It does not describe `cycle.summary` fields today, so no change is expected; say so in the completion notes.

## Tasks / Subtasks

- [ ] **Task 1: Domain drop reason** (AC: 1, 2)
  - [ ] 1.1 In `src/cvestream/domain/matcher.py`, add `class DropReason(StrEnum)` right after `PassThrough`, with a one-line docstring ("Why `match_outcome()` did not match a vulnerability.") and the four members of AC 1.
  - [ ] 1.2 Rename the body of `match()` into `match_outcome()`, returning `DropReason.NO_PRODUCT`, `KEV_ONLY`, `REQUIRE_SCORE` and `MIN_CVSS` in place of the four `return None`. Keep the order of the checks, the single `max_score()` call and the label logic as they are.
  - [ ] 1.3 Make `match()` a wrapper: `outcome = match_outcome(vulnerability, endpoint)` then `return outcome if isinstance(outcome, MatchResult) else None`. Keep its docstring and signature.
  - [ ] 1.4 Module docstring: add one sentence saying that `match_outcome()` tells the caller why a vulnerability was dropped, so the pipeline can count what `require_score` held back. Do not reflow the existing paragraph (review feedback on wixyvir/cvestream#45).
  - [ ] 1.5 Do not touch `_match_entry`, `affected_hits`, `ProductMatch`, `MatchResult` or `PassThrough`.

- [ ] **Task 2: Count in the pipeline and the summary** (AC: 3, 4)
  - [ ] 2.1 `src/cvestream/pipeline/run.py`: import `DropReason` and `match_outcome` instead of `match`. Before the endpoint loop, `held_back = {e.id: 0 for e in endpoints if e.rules.require_score}`. In the candidate loop:
    ```python
    outcome = match_outcome(vulnerability, endpoint)
    if isinstance(outcome, DropReason):
        if outcome is DropReason.REQUIRE_SCORE:
            held_back[endpoint.id] += 1
        continue
    decision = decide(outcome, vulnerability, history.get(...))
    ```
    `isinstance` narrows `outcome` to `MatchResult` for `ty`. `REQUIRE_SCORE` is returned only when `rules.require_score` is true, so the key always exists.
  - [ ] 2.2 `RunResult`: add `held_back: Mapping[str, int]` after `feeds` with a comment, for example `# Per enabled endpoint with require_score: vulnerabilities it held back.`. Build it with `MappingProxyType(held_back)`. No default value: update the two constructors (Task 3).
  - [ ] 2.3 `src/cvestream/pipeline/summary.py`: add `held_back: Mapping[str, int]` to `CycleSummary` after `versions_unknown`, with a comment. `summarize()` passes `held_back=result.held_back`. `log_summary()` adds `held_back=dict(summary.held_back)` after `versions_unknown=`. Update the module docstring: it also counts what an endpoint's `require_score` held back.
  - [ ] 2.4 Leave `pipeline.completed` and `publish.*` events unchanged. Do not add a per-item log (Open questions Q3).

- [ ] **Task 3: Tests** (AC: 1 to 7)
  - [ ] 3.1 `tests/unit/domain/test_matcher.py`: import `DropReason` and `match_outcome` at module level. Add a `# --- drop reasons (NFR-4, story 5.3) ---` section after the `require_score` section (cases under "Testing requirements").
  - [ ] 3.2 `tests/unit/pipeline/test_run.py`: add the pipeline tests under "Testing requirements". In the two existing `require_score` tests, add `assert result.held_back == {"team-php": 1}` after run 2: this only adds asserts and keeps the tests' flow.
  - [ ] 3.3 `tests/unit/pipeline/test_summary.py`: add a `held_back` keyword to `_result()` (default `MappingProxyType({})`) and pass it to `RunResult`. Add `held_back={"a": 3}` to the `CycleSummary(...)` of `test_log_summary_writes_only_the_fields_that_apply` and assert `entry["held_back"] == {"a": 3}`. Add `test_held_back_is_copied_from_the_run` (`summarize()` with `_result(held_back=MappingProxyType({"a": 2}))` gives `summary.held_back == {"a": 2}`) and an empty case (`{}` logged when the run has no key).
  - [ ] 3.4 `tests/unit/pipeline/test_cycle.py`: add a `require_score: bool = False` keyword to `_yaml()` (writes `    require_score: true` when set). Add `test_held_back_is_counted_in_the_summary`: an endpoint with `require_score: true` and the default unscored `_ok(fake_clock)` gives `summary["held_back"] == {"team-php": 1}`, `summary["published"] == {"team-php": 0}` and `summary["count"] == 0`. In `test_a_cycle_logs_exactly_one_summary_with_a_failed_source`, add `assert summary["held_back"] == {}`.
  - [ ] 3.5 Keep every other existing test unchanged (AC 2 and 7 are regression guards). `test_restart_no_repost.py` must stay green.

- [ ] **Task 4: Documentation** (AC: 8)
  - [ ] 4.1 `docs/operations.md`: the `cycle.summary` sample and bullets (wording under Dev Notes).
  - [ ] 4.2 `docs/configuration.md`: append the sentence to the "Score rules" paragraph (line ~91).
  - [ ] 4.3 `CHANGELOG.md`: the `### Added` entry under `## [Unreleased]`, after the two existing entries.
  - [ ] 4.4 `README.md`: check; no change expected.
  - [ ] 4.5 `_bmad-output/planning-artifacts/architecture.md` line ~560 (source tree): `matcher.py  # match(vulnerability, endpoint) → MatchResult | None` → `# match_outcome(vulnerability, endpoint) → MatchResult | DropReason; match() → MatchResult | None`. Keep the column alignment of the tree.
  - [ ] 4.6 `_bmad-output/project-context.md` ("Recall over volume (FR-25)"): "Pass-throughs on unknown data are counted in `cycle.summary`." → "Pass-throughs on unknown data are counted in `cycle.summary`, and so are the items an endpoint's `require_score` held back (`held_back`, from `match_outcome()`'s `DropReason`)."

- [ ] **Task 5: Gates and bookkeeping**
  - [ ] 5.1 Run every gate command from `AGENTS.md`: `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`, `uv run lint-imports`, `uv run pytest`, `uv run cvestream config schema --check`, `uv run cvestream config validate endpoints.example.yaml`, `docker build --target runtime -t cvestream:local .`. The docker build can fail behind the local proxy (ghcr.io x509 or PyPI DNS). If it does, rely on CI and say so in the completion notes.
  - [ ] 5.2 Fill the Dev Agent Record and the File List, then set this story and `sprint-status.yaml` to `review`.

## Dev Notes

### Scope boundaries (read first)

- **In scope:** a drop reason in the matcher, the per-endpoint counter in `run()` and `cycle.summary`, tests and docs.
- **Out of scope:**
  - `/status` (`app/private_api.py`): it reports source staleness, not cycle counts;
  - a per-item log for held-back vulnerabilities (Q3);
  - counting other drops (`kev_only`, `min_cvss`, no product): `DropReason` makes them countable later, but NFR-4 and wixyvir/cvestream#47 ask only for `require_score`;
  - deduplicating counts across cycles (Q1), or excluding vulnerabilities already published on the endpoint (Q2);
  - enabling `require_score` on the team's endpoint, a deployment change Cyprien makes after epic 5 (sprint change proposal 2026-11-21);
  - any change to the config contract, the JSON Schema, `state/`, Alembic, `render.py` or `publication.py`.
- **Do not** re-implement the `require_score` rule in the pipeline (for example `if rules.require_score and max_score(...) is None`). The rule must live in one place, the matcher. Evaluating it twice would drift the day the rule changes. The AC allows "the rule evaluated by the pipeline", but this story picks the drop reason (Q4 records why).

### Current state of the files this story touches

**`src/cvestream/domain/matcher.py`, `match()` (lines ~169-200):**

```python
def match(vulnerability: Vulnerability, endpoint: Endpoint) -> MatchResult | None:
    affected = [a.value for a in vulnerability.affected]
    descriptions = [d.value for d in vulnerability.descriptions]
    matches = tuple(... _match_entry(entry, affected, descriptions) ...)
    if not matches:
        return None                                   # → DropReason.NO_PRODUCT
    rules = endpoint.rules
    if rules.kev_only and not vulnerability.is_known_exploited:
        return None                                   # → DropReason.KEV_ONLY
    score = max_score(vulnerability.scores)
    # Opt-out of FR-25 for scores: an exploited vulnerability is never silenced.
    if rules.require_score and score is None and not vulnerability.is_known_exploited:
        return None                                   # → DropReason.REQUIRE_SCORE
    labels: set[PassThrough] = set()
    if rules.min_cvss is not None:
        if score is None:
            labels.add(PassThrough.SCORE_UNKNOWN)
        elif score < rules.min_cvss:
            return None                               # → DropReason.MIN_CVSS
    if all(m.versions_unknown for m in matches):
        labels.add(PassThrough.VERSIONS_UNKNOWN)
    return MatchResult(...)
```

Preserve: the order of the checks, the single `max_score()` call, the label semantics (`score_unknown` only when `min_cvss` is set; `versions_unknown` only when every match has unknown versions), and `MatchResult`'s shape. `StrEnum` is already imported, and so are `Endpoint` and `Vulnerability`. No new import is needed.

**`src/cvestream/pipeline/run.py`, `run()` candidate loop (lines ~150-184):** `matched = match(vulnerability, endpoint)`, `if matched is None: continue`, then `decide()`, the GUID-collision safety net (`publish.skipped`), `add_publication` and `publish.decided`. Change only the first two lines and the variable name passed to `decide()`. Preserve:
- the single `State.transaction()` (only `pipeline/run.py` opens it);
- the `with_history` / `recent` / `backfill` candidate selection;
- the safety net and the render loop.

`RunResult` (frozen, slots) is built once at the end of `run()`. Outside `run.py` it is only built in `tests/unit/pipeline/test_summary.py::_result()`.

**`src/cvestream/pipeline/summary.py`:** `CycleSummary(sources, published, score_unknown, versions_unknown, duration_ms)`. Note that `score_unknown` and `versions_unknown` are **global** counts over all decisions, while `published` is per endpoint. `held_back` is per endpoint, like `published`, because the rule is per endpoint and the operator needs to know which endpoint hides items. `summarize()` builds `published` from the snapshot (every enabled endpoint, `0` included). `held_back` comes from `RunResult`, because only `run()` sees the drop reasons. `log_summary()` writes the fields in this order: `sources, published, score_unknown, versions_unknown, count, duration_ms`. Insert `held_back` after `versions_unknown`.

### Why only endpoints with `require_score` are keys (default chosen)

`held_back` lists every enabled endpoint with `require_score: true`, `0` included, and no other endpoint:
- a key with `0` says "the rule is on and held nothing back this cycle";
- a missing key says "the rule is off".

Listing every enabled endpoint, as `published` does, would show a permanent `0` for every endpoint without the rule, and that `0` would not tell "off" from "nothing held back". This is a reviewer decision point (Q5).

### Counting semantics (document them)

- **Per cycle, per endpoint, per evaluation.** In one run, each candidate vulnerability is evaluated at most once per endpoint (candidates are a set), so a vulnerability counts at most 1 per endpoint per cycle.
- **Recounted across cycles:**
  - On an endpoint with publication history, candidates are the vulnerabilities touched in this run (`recent`). A held-back vulnerability is counted again only when one of its records changes while it is still unscored, for example a CERT-EU advisory revision or an EUVD update without a score.
  - On an endpoint without publication history, every run re-evaluates the 7-day `backfill` set, so the same held-back vulnerabilities are counted at every cycle until the endpoint publishes its first item. Typical case: a new endpoint whose items are all unscored.
- **Never counted:**
  - a product mismatch;
  - a `kev_only` drop: the check comes first;
  - a known-exploited vulnerability: the rule exempts it;
  - a scored vulnerability below `min_cvss`.
- **Under `kev_only: true`**, `held_back` is always `0`: every vulnerability that reaches the rule is known-exploited, so exempt.
- **When a held-back vulnerability is scored later**, it is published as `new` (story 4.1, AC 5) and counted in `published`, not in `held_back`.

### Testing requirements

**Matcher** (`tests/unit/domain/test_matcher.py`). Use the `make_vulnerability` fixture, `PHP`, `PHP_8_2`, `_endpoint(*entries, min_cvss=, kev_only=, require_score=)`, with `description="PHP flaw"`. For EU KEV, use the fixture's `kev=True, kev_catalog=KevCatalog.EU_KEV`, as the existing `require_score` EU KEV test does.

| Case | Endpoint | Vulnerability | `match_outcome()` |
|---|---|---|---|
| no product | `_endpoint(PHP)` | `description="redis flaw"` | `DropReason.NO_PRODUCT` |
| no entries | `_endpoint()` | `description="PHP flaw"` | `DropReason.NO_PRODUCT` |
| excluded | entry with `exclude=("wordpress",)` | description mentioning both | `DropReason.NO_PRODUCT` |
| kev_only | `kev_only=True` | `scores=[9.8]`, not KEV | `DropReason.KEV_ONLY` |
| kev_only wins over require_score | `kev_only=True, require_score=True` | `scores=[]`, not KEV | `DropReason.KEV_ONLY` |
| require_score | `require_score=True` | `scores=[]`, `[None]` (parametrize) | `DropReason.REQUIRE_SCORE` |
| require_score with threshold | `require_score=True, min_cvss=7.0` | `scores=[None]` | `DropReason.REQUIRE_SCORE` |
| KEV exempt | `require_score=True` | `scores=[]`, `kev=True` | a `MatchResult` |
| below threshold | `min_cvss=7.0` (and with `require_score=True`) | `scores=[5.0]` | `DropReason.MIN_CVSS` |
| matched | `_endpoint(PHP_8_2, min_cvss=7.0)` | description only | `MatchResult`, equal to `match()`'s, labels `{VERSIONS_UNKNOWN, SCORE_UNKNOWN}` |

Add one consistency test over a parametrized sample of the cases above: `match(v, e) is None` if and only if `isinstance(match_outcome(v, e), DropReason)`, and otherwise `match(v, e) == match_outcome(v, e)`.

**Pipeline** (`tests/unit/pipeline/test_run.py`). Use `_endpoint()`, `_snapshot()`, `_euvd(fake_clock, score=..., record_id=..., cve=..., product=...)`, `_kev(fake_clock, cve=...)`, `_batch()`, `_kinds()`, `fake_clock.advance(...)`, plus `Rules` and `replace` (both already imported for the 4.1 tests).

- `test_held_back_is_counted_per_endpoint_with_require_score`: snapshot `strict` (`replace(_endpoint("team-strict"), rules=Rules(require_score=True))`), `plain` (`_endpoint("team-plain")`) and a disabled `require_score` endpoint (`replace(_endpoint("off", enabled=False), rules=Rules(require_score=True))`). One unscored php record (`score=None`). Expected:
  - `result.held_back == {"team-strict": 1}`;
  - `list(result.held_back) == ["team-strict"]`;
  - `_kinds(result) == [("team-plain", "new")]`.
- `test_a_vulnerability_of_another_product_is_not_held_back`: `require_score` endpoint, an unscored record with `product="go"` → `held_back == {"team-php": 0}`, no decision.
- `test_kev_only_drops_before_require_score`: `Rules(kev_only=True, require_score=True)`, an unscored non-KEV php record → `held_back == {"team-php": 0}`.
- `test_an_unscored_known_exploited_vulnerability_is_not_held_back`: `require_score` endpoint, an unscored php record plus `_kev(fake_clock)` for the same CVE in one batch → `held_back == {"team-php": 0}`, `_kinds == [("team-php", "new")]`.
- `test_a_score_below_min_cvss_is_not_held_back`: `Rules(require_score=True, min_cvss=7.0)`, `score=5.0` → `held_back == {"team-php": 0}`.
- `test_held_back_is_counted_again_while_an_endpoint_has_no_publication` (AC 6, second bullet): `require_score` endpoint, the same unscored batch run twice (advance the clock 15 minutes) → `held_back == {"team-php": 1}` both times.
- `test_held_back_is_not_counted_again_for_an_untouched_vulnerability` (AC 6, first bullet): give the endpoint history first (a scored `EUVD-2026-0` / `CVE-2026-1000`, as in the 4.1 tests). Then:
  - run 2: an unscored record → `{"team-php": 1}`;
  - run 3, the same batch unchanged → `{"team-php": 0}`, because `correlate()` does not touch an unchanged record.

**Summary and cycle:** see Tasks 3.3 and 3.4.

Testing rules that apply:
- domain tests stay pure (no DB, no mocks);
- `FakeClock` only, no sleeps; the network is blocked by pytest-socket;
- everything is sync, so anyio is not needed;
- `assert` only in tests;
- ruff `E,F,W,I,UP,B,DTZ,BLE,S,RUF`; `ty` with `error-on-warning`.

### Documentation edits (suggested wording, adjust to the surrounding style)

`docs/operations.md`, the sample (line ~120): insert `"held_back": {"team-php": 3}` after `"versions_unknown": 0`. Change the existing sample `published` value for `team-php` only if needed for coherence (`0` with 3 held back is the typical case worth showing).

`docs/operations.md`, the bullets (line ~125). Replace the last sentence of the `score_unknown` bullet with: "Items held back by an endpoint's `require_score` are not published: they are counted in `held_back`, not here." Then add:

> - `held_back`: per enabled endpoint with `require_score: true` (`0` included; an endpoint without the rule is not listed), the vulnerabilities that matched its products (and `kev_only`) but were not published because no source has scored them yet. Known-exploited ones are never held back. It is a count per cycle: a vulnerability is counted again in every cycle that evaluates it again, that is when one of its records changes, or at every cycle while the endpoint has published nothing yet (it re-checks its 7-day backfill each time). It is not part of `count`. A `held_back` that stays high while `published` stays at `0` means the rule hides most of that endpoint's items, typically CERT-EU-only or freshly published ones.

`docs/configuration.md`, end of the "Score rules" paragraph: "Each `cycle.summary` log counts what the rule held back, per endpoint (`held_back`, see `docs/operations.md`)."

`CHANGELOG.md`, under `## [Unreleased]` → `### Added`:

```markdown
- `held_back` in the `cycle.summary` log: per endpoint with `require_score`, the vulnerabilities the rule held back in the cycle (wixyvir/cvestream#47).
```

### Parallel stories of wave 13 (`5-1 | 5-3 | 5-4`)

- **Story 5.1** (compact item body and title markers) edits `pipeline/render.py` and `domain/publication.py` (`is_fresh`), and adds `publication.is_fresh` with the **only Alembic revision of the wave**. This story must **not** add an Alembic revision, and has no reason to.
- **Story 5.4** (reject duplicate product names, wixyvir/cvestream#48) edits `config/models.py` and `docs/configuration.md` (a different paragraph from "Score rules").
- **Shared files:** `docs/operations.md` (5.1 edits the item-format bullets, 5.3 the `cycle.summary` section), `CHANGELOG.md` (keep every `### Added` entry when rebasing), `README.md`, and `sprint-status.yaml` (each story's line, and `epic-5` set to `in-progress` by each branch: keep `in-progress` and every story's own line).
- **Code:** none of the three stories edits `domain/matcher.py`, `pipeline/run.py` or `pipeline/summary.py` besides this one, so a code conflict is not expected. 5.1 may edit `pipeline/run.py`'s `FeedEntry(...)` construction (render loop) to pass `is_fresh`. That is a different block from the candidate loop, so a rebase should merge it without trouble.
- Story 5.2 (wave 14) will add grouping after every `decide()` in `run.py`. It does not affect the counting here.
- All three branches share the first commit `3c08cdd docs(epic-5): add epic 5 by course correction`. Do not amend or rebase it away. It leaves the diff once one of the PRs merges.

### Architecture compliance

- **Pure domain:** `DropReason` and `match_outcome()` use only stdlib (`enum.StrEnum`) and `domain` modules. `tests/unit/domain/test_purity.py` and import-linter check this.
- **Pipeline:** still a chain of pure functions over frozen dataclasses (`… → MatchResult → PublicationDecision`). `DropReason` is a value, not an exception or an event, and the pipeline never catches.
- **State:** nothing is written. The count lives in `RunResult` and the log only. Only `pipeline/run.py` opens `State.transaction()`, which does not change.
- **Logging:** `cycle.summary` stays one event per cycle. JSON fields are `snake_case`, and endpoint IDs are not secrets. Never log a token or a feed path: the summary carries endpoint IDs only.
- **Unknown is `None`:** the rule still reads `score is None`, and the count is an `int` (`0` is a real count here, not "unknown").
- **Pattern change:** a new public domain function next to `match()`. As AGENTS.md requires, amend the source tree line of `architecture.md` and the FR-25 counting bullet of `project-context.md` in the same PR (Task 4.5, 4.6). The architecture's logging line already names `held_back` (epic 5 course correction, commit `3c08cdd`).

### Library / framework requirements

No new dependency, no `uv add`, no `uv.lock` change. `StrEnum` (stdlib, Python 3.11+) is already used by `PassThrough`. Python 3.14: no `from __future__ import annotations`. `MappingProxyType` is already imported in `run.py`.

### Previous story intelligence

No story of epic 5 is done yet: 5.1 and 5.4 run in parallel, and 5.2 comes after. The closest precedents are **story 4.1** (`4-1-require-score-endpoint-rule.md`), which added the rule this story counts, and **story 4.2** (exact matching). Learnings:
- 4.1 deferred this exact counter as its open question Q2. The `require_score` check sits after `kev_only` and before `min_cvss`, with one `max_score()` call. Keep it there.
- The two 4.1 pipeline tests (`test_an_unscored_vulnerability_held_back_by_require_score_is_new_once_scored` / `..._once_exploited`) give the endpoint history first, so later runs take the `recent` path. Reuse that pattern for AC 6.
- Review feedback on wixyvir/cvestream#45:
  - assert the result of run 1 in multi-run tests;
  - put imports at module level;
  - qualify docstrings without reflowing paragraphs;
  - write epics acceptance criteria in Given/When/Then form (already done for 5.3 in `epics.md`).
- One concern per commit; the story file and the sprint status go in a `chore(...)` commit.
- `gh` needs `SSL_CERT_FILE=$HOME/ca.pem` behind the proxy. Push through the SSH remote (`git@github.com:wixyvir/cvestream.git`).

### Git intelligence

The branch starts at `3c08cdd docs(epic-5): add epic 5 by course correction`, on top of `8d9590a Merge pull request #46` (story 4.2). Since story 4.1, `matcher.py` changed only in `_match_entry` / `affected_hits` (4.2, exact entries), and `run.py` / `summary.py` have not changed. Commit style: Conventional Commits scoped `(story-5.3)`, one concern per commit, never squashed, each ending with the attribution line. Suggested commits:

1. `feat(story-5.3): tell why the matcher drops a vulnerability`: `DropReason`, `match_outcome()`, the `match()` wrapper and the matcher tests.
2. `feat(story-5.3): count items held back by require_score in cycle.summary`: `run.py`, `summary.py` and the run, summary and cycle tests.
3. `docs(story-5.3): document held_back in cycle.summary`: `docs/operations.md`, `docs/configuration.md`, `CHANGELOG.md`.
4. `docs(story-5.3): amend the matcher outcome in the planning docs`: `architecture.md`, `project-context.md`.
5. `chore(story-5.3): ...`: this story file and `sprint-status.yaml`.

### Latest technical information

Nothing version-sensitive: the story uses stdlib `enum.StrEnum`, `types.MappingProxyType`, structlog's existing `log.info` keyword fields and `structlog.testing.capture_logs`, all already used in the code base. No web research was needed.

### Project Structure Notes

- No new module or package. `DropReason` lives in `domain/matcher.py`, next to `PassThrough`: the matcher owns why it matches and why it does not. Test layout mirrors `src/`.
- Branch `story-5-3-held-back-count-in-cycle-summary`, one PR to `main`, titled `feat(story-5.3): held-back count in cycle.summary`. The description links this story file and wixyvir/cvestream#47, and ends with the attribution line.

### Open questions (recorded instead of asked; defaults chosen)

- **Q1: per-cycle count, not unique.** A held-back vulnerability is counted again in every cycle that re-evaluates it. On an endpoint without any publication, that means every cycle, because the 7-day backfill is re-checked each time. Default: keep the per-cycle count (it is what "held back in each cycle" says, and it needs no state) and document it. Alternative: count each vulnerability once, which would need to store held-back decisions (a new table or column, so an Alembic revision, which wave 13 reserves for 5.1). Reviewer decision.
- **Q2: vulnerabilities already published on the endpoint.** Example: published as `new` with "severity unknown" before `require_score` was switched on, then touched again while still unscored. It is counted as held back, although `decide()` would not have re-published it (no KEV, band still `unknown`). Default: count it (the AC counts every drop by the rule; the case is rare and transient). Alternative: skip the count when `history` for `(endpoint, vulnerability)` is not empty. That is cheap, since `history` is already loaded in `run()`, but it adds a second rule to explain.
- **Q3: no per-item log.** The issue notes there is "no log, label or counter". This story adds the counter only. A per-item `debug` event (for example `publish.held_back` with `endpoint_id` and `vuln_id`) would help to find which vulnerability was held back, but it could be noisy on a backfilling endpoint. Default: not added. Easy to add later on top of `DropReason`.
- **Q4: drop reason versus the rule in the pipeline.** The epic allows either. Default: a drop reason from the domain (`match_outcome()`), because it keeps the rule in one place and leaves `match()` and its ~150 call sites in tests untouched. Rejected: changing `match()` to return `MatchResult | DropReason` (large test churn for no gain), and evaluating `require_score` again in `run.py` (the rule would exist twice).
- **Q5: which endpoints are keys.** Default: only enabled endpoints with `require_score: true`, `0` included, so a missing key means "rule off". Alternative: every enabled endpoint, like `published`. Reviewer decision; switching is a one-line change in `run.py` plus the tests.
- **Q6: PRD wording.** PRD NFR-4 lists the pass-throughs but not held-back items. `architecture.md` and `epics.md` already name `held_back` (course correction 2026-11-21). Default: leave the PRD to the PM, as story 4.1 did (its Q5).

### References

- [Source: GitHub issue wixyvir/cvestream#47] (problem, proposal, implementation note)
- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.3: Held-back count in cycle.summary]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-21-epic-5-readable-bursts.md] (section 2 "Technical impact", Story 5.3; section 4.3 logging line; section 4.5; wave 13 caveats)
- [Source: _bmad-output/planning-artifacts/architecture.md] (Logging line ~284; source tree `matcher.py` line ~560; pipeline chain line ~407; Requirements to Structure Mapping, NFR-4 row ~693)
- [Source: _bmad-output/project-context.md#Critical Don't-Miss Rules] ("Recall over volume (FR-25)")
- [Source: _bmad-output/implementation-artifacts/4-1-require-score-endpoint-rule.md] (rule placement, Q2, pipeline test pattern)
- [Source: src/cvestream/domain/matcher.py#match], [src/cvestream/pipeline/run.py#run], [src/cvestream/pipeline/summary.py], [tests/unit/pipeline/test_summary.py], [tests/unit/pipeline/test_cycle.py], [tests/unit/pipeline/test_run.py]
- [Source: docs/operations.md] (`cycle.summary` sample and bullets, line ~117-125), [docs/configuration.md] ("Score rules", line ~91)

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created.

### File List
