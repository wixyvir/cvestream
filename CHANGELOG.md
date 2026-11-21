# Changelog

All notable changes to cvestream are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and cvestream adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `require_score` endpoint rule: holds back vulnerabilities that no source has scored yet, except known-exploited ones, until a source scores them (wixyvir/cvestream#43). Additive change to the v1 configuration contract.
- `exact` product entries: match only an affected product whose whole name equals the name or an alias, ignoring vendor names and the description (wixyvir/cvestream#44). Additive change to the v1 configuration contract; `exact: true` together with an explicit `match_description: true` is now rejected, also by the published JSON Schema.
- `group_by_product` endpoint rule (FR-27, wixyvir/cvestream#52): with `true`, 3 or more items of one run that share a matched product and kind (new, or severity raised) are published as one feed item, in near-equal chunks of at most 5 per item, each of at least 3 (6 → 3 + 3, never a trailing 5 + 1); known-exploited vulnerabilities are never grouped. Additive change to the v1 configuration contract. The new nullable `publication.group_guid` column is applied by Alembic migration `0004` at startup; existing rows stay `NULL`, stored groups are never regrouped, and nothing is posted again.
- `held_back` in the `cycle.summary` log: per endpoint with `require_score`, the vulnerabilities the rule held back in the cycle (wixyvir/cvestream#47).

### Changed

- Feed item format (wixyvir/cvestream#51): the body leads with a facts line (severity band, highest CVSS, exploited, published, updated), the matched products and the full description, so what matters fits in the ~495 plain-text characters Slack's RSS app shows; the affected-products line lists each product once with at most 3 version ranges then `(+N more)`; the KEV detail line appears only when the item is known-exploited. Item titles are flagged with emoji (🔥 known-exploited, 🔴 critical, 🔺 severity raised to `medium` or above, 🆕 first published on the UTC day cvestream published the item), built from decision-time values. No GUID changes: no item is posted again; every feed's ETag changes once after deployment. The new nullable `publication.is_fresh` column is applied by Alembic migration `0003` at startup; existing rows stay `NULL` and never show 🆕.
- Product entry names must be unique within an endpoint, ignoring case and whitespace runs; a shorthand counts by its name, so `php` and `php >= 8.2` collide (wixyvir/cvestream#48). This tightens the v1 configuration contract: a file with duplicate names loaded before and is now rejected. `cvestream config validate` exits 2, the daemon refuses to start with it, and a reload keeps the last valid configuration (`config.rejected`); a disabled endpoint (`enabled: false`) is checked too. Before upgrading, rename the second entry (an alias of the first works) and run `cvestream config validate`.

## [0.1.0] - 2026-11-14

First pre-release. The API and configuration format may still change before 1.0.

### Added

- Polling of public vulnerability sources (EUVD, CISA KEV, CERT-EU advisories) on a schedule, with normalization, cross-source correlation (CVE/EUVD aliases) and per-endpoint matching against each team's product list and rules (`aliases`, `exclude`, `min_version`, `match_description`).
- Token-protected RSS 2.0 feeds per endpoint on `:8080`, health endpoint on `:9090`. Publish-once semantics: an item is re-posted only when it becomes known-exploited or its severity band rises. SQLite state survives restarts without re-posting.
- YAML configuration (`endpoints.yaml`, `settings.yaml`) with a published JSON Schema contract (`schemas/config-v1.json`), validated by `cvestream config validate` and `cvestream config schema --check`.
- CLI (`cvestream run`, `cvestream poll --once`, `cvestream db migrate`, `cvestream config ...`) and a runtime Docker image published to GHCR.
- Apache-2.0 license.
- Security policy (`SECURITY.md`) with GitHub private vulnerability reporting.
- Contributing guide (`CONTRIBUTING.md`).
- Code of conduct (`CODE_OF_CONDUCT.md`, Contributor Covenant 2.1).
- Issue templates (bug report, feature request, new vulnerability source).
- Pull request template.
- `AGENTS.md` (renamed from `CLAUDE.md`): rules for AI agents working on the project.
- This changelog.
