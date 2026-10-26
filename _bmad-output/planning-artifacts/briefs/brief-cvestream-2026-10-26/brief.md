---
title: "Product Brief: cvestream"
status: final
created: 2026-10-26
updated: 2026-10-26
---

# Product Brief: cvestream

## Executive Summary

cvestream is a team-focused CLI daemon that combines the CERT-FR alert feed with structured CVE data and CISA KEV exploitation status, then serves filtered RSS feeds for Slack channels. Each output endpoint has its own product list, so teams receive relevant alerts without managing filters in Slack. The first structured CVE source, NVD or EUVD, remains to be chosen after checking product coverage and data quality.

## The Problem

The current Slack channel receives all alerts from the CERT-FR feed. Team members must scan items that may not concern the products they use. As the team follows more products, channels, or RSS sources, a single unfiltered feed creates noise and makes relevant security issues harder to spot.

## The Solution

Consume configured security sources, including RSS feeds and structured JSON APIs. Keep CERT-FR as a curated input, supplement it with structured affected-product, version, and CVSS data, and use CISA KEV to indicate known exploitation. Correlate records by CVE ID where possible and retain source attribution. Apply each endpoint's product list and publish a filtered RSS feed for its Slack channel.

The feed URL supplied for the first use case is <https://cert.cyber.gouv.fr/alerte/feed/>. Product-name matching is an acceptable starting point, with structured vendor/product/version identifiers used when available. Preserve score source and CVSS version; a missing score must remain unknown rather than be treated as low severity.

## Who This Serves

The primary users are team members who need to monitor security issues affecting the products their teams use. A team member configures which products matter to a channel; channel subscribers receive filtered alerts through Slack's RSS subscription.

## What Makes This Different

The value is a small, self-hostable bridge between existing security RSS sources and team-specific subscriptions. It preserves RSS as the integration contract, so Slack does not need product-filtering logic and other RSS-capable consumers could use the same endpoints.

## Success Criteria

- A team member can configure more than one input RSS source and more than one filtered output, with a separate product list for each output.
- Each output endpoint includes alerts that match its configured products and excludes unrelated alerts, using structured product/version data where available and agreed fallback matching rules.
- For matched CVEs, the output includes CVSS details and known-exploitation status when available, and identifies each data source.
- Slack can subscribe to an output endpoint and receive newly published matching alerts without manual forwarding.
- The team reports less irrelevant alert noise while still seeing the security issues that matter to them.

**Open:** Set measurable thresholds for alert freshness, duplicate handling, endpoint availability, and missed or incorrectly included matches after choosing the deployment model and testing against representative feed items.

## Scope

**First version:** A command-line daemon; ingestion of CERT-FR and at least one structured CVE JSON/API source; CISA KEV enrichment; correlation by CVE ID where possible; multiple independently filtered RSS output endpoints with per-endpoint product lists; endpoints consumable by Slack's RSS subscription.

**Out of scope for the first version:** A Slack app or bot that stores or applies filters; a graphical configuration interface. Whether configuration is kept in a CSV file or a key/value datastore is undecided and belongs in implementation planning.

## Vision

If it succeeds, cvestream becomes a dependable team service for turning security feeds into focused subscriptions. Teams can maintain distinct views for different channels and products, while continuing to use familiar RSS readers and integrations.

## Assumptions and Open Questions

- **[ASSUMPTION] Hosting:** The daemon will run somewhere reachable by Slack and the team, likely a shared team host. The actual deployment environment is undecided.
- **Structured source:** NVD and EUVD are candidates for the first structured CVE API. Choose after checking representative products for coverage, field quality, and update behavior.
- **Matching:** Product-name matching is acceptable as a starting point. Rules for aliases, vendor names, versions, ambiguous names, and identifier mapping remain open.
- **Source coverage:** CERT-FR remains in scope as a curated source. NVD/EUVD structured records and CISA KEV supplement its coverage; they do not imply identical alert selection or timing.
- **Data quality:** CVSS or affected-product data may be absent or delayed. Preserve provenance and unknown values; do not treat missing scores as zero or as evidence that an alert is unimportant.
- **Feed compatibility:** Support RSS sources and the selected JSON/API format; exact source-specific ingestion and correlation behavior need technical validation.
- **Configuration:** CSV and a key/value datastore are candidate approaches. The simplest maintainable choice for the team's operating model remains open.
- **Operations:** Feed refresh interval, endpoint authentication/exposure, logging, and failure behavior remain to be defined.
