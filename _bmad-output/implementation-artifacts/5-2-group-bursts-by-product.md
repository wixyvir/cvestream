---
baseline_commit: cf57527
---

# Story 5.2: Group bursts by product

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- Created from the sprint change proposal 2026-11-21 (epic 5, readable bursts), canvas entry 2. -->

## Story

As a channel subscriber,
I want a burst of items about the same product to arrive as one message,
so that ten Tomcat CVEs published together do not fill my channel with ten tall messages.

Built from the team's feedback canvas "CVEStream Retours" (entry 2: "group by product when CVE-ids are following each-others"), see `_bmad-output/planning-artifacts/sprint-change-proposal-2026-11-21-epic-5-readable-bursts.md`. Evidence: on 2026-11-20 at 00:46 one run posted 10 Apache Tomcat items (EUVD-2026-130760 to EUVD-2026-130778, CVE numbers not consecutive); at 21:26 another run posted 9 `[UPDATE] … severity raised to critical/high` items for the same CVEs; on 2026-11-19 one run posted 15 PHP items. All items of a burst share one `pubDate` (one run = one `Clock` value).

## TL;DR

- **Config (additive, v1).** `group_by_product: bool = False` on `domain/endpoint.Rules` (last field) and `EndpointConfig` (strict bool, after `require_score`). Regenerate `schemas/config-v1.json` in the same commit; `SCHEMA_VERSION` stays `1`.
- **Decide, then group (domain, pure).** `decide()` is **unchanged**. A new pure `group_decisions()` in `domain/publication.py` takes one run's decisions for one endpoint and returns groups: same matched entry name (`products[0]`), same kind family (`new` or `band`), not known-exploited, at least **3** members. Members ordered by band descending, then primary ID in natural order (`CVE-2026-9999` before `CVE-2026-10000`); split into chunks of at most **5** (6 → 5 + 1; a chunk of 1 or 2 is still a group).
- **Group GUID.** `cvestream:{endpoint_id}:group-{family}:{h}`, `h` = first 16 hex of SHA-256 over the sorted member GUIDs joined by `\n`. Built only in `domain/publication.py`. Each member keeps its own `publication` row and GUID and stores the group GUID in a new nullable `publication.group_guid` column (migration `0004`).
- **Pipeline.** `pipeline/run.py` collects an endpoint's accepted decisions first, groups them when the rule is on, then writes the rows, all inside the same single transaction. The GUID-collision safety net must also check the GUIDs collected earlier in the same run (rows are now written later).
- **Render.** Rows sharing a `group_guid` become **one** item: group GUID, the members' `pubDate`, the link of the most severe member, a group title (`🔴🆕 Apache Software Foundation Apache Tomcat: 5 new vulnerabilities, worst critical (1 critical, 3 high, 1 medium)`), and a body with one line per member in **one** `<p>` (Slack keeps only the first block-level elements, a story 5.1 finding). `part i of k` is worked out at render time from the stored rows. Render stays pure and clock-free.
- **Never regroup.** Stored groups render as groups whatever the endpoint's current `group_by_product` value, and turning the rule on never regroups history. Otherwise GUIDs would change and Slack would post the items again.
- **Unchanged.** Default `false`; 2 decisions; a `new`/`band-*` mix; known-exploited decisions; later `kev`/`band-*` decisions of a former member. All of these are published as single items exactly as today.
- **Docs.** Architecture (GUID table, feed-cache rule), project context, `docs/configuration.md`, `docs/operations.md`, `endpoints.example.yaml`, README, CHANGELOG.
- **Stack.** Branch `story-5-2-group-bursts-by-product` is based on the story 5.4 branch (wixyvir/cvestream#50, rebased on `main` on 2026-11-22). Story 5.3 (wixyvir/cvestream#49) also edits `pipeline/run.py`, `summary.py` and `test_run.py`: whichever lands second rebases.

## Acceptance Criteria

1. **Config contract.** **Given** the v1 config contract **When** `group_by_product: bool = False` is added to `domain/endpoint.Rules` and `EndpointConfig` (strict bool) **Then** `Rules()` keeps its "no filtering, no grouping" meaning, `group_by_product: "yes"` (and `1`) is rejected with the path `endpoints.0.group_by_product`, `to_domain()` maps it, and `schemas/config-v1.json` is regenerated in the same commit with `SCHEMA_VERSION` staying `1`.

2. **Grouping.** **Given** an endpoint with `group_by_product: true` and one pipeline run **When** at least 3 of its decisions share the matched entry name (`products[0]`) and the kind family (`new`, or `band-*`), counting only decisions whose vulnerability is not known-exploited **Then** they are grouped by a pure function in `domain/publication.py`, called by `pipeline/run.py` after every `decide()` of the run and inside the run's transaction. It returns the groups with members ordered by band descending, then by primary ID (natural CVE order), and the result does not depend on the order of the input decisions.

3. **Split.** **Given** a group of more than 5 members **When** it is formed **Then** it is split in that order into chunks of at most 5 members (6 → 5 + 1, 11 → 5 + 5 + 1). A chunk below 3 is still published as a group, because the burst reached the threshold.

4. **GUIDs and storage.** **Given** a group or chunk **When** it is recorded **Then** its GUID `cvestream:{endpoint_id}:group-{family}:{h}` (`family` = `new` or `band`; `h` = first 16 hex characters of the SHA-256 of its sorted member GUIDs) is built only in `domain/publication.py` and stored once. Each member keeps its own `publication` row and GUID and stores the group GUID in a new nullable `publication.group_guid` column (Alembic migration `0004`, upgrade and downgrade tested). Rows written before the migration, and ungrouped rows, are `NULL`.

5. **Group item.** **Given** a stored group **When** the feed is rendered **Then**:
   - **Item:** one item replaces its members, with the group GUID (`isPermaLink="false"`), the members' `pubDate`, and the link of the most severe member.
   - **Title:** `<emoji> <product label>: <n> new vulnerabilities, worst <band> (<count per band>)`, or `<emoji> [UPDATE] <product label>: <n> vulnerabilities raised (<count per new band>)`. The product label is the story 1.6/5.1 title product of the most severe member. `<count per band>` lists the non-zero bands, worst first (`1 critical, 3 high, 1 medium`). `vulnerability` is singular when `n` is 1.
     - The emoji, in this order, all that apply, then one space (no leading space when none applies): 🔴 when any member's stored band is `critical`; 🔺 for the `band-*` family (see open question 1 for the `medium` floor); 🆕 when **any** member's stored `is_fresh` is true. 🔥 never appears (known-exploited is never grouped).
     - A split group adds `, part <i> of <k>` at the end.
   - **Body:** one story-5.1-style one-liner per member, most severe first, all in the first `<p>`, separated by `<br/>`: `<primary ID> · <band> <score> · exploited: <yes|no> · <first affected version text> (+N more)`, the primary ID linked to that member's first source link.

6. **Unchanged paths.** **Given** an endpoint with `group_by_product: false` (the default), or 2 decisions of the same product, or a mix of `new` and `band-*`, or a known-exploited member **When** the run is decided **Then** those decisions are published as single items exactly as today (same GUIDs, same titles, same bodies; `group_guid` is `NULL`).

7. **Later decisions of a member.** **Given** a member of a published group **When** it later becomes known-exploited or its band rises **Then** it is decided by `decide()` as today and published on its own (or in a new group of its run, if it is not known-exploited and the new burst reaches the threshold). The earlier group item is unchanged.

8. **Publish once.** **Given** a restart, or the same run replayed **When** the feed is rendered again **Then** no group or member is published twice, the group GUID is read from storage and never recomputed, the XML and the ETag are identical, and the GUID does not depend on the order of the input decisions.

9. **No regrouping.** **Given** stored groups **When** the endpoint's `group_by_product` is later switched off (or switched on over ungrouped history) **Then** the feed renders exactly what was stored: groups stay groups, single items stay single items.

## Tasks / Subtasks

- [x] **Task 1: config contract (AC 1)**
  - [x] 1.1 `src/cvestream/domain/endpoint.py`: add `group_by_product: bool = False` as the **last** field of `Rules`, with a one-line comment (group a same-product burst of one run into one item; story 5.2).
  - [x] 1.2 `src/cvestream/config/models.py`: add `group_by_product: bool = Field(default=False, description=...)` to `EndpointConfig` right after `require_score`. Suggested description: "Publish 3 or more items of one run that share a matched product and kind (new, or severity raised) as one feed item, at most 5 per item; known-exploited vulnerabilities are never grouped." Map it in `to_domain()` (`Rules(..., group_by_product=self.group_by_product)`). Strictness comes from `_CONFIG = ConfigDict(extra="forbid", strict=True)`: nothing else to add.
  - [x] 1.3 Regenerate `uv run cvestream config schema` and commit `schemas/config-v1.json` in the **same commit** as `models.py` (AGENTS.md). Check that `SCHEMA_VERSION` stays `1`.
  - [x] 1.4 `endpoints.example.yaml`: add a commented `group_by_product: false` after `require_score` on the first endpoint (same style: "Optional, default false. true …"). Same commit (it is a companion of `models.py`), and `uv run cvestream config validate endpoints.example.yaml` stays valid.
  - [x] 1.5 Tests in `tests/unit/config/test_models.py`, next to the `require_score` ones: default `False`; `test_group_by_product_maps_to_the_domain` (`Rules(group_by_product=True)`); add `group_by_product="yes"` and `group_by_product=1` to the parametrized rejection test (the one with `(_document(_endpoint(require_score="yes")), "endpoints.0.require_score")`). In `tests/unit/domain/`, if a test pins `Rules()` defaults, extend it.

- [x] **Task 2: grouping in the domain (AC 2, 3, 4)**
  - [x] 2.1 `src/cvestream/domain/identity.py`: make the natural order public. Rename `_sort_key` to `alias_sort_key` (keep its behaviour: `(year, number, alias)` for CVE and EUVD, `(0, 0, alias)` for advisories) and use it in `primary_id()`. No behaviour change for `primary_id()`.
  - [x] 2.2 `src/cvestream/domain/publication.py`, add:
    - `GROUP_MIN_MEMBERS = 3`, `GROUP_MAX_MEMBERS = 5` (FR-27), and `FAMILY_NEW = "new"`, `FAMILY_BAND = "band"`.
    - `kind_family(kind: str) -> str | None`: `new` → `"new"`, `band-*` → `"band"`, anything else (`kev`) → `None`.
    - `member_order_key(band: Band, primary_id: str) -> tuple[...]`: `(-band.rank, alias_sort_key(primary_id))`. Public, because the renderer reuses it to order members and chunks exactly as the split did.
    - `@dataclass(frozen=True, slots=True) class PublicationGroup`: `guid: str`, `endpoint_id: str`, `family: str`, `product: str` (the entry name), `members: tuple[PublicationDecision, ...]` (ordered), `part: int` (1-based), `parts: int`.
    - `group_decisions(decisions: Sequence[PublicationDecision]) -> tuple[PublicationGroup, ...]`:
      1. Candidates: `kind_family(d.kind) is not None`, `not d.is_known_exploited`, `d.products` not empty.
      2. Bucket by `(d.endpoint_id, d.products[0], family)`. The endpoint ID is part of the key, so nothing needs raising if a caller mixes endpoints (`domain` cannot import `CvestreamError`).
      3. Keep buckets with at least `GROUP_MIN_MEMBERS` members; sort each by `member_order_key(d.band, d.primary_id)`, then by `d.guid` as the final tie-break (two advisory IDs can sort equal on the key).
      4. Split with `itertools.batched(members, GROUP_MAX_MEMBERS)`. `parts = len(chunks)`.
      5. GUID per chunk via a private `_group_guid(endpoint_id, family, member_guids)`, the **only** construction site: `h = hashlib.sha256("\n".join(sorted(member_guids)).encode("utf-8")).hexdigest()[:16]` → `f"cvestream:{endpoint_id}:group-{family}:{h}"`. Same comment as the single GUID ("computed once, stored, never recomputed").
      6. Return groups sorted by `(member_order_key of the first member, family, product, part)`. Deterministic whatever the input order.
    - Update the module docstring: grouping is a second, pure step after `decide()`; `decide()` itself does not change.
  - [x] 2.3 `hashlib` and `itertools` are stdlib: the "domain is pure" import-linter contract allows them. `tests/unit/domain/test_purity.py` must stay green.
  - [x] 2.4 Tests in `tests/unit/domain/test_publication.py` (pure: build `PublicationDecision` values directly with a small `_decision(primary_id, band, kind="new", product="tomcat", known=False, fresh=False, endpoint="team")` helper; GUIDs as `decide()` builds them):
    - 3 same-product `new` decisions → one group of 3; 2 → none; 3 with one known-exploited → none (2 left); a `kev` decision never joins.
    - `new` and `band-*` of the same product are separate families (3 + 3 → two groups; 2 + 2 → none).
    - Two products of 3 each → two groups; `products[0]` decides (a decision matched by `("php", "tomcat")` counts for `php` only).
    - Order: band descending, then natural ID order (`CVE-2026-9999` before `CVE-2026-10000`, which plain string order gets wrong); `unknown` sorts last.
    - Split: 6 → parts `(1, 2)` with 5 + 1; 10 → 5 + 5; 11 → 5 + 5 + 1; part 1 holds the most severe.
    - GUID: golden value pinned for one fixed set of member GUIDs (compute it once in the test with `hashlib` from the documented recipe and also pin the literal string); `group-new` / `group-band` prefix; 16 lowercase hex characters; same GUID for any permutation of the input (use `itertools.permutations` on a 4-member set or a few shuffles with a fixed `random.Random(0)`); different chunks → different GUIDs.
    - Result order independent of input order (whole tuple equal after shuffling).
  - [x] 2.5 `tests/unit/domain/test_identity.py`: add one test pinning `alias_sort_key` natural order (no test references `_sort_key` today).

- [x] **Task 3: state (AC 4, 9)**
  - [x] 3.1 `src/cvestream/state/schema.py`: add `Column("group_guid", Text, nullable=True)` to `publication` after `is_fresh`, with a comment: "Group item GUID (story 5.2), built once at decision time; NULL when the row is published on its own or older than this column." No unique constraint (members share it), no index (the feed query is by endpoint and `published_at`).
  - [x] 3.2 New migration `src/cvestream/state/migrations/versions/0004_add_publication_group_guid.py`: copy the shape of `0003_add_publication_is_fresh.py` (`revision = "0004"`, `down_revision = "0003"`, `op.batch_alter_table("publication")`, `add_column(sa.Column("group_guid", sa.Text(), nullable=True))`, downgrade `drop_column`). Never edit `0001` to `0003`.
  - [x] 3.3 `src/cvestream/state/repository.py`:
    - `add_publication(self, vulnerability_id, decision, published_at, *, group_guid: str | None = None)`: writes `group_guid`. A keyword argument rather than a `PublicationDecision` field: the decision stays what `decide()` returns, and the group is a separate fact.
    - `StoredPublication.group_guid: str | None` as the **last** field (it has no default today; add it after `is_fresh` and update every constructor call, there are only the repository and tests).
    - `publications_since()`: select and map `group_guid`.
    - `publication_history()` / `PastPublication`: **unchanged** (AC 7: a group never changes later decisions).
  - [x] 3.4 Tests:
    - `tests/unit/state/test_db.py`: every hard-coded head `"0003"` becomes `"0004"` (`test_migrate_creates_the_schema_at_head`, `test_second_migrate_is_a_no_op`, the `0001` and `is_fresh` migration tests). Add `test_the_group_guid_migration_keeps_rows_and_can_be_downgraded` next to `test_the_is_fresh_migration_keeps_rows_and_can_be_downgraded`, with the `_upgrade_to(state, revision)` helper: migrate to `0003`, insert a publication row, upgrade to head, read `group_guid` as `None`, downgrade to `0003`, check the column is gone and the row is kept.
    - `tests/unit/cli/test_db.py`: `"at revision 0003"` and `("0003",)` become `0004`.
    - `tests/unit/state/test_repository.py`: `group_guid` round trip (a value and `NULL`), next to the `is_fresh` one.

- [x] **Task 4: pipeline (AC 2, 6, 7, 8)**
  - [x] 4.1 `src/cvestream/pipeline/run.py`, inside the existing per-endpoint loop (same transaction, no new one):
    - Collect `accepted: list[tuple[int, PublicationDecision]]` instead of writing each decision at once.
    - Safety net: skip (with the existing `publish.skipped`, `reason=guid_collision` warning) a decision whose GUID is already stored **or already in a run-local `set` of GUIDs accepted earlier in this run**. Today the second check is implicit, because the row is inserted before the next decision is checked; with deferred writes, two advisory-only vulnerabilities sharing a primary ID in one run would hit the unique index and roll back this run and every next one. `test_a_guid_collision_in_one_run_is_skipped_and_logged` pins this and must stay green unchanged.
    - When `endpoint.rules.group_by_product`: `groups = group_decisions([d for _, d in accepted])`; map each member GUID to its group GUID.
    - Write each accepted decision with `repository.add_publication(vulnerability_id, decision, now, group_guid=...)` in the same order as today (vulnerability ID order), append it to `decided`, log `publish.decided` as today (add `group_guid=` only when it is not `None`).
    - Log one `publish.grouped` (info) per group: `endpoint_id`, `family`, `count` (members), `part`, `parts`, `group_guid`. Never a token or a feed path.
  - [x] 4.2 Rendering in `run()`: pass `group_guid=p.group_guid` to `FeedEntry`. The stored item count (`StoredFeed.item_count`, `RenderedFeed.item_count`, `/status` `item_count`) becomes the number of **rendered items** (a group counts once): use the render helper of Task 5 (`count_items(entries)`), not `len(entries)`.
  - [x] 4.3 `RunResult.decisions` keeps one entry per member (`cycle.summary` `published`, `score_unknown`, `versions_unknown` keep counting vulnerabilities; see open question 4). `pipeline/summary.py` needs no change.
  - [x] 4.4 Tests in `tests/unit/pipeline/test_run.py` (helpers `_endpoint`, `_euvd`, `_batch`, `_guids`, `FakeClock`). Add a `group_by_product` parameter to `_endpoint()` or build `Rules` directly:
    - 3 EUVD records matching one entry, rule on → 3 rows with the same non-null `group_guid`, 3 `decisions`, the feed has **one** item whose GUID starts with `cvestream:<endpoint>:group-new:`; `publish.grouped` logged once.
    - Same with the rule off → 3 items, `group_guid` all `NULL` (AC 6).
    - 2 records → 2 single items; 3 records with one known-exploited (add a KEV record) → 3 single items (2 left, below the threshold).
    - 6 records → two group items (`part 1 of 2`, `part 2 of 2`), feed item count 2.
    - Replay of the same batches → no decision, same XML, same ETag (AC 8).
    - Band rise of all 3 members in a later run → one `group-band` item; then one member becomes known-exploited → a single `kev` item, the two earlier group items unchanged (AC 7).
    - Rule switched off after grouping → same XML and ETag on the next run (AC 9).
    - Input order: the same 3 records in reversed batch order on a fresh state → the same group GUID (AC 8).
    - A GUID collision inside one run with the rule on is still skipped (reuse `_advisory_revision`).
  - [x] 4.5 `tests/integration/test_restart_no_repost.py`: keep the existing test **unchanged**, add `test_nothing_is_posted_twice_across_restarts_when_grouped` with `ENDPOINT` + `Rules(group_by_product=True)`. The recorded EUVD search page yields three Chrome records, which is exactly the threshold. Assert one group item after the first run; then restart, replay, and the KEV escalation, with the same GUID set as before plus the single `kev` item.

- [x] **Task 5: render (AC 5, 8, 9)**
  - [x] 5.1 `src/cvestream/pipeline/render.py`:
    - `FeedEntry.group_guid: str | None = None` as the **last** field.
    - A private `_Group` (frozen dataclass): `guid`, `members: tuple[FeedEntry, ...]` (ordered with `member_order_key(e.band, e.primary_id)` then `e.guid`), `part`, `parts`.
    - `_items(entries) -> list[FeedEntry | _Group]`: entries without `group_guid` stay as they are; entries sharing a `group_guid` collapse into one `_Group` placed at the position of its first entry (entries come newest first; all members share `published_at`).
    - Part numbering, worked out from stored values only: the sibling chunks of a burst are the groups of the same endpoint feed with the same `kind_family` of their members, the same `products[0]` and the same `published_at` (one run = one `Clock` value). Order the siblings by the order key of their first member; `part` = 1-based position, `parts` = number of siblings. One sibling → no `part` suffix.
    - `count_items(entries) -> int`: `len(_items(entries))`, public, used by `run.py` (Task 4.2).
    - `_group_title(group, endpoint)`: as AC 5. The product label is `_title_product(first member, endpoint)`. Counts per band from the members' **stored** bands, worst first, e.g. `(1 critical, 3 high, 1 medium)`. `new` family: `<label>: <n> new vulnerabilities, worst <band> (<counts>)`. `band` family: `[UPDATE] <label>: <n> vulnerabilities raised (<counts>)`. `vulnerability` when `n == 1`. Then `, part i of k` when `parts > 1`. Markers via a `_group_markers()` that mirrors `_markers()` (escapes `\N{LARGE RED CIRCLE}`, `\N{UP-POINTING RED TRIANGLE}`, `\N{SQUARED NEW}`; ruff RUF001 forbids literal emoji in code).
    - `_member_line(entry)`: `<primary ID> · <band> <score> · exploited: <yes|no> · <versions>`. The primary ID is the stored `primary_id` (as in the title). Band and score come from current merged data (`band_of(max_score(...))`, score with one decimal; when the score is unknown the part reads just `unknown`). Exploited from current data. `<versions>` is the first version text of the affected label that the title product names (reuse the `_affected()` grouping: factor out a helper that returns `[(label, rows)]` so both use one implementation), then ` (+N more)` when that label has more rows; `versions unknown` when it has none or the vulnerability has no structured affected data. The ID is an `_anchor()` to the member's `_link(_ordered_records(...))` when `_safe_url()` accepts it, otherwise escaped text. Every value goes through `_h()`.
    - `_group_description(group)`: one `<p>` holding every member line joined by `<br/>\n`. Nothing else is required; do not add a second `<p>` before the lines (Slack keeps only the first block-level elements).
    - `_group_item(channel, group, endpoint)`: `title`, `link` (the most severe member's link, omitted when it has none), `description`, `guid` = group GUID with `isPermaLink="false"`, `pubDate` = the members' `published_at`.
    - `render_feed()`: iterate `_items(entries)`. `lastBuildDate` stays the newest `published_at` over all entries.
    - Module docstring: one paragraph on group items (stored groups always render as groups, whatever the current rule).
  - [x] 5.2 Tests in `tests/unit/pipeline/test_render.py` (helper `_entry(**changes)`, plain-text reduction helper from story 5.1):
    - Golden group title and body for a 5-member Tomcat burst (1 critical fresh, 3 high, 1 medium): `🔴🆕 Apache Software Foundation Apache Tomcat: 5 new vulnerabilities, worst critical (1 critical, 3 high, 1 medium)`; body lines in order, first `<p>` only, each ID linked.
    - Golden update title: `🔴🔺 [UPDATE] Apache Software Foundation Apache Tomcat: 4 vulnerabilities raised (2 critical, 2 high), part 2 of 2` (and part 1).
    - No marker → no leading space (`Apache Tomcat: 3 new vulnerabilities, worst high (3 high)`); 🆕 when **one** member is fresh; `is_fresh=None` members give no 🆕; a chunk of 1 reads `1 new vulnerability`.
    - Members given in any order render in the same order and the same XML (shuffle the entries' order; same bytes).
    - Mixed feed: a group between two single items keeps its position, single items unchanged; `count_items` counts the group once.
    - Two bursts of the same product in two runs (different `published_at`) never number each other's parts.
    - Member line: unknown score reads `unknown`; no affected data reads `versions unknown`; a label with 5 rows reads `<first> (+4 more)`; markup in a version text or an ID is escaped; a non-http link is not an anchor.
    - Budget: for the 5-member golden group, the first `SLACK_PREVIEW_CHARS` plain-text characters contain all 5 primary IDs.
    - All existing single-item tests stay green unchanged (`group_guid` defaults to `None`).

- [x] **Task 6: user documentation (same PR)**
  - [x] 6.1 `docs/configuration.md`: `group_by_product: false` in the YAML example after `require_score`; a table row `endpoints[].group_by_product | no | default false; publish 3 or more items of one run that share a matched product and kind as one item (at most 5 per item)`; a **Grouping** paragraph after "Score rules": the key (the first matched entry name, unique per endpoint since story 5.4), the families (`new`; severity raised), the threshold and the 5-member split, known-exploited never grouped, the order inside an item, that a later KEV or severity update of a member comes on its own, that switching the rule on or off never changes items already published, and that a new endpoint's 7-day backfill is grouped too. Add `group_by_product: "yes"` to the strict-contract list in "YAML pitfalls".
  - [x] 6.2 `docs/operations.md`, "Feeds": a bullet for group items (title format with the golden examples, member lines, `part i of k`, GUID `…:group-new:<16 hex>` / `group-band`, members keep their own rows). `/status` `item_count` counts a group as one item; `cycle.summary` `published` counts each member (adjust the `published` bullet wording in "Logs"). Add a `publish.grouped` row to the Logs events table (after `publish.decided`: info, a group item was published, fields `endpoint_id`, `family`, `count`, `part`, `parts`, `group_guid`), and add `group_guid` to the `publish.decided` fields when present.
  - [x] 6.3 `README.md`: one clause where the intro describes the feed (optional grouping of same-product bursts).
  - [x] 6.4 `CHANGELOG.md` `[Unreleased]` → `### Added`: `group_by_product` endpoint rule (FR-27), additive change to the v1 contract, the new nullable `publication.group_guid` column applied by Alembic migration `0004` at startup, existing rows `NULL`, nothing posted again.

- [x] **Task 7: planning docs (same PR, "changing a pattern means amending architecture.md")**
  - [x] 7.1 `architecture.md`, "Naming Patterns" identifiers table, RSS GUID row: replace it with the NEW row of the sprint change proposal (section 4.3), which was **not** applied yet: `… ; a group item (story 5.2): cvestream:{endpoint_id}:group-{family}:{h} with family = new or band, h = 16 hex of SHA-256 over the sorted member GUIDs` and the example `cvestream:team-gitlab:group-new:3f9c0a17b2d4e851`. In the paragraph under the table, add `publication.group_guid` next to `publication.guid`.
  - [x] 7.2 `architecture.md`, "Feed cache" rules (story 1.6): add the group item rules (one item per stored `group_guid`, group title and member lines, part numbering from stored values, a stored group always renders as a group whatever the current rule).
  - [x] 7.3 `architecture.md`, "Correlation and publication rules": the grouping bullet (story 5.2) is already there. Amend it only for what the implementation decides (the `\n` join of the hash input, the known-exploited filter applied before the threshold).
  - [x] 7.4 `project-context.md`, "Publish once": the GUID rule is already amended. Add: "Grouping is a pure step after `decide()` (`group_decisions()`); stored groups always render as groups, never regrouped at render time or when the rule changes."
  - [x] 7.5 `sprint-status.yaml`: `5-2-group-bursts-by-product: in-progress` at start, `review` at the end.

- [x] **Task 8: gates and record**
  - [x] 8.1 Run every gate command of AGENTS.md (the docker build may fail locally on the ghcr.io x509 proxy error: CI is authoritative, say so in the record).
  - [x] 8.2 Fill the Dev Agent Record with real commit hashes. Commits (Conventional Commits, `story-5.2`, one concern each, never squashed), suggested: `feat(story-5.2): add the group_by_product endpoint rule` (models + schema + example yaml + config tests) · `feat(story-5.2): group a run's same-product decisions in the domain` · `feat(story-5.2): store each member's group guid` (schema, migration 0004, repository) · `feat(story-5.2): group decisions in the pipeline run` · `feat(story-5.2): render a stored group as one feed item` · `docs(story-5.2): document group_by_product and group items` · `docs(story-5.2): amend the architecture for group items` · `chore(story-5.2): mark story ready for review`.

## Dev Notes

### Scope boundaries (read first)

- **`decide()` does not change.** Grouping is a second pure step over the decisions it returned. `PastPublication` and `publication_history()` do not change: a group never influences a later decision (AC 7).
- **Grouping happens at decision time, never at render time.** Grouping at render time was rejected in the sprint change proposal: it would change which item GUIDs appear, and Slack would post them again. The renderer only collapses rows that share a **stored** `group_guid`.
- **No regrouping, ever.** Do not consult `endpoint.rules.group_by_product` in the renderer. Switching the rule changes only future runs (AC 9).
- **Out of scope:** the Valkey provider and the CLI (epic 3, still in the backlog; the sprint change proposal notes that story 3.2's CLI must expose `group_by_product` when it is created); any change to the single-item format of story 5.1; any change to `cycle.summary` fields (story 5.3 owns `held_back`).

### Stack and parallel work

- The branch is based on the story 5.4 branch (wixyvir/cvestream#50), which was rebased on `main` (after story 5.1's merge, wixyvir/cvestream#51) on 2026-11-22. Open the PR against `story-5-4-reject-duplicate-product-names`; retarget it to `main` when wixyvir/cvestream#50 merges.
- Why 5.4 first: groups are keyed on the matched entry **name**. Story 5.4 rejects two entries of one endpoint whose names are equal after stripping and case-folding, so a name identifies one entry. Group on the exact stored string `products[0]` (unique per endpoint under 5.4; an entry renamed between runs only affects later runs, and one run sees one configuration).
- Story 5.3 (wixyvir/cvestream#49) edits `pipeline/run.py` (it threads a drop reason out of `match()` for `held_back`), `pipeline/summary.py`, `domain/matcher.py` and `tests/unit/pipeline/test_run.py`. Expect a textual conflict in the per-endpoint loop of `run()`. Whichever story lands second rebases and keeps both behaviours.
- Migration order: `0003` (story 5.1, merged) then `0004` (this story). No other open branch adds a revision.

### Domain design (`src/cvestream/domain/publication.py`)

Current state: `decide(match, vulnerability, history, now)` returns at most one `PublicationDecision` and is the only GUID construction site (`cvestream:{endpoint_id}:{primary_id}:{kind}`). `PublicationDecision` fields: `endpoint_id, primary_id, kind, guid, band, is_known_exploited, products, labels, is_fresh=False`. `NEW`, `KEV`, `band_kind()`, `BACKFILL_WINDOW`, `PUBLICATION_WINDOW` are public.

Change: add the grouping step (Task 2.2). Preserve `decide()`, `_kind()`, `_is_fresh()`, the GUID format of single items, and purity (no clock, no I/O, no logging).

Sketch (adapt names freely, keep the rules):

```python
def _group_guid(endpoint_id: str, family: str, member_guids: Iterable[str]) -> str:
    # The only group GUID construction site: computed once, stored in every
    # member's `publication.group_guid`, never recomputed.
    digest = hashlib.sha256("\n".join(sorted(member_guids)).encode("utf-8"))
    return f"cvestream:{endpoint_id}:group-{family}:{digest.hexdigest()[:16]}"


def group_decisions(
    decisions: Sequence[PublicationDecision],
) -> tuple[PublicationGroup, ...]:
    buckets: dict[tuple[str, str, str], list[PublicationDecision]] = {}
    for d in decisions:
        family = kind_family(d.kind)
        if family is None or d.is_known_exploited or not d.products:
            continue
        buckets.setdefault((d.endpoint_id, d.products[0], family), []).append(d)
    groups: list[PublicationGroup] = []
    for (endpoint_id, product, family), members in buckets.items():
        if len(members) < GROUP_MIN_MEMBERS:
            continue
        members.sort(key=lambda d: (member_order_key(d.band, d.primary_id), d.guid))
        chunks = list(batched(members, GROUP_MAX_MEMBERS))
        groups.extend(
            PublicationGroup(
                guid=_group_guid(endpoint_id, family, (m.guid for m in chunk)),
                endpoint_id=endpoint_id, family=family, product=product,
                members=chunk, part=index, parts=len(chunks),
            )
            for index, chunk in enumerate(chunks, start=1)
        )
    return tuple(sorted(groups, key=lambda g: (
        member_order_key(g.members[0].band, g.members[0].primary_id),
        g.family, g.product, g.part,
    )))
```

Known-exploited filter: a known-exploited decision is removed **before** the threshold is counted (3 Tomcat `new` decisions, one of them known-exploited → 2 candidates → no group). This is the reading of FR-27 ("A known-exploited vulnerability is never grouped") recorded as open question 2.

`kev` decisions always carry `is_known_exploited=True`, and `kind_family("kev")` is `None`: they are excluded twice, which is fine.

Natural order: `identity._sort_key` already sorts CVE and EUVD numerically (`CVE-2026-9999` < `CVE-2026-10000`). Advisory IDs sort as `(0, 0, alias)`, so before CVEs of the same band; that is deterministic and acceptable (mixed groups are rare: a CERT-EU-only vulnerability has `unknown` band, sorted last anyway).

### State design

- `publication.group_guid`: `Text`, nullable, after `is_fresh`. Portable type, no dialect SQL, named by the column convention (no `_json`, no `is_`). No constraint name to write by hand.
- `StoredPublication.group_guid: str | None`; `FeedEntry.group_guid: str | None = None`.
- Writes stay inside `State.transaction()` opened by `pipeline/run.py`. No new transaction.
- `merge_vulnerabilities()` re-points publication rows to the survivor; a group can then hold two rows of one vulnerability. Render them as stored (each row has its own GUID); this is the same edge case single items already have. Do not dedupe.

### Pipeline design (`src/cvestream/pipeline/run.py`)

Current per-endpoint loop: for each candidate vulnerability (sorted IDs), `match()` → `decide()` → `publication_guid_exists()` safety net → `add_publication()` → `decided.append()` → `publish.decided` log. Rendering then reads `publications_since()` for every enabled endpoint, builds one `FeedEntry` per row, `render_feed()`, `etag_of()`, `save_feed()` with `len(entries)` as the item count.

Change: decide and check every candidate first, keep the accepted ones, group them when the rule is on, then write them. Preserve: one transaction, the backfill and history rules, the order of `decided`, the `publish.skipped` warning, the render of every enabled endpoint, the `changed_at` rule.

### Render design (`src/cvestream/pipeline/render.py`)

Current state (story 5.1): `_title()` with `_markers()`, `_title_product()`, `_description()` whose first `<p>` holds the facts line, `Matched:` and `Description:`, `_affected()` grouping labels case-insensitively with at most 3 rows then `(+N more)`, `_ordered_records()`, `_link()`, `_safe_url()`, `_anchor()`, `_h()`, `SLACK_PREVIEW_CHARS = 495`. Pure, clock-free.

Change: Task 5.1. Preserve everything about single items byte for byte: their XML must not change (story 5.1's golden tests are the check), so no existing feed changes its ETag unless it gets a new group.

Golden group body (plain text, ID links stripped; versions from the 5.1 Tomcat fixture style):

```
CVE-2026-95517 · critical 9.1 · exploited: no · 10.1.0-M1 ≤10.1.61 (+4 more)
CVE-2026-95518 · high 7.5 · exploited: no · 11.0.0-M1 ≤11.0.27 (+1 more)
…
```

Why one `<p>`: story 5.1 Task 1 inferred that Slack's RSS app keeps only the first block-level elements (`architecture.md`, "Feed cache" rules). Five member lines are about 350 to 400 plain-text characters, inside the 495-character window.

Part numbering at render time: two chunks of one burst share endpoint, family, `products[0]` and `published_at`, because one run writes them with one `now`. Chunk 1 holds the most severe members, so ordering siblings by their first member's order key gives back the split order. Caveat: tests that run twice with a frozen `FakeClock` could produce two bursts with the same `published_at`; advance the clock between runs in tests.

### Testing requirements

- Domain tests are pure (no database, no mocks of lower layers): build `PublicationDecision` directly.
- No real time, no sleeps: `FakeClock` from `tests/conftest.py`; advance it between runs.
- Network blocked (pytest-socket); the integration test reuses the recorded EUVD and CISA KEV fixtures with respx. No new fixture is needed (fixtures are recorded, never hand-written; render tests build domain objects in code, as story 5.1 did).
- Guardrails that must stay green unchanged: `tests/integration/test_restart_no_repost.py::test_nothing_is_posted_twice_across_restarts`, `tests/unit/domain/test_purity.py`, story 5.1's golden render tests, `test_a_guid_collision_in_one_run_is_skipped_and_logged`, `test_a_rerun_past_midnight_keeps_the_xml_the_etag_and_changed_at`.
- Cover the unknown paths in member lines (score, versions) and the `NULL` `group_guid` of old rows.

### Documentation edits

See Tasks 6 and 7. The "YAML pitfalls" sentence in `docs/configuration.md` lists the strict booleans; add `group_by_product: "yes"`. `docs/config-contract.md` does not exist; the contract lives in `docs/configuration.md` and `schemas/config-v1.json`.

### Existing files this story touches (current state → change → preserve)

| File | Current state | Change | Preserve |
|---|---|---|---|
| `src/cvestream/domain/endpoint.py` | `Rules(min_cvss, kev_only, require_score)` | `group_by_product: bool = False` last | `Rules()` = no filtering |
| `src/cvestream/config/models.py` | `EndpointConfig` with `require_score`, `_unique_product_names` (5.4) | field + `to_domain()` mapping | strictness, 5.4 validator, token `repr=False` |
| `schemas/config-v1.json`, `endpoints.example.yaml` | v1 | regenerated / one commented key | `SCHEMA_VERSION = 1` |
| `src/cvestream/domain/identity.py` | private `_sort_key` | public `alias_sort_key` | `primary_id()` behaviour |
| `src/cvestream/domain/publication.py` | `decide()`, single GUID | grouping step, group GUID | `decide()`, purity |
| `src/cvestream/state/schema.py` | `publication` up to `is_fresh` | `group_guid` nullable `Text` | naming convention |
| `src/cvestream/state/migrations/versions/` | `0001` to `0003` | new `0004_add_publication_group_guid.py` | never edit merged revisions |
| `src/cvestream/state/repository.py` | `add_publication`, `StoredPublication`, `publications_since` | `group_guid` written, mapped, selected | `publication_history`, `merge_vulnerabilities` |
| `src/cvestream/pipeline/run.py` | write per decision; `len(entries)` item count | collect → group → write; run-local GUID set; `count_items()` | one transaction, safety net, render every endpoint |
| `src/cvestream/pipeline/render.py` | one item per row | collapse stored groups, group title/body | single-item XML byte for byte, purity |
| `tests/unit/state/test_db.py`, `tests/unit/cli/test_db.py` | head `0003` | head `0004`, new migration test | the other migration tests |

### Architecture compliance

- `domain` stays pure: `hashlib`, `itertools`, `dataclasses` are stdlib. The group GUID is built only in `domain/publication.py`, stored once, read by the renderer, never recomputed.
- State written only inside `State.transaction()`, opened only by `pipeline/run.py`; one transaction per run.
- Portable column, Alembic batch mode, a new revision.
- Render is pure and clock-free; ETag stability (AC 8) follows.
- `config/models.py` is a public contract: schema regenerated in the same commit.
- Logs: `publish.grouped` follows `<area>.<past-participle>`; no token, no feed path.
- Unknown is `None` (`group_guid` on old rows) and displays `unknown` (member lines).

### Library / framework requirements

No new dependency. stdlib `hashlib` (SHA-256), `itertools.batched` (Python 3.12+, already used in `state/repository.py`), `dataclasses`; SQLAlchemy 2.1 Core `Text`; Alembic 1.20 `op.batch_alter_table`; Pydantic 2.14 strict `bool`. Use `uv run` for everything.

### Previous story intelligence

- **Story 5.1** (wixyvir/cvestream#51, merged): added `decide(..., now)`, `is_fresh` (last field of `PublicationDecision` and `FeedEntry`), migration `0003`, the facts-line body, `_markers()`, `SLACK_PREVIEW_CHARS`. Lessons: add new dataclass fields **last** with defaults so existing constructions keep working; Slack keeps only the first block-level elements (put everything important in the first `<p>`); emoji in code are `\N{...}` escapes (ruff RUF001); review decision D1 restricts 🔺 to rises to `medium` or above; D3 groups Affected labels case-insensitively. Its review added `fix(story-5.1)`, `test(story-5.1)` and `chore(story-5.1): record the review fixes` commits: expect the same here.
- **Story 5.4** (wixyvir/cvestream#50, in review, this branch's base): `EndpointConfig._unique_product_names` rejects duplicate names (stripped, case-folded) with the path in the message. Its CHANGELOG entry sits under `### Changed` next to 5.1's.
- **Story 4.1** (`require_score`): the model for adding a strict bool endpoint rule end to end (domain `Rules`, `EndpointConfig`, schema, example yaml, docs table, YAML pitfalls, tests with `"yes"`/`1`).
- **Story 1.6**: ETag and `changed_at` rules; `test_an_unchanged_rerun_keeps_the_etag_and_moves_the_render_time` is the model for AC 8/9 tests.

### Git intelligence

- `main` is at `cf0ce3a` (merge of wixyvir/cvestream#51). The base branch adds five story 5.4 commits (`3c1dbad` … `cf57527`), rebased on 2026-11-22 (conflicts resolved in `sprint-status.yaml` and `CHANGELOG.md`; every gate but the docker build re-run green: 1272 passed).
- Recent pattern: `feat` commits per layer, then `docs`, then `chore(story-x.y): mark story ready for review`.

### Latest technical information

No external API or library change is involved. `hashlib.sha256(...).hexdigest()` returns lowercase hex; `itertools.batched(iterable, n)` yields tuples (Python 3.12+; `strict=` exists since 3.13 and defaults to `False`).

### Open questions (decided by default, to confirm in review)

1. **🔺 on a group of rises below `medium`.** The epic AC says "🔺 for the `band-*` family", written before review decision D1 of wixyvir/cvestream#51 restricted 🔺 on single items to rises to `medium` or above. Default: apply D1 to groups too, so 🔺 appears when the family is `band` and the chunk's worst band is `medium` or above. Alternative: 🔺 on every `band` group, as the epic text says.
2. **Known-exploited and the threshold.** Default: known-exploited decisions are removed before counting, so 3 decisions with 1 known-exploited form no group. Alternative: count all 3 and group the 2 others (rejected by default: a "group" of 2 contradicts the threshold).
3. **Member line values.** Default: the stored primary ID and order (decision time, like the title), current band, score, exploited flag and versions (like every body). Alternative: everything from decision time (would need the stored score, which is not stored).
4. **`cycle.summary` `published`.** Default: keeps counting vulnerabilities (members), plus a `publish.grouped` event per group. Alternative: add a per-endpoint `items` count (would touch `summary.py`, also edited by story 5.3).
5. **Hash input encoding.** Default: member GUIDs sorted, joined with `\n`, UTF-8. GUIDs contain no newline, so the join is unambiguous. Pinned by a golden test; architecture amended.

### Project Structure Notes

- One new file in `src/`: `state/migrations/versions/0004_add_publication_group_guid.py`. No new module or package; the grouping step lives in `domain/publication.py`, the group render helpers stay private in `pipeline/render.py` (no `utils.py`).
- Tests go in `tests/unit/domain/`, `tests/unit/state/`, `tests/unit/pipeline/`, `tests/unit/config/`, `tests/unit/cli/`, `tests/integration/`.
- No conflict with the unified structure detected.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.2: Group bursts by product]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-21-epic-5-readable-bursts.md] (sections 1 "Bursts", 2 "Technical impact", 4.3 GUID row, 4.4, 4.5, 5)
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md] (FR-27, FR-19)
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Architecture] ("Correlation and publication rules", the grouping bullet; "Feed cache" rules), [#Naming Patterns] (identifiers table, migrations), [#Requirements to Structure Mapping] (FR-27)
- [Source: _bmad-output/project-context.md#Critical Don't-Miss Rules] ("Publish once")
- [Source: _bmad-output/implementation-artifacts/5-1-compact-item-body-and-title-markers.md] (render design, Task 1 finding, D1/D3)
- [Source: _bmad-output/implementation-artifacts/5-4-reject-duplicate-product-names.md]
- [Source: src/cvestream/domain/publication.py], [src/cvestream/domain/identity.py], [src/cvestream/domain/endpoint.py], [src/cvestream/config/models.py], [src/cvestream/pipeline/run.py], [src/cvestream/pipeline/render.py], [src/cvestream/state/schema.py], [src/cvestream/state/repository.py], [src/cvestream/state/migrations/versions/0003_add_publication_is_fresh.py]
- [Source: tests/unit/pipeline/test_run.py], [tests/unit/pipeline/test_render.py], [tests/unit/state/test_db.py], [tests/integration/test_restart_no_repost.py]
- [Source: docs/configuration.md], [docs/operations.md#Feeds], [endpoints.example.yaml], [CHANGELOG.md], [README.md], [AGENTS.md] (gate commands)

## Dev Agent Record

### Agent Model Used

- Mistral Vibe (Mistral AI), 2026-11-23, branch `story-5-2-group-bursts-by-product` based on `story-5-4-reject-duplicate-product-names` (`cf57527`).

### Debug Log References

- All gates run locally on 2026-11-23 and green: `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports` (7 contracts kept), `pytest` (1316 passed, 6 deselected), `cvestream config schema --check`, `cvestream config validate endpoints.example.yaml`, and `docker build --target runtime -t cvestream:local .` (succeeded locally here; CI stays authoritative).
- GUID golden value pinned in `tests/unit/domain/test_publication.py` (`cvestream:team:group-new:f92dff6898666ea1` for the four fixed member GUIDs).
- One test-design fix during the run: the group body follows current merged data (like single items), so AC 7 is asserted on GUID and decision-time title stability, not on byte-identical items when a member's score changed.

### Completion Notes List

- Story context created by the create-story workflow (2026-11-22): ultimate context engine analysis completed, comprehensive developer guide created from epic 5, the sprint change proposal of 2026-11-21, the PRD (FR-27), the architecture, the project context, stories 5.1 and 5.4, and the current code.
- 2026-11-23: implemented all 8 tasks; every AC covered by tests (config contract, grouping/split/GUID purity, migration 0004 upgrade+downgrade, pipeline collect-group-write with the run-local GUID safety net, group render with part numbering from stored values, unchanged-path/replay/no-regroup guardrails, restart integration test). Commits: `9a54cc3` rule · `de6bf49` domain · `7879903` state · `afd0248` render · `a9a0353` pipeline · `99eca1a` user docs · `885578a` architecture. All 40 task checkboxes checked; status review.

### File List

- `src/cvestream/domain/endpoint.py` (Rules.group_by_product)
- `src/cvestream/domain/identity.py` (public `alias_sort_key`)
- `src/cvestream/domain/publication.py` (`group_decisions`, `PublicationGroup`, `kind_family`, `member_order_key`, `_group_guid`)
- `src/cvestream/config/models.py` (`EndpointConfig.group_by_product` + `to_domain()`)
- `schemas/config-v1.json` (regenerated, SCHEMA_VERSION stays 1)
- `endpoints.example.yaml` (commented key)
- `src/cvestream/state/schema.py` (`publication.group_guid`)
- `src/cvestream/state/migrations/versions/0004_add_publication_group_guid.py` (new)
- `src/cvestream/state/repository.py` (`add_publication(group_guid=...)`, `StoredPublication.group_guid`, `publications_since`)
- `src/cvestream/pipeline/run.py` (collect → group → write, run-local GUID set, `publish.grouped`, `count_items`)
- `src/cvestream/pipeline/render.py` (`FeedEntry.group_guid`, `_Group`, `_items`, `count_items`, group title/body/item)
- `tests/unit/config/test_models.py`, `tests/unit/domain/test_endpoint.py`, `tests/unit/domain/test_identity.py`, `tests/unit/domain/test_publication.py`, `tests/unit/state/test_db.py`, `tests/unit/state/test_repository.py`, `tests/unit/cli/test_db.py`, `tests/unit/pipeline/test_run.py`, `tests/unit/pipeline/test_render.py`, `tests/integration/test_restart_no_repost.py`
- `docs/configuration.md`, `docs/operations.md`, `README.md`, `CHANGELOG.md`
- `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Change Log

- 2026-11-22: story created (ready-for-dev), based on the story 5.4 branch rebased on `main`.
- 2026-11-23: implemented (commits `9a54cc3` … `885578a`), all gates green locally, status review.
