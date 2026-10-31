# Recorded fixtures

Fixtures are recorded from the real APIs, never hand-written and never edited
by value. A payload that is too big is reduced with `jq` by selecting whole
records (the `total` field stays as the API returned it). Malformed payloads are
not stored here: tests derive them from these files.

Recorded on 2026-10-26 (EUVD at about 20:00 UTC). `B=https://euvdservices.enisa.europa.eu/api`.
`D=2026-10-20` is a past, closed day, so its pages are stable. Behind a
TLS-intercepting proxy, point `curl` at your CA bundle in your own shell only.

## `euvd/`

| Fixture | How it was recorded |
|---|---|
| `euvd_search_page0.json` | `curl "$B/search?fromUpdatedDate=$D&toUpdatedDate=$D&size=3&page=0" \| jq .` (total 1519) |
| `euvd_search_page1.json` | same query, `page=1` (recorded right after page 0) |
| `euvd_search_last_page.json` | same query, `page=506` (`ceil(1519 / 3) - 1`): a short last page of 1 item |
| `euvd_no_score.json` | `curl "$B/search?fromScore=0&toScore=0&fromUpdatedDate=$D&toUpdatedDate=$D&size=3&page=0" \| jq .` (`baseScore` 0.0, no `baseScoreVersion`; total 51) |
| `euvd_total_boundary.json` | same query, `page=16`: exactly 3 items and `(16 + 1) * 3 == total` (the `total` stop condition) |
| `euvd_version_na.json` | 2 records with a real product and `product_version` `"-"`: the 4 pages `fromUpdatedDate=2026-10-26&size=100&page=0..3` concatenated, then `jq -s '{items: ([.[].items[] \| select(any(.enisaIdProduct[]?; (.product_version=="n/a" or .product_version=="-") and .product.name!="n/a"))] \| .[0:2]), total: .[0].total}'` |
| `euvd_no_product.json` | 3 records, one with an empty `enisaIdProduct` (EUVD-2026-104983) and two with product, vendor and version all `"n/a"`: the 16 pages `fromUpdatedDate=$D&toUpdatedDate=$D&size=100&page=0..15` in page order, then `jq -s '{items: [.[].items[] \| select(.id=="EUVD-2026-104983" or .id=="EUVD-2025-211112" or .id=="EUVD-2025-211129")], total: .[0].total}'` |
| `euvd_epss_zero.json` | 2 records with `epss` 0.0, a positive score and a product: same 4 pages as `euvd_version_na.json`, then `jq -s '{items: ([.[].items[] \| select(.epss==0 and .baseScore>0 and (.enisaIdProduct\|length>0))] \| .[0:2]), total: .[0].total}'` |
| `euvd_exploited.json` | `curl "$B/search?exploited=true&size=3&page=0" \| jq '.items \|= .[0:1]'`: one record carrying `exploitedSince` (ignored by the adapter) |
| `euvd_kev_dump.json` | `curl "$B/kev/dump" \| jq '[.[] \| select(.cveId=="CVE-2021-22555" or .cveId=="CVE-2023-3519" or .cveId=="CVE-2025-25231" or .cveId=="CVE-2026-67276" or .cveId=="CVE-2026-67277" or .cveId=="CVE-2020-1472")]'`: CISA-only (CVE-2021-22555), both lists (CVE-2023-3519, CVE-2020-1472, CVE-2026-67277), EU-only (CVE-2025-25231, CVE-2026-67276), two with `vulnerabilityName` |

## `cisa_kev/`

| Fixture | How it was recorded |
|---|---|
| `cisa_kev_catalog.json` | `curl "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json" \| jq '.vulnerabilities \|= map(select(.cveID=="CVE-2026-114625" or .cveID=="CVE-2023-3519" or .cveID=="CVE-2020-1472" or .cveID=="CVE-2021-22555"))'`: `catalogVersion` 2026.10.23, `count` 1756 as returned; two `Known` and two `Unknown` entries |
