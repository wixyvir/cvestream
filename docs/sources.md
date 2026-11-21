# Sources

What cvestream assumes about each upstream source. Behaviour changes here and in
the code in the same pull request. The recorded payloads that back these
assumptions live in `tests/fixtures/` (provenance in `tests/fixtures/README.md`),
and the `live` contract tests check them against the real APIs.

## Common behaviour

- **One HTTP client.** Every outbound call goes through `cvestream.http`. It
  ignores proxy variables (`trust_env=False`), verifies TLS against the system
  trust store (never disabled), uses explicit timeouts (connect 10 s, read 30 s)
  and refuses responses larger than 16 MiB. Redirects are followed, but a
  redirect from HTTPS to plain HTTP is refused (transport error, not retried).
- **Retries live there and nowhere else.** A request is tried up to 4 times, and
  only on a timeout, a 5xx or a 429. The wait is a full-jitter exponential
  backoff capped at 30 s, or `Retry-After` (seconds or HTTP date) when the server
  sends one; a `Retry-After` above 60 s means give up and let the next cycle
  retry. Every other failure (connection or TLS error, other 4xx) is not retried.
- **Error taxonomy** (`sources/base.py`): `SourceTransportError` (no usable
  response), `SourcePayloadError` (not JSON, not XML, a DOCTYPE in XML, bad encoding, too large) and
  `SourceContractError` (valid JSON in an unexpected shape: the API drifted).
  A fetch is all-or-nothing: on any of them there is no result, so the cursor
  does not advance. `SourceTransportError.status_code` carries the final HTTP
  status (`None` without a response), so an adapter that fetches one document
  per record (CERT-EU) can treat a final 4xx on that document as a record-level
  skip.
- **Record-level failures are skipped and counted** (`FetchResult.skipped`), not
  fatal: an item that fails validation, has an unusable ID or an unparseable
  `dateUpdated`.
- **Placeholders mean unknown.** After trimming, `""`, `n/a`, `na`, `-` and
  `unknown` (any case) become `None`. Unknown is never `0` or an empty string. A
  field of the wrong type makes the whole record invalid (skipped and counted).
- **The cursor is an opaque string** owned by the adapter. `next_cursor` comes
  from source data, never from the local clock, so a skewed local clock cannot
  make cvestream skip records.

## EUVD (`euvd`)

Base URL `https://euvdservices.enisa.europa.eu/api`, GET only, no authentication.
Public record page: `https://euvd.enisa.europa.eu/vulnerability/{EUVD-ID}`.

- **Endpoints.** `GET /search?fromUpdatedDate=YYYY-MM-DD&size=100&page=N` returns
  `{"items": [...], "total": <int>}`; `GET /kev/dump` returns the KEV list (see
  below).
- **Day granularity.** `fromUpdatedDate` accepts only `yyyy-MM-dd`; a time of day
  is rejected with HTTP 400. The adapter queries from the UTC day of the cursor and
  drops the records older than the cursor itself.
- **Pagination.** `size` is at most 100 and the server caps a larger value
  silently (101 gives 100); `page` starts at 0; a page past the end has no items.
  The adapter assumes the server pages with the size it actually applies, so it
  counts the items it read against `total` instead of trusting the requested
  `size`: a page shorter than requested is not taken as the last one, and a lower
  server maximum costs more requests, not records. Pages are fetched one after the
  other with a 1.5 s pause (setting `sources.euvd.page_pause`), up to 1,000 pages (about 5 times a 14-day catch-up);
  reaching that cap is a contract error, because the cursor must not advance past
  data that was not read.
- **Rate limit.** Not documented. Observed on 2026-10-31 from one IP: about 40
  to 60 sequential requests a minute get through; faster paging is blocked by the
  gateway (`Server: Microsoft-Azure-Application-Gateway/v2`) with HTTP 403, an
  HTML body and no `Retry-After`, for a few seconds to about 20 s. Any single page
  answers 200 and the User-Agent does not matter. A 403 is not retried (EUVD also
  answers 403 to malformed parameters), so it fails the whole fetch. With a 0.5 s
  pause a 14-day first fetch (about 180 pages) never completed; with 1.5 s it
  takes about 5 minutes and incremental fetches are a few pages.
- **Order.** Items come sorted by `dateUpdated`, newest first. The adapter relies
  on it to stop early (a page whose oldest item is older than the cursor is the last
  one) and checks it on every item: any item newer than the one before it is a
  `SourceContractError`. A silent switch to ascending order would otherwise drop
  almost everything. Items already seen earlier in the same fetch are skipped
  before the check: records inserted on top while paging push older items onto
  the next page, which is not an ordering change.
- **Stop conditions.** The items read reach `total`, or an item is older than
  the cursor. An empty page before `total` is reached (a stale cached response or
  a glitch) does not fail the fetch: the records already read are kept, a
  `source.paging_incomplete` warning is logged, and the cursor does not move, so
  the next fetch reads the same window again and the unread items are not lost.
  Bad pagination must not take the source down.
- **First run.** With no cursor, the fetch starts at now minus 14 days (the feed
  window), from the injected clock.
- **Stale cursor.** A stored cursor older than now minus 14 days (a long outage) is
  clamped to the first-run window and logged (`source.cursor_clamped`): updates
  older than the feed window are given up, exactly like on a first run. Without
  the clamp a backlog above the 1,000-page cap would fail on every fetch and the
  cursor would never move again.
- **Cursor.** The newest `dateUpdated` seen, as ISO 8601 UTC with second precision
  (`2026-10-26T18:36:49Z`), capped at the fetch time: a `dateUpdated` in the future
  (bad source data) must not park the cursor ahead of every real record. The cap
  can only deliver records again, never skip one. Records equal to the cursor are
  delivered again on purpose: downstream is idempotent, a duplicate costs nothing
  and a miss is a lost alert. With no new record the cursor does not move. A stored
  cursor that cannot be parsed, has no timezone or lies in the future is logged
  (`source.cursor_invalid`) and replaced by the first-run window.
- **Timezone.** Dates look like `Oct 26, 2026, 6:36:49 PM` and carry no timezone.
  They are UTC. Evidence: EUVD-2026-99256 shows `datePublished` `Oct 13, 2026,
  6:50:03 PM` and `dateUpdated` `Oct 26, 2026, 6:36:49 PM`, and CVE.org's record for
  its alias CVE-2026-110215 shows `2026-10-13T18:50:03.848Z` and
  `2026-10-26T18:36:49.707Z`: the same wall clock on a summer-time date and a
  winter-time date, so no local offset applies. The parser is locale independent
  (a fixed English month table, a 12-hour clock where `12 AM` is 0 h and `12 PM` is
  12 h, and a narrow no-break space before AM/PM is accepted).
- **Unscored records.** EUVD writes `baseScore` `0.0` and omits `baseScoreVersion`
  and `baseScoreVector`. That becomes an unknown score (`Sourced(None)`), not a
  CVSS score of 0. A `0.0` that comes with a version is a real CVSS "none" and is
  kept. A score outside 0 to 10 is unknown.
- **EPSS.** EUVD gives a percentage (`9.81`); it is stored as a 0 to 1 fraction
  (`0.0981`). `0.0` means unknown: EUVD shows it for CVEs that have no EPSS score
  yet, and a real EPSS probability is never exactly 0.
- **Products.** `enisaIdProduct[]` gives product, vendor and `product_version`
  (free text, kept as is: parsing belongs to the matcher). The matcher
  understands these version formats: a single version (`8.2.29`); `X <Y` and
  `X ≤Y`, where `Y` may be `*` (`8.1.0 <8.2.29`); `<Y`, `<=Y`, `>=X`, `>X`;
  `X - Y`; `*`; a branch `B.*` (`8.2.*` is the whole 8.2 branch) and the PHP CNA
  format `B.* <Y` (`8.2.* <8.2.35`, as in PHP2Shell); a compound range
  `>=X <Y` (also `>`, `<=`); a placeholder lower bound `* <Y` or
  `unspecified <Y`. Anything else (`8.3.x`, `8.2rc1.*`, `>=8.0, <8.3`, `<= *`) is
  an unknown version: `min_version` never drops the item on that row, and the
  item is labelled "versions unknown" when no other row of the product reaches
  the minimum. A product named with a placeholder is dropped (the
  record is kept); a placeholder version (`-`, `n/a`)
  is an unknown version; a record with no product has no affected products.
- **Aliases and references** are newline separated strings with a trailing newline.
  The EUVD ID comes first in the aliases, then the others in order, placeholders
  removed, duplicates removed. References are for display only.
- **Ignored fields.** `exploitedSince` (it does not say which catalogue flagged the
  record: the flag comes from the KEV lists, see below), `assigner`, `enisaUuid`
  and `enisaIdVendor`.
- **Known gap.** A record updated while the adapter is paging jumps to page 0 and
  the items behind it shift up by one, so one record at a page boundary can be
  missed until its next update. It needs an update during the few seconds of paging
  and the next update delivers it again; concurrent updates otherwise only cause
  duplicates, which are removed (each EUVD ID is emitted once per fetch).

## EU KEV (record source `eu_kev`, fetched by `euvd`)

- **Full load on every fetch.** `GET /kev/dump` returns the whole list (about
  1,800 entries, 260 KB) and is read on every EUVD fetch, before `/search`. An EU
  KEV addition does not change `dateUpdated`: EUVD-2026-23571 was added to EU KEV on
  2026-10-10 (`exploitedSince` `Oct 10, 2026, 12:00:00 AM`) while its `dateUpdated`
  is still `Aug 17, 2026`. Incremental search alone would never see it. An empty or
  non-list dump is a contract error, and so is a dump where every EU KEV entry is
  rejected (renamed fields, not bad records).
- **Which entries.** Each entry has `sources`, a list that may hold `cisa_kev` and
  `eukev_kev`. Only entries with `eukev_kev` produce records; CISA-only entries are
  left to the `cisa_kev` adapter, which is authoritative for them.
- **Record source `eu_kev`, not `euvd`.** Records are keyed on their source and
  record ID. A KEV-only record under `euvd` would overwrite the full search record
  of the same EUVD ID. The adapter name (registry, cursor, logs) stays `euvd`.
- **Fields.** Record ID is the EUVD ID (the CVE ID if missing); aliases are the EUVD
  and CVE IDs, trimmed and upper-cased; an ID that does not look like `EUVD-YYYY-N`
  or `CVE-YYYY-NNNN...` counts as missing; `vulnerabilityName`, when present, is the title; `dateAdded`
  (`YYYY-MM-DD`) is stored as midnight UTC (display only; an unparseable one is
  unknown and the flag is kept). `known_ransomware_use` is unknown: EU KEV does not
  say. An entry with neither ID is skipped and counted.
- **Order in a fetch.** KEV records come first, then search records.

## CISA KEV (`cisa_kev`)

- **Endpoint.** `GET https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`
  (about 1.8 MB). The catalogue page used as the record URL is
  `https://www.cisa.gov/known-exploited-vulnerabilities-catalog`.
- **Full load on every poll.** There is no incremental filter, so the whole
  catalogue is read each time and the cursor argument is ignored. A missing or
  empty `vulnerabilities` list is a contract error, and so is a list where every
  entry is rejected (renamed fields, not bad records).
- **Entries.** `cveID` (note the capital `ID`) is required and must look like
  `CVE-YYYY-NNNN...`; it is trimmed and upper-cased, otherwise the entry is skipped
  and counted. `vulnerabilityName` is the title and `shortDescription` the
  description.
- **Ransomware.** `knownRansomwareCampaignUse` `Known` means known ransomware use
  (`True`). `Unknown`, a missing value or anything else is `None`: CISA only says
  "known" or "unknown", so `False` is never produced.
- **No affected products.** `vendorProject` and `product` are coarse labels without
  versions (for example "Netlogon (ZeroLogon)"). As structured products they would
  make every KEV item an unusable match for endpoints that have a minimum version.
  The description still feeds description matching, except for a name inside a file
  name or path, which does not count. Entries with `match_description: false` or
  `exact: true` ignore it, so such a KEV-only vulnerability waits for EUVD product
  data.
- **Not mapped.** `dueDate`, `requiredAction`, `notes`, `cwes` and `forensicTriage`
  are not required and `KevEntry` has no fields for them.
- **Dates.** `dateAdded` is a calendar date, stored as midnight UTC (display only).
- **Cursor.** `catalogVersion` when it is a non-empty string, otherwise none. It is
  informational (for status) and never used to skip a load.
- **Disabling.** Turning `cisa_kev` off in the settings removes the CISA flags; the
  EUVD dump is not used for them.

## CERT-EU (`cert_eu`)

CERT-EU security advisories, written by hand and published a couple of times a
month. They are often the first public word on an urgent vulnerability: for
PHP2Shell, advisory 2026-018 came about 7.5 hours before EUVD had the records.
Both endpoints are GET only, without authentication, and were checked on
2026-11-03. Texts and links belong to CERT-EU (licence in the JSON `licence`
field); the feed items link to the advisory page and credit the source as
`cert_eu`. :factory:

- **Endpoints.**
  - RSS: `GET https://cert.europa.eu/publications/security-advisories-rss`, the
    latest 10 advisories (about 10 KB, `ttl` 1440), no XML declaration.
  - JSON: `GET https://cert.europa.eu/publications/security-advisories/<serial>/json`
    (4 to 19 KB; an unknown serial answers `404 text/html`).
  - Display page, used as the record URL: `https://cert.europa.eu/publications/security-advisories/<serial>/`.
- **The RSS lists, the JSON carries the data.** The adapter reads the RSS, then
  fetches the JSON of each item whose `pubDate` is newer than the cursor, one by
  one, in RSS order. The serial (`2026-018`) comes from the stripped `<link>`
  path (title prefix as fallback). The `<guid>` is an internal counter and is
  never used. The `Accept` header does not change the RSS body.
- **Dates.**
  - RSS `pubDate` is Paris time written with the zone abbreviations `CEST` and
    `CET`, which `email.utils` does not understand (it returns a naive datetime,
    a silent 2-hour error). The adapter maps them to `+0200` and `+0100`;
    `GMT`, `UT` and numeric offsets are kept; any other zone is unusable and the
    item is skipped and counted. `Fri, 23 Oct 2026 16:36:26 CEST` is
    `2026-10-23T14:36:26Z`.
  - JSON `publish_date` (`23-10-2026 14:36:26`, `DD-MM-YYYY HH:MM:SS`) is UTC. It
    is the time of the last revision, and `updated_at`. When it cannot be read,
    the RSS `pubDate` is used.
  - `published_at` is the `original_date` of the YAML front matter of
    `content_markdown` (a calendar date, midnight UTC, display only), unknown when
    missing. The front matter's `date` is not used.
- **One record per CVE.** Every CVE ID found in `content_markdown` (any case,
  upper-cased, de-duplicated, in order of first appearance) gives one record:
  `record_id` `<serial>:<CVE>` (`2026-018:CVE-2026-109618`), aliases
  `(<CVE>, CERT-EU-SA<serial>)`. A multi-CVE advisory therefore never merges its
  vulnerabilities (advisory IDs are not merge keys): 2026-018 gives three
  vulnerabilities, and each merges with the EUVD vulnerability of its CVE.
- **The advisory-only record.** An advisory without any CVE ID (a pre-announcement
  such as 2025-029) gives one record whose `record_id` and only alias is
  `CERT-EU-SA<serial>`. It is never merged. The prefix keeps the bare serial from
  becoming the display ID (`2025-029` sorts before `CERT-EU-SA2025-029`). When a
  revision adds CVE IDs, they become new records and the advisory is published
  again, once per CVE: at most one extra item per advisory. Recall over volume.
- **Scores and KEV.** CERT-EU has no score field, so `scores` is empty (unknown,
  never `0`); a CVSS number written in the prose is ignored. No KEV data.
- **Description, title, references.** The description is the JSON `description`
  (plain text whose only markup is `<br>`, turned into line breaks). The title is
  the JSON `title`. References are the `<https://...>` links of the `# References`
  section.
- **Affected products are text, not versions.** The `# Affected Products` section
  (`# Products Affected` too, any case, emphasis or a trailing colon allowed, up
  to the next level-1 heading) is
  free-form: bullets, prose, or a lead-in paragraph naming the product with
  bullets that do not (`- 19.4 before 19.4.3`). The adapter keeps the whole
  section as blocks:
  - a paragraph followed by a bullet list is one entry,
    `"<paragraph>: <bullet>; <bullet>"`;
  - a bullet list without a lead-in gives one entry per top-level bullet, nested
    bullets inline;
  - a paragraph without a list is one entry, only in a section without any
    list: in a section with a list, a paragraph that is not a lead-in is
    commentary ("Many commercial and open-source products use PHP ...") and is
    dropped, because every entry is a structured hit for the matcher;
  - fenced code blocks are dropped;
  - emphasis, `[New]` and `[n]` markers are removed and whitespace is collapsed.

  An entry that names CVE IDs goes only to those CVEs' records (2025-008 lists one
  product block per CVE); an entry that names none goes to every record of the
  advisory. Versions are unknown (`version_text=None`), so a `min_version` entry
  matches with "versions unknown" until EUVD adds versions; an advisory without
  the section has no affected product.
- **Matching.** The entries are structured affected products, so they decide: a
  `gitlab` entry matches a GitLab advisory (2026-016, 2026-019) whose bullets omit
  the product name, and `match_description: false` does not hide a CERT-EU-only
  item. Because the text is prose, it never equals a product name, so an `exact`
  entry never matches CERT-EU text and a CERT-EU-only item waits for EUVD data on
  such an entry. The text is also prose for the title, so it never names the title
  of an item: the title uses the entry name until EUVD data names the product. The
  item body still lists the CERT-EU text.
- **Cursor and revisions.**
  - The cursor is the newest RSS `pubDate` seen, as ISO 8601 UTC
    (`2026-10-23T14:36:26Z`), capped at the fetch time.
  - Only items with a `pubDate` strictly newer than the cursor have their JSON
    fetched: a revised advisory has a new `pubDate` and is fetched again, an
    unchanged one costs no request. Existing records keep their `record_id`, so a
    revision alone never re-publishes (FR-19); a CVE added by a revision is a new
    record (2026-018 v1.2 added CVE-2026-115560).
  - With no cursor the start is now minus 14 days; an invalid or future cursor
    falls back to it (`source.cursor_invalid`) and a cursor older than it is clamped
    to it (`source.cursor_clamped`), like EUVD.
  - Assumption: fewer than 10 publications or revisions between two polls (the RSS
    has 10 items). CERT-EU publishes about two a month.
- **Failures.** All-or-nothing on source-level problems, per-advisory skips
  otherwise:
  - an RSS transport error, or an advisory JSON answering a 5xx or a 429 after
    retries, a timeout or a network error, is a `SourceTransportError` (no
    result, cursor unchanged);
  - an RSS that is not XML, has a DOCTYPE, or exceeds 1 MiB is a
    `SourcePayloadError`; an RSS without `channel` or `item`, or whose items are
    all unusable, is a `SourceContractError`;
  - an advisory whose JSON answers a final 4xx other than 429 (withdrawn,
    renamed, or not exported yet), is not JSON, is too large (2 MiB), has an
    unexpected shape or another `serial_number` than the RSS is **skipped and
    counted**, so one missing advisory never silences the source; the
    others are still emitted, and the cursor stays strictly below the failed
    advisory's `pubDate` so the next fetch retries it. When every advisory fetched
    is unusable, the fetch is a `SourceContractError`.
- **XML safety.** The RSS is parsed with the standard library
  (`xml.etree.ElementTree`) and no entity resolution: a document with a DOCTYPE is
  refused before anything is declared or expanded, so neither an external entity
  (XXE) nor an entity bomb ("billion laughs") runs. No `feedparser` (its date table
  has no `CET`/`CEST` either) and no `defusedxml`.
- **Assumed risks.**
  - Every CVE ID of the advisory text counts, including the References section and
    older CVEs named for context. Such a CVE gets a record and can make an old
    vulnerability active again for 14 days, publishing it as `new` on an endpoint it
    matches.
  - An advisory published without CVE IDs publishes once as `CERT-EU-SA<serial>`,
    and again per CVE when a revision adds them.
  - The affected-product text is prose: a broad lead-in or a list-free section
    can still match an endpoint on a word that is not the product (recall over
    precision). Commentary after a list is dropped, which removes the most
    common case.
- **Disabling.** `sources.cert_eu.enabled: false` (or
  `CVESTREAM_SOURCES__CERT_EU__ENABLED=false`) turns the source off; the interval
  (default 15 minutes, at most 60) is `sources.cert_eu.interval`.
