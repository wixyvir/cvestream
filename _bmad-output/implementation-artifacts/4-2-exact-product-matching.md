---
baseline_commit: 3b6702591590aa442203f3ff96bf2e11cc56c78b
---

# Story 4.2: Exact product matching

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Created from GitHub issue wixyvir/cvestream#44. -->

## Story

As a channel subscriber,
I want an optional product-entry field that requires the whole affected-product name to match,
so that short, generic names like `php` or `python` stop pulling in unrelated products whose names or vendors contain them.

Added from GitHub issue wixyvir/cvestream#44. Today a name matches as a whole word against the affected product **or the vendor** (`_hits()` in `src/cvestream/domain/matcher.py`), and a hyphen is a word boundary. `min_version` cannot cut the noise, because these products use version formats the parser does not read (git hashes, `2.0.0a1, < 2.2.0`), so they pass as "versions unknown". Measured on every EUVD record updated since 2026-08-01 that mentions `php` or `python`, replayed through the real parser and matcher:

- `php` with `min_version: "7.4"` and `match_description: false`: **75 matches, only 13 of them `PHP Group / PHP`**. The others are project names (`CloudClassroom-PHP-Project` in 25+ records, `php-travel-agency-system`, `file-management-system-php`, `TikTok-Video-Downloader-PHP`, `core-php-admin-panel`, `graphql-php`, `php-sdk`) and vendor names (vendor `PHP` on product `frankenphp` in 15 records, vendor `php-web-design.com` on a Joomla extension).
- `python` (alias `cpython`, `min_version: "3.9"`): **38 matches, about 3 of them CPython**. The others include vendor `python-pillow` (Pillow), `modelcontextprotocol/python-sdk`, `SageMaker Python SDK`, `Snowflake Connector for Python`, `MongoDB Python Driver`, `python-engineio` and `HPE NonStop Python 3.x`.

The only workaround today is a growing `exclude` list, which is whack-a-mole.

## TL;DR

- **New opt-in field** `exact: bool = False` on full product entries (config contract v1, additive) and on the domain `ProductEntry`.
- **Rule when `exact` is true:** a hit needs the **whole affected-product name** to equal the entry name or one of its aliases (case-insensitive, whitespace runs collapsed). Vendor names are ignored. The description is never searched. `min_version` and `exclude` work as before. `affected_hits()` (which names the product in the item title) follows the same rule.
- **Validation:** `exact: true` together with an **explicit** `match_description: true` is rejected at the config boundary (model validator, plus a `not` clause in the published JSON Schema). The shorthand `php >= 8.2` stays word matching.
- **One design choice:** a single private helper decides "does this affected product name the entry", used for hits, for the title ordering and for the version check, so the three paths cannot drift apart.
- **Accepted trade-off (documented, see Open questions):** CERT-EU affected products are prose, so they never equal a product name. A CERT-EU-only advisory does not match an `exact` entry until EUVD lists the product. The same goes for a KEV-only vulnerability.
- **Also in the same PR:** tests built on the EUVD shapes of the issue, docs (`docs/configuration.md`, `docs/sources.md`, `endpoints.example.yaml`, `CHANGELOG.md`, the matcher docstring), the regenerated `schemas/config-v1.json`, and amended planning docs (`architecture.md`, `project-context.md`, PRD and epics FR-11/FR-12), because the matching rule changes.
- No new dependency, no migration, no change to publication or render code.

## Acceptance Criteria

1. **Domain field (FR-11).** `ProductEntry` in `src/cvestream/domain/endpoint.py` gains `exact: bool = False` as its **last** field. All current constructions (`ProductEntry(name)` in `pipeline/render.py`, keyword constructions in tests) keep working, and `ProductEntry` stays frozen, slotted and hashable.

2. **Exact hits (FR-12).** In `src/cvestream/domain/matcher.py`, for an entry with `exact=True`:
   - an affected product is a hit only when its `product` name, normalized (whitespace runs collapsed to one space, stripped, `str.casefold()`), **equals** the normalized entry name or one of its normalized aliases;
   - the `vendor` field is never used to find a hit;
   - the description is never searched, whatever `match_description` says: a vulnerability without affected-product data never matches the entry;
   - every hit counts as a product-name hit for the version check, so `min_version` is checked against all hits' `version_text` with the current rules (reaching → match, unparsable → `versions_unknown`, all known and below → no match);
   - a match is always `ProductMatch(structured=True, ...)`.

3. **Exclusions unchanged.** `exclude` terms keep plain whole-word matching against product names, vendor names **and** descriptions, for exact entries too. An exclusion still drops the entry before any hit is searched.

4. **Default unchanged.** With `exact=False` (the default), matching is byte-for-byte the current behaviour: every existing test in `tests/unit/domain/test_matcher.py`, `tests/unit/pipeline/test_render.py` and `tests/unit/pipeline/test_run.py` passes **unchanged**.

5. **Title naming.** `affected_hits(entry, affected)` returns only exact hits for an exact entry (no vendor-only hits). The render keeps calling it unchanged (`_title_product()` in `src/cvestream/pipeline/render.py` looks up the configured entry by name), so the title of an exact entry's item names the exact product (`PHP Group PHP`, never `PHP Group php-fpm`).

6. **Config contract.** `ProductEntryConfig` in `src/cvestream/config/models.py` gains `exact: bool = Field(default=False, description=...)` after `match_description`. It is strict like the other booleans (`"yes"` is rejected), `_to_product()` passes `exact=item.exact`, and the shorthand branch passes nothing (`exact` stays `False`). The contract stays `version: 1` (`SCHEMA_VERSION` unchanged).

7. **Contradiction rejected.** A full entry with `exact: true` **and** an explicit `match_description: true` is rejected by a `model_validator(mode="after")` on `ProductEntryConfig` that reads `self.model_fields_set`. The provider formats the error as one line, `endpoints[0].products[0]: exact: true contradicts match_description: true; omit match_description or set it to false`. `exact: true` with `match_description` omitted, or explicitly `false`, is accepted. The published JSON Schema carries the same rule as a `not` clause on `ProductEntryConfig`, so third-party validators reject the combination too.

8. **Schema regenerated.** `schemas/config-v1.json` is regenerated with `uv run cvestream config schema` **in the same commit** as `config/models.py`. The diff only adds the `exact` property and the `not` clause.

9. **Tests built on the issue's EUVD shapes.**
   - Hits: `{name: php, exact: true}` matches `PHP Group / PHP`. `{name: cpython, aliases: [python/cpython], exact: true}` matches `Python Software Foundation / CPython`.
   - Misses for `php` exact: `php-travel-agency-system`, `CloudClassroom-PHP-Project`, `PHP Driver` (vendor `MongoDB`), vendor `PHP` / product `frankenphp`, and a description-only vulnerability ("A flaw in PHP 8.3 ...", no affected products).
   - For each miss, the same vulnerability **does** match the non-exact `php` entry, so the test shows the difference.
   - Coverage also includes `min_version` with exact hits, exclusions with an exact entry, `affected_hits()` ordering and filtering, and the config, provider and schema tests listed in "Testing requirements".

10. **Documentation.** `docs/configuration.md` (product entry field list, "How an entry matches", the YAML example), `docs/sources.md` (CERT-EU "Matching" and CISA KEV "No affected products" bullets), `endpoints.example.yaml` (a commented exact entry), `CHANGELOG.md` (`[Unreleased]` → `Added`) and the `matcher.py` module docstring describe `exact`. They state the CERT-EU and KEV-only trade-off and the notes of the issue: PHP's bundled extensions are filed under product `PHP`, while PECL extensions are separate products and need their own entries.

11. **Planning docs amended (pattern change).** In the same PR: `_bmad-output/planning-artifacts/architecture.md` (F3 row of the FR table, and the `endpoint:{id}:products` line of the Valkey key layout), `_bmad-output/project-context.md` ("Matching (FR-12)"), and FR-11/FR-12 in the PRD (`prds/prd-cvestream-2026-10-26/prd.md`) and in the requirements inventory of `epics.md` mention the exact mode.

12. **No retraction, no backfill.** Publications already made are not retracted or re-decided. Turning `exact` on for an existing endpoint only changes future decisions (unchanged rules of `domain/publication.py` and `pipeline/run.py`).

13. **Gates.** Every gate command of `AGENTS.md` passes, including `uv run cvestream config schema --check` and `uv run cvestream config validate endpoints.example.yaml`.

## Tasks / Subtasks

- [x] **Task 1: Branch (AC: all)**
  - [x] 1.1 Work on `story-4-2-exact-product-matching` (created from `main` at `3b67025` with this story file). If the PR that carries this story file is already merged, recreate the branch from up-to-date `main` with the same name. No `/` in the branch name. It is not stacked on 4.1 (see "Parallel work with story 4.1").

- [x] **Task 2: Domain field and matcher (AC: 1, 2, 3, 4, 5, 9)**
  - [x] 2.1 `src/cvestream/domain/endpoint.py`: add `exact: bool = False` as the last field of `ProductEntry`, with a one-line comment (see "Domain entry").
  - [x] 2.2 `src/cvestream/domain/matcher.py`: add the private name-equality helper and the entry-aware helpers described in "Matcher design". Route `_match_entry()` (hits, description fallback guard, version check) and `affected_hits()` through them. Keep `_pattern()`, `_description_pattern()`, `_mentions()`, `_mentioned_in_prose()` and `_hits()` unchanged. Exclusions keep using `_hits(entry.exclude, affected)` and `_mentions(entry.exclude, d)`.
  - [x] 2.3 Update the module docstring (see "Documentation edits").
  - [x] 2.4 Tests in `tests/unit/domain/test_matcher.py` and `tests/unit/domain/test_endpoint.py` (see "Testing requirements").
  - [x] 2.5 Add one title test in `tests/unit/pipeline/test_render.py` (see "Testing requirements"). Render code is unchanged.
  - [x] 2.6 Commit: `feat(story-4.2): match a product entry on the exact product name`.

- [x] **Task 3: Config contract and schema (AC: 6, 7, 8)**
  - [x] 3.1 `src/cvestream/config/models.py`: add the `exact` field, the `model_validator`, the `json_schema_extra` `not` clause and the `_to_product()` mapping (see "Config contract").
  - [x] 3.2 `uv run cvestream config schema`. Check that the diff of `schemas/config-v1.json` only adds `exact` and the `not` clause.
  - [x] 3.3 Tests in `tests/unit/config/test_models.py`, `tests/unit/config/test_provider.py` and `tests/unit/config/test_schema.py` (see "Testing requirements").
  - [x] 3.4 One commit with models, schema and tests: `feat(story-4.2): add exact to product entries in the v1 contract`.

- [x] **Task 4: User documentation (AC: 10)**
  - [x] 4.1 `docs/configuration.md`, `docs/sources.md`, `endpoints.example.yaml` and `CHANGELOG.md` (exact edits in "Documentation edits").
  - [x] 4.2 Check `README.md` and `AGENTS.md`. Neither describes the matching rule today (`grep -n -i "whole word\|match_description\|vendor" README.md AGENTS.md` finds nothing), so no change is expected. Confirm it.
  - [x] 4.3 `uv run cvestream config validate endpoints.example.yaml` must pass.
  - [x] 4.4 Commit: `docs(story-4.2): document exact product matching`.

- [x] **Task 5: Planning docs (AC: 11)**
  - [x] 5.1 Amend `architecture.md`, `project-context.md`, the PRD and `epics.md` (exact edits in "Planning doc amendments").
  - [x] 5.2 Commit: `docs(story-4.2): amend the matching rule in the planning docs`.

- [x] **Task 6: Gates and delivery (AC: 13)**
  - [x] 6.1 Run every gate command of `AGENTS.md`. Behind the corporate proxy, the local `docker build` fails on `ghcr.io` (x509): rely on CI for that gate and say so in the PR.
  - [x] 6.2 Story file: tick the tasks, fill the Dev Agent Record and set Status to `review`. In `sprint-status.yaml`, set `4-2-exact-product-matching: review`. Commit: `chore(story-4.2): mark story ready for review`.
  - [x] 6.3 Push over SSH (`git@github.com:wixyvir/cvestream.git`) and open the PR to `main` (`SSL_CERT_FILE=$HOME/ca.pem gh pr create ...`). Title: `feat(story-4.2): exact product matching`. The body links this story file, contains `Closes wixyvir/cvestream#44`, lists the open questions below with the decision taken, and ends with the attribution line.

## Dev Notes

### Scope boundaries (read first)

- **In scope:** `domain/endpoint.py`, `domain/matcher.py`, `config/models.py`, `schemas/config-v1.json`, their unit tests, one render test, the user docs and the planning docs listed above.
- **Out of scope:**
  - no change to `pipeline/render.py`, `pipeline/run.py`, `domain/publication.py`, the adapters or the state;
  - no vendor + product pairs (rejected in the issue: vendor strings differ across CNAs, for example `MONGODB` / `MongoDB Inc.`, and are `n/a` on older records);
  - no parsing of `X, < Y` GitHub-CNA versions (a separate issue, if wanted);
  - no change of the default to exact (it would break every endpoint relying on `php` matching `php-fpm`);
  - no fuzzy normalization (hyphens, underscores, punctuation, trailing versions in product names).
- **Valkey provider and CLI product commands do not exist yet.** Epic 3 is still `backlog`: there is no `config/valkey_provider.py`, `config/keys.py`, `cli/product.py` or `cli/endpoint.py` on `main`. Nothing to do for them here:
  - Story 3.1 reuses `ConfigDocument`, so `exact` flows through unchanged.
  - Story 3.2 must expose `exact` in `cvestream product add` (for example `--exact`), and its `config export` must not write `match_description: true` next to `exact: true`. Dump with `exclude_unset=True` or `exclude_defaults=True`, otherwise the export does not re-import. This is recorded under "Open questions" for whoever writes 3.2.

### Matcher design (`src/cvestream/domain/matcher.py`)

**Current state** (read the whole file first):
- `_hits(terms, affected)` returns the rows whose `product` **or** `vendor` contains a term as a whole word (`_mentions`, `_pattern`).
- `affected_hits(entry, affected)` returns `_hits` sorted with product-name hits first; it is used only by the render to name the product in the title.
- `_match_entry(entry, affected, descriptions)` runs in this order:
  1. exclusions (structured and descriptions);
  2. hits;
  3. when there is no hit, the description fallback: only when `affected` is empty and `entry.match_description` is set, using `_mentioned_in_prose`;
  4. the version check, which ignores vendor-only hits (`parse_range(hit.version_text) if _mentions(terms, hit.product) else None`).

**Change:** one notion, "this affected product names the entry", decided in one place and used by the three paths (hits, title ordering, version check):

```python
def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _is_named(terms: tuple[str, ...], product: str) -> bool:
    # Exact entries: the whole product name, not a word inside it.
    name = _normalized(product)
    return any(_normalized(term) == name for term in terms)


def _names_product(entry: ProductEntry, terms: tuple[str, ...], product: str) -> bool:
    return _is_named(terms, product) if entry.exact else _mentions(terms, product)


def _entry_hits(
    entry: ProductEntry, terms: tuple[str, ...], affected: list[AffectedProduct]
) -> list[AffectedProduct]:
    if entry.exact:
        # Vendor names never count: vendor `PHP` ships `frankenphp`.
        return [a for a in affected if _is_named(terms, a.product)]
    return _hits(terms, affected)
```

Then:
- `affected_hits()`: `hits = _entry_hits(entry, terms, affected)` and `sorted(hits, key=lambda a: not _names_product(entry, terms, a.product))`. The sort is stable, so for an exact entry the order is the affected order.
- `_match_entry()`:
  - `hits = _entry_hits(entry, terms, affected)`;
  - the fallback guard becomes `if affected or entry.exact or not entry.match_description or not any(_mentioned_in_prose(terms, d) for d in descriptions): return None`;
  - the version list uses `_names_product(entry, terms, hit.product)` instead of `_mentions(terms, hit.product)`.
- Exclusions stay **exactly** as they are (`_hits(entry.exclude, affected)` with the vendor, and the descriptions). The issue says "`exclude` works unchanged", and an exclusion only reduces recall, so a broad check is the safe direction.

Notes:
- Names are free, but keep every helper private (`_`). `match()`, `MatchResult`, `ProductMatch`, `PassThrough` and `affected_hits()`'s signature stay unchanged.
- `@cache` is not needed: the terms are short and few. If you cache, cache a `frozenset` of normalized terms per entry tuple, not per call.
- `str.split()` with no argument splits on every Unicode whitespace run and drops leading and trailing whitespace. `casefold()` is the caseless comparison of the stdlib (`re.IGNORECASE` is what `_pattern` uses; both agree on ASCII). The domain stays stdlib only (`tests/unit/domain/test_purity.py`).
- Config terms are already stripped (`Term` in `config/models.py`). EUVD product names come through `clean_text()` (stripped, placeholders such as `n/a` turned into `None` and dropped), but inner whitespace is not collapsed, hence the normalization of both sides.
- `ProductMatch.structured` is `bool(hits)` today; for an exact match it is always `True`, since the fallback is closed.
- PHP's bundled extensions are filed under product `PHP` (EUVD-2026-87343, `ext/openssl/xp_ssl.c`), so `php` exact still covers them. PECL extensions (MongoDB `PHP Extension`, imagick, redis...) are separate products and need their own entries.
- Description matching brings nothing for such entries: only 0 of 616 php records and 1 of 445 python records had no affected-product data (issue measurement).

### Domain entry (`src/cvestream/domain/endpoint.py`)

Current `ProductEntry`: `name`, `aliases=()`, `exclude=()`, `min_version=None`, `match_description=True`, frozen and slotted. Add as the **last** field:

```python
    # True: the whole affected-product name must equal the name or an alias;
    # vendor names and the description are never used.
    exact: bool = False
```

`test_shorthand_expands_to_domain` and `test_full_product_object_to_domain` compare with `ProductEntry(...)` built without `exact`. They stay green because both sides default to `False`.

### Config contract (`src/cvestream/config/models.py`)

Current state: `_CONFIG = ConfigDict(extra="forbid", strict=True)` is shared by `ProductEntryConfig` and `EndpointConfig`. `ProductEntryConfig` has `name`, `aliases`, `exclude`, `min_version`, `match_description` and a `min_version` `field_validator`. `_to_product()` maps a full entry to `ProductEntry`.

Change (prototyped with the project's pydantic 2.14 on 2026-11-17: the error location is `('endpoints', 0, 'products', 0, 'entry')` and the `not` clause is emitted under `$defs.ProductEntryConfig`):

```python
# JSON Schema twin of `_exact_contradiction`: exact and an explicit
# match_description true cannot both be present.
_EXACT_CONTRADICTION = {
    "not": {
        "required": ["exact", "match_description"],
        "properties": {
            "exact": {"const": True},
            "match_description": {"const": True},
        },
    }
}


class ProductEntryConfig(BaseModel):
    model_config = ConfigDict(**_CONFIG, json_schema_extra=_EXACT_CONTRADICTION)
    ...
    exact: bool = Field(
        default=False,
        description=(
            "Match only an affected product whose whole name equals the name or "
            "an alias (case-insensitive); vendor names and the description are "
            "never used. Cannot be combined with match_description: true."
        ),
    )

    @model_validator(mode="after")
    def _exact_contradiction(self) -> ProductEntryConfig:
        # Only an explicit `match_description: true`: its default is true too.
        if (
            self.exact
            and self.match_description
            and "match_description" in self.model_fields_set
        ):
            msg = (
                "exact: true contradicts match_description: true; "
                "omit match_description or set it to false"
            )
            raise ValueError(msg)
        return self
```

- `_to_product()` full-entry branch: add `exact=item.exact`. Keep `match_description=item.match_description` as it is. The matcher ignores the description for exact entries, so the domain does not need a second rule.
- `ConfigDict` is a `TypedDict`, so `ConfigDict(**_CONFIG, json_schema_extra=...)` keeps `extra="forbid"` and `strict=True`. Do **not** add `json_schema_extra` to the shared `_CONFIG`.
- `json_schema_extra` and `model_fields_set` are first uses in this project, both standard pydantic v2 API, checked with the installed pydantic. If the reviewer prefers no schema-level constraint, drop the `not` clause and say in the field `description` that the combination is rejected. Record the choice in the Dev Agent Record.
- The error message carries no input value, so it can never print a token. `build_snapshot()` strips the `Value error, ` prefix and skips the `entry` tag segment (`_format_location` in `config/provider.py`).
- Additive change: `exact` has a default and is not `required`, and the `not` clause only rejects a combination that could not exist before (no `exact` field). It stays contract v1.

### Testing requirements

Use the existing helpers. In `test_matcher.py` that means `make_vulnerability(products=[(product, vendor, version_text)], description=...)` from `tests/unit/domain/conftest.py`, plus the `_endpoint()` and `_names()` helpers and the `PHP` / `PHP_8_2` constants. Name tests after what they show, as the file does. Domain tests stay pure.

`tests/unit/domain/test_matcher.py`, new section `# --- exact product matching (FR-12, wixyvir/cvestream#44) ---`:
- Constants: `PHP_EXACT = ProductEntry(name="php", exact=True)` and `CPYTHON_EXACT = ProductEntry(name="cpython", aliases=("python/cpython",), exact=True)`.
- `test_exact_matches_the_whole_product_name`, parametrized:
  - `("PHP", "PHP Group", "8.2.* <8.2.35")` with `PHP_EXACT`;
  - `("CPython", "Python Software Foundation", None)` with `CPYTHON_EXACT`;
  - `("python/cpython", None, None)` with `CPYTHON_EXACT` (alias);
  - `("  PHP ", "PHP Group")` (whitespace);
  - `("Apache  HTTP\nServer",)` with `ProductEntry(name="apache http server", exact=True)` (collapsed runs).
  - Each asserts a match with `structured=True`.
- `test_exact_rejects_what_word_matching_lets_through`, parametrized with the issue's misses: `("php-travel-agency-system", "Acme")`, `("CloudClassroom-PHP-Project", "Acme")`, `("PHP Driver", "MongoDB")`, `("frankenphp", "PHP")` (vendor-only hit), and `("Joomla extension", "php-web-design.com")`. For each, assert `match(v, _endpoint(PHP)) is not None` **and** `match(v, _endpoint(PHP_EXACT)) is None`. The vendor is a neutral placeholder where the issue does not name it; only the shapes matter. (Checked on 2026-11-17: each of these rows matches the non-exact `php` on `main` today.)
- `test_exact_never_searches_the_description`: one vulnerability with no products and `description="A flaw in PHP 8.3 allows remote code execution"`, KEV and non-KEV (`kev` parameter). `PHP` matches; `PHP_EXACT` does not, and neither does `ProductEntry(name="php", exact=True, match_description=True)` (the domain does not validate, the matcher must still ignore the description).
- `test_exact_ignores_a_description_mention_when_products_exist`: products `[("WordPress plugin X", "Acme")]` and a description naming PHP give no match for `PHP_EXACT`. This is the current structured-wins rule, pinned for exact too.
- `test_exact_with_min_version`, with `ProductEntry(name="php", min_version=Version("8.2"), exact=True)` and version texts the existing version tests already use:
  - `("PHP", "PHP Group", "8.2.* <8.2.35")` matches, no label;
  - `("PHP", "PHP Group", "7.4.0 <7.4.33")` gives no match;
  - `("PHP", "PHP Group", "8.3.x")` (unparsable) matches with `PassThrough.VERSIONS_UNKNOWN`;
  - a vulnerability with `("PHP", "PHP Group", "7.4.0 <7.4.33")` **and** `("frankenphp", "PHP", "2.0.0a1, < 2.2.0")` gives no match: the frankenphp row is not a hit, so it cannot turn the item into a "versions unknown" match. Assert the contrast too: the non-exact `PHP_8_2` matches the same vulnerability with `VERSIONS_UNKNOWN`, because its vendor-only hit counts as unusable versions.
- `test_exact_vendor_only_vulnerability_is_not_a_versions_unknown_match`: only `("frankenphp", "PHP", "a1b2c3d")`. `PHP_8_2` matches with `VERSIONS_UNKNOWN` (current behaviour, pinned); `ProductEntry(name="php", min_version=Version("8.2"), exact=True)` gives `None`. This is the noise the issue describes.
- `test_exclusions_still_apply_to_exact_entries`, with `ProductEntry(name="php", exclude=("wordpress",), exact=True)`. Each case contains `("PHP", "PHP Group")` and is still dropped. Parametrize where the exclusion term appears:
  - in another row's product name: `("WordPress", "Automattic")`;
  - in another row's vendor: `("Some Plugin", "WordPress")`;
  - in the description: "... a WordPress site ...".
- `test_shorthand_style_entry_stays_word_matching`: `ProductEntry(name="php")` (what the shorthand builds) still matches `("php-fpm", "PHP Group")`. Default `exact=False`.
- `affected_hits` tests (first direct tests of this function; import it from `cvestream.domain.matcher`):
  - `test_affected_hits_of_an_exact_entry`: affected `[("Zend Engine", "PHP"), ("php-fpm", "PHP Group"), ("PHP", "PHP Group")]`. Non-exact `php` returns `[php-fpm, PHP, Zend Engine]` (product-name hits first, vendor-only last). Exact returns `[PHP]` only.
  - `test_affected_hits_of_an_exact_entry_is_empty_without_an_exact_name`.

`tests/unit/domain/test_endpoint.py`: in `test_product_entry_defaults`, add `assert entry.exact is False`.

`tests/unit/pipeline/test_render.py`: `test_the_title_of_an_exact_entry_names_the_exact_product`, built like `test_the_title_uses_the_aliases_of_the_configured_entry`. Use `_with_affected(AffectedProduct("php-fpm", "PHP Group"), AffectedProduct("PHP", "PHP Group"))`. With `endpoint = replace(ENDPOINT, products=(ProductEntry("php", exact=True),))` the title reads `CVE-2026-1234 in PHP Group PHP: severity high`. With `ProductEntry("php")` it reads `... in PHP Group php-fpm: ...` (both asserted).

`tests/unit/config/test_models.py`:
- `test_product_entry_config_to_domain_defaults`: add `assert entry.exact is False`.
- `test_exact_maps_to_the_domain`: `products=[{"name": "php", "exact": True}, {"name": "gitlab"}, "nodejs >= 20"]` gives `[True, False, False]`.
- `test_exact_accepts_match_description_omitted_or_false`: both validate, and map to `ProductEntry(name="php", exact=True)` and `ProductEntry(name="php", exact=True, match_description=False)`.
- `test_rejected`: add two rows with their ids:
  - `exact-string`: `{"name": "a", "exact": "yes"}` at `endpoints.0.products.0.entry.exact`;
  - `exact-with-match-description`: `{"name": "a", "exact": True, "match_description": True}` at `endpoints.0.products.0.entry`.
- `test_exact_contradiction_message`: `_message(...)` contains `exact: true contradicts match_description: true`.

`tests/unit/config/test_provider.py`: `test_exact_contradiction_error_path`, built like `test_shorthand_error_path`. `_errors(...)` equals `("endpoints[0].products[0]: exact: true contradicts match_description: true; omit match_description or set it to false",)`.

`tests/unit/config/test_schema.py`: `test_exact_contradiction_is_published`. `config_schema()["$defs"]["ProductEntryConfig"]["not"]` equals the clause above, and `"exact"` is in its `properties` with `default` `False`. `test_every_property_has_a_description` and `test_committed_schema_is_fresh` cover the rest.

Do not add fixtures: these are hand-built domain objects in the shape of EUVD rows, as story 1.7 did. `tests/fixtures/` stays untouched (fixtures are recorded, never hand-written).

### Documentation edits

- **`src/cvestream/domain/matcher.py` docstring:** after the `match_description` sentence, add in the file's style: "An entry with `exact` true matches only an affected product whose whole name equals its name or an alias: vendor names and the description are never used."
- **`docs/configuration.md`:**
  - Field paragraph ("A product entry has ..."): add `exact` (default `false`). State that `exact: true` with an explicit `match_description: true` is rejected.
  - "How an entry matches": add a paragraph **"Exact entries."**. With `exact: true`, the whole affected-product name must equal the name or an alias (case and whitespace runs ignored; hyphens and other characters count, so list variants as aliases). Vendor names are ignored and the description is never searched. Example: `{name: php, exact: true, min_version: "7.4"}` keeps `PHP Group / PHP` and drops `CloudClassroom-PHP-Project`, `php-travel-agency-system`, `PHP Driver` and vendor `PHP` / product `frankenphp`; `{name: cpython, aliases: [python/cpython], exact: true}` keeps CPython only.
  - Then the notes: PHP's bundled extensions are product `PHP`, so `php` exact covers them; PECL extensions (MongoDB `PHP Extension`, imagick, redis) need their own entries. A vulnerability known only from CISA KEV, or only from a CERT-EU advisory (whose affected products are prose), waits for EUVD to list the product and is then published as new. Exclusion terms keep plain whole-word matching in products, vendors and description.
  - Add an exact entry to the YAML example (for example `- name: cpython` / `aliases: [python/cpython]` / `exact: true`).
- **`docs/sources.md`:**
  - CISA KEV "No affected products" bullet (line ~173): "Entries with `match_description: false` or `exact: true` ignore it".
  - CERT-EU "Matching" bullet (line ~261): add that the prose never equals a product name, so an `exact` entry never matches CERT-EU text and a CERT-EU-only item waits for EUVD data on such an entry.
- **`endpoints.example.yaml`:** in `team-platform`, add a new entry after `apache http server`, commented in the file's style. Do **not** add `exact: true` to the `gitlab` entry, whose explicit `match_description: true` would make the file invalid.

  ```yaml
      - # Optional, default false. true matches only an affected product whose
        # whole name equals the name or an alias (case-insensitive): vendor
        # names and the description are never used. Keeps generic names like
        # php or python from matching php-sdk or python-pillow. Cannot be
        # combined with match_description: true.
        name: cpython
        aliases: [python/cpython]
        exact: true
        min_version: "3.9"
  ```
- **`CHANGELOG.md`:** under `## [Unreleased]`, add `### Added` (or reuse the heading if story 4.1 already added it): "- `exact` product entries: match only an affected product whose whole name equals the name or an alias, ignoring vendor names and the description (wixyvir/cvestream#44)."

### Planning doc amendments (the matching rule changes)

- `architecture.md`:
  - F3 row of the requirements table (line ~41): after "skipped for entries with `match_description: false`", add "or, for entries with `exact: true`, matched on the whole affected-product name only (no vendor, no description)".
  - Valkey key layout (line ~186): `cvestream:v1:endpoint:{id}:products   JSON [{name, aliases[], exclude[], min_version, match_description, exact}]` (the line already lacks `match_description`).
- `project-context.md`, "Matching (FR-12)" (line ~139): add "A product entry with `exact: true` matches only an affected product whose whole name equals its name or an alias (case-insensitive, whitespace collapsed): vendor names and the description are never used; `exact: true` with an explicit `match_description: true` is rejected."
- PRD (`prds/prd-cvestream-2026-10-26/prd.md`):
  - FR-11 (line 57): "... and it can require the whole affected-product name to match (`exact`)".
  - FR-12 (line 58): one sentence on exact entries.
- `epics.md`, requirements inventory:
  - FR-11 (line 34): add "or require the whole product name";
  - FR-12 (line 35): add "an exact entry matches the whole product name only".

  The coverage map (line 100) already lists Story 4.2.

### Existing files this story touches (current state → change → preserve)

| File | Current state | Change | Preserve |
|---|---|---|---|
| `src/cvestream/domain/endpoint.py` | `ProductEntry` with 5 fields | `exact: bool = False` last | positional `ProductEntry(name)`, hashability |
| `src/cvestream/domain/matcher.py` | word matching on product or vendor, description fallback, version check skipping vendor-only hits | entry-aware hit and name helpers; fallback closed for exact | `match()` signature, `MatchResult`, `ProductMatch`, `PassThrough`, every current test |
| `src/cvestream/config/models.py` | `ProductEntryConfig` with `match_description`; shared `_CONFIG` | `exact` field, after-validator, own `ConfigDict` with `json_schema_extra`, `_to_product` mapping | `_CONFIG` itself, strictness, error paths, shorthand |
| `schemas/config-v1.json` | generated | regenerated | never hand-edited |
| `src/cvestream/pipeline/render.py` | `_title_product()` calls `affected_hits(product_entry, affected)` without CERT-EU rows | **none** | — |
| `docs/configuration.md`, `docs/sources.md`, `endpoints.example.yaml`, `CHANGELOG.md` | describe word matching and `match_description` | add `exact` | existing wording |

### Parallel work with story 4.1 (wave 12: `4-1 | 4-2`)

Story 4.1 (`require_score`, wixyvir/cvestream#43) runs in parallel on its own branch. Expect conflicts on these files:
- `src/cvestream/domain/endpoint.py` (4.1 edits `Rules`, this story edits `ProductEntry`);
- `src/cvestream/domain/matcher.py` (4.1 edits `match()`, this story edits `_match_entry()` and `affected_hits()`);
- `src/cvestream/config/models.py` (`EndpointConfig` vs `ProductEntryConfig`);
- `schemas/config-v1.json`;
- `docs/configuration.md`, `endpoints.example.yaml`, `CHANGELOG.md` (both add to `[Unreleased]`);
- the planning docs;
- `tests/unit/domain/test_matcher.py` and `tests/unit/config/test_models.py`.

Whichever PR merges second rebases on `main`, regenerates the schema with `uv run cvestream config schema` (never merge the JSON by hand), and re-runs every gate.

### Architecture compliance

- `domain` stays pure: stdlib only, no new import (`tests/unit/domain/test_purity.py`, import-linter `layers` contract).
- Validation only at the boundary (`config/models.py`); the domain trusts its input and does not re-validate. The matcher closing the description path for `exact` is matching semantics, not validation.
- `config/models.py` is a public contract: the schema is regenerated in the same commit (AGENTS.md).
- Unknown stays `None`. Recall over volume (FR-25) is unchanged for the default; `exact` is an explicit opt-in that trades recall for precision, like `match_description: false`, and is documented as such.
- No change to publication rules, GUIDs or transactions. No new log event.
- Conventional Commits with `story-4.2`, one concern per commit, never squashed. GitHub references in full form (`wixyvir/cvestream#44`).

### Library / framework requirements

No new dependency. pydantic 2.14 (`model_validator`, `model_fields_set`, `ConfigDict(json_schema_extra=...)`), `packaging` (already used by `min_version`), stdlib `str.casefold`. Use `uv run` for everything, never pip or poetry.

### Previous story intelligence (1.7, the closest precedent)

- Story 1.7 added `match_description` with the same shape: domain field last, config field with a `description`, schema regenerated in the same commit, docs in `configuration.md`, `sources.md`, the example file and the matcher docstring. Follow its commit split and test style.
- Its review insisted on pinning trade-offs with tests and documenting them as assumed risks (the slash rule). Do the same here for the CERT-EU-only and KEV-only cases. A test showing that a no-product, PHP-describing vulnerability does not match `php` exact covers the KEV-only case.
- Story 2.1 made CERT-EU affected products structured but prose. The render already excludes them from titles (`_PROSE_SOURCES` in `pipeline/render.py`); the matcher does not. That is why the CERT-EU trade-off exists.

### Git intelligence

- `main` is at `3b67025` ("feat: add Epic 4 for matching and scoring enhancements"), on top of the `v0.1.0` pre-release (`1dbc2ae`), story 3.5 (open-source readiness: README rewrite, `CHANGELOG.md`, `AGENTS.md` renamed from `CLAUDE.md`) and Dependabot bumps (pydantic 2.14.3, ruff 0.17.4, sqlalchemy 2.1.8, import-linter 2.16).
- The last code change to the matcher was story 1.7, followed by story 1.8 (`domain/version.py`). No pending change touches `matcher.py` apart from story 4.1.

### Latest technical information

No external API or library change is involved. The pydantic features used were checked against the installed pydantic 2.14 on 2026-11-17 with a throwaway prototype (outside the repository): a `model_validator(mode="after")` raising `ValueError` inside the discriminated `ProductItem` union reports the location `products.<i>.entry`, `model_fields_set` tells an explicit `match_description: true` from the default, and `json_schema_extra` adds the `not` clause to `$defs.ProductEntryConfig`. The "today" behaviours that the tests contrast were also checked against the current matcher: the five issue rows match non-exact `php`; `PHP_8_2` on `PHP Group/PHP 7.4.0 <7.4.33` plus vendor-only `frankenphp` matches with `versions_unknown`; `affected_hits(php)` orders `[php-fpm, PHP, Zend Engine]`.

### Open questions (decided by default, to confirm in review)

1. **CERT-EU-only and KEV-only vulnerabilities do not match an `exact` entry.**
   - CERT-EU affected products are prose bullets (`PHP 8.5 versions prior to 8.5.12`, see `tests/fixtures/cert_eu/cert_eu_2026_018_multi_cve_revised.json`), so they never equal `php`. A CERT-EU pre-announcement (the PHP2Shell case of Epic 2) therefore does not reach an `exact` `php` entry until EUVD lists the product; it then publishes as `new` (FR-26).
   - **Default taken:** accept, document and pin with a test, as for `match_description: false`.
   - **Alternative:** word-match CERT-EU rows for exact entries. That needs the matcher to see each row's source (today it only gets `AffectedProduct` values) and would be a pattern change to agree first. Workaround for teams that want the early warning: a second, non-exact entry with `match_description: false` and suitable `exclude` terms.
2. **How the contradiction is detected.**
   - **Default taken:** reject only an **explicit** `match_description: true` (via `model_fields_set`), as the issue words it, and publish the rule as a JSON Schema `not` clause. `match_description` keeps its type and default.
   - **Alternative:** make `match_description` `bool | None`. It was rejected because it widens an existing field's schema for third-party tools.
   - **Consequence for story 3.2:** `config export` must not emit the default `match_description: true` next to `exact: true` (dump with `exclude_unset`/`exclude_defaults`), and `product add` should get an `--exact` option.
3. **Normalization is minimal.** Only case and whitespace runs are ignored. `c-python`, `CPython 3`, `php (cli)` or vendor-qualified names such as `python/cpython` are different names and must be listed as aliases. This is deliberate: anything fuzzier brings back the false positives the field removes.
4. **Epic ordering.** `sprint-status.yaml` says an epic starts only when the previous one is done, but Epic 4 (wave 12) is started while Epic 3 is still `backlog`. The parent workflow requested this story; the deviation is noted here, not resolved.
5. **Existing false positives stay.** Turning `exact` on does not remove items already published in the 14-day feed window (no retraction, as in story 1.7). They age out.

### Project Structure Notes

- No new module, file or package. Helpers stay private in `domain/matcher.py` (no `utils.py`).
- Tests go where the tree says: `tests/unit/domain/`, `tests/unit/config/`, `tests/unit/pipeline/`.
- No conflict with the unified structure was detected. `config/valkey_provider.py`, `config/keys.py` and `cli/product.py` appear in the architecture tree but do not exist yet (Epic 3).

### References

- Issue: wixyvir/cvestream#44 (problem, proposal, alternatives, doc notes).
- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.2: Exact product matching]
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.7: Description fallback precision]
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements Overview] (F3 row), [#Data Architecture] (Valkey key layout, config schema), [#Enforcement Guidelines] (contract change)
- [Source: _bmad-output/project-context.md#Critical Don't-Miss Rules] ("Matching (FR-12)", "Recall over volume (FR-25)")
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-11, FR-12)
- [Source: _bmad-output/implementation-artifacts/1-7-description-fallback-precision.md] (precedent for a product-entry flag)
- [Source: src/cvestream/domain/matcher.py], [src/cvestream/domain/endpoint.py], [src/cvestream/config/models.py], [src/cvestream/config/provider.py] (`_format_location`), [src/cvestream/pipeline/render.py] (`_title_product`, `_PROSE_SOURCES`), [src/cvestream/sources/euvd.py] (`_affected`), [src/cvestream/sources/cert_eu.py] (prose `AffectedProduct`)
- [Source: docs/configuration.md#endpoints.yaml], [docs/sources.md] (CISA KEV, CERT-EU), [AGENTS.md] (gate commands)

## Dev Agent Record

### Agent Model Used

Mistral Vibe (Mistral AI coding agent), session of 2026-11-21.

### Debug Log References

- Gates: `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports`, `pytest` (1221 passed, 6 deselected), `cvestream config schema --check`, `cvestream config validate endpoints.example.yaml`, `docker build --target runtime -t cvestream:local .` plus a `docker run --rm cvestream:local --help` smoke test — all green. (A first local docker attempt failed on a transient base-image pull behind the corporate proxy; the retry succeeded, so CI is not needed for that gate.)
- `ty` initially flagged the `json_schema_extra` literal (annotated `_EXACT_CONTRADICTION: dict[str, Any]`) and a `MatchResult | None` union in one test (assigned to a variable with an assert). Both fixed.

### Completion Notes List

- Story context created by the create-story workflow (2026-11-17): complete developer guide built from wixyvir/cvestream#44, the stub story, epics, architecture, project context and the current code.
- Task 2 (commit e440d50): `ProductEntry.exact: bool = False` as the last field; matcher helpers `_normalized`/`_is_named`/`_names_product`/`_entry_hits` decide "this affected product names the entry" in one place, used by hits, title ordering and the version check; description fallback closed for exact entries; exclusions unchanged; docstring updated. 20 new tests (matcher, endpoint, one render title test); all 176 pre-existing matcher/endpoint/render tests pass unchanged (AC 4).
- Task 3 (commit 84b588d): `exact` field after `match_description` in `ProductEntryConfig`, `model_validator(mode="after")` reading `model_fields_set` rejects only an explicit `match_description: true`, `json_schema_extra` publishes the same rule as a `not` clause, `_to_product()` maps `exact`, schema regenerated in the same commit (diff = `exact` property + `not` clause only, AC 8), contract stays v1. Config/provider/schema tests added.
- Task 4 (commit 24af5bb): `docs/configuration.md` (field list, "Exact entries" paragraph, YAML example), `docs/sources.md` (CISA KEV and CERT-EU bullets), `endpoints.example.yaml` (commented cpython exact entry), `CHANGELOG.md` ([Unreleased] Added). README.md and AGENTS.md confirmed free of matching-rule wording. `config validate endpoints.example.yaml` passes.
- Task 5 (commit f0d444d): `architecture.md` F3 row + Valkey products key layout, `project-context.md` Matching (FR-12), PRD FR-11/FR-12, `epics.md` requirements inventory amended.
- Open questions: (1) CERT-EU-only and KEV-only items do not match an exact entry — accepted, documented, pinned by `test_exact_never_searches_the_description`; (2) contradiction detected via `model_fields_set` (explicit `match_description: true` only) and published as a JSON Schema `not` clause — taken; (3) normalization is case + whitespace runs only — taken; (4) Epic 4 started while Epic 3 is backlog — noted, not resolved; (5) no retraction/backfill of already-published items — unchanged.
- Delivery: branch pushed over SSH, PR wixyvir/cvestream#46 opened to `main` ("feat(story-4.2): exact product matching", body links this story file, `Closes wixyvir/cvestream#44`, lists the open questions with the decisions taken).

### File List

- `src/cvestream/domain/endpoint.py` (modified: `exact` field)
- `src/cvestream/domain/matcher.py` (modified: exact helpers, routing, docstring)
- `src/cvestream/config/models.py` (modified: `exact` field, validator, `not` clause, `_to_product`)
- `schemas/config-v1.json` (regenerated)
- `tests/unit/domain/test_matcher.py` (modified: exact matching section)
- `tests/unit/domain/test_endpoint.py` (modified: `exact` default assertion)
- `tests/unit/pipeline/test_render.py` (modified: exact title test)
- `tests/unit/config/test_models.py` (modified: exact mapping, rejection rows, message)
- `tests/unit/config/test_provider.py` (modified: contradiction error path)
- `tests/unit/config/test_schema.py` (modified: published `not` clause)
- `docs/configuration.md` (modified)
- `docs/sources.md` (modified)
- `endpoints.example.yaml` (modified)
- `CHANGELOG.md` (modified)
- `_bmad-output/planning-artifacts/architecture.md` (modified)
- `_bmad-output/project-context.md` (modified)
- `_bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md` (modified)
- `_bmad-output/planning-artifacts/epics.md` (modified)
- `_bmad-output/implementation-artifacts/4-2-exact-product-matching.md` (this file)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status -> review)
