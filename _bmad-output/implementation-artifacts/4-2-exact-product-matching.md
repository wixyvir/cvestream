---
baseline_commit: HEAD
---

# Story 4.2: Exact product matching

Status: backlog

<!-- Note: This story was created from GitHub issue wixyvir/cvestream#44 -->

## Story

As a channel subscriber,
I want an optional product-entry field to require the whole affected-product name to match,
so that short, generic names like `php` or `python` do not pull in many unrelated products whose names or vendors contain them.

This addresses the real data problem: every EUVD record updated since 2026-08-01 that mentions `php` or `python` results in many false positives:
- `php` with `min_version: "7.4"` and `match_description: false`: **75 matches, only 13 of them are `PHP Group / PHP`**
- `python` (alias `cpython`, `min_version: "3.9"`): **38 matches, about 3 of them CPython**

The only workaround today is a growing `exclude` list, which is whack-a-mole.

## TL;DR

- **New product-entry field**: `exact: bool` (default `false`) in `domain/endpoint.ProductEntry`
- **Behavior**: When `true`, a hit requires the **whole affected-product name** to equal the name or one of the aliases (case-insensitive, whitespace collapsed)
- **Constraints**: Vendor names are ignored; description is never searched; `exact: true` with `match_description: true` is rejected as contradictory
- **Compatibility**: `min_version` and `exclude` work unchanged; `affected_hits()` follows the same rule

## Acceptance Criteria

1. **New field.** `domain/endpoint.ProductEntry` gains `exact: bool = False`.
2. **Exact matching.** In `domain/matcher._match_entry` and `affected_hits`:
   - when `exact` is `True`, the affected-product name must equal (case-insensitive, whitespace collapsed) the entry name or one of its aliases
   - vendor names are ignored
   - the description is never searched
3. **Validation.** `exact: true` with `match_description: true` is rejected as contradictory at the config boundary.
4. **Shorthand preservation.** The string shorthand form (`php >= 8.2`) stays word matching, unaffected by this change.
5. **Config contract.** `config/models.ProductEntryConfig.exact` is a bool (default `False`) with a validator. Regenerate `schemas/config-v1.json` in the same commit.
6. **Tests.** Built on the EUVD shapes from the issue:
   - Hits: `PHP Group/PHP`, `Python Software Foundation/CPython`
   - Misses: `php-travel-agency-system`, `CloudClassroom-PHP-Project`, `PHP Driver`, vendor `PHP`/product `frankenphp`, description-only
7. **Documentation.** `docs/configuration.md` ("How an entry matches"), `endpoints.example.yaml`, `CHANGELOG.md`.

## Tasks / Subtasks

- [ ] **Task 1: Domain field** - Add `exact` to `domain/endpoint.ProductEntry`
- [ ] **Task 2: Matcher logic** - Implement exact matching in `domain/matcher._match_entry` and `affected_hits`
- [ ] **Task 3: Config contract** - Add field to `config/models.ProductEntryConfig`, add validator, regenerate schema
- [ ] **Task 4: Tests** - Add matcher tests with EUVD shapes from the issue
- [ ] **Task 5: Documentation** - Update docs/configuration.md, endpoints.example.yaml, CHANGELOG.md
- [ ] **Task 6: Gates** - Run all gate commands from project-context.md

## Dev Notes

### Touch Points

- `domain/endpoint.ProductEntry.exact`
- `domain/matcher._match_entry`
- `domain/matcher.affected_hits`
- `config/models.ProductEntryConfig.exact`
- `config/models` validator for contradictory flags
- `schemas/config-v1.json` (regenerate)
- Tests in `tests/unit/domain/test_matcher.py`
- Tests in `tests/unit/config/test_models.py`
- `docs/configuration.md`
- `endpoints.example.yaml`
- `CHANGELOG.md`

### Related Issues

- GitHub Issue: wixyvir/cvestream#44
- Related FR: FR-12

### Dependencies

- Story 1-2 (vulnerability model and matching engine)
- Story 1-3 (endpoint configuration in YAML)

### Notes

- PHP's bundled extensions are filed under product `PHP` (e.g. EUVD-2026-87343, `ext/openssl/xp_ssl.c`), so `php` exact still covers them
- PECL extensions (MongoDB `PHP Extension`, imagick, redis...) are separate products and need their own entries
- Description matching brings nothing for such entries: only 0 of 616 php records and 1 of 445 python records had no affected-product data
