# Configuration

cvestream has two configuration layers:

- **Static settings** (`settings.yaml` and `CVESTREAM_*` variables): logging, polling intervals, where the endpoint configuration lives.
- **Endpoint configuration** (`endpoints.yaml`): teams, product lists, rules and feed tokens.

## `settings.yaml`

[`settings.example.yaml`](../settings.example.yaml) is the annotated reference. Copy it to `settings.yaml` (gitignored).

- **Lookup:** the file named by `CVESTREAM_SETTINGS_FILE`, otherwise `./settings.yaml`. A missing default file is fine (every key has a default). A missing explicit file, invalid YAML or a top level that is not a mapping is an error. Unknown keys are rejected.
- **Keys:**

  | Key | Default | Meaning |
  |---|---|---|
  | `log.level` | `info` | `debug`, `info`, `warning` or `error` |
  | `log.format` | `json` | `json` (one object per line) or `console` |
  | `http.public.host` | `0.0.0.0` | address the public listener (feeds) binds |
  | `http.public.port` | `8080` | port of the public listener, 1 to 65535 |
  | `http.private.host` | `0.0.0.0` | address the private listener (`/healthz`, `/readyz`, `/status`) binds |
  | `http.private.port` | `9090` | port of the private listener, 1 to 65535 |
  | `sources.<name>.enabled` | `true` | poll this source (`euvd`, `cisa_kev`, `cert_eu`) |
  | `sources.<name>.interval` | `PT15M` | polling interval, greater than 0 and at most 60 minutes |
  | `sources.euvd.page_pause` | `PT1.5S` | pause between two EUVD result pages, 0 to 60 seconds; EUVD answers 403 when paged faster than about 40 to 60 requests a minute (see [sources](sources.md)) |
  | `config_provider` | `yaml` | where endpoints come from; only `yaml` exists for now |
  | `endpoints_file` | `endpoints.yaml` | path of the endpoints file, relative to the working directory |
  | `data_dir` | `data` | directory for runtime data, the state database included (`/data` in the image) |
  | `state.url` | `sqlite:///<data_dir>/cvestream.db` | SQLAlchemy URL of the state database; a **secret** |

- **Durations:** ISO 8601 in environment variables (`PT30M`; a bare number is rejected there). In YAML, either ISO 8601 or a number of seconds (`1800`).

## `CVESTREAM_*` variables

- Every setting can be set through a variable with the `CVESTREAM_` prefix. A double underscore nests: `CVESTREAM_SOURCES__EUVD__INTERVAL=PT30M` sets `sources.euvd.interval`.
- **Precedence**, highest first: environment variables, secret files (`*_FILE`), `settings.yaml`. Values merge key by key.
- **Secrets** are read from a file with `CVESTREAM_<NAME>_FILE=/path`, nested names included. This applies to secret fields only: `state.url` is the first one (`CVESTREAM_STATE__URL_FILE`), and the Valkey password will follow with the Valkey provider. Setting both `CVESTREAM_<NAME>` and `CVESTREAM_<NAME>_FILE` is an error.
- `http.*` values are not secrets: `CVESTREAM_HTTP__PUBLIC__PORT=18080`, `CVESTREAM_HTTP__PRIVATE__HOST=127.0.0.1`.
- `CVESTREAM_ENDPOINTS_FILE` is a plain path, **not** a secret file: it sets `endpoints_file`. `CVESTREAM_CONFIG_PROVIDER` sets `config_provider`. `CVESTREAM_DATA_DIR` sets `data_dir`.

## State database

The state (vulnerabilities, source records, publications and source cursors) lives in a database that survives restarts: it is what keeps an item from being posted twice.

- **Default:** the SQLite file `<data_dir>/cvestream.db` (`./data/cvestream.db`, or `/data/cvestream.db` in the image). It runs in WAL mode with foreign keys on and a 5 s busy timeout.
- **Storage:** SQLite needs block storage (a local disk or a block volume). Never put it on NFS, SMB or CIFS: file locking is not reliable there.
- **Create or upgrade:** `cvestream db migrate` brings the database to the latest revision and creates the data directory when it is missing. Running it again changes nothing. The daemon does the same at startup.
- **Back up:** `cvestream db backup <file>` copies the database with the SQLite online backup API (a consistent snapshot, WAL included, while the daemon runs). It refuses to overwrite an existing file. For PostgreSQL use `pg_dump`.
- **PostgreSQL:** planned. It will be an optional `psycopg` extra and a `state.url` change; the code is already portable.

## `endpoints.yaml`

[`endpoints.example.yaml`](../endpoints.example.yaml) documents every field. Copy it to `endpoints.yaml` (gitignored) and replace the placeholder tokens.

```yaml
version: 1
endpoints:
  - id: team-php
    name: PHP team
    enabled: true
    min_cvss: 7.0
    kev_only: false
    require_score: false
    group_by_product: false
    token: "<43-character token>"
    products:
      - php >= 8.2
      - name: gitlab
        aliases: [gitlab-ce, gitlab-ee]
        exclude: [gitlab-runner]
        min_version: "17.0"
        match_description: false
      - name: cpython
        aliases: [python/cpython]
        exact: true
        min_version: "3.9"
```

| Field | Required | Meaning |
|---|---|---|
| `version` | yes | contract version, the integer `1` |
| `endpoints` | no | list of endpoints (default empty) |
| `endpoints[].id` | yes | unique slug, `^[a-z0-9-]{1,40}$` |
| `endpoints[].name` | yes | display name, 1 to 200 characters |
| `endpoints[].enabled` | no | default `true`; a disabled endpoint is kept but not served |
| `endpoints[].min_cvss` | no | number from 0 to 10; omitted means no threshold |
| `endpoints[].kev_only` | no | default `false`; keep only known-exploited vulnerabilities |
| `endpoints[].require_score` | no | default `false`; drop vulnerabilities that no source has scored yet, except known-exploited ones |
| `endpoints[].group_by_product` | no | default `false`; publish 3 or more items of one run that share a matched product and kind as one item (near-equal chunks of at most 5 per item, each of at least 3) |
| `endpoints[].token` | yes | feed secret, `^[A-Za-z0-9_-]{32,128}$`, unique |
| `endpoints[].products` | no | list of shorthand strings or entries (default empty: matches nothing) |

**Score rules.** `min_cvss` compares the highest score any source gives. A vulnerability that no source has scored yet is never dropped by the threshold: it is published with "severity unknown" (recall over volume). Set `require_score: true` to hold such vulnerabilities back until a source scores them, with or without `min_cvss`. A known-exploited vulnerability (CISA KEV or EU KEV) is never held back for lack of a score. A held-back vulnerability is published as new when a source scores it within the 14-day window (or the 7-day backfill for an endpoint without history), if the score reaches `min_cvss`. Trade-off: CERT-EU advisories carry no score, so an endpoint with `require_score` loses CERT-EU's early warning and waits for EUVD's score. Switching the rule off later does not bring back what it held back: such a vulnerability is published only when one of its records changes. Each `cycle.summary` log counts what the rule held back, per endpoint (`held_back`, see `docs/operations.md`).

**Grouping.** With `group_by_product: true`, one pipeline run that publishes at least 3 items sharing a matched product and the same kind — all `new`, or all severity-raised (`band-*`) — posts them as one feed item instead of several, so a burst of ten CVEs of one product does not fill the channel with ten messages. The key is the first matched entry name (unique per endpoint since names are unique); the kind family is `new` or `band` (`new` and `band-*` decisions of the same product never mix). Known-exploited vulnerabilities are never grouped, and they are removed before the threshold is counted (3 candidates, one known-exploited, means no group). Inside an item, members are ordered by severity band descending, then by ID in natural order (`CVE-2026-9999` before `CVE-2026-10000`), and a burst of more than 5 splits into near-equal chunks of at most 5 (`part 1 of 2`, …; 6 → 3 + 3, 11 → 4 + 4 + 3, so every chunk keeps at least 3 members — never a trailing 5 + 1). A member that later becomes known-exploited or whose band rises again is published on its own, as today. Switching the rule on or off never changes items already published: groups stay groups, single items stay single items, nothing is posted again. A new endpoint's 7-day backfill is grouped too.

A product entry has `name` (required), `aliases[]`, `exclude[]`, `min_version`, `match_description` (default `true`) and `exact` (default `false`). `exact: true` together with an explicit `match_description: true` is rejected. Names, aliases and exclusion terms are stripped and must not be blank. Entry names must be unique within an endpoint, ignoring case and whitespace runs, and a shorthand counts by its name: `php`, `PHP` and `php >= 8.2` in one endpoint are rejected, including in a disabled endpoint (`enabled: false`). An alias of one entry can be another entry's name, and two endpoints can watch the same name. `min_version` is a quoted version string. It is checked against the affected versions the source gives: an entry drops a vulnerability only when every row that names the product has a [version format the parser understands](sources.md#euvd-euvd) and none of them reaches the minimum. A row it cannot read never drops the item, which is labelled "versions unknown" when no other row reaches the minimum.

**How an entry matches.** The name and its aliases are matched case-insensitively, on whole words (`php` matches `PHP` and `php-fpm`, not `phpMyAdmin`), against the vulnerability's affected products (product or vendor name). The description is searched only when the vulnerability lists no affected product at all: a WordPress plugin whose description mentions `Module.php` does not match `php`. In the description, a name inside a file name or path does not count (`loan_history_maintenance.php`, `/usr/lib/php/`, `C:\php\`), while `php.ini`, `php.net` and "Node.js" still do. This is an assumed risk: a slash next to a name in prose reads as a path too, so `Apache/2.4.49`, `nginx/1.25.3`, `Linux/Unix` or `GNU/Linux` do not count, and such a vulnerability matches only once a record lists the product. Such a description-only match cannot check `min_version` and is labelled "versions unknown". With `match_description: false` the entry matches on affected-product data only, never on the description: a vulnerability known only from CISA KEV, which lists no product, then waits for EUVD data and is published as new when it arrives. The trade-off is fewer false positives, but a vulnerability whose records never list a product is missed for that entry. An exclusion term found in the affected products or the description drops the entry, whatever `match_description` says.

**Exact entries.** With `exact: true`, the whole affected-product name must equal the name or an alias (case-insensitive, whitespace runs collapsed; hyphens and other characters count, so list variants as aliases). Vendor names are ignored and the description is never searched. For example, `{name: php, exact: true, min_version: "7.4"}` keeps `PHP Group / PHP` and drops `CloudClassroom-PHP-Project`, `php-travel-agency-system`, `PHP Driver` and vendor `PHP` / product `frankenphp`; `{name: cpython, aliases: [python/cpython], exact: true}` keeps CPython only. PHP's bundled extensions are filed under the product `PHP`, so an exact `php` entry still covers them; PECL extensions (MongoDB `PHP Extension`, imagick, redis) are separate products and need their own entries. A vulnerability known only from CISA KEV lists no affected product at all and the description is never searched for an exact entry, so it waits for EUVD to list the product and is then published as new. A CERT-EU-only advisory keeps its affected products as prose: the prose equals a product name only when a bullet is exactly the name (`- PHP`); longer prose (`PHP 8.5 versions prior to 8.5.12`) never does, so such an item waits for EUVD data on an exact entry — but a bare-name bullet does match, and it carries no version, so with `min_version` the item is published as "versions unknown". Exclusion terms keep plain whole-word matching against product names, vendor names and the description. A second, non-exact early-warning entry for the same product needs another `name`, since names are unique per endpoint: an alias of the exact entry works.

**Shorthand.** `php >= 8.2` means name `php`, minimum version `8.2`. A bare `php` is a name only. Spaces around `>=` are optional and names may contain spaces (`apache http server >= 2.4`). Only `>=` exists.

**Tokens.** The token **is** the access control: anyone who knows a feed URL can read the feed. Generate one per endpoint:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The `REPLACE-ME-...` tokens of `endpoints.example.yaml` are valid tokens: nothing rejects them, and a copied example serves its feeds at URLs anyone can guess from this repository. Replace every one of them before you expose the daemon. Tokens are stored in plain text, so keep `endpoints.yaml` readable by the daemon's user only (`chmod 600 endpoints.yaml`).

**YAML pitfalls.** PyYAML follows YAML 1.1: `8.10` is the number 8.1 (quote versions), `yes`/`no`/`on`/`off` are booleans, `2026-10-26` is a date, and an all-digit `id` is an integer (quote it). The contract is strict: `min_cvss: "7.0"`, `kev_only: "yes"`, and `require_score: "yes"` are rejected, and so is `group_by_product: "yes"`; a version must be quoted, and unknown or duplicate keys are errors. Anchors, aliases and merge keys (`<<: *defaults`) work.

### Validate

```sh
uv run cvestream config validate endpoints.yaml
```

Exit code 0 when valid (one line on stdout). Exit code 2 when invalid or unreadable, with one plain line on stderr naming the field, for example `endpoints.yaml: endpoints[0].min_cvss: Input should be a valid number`. Without a file argument, the `endpoints_file` setting is used. Error messages never contain a token.

### JSON Schema

[`schemas/config-v1.json`](../schemas/config-v1.json) is the published contract (JSON Schema 2020-12), generated from the same models the daemon validates with. Other tools can validate their `endpoints.yaml` against it in their own CI. The schema checks the structure only: unique ids and tokens, unique product names per endpoint, valid version strings and duplicate YAML keys are checked by `cvestream config validate`, so run that too when you can. `uv run cvestream config schema` regenerates it and `--check` fails when it is stale.

### Reloading

The daemon reloads the file by itself, without a restart. The revision of the file is the SHA-256 of its bytes: any change, comments included, triggers a reload. The check runs at the start of every pipeline cycle, so a change applies at the next cycle, that is the next source fetch (within the polling interval, 15 minutes by default). It covers endpoints, product lists, rules, `enabled` flags and tokens: a rotated token serves the new URL at once and the old one answers `404`.

When the new content is invalid, the daemon keeps the last valid configuration and logs `config.rejected` with the field errors (never a token). When the file cannot be read, it keeps it too and logs `config.unavailable`. See [operations.md](operations.md).
