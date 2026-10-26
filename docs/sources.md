# Sources

What cvestream assumes about each upstream source. Behaviour changes here and in
the code in the same pull request. The recorded payloads that back these
assumptions live in `tests/fixtures/` (provenance in `tests/fixtures/README.md`),
and the `live` contract tests check them against the real APIs.

## Common behaviour

- **One HTTP client.** Every outbound call goes through `cvestream.http`. It
  ignores proxy variables (`trust_env=False`), verifies TLS against the system
  trust store (never disabled), uses explicit timeouts (connect 10 s, read 30 s)
  and refuses responses larger than 16 MiB.
- **Retries live there and nowhere else.** A request is tried up to 4 times, and
  only on a timeout, a 5xx or a 429. The wait is a full-jitter exponential
  backoff capped at 30 s, or `Retry-After` (seconds or HTTP date) when the server
  sends one; a `Retry-After` above 60 s means give up and let the next cycle
  retry. Every other failure (connection or TLS error, other 4xx) is not retried.
- **Error taxonomy** (`sources/base.py`): `SourceTransportError` (no usable
  response), `SourcePayloadError` (not JSON, bad encoding, too large) and
  `SourceContractError` (valid JSON in an unexpected shape: the API drifted).
  A fetch is all-or-nothing: on any of them there is no result, so the cursor
  does not advance.
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
- **Pagination.** `size` is at most 100; `page` starts at 0; a page past the end
  has no items. Pages are fetched one after the other with a 0.5 s pause, up to
  1,000 pages (about 5 times a 14-day catch-up); reaching that cap is a contract
  error, because the cursor must not advance past data that was not read.
- **Order.** Items come sorted by `dateUpdated`, newest first. The adapter relies
  on it to stop early (a page whose oldest item is older than the cursor is the last
  one) and checks it on every item: any item newer than the one before it is a
  `SourceContractError`. A silent switch to ascending order would otherwise drop
  almost everything.
- **Stop conditions.** An empty page, a page shorter than `size`, reaching
  `total`, or an item older than the cursor.
- **First run.** With no cursor, the fetch starts at now minus 14 days (the feed
  window), from the injected clock.
- **Cursor.** The newest `dateUpdated` seen, as ISO 8601 UTC with second precision
  (`2026-10-26T18:36:49Z`). Records equal to the cursor are delivered again on
  purpose: downstream is idempotent, a duplicate costs nothing and a miss is a lost
  alert. With no new record the cursor does not move. A stored cursor that cannot be
  parsed (or has no timezone) is logged (`source.cursor_invalid`) and replaced by the
  first-run window.
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
  (free text, kept as is: parsing belongs to the matcher). A product named with a
  placeholder is dropped (the record is kept); a placeholder version (`-`, `n/a`)
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
  non-list dump is a contract error.
- **Which entries.** Each entry has `sources`, a list that may hold `cisa_kev` and
  `eukev_kev`. Only entries with `eukev_kev` produce records; CISA-only entries are
  left to the `cisa_kev` adapter, which is authoritative for them.
- **Record source `eu_kev`, not `euvd`.** Records are keyed on their source and
  record ID. A KEV-only record under `euvd` would overwrite the full search record
  of the same EUVD ID. The adapter name (registry, cursor, logs) stays `euvd`.
- **Fields.** Record ID is the EUVD ID (the CVE ID if missing); aliases are the EUVD
  and CVE IDs; `vulnerabilityName`, when present, is the title; `dateAdded`
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
  empty `vulnerabilities` list is a contract error.
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
  The description still feeds description matching.
- **Not mapped.** `dueDate`, `requiredAction`, `notes`, `cwes` and `forensicTriage`
  are not required and `KevEntry` has no fields for them.
- **Dates.** `dateAdded` is a calendar date, stored as midnight UTC (display only).
- **Cursor.** `catalogVersion` when it is a non-empty string, otherwise none. It is
  informational (for status) and never used to skip a load.
- **Disabling.** Turning `cisa_kev` off in the settings removes the CISA flags; the
  EUVD dump is not used for them.
