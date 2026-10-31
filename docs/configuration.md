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
  | `sources.<name>.enabled` | `true` | poll this source (`euvd`, `cisa_kev`) |
  | `sources.<name>.interval` | `PT15M` | polling interval, greater than 0 and at most 60 minutes |
  | `config_provider` | `yaml` | where endpoints come from; only `yaml` exists for now |
  | `endpoints_file` | `endpoints.yaml` | path of the endpoints file, relative to the working directory |
  | `data_dir` | `data` | directory for runtime data, the state database included (`/data` in the image) |
  | `state.url` | `sqlite:///<data_dir>/cvestream.db` | SQLAlchemy URL of the state database; a **secret** |

- **Durations:** ISO 8601 in environment variables (`PT30M`; a bare number is rejected there). In YAML, either ISO 8601 or a number of seconds (`1800`).

## `CVESTREAM_*` variables

- Every setting can be set through a variable with the `CVESTREAM_` prefix. A double underscore nests: `CVESTREAM_SOURCES__EUVD__INTERVAL=PT30M` sets `sources.euvd.interval`.
- **Precedence**, highest first: environment variables, secret files (`*_FILE`), `settings.yaml`. Values merge key by key.
- **Secrets** are read from a file with `CVESTREAM_<NAME>_FILE=/path`, nested names included. This applies to secret fields only: `state.url` is the first one (`CVESTREAM_STATE__URL_FILE`), and the Valkey password will follow with the Valkey provider. Setting both `CVESTREAM_<NAME>` and `CVESTREAM_<NAME>_FILE` is an error.
- `CVESTREAM_ENDPOINTS_FILE` is a plain path, **not** a secret file: it sets `endpoints_file`. `CVESTREAM_CONFIG_PROVIDER` sets `config_provider`. `CVESTREAM_DATA_DIR` sets `data_dir`.

## State database

The state (vulnerabilities, source records, publications and source cursors) lives in a database that survives restarts: it is what keeps an item from being posted twice.

- **Default:** the SQLite file `<data_dir>/cvestream.db` (`./data/cvestream.db`, or `/data/cvestream.db` in the image). It runs in WAL mode with foreign keys on and a 5 s busy timeout.
- **Storage:** SQLite needs block storage (a local disk or a block volume). Never put it on NFS, SMB or CIFS: file locking is not reliable there.
- **Create or upgrade:** `cvestream db migrate` brings the database to the latest revision and creates the data directory when it is missing. Running it again changes nothing. The daemon does the same at startup (story 1.6).
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
    token: "<43-character token>"
    products:
      - php >= 8.2
      - name: gitlab
        aliases: [gitlab-ce, gitlab-ee]
        exclude: [gitlab-runner]
        min_version: "17.0"
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

A product entry has `name` (required), `aliases[]`, `exclude[]` and `min_version`. Names, aliases and exclusion terms are stripped and must not be blank. `min_version` is a quoted version string.

**Shorthand.** `php >= 8.2` means name `php`, minimum version `8.2`. A bare `php` is a name only. Spaces around `>=` are optional and names may contain spaces (`apache http server >= 2.4`). Only `>=` exists.

**Tokens.** The token **is** the access control: anyone who knows a feed URL can read the feed. Generate one per endpoint:

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

The revision of the file is the SHA-256 of its bytes: any change, comments included, triggers a reload. When the new content is invalid, the daemon keeps the last valid configuration and logs `config.rejected` (once the daemon exists, story 1.6).
