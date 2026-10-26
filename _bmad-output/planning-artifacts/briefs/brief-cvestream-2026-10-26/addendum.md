# Addendum: Technical Notes for Downstream Planning

These details support source selection and implementation planning. They are not all settled decisions.

## Configuration and Delivery

- Configuration options raised so far are a CSV file or key/value store; the choice remains open.
- Support multiple input sources and distinct product lists for output feeds. Slack subscribes to the resulting RSS endpoint and does not need access to filter configuration.
- Product-name matching is acceptable initially. Alias handling, false positives, vendor names, versions, and identifier mapping remain to be defined.

## Sources and Data Model

- **NVD:** Searchable JSON API with product/CPE data and CVSS enrichment when available. Some records remain awaiting enrichment or are not scheduled. Validate coverage against the team's product list. Sources: [NVD APIs and feeds](https://nvd.nist.gov/vuln/data-feeds), [NVD status definitions](https://nvd.nist.gov/vuln/vulnerability-status).
- **EUVD:** EU-focused vulnerability aggregator with an API. Compare its product coverage and update behavior with NVD using representative alerts. Sources: [ENISA Vulnerability Services](https://www.enisa.europa.eu/topics/vulnerability-services), [EUVD API docs](https://euvd.enisa.europa.eu/apidoc).
- **CVE records:** Structured affected-product information is useful as a baseline, but completeness varies by submitting authority and CVSS is not guaranteed. Source: [CVE CNA Rules](https://www.cve.org/ResourcesSupport/AllResources/CNARules/).
- **CISA KEV:** JSON/CSV catalog that provides a known-exploitation signal. It covers known exploited vulnerabilities and should supplement a broader CVE or advisory source. Source: [CISA KEV Catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog).
- **Vendor CSAF advisories:** Consider as an additional source where available. CSAF can link products, versions, vulnerabilities, scores, and remediation guidance, but vendor coverage varies. Source: [OASIS CSAF 2.1](https://docs.oasis-open.org/csaf/csaf/v2.1/csaf-v2.1.html).

For normalized records, retain CVE ID, source, affected vendor/product/version, CVSS version/score/vector when present, exploitation flags, publication/update times, and original advisory references. Correlate by CVE ID and preserve source provenance and disagreements. Missing or delayed enrichment must remain unknown rather than being treated as zero or as evidence that an alert is unimportant.
