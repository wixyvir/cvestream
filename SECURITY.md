# Security Policy

cvestream is a vulnerability monitoring pipeline. We take reports about
vulnerabilities **in cvestream itself** seriously.

## Reporting a vulnerability

Please use [GitHub's private vulnerability reporting](../../security/advisories/new)
to report a security issue. This keeps the details visible only to the
maintainers until a fix is available.

Do **not** open a public issue, pull request, or discussion for an unpatched
security vulnerability.

Include as much as you can:

- A description of the issue and its security impact.
- Steps to reproduce, or a proof of concept.
- The affected version or commit.
- Any suggested mitigation.

## What counts as a security issue

For cvestream, security issues include:

- Vulnerabilities in the source adapters that could be triggered by a
  malicious or malformed upstream payload (e.g. EUVD, CISA KEV, CERT-EU),
  leading to crashes, data corruption, or **false negatives in CVE coverage**.
- Issues in the RSS/XML rendering that could allow injection into feeds
  consumed by downstream clients.
- Exposure of feed tokens or other secrets in logs, error messages, or HTTP
  responses.
- Authentication or authorization weaknesses in the private listener.

A source returning unexpected data that is handled gracefully (logged, skipped)
is a bug, not a security issue — please open a regular issue for it.

## Response

- We will acknowledge your report within **7 days**.
- We will keep you informed of our progress toward a fix.
- Once a fix is released, we will credit you in the security advisory
  (unless you prefer to remain anonymous).

## Supported versions

Only the latest release on `main` is supported. Since cvestream is
pre-1.0, there are no backported security fixes to older versions.
