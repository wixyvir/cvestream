# Addendum: cvestream PRD

Supporting detail for downstream work (architecture, solution design), and material that does not fit the PRD body.

## Technical preferences (for architecture)

- **Language/tooling:** Python, managed with `uv`.
- **Developer experience:** hot reloading during development.
- **Runtime:** delivered and hosted as a container image.
- **Development environment:** all development must happen inside a container (dev container / compose-based dev loop).
- **Configuration provider:** pluggable. Preferred backend is a key-value datastore; a YAML file provider is an acceptable alternative. The exact KV technology is not yet chosen.

## Evidence: Slack #theme-security (read 2026-10-26)

Read-only digest of the last ~10 days (~300 messages) plus keyword search over history since 2024.

- **Problem statement, 2024-07:** an infra/dev lead asked whether notifications could be filtered; the channel owner answered no, so the workaround is "mute it and check once a day".
- **Current setup:** one Slack RSS app integration posts two feeds: cvefeed.io "Latest High/Critical" (~85–90% of posts) and CERT-FR *avis* (not *alerte*). No EUVD, no KEV. CERT-EU links are posted by hand.
- **Wanted but missing:** CERT-EU, vendor bulletins, KEV.
- **Volume:** ~25–30 items/day (peak 70), ~190/week, in bursts of up to 25 per poll.
- **Signal:** an estimated ~10% of items are relevant overall. CERT-FR avis ~30–40% relevant to a PHP/Node/GitLab/Linux stack; cvefeed <5% (WordPress plugins, IoT, obscure AI tools, "[Product Name]" placeholder titles).
- **Item formats:** CERT-FR titles name the product ("Multiples vulnérabilités dans GitLab (22 octobre 2026)") but carry no CVE/CVSS/version. cvefeed titles often omit the product entirely.
- **Duplication:** the same issue arrives via CERT-EU, CERT-FR and cvefeed. cvefeed re-posts a CVE when its metadata changes (10–23 duplicate titles per 100 messages). The CERT-FR domain moved (cert.ssi.gouv.fr → cert.cyber.gouv.fr) between Oct 23 and Oct 26, so the same advisory appears under two URLs. Stable item identity must therefore key on the advisory or CVE ID, never the URL.
- **PHP2Shell, Oct 2026 (CVE-2026-109618/109619, CVSS 9.2):** the team learned of it from CERT-EU and php.net links posted by hand, hours before cvefeed relayed it. cvefeed titles didn't mention PHP, so a title-only match would have missed it.
- **Behaviour:** reactions and threads appear only on items touching the team's stack (GitLab advisory thread: 9 replies; PHP2Shell remediation thread: 11). Teams post per-product exposure notes (PMSIpilot/BI/PlaniPSY, Cod+), which suggests one feed per team.

## Source research (2026-10-26, live checks)

### EUVD (https://euvd.enisa.europa.eu/apidoc)

- **API:** base `https://euvdservices.enisa.europa.eu`, GET-only, no auth, no observed rate limit.
- **Ingestion endpoint:** `/api/search`. Page size is 100 max and `page` starts at 0. Use `fromUpdatedDate` for incremental polling. The `last*`, `exploited*` and `critical*` list endpoints return at most 8 records, so they are useless for ingestion.
- **Other endpoints:** `/api/enisaid?id=` resolves an EUVD or CVE ID. `/api/advisory?id=` returns one advisory.
- **Bulk files:** `/api/dump/cve-euvd-mapping` (CSV, daily) and `/api/kev/dump` (JSON, daily, `sources` = `cisa_kev` | `eukev_kev`). There is no RSS and no full-data dump.
- **Record fields:** `id`, `description`, `datePublished`, `dateUpdated`, `baseScore`, `baseScoreVersion`, `baseScoreVector`, `aliases`, `references` (both newline-separated strings), `assigner`, `epss` (×100), `exploitedSince`, `enisaIdVendor[]`, `enisaIdProduct[{product.name, product_version}]`.
- **Freshness:** products come from the CNA's CVE record, so they are available before NVD enrichment. In one sample, EUVD had product and version data while NVD still showed "Received".
- **Data quality:**
  - Dates are non-ISO with no timezone ("Oct 26, 2026, 2:45:45 PM").
  - Version strings are free text.
  - In a sample of 100, about 14% had no product (or "n/a") and about 13% had no score.
  - VulnCheck (May 2025) reported missing CVEs and duplicate EUVD IDs.
- **Stability:** the API has no version in its path and has already broken earlier clients (old `/api/vulnerabilities` paths now return 404). This motivates the source-adapter and contract-test requirement.
- **Exploited signal:** `exploited=true` gives 1772 records, the same set as the KEV dump. Every CISA KEV entry is included, plus 15 found only in EU KEV. Fetch the CISA KEV JSON only for `knownRansomwareCampaignUse`, `dueDate` and `requiredAction`.

### CERT-FR

- **Feeds:** `alerte` RSS has about 14 items per year and is not chronological, because updated old alertes reappear; dedupe on reference + revision. `avis` RSS has about 6 items per working day.
- **Per-advisory JSON:** `/{avis|alerte}/<REF>/json/` with `cves[]`, `affected_systems[]` (French free-text versions), `revisions[]`, and `closed_at` for alertes.
- **What EUVD alone would miss:** the editorial "this is urgent" layer of the *alerte* feed, and French summaries. *Avis* mostly repeat vendor bulletins that EUVD already has.

### Other European sources

- **CERT-EU:** RSS of the latest 10 advisories, plus JSON per advisory. Low volume, critical issues only.
- **BSI CERT-Bund WID:** RSS (high volume, German) and CSAF 2.0. Already included in EUVD.
- **NCSC-NL:** RSS (Dutch) and CSAF 2.0.
- **INCIBE-CERT:** vulnerabilities RSS. INCIBE is the main EU CNA.
- **CIRCL Vulnerability-Lookup:** the AGPL engine underneath EUVD, which can be self-hosted. It has an API, RSS, KEV catalogs and product watchlists (email). Whether its RSS can be filtered per product is UNVERIFIED. Considered and rejected by the user (2026-10-26): cvestream stays standalone.

### Comparable tools and the gap

- **OpenCVE:** self-hosted or SaaS, CPE subscriptions, Slack and webhooks. No version ranges, no EUVD.
- **cvefeed.io:** SaaS; filtered RSS and Slack only on paid plans.
- **Vulmon:** SaaS watchlists.
- **stack.watch:** SaaS, per-product RSS from $365/yr.

**The gap:** a free, self-hosted tool that uses EU data (EUVD plus EU and CISA KEV) and matches against CNA version ranges, so it doesn't wait for NVD's CPE backlog, and offers one plain RSS feed per channel.
