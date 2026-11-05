# Configuration

cvestream has two configuration layers:

- **Static settings** (`settings.yaml` and `CVESTREAM_*` variables): logging, polling intervals, where the endpoint configuration lives.
- **Endpoint configuration** (`endpoints.yaml`, or a Valkey service): teams, product lists, rules and feed tokens.

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
  | `http.public.base_url` | none | where readers reach the feeds (`https://feeds.example.org`, a path prefix is allowed); only the operator CLI uses it, to print feed URLs. Without it the CLI prints `http://localhost:<http.public.port>` :factory: |
  | `http.private.host` | `0.0.0.0` | address the private listener (`/healthz`, `/readyz`, `/status`) binds |
  | `http.private.port` | `9090` | port of the private listener, 1 to 65535 |
  | `sources.<name>.enabled` | `true` | poll this source (`euvd`, `cisa_kev`, `cert_eu`) |
  | `sources.<name>.interval` | `PT15M` | polling interval, greater than 0 and at most 60 minutes |
  | `sources.euvd.page_pause` | `PT1.5S` | pause between two EUVD result pages, 0 to 60 seconds; EUVD answers 403 when paged faster than about 40 to 60 requests a minute (see [sources](sources.md)) |
  | `config_provider` | `yaml` | where endpoints come from: `yaml` (the file) or `valkey` (a Valkey service, see [Valkey provider](#valkey-provider)) |
  | `endpoints_file` | `endpoints.yaml` | path of the endpoints file, relative to the working directory |
  | `data_dir` | `data` | directory for runtime data, the state database included (`/data` in the image) |
  | `state.url` | `sqlite:///<data_dir>/cvestream.db` | SQLAlchemy URL of the state database; a **secret** |
  | `valkey.url` | none | `valkey://<user>@<host>:<port>/<db>` (`valkeys://` for TLS); required when `config_provider` is `valkey`; never carries a password |
  | `valkey.password` | none | password of the user in `valkey.url`; a **secret** |
  | `valkey.startup_timeout` | `PT60S` | how long the daemon waits for Valkey at startup before exiting with code 3; greater than 0, at most 1 hour |

- **Durations:** ISO 8601 in environment variables (`PT30M`; a bare number is rejected there). In YAML, either ISO 8601 or a number of seconds (`1800`).

## `CVESTREAM_*` variables

- Every setting can be set through a variable with the `CVESTREAM_` prefix. A double underscore nests: `CVESTREAM_SOURCES__EUVD__INTERVAL=PT30M` sets `sources.euvd.interval`.
- **Precedence**, highest first: environment variables, secret files (`*_FILE`), `settings.yaml`. Values merge key by key.
- **Secrets** are read from a file with `CVESTREAM_<NAME>_FILE=/path`, nested names included. This applies to secret fields only: `state.url` (`CVESTREAM_STATE__URL_FILE`) and `valkey.password` (`CVESTREAM_VALKEY__PASSWORD_FILE`). Setting both `CVESTREAM_<NAME>` and `CVESTREAM_<NAME>_FILE` is an error.
- `http.*` values are not secrets: `CVESTREAM_HTTP__PUBLIC__PORT=18080`, `CVESTREAM_HTTP__PRIVATE__HOST=127.0.0.1`.
- `CVESTREAM_ENDPOINTS_FILE` is a plain path, **not** a secret file: it sets `endpoints_file`. `CVESTREAM_CONFIG_PROVIDER` sets `config_provider`. `CVESTREAM_DATA_DIR` sets `data_dir`. `CVESTREAM_VALKEY__URL` and `CVESTREAM_VALKEY__STARTUP_TIMEOUT` set the non-secret `valkey.*` keys.

## State database

The state (vulnerabilities, source records, publications and source cursors) lives in a database that survives restarts: it is what keeps an item from being posted twice.

- **Default:** the SQLite file `<data_dir>/cvestream.db` (`./data/cvestream.db`, or `/data/cvestream.db` in the image). It runs in WAL mode with foreign keys on and a 5 s busy timeout.
- **Storage:** SQLite needs block storage (a local disk or a block volume). Never put it on NFS, SMB or CIFS: file locking is not reliable there.
- **Create or upgrade:** `cvestream db migrate` brings the database to the latest revision and creates the data directory when it is missing. Running it again changes nothing. The daemon does the same at startup.
- **Back up:** `cvestream db backup <file>` copies the database with the SQLite online backup API (a consistent snapshot, WAL included, while the daemon runs). It refuses to overwrite an existing file. For PostgreSQL use `pg_dump`.
- **PostgreSQL:** planned. It will be an optional `psycopg` extra and a `state.url` change; the code is already portable.

## Valkey provider

With `config_provider: valkey` the daemon reads the endpoint configuration from a Valkey service instead of `endpoints.yaml`, so that other tools (BoM connectors, CD workers) can maintain product lists through a documented, versioned contract: [config-contract.md](config-contract.md). `config_provider: yaml` stays the default and keeps working as before; it never imports the Valkey client.

- **Connection:** `valkey.url` carries the scheme, the **user name**, the host, the port and the database number (`valkey://cvestream-daemon@valkey:6379/0`). The **password never goes in the URL**: a URL with a password, a query string, a fragment or another scheme is rejected (the error does not repeat the URL). Provide it as `valkey.password`, preferably as a secret file: `CVESTREAM_VALKEY__PASSWORD_FILE=/run/secrets/valkey-password`. Without a user name in the URL the client authenticates as `default`, which the shipped ACL file disables.
- **Server and users:** [`deploy/valkey/`](../deploy/valkey/) holds the server configuration (`valkey.conf`: AOF every second) and the ACL file (`users.acl`): `cvestream-daemon` (read-only, what this daemon uses), `cvestream-admin` and `connector-example`. The passwords in `deploy/valkey/dev-secrets/` are public and for development only: production ships its own ACL file with its own hashes.
- **TLS:** `valkeys://` is accepted; the server certificate is verified against the system trust store and must match the host name of `valkey.url` (there is no option to turn either check off). TLS is not exercised by the test suite: no TLS server runs in CI. :factory:
- **Startup:** the daemon retries an unreachable Valkey with a capped exponential backoff (1 s, doubling, at most 10 s) for `valkey.startup_timeout` (`PT60S` by default), then exits with code `3`. Meanwhile `/healthz` answers 200 and `/readyz` 503. A refused authentication or permission is not retried: the daemon exits `3` at once. An invalid or unseeded store exits `2`, as an invalid `endpoints.yaml` does.
- **Hot reload:** see [Reloading](#reloading).

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
    token: "<43-character token>"
    products:
      - php >= 8.2
      - name: gitlab
        aliases: [gitlab-ce, gitlab-ee]
        exclude: [gitlab-runner]
        min_version: "17.0"
        match_description: false
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
| `endpoints[].token` | yes | feed secret, `^[A-Za-z0-9_-]{32,128}$`, unique |
| `endpoints[].products` | no | list of shorthand strings or entries (default empty: matches nothing) |

A product entry has `name` (required), `aliases[]`, `exclude[]`, `min_version` and `match_description` (default `true`). Names, aliases and exclusion terms are stripped and must not be blank. `min_version` is a quoted version string. It is checked against the affected versions the source gives: an entry drops a vulnerability only when every row that names the product has a [version format the parser understands](sources.md#euvd-euvd) and none of them reaches the minimum. A row it cannot read never drops the item, which is labelled "versions unknown" when no other row reaches the minimum.

**How an entry matches.** The name and its aliases are matched case-insensitively, on whole words (`php` matches `PHP` and `php-fpm`, not `phpMyAdmin`), against the vulnerability's affected products (product or vendor name). The description is searched only when the vulnerability lists no affected product at all: a WordPress plugin whose description mentions `Module.php` does not match `php`. In the description, a name inside a file name or path does not count (`loan_history_maintenance.php`, `/usr/lib/php/`, `C:\php\`), while `php.ini`, `php.net` and "Node.js" still do. This is an assumed risk: a slash next to a name in prose reads as a path too, so `Apache/2.4.49`, `nginx/1.25.3`, `Linux/Unix` or `GNU/Linux` do not count, and such a vulnerability matches only once a record lists the product. Such a description-only match cannot check `min_version` and is labelled "versions unknown". With `match_description: false` the entry matches on affected-product data only, never on the description: a vulnerability known only from CISA KEV, which lists no product, then waits for EUVD data and is published as new when it arrives. The trade-off is fewer false positives, but a vulnerability whose records never list a product is missed for that entry. An exclusion term found in the affected products or the description drops the entry, whatever `match_description` says.

**Shorthand.** `php >= 8.2` means name `php`, minimum version `8.2`. A bare `php` is a name only. Spaces around `>=` are optional and names may contain spaces (`apache http server >= 2.4`). Only `>=` exists.

**Tokens.** The token **is** the access control: anyone who knows a feed URL can read the feed. `cvestream endpoint create` and `cvestream token rotate` generate one (see [CLI](#cli)). When you edit the file by hand, generate one per endpoint:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The `REPLACE-ME-...` tokens of `endpoints.example.yaml` are valid tokens: nothing rejects them, and a copied example serves its feeds at URLs anyone can guess from this repository. Replace every one of them before you expose the daemon. Tokens are stored in plain text, so keep `endpoints.yaml` readable by the daemon's user only (`chmod 600 endpoints.yaml`).

**YAML pitfalls.** PyYAML follows YAML 1.1: `8.10` is the number 8.1 (quote versions), `yes`/`no`/`on`/`off` are booleans, `2026-10-26` is a date, and an all-digit `id` is an integer (quote it). The contract is strict: `min_cvss: "7.0"` and `kev_only: "yes"` are rejected, a version must be quoted, and unknown or duplicate keys are errors. Anchors, aliases and merge keys (`<<: *defaults`) work.

### Validate

```sh
uv run cvestream config validate endpoints.yaml
```

Exit code 0 when valid (one line on stdout). Exit code 2 when invalid or unreadable, with one plain line on stderr naming the field, for example `endpoints.yaml: endpoints[0].min_cvss: Input should be a valid number`. Without a file argument, the `endpoints_file` setting is used. Error messages never contain a token.

### JSON Schema

[`schemas/config-v1.json`](../schemas/config-v1.json) is the published contract (JSON Schema 2020-12), generated from the same models the daemon validates with. Other tools can validate their `endpoints.yaml` against it in their own CI. The schema checks the structure only: unique ids and tokens, valid version strings and duplicate YAML keys are checked by `cvestream config validate`, so run that too when you can. `uv run cvestream config schema` regenerates it and `--check` fails when it is stale.

### Reloading

The daemon reloads the file by itself, without a restart. The revision of the file is the SHA-256 of its bytes: any change, comments included, triggers a reload. The check runs at the start of every pipeline cycle, so a change applies at the next cycle, that is the next source fetch (within the polling interval, 15 minutes by default). It covers endpoints, product lists, rules, `enabled` flags and tokens: a rotated token serves the new URL at once and the old one answers `404`.

**With Valkey,** the revision is the `cvestream:v1:revision` integer that every writer increments: the daemon reads it (one `GET`) at the start of each cycle and reloads when it changed. As a safety net for a writer that forgot to increment it, **every 12th pipeline cycle reloads the configuration even when the revision did not change** (about once an hour with three sources at 15 minutes); that reload is silent when nothing changed. While Valkey is unreachable, each cycle logs a `config.unavailable` warning and keeps the last valid configuration: polling and feeds continue, `/readyz` stays 200, and a cycle is delayed at most by the client's 5 s socket timeouts. A rotated token reaches the daemon at the next cycle, that is the next source fetch. :factory:

When the new content is invalid, the daemon keeps the last valid configuration and logs `config.rejected` with the field errors (never a token). When the file cannot be read, it keeps it too and logs `config.unavailable`. See [operations.md](operations.md).

## CLI

The operator CLI manages endpoints, products and tokens through the **active provider** (`config_provider`): the same commands write `endpoints.yaml` or the Valkey store, so nobody edits Valkey keys by hand. They read the settings like the daemon does. The daemon applies a change at its next cycle, with no restart. :factory:

| Command | What it does |
|---|---|
| `cvestream endpoint create ID --name NAME [--min-cvss N] [--kev-only] [--disabled]` | create an endpoint with a new random token; prints `feed: <url>` as its last line. Add products with `product add` |
| `cvestream endpoint list [--json]` | `ID NAME ENABLED MIN_CVSS KEV_ONLY PRODUCTS`, sorted by id; no token, no URL |
| `cvestream endpoint show ID [--json]` | every field, the products and the feed URL |
| `cvestream endpoint edit ID [--name N] [--min-cvss N \| --no-min-cvss] [--kev-only/--no-kev-only] [--enable/--disable]` | change the given fields; no option is an error |
| `cvestream endpoint delete ID [--yes]` | delete the endpoint, its products and its token; asks for confirmation, and without a terminal needs `--yes` |
| `cvestream product add ENDPOINT PRODUCT [--alias A]... [--exclude T]... [--min-version V] [--match-description/--no-match-description]` | `PRODUCT` is a name or `"name >= version"`; stored as given when no option refines it, as a full entry otherwise; a name the endpoint already has is refused |
| `cvestream product remove ENDPOINT NAME` | remove every product named NAME (case-insensitive, aliases are not considered) |
| `cvestream product list ENDPOINT [--json]` | `NAME MIN_VERSION ALIASES EXCLUDE MATCH_DESCRIPTION` |
| `cvestream token rotate ENDPOINT` | new token, prints the new feed URL; the old URL answers `404` once the daemon reloaded |
| `cvestream config export [-o FILE]` | the whole active configuration (tokens included) as a v1 YAML document, endpoints sorted by id; `-o` creates a new file with mode `0600` |
| `cvestream config import FILE` | validate FILE, then **replace** the whole configuration of the active provider (it also repairs an invalid or unseeded store) |

```sh
cvestream endpoint create team-php --name "PHP team" --min-cvss 7
cvestream product add team-php "php >= 8.2"
cvestream product add team-php gitlab --alias gitlab-ce --exclude gitlab-runner --min-version 17.0
cvestream endpoint show team-php --json
cvestream token rotate team-php
```

- **Feed URL.** `endpoint create`, `endpoint show` and `token rotate` print `<base>/feeds/<token>.xml`. `<base>` is `http.public.base_url` (`CVESTREAM_HTTP__PUBLIC__BASE_URL`, for example `https://feeds.example.org`), or `http://localhost:<http.public.port>` without it. The daemon ignores the setting. The token appears on stdout only in those three feed URLs and in `config export`; it never appears in an error line or a log.
- **Tokens** come from `secrets.token_urlsafe(32)`, never from a command-line option (it would end up in the shell history).
- **Exit codes.** `0` success. `2` for a user error: unknown or duplicate id, invalid value, invalid current document, bad options, provider unreachable or access denied (with Valkey, a write as the read-only `cvestream-daemon` user), a write conflict that lasted: one line on stderr. `1` for an unexpected error (the traceback only with `cvestream --debug ...`).
- **Validation.** Every change is validated with the contract models before anything is written: an invalid change leaves the file bytes (YAML) or the keys and the revision (Valkey) untouched. An invalid current file or store refuses every edit with the field errors; repair it by hand or with `cvestream config import`.
- **`--json`** (`endpoint list`, `endpoint show`, `product list`) prints snake_case JSON with no envelope. Products always have the same shape (`name`, `aliases`, `exclude`, `min_version`, `match_description`), whether stored as a shorthand string or as an entry; an unknown `min_cvss` or `min_version` is `null`.
- **YAML rewrite.** The CLI rewrites the whole file: the write is atomic (a temporary file in the same directory, `fsync`, then a rename), the file mode is kept (`0600` for a new file), only values that differ from the defaults are written, and **comments are not kept**. The first line says so (`# Written by cvestream; comments are not kept`), and the CLI prints a notice on stderr the first time it rewrites a file that had comments. Hand editing keeps working: every command starts from the current content of the file, and a change that lands between the CLI's read and its rename makes it read again (a very small window remains: do not run two writers on purpose). The directory must be writable: with a read-only mount (the self-hosted compose mounts `./config` read-only, Kubernetes mounts a Secret read-only) run the CLI where the file is writable, then the daemon reloads it through the mount, or use the Valkey provider.
- **Valkey.** A write is the 3.1 write protocol: `WATCH` the revision, read, apply, validate, `MULTI`, write every key of the configuration, `INCR` the revision, `EXEC`, retried on conflict, so each command grows the revision by exactly 1 and a connector writing at the same time makes the CLI retry instead of losing its change. See [Running the CLI against Valkey](operations.md#valkey).
