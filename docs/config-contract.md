# Configuration contract (Valkey, v1)

## TL;DR

- With `config_provider: valkey`, cvestream reads its endpoints from **Valkey keys under `cvestream:v1:*`**. Any tool that follows this document can seed and edit them: BoM connectors, CD workers, the operator CLI.
- Six kinds of key: `schema` (`1`), `revision` (an integer every writer `INCR`s), `endpoints` (a set of ids), and per endpoint `endpoint:<id>` (JSON), `endpoint:<id>:products` (JSON array) and `token:<id>` (the feed token, **raw text**).
- **Readers** (the daemon): read `revision`, read the keys, read `revision` again; if it moved, start over. **Writers**: `WATCH revision`, read, `MULTI`, write, **`INCR revision`**, `EXEC`; retry on conflict.
- The JSON values follow [`schemas/config-v1.json`](../schemas/config-v1.json): the same models validate `endpoints.yaml` and Valkey. Anything invalid is rejected as a whole and the daemon keeps its last valid configuration.
- Access is per role through an ACL file ([`deploy/valkey/users.acl`](../deploy/valkey/users.acl)): the daemon is read-only, the admin writes `cvestream:v1:*`, a connector writes only `endpoint:*:products` and `revision`, and **nobody but the daemon, the admin and the administration UI can see a token**.
- Compatibility: the `v1` in the key prefix is the contract version. A breaking change gets a new prefix (`cvestream:v2:*`), never a silent change of `v1`.

:factory: This document was written by an AI agent.

## Who uses it

| Role | Valkey user (example) | Reads | Writes |
|---|---|---|---|
| Daemon (`cvestream run`) | `cvestream-daemon` | everything under `cvestream:v1:*`, tokens included (it serves the feeds) | nothing |
| Operator / CLI | `cvestream-admin` | everything under `cvestream:v1:*` | everything under `cvestream:v1:*` |
| Connector (BoM, CD worker) | `connector-<name>` | `schema`, `revision`, `endpoints`, `endpoint:*` (products included) | `endpoint:<id>:products` and `revision` only; never `token:*` |
| Administration UI (story 3.4) | `cvestream-ui` | everything under `cvestream:v1:*`, tokens included, plus the key names of the whole database through `SCAN` | everything under `cvestream:v1:*` |

## Key layout

| Key | Type | Value | Notes |
|---|---|---|---|
| `cvestream:v1:schema` | string | `1`, a JSON integer | the contract version; **missing means the store is not seeded**: the daemon rejects it (exit code 2 at startup) |
| `cvestream:v1:revision` | string | a decimal integer | every writer `INCR`s it in the same transaction as its change; missing reads as `0` |
| `cvestream:v1:endpoints` | set | endpoint ids, `^[a-z0-9-]{1,40}$` | the list of endpoints; a member that is not a valid id rejects the snapshot |
| `cvestream:v1:endpoint:<id>` | string | a JSON object: `name`, `enabled`, `min_cvss`, `kev_only` | `id`, `token` and `products` are **not allowed** in it (they come from the key names); a missing key rejects the snapshot |
| `cvestream:v1:endpoint:<id>:products` | string | a JSON array of product items | a missing key means no products (an endpoint without products matches nothing) |
| `cvestream:v1:token:<id>` | string | the feed token, **raw text, not JSON-quoted** | 32 to 128 URL-safe characters; its own namespace so that no connector pattern covers it; a missing key rejects the snapshot |

Values are UTF-8 JSON, except `token:<id>` (raw text) and `revision` (a decimal integer). The JSON must be strict: `NaN` and `Infinity` are rejected, `min_cvss` is a JSON number (`7.5`, not `"7.5"`), `enabled` and `kev_only` are JSON booleans.

### JSON values

The endpoint object has the fields of the `EndpointConfig` definition of [`schemas/config-v1.json`](../schemas/config-v1.json) **without** `id`, `token` and `products`:

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | display name, 1 to 200 characters |
| `enabled` | no | default `true`; a disabled endpoint is kept but not served |
| `min_cvss` | no | number from 0 to 10; omitted means no threshold |
| `kev_only` | no | default `false`; keep only known-exploited vulnerabilities |

The products value is a JSON array of items. An item is a shorthand string (`"php >= 8.2"`, or a bare `"php"`) or an object (`ProductEntryConfig` in the schema): `name` (required), `aliases[]`, `exclude[]`, `min_version` (a **string**), `match_description` (default `true`). How entries match is described in [configuration.md](configuration.md#endpointsyaml). The provider writes the full object, defaults included, with sorted keys and no whitespace; readers must accept any valid JSON.

### Full example

A single endpoint `team-php`:

```
cvestream:v1:schema                      1
cvestream:v1:revision                    7
cvestream:v1:endpoints                   {"team-php"}
cvestream:v1:endpoint:team-php           {"enabled":true,"kev_only":false,"min_cvss":7.0,"name":"PHP team"}
cvestream:v1:endpoint:team-php:products  ["php >= 8.2",{"aliases":["gitlab-ce"],"match_description":false,"min_version":"17.0","name":"gitlab"}]
cvestream:v1:token:team-php              Qm9y...43 URL-safe characters...
```

The daemon assembles these keys into the document it would read from `endpoints.yaml` and validates it with the same models:

```yaml
version: 1                 # cvestream:v1:schema
endpoints:
  - id: team-php           # the member of cvestream:v1:endpoints
    name: PHP team         # from cvestream:v1:endpoint:team-php ...
    enabled: true
    min_cvss: 7.0
    kev_only: false
    token: Qm9y...         # cvestream:v1:token:team-php
    products:              # cvestream:v1:endpoint:team-php:products
      - php >= 8.2
      - {name: gitlab, aliases: [gitlab-ce], min_version: "17.0", match_description: false}
```

## Read protocol

The daemon user has no `WATCH` or `MULTI`; the revision brackets the read instead:

```
revision (every pipeline cycle):  GET cvestream:v1:revision
load:
  r1 = GET revision
  GET schema, SMEMBERS endpoints                                   (pipelined)
  for every id: GET endpoint:<id>, GET endpoint:<id>:products, GET token:<id>   (pipelined)
  r2 = GET revision
  if r1 == r2: assemble, validate, done (the snapshot's revision is r1)
  else: start over, at most 3 retries, then give up and keep the last valid configuration
```

A writer that follows the write protocol increments the revision inside its transaction, so a read that straddles a write sees two different revisions and retries. A tool that only needs to read can do the same.

When the new snapshot is invalid (bad JSON, wrong type, missing key, a model error such as a duplicate token) the daemon logs `config.rejected`, naming the key or field and never a value, and **keeps the last valid configuration**. When Valkey is unreachable it logs `config.unavailable` and keeps it too.

The daemon reloads when `revision` changed, and also **every 12th pipeline cycle whatever the revision** (about once an hour with the default polling): a writer that forgot to `INCR` is picked up within that time, but never rely on it.

## Write protocol

Every change, whatever the tool, follows the same transaction:

```
WATCH cvestream:v1:revision
<read the keys you depend on>
<decide the change; validate it against schemas/config-v1.json>
MULTI
  <SET / SADD / SREM / DEL ...>
  INCR cvestream:v1:revision          # always, in the same transaction
EXEC                                  # nil when another writer touched the revision: start again
```

- Always `INCR` the revision **inside** the transaction: the daemon's consistent read depends on it.
- A write that leaves the store invalid (bad JSON, a duplicate token, an unknown id in the set) rejects the whole configuration, for every feed: validate before you write.
- A key written without its sibling (a `token:<id>` for an id that is not in `endpoints`) is ignored; an id in `endpoints` without its `endpoint:<id>` or `token:<id>` rejects the snapshot.
- Removing an endpoint means `SREM endpoints <id>` and `DEL` of its three keys, in one transaction.
- libraries: valkey-py sends `INCR` as `INCRBY key 1`, so the ACL line of a user that increments lists both commands.

The `cvestream` operator CLI is the reference writer: every `endpoint`, `product` and `token` command, and `config import`, runs this transaction (`WATCH` the revision, read, apply, validate, `MULTI`, write **every** key of the configuration, `INCR`, `EXEC`), so a connector's concurrent `INCR` makes the CLI retry with fresh data and never loses the connector's change. `cvestream config export` and `cvestream config import` exist for backups: `import` replaces the whole configuration (the `endpoints` set and all keys of the endpoints it drops) in one such transaction, and works on an invalid or unseeded store. :factory:

### `valkey-cli` example (admin)

Seed the store with one endpoint, as `cvestream-admin` (here against the dev stack, `docker compose exec -T valkey valkey-cli`; the password of the dev users is in `deploy/valkey/dev-secrets/`):

```sh
TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
docker compose exec -T valkey valkey-cli --no-auth-warning --user cvestream-admin \
  --pass "$(cat deploy/valkey/dev-secrets/cvestream-admin.password)" <<EOF
WATCH cvestream:v1:revision
MULTI
SET cvestream:v1:schema 1
SADD cvestream:v1:endpoints team-php
SET cvestream:v1:endpoint:team-php '{"name":"PHP team","enabled":true,"min_cvss":7.0,"kev_only":false}'
SET cvestream:v1:endpoint:team-php:products '["php >= 8.2"]'
SET cvestream:v1:token:team-php $TOKEN
INCR cvestream:v1:revision
EXEC
EOF
```

`EXEC` prints `1)` `OK` ... `6)` the new revision. Run `cvestream config validate` on an equivalent YAML first if you want a check before the write; the daemon logs `config.rejected` otherwise.

### Connector example

A connector replaces the product list of an existing endpoint and nothing else. It cannot create endpoints, rename them, read or change tokens:

```sh
valkey-cli --user connector-bom --pass "$CONNECTOR_PASSWORD" <<'EOF'
WATCH cvestream:v1:revision
MULTI
SET cvestream:v1:endpoint:team-php:products '["php >= 8.3","symfony","gitlab"]'
INCR cvestream:v1:revision
EXEC
EOF
```

A connector should read the list of ids it may edit from `SMEMBERS cvestream:v1:endpoints` (it can) and write a valid array: an invalid array rejects the **whole** configuration, not only its endpoint.

## Access rules (ACL)

[`deploy/valkey/users.acl`](../deploy/valkey/users.acl) defines five users. It is loaded by `valkey.conf` (`aclfile /usr/local/etc/valkey/users.acl`):

```
user default off resetpass resetkeys resetchannels -@all
user cvestream-daemon on #<sha256> resetchannels %R~cvestream:v1:* -@all +get +mget +smembers +exists +ping +select +client|setinfo
user cvestream-admin on #<sha256> resetchannels ~cvestream:v1:* -@all +get +mget +smembers +exists +set +del +sadd +srem +incr +incrby +watch +unwatch +multi +exec +discard +ping +select +client|setinfo
user cvestream-ui on #<sha256> resetchannels ~cvestream:v1:* -@all +get +mget +smembers +exists +set +del +sadd +srem +incr +incrby +watch +unwatch +multi +exec +discard +ping +select +client|setinfo +client|setname +scan +type +ttl +pttl +strlen +getrange +scard +sscan +sismember +dbsize +info +memory|usage
user connector-example on #<sha256> resetchannels %R~cvestream:v1:schema %R~cvestream:v1:endpoints %R~cvestream:v1:endpoint:* %W~cvestream:v1:endpoint:*:products %RW~cvestream:v1:revision -@all +get +mget +smembers +exists +set +incr +incrby +watch +unwatch +multi +exec +discard +ping +select +client|setinfo
```

(`<sha256>` stands for the 64-character hash that the file carries.) Why each rule:

- **No comment lines in `users.acl`.** Valkey 9.1 accepts only blank lines and lines starting with `user` in an ACL file: a `#` comment makes the whole file fail to load. This section is where the explanations live.
- **The `default` line is required.** When the file has no `default` user, Valkey recreates it as `on nopass ~* &* +@all`: an open server.
- **Explicit command lists, not categories.** `+@read` and `+@write` include `KEYS`, `SCAN`, `SORT` and `FLUSHALL` (some only removed by `-@dangerous`), and `SCAN` lists key names whatever the key patterns say. The lists above are exactly what the daemon and the write protocol use.
- **Key permissions per command.** `GET`, `MGET` and `SMEMBERS` need read access to the key; `SET`, `SADD`, `SREM` and `DEL` need write access; **`INCR` needs read and write** (hence `%RW~` on `revision` for the connector); `WATCH` only needs the key to match one of the user's patterns. A command queued inside `MULTI` is checked when it is queued: a refused one makes the transaction fail.
- **Pattern details.** `%R~cvestream:v1:endpoint:*` also matches `cvestream:v1:endpoint:<id>:products` (a glob `*` crosses `:`), so connectors can read product lists. It does **not** match `cvestream:v1:endpoints` (which has its own pattern) nor `cvestream:v1:token:<id>`.
- **Tokens stay secret.** `token:<id>` lives in its own namespace, outside `endpoint:*`: no connector pattern covers it. Keep it that way when you add a user.
- **`client|setinfo`.** valkey-py sends `CLIENT SETINFO` after `AUTH` and ignores a refusal; the line grants it so the server's ACL log stays clean. The UI's client (ioredis) sends `CLIENT SETNAME` instead: `cvestream-ui` grants `client|setname`, never the whole `client` command.
- **`cvestream-ui` (the administration UI, story 3.4).** It is the only user with `SCAN`: `SCAN` is keyspace-wide and lists every key name of the database, which is accepted because the database holds only `cvestream:v1:*` (a token key name carries only the endpoint id). Its extra grants are the browse commands the UI needs (`TYPE`, `TTL`, `PTTL`, `STRLEN`, `GETRANGE`, `SCARD`, `SSCAN`, `SISMEMBER`, `DBSIZE`, `INFO`, `MEMORY USAGE`), settled empirically with `ACL LOG` on 2026-11-07: with them, the key browser, string and set views and edits, key deletion and the CLI tab all work; the commands the UI also tries and that stay refused (`CONFIG GET`, `CLUSTER NODES`, `ACL CAT`) are optional and the UI degrades on them.
- **Never granted to any user:** `KEYS`, `FLUSHALL`/`FLUSHDB`, `CONFIG`, `ACL`, `DEBUG`, `MODULE`, scripting (`EVAL`/`EVALSHA`/`SCRIPT`/`FUNCTION`/`FCALL`), `SHUTDOWN`, `REPLICAOF`/`SLAVEOF`, `MIGRATE`, `MONITOR`, `SLOWLOG`, `LATENCY`, `CLIENT LIST`/`KILL`, `RENAME`/`RENAMENX`, `MOVE`, `COPY`, `RESTORE`, `DUMP`, `SWAPDB`, the `EXPIRE` family (`EXPIRE`/`PEXPIRE`/`EXPIREAT`/`PEXPIREAT`/`PERSIST`/`GETEX` — a TTL on a config key would make it vanish), `SAVE`/`BGSAVE`/`BGREWRITEAOF`, `CLUSTER`, `FAILOVER`, `SYNC`/`PSYNC`/`REPLCONF` (they stream the whole dataset), and every `CLIENT` subcommand but `SETINFO` and `SETNAME`; no user gets a `+@` category either. `tests/unit/test_deploy_valkey.py` guards the list on the raw file, for every user, whatever the case of the rule.
- **`SET` can still set a TTL.** An ACL cannot filter arguments: a user with `SET` can run `SET <key> <value> EX <seconds>` (or `PX`, `EXAT`, `PXAT`). The `EXPIRE` ban closes the commands made for it, not this option: never give a configuration key a TTL (see `docs/operations.md`, "Valkey administration UI").
- **The accepted `INFO` exposure.** `cvestream-ui` is granted `INFO`. Its clients section has counters only (the client list, with addresses and last commands, is `CLIENT LIST`, refused), but the other sections show the server and OS versions, the executable and configuration file paths, persistence and replication details (replica addresses), per-command statistics and the key count of each logical database. Refusing `INFO` degrades the UI, so the exposure is accepted and recorded here (checked against `valkey/valkey:9.1.3` on 2026-11-07).
- **UI writes skip validation and the revision bump.** The UI writes keys directly: no `WATCH`/`INCR` protocol, no validation against the schema. The daemon still catches both: the forced reload every 12th pipeline cycle picks up an edit that did not `INCR`, and an invalid edit is rejected with `config.rejected`, keeping the last valid configuration (see the read protocol above). Prefer the CLI for writes; after a UI edit, run `INCR cvestream:v1:revision`.
- **Production: the UI user.** The committed file carries the hash of a public dev password. In production add your own `cvestream-ui` line (same commands, your own `#<sha256>`, generated as in step 1 below) only when you enable the UI, and put the password in the `cvestream-valkey-ui` Secret's `databases.json` (see `docs/operations.md`, "Valkey administration UI").

### Adding a connector user

1. Generate a password and its hash. The hash goes in the file, the password in the connector's own secret store:

   ```sh
   PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
   printf '%s' "$PASSWORD" | sha256sum | cut -d' ' -f1
   ```

2. Copy the `connector-example` line, rename it `connector-<name>`, replace the `#<sha256>` by `#<the hash from step 1>` (a `#` followed by 64 lower-case hex characters). Never write the password with `>` in a committed file.
3. Narrow the patterns when the connector needs less (a fixed endpoint: `%W~cvestream:v1:endpoint:team-php:products`).
4. Reload the ACL (`ACL LOAD` as a user allowed to run it, or restart Valkey) and test the new line the way `tests/integration/test_valkey_provider.py` tests the shipped ones: what it must do works, what it must not do (`GET` and `SET` of `cvestream:v1:token:*`, `SET` of `cvestream:v1:endpoint:<id>`, `SADD endpoints`) is refused with `NOPERM`.

The passwords in `deploy/valkey/dev-secrets/` are **public and for development only**. Production ships its own `users.acl`, with its own hashes, as a secret.

## Compatibility rules

- The prefix `cvestream:v1:` is the contract version. `cvestream:v1:schema` holds `1` and stays `1` for as long as the `v1` layout and the `v1` models stay compatible.
- Within `v1`, additions the daemon ignores are allowed only where this document says so; an unknown field in an endpoint object, or a `token` or `id` in it, is **rejected**, not ignored (the models are strict).
- A breaking change (a renamed key, another value format, a new required field) means a new prefix `cvestream:v2:*` and a new `schemas/config-v2.json`; the daemon that understands it reads that prefix. A `v1` daemon never reads `v2` keys.
- The JSON Schema `schemas/config-v1.json` is the published form of the values. A tool that edits `endpoint:<id>` or `endpoint:<id>:products` can validate its JSON against the matching definitions (`EndpointConfig` minus `id`, `token` and `products`; the product item) before writing.
- Reads are best-effort consistent (the revision bracket); a tool needing a strict snapshot should use the write protocol (`WATCH`) for its own reads.
