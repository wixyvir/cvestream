---
baseline_commit: 3c08cdd
---

# Story 5.4: Reject duplicate product names within an endpoint

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Created from GitHub issue wixyvir/cvestream#48 (decision D2 in the review of wixyvir/cvestream#46). -->

## Story

As the operator,
I want the configuration to reject two product entries with the same name in one endpoint,
so that the item title, and story 5.2's groups, never pick the wrong entry.

Added from GitHub issue wixyvir/cvestream#48, found during the review of wixyvir/cvestream#46 (story 4.2, decision D2, deferred with documentation). `_title_product()` in `src/cvestream/pipeline/render.py` resolves the configured entry with `next((p for p in endpoint.products if p.name == name), ProductEntry(name))`, so it always returns the **first** entry with that name. Nothing rejects duplicate names today. Two entries with the same name and different rules, for example `{name: php, exact: true}` next to a non-exact early-warning `php` entry (the workaround once suggested for exact entries), make the title pick whichever comes first, and the title can fall back to the bare name although the item matched the other entry. Story 5.2 (wave 14) keys its burst groups on the matched entry name (`products[0]`) and depends on this story.

## TL;DR

- **One validator:** a `model_validator(mode="after")` on `EndpointConfig` in `src/cvestream/config/models.py` rejects an endpoint whose `products` hold two items with the same name. Names are compared after whitespace collapse, strip and `str.casefold()`, the same normalization the matcher uses for exact entries. A shorthand counts by its name, so `php` and `php >= 8.2` collide.
- **Error line** (prototyped with the project's pydantic 2.14.3): `endpoints[0]: products[1]: duplicate product name 'PHP' (same as products[0])`. `cvestream config validate` prints it after the file name and exits 2. A standalone `EndpointConfig` validation (the future CLI write path) reads `products[1]: ...`.
- **Still accepted:** distinct names, an alias of one entry used as another entry's name (the documented workaround), and the same name in two different endpoints.
- **Why on `EndpointConfig`:** every path goes through it: YAML now, the Valkey provider (3.1, through `build_snapshot()` → `ConfigDocument`) and the CLI write commands (3.2) later. No provider or CLI code changes.
- **Contract:** a tightening of v1 (a config that loads today can be rejected). `SCHEMA_VERSION` stays `1`, as in story 4.2. JSON Schema cannot express "unique property across array items", so no constraint is added to the schema. The `products` field description gains one sentence on uniqueness, so `schemas/config-v1.json` is regenerated in the same commit (description change only).
- **Docs:** `docs/configuration.md` (remove the "not rejected yet, wixyvir/cvestream#48" note, state the rule, list it among the checks the schema cannot make), `CHANGELOG.md` (`### Changed`, an upgrade note), `project-context.md` (one configuration rule). `architecture.md` already says it (F3 row, written by the course correction).
- **Not touched:** `pipeline/render.py` (story 5.1 rewrites it in parallel), the state, the domain, the providers, the CLI code. No migration.
- **Parallel wave 13:** 5.1 and 5.3 run at the same time. Shared files: `docs/operations.md` (this story does not expect to touch it), `CHANGELOG.md`, `README.md`. 5.1 owns the wave's only Alembic revision; this story adds none.

## Acceptance Criteria

1. **Duplicates rejected (FR-21 boundary).** Given an endpoint whose `products` contain two items whose names are equal after normalization (whitespace runs collapsed to one space, stripped, `str.casefold()`), when the configuration is validated through `EndpointConfig` (YAML now, Valkey later through the same model), then validation fails with **one** error located at the endpoint, whose message names the **second** item's path, the duplicate name as written in that item, and the first item's path: `products[<j>]: duplicate product name '<name>' (same as products[<i>])`. Formatted by `build_snapshot()`, the line is `endpoints[<k>]: products[<j>]: duplicate product name '<name>' (same as products[<i>])`.
   - A full entry's name is `item.name` (already stripped by `Term`). A shorthand's name is the first group of `_split_shorthand()`, so `"php"`, `"php >= 8.2"` and `{name: PHP}` all have the normalized name `php`.
   - Only the first duplicate is reported (the validator raises on the first collision, like `ConfigDocument._unique`).
   - The message quotes the name with `!r`, so it stays on one line. It never contains a token (names are not secrets, and the token is not read).

2. **CLI exit code.** Given such a file, `cvestream config validate <file>` exits `2`, prints nothing on stdout and exactly one line on stderr: `<file>: endpoints[0]: products[1]: duplicate product name 'php' (same as products[0])` (for the example used in the CLI test).

3. **Distinct names accepted.** Given names that differ after normalization, including:
   - an alias of one entry used as another entry's name (`[{name: php, aliases: [php-src], exact: true}, {name: php-src, match_description: false}]`), the workaround documented for exact entries;
   - an alias equal to another entry's alias (not a name collision; see Open question 6);
   - the same name in two **different** endpoints;

   when validated, then the configuration is accepted and `to_domain()` maps every entry unchanged.

4. **Daemon behaviour unchanged in shape.** The rule rides on the existing paths, no new code there: at startup an endpoints file with duplicates logs `config.rejected` and exits `2` (`app/runtime.py` `_load_snapshot`); on a reload the last valid configuration stays and `config.rejected` is logged (`pipeline/run.py`). No new log event, no new exit code.

5. **Contract and schema.** `SCHEMA_VERSION` stays `1`. The `products` field `description` of `EndpointConfig` gains a sentence saying names must be unique within the endpoint (case-insensitive). `schemas/config-v1.json` is regenerated with `uv run cvestream config schema` **in the same commit** as `config/models.py`, and its diff is that description only. `uv run cvestream config schema --check` passes.

6. **Example file still valid.** `endpoints.example.yaml` has no duplicate names today (`php`, `nodejs` in `team-php`; `gitlab`, `apache http server`, `cpython` in `team-platform`) and stays unchanged. `uv run cvestream config validate endpoints.example.yaml` passes and `test_example_file_is_valid` stays green.

7. **Documentation.**
   - `docs/configuration.md`: the "Exact entries" paragraph loses its last sentence ("Entries of one endpoint need distinct `name`s: ... not rejected yet, wixyvir/cvestream#48)"). The product-entry paragraph ("A product entry has ...") states the rule, the shorthand case and the alias workaround. The "JSON Schema" section lists unique product names among the checks the schema cannot make.
   - `CHANGELOG.md`: under `## [Unreleased]`, a `### Changed` entry (after `### Added`, Keep a Changelog order) describing the tightening and what to do before upgrading, with wixyvir/cvestream#48.
   - `_bmad-output/project-context.md`: one rule under "Configuration (Valkey / YAML)".
   - `README.md` and `AGENTS.md`: checked; no change expected (neither describes product entry rules).

8. **Gates.** Every gate command of `AGENTS.md` passes.

## Tasks / Subtasks

- [x] **Task 1: Branch (AC: all)**
  - [x] 1.1 Work on `story-5-4-reject-duplicate-product-names` (already holds this story file on top of `3c08cdd docs(epic-5): add epic 5 by course correction`, the base shared with 5.1 and 5.3). Do not amend or rebase `3c08cdd`. If the 5.1 or 5.3 PR merges first, rebase this branch on up-to-date `main` before pushing the implementation.

- [x] **Task 2: Validator, schema and tests (AC: 1, 2, 3, 5, 6)**
  - [x] 2.1 `src/cvestream/config/models.py`: add the private name helpers and the `EndpointConfig` `model_validator` (see "Config contract").
  - [x] 2.2 Extend the `products` `Field(description=...)` of `EndpointConfig` with the uniqueness sentence.
  - [x] 2.3 `uv run cvestream config schema`; check that the diff of `schemas/config-v1.json` is the `products` description only.
  - [x] 2.4 Tests in `tests/unit/config/test_models.py`, `tests/unit/config/test_provider.py`, `tests/unit/config/test_schema.py` and `tests/unit/cli/test_config.py` (see "Testing requirements").
  - [x] 2.5 One commit with models, schema and tests: `feat(story-5.4): reject duplicate product names within an endpoint`.

- [x] **Task 3: User documentation (AC: 7)**
  - [x] 3.1 `docs/configuration.md` and `CHANGELOG.md` (exact edits in "Documentation edits").
  - [x] 3.2 Confirm `README.md` and `AGENTS.md` need nothing: `grep -n -i "duplicate\|distinct\|products\[" README.md AGENTS.md`.
  - [x] 3.3 Commit: `docs(story-5.4): document unique product names per endpoint`.

- [x] **Task 4: Planning docs (AC: 7)**
  - [x] 4.1 `_bmad-output/project-context.md` (exact edit in "Planning doc amendments"). `architecture.md` needs no change: check that the F3 row still reads "Product entry names are unique per endpoint (story 5.4)."
  - [x] 4.2 Commit: `docs(story-5.4): record the unique-name rule in the project context`.

- [x] **Task 5: Gates and delivery (AC: 8)**
  - [x] 5.1 Run every gate command of `AGENTS.md`. The local `docker build` may fail on a ghcr.io x509 error behind the corporate proxy; CI is then the gate for it (note it in the Dev Agent Record).
  - [x] 5.2 Story file: tick the tasks, fill the Dev Agent Record, set Status to `review`; `sprint-status.yaml`: `5-4-reject-duplicate-product-names: review`. Commit: `chore(story-5.4): mark story ready for review`.
  - [x] 5.3 Push over SSH (`git@github.com:wixyvir/cvestream.git`) and update the draft PR opened with this story (`feat(story-5.4): reject duplicate product names`): mark it ready for review, keep `Closes wixyvir/cvestream#48`, list the open questions below with the decision taken. GitHub references in full form.

## Dev Notes

### Scope boundaries (read first)

- **In scope:** `src/cvestream/config/models.py`, `schemas/config-v1.json`, the config and CLI unit tests, `docs/configuration.md`, `CHANGELOG.md`, `_bmad-output/project-context.md`.
- **Out of scope:**
  - `src/cvestream/pipeline/render.py`: `_title_product()` keeps its `p.name == name` lookup. With unique names it is no longer ambiguous. Do **not** edit it, not even its comment: story 5.1 rewrites `render.py` in parallel and any edit there is a needless conflict.
  - Storing the matched entry's index in `publication` (the issue's alternative): rejected, it touches the state and needs a migration; 5.1 owns the wave's only Alembic revision.
  - `config/provider.py` (`_format_error`, `build_snapshot`), `config/yaml_provider.py`, `cli/config.py`, `app/`, `pipeline/`, `domain/`: no change. The rule flows through them.
  - Alias collisions (an alias equal to another entry's name or alias): not rejected (AC 3, Open question 6).
  - Epic 3 code (`config/valkey_provider.py`, `config/keys.py`, `cli/endpoint.py`, `cli/product.py`) does not exist yet (`epic-3: backlog`). Nothing to write for it; see "Valkey provider and CLI write paths".

### Current state of the files this story touches

| File | Current state | Change | Preserve |
|---|---|---|---|
| `src/cvestream/config/models.py` | `EndpointConfig` has field-level validation only. Cross-item uniqueness exists once, in `ConfigDocument._unique` (endpoint ids and tokens), whose messages carry the path because "a model-level error has no location" of its own. `_split_shorthand()` returns `(name, version)`; `Term` strips names. | private helpers + `EndpointConfig` `model_validator(mode="after")`; one sentence in the `products` description | `_CONFIG` (strict, `extra="forbid"`), `ConfigDocument._unique`, `_to_product()`, every existing error path and message |
| `schemas/config-v1.json` | generated | regenerated (description only) | never hand-edited |
| `docs/configuration.md` | line ~97 ends the "Exact entries" paragraph with the "not rejected yet, wixyvir/cvestream#48" note; line ~94 is the product-entry paragraph; line ~121 lists what the schema does not check | see "Documentation edits" | existing wording elsewhere |
| `CHANGELOG.md` | `[Unreleased]` has `### Added` (`require_score`, `exact`) | add `### Changed` | existing entries |
| `_bmad-output/project-context.md` | "Configuration (Valkey / YAML)" says `config/models.py` is the single source of truth | one rule | rest of the file |

### Config contract (`src/cvestream/config/models.py`)

Prototyped on 2026-11-21 with the project's pydantic 2.14.3, in a throwaway subclass outside the repository: a `model_validator(mode="after")` on `EndpointConfig` that raises `ValueError` is reported at `loc == ("endpoints", 0)`, and `build_snapshot()`'s `_format_error` turns it into `endpoints[0]: products[1]: duplicate product name 'PHP' (same as products[0])` (the `Value error, ` prefix is stripped). Validating an `EndpointConfig` alone gives the line `products[1]: duplicate product name 'PHP' (same as products[0])`. When another field of the endpoint is invalid (for example an unknown key in a product), pydantic stops before the after-validator and only that field error is reported, which is the expected behaviour.

Suggested shape (names are free, keep them private):

```python
def _normalized_name(name: str) -> str:
    # The matcher's equality for exact entries (`domain/matcher.py`): case and
    # whitespace runs never make two names different.
    return " ".join(name.split()).casefold()


def _item_name(item: str | ProductEntryConfig) -> str:
    return _split_shorthand(item)[0] if isinstance(item, str) else item.name
```

In `EndpointConfig`, after `to_domain()` or before it, as the file reads best:

```python
    @model_validator(mode="after")
    def _unique_product_names(self) -> EndpointConfig:
        # The item title and story 5.2's groups find an entry by its name.
        # The message carries the path: a model-level error is located at the
        # endpoint only.
        seen: dict[str, int] = {}
        for index, item in enumerate(self.products):
            name = _item_name(item)
            key = _normalized_name(name)
            if key in seen:
                msg = (
                    f"products[{index}]: duplicate product name {name!r} "
                    f"(same as products[{seen[key]}])"
                )
                raise ValueError(msg)
            seen[key] = index
        return self
```

Notes:
- `_split_shorthand()` cannot fail here: the `Shorthand` `AfterValidator` already accepted the string. Calling it twice per shorthand (once in validation, once in `_to_product()`) is fine; the lists are short.
- Do **not** import `domain.matcher._normalized` (private, and the config layer should not depend on matcher internals). The one-line duplicate is deliberate; the comment points to the matcher. If the reviewer prefers one shared function, it would have to be a public helper in `domain/` (e.g. on `ProductEntry`), which widens the story: record the choice in the Dev Agent Record.
- Why `EndpointConfig` and not `ConfigDocument._unique`: putting the check in `ConfigDocument` would give a one-path line (`endpoints[0].products[1]: ...`) but would not protect a standalone `EndpointConfig` validation, which the CLI write commands of story 3.2 are expected to use before writing to Valkey. The epic AC says "through the same `EndpointConfig`". See Open question 2 for the line format.
- Style: build the message in a `msg` variable, then `raise ValueError(msg)`, as every validator of the file does.
- `ty`: the return annotation `-> EndpointConfig` works without quotes (Python 3.14, deferred annotations), like `ProductEntryConfig._exact_contradiction`.

`products` description, suggested wording (keep the existing sentences):

```python
        description=(
            "Products to watch: a shorthand string or a full entry. "
            "Names must be unique within the endpoint (case-insensitive). "
            "An endpoint without products matches nothing."
        ),
```

### Valkey provider and CLI write paths (epic 3, not implemented yet)

- **Valkey provider (3.1):** reads `cvestream:v1:endpoint:{id}:products` (a JSON list) and validates through `build_snapshot()` → `ConfigDocument` → `EndpointConfig`. The rule applies with no extra code. Connectors may write `endpoint:*:products` directly (ACL in the architecture); a connector that writes a duplicate makes the whole snapshot invalid, so the daemon keeps the last valid configuration and logs `config.rejected` at each revision change, as for any invalid write. Worth one sentence in `docs/config-contract.md` when 3.1 creates it (recorded under Open question 5, nothing to do now).
- **CLI (3.2):** `product add` / `endpoint edit` should validate the resulting `EndpointConfig` before the `WATCH/MULTI/EXEC` write, so a duplicate is refused with exit code 2 and the one-line message instead of being written and then rejected by the daemon. Whoever writes 3.2 should note it there.
- **`config export` (3.2):** a valid configuration has unique names, so the export always re-imports.

### Backward compatibility

- A tightening of contract v1: a configuration that loads today can be rejected after the upgrade. The project is pre-1.0 (`0.1.0`), and story 4.2 tightened v1 the same way (`exact` + explicit `match_description: true`) without changing `SCHEMA_VERSION`. Keep `1`.
- Upgrade impact, to say in the CHANGELOG: with a duplicate in `endpoints.yaml`, the new version refuses to start (exit 2, `config.rejected`); a reload with a duplicate keeps the last valid configuration. Fix: rename the second entry (an alias of the first works) and run `cvestream config validate` before upgrading.
- Feeds and state are unaffected: no GUID, no stored row, no rendered byte changes. An item already published under a renamed entry keeps its stored product name; `_title_product()` then finds no configured entry with that name and falls back to the bare name, which is today's behaviour for a removed or renamed entry (unchanged, out of scope).

### Testing requirements

Use the existing helpers of each file; name tests after what they show, as the files do.

`tests/unit/config/test_models.py` (helpers `_endpoint`, `_document`, `_validate`, `_message`, `_only_endpoint`, `TOKEN_A`, `TOKEN_B`), new section `# --- unique product names (wixyvir/cvestream#48) ---`:
- `test_duplicate_product_names_rejected`, parametrized with ids, each asserting the exact message from `_message(...)`:
  - two full entries: `[{"name": "php", "exact": True}, {"name": "php"}]` → `products[1]: duplicate product name 'php' (same as products[0])`;
  - case: `["php", {"name": "PHP"}]` → `... 'PHP' (same as products[0])`;
  - shorthand with a version: `["php >= 8.2", "php"]` → `... 'php' ...`;
  - surrounding and inner whitespace: `["apache http server", {"name": "Apache  HTTP\tServer"}]` → name quoted with `!r` (`'Apache  HTTP\tServer'`); `Term` strips the ends only, so the inner runs stay as written;
  - third item collides with the first: `["php", "nodejs", {"name": "PHP"}]` → `products[2]: ... (same as products[0])`.
- `test_duplicate_product_name_location_is_the_endpoint`: the `loc` of the single error is `("endpoints", 0)` (built like `test_rejected`), and for a second endpoint `("endpoints", 1)`.
- `test_duplicate_product_names_rejected_on_a_standalone_endpoint`: `EndpointConfig.model_validate(_endpoint(products=["php", "PHP"]))` raises with the same message (the future CLI write path).
- `test_distinct_product_names_accepted`, parametrized: the alias workaround (`[{"name": "php", "aliases": ["php-src"], "exact": True}, {"name": "php-src", "match_description": False}]`), two entries sharing an alias (`[{"name": "a", "aliases": ["x"]}, {"name": "b", "aliases": ["x"]}]`), near names (`["php", "php-fpm", "phpmyadmin"]`). Each validates and `to_domain().products` has one entry per item, in order.
- `test_same_product_name_in_two_endpoints_accepted`: `_document(_endpoint(products=["php"]), _endpoint(id="other", token=TOKEN_B, products=["php"]))` validates.
- `test_disabled_endpoint_is_checked_too`: `enabled: False` with duplicates is still rejected (a disabled endpoint is kept in the snapshot and can be re-enabled).

`tests/unit/config/test_provider.py` (helpers `_data`, `_endpoint`, `_errors`):
- `test_duplicate_product_name_error_line`: `_errors(_data(_endpoint(products=["php", {"name": "PHP", "exact": True}])))` equals `("endpoints[0]: products[1]: duplicate product name 'PHP' (same as products[0])",)`.
- Same with the duplicate in the second endpoint (`_endpoint(id="other", token=TOKEN_B, ...)`) → `endpoints[1]: ...`.

`tests/unit/cli/test_config.py`: add a row to `test_validate_invalid_file`, id `duplicate-product-name`: content `VALID + "      - php\n"`, field `"products[1]: duplicate product name 'php'"`. The test already checks exit code 2, empty stdout, one stderr line, no traceback and no token.

`tests/unit/config/test_schema.py`: `test_unique_product_names_are_described`: `"unique" in config_schema()["$defs"]["EndpointConfig"]["properties"]["products"]["description"]`. `test_committed_schema_is_fresh` covers the regenerated file, and `test_every_property_has_a_description` keeps passing.

`tests/unit/config/test_yaml_provider.py`: nothing new is required (`test_example_file_is_valid` covers AC 6). Optional: one load test with a duplicate written in YAML, asserting the line contains `products[1]`.

No fixture, no domain test, no render test (render is unchanged and 5.1 edits `tests/unit/pipeline/test_render.py`).

### Documentation edits

- **`docs/configuration.md`:**
  - Product-entry paragraph (starts "A product entry has `name` (required), ..."): after "Names, aliases and exclusion terms are stripped and must not be blank.", add: "Entry names must be unique within an endpoint, ignoring case and whitespace runs, and a shorthand counts by its name: `php`, `PHP` and `php >= 8.2` in one endpoint are rejected. An alias of one entry can be another entry's name, and two endpoints can watch the same name."
  - "Exact entries" paragraph: delete the last sentence ("Entries of one endpoint need distinct `name`s: the item title looks up the first entry with the matched name, so a second, non-exact early-warning entry for the same product must use another `name` (an alias works; duplicate names per endpoint are not rejected yet, wixyvir/cvestream#48)."). Replace it with: "A second, non-exact early-warning entry for the same product needs another `name`, since names are unique per endpoint: an alias of the exact entry works."
  - "JSON Schema" section: "unique ids and tokens, valid version strings and duplicate YAML keys are checked by `cvestream config validate`" → "unique ids and tokens, unique product names per endpoint, valid version strings and duplicate YAML keys are checked by `cvestream config validate`".
- **`CHANGELOG.md`**, under `## [Unreleased]`, after the `### Added` list:

  ```markdown
  ### Changed

  - Product entry names must be unique within an endpoint, ignoring case and whitespace runs; a shorthand counts by its name, so `php` and `php >= 8.2` collide (wixyvir/cvestream#48). This tightens the v1 configuration contract: a file with duplicate names loaded before and is now rejected. `cvestream config validate` exits 2, the daemon refuses to start with it, and a reload keeps the last valid configuration (`config.rejected`). Before upgrading, rename the second entry (an alias of the first works) and run `cvestream config validate`.
  ```

  If 5.1 or 5.3 merged a `### Changed` heading first, add the bullet under it instead of a second heading.
- **`README.md`, `AGENTS.md`, `docs/operations.md`, `endpoints.example.yaml`:** no change expected. `docs/operations.md` already documents `config.rejected` and exit code 2 generically.

### Planning doc amendments

- `_bmad-output/project-context.md`, section "Configuration (Valkey / YAML)", after the first bullet (`config/models.py` ... single source of truth): add "- Product entry names are unique per endpoint (whitespace runs collapsed, case-folded; a shorthand counts by its name), checked by `EndpointConfig` so every provider and the CLI get the rule (story 5.4). Code may look an entry up by its name."
- `architecture.md`: no change. The F3 row already says "Product entry names are unique per endpoint (story 5.4)" (added by `3c08cdd`). This story does not change a pattern beyond what the course correction recorded.
- `epics.md`, PRD: no change.

### Parallel work in wave 13 (5.1 | 5.3 | 5.4)

- Story 5.1 (compact item body and title markers) and story 5.3 (held-back count in `cycle.summary`, wixyvir/cvestream#47) run at the same time, each on its own branch and PR from the same base `3c08cdd`.
- Shared files: `docs/operations.md`, `CHANGELOG.md`, `README.md`. This story touches only `CHANGELOG.md` among them. Whichever PR merges later rebases on `main` and merges the `[Unreleased]` headings by hand (one `### Added`, one `### Changed`).
- `sprint-status.yaml`: all three create-story commits set `epic-5: in-progress`; the same change on three branches rebases cleanly or with a trivial conflict.
- 5.1 owns the wave's only Alembic revision (`publication.is_fresh`). This story adds no migration.
- `config/models.py` and `schemas/config-v1.json`: 5.1 and 5.3 are not expected to touch them. If one does, the later PR regenerates the schema with `uv run cvestream config schema`, never merging the JSON by hand.
- Story 5.2 (wave 14) adds `group_by_product` to `EndpointConfig` and keys its groups on the matched entry name. It starts when 5.1 and 5.4 are `done` and relies on the uniqueness this story guarantees.

### Architecture compliance

- Validation only at the boundary (`config/models.py`); `domain` is untouched and stays pure.
- `config/models.py` is a public contract: the schema is regenerated in the same commit (AGENTS.md), even for a description change.
- Error messages name the field and the problem, never the input token (`config/provider.py` docstring). A product name is not a secret.
- No new exception class (pydantic's `ValidationError` becomes the existing `ConfigValidationError`), no new log event, no state change, no new dependency.
- Conventional Commits with `story-5.4`, one concern per commit, never squashed. GitHub references in full form (`wixyvir/cvestream#48`).

### Library / framework requirements

No new dependency. pydantic 2.14 (`model_validator(mode="after")`, already used by `ProductEntryConfig` and `ConfigDocument`), stdlib `str.split` and `str.casefold`. `uv run` for everything, never pip or poetry.

### Previous story intelligence

- **Story 4.2 (exact entries, wixyvir/cvestream#46)** is the direct precedent and the source of this issue:
  - It added a `model_validator(mode="after")` on `ProductEntryConfig` and proved the error line format through `build_snapshot()` (`endpoints[0].products[0]: exact: true contradicts ...`). The same mechanism one level up gives `endpoints[0]: products[1]: ...`.
  - It tightened v1 without bumping `SCHEMA_VERSION` and put a contract note in the CHANGELOG; review P5 asked for that note to be explicit. Do the same here, with the upgrade step.
  - Its review decided D2 option 1: defer this check and document the workaround (distinct names, alias as the second name). This story removes the "not rejected yet" wording and keeps the workaround.
  - Its normalization for exact names is `" ".join(text.split()).casefold()` (`domain/matcher.py` `_normalized`). Reuse the rule, not the private function.
  - Its review asked for real commit hashes and complete open questions in the Dev Agent Record and the PR body.
- **Story 4.1 (`require_score`)** shows the small-field pattern for `EndpointConfig` and the docs table in `docs/configuration.md`.

### Git intelligence

- `main` is at `8d9590a` (merge of wixyvir/cvestream#46, story 4.2). This branch adds `3c08cdd docs(epic-5): add epic 5 by course correction` (planning docs only) and this story's commit.
- The last changes to `config/models.py` were story 4.2 (`exact`, the `_exact_contradiction` validator, `_EXACT_CONTRADICTION` schema clause, field wording fixes in review) and story 4.1 (`require_score`). No open branch other than the wave-13 ones is expected to touch it.

### Latest technical information

No external API or library change. The behaviour the story relies on was checked against the installed pydantic 2.14.3 on 2026-11-21 (throwaway script outside the repository, see "Config contract"): location of an `EndpointConfig` after-validator error, the formatted line from `config.provider._format_error`, the standalone `EndpointConfig` line, and the short-circuit when another field is invalid. JSON Schema 2020-12 has `uniqueItems` (whole-item equality only) and no keyword for uniqueness of one property across items, so the rule cannot be published in the schema; the description carries it.

### Open questions (decided by default, to confirm in review)

1. **Normalization.** The epic AC says "equal after stripping and case-folding". **Default taken:** also collapse inner whitespace runs (`" ".join(name.split()).casefold()`), the matcher's equality for exact entries, because the matcher treats `apache  http server` and `apache http server` as the same term. Stricter than the AC's wording, never looser. Alternative: `name.strip().casefold()` only.
2. **Error line format.** **Default taken:** `endpoints[0]: products[1]: duplicate product name 'PHP' (same as products[0])`, produced by an `EndpointConfig` validator and the unchanged `_format_error`. It has two colons, like the existing `endpoints[0].products[0]: exact: true contradicts ...`. Alternatives: (a) check in `ConfigDocument._unique` for a single path `endpoints[0].products[1]: ...`, but a standalone `EndpointConfig` (CLI 3.2) would not be protected; (b) teach `_format_error` to join a message that starts with a relative path, a special case in shared code. Rejected for now.
3. **Schema description.** The course correction said "only the validator changes; `config schema --check` stays green". **Default taken:** also add one sentence to the `products` description and regenerate the schema, because the schema is what connector authors read and it cannot carry the rule otherwise. Alternative: leave models' descriptions untouched and keep the schema byte-identical.
4. **Contract version.** **Default taken:** `SCHEMA_VERSION` stays `1` (pre-1.0, precedent 4.2), with an explicit CHANGELOG upgrade note. Alternative: a v2 contract, judged out of proportion for a rule the docs already asked for.
5. **Connectors writing Valkey directly (epic 3).** A duplicate written by a connector straight to `endpoint:{id}:products` invalidates the whole snapshot until fixed (last valid configuration kept, `config.rejected`). **Default taken:** accept it, as for any invalid connector write; story 3.1 should say so in `docs/config-contract.md`, and story 3.2's write commands should validate `EndpointConfig` before writing.
6. **Alias collisions.** An alias equal to another entry's name or alias is accepted (AC 3: the documented workaround uses an alias as a name). It cannot confuse the title lookup or 5.2's groups, which use entry names only. **Default taken:** no check. Alternative: reject an alias equal to another entry's name, which would forbid the workaround.
7. **Renamed entries.** An item published before an entry is renamed (for example to remove a duplicate) keeps its stored product name; its title then falls back to the bare name. Pre-existing behaviour, unchanged here, and `render.py` stays untouched because of story 5.1.

### Project Structure Notes

- No new module, file or package. The helpers stay private in `config/models.py` (no `utils.py`).
- Tests go where the tree says: `tests/unit/config/` and `tests/unit/cli/`.
- No conflict with the unified structure was detected.

### References

- Issue: wixyvir/cvestream#48 (problem, proposal, alternative, interim mitigation).
- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.4: Reject duplicate product names within an endpoint]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-21-epic-5-readable-bursts.md] (technical impact of 5.4, risks, wave 13 caveats, 5.2's dependency)
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements Overview] (F3 row), [#Data Architecture] (Valkey key layout, ACLs), [#Enforcement Guidelines] (config models shared by YAML, Valkey, CLI and schema)
- [Source: _bmad-output/project-context.md#Configuration (Valkey / YAML)], [#Code Quality & Style Rules] (docs follow behaviour)
- [Source: _bmad-output/implementation-artifacts/4-2-exact-product-matching.md] (validator precedent, D2 decision, normalization)
- [Source: src/cvestream/config/models.py] (`EndpointConfig`, `ConfigDocument._unique`, `_split_shorthand`, `Term`), [src/cvestream/config/provider.py] (`build_snapshot`, `_format_error`), [src/cvestream/cli/config.py] (`validate`, exit code 2), [src/cvestream/app/runtime.py] (`_load_snapshot`), [src/cvestream/pipeline/run.py] (`config.rejected` on reload), [src/cvestream/pipeline/render.py] (`_title_product`), [src/cvestream/domain/matcher.py] (`_normalized`)
- [Source: docs/configuration.md] (product entries, "Exact entries", "JSON Schema"), [CHANGELOG.md], [AGENTS.md] (gate commands)

## Dev Agent Record

### Agent Model Used

Mistral Vibe (vibe@mistral.ai), running the `bmad-dev-story` workflow on branch `story-5-4-reject-duplicate-product-names` (baseline_commit `3c08cdd`, preserved).

### Debug Log References

- Red phase: the 12 new tests failed before the validator (`DID NOT RAISE ValidationError`, CLI exit 0, schema description without "unique"); green after the implementation commit.
- pydantic 2.14.3 puts a `Value error, ` prefix on the `msg` of an after-model-validator; `config/provider.py` `_format_error` strips it, so the provider/CLI line is `endpoints[0]: products[1]: duplicate product name 'PHP' (same as products[0])`. The model tests assert the stripped message.
- Manual CLI check (AC 2): a file with `php >= 8.2` plus `php` exits 2, prints nothing on stdout and exactly one stderr line `<file>: endpoints[0]: products[1]: duplicate product name 'php' (same as products[0])`.
- `schemas/config-v1.json` regenerated with `uv run cvestream config schema`; the diff is the `products` description line only. `config schema --check` passes.
- `origin/main` was still at `8d9590a` when the gates ran, so no rebase onto a newer main was needed (task 1.1).
- Local `docker build --target runtime -t cvestream:local .` succeeded (exit 0); the ghcr.io x509 proxy error did not occur this run, so no CI fallback was needed.

### Completion Notes List

- Story context created by the create-story workflow (2026-11-21): developer guide built from wixyvir/cvestream#48, epic 5, the sprint change proposal of 2026-11-21, architecture, project context, story 4.2 and the current code, with the error location prototyped against pydantic 2.14.3.
- Implemented `_unique_product_names` (`model_validator(mode="after")`) on `EndpointConfig` in `src/cvestream/config/models.py`, with private helpers `_normalized_name` (`" ".join(name.split()).casefold()`, the matcher's exact-entry equality, duplicated deliberately instead of importing private `domain.matcher._normalized`) and `_item_name` (a shorthand counts by the first group of `_split_shorthand()`). Only the first collision is reported; the message carries the relative path because a model-level error is located at the endpoint only.
- The `products` field description gained the uniqueness sentence; `schemas/config-v1.json` regenerated in the same commit (description-only diff). `SCHEMA_VERSION` stays `1` (tightening of v1, precedent story 4.2).
- Tests added: `tests/unit/config/test_models.py` (5 parametrized rejection cases incl. case/whitespace/shorthand/third-collides, error location `("endpoints", 0)`/`("endpoints", 1)`, standalone `EndpointConfig` rejection, 3 accepted cases incl. the alias workaround, same name in two endpoints, disabled endpoint still checked), `tests/unit/config/test_provider.py` (exact error line `endpoints[0]: products[1]: ...` and the second-endpoint variant), `tests/unit/cli/test_config.py` (row `duplicate-product-name`: exit 2, empty stdout, one stderr line, no token), `tests/unit/config/test_schema.py` (`test_unique_product_names_are_described`). Full suite: 1240 passed, 6 deselected.
- Docs: `docs/configuration.md` (uniqueness rule + shorthand + alias workaround in the product-entry paragraph, "Exact entries" last sentence replaced, unique product names listed among the checks the JSON Schema cannot make), `CHANGELOG.md` (`### Changed` under `[Unreleased]` with the upgrade note, wixyvir/cvestream#48), `_bmad-output/project-context.md` (one rule under "Configuration (Valkey / YAML)"). `README.md` and `AGENTS.md` checked with grep: nothing to change. `architecture.md` F3 row already reads "Product entry names are unique per endpoint (story 5.4)" — no change.
- Out of scope respected: `pipeline/render.py`, `config/provider.py`, `config/yaml_provider.py`, `cli/config.py`, `app/`, `pipeline/`, `domain/` untouched; no new exception, log event, exit code, migration or dependency.
- Gates (all of AGENTS.md, 2026-11-21): `uv sync --locked` OK; `ruff check` OK; `ruff format --check` OK; `ty check` OK; `lint-imports` 7 contracts kept, 0 broken; `pytest` 1240 passed; `cvestream config schema --check` OK; `cvestream config validate endpoints.example.yaml` OK; `docker build --target runtime -t cvestream:local .` OK (exit 0).
- Open questions: all defaults taken as written in the story — (1) normalization also collapses inner whitespace runs, stricter than the AC wording, never looser; (2) error line keeps the two-colon form from the `EndpointConfig` validator; (3) one uniqueness sentence added to the `products` description and the schema regenerated; (4) `SCHEMA_VERSION` stays `1` with the CHANGELOG upgrade note; (5) a connector writing a duplicate to Valkey invalidates the snapshot until fixed (last valid config kept, `config.rejected`); (6) alias collisions are not rejected (the documented workaround needs an alias as a name); (7) renamed-entry title fallback is pre-existing behaviour, unchanged.
- Delivered as PR wixyvir/cvestream#50 (draft marked ready), closes wixyvir/cvestream#48. Commits: `348843e` feat (validator + schema + tests), `b645241` docs (configuration.md + CHANGELOG.md), `5473ef4` docs (project-context.md), plus the review-marking chore commit.

### File List

- `src/cvestream/config/models.py` — modified (validator, helpers, `products` description)
- `schemas/config-v1.json` — regenerated (description-only diff)
- `tests/unit/config/test_models.py` — modified (unique-name section)
- `tests/unit/config/test_provider.py` — modified (two error-line tests)
- `tests/unit/config/test_schema.py` — modified (description test)
- `tests/unit/cli/test_config.py` — modified (`duplicate-product-name` row)
- `docs/configuration.md` — modified (rule, exact-entries sentence, JSON Schema section)
- `CHANGELOG.md` — modified (`### Changed` under `[Unreleased]`)
- `_bmad-output/project-context.md` — modified (one configuration rule)
- `_bmad-output/implementation-artifacts/5-4-reject-duplicate-product-names.md` — this file (tasks, Dev Agent Record, Status)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `5-4-reject-duplicate-product-names: review`

### Change Log

- 2026-11-21: implemented the `EndpointConfig` unique-product-name validator, regenerated `schemas/config-v1.json`, added the unit/CLI/schema tests (commit `348843e`).
- 2026-11-21: documented the rule in `docs/configuration.md` and `CHANGELOG.md` (commit `b645241`); recorded it in `_bmad-output/project-context.md` (commit `5473ef4`).
- 2026-11-21: all AGENTS.md gates green (docker build included); story marked `review`, sprint-status updated, PR wixyvir/cvestream#50 marked ready.
