# Sprint Change Proposal: Epic 5, readable bursts

- **Date:** 2026-11-21
- **Trigger:** the team's feedback canvas "CVEStream Retours" (a Slack canvas), read on 2026-11-21, after epic 4 shipped (wixyvir/cvestream#45, wixyvir/cvestream#46). Its open entries were discussed one by one in a party-mode session with the BMAD agents the same day.
- **Decision:** add **Epic 5: Readable bursts** with four stories:
  - 5.1: compact item body and title markers
  - 5.2: group bursts by product
  - 5.3: held-back count in `cycle.summary` (wixyvir/cvestream#47)
  - 5.4: reject duplicate product names (wixyvir/cvestream#48)
- **Scope:** Moderate. One new epic, one new FR, one amended FR, two Alembic migrations, a tighter config contract and new architecture rules for the item format and grouping. No completed story is reopened.
- **Mode:** batch

## TL;DR

- **The problem.** Slack's RSS app posts one message per item and cuts the item text at about **495 characters**. In the team's channel this had three effects:
  - On Apache Tomcat items, the five-times-repeated `Affected:` line used up that space, so the vulnerability's description never showed.
  - A single pipeline run posted 10 Tomcat messages, and 15 PHP messages in another run.
  - Nothing in a title stood out when scanning the channel.
- **Story 5.1** reorders and compacts the item text and adds Unicode emoji at the start of the title:
  - New order: one facts line, Matched, the full Description, a compact Affected line, then IDs, per-source CVSS, KEV detail, Sources and References.
  - Emoji: 🔥 exploited, 🔴 critical, 🔺 severity raised, 🆕 published today.
  - 🆕 is decided once and stored in a new `publication.is_fresh` column.
- **Story 5.2** adds an opt-in endpoint setting `group_by_product`:
  - When it is on, 3 or more decisions of the same product and the same kind family in one run become one item.
  - That item has its own stored GUID, at most 5 members (sorted by severity, worst first) and its own title.
  - KEV is never grouped.
- **Story 5.3** counts items held back by `require_score` in `cycle.summary` (wixyvir/cvestream#47).
- **Story 5.4** rejects duplicate product names per endpoint (wixyvir/cvestream#48). Story 5.2 keys groups on the entry name, so 5.4 lands before 5.2.
- **No work for canvas item 1** ("severity unknown" alerts): `require_score` already shipped (story 4.1). Enabling it is a deployment change, which is out of scope here; Cyprien switches it on after epic 5.
- **Order:** wave 13 runs 5.1, 5.3 and 5.4 in parallel; wave 14 runs 5.2. Epic 4 is marked `done`. Epic 3 stays in the backlog, as it did when epic 4 started.

## 1. Issue summary

**Problem.** The feed is correct, but the channel is hard to read. The canvas lists four open entries:

1. "Pending: drop 'severity unknown' alerts until a source scores them (known-exploited ones excepted): wixyvir/cvestream#43"
2. "group by product when CVE-ids are following each-others"
3. "description is hidden sometimes, make one-liners for dates and severity/status to shorten the advisory height, use a table ?"
4. "Would be great to have a status emoji on every CVE to quickly see if there is a known exploit available or not"

**Evidence.** From the team's Slack channel, read on 2026-11-21:

- **Truncation.** Slack's RSS app shows the item description as plain text and cuts it with "..." after **492 to 498 characters**. This was measured on three long posts, counting a line break as one character. On Tomcat posts, the `Affected:` line (`Apache Software Foundation Apache Tomcat 10.1.0-M1 ≤10.1.61; Apache Software Foundation Apache Tomcat 11.0.0-M1 ≤11.0.27; …`) is cut first, and `Description:` never appears.
- **Short posts without a description.** Short PHP posts (about 208 visible characters) show **no** `Description:` line either. The cause is unknown; story 5.1 investigates it.
- **Bursts.**
  - On 2026-11-20 at 00:46, one run posted 10 Apache Tomcat items. Their EUVD IDs follow each other (EUVD-2026-130760 to EUVD-2026-130778); their CVE numbers do not.
  - At 21:26 the same day, another run posted 9 `[UPDATE] … severity raised to critical/high` items for the same CVEs.
  - On 2026-11-19 at 17:16, one run posted 15 PHP items, and another posted 4 git-lfs items.
  - All items of a burst share one `pubDate`.
- **Scanning.** A title reads `CVE-2026-95517 in Apache Software Foundation Apache Tomcat: severity critical`. When an item is known-exploited, only the word `EXPLOITED` shows it.

**Category:** a new requirement from the stakeholders, based on how the feed is used day to day.

## 2. Impact analysis

### Checklist

| Item | Status | Notes |
|---|---|---|
| 1.1 Triggering story | [x] | No single story. Feedback from use after epic 4 (stories 4.1 and 4.2, wixyvir/cvestream#45 and wixyvir/cvestream#46) |
| 1.2 Core problem | [x] | Item text cut before the description, one message per vulnerability during bursts, nothing in titles to scan for |
| 1.3 Evidence | [x] | Channel measurements (section 1) |
| 2.1 Current epic | [x] | Epic 4 is complete (4.1 and 4.2 `done`) and is marked `done` |
| 2.2 Epic-level change | [x] | Add Epic 5 |
| 2.3 Future epics | [x] | Epic 3 (still in the backlog) is not affected. Story 5.4 tightens `EndpointConfig`, which the Valkey provider (3.1) and the CLI (3.2) reuse unchanged. Story 5.2 adds an endpoint field that the 3.2 CLI will need to expose (noted in 3.2) |
| 2.4 New or obsolete epics | [x] | None obsolete. Proof-of-concept or EPSS exploit data ("a known exploit is available") would need a new source. It is out of this epic and recorded as a possible later epic |
| 2.5 Epic order | [x] | Epic 5 starts now, ahead of epic 3, the same way epic 4 did |
| 3.1 PRD | [!] | Amend FR-18 (title markers, item order). Add FR-27 (grouping) |
| 3.2 Architecture | [!] | Feed cache rules (story 1.6), publication rules, the GUID table, the `publication` table, `cycle.summary`, the endpoint rules, the requirements mapping |
| 3.3 UX | [N/A] | No UX document. The item format is specified in the PRD, the architecture and `docs/operations.md` |
| 3.4 Other artifacts | [!] | `project-context.md`, `docs/operations.md`, `docs/configuration.md`, `endpoints.example.yaml`, `schemas/config-v1.json`, CHANGELOG, `sprint-status.yaml`, the "CVEStream Retours" canvas |
| 4.1 Direct adjustment | Viable | Effort: medium. Risk: low for 5.1, 5.3 and 5.4; medium for 5.2 (it changes the publish-once path) |
| 4.2 Rollback | Not viable | Nothing to roll back |
| 4.3 MVP review | Not viable | The MVP is unaffected |
| 4.4 Path | [x] | Direct adjustment: new Epic 5 |

### Canvas entry to story

| Canvas entry | Outcome |
|---|---|
| 1. "severity unknown" alerts | No story. `require_score` shipped in story 4.1. Turning it on for the team's endpoint is a deployment change, which is out of scope. Cyprien does it after epic 5. Note: an item already posted as `unknown` still gets its `[UPDATE] … severity raised` when it is scored (`domain/publication.py`), so the first runs after the switch still show a few such pairs |
| 2. group by product | Story 5.2 |
| 3. one-liners, description hidden, table? | Story 5.1. No table: Slack shows the HTML as plain text and table cells run together |
| 4. status emoji | Story 5.1, redirected by Cyprien to "emoji in the title for relevant labels". Only KEV data exists: the emoji means "listed in CISA KEV or EU KEV", never "a public exploit exists" |
| (open issues) | wixyvir/cvestream#47 becomes story 5.3, wixyvir/cvestream#48 becomes story 5.4 |

### Technical impact

- **Story 5.1, title markers.** These come from values fixed at decision time, so a title never changes when the feed is re-rendered.
  - 🔥, 🔴 and 🔺 come from the stored `is_known_exploited`, `band` and `kind`.
  - 🆕 needs a new nullable column `publication.is_fresh`. `decide()` sets it: true when the vulnerability's `published_at` falls on the same UTC calendar day as the decision. The sources give day-level dates, which is why a calendar day is used and not "the last 24 hours".
  - Existing rows get `NULL`, which means no 🆕. Render stays clock-free.
- **Story 5.1, item text.** Only `pipeline/render.py` changes.
  - Every feed's ETag changes once.
  - GUIDs are untouched, so nothing is posted again to Slack. Only items published after the deployment look different there.
- **Story 5.2, grouping.**
  - Grouping is a pure step in `domain/publication.py`, run after all `decide()` calls of a run and inside the run's single `State.transaction()`.
  - Its group GUID is built only there and stored once.
  - New nullable columns on `publication` link each member to its group; this needs an Alembic migration.
  - Render shows one item per group in place of its members.
  - Grouping at render time is rejected: it would change which item GUIDs appear, and Slack would post them again.
  - A new endpoint field `group_by_product` goes into the v1 contract, and the schema is regenerated.
- **Story 5.3.**
  - `match()` (or the pipeline) must tell "held back by `require_score`" apart from "no match".
  - `CycleSummary` gains a per-endpoint `held_back` count.
- **Story 5.4.**
  - A contract tightening: configs with two entries of the same `name` in one endpoint load today and are rejected afterwards.
  - Needs a CHANGELOG note.
  - The JSON Schema cannot express uniqueness of a field across array items, so only the validator changes. `config schema --check` stays green.

## 3. Recommended approach

**Direct adjustment:** add Epic 5 with four stories. Every rule stays as it is: publish-once, GUIDs stored once, a pure renderer, one transaction per run. Grouping is an extra decision recorded in the same transaction, not a change to `decide()`.

- **Effort:** medium overall. 5.1 and 5.2 are the substantial stories; 5.3 and 5.4 are small.
- **Risks:**
  - **Story 5.2 touches the publish-once path.** It is mitigated by tests for restart without re-posting, GUIDs independent of input order, and ETag stability.
  - **The 495-character budget is measured, not documented by Slack.** Story 5.1 pins it in one constant and one test, so a later change is a one-line fix.
  - **Story 5.4 can reject a config that loads today.** The CHANGELOG says so, and `docs/configuration.md` already warns that duplicate names are not supported.
- **Success signal:**
  - fewer Slack messages per burst on an endpoint with `group_by_product: true`;
  - the description visible in Slack for every item whose description is not empty (the start of the description falls within the first 495 characters).

## 4. Detailed change proposals

### 4.1 PRD (`prds/prd-cvestream-2026-10-26/prd.md`)

**FR-18. OLD:**

> - **FR-18** Each feed item's title names the matched product and the severity. Its body shows the CVE and EUVD IDs, when the vulnerability was first published and last updated, each source's CVSS score with its version, KEV status with the date each list added it, affected versions, and links to every source.

**FR-18. NEW:**

> - **FR-18** Each feed item's title names the matched product and the severity, and starts with emoji markers for the labels that matter: known-exploited, critical, severity raised, and first published on the day of publication. Each marker sits beside the label's word, never instead of it. Its body shows the CVE and EUVD IDs, when the vulnerability was first published and last updated, each source's CVSS score with its version, KEV status with the date each list added it, affected versions, and links to every source. The body leads with the severity, the exploited status, the dates, the matched product and the description, so that a reader that shows only the start of the text still sees them. [ASSUMPTION: Slack's RSS app shows about the first 495 characters of the body]

*Rationale:* canvas entries 3 and 4. The facts FR-18 lists are unchanged; only their order and the title markers are new.

**New FR-27 (after FR-26). NEW:**

> - **FR-27** An endpoint can be set to group bursts: when one cycle publishes three or more items for the same matched product and of the same kind (new, or severity raised), they are published as one feed item that lists them from the most to the least severe, at most five per item. A known-exploited vulnerability is never grouped. A member that later becomes known-exploited or whose severity band rises is published on its own, as FR-19 says.

*Rationale:* canvas entry 2. FR-19 is unchanged: each vulnerability is still decided and recorded once per endpoint.

### 4.2 Epics (`epics.md`)

**Requirements Inventory, FR-18. OLD:**

> - FR-18: Item title names the matched product and severity; body shows CVE/EUVD IDs, first published and last updated dates, each source's CVSS score + version, KEV status with the date each list added it, affected versions, links to every source.

**NEW:**

> - FR-18: Item title names the matched product and severity, with emoji markers (exploited, critical, severity raised, published today) beside the words; body shows CVE/EUVD IDs, first published and last updated dates, each source's CVSS score + version, KEV status with the date each list added it, affected versions, links to every source, leading with the facts a truncating reader must see.

**Requirements Inventory, after FR-26. ADD:**

> - FR-27: Opt-in per endpoint: a burst of 3 or more items of the same matched product and kind family in one cycle becomes one feed item (at most 5 members, most severe first); known-exploited vulnerabilities are never grouped.

**FR Coverage Map. ADD:**

> - FR-18: Epic 5 (Story 5.1) - title markers, compact body
> - FR-27: Epic 5 (Story 5.2) - burst grouping

**Epic List, after Epic 4. ADD:**

> ### Epic 5: Readable bursts
>
> The team reads its channel faster: each Slack message shows the severity, the exploited status, the matched product and the start of the description before Slack cuts it; emoji in the title flag exploited, critical, raised and same-day vulnerabilities; an endpoint can group a burst of same-product items into one message; held-back items are counted; duplicate product names are rejected.
> **FRs covered:** FR-18 (amended), FR-27. NFR-4 (held-back count).

**New section at the end of the file. ADD:**

> ## Epic 5: Readable bursts
>
> Built from the team's feedback canvas "CVEStream Retours" (sprint change proposal 2026-11-21, epic 5). Slack's RSS app shows about the first 495 characters of an item, as plain text, one message per item.
>
> ### Story 5.1: Compact item body and title markers
>
> As a channel subscriber,
> I want each Slack message to show the severity, the exploited status, the dates, the matched product and the start of the description, and the title to flag what matters with an emoji,
> so that I can triage a message without opening it and spot the ones that matter while scrolling.
>
> **Acceptance criteria:**
>
> **Given** any feed item
> **When** its description is rendered
> **Then** the blocks come in this order:
> 1. a facts line, `<severity band> · CVSS <highest score> · exploited: <yes|no> · published <day> · updated <day>`, where the `updated` part is left out when it falls on the same UTC day as `published`, and an unknown value reads `unknown`;
> 2. the matched products line, `Matched: …`, with `(versions unknown)` when it applies;
> 3. the full description, never cut on the server;
> 4. the affected products;
> 5. then the IDs line, each source's CVSS score with its version, the KEV detail (each list, the day it added the vulnerability, ransomware use) when it is known-exploited, the sources and the references.
>
> **Given** affected products from several sources, or several rows for one product
> **When** the Affected line is rendered
> **Then** each product label (vendor dropped when the product name already contains it) appears once, followed by its rows' version text **verbatim** (never parsed or rewritten), at most 3 per product, then `(+N more)`
>
> **Given** the Tomcat fixture (CVE-2026-95517: five affected rows, CVSS 9.1, not exploited)
> **When** the description is reduced to plain text (tags stripped, a line break counted as one character)
> **Then** its first 495 characters contain the severity band, `exploited: no`, the matched product and the first 40 characters of the description; the budget is one named constant
>
> **Given** an item's decision-time values
> **When** its title is rendered
> **Then** it starts with these Unicode emoji, in this order, all that apply, followed by one space:
> - 🔥 when the stored `is_known_exploited` is true;
> - 🔴 when the stored band is `critical`;
> - 🔺 when the kind is `band-<band>`;
> - 🆕 when the stored `is_fresh` is true.
>
> Every other band and the unknown state get no emoji, and there is no "safe" marker. The existing words (`EXPLOITED`, `[UPDATE]`, `severity critical`) stay. No Slack shortcode (`:fire:`) is used.
>
> **Given** a `new` decision
> **When** the vulnerability's earliest `published_at` falls on the same UTC calendar day as the decision time from the injected `Clock`
> **Then** `PublicationDecision.is_fresh` is true and stored in a new nullable `publication.is_fresh` column (Alembic migration); it is false when `published_at` is unknown or on another day, and false for every `kev` and `band-*` decision; existing rows are `NULL` and render without 🆕
>
> **Given** a feed rendered twice from the same state
> **When** the clock has moved past midnight in between
> **Then** the XML and the ETag are identical (render stays clock-free)
>
> **Given** short items whose body fits in the budget (the PHP posts of 2026-11-19 showed no `Description:` line in Slack)
> **When** the story starts
> **Then** the cause is investigated and either fixed or recorded in `deferred-work.md`
>
> Examples (golden):
> - `🔥🔴🆕 CVE-2026-95517 in Apache Software Foundation Apache Tomcat: severity critical, EXPLOITED`
> - `🆕 CVE-2026-107494 in OpenSSL: severity unknown`
> - `🔴🔺 [UPDATE] CVE-2026-118028 in Apache Software Foundation Apache Tomcat: severity raised to critical`
> - `🔥 [UPDATE] CVE-2026-1234 in PHP Group PHP: now EXPLOITED, severity high`
>
> Documentation: the story 1.6 rules in `architecture.md`, the item format in `docs/operations.md` (a golden example and the budget), README, CHANGELOG.
>
> ### Story 5.2: Group bursts by product
>
> As a channel subscriber,
> I want a burst of items about the same product to arrive as one message,
> so that ten Tomcat CVEs published together do not fill my channel with ten tall messages.
>
> **Acceptance criteria:**
>
> **Given** the v1 config contract
> **When** `group_by_product: bool = False` is added to `domain/endpoint.Rules` and `EndpointConfig` (strict bool)
> **Then** `Rules()` keeps its "no filtering, no grouping" meaning, `group_by_product: "yes"` is rejected with the path `endpoints.0.group_by_product`, and `schemas/config-v1.json` is regenerated in the same commit with `SCHEMA_VERSION` staying `1`
>
> **Given** an endpoint with `group_by_product: true` and one pipeline run
> **When** at least 3 decisions share the matched entry name (`products[0]`) and the kind family (`new`, or `band-*`) and none of their vulnerabilities is known-exploited
> **Then** they are grouped: a pure function in `domain/publication.py`, called by `pipeline/run.py` after every `decide()` of the run and inside the run's transaction, returns the groups, ordered by band descending then by primary ID (natural CVE order)
>
> **Given** a group of more than 5 members
> **When** it is formed
> **Then** it is split in that order into items of at most 5 members (6 members → 5 + 1, and a chunk below 3 is still published as a group, because the burst reached the threshold)
>
> **Given** a group or chunk
> **When** it is recorded
> **Then** its GUID `cvestream:{endpoint_id}:group-{family}:{h}`, where `h` is the first 16 hex characters of the SHA-256 of its sorted member GUIDs, is built only in `domain/publication.py` and stored once. Each member keeps its own `publication` row and GUID and stores the group GUID in a new nullable `publication.group_guid` column (Alembic migration, upgrade and downgrade tested)
>
> **Given** a stored group
> **When** the feed is rendered
> **Then**:
> - **Item:** one item replaces its members, with the group GUID, the members' `pubDate`, and the link of the most severe member.
> - **Title:** `<emoji> <product label>: <n> new vulnerabilities, worst <band> (<count per band>)`, or `<emoji> [UPDATE] <product label>: <n> vulnerabilities raised (<count per new band>)`.
>   - The emoji: 🔴 when any member is critical, 🔺 for the `band-*` family, 🆕 when **any** member is fresh.
>   - A split group adds `, part <i> of <k>`.
> - **Body:** one story-5.1 one-liner per member, most severe first, `<CVE> · <band> <score> · exploited: no · <first affected version text> (+N more)`, each CVE linked.
>
> **Given** an endpoint with `group_by_product: false` (the default), 2 decisions of the same product, a mix of `new` and `band-*`, or a known-exploited member
> **When** the run is decided
> **Then** those decisions are published as single items exactly as today
>
> **Given** a member of a published group
> **When** it later becomes known-exploited or its band rises
> **Then** it is decided by `decide()` as today and published on its own (or in a new group of its run, if not known-exploited)
>
> **Given** a restart, or the same run replayed
> **When** the feed is rendered again
> **Then** no group or member is published twice, the group GUID is read from storage and never recomputed, and the GUID does not depend on the order of the input decisions
>
> Documentation: architecture (publication rules, GUID table, `publication` table), `docs/configuration.md`, `docs/operations.md`, `endpoints.example.yaml`, README, CHANGELOG.
>
> ### Story 5.3: Held-back count in cycle.summary
>
> As the operator,
> I want the number of items an endpoint's `require_score` held back in each cycle,
> so that a rule that silently drops CERT-EU-only and freshly published items is visible.
>
> Added from GitHub issue wixyvir/cvestream#47 (decision D3 in the review of wixyvir/cvestream#45).
>
> **Acceptance criteria:**
>
> **Given** an endpoint with `require_score: true`
> **When** a cycle matches a vulnerability on products and `kev_only` but drops it because no source has scored it
> **Then** `cycle.summary` reports it in a per-endpoint `held_back` count next to `score_unknown` and `versions_unknown`; a vulnerability that does not match the products is not counted
>
> **Given** `match()`
> **When** it drops a vulnerability
> **Then** the caller can tell `require_score` from every other reason (a drop reason, or the rule evaluated by the pipeline), and `domain` stays pure
>
> Documentation: `docs/operations.md` (`cycle.summary` fields).
>
> ### Story 5.4: Reject duplicate product names within an endpoint
>
> As the operator,
> I want the configuration to reject two product entries with the same name in one endpoint,
> so that the item title, and story 5.2's groups, never pick the wrong entry.
>
> Added from GitHub issue wixyvir/cvestream#48 (decision D2 in the review of wixyvir/cvestream#46).
>
> **Acceptance criteria:**
>
> **Given** an endpoint whose `products` contain two entries whose names are equal after stripping and case-folding
> **When** the configuration is validated (YAML now, Valkey later through the same `EndpointConfig`)
> **Then** it is rejected with the path of the second entry and a message naming the duplicate, and `cvestream config validate` exits non-zero
>
> **Given** distinct names, including an alias of one entry used as another entry's name
> **When** validated
> **Then** it is accepted (the workaround documented for exact entries keeps working)
>
> Documentation: `docs/configuration.md` (remove the "not rejected yet" note), CHANGELOG (a contract tightening: configs that load today can be rejected).

### 4.3 Architecture (`architecture.md`)

**Feed cache rules (story 1.6), title and body sentences. OLD:**

> … known-exploited reads `EXPLOITED`. Its body comes from the current merged data of the vulnerability (dates included: the earliest `published_at` and the newest `updated_at` of the sources, and each KEV list's earliest `added_at`, as UTC days), as an HTML fragment (RSS 2.0 `description` is entity-encoded HTML): bold labels, `<br/>` between facts, source records and references as links; …

**NEW:**

> … known-exploited reads `EXPLOITED`. The title starts with Unicode markers built from decision-time values only, in this order: 🔥 known-exploited, 🔴 band `critical`, 🔺 kind `band-*`, 🆕 `publication.is_fresh`. All that apply are shown, each beside its word, and nothing marks other bands or the unknown state (story 5.1). Its body comes from the current merged data of the vulnerability (dates included: the earliest `published_at` and the newest `updated_at` of the sources, and each KEV list's earliest `added_at`, as UTC days), as an HTML fragment (RSS 2.0 `description` is entity-encoded HTML). Slack's RSS app shows about the first 495 plain-text characters, so the body leads with what a reader needs (story 5.1):
> - a facts line (band · highest CVSS · exploited · published · updated when it is another day);
> - the matched products;
> - the full description;
> - the affected products (each label once, source version text verbatim, at most 3 rows per product, then `(+N more)`);
> - then the IDs, each source's CVSS, the KEV detail, the source records and the references as links.
>
> …

**Correlation and publication rules (story 1.5). ADD at the end:**

> - 🆕 freshness (story 5.1): `decide()` sets `is_fresh` on a `new` decision when the vulnerability's earliest `published_at` falls on the decision's UTC calendar day. It is stored, never recomputed, and `NULL` on rows older than the column.
> - Grouping (story 5.2, FR-27), on an endpoint with `group_by_product`: after every `decide()` of the run, `domain/publication.py` groups decisions that share the matched entry name and the kind family (`new`, `band-*`), with at least 3 members and none known-exploited. A group is ordered by band descending, then primary ID, and split into items of at most 5. Each group or chunk gets the GUID `cvestream:{endpoint_id}:group-{family}:{sha256(sorted member GUIDs)[:16]}`, stored in each member's `publication.group_guid`. A member's later `kev` or `band-*` decision is independent of its group.

**Identifiers table, RSS GUID row. OLD:**

> | RSS GUID | `cvestream:{endpoint_id}:{primary_id}:{kind}` with `kind` = `new`, `kev`, `band-{band}` | `cvestream:team-gitlab:CVE-2026-109618:kev` |

**NEW:**

> | RSS GUID | `cvestream:{endpoint_id}:{primary_id}:{kind}` with `kind` = `new`, `kev`, `band-{band}`; a group item (story 5.2): `cvestream:{endpoint_id}:group-{family}:{h}` with `family` = `new` or `band`, `h` = 16 hex of SHA-256 over the sorted member GUIDs | `cvestream:team-gitlab:CVE-2026-109618:kev`, `cvestream:team-gitlab:group-new:3f9c0a17b2d4e851` |

**Logging (`cycle.summary`). OLD:**

> `cycle.summary` per run: per-source result, per-endpoint published count, pass-throughs on unknown score and unknown versions (NFR-4).

**NEW:**

> `cycle.summary` per run: per-source result, per-endpoint published count, pass-throughs on unknown score and unknown versions, and items held back by `require_score` (NFR-4, story 5.3).

**Requirements to Structure Mapping. ADD:**

> | FR-18 title markers, compact body | `pipeline/render.py`, `domain/publication.py` (`is_fresh`) |
> | FR-27 burst grouping | `domain/publication.py`, `pipeline/run.py`, `pipeline/render.py`, table `publication` |

**Endpoint rules (where `require_score` and `exact` are described). ADD:** `group_by_product` (story 5.2), and duplicate entry names per endpoint are rejected (story 5.4).

### 4.4 Project context (`project-context.md`)

**GUID rule. OLD:**

> - GUID = `cvestream:{endpoint_id}:{primary_id}:{kind}` with `kind` = `new`, `kev` or `band-{band}`. It is computed **once** at decision time, stored in `publication.guid`, and **never recomputed**. …

**NEW:**

> - GUID = `cvestream:{endpoint_id}:{primary_id}:{kind}` with `kind` = `new`, `kev` or `band-{band}`; a group item's GUID is `cvestream:{endpoint_id}:group-{family}:{h}` (story 5.2). Both are computed **once** at decision time, in `domain/publication.py` only, stored (`publication.guid`, `publication.group_guid`), and **never recomputed**. …

**ADD (rendering rules):**

> - Title markers come from decision-time values only (🔥 🔴 🔺 🆕, in that order, beside the words); never derive a title from the clock or from current merged data. The body leads with the facts Slack's ~495-character window must show; source version text is shown verbatim, never reformatted.

### 4.5 Other artifacts

- **`docs/operations.md`:**
  - the item-format bullets (title, body order, markers, 495-character window, group items);
  - `cycle.summary` gains `held_back`.
- **`docs/configuration.md`:**
  - `group_by_product` in the table and a paragraph;
  - duplicate names are rejected (replacing the wixyvir/cvestream#48 note).
- **Same-commit companions of `config/models.py`:** `endpoints.example.yaml` and `schemas/config-v1.json` (story 5.2).
- **CHANGELOG and README:** updated by each story.
- **`sprint-status.yaml`:**
  - `epic-4: done`;
  - add `epic-5: backlog` with its four stories in `backlog` and `epic-5-retrospective: optional`;
  - in the waves comment: `Epic 5 (starts when epic-4 is done)`, `Wave 13: 5-1 | 5-3 | 5-4`, `Wave 14: 5-2`;
  - dependencies `5-1: 1-6 · 5-2: 5-1, 5-4 · 5-3: 4-1 · 5-4: 4-2`;
  - parallel caveat for wave 13: shared `docs/operations.md`, CHANGELOG and README; 5.1 owns the only migration of the wave.
- **Story 3.2 (backlog):** its CLI options must include `group_by_product` (a note in the story when it is created).
- **"CVEStream Retours" canvas:** Cyprien decides how to mark entries 2 to 4. Entry 1 stays pending until he enables `require_score`.

## 5. Implementation handoff

- **Scope:** Moderate. A new epic and contract changes, but no replan.
- **Planning artifacts:** the developer agent applies sections 4.1 to 4.4 and `sprint-status.yaml` after approval, in one docs commit on its own branch and PR, as for earlier proposals.
- **Stories:**
  - `bmad-create-story` creates 5.1, 5.3 and 5.4 (wave 13, in parallel; one branch and PR each, `story-5-<n>-<slug>`);
  - 5.2 follows when 5.1 and 5.4 are `done`.
  - 5.2 and 5.1 each add one Alembic revision. 5.2's revision comes after 5.1's, so they are never developed at the same time.
- **Success criteria:**
  - every gate command passes;
  - the Tomcat golden test and the 495-character budget test pass;
  - a feed with `group_by_product: true` posts one message per burst and per kind family in Slack;
  - no item is posted again after deployment, restart or replay;
  - the docs match the behaviour in the same PR.
- **After epic 5:** Cyprien enables `require_score` (and, if wanted, `group_by_product`) on the team's endpoint and updates the canvas.
