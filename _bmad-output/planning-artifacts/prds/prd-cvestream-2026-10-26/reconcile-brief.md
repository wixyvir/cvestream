# Input Reconciliation: Product Brief → PRD (cvestream)

Input: `planning-artifacts/briefs/brief-cvestream-2026-10-26/` (`brief.md`, `addendum.md`, `.decision-log.md`)
Checked against: `prd.md`, `addendum.md`, `.decision-log.md` in this folder.
Date: 2026-10-26

## TL;DR

- The PRD carries most of the brief's functional intent (multi-endpoint filtering, provenance, unknown-score handling, KEV, logging, poll interval, source isolation). The three deliberate supersessions (EUVD-first, CERT-FR *alerte* optional, pluggable KV/YAML config) are logged and mostly reflected clearly. Two small clarity issues are listed in section 3.
- **Most important gaps:**
  1. **RSS as a consumer-agnostic contract** ("other RSS-capable consumers could use the same endpoints"; "familiar RSS readers") is narrowed to "one feed per Slack channel" and "Slack's RSS app" (Summary, FR-17).
  2. **False positives / ambiguous names** (an open question in the brief) were dropped. FR-12 now matches description text too, which makes the problem worse (e.g. "PHP" in WordPress-plugin CVEs). The only success metric for precision is the volume proxy.
  3. **Delayed enrichment** ("absent or delayed") is only half handled. FR-19 doesn't say whether *unknown → scored* or *no product data → matching product* re-publishes or first-publishes an item.
  4. **Failure behaviour and operations** are partial. Nothing requires item state to persist across restarts, even though FR-16 and FR-19 depend on it. There is no availability target (the brief's open "endpoint availability" threshold), and subscribers can't see that a feed is stale.
  5. **Provenance "disagreements"** (brief addendum) are dropped. FR-18 shows a single CVSS score/source, with no rule for conflicting values.
- Smaller items: vendor CSAF sources, an unlogged narrowing of "team member configures" to a "single operator", the PRD body has no value-proposition or vision section, Slack reachability (public exposure) is only implicit, NVD's status isn't stated, and the publication/update times to retain aren't listed.

---

## 1. Gaps: dropped or contradicted brief intent

### G1. RSS as a consumer-agnostic integration contract (HIGH)

- **Brief:** "It preserves RSS as the integration contract, so Slack does not need product-filtering logic **and other RSS-capable consumers could use the same endpoints**." (What Makes This Different). Also: "while continuing to use **familiar RSS readers and integrations**." (Vision)
- **PRD status:** Partially dropped. The Summary says "one filtered RSS feed **per Slack channel**". FR-17 says "valid RSS 2.0 feed that **Slack's RSS app** can subscribe to". "RSS is the only output contract" survives, but the reason for it (any RSS consumer works) doesn't. Nothing stops FR-18 item formatting from being tuned to Slack's renderer.
- **Suggested fix:** In the Summary, say "one filtered RSS feed per endpoint (typically one per Slack channel)". Extend FR-17: "standards-compliant RSS 2.0, with no Slack-specific markup; Slack's RSS app is the reference consumer, and the feed must also render in a generic feed reader". Optionally add an acceptance check against one non-Slack reader.

### G2. False positives and ambiguous product names (HIGH)

- **Brief:** "Rules for aliases, vendor names, versions, **ambiguous names**, and identifier mapping remain open." (Assumptions). Addendum: "Alias handling, **false positives**, vendor names, versions, and identifier mapping remain to be defined." Success criterion: "...includes alerts that match its configured products **and excludes unrelated alerts**". Open thresholds: "missed **or incorrectly included** matches".
- **PRD status:** Dropped. Open Questions keeps only "Product aliases". FR-11/FR-13 settle versions. False positives and ambiguous names are gone. FR-12 adds description-text matching, which raises false-positive risk. For example, many WordPress-plugin CVEs say "PHP Object Injection", and those are exactly the cvefeed noise the Slack evidence complains about. Short names ("Go", "Node", "Git" vs "GitLab") collide too. Success Metrics measure recall precisely but precision only through the "≥80% fewer items" volume proxy.
- **Suggested fix:** Add an Open Question on false positives and ambiguous names. Define the matching precedence in FR-12: structured product data wins, description text is a fallback only when there is no structured data, matching uses word boundaries and/or the vendor name. Optionally add per-entry exclusion terms. Add a precision metric next to recall, e.g. "≤ N% of posted items judged irrelevant during the one-month trial".

### G3. Delayed enrichment: what happens when data arrives later (HIGH)

- **Brief:** "CVSS or affected-product data may be **absent or delayed**. Preserve provenance and unknown values; do not treat missing scores as zero..." (Assumptions). Addendum: "**Missing or delayed enrichment** must remain unknown..."
- **PRD status:** "Absent" is covered (FR-9, FR-13 fail-open, FR-14 never drops unscored items). "Delayed" isn't specified:
  - FR-19 re-publishes only when "its CVSS severity band rises (e.g. High → Critical)". It doesn't say whether **unknown → High/Critical** counts as a rise. That is the common case for EUVD records that start without a score (about 13% in the sample).
  - A record with no product data (about 14% "n/a") may first match only once products arrive later. The PRD doesn't say whether that is a first publication (and whether the 1-hour freshness clock starts at that update).
  - An item published fail-open as "versions unknown" may later get version data showing the team isn't affected. Is that silently kept, or marked?
- **Suggested fix:** Extend FR-19: "unknown → any scored band counts as a band rise when the new band is ≥ the endpoint's threshold (or ≥ High)". Add an FR saying that records are re-evaluated against every endpoint when they are updated, and that an item matching for the first time after an update is published as new. State that items are never retracted, or define a "no longer applies" update.

### G4. Failure behaviour: state persistence, staleness, availability (MEDIUM-HIGH)

- **Brief:** "**Operations:** Feed refresh interval, endpoint authentication/exposure, logging, and **failure behavior** remain to be defined." Open: "Set measurable thresholds for alert freshness, duplicate handling, **endpoint availability**, and missed or incorrectly included matches..." Vision: "cvestream becomes a **dependable** team service".
- **PRD status:** Partial.
  - Covered: refresh (FR-4, 1h freshness), logging (Operability), source failure isolation (FR-6, Source resilience).
  - **Not covered:** persistence of item state (published GUIDs, last severity band and KEV state, the 7-day backfill window) across daemon restarts and container redeploys. Without it, FR-16, FR-19 and the "no duplicates" metric break on every restart. The KV store is defined only as a *configuration* provider.
  - **Not covered:** an endpoint availability target. The brief named it explicitly; the PRD sets freshness and duplicates but not availability.
  - **Not covered:** subscriber-visible staleness. FR-6 serves "last good content", so a source that is down for days looks like "no alerts" in Slack. Only the operator's logs show it.
  - **Not covered:** behaviour on invalid configuration under FR-23 hot reload (keep the last valid config? log? refuse?).
  - The tension isn't resolved: the decision log records "Stakes: hobby/solo", while the brief's vision is a "dependable team service" serving several teams (PRD Users).
- **Suggested fix:** Add an FR saying that item state persists across restarts and that a restart never re-publishes items. Add an Availability NFR, even a modest one (e.g. "best effort, single host; feeds survive restarts with no duplicate or missing items"). Add a staleness signal: a health or status endpoint, or a source-age log warning after N missed polls. Add to FR-23 that invalid config is rejected and the last valid config is kept, with an error logged. State the dependability expectation that follows from the hobby/solo stakes.

### G5. Provenance disagreements between sources (MEDIUM)

- **Brief addendum:** "Correlate by CVE ID and **preserve source provenance and disagreements**."
- **PRD status:** Provenance is kept (FR-7, FR-9). Disagreements aren't: FR-18 shows "the CVSS score with its version and source" (singular), and no rule says which source wins when, for example, EUVD and CERT-EU report different scores or versions. FR-14 thresholds and FR-19 band changes depend on that choice.
- **Suggested fix:** Extend FR-9/FR-18: when sources disagree, show every value with its source, or define a precedence order and display the alternatives. Say which value drives FR-14 and FR-19 (e.g. the highest, for fail-safe filtering).

### G6. Who configures: "a team member" vs a "single operator" (LOW-MEDIUM)

- **Brief:** "A team member configures which products matter to a channel" (Who This Serves). Vision: "**Teams can maintain distinct views** for different channels and products".
- **PRD status:** Narrowed without a log entry. Users says "Operator: **a single team member** who runs the container and maintains the endpoints and product lists". Channel teams "never touch the filter configuration". This may be intended (hobby/solo stakes), but the PRD decision log doesn't record it, and it contradicts the vision wording.
- **Suggested fix:** Either log the decision (single operator in v1; per-team self-service is a later possibility) or loosen Users to "one or more operators".

### G7. Value proposition and vision missing from the PRD body (LOW-MEDIUM)

- **Brief:** "The value is a **small, self-hostable bridge** between existing security RSS sources and team-specific subscriptions." Vision: "a dependable team service for turning security feeds into focused subscriptions".
- **PRD status:** The Summary keeps "small, self-hosted". The differentiation ("The gap": free, self-hosted, EU data, CNA version ranges, plain RSS) and the vision appear only in the addendum (Comparable tools). The PRD body has no "why this, not OpenCVE/cvefeed paid" line and no vision.
- **Suggested fix:** Add one or two sentences to the Summary, or a short "Why / Vision" section, carrying the gap statement and the vision. This keeps the qualitative intent in the main document that downstream readers use.

### G8. Exposure: Slack must reach the endpoint (LOW-MEDIUM)

- **Brief:** "[ASSUMPTION] Hosting: The daemon will run somewhere **reachable by Slack and the team**". Ops: "endpoint authentication/**exposure**".
- **PRD status:** Implicit. Deployment says "served behind HTTPS (a reverse proxy is acceptable)". FR-20 covers tokens. Nothing says the feed endpoints must be reachable from Slack's servers over the public internet, while the admin CLI and config store are not exposed. Slack's own RSS polling delay isn't acknowledged either: the 1h freshness is measured at the feed, not in the channel.
- **Suggested fix:** Extend the Deployment NFR: "feed endpoints must be publicly reachable over HTTPS by Slack's RSS fetcher; only the feed paths are exposed; configuration and CLI are not network-exposed". Add a note that end-to-end latency to Slack also depends on Slack's polling interval.

### G9. Vendor CSAF advisories silently dropped (LOW)

- **Brief addendum:** "**Vendor CSAF advisories:** Consider as an additional source where available."
- **PRD status:** Not in the FRs, Out of Scope or Open Questions. The PRD addendum lists "vendor bulletins" under "Wanted but missing" (Slack evidence) and mentions CSAF only for BSI and NCSC-NL. FR-5 ("RSS or JSON") doesn't clearly cover CSAF.
- **Suggested fix:** Either add "Vendor CSAF advisories" to Out of Scope (v1) or the Open Questions, or say that FR-5's JSON sources may include CSAF feeds. The Slack evidence shows users want vendor bulletins, so an explicit deferral is better than silence.

### G10. Normalized record fields: publication/update times (LOW)

- **Brief addendum:** "For normalized records, retain CVE ID, source, affected vendor/product/version, CVSS version/score/vector when present, exploitation flags, **publication/update times**, and original advisory references."
- **PRD status:** Mostly covered by FR-18 and FR-9. Publication and update times, and the CVSS vector, aren't listed. The RSS `pubDate` and the "updated" marking in FR-19 depend on them, and EUVD dates have no timezone (PRD addendum).
- **Suggested fix:** Add to FR-18 "publication and last-update dates (normalized to UTC); CVSS vector when present".

---

## 2. Generic RSS sources: matching and correlation without structure (LOW)

- **Brief:** Success criterion: "configure **more than one input RSS source**". Feed compatibility: "Support RSS sources and the selected JSON/API format; exact source-specific ingestion and correlation behavior need technical validation."
- **PRD status:** FR-5 allows extra RSS sources through configuration. Nothing says how an arbitrary RSS item without a CVE ID or structured products is identified (FR-8 assumes an advisory or CVE ID), correlated (FR-7) or matched (FR-12).
- **Suggested fix:** Add to FR-5: generic RSS items are matched on title and description text, identified by `guid` (or advisory ID if one can be extracted), and correlated when a CVE ID can be extracted from the text. Alternatively, limit FR-5 to sources that have an adapter.

---

## 3. Deliberate supersessions: verification

| Brief position | PRD decision (logged) | Reflected clearly? | Note |
|---|---|---|---|
| Structured source "NVD or EUVD", to be chosen | EUVD and European sources first | Mostly | The Summary names EUVD and CERT-EU. **NVD's status is never stated** in the PRD body: is it excluded, a v2 option, or allowed through FR-5? Suggest one line in Out of Scope or Open Questions. |
| "Keep CERT-FR as a curated input"; first-use-case URL `https://cert.cyber.gouv.fr/alerte/feed/` | CERT-FR *alerte* optional (FR-2b), *avis* out of scope | Yes | Minor: "Optionally ingest" is ambiguous (built and off by default, or might not be built?). Note also that the brief's URL is the *alerte* feed, while the Slack evidence shows the current integration posts *avis*. The PRD handles this correctly, but one sentence would tell readers the brief's URL maps to FR-2b. The Summary omits CERT-FR entirely, which is fine given that it's optional. |
| Config in CSV or a key/value store, open | Pluggable provider: KV (preferred) + YAML (FR-21, FR-22) | Yes | CSV is implicitly dropped. Fine, since the decision log covers it. |

---

## 4. Brief intent confirmed as covered (no action)

- Per-endpoint product lists; no filter management in Slack: FR-10, Users, Out of Scope.
- Multiple sources, RSS and JSON: FR-1, FR-2, FR-2b, FR-5.
- CISA KEV exploitation signal with source attribution: FR-3, FR-15, FR-18.
- Correlation by CVE ID with source attribution kept: FR-7, FR-8.
- Score source and CVSS version kept; a missing score is unknown, never zero or low: FR-9, FR-14.
- Structured vendor/product/version used when available: FR-11 to FR-13.
- Slack subscribes without manual forwarding: FR-17.
- "Less irrelevant noise while still seeing what matters": Success Metrics (volume and recall).
- Thresholds the brief left open: freshness (1h NFR) and duplicates (metric and FR-19) are set. Availability and incorrectly included matches are not (see G2 and G4).
- Ops: refresh (FR-4), logging (Operability NFR), source failure (FR-6, Source resilience), auth/exposure (FR-20, HTTPS). See G4 and G8 for what's left.
- Out of scope carried over: Slack app or bot, GUI.
- "Team-focused CLI daemon": daemon in the Summary, CLI in FR-22.
