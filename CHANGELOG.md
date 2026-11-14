# Changelog

All notable changes to cvestream are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and cvestream adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
