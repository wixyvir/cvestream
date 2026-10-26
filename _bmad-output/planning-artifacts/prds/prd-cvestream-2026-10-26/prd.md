---
title: "PRD: cvestream"
status: final
created: 2026-10-26
updated: 2026-10-26
inputs:
  - planning-artifacts/briefs/brief-cvestream-2026-10-26/brief.md
  - planning-artifacts/briefs/brief-cvestream-2026-10-26/addendum.md
  - Slack #theme-security (digest in addendum.md)
---

# PRD: cvestream

## Summary

cvestream is a small, self-hosted daemon. It pulls vulnerability data from European sources, mainly EUVD and CERT-EU, and adds exploitation status from CISA KEV and ENISA's EU KEV. It then publishes filtered RSS feeds. Each feed has its own product list with optional version and severity rules, so a Slack channel subscribed to it shows only alerts that affect the stack its team runs. RSS is the only output contract, and any RSS reader can use the same feeds.

## Problem

#theme-security receives about 190 alerts a week from cvefeed.io and CERT-FR. [ASSUMPTION: estimate from a 10-day sample] Roughly 10% of them are relevant. Since 2024 the agreed workaround has been to "mute it and check once a day". The noise hides real alerts. During PHP2Shell (Oct 2026), the team first heard of the issue from CERT-EU links posted by hand. The raw feed relayed it hours later, under titles that didn't mention PHP. A single issue also arrives several times, from several sources and through metadata re-posts. Evidence is in `addendum.md`.

## Users

- **Operator:** a single team member who runs the container and maintains the endpoints and product lists.
- **Channel subscribers:** teams with different stacks (e.g. PMSIpilot/BI/PlaniPSY, Cod+, GitLab/infra). Each team reads its channel through Slack's RSS app and never touches the filter configuration.

## Glossary

- **Source record:** one entry fetched from a source (an EUVD record, a CERT-EU advisory, a CERT-FR alerte revision, a KEV entry).
- **Vulnerability:** the unit cvestream tracks. It merges every source record that shares a CVE or EUVD ID.
- **Endpoint (feed):** one RSS feed with its own product list and rules.
- **Feed item:** one entry in an endpoint's feed. It is either the first publication of a vulnerability or an update to one.
- **Product entry:** one line in an endpoint's product list: a name, optional aliases, optional exclusion terms, and an optional minimum version.

## Features and Functional Requirements

### F1. Source ingestion

- **FR-1** Poll EUVD for new and updated source records.
- **FR-2** Ingest CERT-EU advisories.
- **FR-2b** Ingest CERT-FR *alerte* items, CERT-FR's urgent editorial layer (about 14 a year). This source is configurable and off by default. An updated alerte reappears in CERT-FR's feed, so each revision is identified by its reference plus revision number. A revision merges into the existing vulnerability and does not trigger a re-publish by itself.
- **FR-3** Flag a vulnerability as known-exploited when it appears in CISA KEV or ENISA's EU KEV (as exposed by EUVD), and show which list the flag came from. When CISA KEV notes known ransomware use, display that too.
- **FR-4** Configure a poll interval for each source.
- **FR-5** Add further sources, RSS or JSON, through configuration. A configured source must provide at least a stable ID and some text. Any other missing data degrades gracefully: with no CVE ID it is not merged, with no product data it is matched on text only, and with no score or version data those values stay unknown.
- **FR-6** If one source fails, the other sources keep working, and feeds keep serving their last good content.

### F2. Correlation and identity

- **FR-7** Merge source records into one vulnerability using CVE ID and EUVD ID aliases, and keep every contributing source as a reference.
- **FR-8** Identify a vulnerability by its CVE, EUVD or advisory ID, never by its URL. A domain change at a source must not create a duplicate.
- **FR-9** Record which source supplied each value (score, CVSS version, affected products). Show a missing score or missing version data as *unknown*, never as zero or low.
- **FR-24** When sources disagree on the CVSS score, the highest available score drives the CVSS threshold (FR-14), the severity in the title (FR-18) and the re-publish triggers (FR-19). The feed item shows each source's score.

### F3. Endpoints and filtering

- **FR-10** Support multiple endpoints. Each endpoint has its own product list and its own rules.
- **FR-11** A product entry is a vendor/product name, optionally followed by a minimum version (e.g. `php >= 8.2`). It can also list aliases (e.g. `php-src`) and exclusion terms (e.g. `wordpress`).
- **FR-12** Match against structured affected-product data and the description text, never against the title alone. Matching ignores case and works on whole words, so `php` does not match "phpMyAdmin". A vulnerability that mentions an exclusion term does not match that entry.
- **FR-13** When an entry has a minimum version, a vulnerability matches only if its affected versions reach that version or higher. A vulnerability without usable version data matches anyway and is labelled "versions unknown". Version data is unusable when it is missing, "n/a", or free text that can't be parsed, or when the match was found only in the description.
- **FR-14** An endpoint can set a minimum CVSS score. Vulnerabilities with no score are never dropped by this threshold.
- **FR-15** An endpoint can be set to show only known-exploited vulnerabilities.
- **FR-16** A new endpoint starts with the vulnerabilities it would have matched in the last 7 days.
- **FR-25** When filtering rules conflict with volume, recall wins: rules let through what they can't rule out. The operator lowers volume by tightening product entries (see NFR-4).

### F4. RSS output

- **FR-17** Serve each endpoint as a valid RSS 2.0 feed, readable by any RSS client and with no Slack-specific markup. Slack's RSS app is the reference consumer. [ASSUMPTION] Each feed carries the feed items from the last 14 days.
- **FR-18** Each feed item's title names the matched product and the severity. Its body shows the CVE and EUVD IDs, each source's CVSS score with its version, KEV status, affected versions, and links to every source.
- **FR-19** A vulnerability is published once per endpoint. It is published again as an update only when it becomes known-exploited or when its severity band rises (e.g. High → Critical, including unknown → rated). Other changes do not re-publish it. [ASSUMPTION: Slack's RSS app ignores items whose GUID it has already seen] Each update is therefore a new feed item, with its own GUID and a title prefixed `[UPDATE]`.
- **FR-26** A vulnerability that first matches an endpoint only after late data arrives (e.g. product data added later) is published as new on that endpoint.
- **FR-20** Each feed URL contains an unguessable token. A token can be rotated, which invalidates the old URL.

### F5. Configuration

- **FR-21** Configuration is read from one of two providers: a key-value datastore (preferred) or a YAML file.
- **FR-22** CLI subcommands create, list, edit and delete endpoints, product entries, rules and tokens, through whichever provider is active. Editing the YAML file by hand remains supported.
- **FR-23** Configuration changes take effect at the next poll cycle, without restarting the daemon. If the new configuration is invalid, the daemon keeps the last valid one and logs the error.

## Non-Functional Requirements

- **NFR-1 Freshness:** a new source record appears in the matching feeds within 1 hour.
- **NFR-2 Deployment:** ships as a container image. Feeds are served behind HTTPS (a reverse proxy is acceptable).
- **NFR-3 Persistence:** vulnerability state and publication history survive container restarts and upgrades. A restart never re-posts feed items.
- **NFR-4 Operability:** logs record, for each poll, the result for each source and the number of feed items per endpoint. They also show how many items got through only because their score or versions were unknown. A failed source is visible in the logs without reading the feeds.
- **NFR-5 Source resilience:** source APIs are unversioned and change without notice (EUVD has already broken older clients). Each source is isolated behind its own adapter and covered by contract tests. Unexpected payloads are logged as source errors and never crash the daemon.
- **NFR-6 Availability:** best effort. There is no SLA.

## Constraints (user-stated)

- Development happens inside a container. Tooling preferences are listed in the addendum.
- Configuration storage is pluggable (see FR-21). The choice of key-value technology is left to architecture.

## Success Metrics

Trial: one month, one endpoint using the combined stack of all teams (PHP, Node, GitLab, Linux…), running alongside the raw feed in #theme-security.

- **SM-1 Volume:** at least 80% fewer posted items than the raw feed.
- **SM-2 Recall (counter-metric):** no relevant alert missed. The reference set covers every alert the team reacted to or discussed in the raw feed, every alert posted by hand in the channel, and every KEV addition for the team's products.
- **SM-3 Timeliness:** for every alert posted by hand during the trial, the filtered feed had it no later than the hand post.
- **SM-4 Duplicates:** no vulnerability posted twice, except for the updates allowed by FR-19.

## Out of Scope (v1)

- A Slack app or bot, threaded updates, and any filter management from Slack.
- A graphical configuration interface.
- A daily-digest mode (candidate for v2).
- Authentication with headers or user accounts on feeds. The URL token is the only access control.
- NVD as a source. EUVD comes first.
- CERT-FR *avis*: they mostly repeat vendor bulletins already in EUVD.
- Dedicated vendor-bulletin sources. They can be added as configured sources (FR-5).
- Building on CIRCL Vulnerability-Lookup: cvestream stays standalone.

## Open Questions

None blocking. The only open choice, the key-value store technology, belongs to architecture (see Constraints).
