---
baseline_commit: HEAD
---

# Story 4.1: require_score endpoint rule

Status: backlog

<!-- Note: This story was created from GitHub issue wixyvir/cvestream#43 -->

## Story

As a channel subscriber,
I want an optional endpoint rule to drop vulnerabilities without any score from any source,
so that my busy endpoint does not receive many "severity unknown" items that I cannot triage yet, while still keeping exploited vulnerabilities even without a score.

This addresses the real case: a BI platform endpoint with `min_cvss: 4.0` still gets every fresh OpenSSL record before EUVD scores it (e.g. EUVD-2026-126927, EUVD-2026-126928, EUVD-2026-126929, all "severity unknown" on the day they were published).

## TL;DR

- **New endpoint rule**: `require_score: bool` (default `false`) in `domain/endpoint.Rules`
- **Behavior**: When `true`, a vulnerability without any score from any source is dropped
- **Exception**: KEV (known-exploited) vulnerabilities are **never** silenced for lack of a score
- **Independence**: Works with or without `min_cvss` threshold
- **Safety**: Dropped vulnerabilities are matched again when their records change, and with no publication history they publish as new once a source scores them

## Acceptance Criteria

1. **New rule field.** `domain/endpoint.Rules` gains `require_score: bool = False`.
2. **Drop unscored, keep KEV.** In `domain/matcher.match()`, when `require_score` is `True`:
   - compute the score once from all sources
   - drop the vulnerability before the `min_cvss` check if the score is `None`
   - **except** when the vulnerability is known-exploited (CISA KEV or EU KEV): it keeps the "score unknown" label
3. **No dependency on min_cvss.** The rule works with or without a `min_cvss` threshold.
4. **Re-publication.** A vulnerability dropped due to `require_score` is matched again when its records change. With no publication history, `domain/publication.decide()` publishes it as `new` once a source scores it at or above `min_cvss`.
5. **Config contract.** `config/models.EndpointConfig.require_score` is a strict bool (default `False`). `to_domain()` maps it to the domain rule. Regenerate `schemas/config-v1.json` in the same commit.
6. **Tests.**
   - Matcher tests: unscored dropped with and without `min_cvss`, unscored KEV kept, `[None, 9.8]` kept, scored unaffected
   - Config tests: strict bool validation
   - Pipeline tests: unscored then scored: published once, as new
7. **Documentation.** `docs/configuration.md` (table and threshold paragraph), `endpoints.example.yaml`, `CHANGELOG.md`, `architecture.md` (FR-25 now has an opt-out).

## Tasks / Subtasks

- [ ] **Task 1: Domain rule** - Add `require_score` to `domain/endpoint.Rules`
- [ ] **Task 2: Matcher logic** - Implement drop logic in `domain/matcher.match()`
- [ ] **Task 3: Config contract** - Add field to `config/models.EndpointConfig`, regenerate schema
- [ ] **Task 4: Tests** - Add matcher, config, and pipeline tests
- [ ] **Task 5: Documentation** - Update docs/configuration.md, endpoints.example.yaml, CHANGELOG.md
- [ ] **Task 6: Gates** - Run all gate commands from project-context.md

## Dev Notes

### Touch Points

- `domain/endpoint.Rules.require_score`
- `domain/matcher.match()` (compute score once, drop before `min_cvss` check)
- `config/models.EndpointConfig.require_score`
- `config/models.to_domain()`
- `schemas/config-v1.json` (regenerate)
- Tests in `tests/unit/domain/test_matcher.py`
- Tests in `tests/unit/config/test_models.py`
- `docs/configuration.md`
- `endpoints.example.yaml`
- `CHANGELOG.md`
- `architecture.md`

### Related Issues

- GitHub Issue: wixyvir/cvestream#43
- Related FR: FR-14, FR-25

### Dependencies

- Story 1-2 (vulnerability model and matching engine)
- Story 1-3 (endpoint configuration in YAML)
