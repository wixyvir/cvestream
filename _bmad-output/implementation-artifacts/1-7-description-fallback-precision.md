---
baseline_commit: ea797baefced38ff8f831f749f2ae840ff98a8fe
---

# Story 1.7: Description fallback precision

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a channel subscriber,
I want a product name that only appears inside a file name or path not to count as a mention, and my team to be able to rely on structured product data alone for an entry,
so that my channel stops receiving vulnerabilities about other products, including KEV-only ones.

Added by the 2026-11-01 course correction for wixyvir/cvestream#8 (fixes **B** and **C**), found while trialling Story 1.6 on live data: 9 of 16 `team-php` items were about other products.

## TL;DR

- Two small changes, both in the description fallback of `domain/matcher.py` (the path taken only when a vulnerability has **no** affected-product data).
- **B (path rule, every endpoint):** a name or alias hit preceded by `.`, `/` or `\`, or followed by `/` or `\`, does not count. One extra compiled pattern; structured data and exclusions keep the current `_pattern()`.
- **C (`match_description`, opt-in):** new optional `match_description: bool = true` on full product entries (config contract v1, additive) and on `ProductEntry`; `false` skips the description fallback for that entry. Exclusions are still searched in the description.
- Tests: table tests built from the **real** descriptions of the issue (given below), the `match_description` trade-off, a pipeline test from a KEV-only record to the merged record (FR-26), config and schema tests.
- Docs in the same PR: `schemas/config-v1.json` (same commit as `config/models.py`), `docs/configuration.md`, `docs/sources.md`, `endpoints.example.yaml`, matcher docstring. Planning docs (PRD, epics, architecture, project-context) are **already amended** on `main`: do not edit them again.
- No new dependency, no migration, no retraction of past publications.
- One branch `story-1-7-description-fallback-precision` from `main`, one PR to `main` that closes wixyvir/cvestream#8.

## Acceptance Criteria

1. **Path rule (FR-12).** Given a vulnerability without any affected-product data (an EUVD record whose product is `n/a`, or a CISA KEV-only record), when `matcher.match()` falls back to its description, a name or alias hit **preceded by `.`, `/` or `\`, or followed by `/` or `\`**, does not count:
   - `php` does **not** match `admin/modules/circulation/loan_history_maintenance.php`, `diagnostic.php`, `/usr/lib/php/` or `C:\php\`;
   - `php` **still** matches "PHP 8.3", "php-fpm", "(PHP)", "PHP.", `php.ini`, "See the php.net advisory"; `node` still matches "Node.js".
2. **Mixed description.** A description that also mentions the product outside a path still matches (`Upload a .php file to a PHP 8.3 server` matches `php`).
3. **Unchanged rules.** Structured affected-product data (product and vendor names) and exclusion terms keep plain whole-word matching (`_pattern()` unchanged). `affected_hits()` (used by the renderer) is unchanged. Every existing test in `tests/unit/domain/test_matcher.py` passes **unchanged**.
4. **Table tests from wixyvir/cvestream#8.** `tests/unit/domain/test_matcher.py` covers the file-name descriptions of CVE-2026-107084, CVE-2026-52356, CVE-2026-50787 and CVE-2026-107081 (verbatim texts in Dev Notes), with vulnerabilities shaped like the recorded EUVD `n/a`-product record (no affected products) and the CISA KEV record (no affected products, KEV flag).
5. **`match_description: false` (FR-11, FR-12).** A product entry with `match_description=False` does not match a vulnerability without affected-product data, whatever its description says: the KEV-only CVE-2026-46300 (description mentions "PHP webshells") does not match `php`. With the default `True` it still does: both outcomes are asserted in one test, so the trade-off is visible. Structured hits are unaffected by the flag (a vulnerability with a `PHP` affected product still matches, with `structured=True`).
6. **Exclusions still read the description.** With `match_description=False`, an exclusion term found in the description still drops the entry, even when structured data names the product.
7. **Late data is `new` (FR-26).** A pipeline test in `tests/unit/pipeline/test_run.py` goes from a KEV-only record to the merged record: the entry with `match_description=False` publishes nothing on the KEV-only run, then, when an EUVD record bringing an affected product the entry names arrives, the vulnerability is published as `new` on that endpoint.
8. **Config contract.** `ProductEntryConfig` gains the optional `match_description: bool` (default `true`, strict: a string such as `"no"` is rejected) and `_to_product()` maps it to `ProductEntry.match_description`. The `php >= 8.2` shorthand keeps the default. The contract stays `version: 1`. `schemas/config-v1.json` is regenerated **in the same commit** as `config/models.py`.
9. **Documentation.** `docs/configuration.md` ("How an entry matches" and the product entry field list), `docs/sources.md` (CISA KEV "No affected products" note), `endpoints.example.yaml` and the `matcher.py` module docstring describe the path rule and `match_description`, including that a KEV-only vulnerability waits for EUVD data on such an entry.
10. **No retraction.** Publications already made are not retracted or re-decided; publication rules (`domain/publication.py`, `pipeline/run.py`) are unchanged.
11. **Gates.** Every gate command of `CLAUDE.md` passes, including `cvestream config schema --check` and `cvestream config validate endpoints.example.yaml`.

## Tasks / Subtasks

- [x] **Task 1: Branch (AC: all)**
  - [x] 1.1 `git switch -c story-1-7-description-fallback-precision` from up-to-date `main` (ea797ba or later). Not stacked: 1.1 to 1.6 are merged.

- [x] **Task 2: Path rule in the matcher (AC: 1, 2, 3, 4)**
  - [x] 2.1 In `src/cvestream/domain/matcher.py`, add a cached `_description_pattern(term)` (see "Matcher design") and a `_mentioned_in_prose(terms, text)` helper; use it **only** for the name/alias description fallback in `_match_entry`.
  - [x] 2.2 Keep `_pattern()`, `_mentions()`, `_hits()` and `affected_hits()` exactly as they are (structured data, vendor names, exclusions, render).
  - [x] 2.3 Tests: a parametrized table of short path/non-path cases (AC 1, 2), and a parametrized table of the four real issue descriptions (AC 4), each asserting `match(...) is None` for `PHP` and `PHP_8_2`. Add the multi-word and alias cases listed in "Testing requirements".
  - [x] 2.4 Commit: `fix(story-1.7): ignore product names inside paths in the description fallback`.

- [x] **Task 3: `match_description` in the domain and the matcher (AC: 5, 6)**
  - [x] 3.1 `src/cvestream/domain/endpoint.py`: add `match_description: bool = True` as the **last** field of `ProductEntry`, with a one-line comment.
  - [x] 3.2 `_match_entry`: when there are no structured hits, return `None` if `affected` is non-empty, **or** `not entry.match_description`, **or** no prose mention. The exclusion check above it is untouched (it still reads descriptions).
  - [x] 3.3 Tests in `test_matcher.py` (AC 5, 6) and `test_endpoint.py` (default `True`).
  - [x] 3.4 Commit: `feat(story-1.7): let a product entry opt out of description matching`.

- [x] **Task 4: Config contract and schema (AC: 8)**
  - [x] 4.1 `src/cvestream/config/models.py`: `match_description: bool = Field(default=True, description=...)` on `ProductEntryConfig`, after `min_version`; `_to_product()` passes `match_description=item.match_description`. The shorthand branch passes nothing (default).
  - [x] 4.2 `uv run cvestream config schema` to regenerate `schemas/config-v1.json`; check the diff only adds the `match_description` property.
  - [x] 4.3 Tests in `tests/unit/config/test_models.py`: full entry with `match_description: false` maps to the domain; default `True` (extend `test_product_entry_config_to_domain_defaults`); shorthand keeps `True` (the existing `test_shorthand_expands_to_domain` already compares with `ProductEntry(name=..., min_version=...)`, which now defaults to `True`: it must stay green); `match_description: "no"` rejected at `endpoints.0.products.0.entry.match_description` (add to `test_rejected`, id `match-description-string`).
  - [x] 4.4 One commit with models + schema + tests: `feat(story-1.7): add match_description to product entries in the v1 contract`.

- [x] **Task 5: Pipeline test, KEV-only to merged (AC: 7, 10)**
  - [x] 5.1 `tests/unit/pipeline/test_run.py`: give `_kev()` an optional `description: str | None = None` (sets `description=Sourced(description, "cisa_kev", now)` when given). Existing callers unchanged.
  - [x] 5.2 Add `test_a_kev_only_record_waits_for_product_data_without_description_matching` (see "Pipeline test design").
  - [x] 5.3 Commit: `test(story-1.7): publish a KEV-only vulnerability as new once EUVD names the product`.

- [x] **Task 6: Documentation (AC: 9)**
  - [x] 6.1 `src/cvestream/domain/matcher.py` module docstring (can go with Task 2/3 commits).
  - [x] 6.2 `docs/configuration.md`, `docs/sources.md`, `endpoints.example.yaml` (exact edits in "Documentation edits").
  - [x] 6.3 Check `README.md` and `CLAUDE.md`: neither describes the matching rule today, so no change is expected; confirm with `grep -n -i "description\|match" README.md CLAUDE.md`.
  - [x] 6.4 Commit: `docs(story-1.7): document the path rule and match_description`.

- [x] **Task 7: Gates and delivery (AC: 11)**
  - [x] 7.1 Run every gate command of `CLAUDE.md` (baseline on `main`: **978 passed, 2 deselected**).
  - [x] 7.2 Story file: tick tasks, fill the Dev Agent Record, Status → `review`; `sprint-status.yaml`: `1-7-description-fallback-precision: review`. Commit: `chore(story-1.7): mark story ready for review`.
  - [x] 7.3 Push with SSH (`git@github.com:wixyvir/cvestream.git`), open the PR to `main` (`SSL_CERT_FILE=$HOME/ca.pem gh pr create ...`), title `fix(story-1.7): description fallback precision`, body linking this story file, stating the narrowed rule B, containing `Closes wixyvir/cvestream#8`, and ending with the attribution line.

### Review Findings

Code review of 2026-11-01 (Blind Hunter, Edge Case Hunter, Acceptance Auditor; diff `ea797ba..b943809`).

- [x] [Review][Patch] Pin the slash trade-off with tests (`Apache/2.4.49`, `Linux/Unix` do not match) and document it as an assumed risk in `docs/configuration.md` [tests/unit/domain/test_matcher.py:231] — resolved from the decision below (Cyprien, 2026-11-01: keep rule B, record the risk)
- [x] [Review][Decision] A slash next to a name in prose is read as a path — resolved: keep rule B as agreed (option 1), see the patch above. The `(?![\w/\\])` lookahead and `[./\\]` lookbehind of `_description_pattern` [src/cvestream/domain/matcher.py:63] drop more than file paths: `Apache/2.4.49`, `nginx/1.25.3` (banner style), `Linux/Unix`, `MySQL/MariaDB`, `GNU/Linux`, and both mentions in `Microsoft Windows/Windows Server` no longer match in the fallback (confirmed by running `match()`). The spec accepted `PHP/8.3` and `PHP/MySQL` as trade-offs; the reach to banner-style `name/version` for every product was not spelled out. Only no-product vulnerabilities are affected (EUVD `n/a`, KEV-only until EUVD brings products). No test pins any of these cases.
- [x] [Review][Patch] Path-rule tests never use a KEV-shaped vulnerability (AC 4 names both the EUVD `n/a` and the CISA KEV shape) [tests/unit/domain/test_matcher.py:304]
- [x] [Review][Defer] A name between hyphens inside a path segment still matches (`wp-content/plugins/insert-php-code-snippet/readme.txt`) [src/cvestream/domain/matcher.py:63] — deferred, outside the agreed rule B (one boundary character each side); none of the measured cases had this shape

## Dev Notes

### Scope boundaries (read first)

- **In scope:** `domain/matcher.py`, `domain/endpoint.py`, `config/models.py`, `schemas/config-v1.json`, their tests, one pipeline test, the four doc files above.
- **Out of scope:** fixes A, D and E of the issue (no CISA `vendorProject`/`product` mapping, no new `PassThrough` label, no "possible match" title); the CLI flag `--no-match-description` (Story 3.2 picks it up from the contract); the Valkey provider (3.1 reuses the same models, nothing to do now); retracting or re-deciding past publications; any change to `domain/publication.py`, `pipeline/run.py`, `pipeline/render.py`, the adapters or the state.
- **Planning docs are already amended** by commits `6fb9873` and `ea797ba` (PRD FR-11/FR-12, `epics.md`, `architecture.md` F3 row, `project-context.md` "Matching (FR-12)", sprint change proposal). Do not touch them; if the implemented rule differs from them, stop and ask.
- **Do not widen or narrow rule B.** The "followed by `.` + letter" variant from the issue body was explicitly **dropped** (decided with Cyprien on 2026-11-01): it would break the existing test "See the php.net advisory" and stop `node` matching "Node.js" or `asp` matching "ASP.NET". `php.ini` keeps matching on purpose.

### Matcher design (`src/cvestream/domain/matcher.py`)

Current state: `_pattern(term)` compiles `(?<!\w)<words>(?!\w)` (case-insensitive, `\s+` between words, cached). `_mentions(terms, text)` = any `_pattern(term).search(text)`. `_match_entry` checks exclusions on structured data and descriptions, then structured hits, then falls back to `any(_mentions(terms, d) for d in descriptions)` only when `affected` is empty.

Change: one more cached pattern whose boundaries also exclude the path characters, used only in the fallback. A regex keeps "the first occurrence is in a path but a later one is not" correct for free, since `search()` keeps scanning (AC 2):

```python
@cache
def _description_pattern(term: str) -> re.Pattern[str]:
    # A name inside a file name or path is not a mention: `x/maintenance.php`,
    # `/usr/lib/php/`, `C:\php\`. `php.ini`, `php.net` and "Node.js" still are.
    words = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(rf"(?<![\w./\\]){words}(?![\w/\\])", re.IGNORECASE)


def _mentioned_in_prose(terms: tuple[str, ...], text: str) -> bool:
    return any(_description_pattern(term).search(text) for term in terms)
```

And in `_match_entry`:

```python
    if not hits:
        # Structured data wins: the description is only a fallback for a
        # vulnerability without any affected product, and an entry can opt out.
        if (
            affected
            or not entry.match_description
            or not any(_mentioned_in_prose(terms, d) for d in descriptions)
        ):
            return None
```

Notes:
- Lookbehind with a character class is fixed-width (one character): valid in Python `re`. The prototype of this exact pattern was checked against every example of AC 1, AC 2 and the five real descriptions below; all pass.
- Sharing the `words` construction with `_pattern()` through a small private helper is fine (no `utils.py`); keep both patterns `@cache`d.
- Names are free to choose, but keep them private (`_`) and keep `match()`'s signature and `MatchResult`/`ProductMatch` unchanged. `ProductMatch.structured` stays `False` for a description-only match.
- `descriptions` elements are `str` (never `None`: `Sourced[str]` values), so the new helper does not need the `None` guard of `_mentions`.
- Do not apply the path rule to exclusions: an exclusion is a recall-reducing rule, and the agreed rule keeps it plain whole-word (AC 3). Do not apply it to structured product/vendor names either.
- Known, accepted trade-offs of rule B (document nothing extra, but do not "fix" them): `PHP/8.3` (a `X-Powered-By` value) and `PHP/MySQL` no longer count in the fallback; `www.php.net` does not count (preceded by `.`) while `php.net` at a word start does.

Update the module docstring (current lines 3 to 9) to say, in the file's style: the description is searched only when there is no affected-product data at all, and then a name inside a file name or path (`loan_history_maintenance.php`, `/usr/lib/php/`) does not count; an entry with `match_description` false never uses the description, so a KEV-only vulnerability waits for EUVD product data; exclusion terms are looked for in both.

### Domain entry (`src/cvestream/domain/endpoint.py`)

Current `ProductEntry`: `name`, `aliases=()`, `exclude=()`, `min_version=None`, frozen + slots. Add as the last field:

```python
    # False: structured affected-product data only, never the description.
    match_description: bool = True
```

Positional constructions in the code (`ProductEntry(name)` in `pipeline/render.py:118`) still work. `ProductEntry` stays hashable; equality-based tests (`test_full_product_object_to_domain`, `test_shorthand_expands_to_domain`) stay green because both sides default to `True`.

### Config contract (`src/cvestream/config/models.py`)

`_CONFIG = ConfigDict(extra="forbid", strict=True)` already makes `bool` strict (`"yes"`/`"no"` rejected, like `kev_only`). Add after `min_version`:

```python
    match_description: bool = Field(
        default=True,
        description=(
            "Search the description when a vulnerability lists no affected "
            "product; false matches on affected-product data only."
        ),
    )
```

`test_every_property_has_a_description` (in `tests/unit/config/test_schema.py`) requires the `description`. `_to_product()` full-entry branch adds `match_description=item.match_description`. Regenerate with `uv run cvestream config schema` and commit the JSON with the model change (CI and `test_committed_schema_is_fresh` fail otherwise). The schema change is additive (`default: true`, not in `required`): still contract v1, no `SCHEMA_VERSION` change.

### Real descriptions for the table tests (from EUVD and the CISA KEV catalogue, 2026-11-01)

All four EUVD records have `enisaIdProduct` = `[{"product": {"name": "n/a", "vendor": {"name": "n/a"}}, "product_version": "n/a"}]`, which the EUVD adapter drops (`clean_text()` placeholders), so the vulnerability has **no** affected products: build them with `make_vulnerability(description=...)` and no `products`. Use these texts verbatim (they are API text, not fixtures; `tests/fixtures/` stays untouched):

| ID | EUVD | Description |
|---|---|---|
| CVE-2026-107084 | EUVD-2026-104552 | `Slims (Senayan Library Management Systems) 9 Bulian 9.8.0 allows cross-site request forgery (CSRF) against admin/modules/circulation/loan_history_maintenance.php because the action=run GET parameter performs privileged, destructive operations with no CSRF token or POST confirmation, leading to data loss of loan history records.` |
| CVE-2026-52356 | EUVD-2026-109093 | `An OS command injection vulnerability in the /htdocs/web/diagnostic.php endpoint of D-Link DAP1160L A1_FW101WWb04 allows attackers to execute arbitrary commands via injecting arbitrary shell metacharacters.` |
| CVE-2026-50787 | EUVD-2026-109108 | `e-SIC Livre (esiclivre) v0.2.2 and earlier contains an uncontrolled resource consumption vulnerability in restrito/inc/captcha.php. The application uses user-supplied parameters (l, a, tf, ql) without validation to generate CAPTCHA images, allowing remote attackers to cause denial of service via excessive memory and CPU consumption.` |
| CVE-2026-107081 | EUVD-2026-104452 | `SQL injection via identifier injection in the tableName and tableFields POST parameters of admin AJAX endpoints (admin/AJAX_lookup_handler.php, admin/AJAX_check_id.php, admin/AJAX_vocabolary_control.php) in SENAYAN Library Management System (SLiMS) Bulian 9.8.0 allows any authenticated staff user to extract arbitrary database contents.` |

KEV-only record, CISA KEV `shortDescription` of **CVE-2026-46300** (`vendorProject` "Linux", `product` "Kernel", not mapped by the adapter): build it with `make_vulnerability(description=..., kev=True)` and no `products`:

`Linux Kernel contains a missing initialization of a variable vulnerability that could allow for unintended modification of page-cache-backed data via failure to preserve the shared-fragment marker during socket buffer coalescing, causing ESP input processing to decrypt shared fragments in place. This vulnerability can be chained with CVE-2026-109618 and CVE-2026-109619 to implant PHP webshells and run commands as root.`

This one is **not** a path case: with the default entry it still matches `php` (rule B does not fix it, by design); only `match_description=False` drops it (AC 5). The other two PHP-application cases of the issue (CVE-2026-106971 "32-bit PHP", CVE-2026-115190 Mautic "PHP object injection") are the same kind; adding one of them to the AC 5 test is optional.

Long strings: keep lines within ruff's limit by splitting the literals inside parentheses (implicit concatenation), as the existing `test_a_description_mention_does_not_match_another_product` does. Ruff `E501` applies to tests too.

### Testing requirements

Domain tests stay pure (no database, no network, no mocks). Use the `make_vulnerability` fixture (`tests/unit/domain/conftest.py`) and the existing `_endpoint`, `PHP`, `PHP_8_2` helpers of `test_matcher.py`. Add the new tests under the existing `# --- product matching (FR-11, FR-12)` section, or a new `# --- description fallback precision (FR-12)` section right after it.

- **Path table (AC 1, 2):** parametrized `(description, matches)` with readable ids, at least: `admin/modules/circulation/loan_history_maintenance.php` → False (`path-file`), `diagnostic.php` → False (`file-name`), `/usr/lib/php/` → False (`unix-dir`), `C:\php\` → False (`windows-dir`; write it `"C:\\php\\"`, since a raw string cannot end with a single backslash), `PHP 8.3` → True, `php-fpm` → True, `(PHP)` → True, `PHP.` → True, `php.ini` → True, `See the php.net advisory` → True, `Upload a .php file to a PHP 8.3 server` → True (`path-then-prose`).
- **Node.js:** `ProductEntry(name="node")` matches "A flaw in Node.js" (description-only).
- **Alias and multi-word:** an alias inside a path does not count (`ProductEntry(name="php", aliases=("php-src",))` vs `ext/php-src/main.c`), and a multi-word term after `/` does not either (optional).
- **Issue table (AC 4):** the four descriptions above, ids by CVE, asserting `match(v, _endpoint(PHP)) is None` and `match(v, _endpoint(PHP_8_2)) is None`.
- **`match_description` (AC 5):** with the CVE-2026-46300 KEV-only vulnerability: `match(v, _endpoint(PHP))` is not None (description-only, `structured is False`) **and** `match(v, _endpoint(ProductEntry(name="php", match_description=False)))` is None, in one test named for the trade-off. A second test: with `products=[("PHP", "PHP Group", "8.1.0 <8.2.29")]`, the opted-out entry still matches with `structured is True` (and with `min_version` 8.2 the versions are checked as before).
- **Exclusion (AC 6):** `ProductEntry(name="php", exclude=("wordpress",), match_description=False)` with `products=[("PHP", "PHP Group")]` and a description mentioning WordPress → None.
- **Endpoint default:** `test_product_entry_defaults` asserts `entry.match_description is True`.
- **Config:** see Task 4.3. `test_example_file_is_valid` (`tests/unit/config/test_yaml_provider.py`) already loads `endpoints.example.yaml`; optionally assert the example's entry carries the value it shows.
- **Pipeline (AC 7):** see below.
- Do not modify existing assertions. If one fails, the rule was implemented wrong (most likely candidates: `dot-boundary` "See the php.net advisory", `hyphen-boundary`, or a structured test going through the new pattern).

### Pipeline test design (`tests/unit/pipeline/test_run.py`)

Helpers already there: `_euvd(clock, *, record_id, cve, score, product, description, updated_ago)`, `_kev(clock, cve, name)`, `_batch(*records)`, `_snapshot(*endpoints)`, `_kinds(result)`, `CVE`, fixtures `state` and `fake_clock`. A KEV-only record has `activity_time` = `added_at` (now − 5 min), so it is inside the 14-day decision window; the EUVD record merges into it through the CVE alias (as in `test_known_exploited_publishes_one_kev_update`, in the other order).

Sketch:

```python
def test_a_kev_only_record_waits_for_product_data_without_description_matching(
    state: State, fake_clock: FakeClock
) -> None:
    strict = Endpoint(
        id="team-php-strict",
        name="strict",
        products=(ProductEntry(name="php", match_description=False),),
    )
    snapshot = _snapshot(_endpoint(), strict)
    kev_only = _kev(fake_clock, description="... to implant PHP webshells ...")
    first = run(state, snapshot, [_batch(kev_only)], fake_clock)
    assert _kinds(first) == [("team-php", "new")]  # the default still matches
    fake_clock.advance(timedelta(minutes=15))
    # FR-26: EUVD brings the product; no score, so no band change for team-php
    late = run(state, snapshot, [_batch(_euvd(fake_clock, score=None))], fake_clock)
    assert _kinds(late) == [("team-php-strict", "new")]
```

Keep `score=None` on the EUVD record: with a score, `team-php` would also get a `band-*` update (unknown → rated), which is correct but not what this test is about. If the decision order differs, compare sets rather than reordering the code. Use the real CVE-2026-46300 sentence or a shorter one; what matters is a PHP mention and no affected product.

### Documentation edits

- **`docs/configuration.md`**
  - Line 84 ("A product entry has `name` (required), `aliases[]`, `exclude[]` and `min_version`…"): add `match_description` (default `true`).
  - Line 86 ("How an entry matches"): after "…does not match `php`.", add that in the description a name inside a file name or path does not count (`loan_history_maintenance.php`, `/usr/lib/php/`, `C:\php\`), while `php.ini`, `php.net` and "Node.js" still do. Then: `match_description: false` makes the entry match on affected-product data only; a vulnerability known only from CISA KEV (which lists no products) then waits for EUVD data and is published as new when it arrives. The trade-off: fewer false positives, but a vulnerability whose records never list products is missed for that entry. Keep the existing sentences on "versions unknown" and exclusions.
  - Optionally add `match_description: false` to the YAML example block at lines 55 to 70 (e.g. on the `gitlab` entry) so the field is shown once.
- **`docs/sources.md`** line 164 ("The description still feeds description matching."): append that a name inside a file name or path does not count, and that entries with `match_description: false` ignore it, so such a KEV-only vulnerability waits for EUVD product data.
- **`endpoints.example.yaml`**: in the `team-platform` full entry (`gitlab`, lines 57 to 68), update the `name` comment (lines 58 to 60) to mention that a name inside a file name or path in the description does not count, and add after `min_version`:

  ```yaml
          # Optional, default true. false matches on the affected products only,
          # never on the description: fewer false positives, but a vulnerability
          # known only from CISA KEV (no products) waits for EUVD data.
          match_description: true
  ```

  Whether to show `true` or `false`: the file shows defaults elsewhere (`enabled: true`, `kev_only: false`), so `true` with the comment is consistent. `cvestream config validate endpoints.example.yaml` must pass.
- **`schemas/config-v1.json`**: regenerated, never hand-edited.
- No change to `settings.example.yaml` (no setting), `docs/operations.md`, `README.md`, `CLAUDE.md` (Task 6.3 checks).

### Existing files this story touches (current state → change → preserve)

- `domain/matcher.py`: `_pattern`, `_mentions`, `_hits`, `affected_hits`, `_match_entry`, `match`, `ProductMatch`, `MatchResult`, `PassThrough`. Change: new description pattern and helper, fallback condition, docstring. Preserve: everything else, including the version logic (vendor-only hits unusable, "structured versions win over a description mention") and the order of `matches`.
- `domain/endpoint.py`: `ProductEntry`, `Rules`, `Endpoint`. Change: one field. Preserve frozen/slots and field order of the existing fields.
- `config/models.py`: `ProductEntryConfig`, `_to_product`, shorthand handling. Change: one field + mapping. Preserve `extra="forbid"`, `strict=True`, the `_quoted` validator, error messages that never quote values.
- `schemas/config-v1.json`: regenerated.
- `tests/unit/domain/test_matcher.py`, `tests/unit/domain/test_endpoint.py`, `tests/unit/config/test_models.py`, `tests/unit/pipeline/test_run.py`: additions only (plus the optional `description` parameter of `_kev`).
- `docs/configuration.md`, `docs/sources.md`, `endpoints.example.yaml`: the lines above.
- Story file and `sprint-status.yaml` at the end.

### Architecture compliance

- `domain` stays pure: only `re`, `dataclasses`, `enum`, `functools` and `packaging` (`tests/unit/domain/test_purity.py` and import-linter enforce it).
- Recall over volume (FR-25) holds **by default**: rule B removes only path hits; rule C is opt-in per entry.
- Pydantic stays at the boundary (`config/models.py`); the domain trusts the boundary and does not re-validate `match_description`.
- `config/models.py` is a public contract: schema regenerated in the same commit (`CLAUDE.md`).
- No pattern change beyond what `architecture.md` and `project-context.md` already say (amended on `main`), so no architecture amendment in this PR.

### Library / framework requirements

- No new dependency, no `uv add`, no `uv.lock` change. Python 3.14 `re` (lookbehind with a one-character class is fixed width and supported). Pydantic 2.14 strict `bool` (already used for `enabled` and `kev_only`).

### Previous story intelligence (1.6 and its trial)

- The structured-data-first rule this story refines came from the 1.6 trial (commit `ae4c9e7`, "fix(story-1.6): let structured product data decide a match"): it added `test_a_description_mention_does_not_match_another_product` (`Module.php`, PHPOK, Kirki) and `test_php_and_its_sub_products_still_match`. Those cases have structured data and must keep behaving the same; they are not path-rule cases.
- The 1.6 render names the affected product a match hit through `affected_hits()` (`pipeline/render.py:_title_product`); for a description-only match it shows the entry name. Nothing to change there.
- Gate baseline on `main` (ea797ba): **978 passed, 2 deselected**. Ruff rules `E,F,W,I,UP,B,DTZ,BLE,S,RUF`; `ty` with `error-on-warning`; `assert` only in tests.
- `gh` needs `SSL_CERT_FILE=$HOME/ca.pem` behind the proxy; push through the SSH remote.

### Git intelligence

Recent commits: `docs(story-1.7): add story 1.7 to epic 1 by course correction`, `docs(story-1.7): narrow the description fallback in the matching rule` (planning amendments), the 1.6 merge (wixyvir/cvestream#6), a Dependabot uv image bump (wixyvir/cvestream#7), then the 1.6 trial fixes (`fix(story-1.6): ...`, `feat(story-1.6): ...`). Pattern: Conventional Commits scoped `(story-1.x)`, one concern per commit, docs in `docs`, contract change and regenerated schema together, story bookkeeping in `chore`. Each commit message ends with a blank line and the attribution line.

### Latest technical information

Nothing version-sensitive: the story uses the standard library `re` and existing Pydantic features. The EUVD and CISA KEV texts above were fetched from `euvdservices.enisa.europa.eu/api/search?text=<CVE>` and the CISA KEV JSON feed on 2026-11-01.

### Project Structure Notes

- No new module or package. The helpers live in `domain/matcher.py` (the layer that owns matching); no `utils.py`.
- Test layout mirrors `src/` (`tests/unit/domain/`, `tests/unit/config/`, `tests/unit/pipeline/`).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.7: Description fallback precision]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-01.md] (fix B narrowed, fix C, out-of-scope options)
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements Overview] (F3 row, amended)
- [Source: _bmad-output/project-context.md#Critical Don't-Miss Rules] ("Matching (FR-12)", "Recall over volume")
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-11, FR-12, FR-25, FR-26)
- wixyvir/cvestream#8 (evidence, options, and the owner comment that narrows rule B)
- [Source: src/cvestream/domain/matcher.py], [Source: src/cvestream/domain/endpoint.py], [Source: src/cvestream/config/models.py]
- [Source: docs/configuration.md#endpoints.yaml], [Source: docs/sources.md] (CISA KEV "No affected products")

## Dev Agent Record

### Agent Model Used

claude-sonnet-5-5

### Debug Log References

- Red phase confirmed before each change: 10 path/issue-table tests failed before the path rule, 4 tests failed before `match_description` (domain), 2 before the config field. The pipeline test (Task 5) covers behaviour added in Task 3, so it passed at once; it was checked by disabling the opt-out in the matcher (the test then fails) and restoring it.
- `docker build --target runtime` could not run locally: pulling `ghcr.io/astral-sh/uv:0.13.6-python3.14-trixie-slim` fails with `x509: certificate signed by unknown authority` (the Docker daemon does not trust the TLS-intercepting proxy, and the base image is not cached). Verification was not disabled. The story changes no `Dockerfile`, `pyproject.toml` or `uv.lock`; CI runs that gate on the PR.

### Completion Notes List

- Path rule (B): `_description_pattern()` (boundaries `(?<![\w./\\])` and `(?![\w/\\])`) and `_mentioned_in_prose()` in `domain/matcher.py`, used only by the description fallback of `_match_entry`. `_pattern()`, `_mentions()`, `_hits()` and `affected_hits()` are unchanged; the two patterns share a small `_words()` helper. All pre-existing matcher tests pass unchanged.
- `match_description` (C): `ProductEntry.match_description: bool = True` (last field), checked in the fallback condition of `_match_entry`; exclusions still read descriptions. `ProductEntryConfig.match_description` (strict bool, default `true`) mapped in `_to_product()`; shorthand keeps the default. `schemas/config-v1.json` regenerated in the same commit and only gains the new property; contract stays `version: 1`.
- Tests added: path table (11 cases), Node.js, alias and multi-word cases, the four real issue descriptions (CVE-2026-107084, CVE-2026-52356, CVE-2026-50787, CVE-2026-107081), the CVE-2026-46300 trade-off test (default matches, opt-out does not), structured hits unaffected by the flag (with and without `min_version`), exclusion with the flag off, `ProductEntry` default, config mapping/default/`"no"` rejected, and the pipeline test for FR-26 (KEV-only record, then EUVD brings the product: published as `new` on the strict endpoint).
- Docs: `matcher.py` docstring, `docs/configuration.md` (field list, "How an entry matches", YAML example), `docs/sources.md` (CISA KEV "No affected products"), `endpoints.example.yaml`. `README.md` and `CLAUDE.md` do not describe the matching rule: no change. Planning documents untouched, as instructed.
- Gates on the branch: `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports` (7 kept), `pytest` (**1004 passed, 2 deselected**, baseline 978), `config schema --check`, `config validate endpoints.example.yaml` all pass. `docker build` not run locally (see Debug Log).
- No retraction of past publications; `domain/publication.py`, `pipeline/run.py` and `pipeline/render.py` untouched.

### File List

- `src/cvestream/domain/matcher.py` (modified)
- `src/cvestream/domain/endpoint.py` (modified)
- `src/cvestream/config/models.py` (modified)
- `schemas/config-v1.json` (modified, regenerated)
- `tests/unit/domain/test_matcher.py` (modified)
- `tests/unit/domain/test_endpoint.py` (modified)
- `tests/unit/config/test_models.py` (modified)
- `tests/unit/pipeline/test_run.py` (modified)
- `docs/configuration.md` (modified)
- `docs/sources.md` (modified)
- `endpoints.example.yaml` (modified)
- `_bmad-output/implementation-artifacts/1-7-description-fallback-precision.md` (modified)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified)

### Change Log

- 2026-11-01: Implemented story 1.7: path rule in the description fallback, opt-in `match_description` on product entries (config contract v1, additive), tests and documentation. Status: review.
- 2026-11-01: Code review: 1 decision (slash trade-off kept as an assumed risk, documented and pinned by tests), 2 patches applied (slash tests and docs, KEV-shaped issue table), 1 deferred, 9 dismissed. Status: done.
