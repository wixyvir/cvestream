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
| `euvd_php_branch_wildcard.json` | Recorded on 2026-11-03: `curl "$B/search?text=EUVD-2026-107260&size=10&page=0" \| jq .` (PHP2Shell, CVE-2026-109618; total 1): four `PHP` rows in the PHP CNA branch-wildcard format `8.2.* <8.2.35` … `8.5.* <8.5.12` (wixyvir/cvestream#11) |
| `euvd_php2shell_second_cve.json` | Recorded on 2026-11-03: `curl "$B/search?text=EUVD-2026-107265&size=10&page=0" \| jq .` (PHP2Shell, CVE-2026-109619; total 1): the second CVE of CERT-EU 2026-018, replayed by `tests/integration/test_cert_eu_php2shell.py` |
| `euvd_kev_dump.json` | `curl "$B/kev/dump" \| jq '[.[] \| select(.cveId=="CVE-2021-22555" or .cveId=="CVE-2023-3519" or .cveId=="CVE-2025-25231" or .cveId=="CVE-2026-67276" or .cveId=="CVE-2026-67277" or .cveId=="CVE-2020-1472")]'`: CISA-only (CVE-2021-22555), both lists (CVE-2023-3519, CVE-2020-1472, CVE-2026-67277), EU-only (CVE-2025-25231, CVE-2026-67276), two with `vulnerabilityName` |
| `euvd_tomcat_five_affected_rows.json` | Recorded on 2026-11-21: `curl "$B/search?text=CVE-2026-95517&size=10&page=0" \| jq .` (total 1): EUVD-2026-130760, aliases CVE-2026-95517 and GHSA-mqvp-jwc5-xgv2, `baseScore` 9.1 (3.1), `datePublished` Nov 19 2026, `dateUpdated` Nov 20 2026, no `exploitedSince`, a 772-character description, and five `Apache Software Foundation / Apache Tomcat` rows (`10.1.0-M1 ≤10.1.61`, `11.0.0-M1 ≤11.0.27`, `7.0.100 ≤7.0.109`, `9.0.13 ≤9.0.123`, `8.5.38 ≤8.5.100`): the story 5.1 budget fixture |

## `cisa_kev/`

| Fixture | How it was recorded |
|---|---|
| `cisa_kev_catalog.json` | `curl "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json" \| jq '.vulnerabilities \|= map(select(.cveID=="CVE-2026-114625" or .cveID=="CVE-2023-3519" or .cveID=="CVE-2020-1472" or .cveID=="CVE-2021-22555"))'`: `catalogVersion` 2026.10.23, `count` 1756 as returned; two `Known` and two `Unknown` entries |

## `cert_eu/`

Recorded on 2026-11-03 (CERT-EU, about 18:40 UTC). `R=https://cert.europa.eu/publications/security-advisories`.
The RSS is stored as served (bytes unchanged, no XML declaration, `pubDate` in `CEST`); the advisory JSON is pretty-printed with `jq .`.

| Fixture | How it was recorded |
|---|---|
| `cert_eu_rss.xml` | `curl "https://cert.europa.eu/publications/security-advisories-rss" -o cert_eu_rss.xml`: 10 items, 2026-018 at `Fri, 23 Oct 2026 16:36:26 CEST`, 2026-019 at `Thu, 22 Oct 2026 11:14:22 CEST` |
| `cert_eu_2026_018_multi_cve_revised.json` | `curl "$R/2026-018/json" \| jq .`: PHP2Shell v1.2, three CVEs (CVE-2026-109618, CVE-2026-109619, CVE-2026-115560, the last one added by v1.2), `publish_date` `23-10-2026 14:36:26` |
| `cert_eu_2026_019_single_cve.json` | `curl "$R/2026-019/json" \| jq .`: GitLab, one CVE (CVE-2026-110264), the lead-in paragraph names the product and the bullets do not |
| `cert_eu_2025_029_no_cve.json` | `curl "$R/2025-029/json" \| jq .`: SonicWall, no CVE ID, `# Products Affected` is prose |
| `cert_eu_2025_008_cve_blocks.json` | `curl "$R/2025-008/json" \| jq .`: Fortinet, three CVEs, one product block per CVE |
