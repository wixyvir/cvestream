# Operations

## TL;DR

- `cvestream run` is the daemon: it polls the sources, decides what each team gets and serves one RSS feed per endpoint on `:8080` (public). Health and status are on `:9090` (private, never publish it).
- Self-hosted: `deploy/compose/compose.yml` with your `config/endpoints.yaml`. One replica only.
- Kubernetes: `deploy/k8s/base` (YAML provider) or `deploy/k8s/overlays/example` (Valkey provider, example Valkey, Ingress for `/feeds/` only), applied with `kubectl apply -k`. One replica, `Recreate`, block storage for `/data`. See [Kubernetes](#kubernetes).
- Releases: tag `vX.Y.Z` on `main` publishes the multi-arch image `X.Y.Z`, `X.Y`, `latest` to GHCR and a GitHub Release; pin `X.Y.Z`. See [Releases and image tags](#releases-and-image-tags).
- Feed URL: `http://<host>:8080/feeds/<token>.xml`. The token is the access control: put an HTTPS reverse proxy in front and **turn off its path logging for `/feeds/`**.
- Health: `cvestream healthcheck [--ready] [--port N]`, `/healthz`, `/readyz`, `/status`. The image already has a `HEALTHCHECK`.
- Logs are JSON lines on stdout. `cycle.summary` says what each cycle did; `source.failed`, `source.stale`, `config.rejected` and `pipeline.failed` are the ones to alert on.
- Edit `endpoints.yaml` (or the Valkey store, `config_provider: valkey`) and the daemon picks it up at the next cycle, no restart. Rotate a token by editing it.
- With the Valkey provider the daemon waits for Valkey at startup (`valkey.startup_timeout`), exits `3` when it stays unreachable or refuses the credentials, and rides out an outage on the last valid configuration (see [Valkey](#valkey)).
- Back up the state with `cvestream db backup <file>`.
- A nightly GitHub workflow (`source-drift`) runs the live contract tests against the real source APIs and opens or updates one `source-drift` issue when a source changed or was unreachable.

## Run

| How | Command |
|---|---|
| Locally | `uv run cvestream run` (needs `endpoints.yaml`, writes `./data/cvestream.db`) |
| One cycle, then exit | `uv run cvestream poll --once` |
| Container | `docker run -v data:/data -v ./config:/config:ro -e CVESTREAM_ENDPOINTS_FILE=/config/endpoints.yaml -p 8080:8080 ghcr.io/wixyvir/cvestream:0.1.0` |
| Self-hosted compose | `cd deploy/compose && mkdir config && cp ../../endpoints.example.yaml config/endpoints.yaml`, edit it, then `docker compose up -d` |
| Kubernetes | `kubectl apply -k deploy/k8s/overlays/example` (see [Kubernetes](#kubernetes)) |
| Dev stack | `docker compose watch` (serves the placeholder tokens of `endpoints.example.yaml`; a `valkey` service starts with it, use it with `CVESTREAM_CONFIG_PROVIDER=valkey`) |

`deploy/compose/compose.yml` runs `ghcr.io/wixyvir/cvestream`, with a read-only root filesystem (`/tmp` is a `tmpfs`), no capabilities, `no-new-privileges`, a named volume on `/data` and only port 8080 published. `config/` is gitignored: never commit it, it holds your tokens.

**Mount the configuration directory, not the file.** An editor that saves by replacing the file gives it a new inode, and a single-file bind mount keeps showing the old content: hot reload would silently stop working. The compose file mounts `./config` on `/config`, read-only.

`poll --once` is a one-shot: fetch each enabled source once, run one cycle, log one `cycle.summary` and exit `0`, also when a source failed (the failure is in the summary). Do not run it against the database of a running daemon: both would write (see Limits).

## Startup and shutdown

Startup order: logging, state migrated (`state.migrated`), private listener up (`/healthz` 200, `/readyz` 503), configuration loaded, stored feeds loaded into memory, worker and one loop per source started (`app.started`), public listener up, `/readyz` 200 (`app.ready`). The first fetch happens right away.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | clean stop (SIGTERM or SIGINT), or a successful `poll --once` |
| `1` | unexpected error, or a listener could not start or died (`app.failed`) |
| `2` | user error: invalid settings, invalid state URL or database, invalid `endpoints.yaml` or invalid (or not yet seeded) Valkey configuration (`config.rejected`) |
| `3` | configuration unavailable (`config.unavailable`): `endpoints.yaml` missing or unreadable; with Valkey, the store stayed unreachable for `valkey.startup_timeout`, or it refused the credentials or the permission (at once, no retry) |

A startup failure prints one line on stderr. The traceback of an unexpected error is shown only with `--debug` (`cvestream --debug run`).

SIGTERM or SIGINT: `/readyz` turns 503 (`app.stopping`), the source loops stop, the pipeline run in progress **finishes** (it is never cancelled), both listeners stop, the HTTP client and the state close (`app.stopped`). Give the container a grace period of 60 s (`stop_grace_period: 60s`, `terminationGracePeriodSeconds: 60`).

## Health

All on the private listener (`http.private`, default `:9090`):

| Path | Answer |
|---|---|
| `/healthz` | `200 {"status": "ok"}` while the event loop responds |
| `/readyz` | `200 {"status": "ready"}` once the configuration, the state and the feed cache are loaded; `503 {"error": {"code": "not_ready", ...}}` before that and during shutdown. It does not depend on the sources: a source outage never makes the daemon unready |
| `/status` | per source and per endpoint, see below |

`cvestream healthcheck` asks `/healthz` on `127.0.0.1:9090` with the standard library only, prints nothing and exits `0` on HTTP 200, `1` otherwise (2 s timeout, no proxy). `--ready` asks `/readyz`; `--port` changes the port when you changed `http.private.port`. The runtime image runs `cvestream healthcheck` as its `HEALTHCHECK`; the image has no `curl`, so orchestrator probes use the same command (`docker compose exec cvestream cvestream healthcheck --ready`). The command always asks `127.0.0.1`: keep `http.private.host` at `0.0.0.0` (the default) or `127.0.0.1`, because binding the private listener to one specific non-loopback address makes the probes fail.

## `/status`

No envelope. Datetimes are ISO 8601 with `Z`. No token and no feed path appears in it.

```json
{
  "sources": {
    "euvd": {
      "activity": "idle",
      "activity_since": "2026-10-26T20:15:02Z",
      "next_fetch_at": "2026-10-26T20:31:20Z",
      "interval_seconds": 900,
      "last_success_at": "2026-10-26T20:15:00Z",
      "last_error": {"type": "SourceTransportError", "at": "2026-10-26T20:00:00Z"},
      "cursor": "2026-10-26T20:10:00Z",
      "stale": false
    }
  },
  "endpoints": {
    "team-php": {"item_count": 4, "last_render_at": "2026-10-26T20:15:02Z"}
  }
}
```

- `activity` says what the source loop is doing now, since `activity_since`:
  - `fetching`: a fetch is running (an EUVD first fetch pages for several minutes);
  - `processing`: the fetch is over, successful or not, and its pipeline run is pending or running;
  - `idle`: waiting for the next fetch at `next_fetch_at` (the interval plus jitter); also the state before the first fetch, with `activity_since` `null`;
  - `crashed`: the loop crashed and the supervisor restarts it at `next_fetch_at`.

  `next_fetch_at` is `null` unless the source is `idle` or `crashed`. With `last_success_at` `null`, `fetching` tells a first fetch still in progress from one that never started.
- `stale` is true when the source has had no success for more than three intervals; for a source that has not succeeded yet, the time is counted from the start of the daemon. The daemon logs `source.stale` (warning) at the same moment.
- `activity`, `last_success_at` and `last_error` live in memory: they are lost at restart. `last_error` keeps the last failure (a `SourceError`, or the exception type of a crash of the source loop), even after the source recovered.
- `endpoints` lists the enabled endpoints. `last_render_at` is `null` for an endpoint that no run has rendered yet.

## Logs

One JSON object per line on stdout (`CVESTREAM_LOG__FORMAT=console` for humans). Events are named `<area>.<past-participle>`; the standard fields are `source`, `endpoint_id`, `vuln_id`, `count`, `duration_ms`, `error_type`.

| Event | Level | When | Fields |
|---|---|---|---|
| `app.started` / `app.ready` / `app.stopping` / `app.stopped` | info | lifecycle | `sources` (started) |
| `app.failed` | error | a listener could not start or died | `listener` or `component`, `error_type` |
| `state.migrated` | info | startup | `revision` |
| `source.fetched` | info | a fetch succeeded | `source`, `count`, `skipped`, `duration_ms` |
| `source.failed` | warning | a fetch failed (`SourceError`): its cursor does not advance, the other sources are unaffected | `source`, `error_type`, `message` |
| `source.stale` | warning | no success for more than 3 intervals, checked after a failed fetch, a rolled-back run or a crash | `source`, `last_success_at` |
| `source.crashed` | error | an unexpected exception in a source loop (with traceback); the loop restarts after 1, 2, 4... s, capped at 300 s or the source interval, whichever is longer, so a crashing source never polls more often than a healthy one | `source` |
| `pipeline.failed` | error | a cycle raised (with traceback): the run is rolled back, the previous feeds keep being served | `error_type` |
| `pipeline.completed` | info | a run committed | `count`, `records`, `changed`, `touched`, `duration_ms` |
| `publish.decided` | info | an item was published to an endpoint | `endpoint_id`, `vuln_id`, `kind` |
| `publish.skipped` | warning | a decision was dropped because its GUID is already stored for another vulnerability (two advisory-only vulnerabilities sharing a primary ID); the run goes on | `reason` (`guid_collision`), `endpoint_id`, `vuln_id`, `kind` |
| `config.loaded` | info | the configuration was loaded at startup (`run` and `poll --once`) | `count`, `revision` |
| `config.reloaded` | info | a changed configuration was loaded | `count`, `revision` |
| `config.rejected` | warning (error at startup) | the new content is invalid; the last valid configuration stays. With Valkey the errors name the key at fault (`cvestream:v1:endpoint:team-php:products: invalid JSON`) | `errors` (field errors, never a token or a value) |
| `config.unavailable` | warning (error at startup) | the file or the Valkey store cannot be read; the last valid configuration stays. At startup with Valkey: a warning per failed attempt (`retry_in` seconds), an error on the last one. `error_type` `ConfigAccessDeniedError` is a refused login or permission (not retried), `ConfigConflictError` a revision that kept moving during a read | `error_type`, `retry_in` (startup retries) |
| `feed.served` | info | a feed request for a known token | `endpoint_id`, `method`, `status`, `duration_ms` |
| `feed.not_found` | info | any other `GET`/`HEAD` | `method` |

`cycle.summary` (info) is logged once per cycle:

```json
{"event": "cycle.summary", "sources": {"euvd": {"result": "ok", "records": 8, "skipped": 0}, "cisa_kev": {"result": "failed", "error_type": "SourceTransportError"}}, "published": {"team-php": 0, "team-go": 2}, "score_unknown": 1, "versions_unknown": 0, "count": 2, "duration_ms": 740}
```

- `sources`: each source of the cycle, `ok` with `records` (what the adapter returned) and `skipped` (records the adapter dropped), or `failed` with `error_type`.
- `published`: items published per enabled endpoint, `0` included. A permanent `0` for every endpoint is worth a look.
- `score_unknown` and `versions_unknown`: published items let through because their score or their versions are unknown (recall over volume).

A fetch outcome, failed ones included, triggers a cycle: a failed source still reloads the configuration, slides the 14-day window and appears in the summary. The cvestream logs never contain a token or a feed path.

## Source drift

`.github/workflows/source-drift.yml` runs every night (04:17 UTC) and on demand, from `main`. It runs `uv run pytest -m live tests/contract`: every contract test marked `live` (EUVD, CISA KEV, CERT-EU and each later source), against the real APIs, with the same HTTP client as the daemon. Tests are selected by marker, so a new source's live test runs without editing the workflow. It uses a few Actions minutes per night (private repository) and only the `GITHUB_TOKEN`.

What a `source-drift` issue means: a live test failed. Either the source changed its API or payload (fields renamed, type changed, ordering, pagination, URL), which would make the daemon skip records or fail the source, or the source was down, slow or rate-limiting that night.

- The workflow opens **one** issue labelled `source-drift`, then adds a comment on each later failing night while the issue stays open. It never opens a second one and never closes it. It creates the label itself on its first failure.
- The issue (or comment) links the run, names the commit tested and lists the failing tests (the `FAILED` and `ERROR` lines of pytest).
- A run that fails before the tests (checkout, install) also reports, with "No test result" instead of a list. A run where pytest prints no `FAILED` or `ERROR` line (no `live` test collected, the test step timed out, pytest crashed) reports "No failing-test line".
- A green run opens nothing. A run that hits the 20-minute job timeout is cancelled and reports nothing: look at the Actions tab. A hung API fails the test step at 15 minutes and does report.

Triage:

1. Open the run linked in the issue and read the failing assertion.
2. Reproduce locally from a direct connection: `uv run pytest -m live tests/contract -v -rfE`. Behind a TLS-intercepting proxy the live tests fail by design (the client ignores proxy settings).
3. Compare the live payload with the recorded fixtures in `tests/fixtures/<source>/` (how each was recorded: `tests/fixtures/README.md`).
4. Check the daemon side: `source.failed` with `SourceContractError` or `SourcePayloadError` in the logs, and `last_error` and `stale` in `/status` (see Logs and `/status` above).
5. One failing night followed by green ones is a transient outage: close the issue with a note.

Fix: update the adapter in `src/cvestream/sources/`, re-record the fixtures (never edit them by hand), update the contract tests and `docs/sources.md`, in a pull request.

Close: by hand, once the fix is on `main` and a run is green (next night, or run it by hand).

Run it by hand: Actions tab, `source-drift`, "Run workflow", or

```sh
gh workflow run source-drift.yml --repo wixyvir/cvestream [--ref <branch>]
gh run watch --repo wixyvir/cvestream
```

A `--ref` other than `main` runs that branch's copy of the workflow (useful to test a change), but the workflow file must already exist on `main`.

## Feeds

- URL: `http://<host>:8080/feeds/<token>.xml`. Only `GET` and `HEAD`; any other method on that path is `405`. There is no trailing-slash redirect: `/feeds/<token>.xml/` is a `404` like any other path.
- An unknown, revoked or disabled endpoint's token, and any other path, return `404` with an empty body (never `401` or `403`).
- Headers: `Content-Type: application/rss+xml; charset=utf-8`, `ETag`, `Last-Modified`, `Cache-Control: max-age=300`. `If-None-Match` (which wins) or `If-Modified-Since` that matches returns `304` with no body.
- The `ETag` is the SHA-256 of the document and `Last-Modified` is the last time it changed, so an unchanged feed answers `304` to well-behaved readers.
- Window: the last 14 days of publications, newest first. A vulnerability that becomes known-exploited or whose severity band rises is published again as a new item prefixed `[UPDATE]`.
- An item's title names the CVE (or EUVD ID), the affected product it matched as the source names it (`CVE-2026-1234 in PHP Group PHP`, or your entry's name when the vulnerability lists no matching product), the severity band and `EXPLOITED` when it is in CISA KEV or EU KEV: `CVE-2026-1234 in PHP Group PHP: severity critical, EXPLOITED`. An update reads `[UPDATE] … now EXPLOITED, severity high` or `[UPDATE] … severity raised to critical`.
- An item shows, one fact per line: the CVE and EUVD IDs, the first publication date and the last update date given by the sources (UTC days, `unknown` when no source gives one), the matched products (`versions unknown` when the versions are not known), the severity band and the highest CVSS score, each source's score with its CVSS version, the known-exploited status (CISA KEV, EU KEV, the day each list added it, ransomware use), the affected products and versions, the description and links to every source record. A missing value reads `unknown`, never `0`. The title keeps the severity recorded when the item was published; the body follows the current merged data.
- The item date (`pubDate`, what readers show) is when cvestream published the item, not when the vulnerability was disclosed: an old CVE that just became known-exploited shows today's date and its real age under `Published`.
- The description is simple HTML, the standard for RSS: bold labels, line breaks, the source records and the references as links (only `http` and `https` URLs become links). Text from the sources is escaped, so markup in a vulnerability description shows as text and cannot inject anything. There is no Slack-specific markup; Slack's RSS app shows the text without the formatting.
- A configured, enabled endpoint that no run has rendered yet (first start, new endpoint) serves a valid empty channel until its first cycle.

## Token rotation

1. Generate a new token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Replace the endpoint's `token` in `endpoints.yaml`.
3. At the next cycle the daemon logs `config.reloaded`; the new URL serves the feed and the old one returns `404`.
4. Give the team the new URL: they must resubscribe their channel.

With the Valkey provider, step 2 is, as `cvestream-admin` (until the operator CLI exists): `SET cvestream:v1:token:<id> <new token>` then `INCR cvestream:v1:revision`, inside one `MULTI` so a reader never sees one without the other (see [config-contract.md](config-contract.md)). :factory:

## Valkey

With `config_provider: valkey` the configuration lives in a Valkey service ([config-contract.md](config-contract.md) is the contract, [configuration.md](configuration.md#valkey-provider) the settings). :factory:

- **Running it:** the dev stack has a `valkey` service (`compose.yml`, internal network, no host port, AOF on the `valkey-data` volume). In production run `valkey/valkey:9.1.3` (or newer 9.1) with [`deploy/valkey/valkey.conf`](../deploy/valkey/valkey.conf) and your own ACL file, on a private network that only the daemon, the operator and the connectors reach. Never publish the port.
- **ACL users:** `cvestream-daemon` reads `cvestream:v1:*` and writes nothing; `cvestream-admin` reads and writes `cvestream:v1:*` and nothing else; `connector-<name>` users read the configuration and write only `endpoint:*:products` and `revision`, and can neither read nor write `token:*`. `default` is disabled. The `users.acl` in the repository carries SHA-256 hashes of public development passwords: **write your own** (`printf '%s' "$PASSWORD" | sha256sum`) and keep the file in a secret.
- **Rotating a Valkey password:** compute the new hash, replace the `#<hash>` of the user in `users.acl`, reload the ACL (`ACL LOAD` as a user allowed to, or restart Valkey), then update the secret file the daemon reads (`CVESTREAM_VALKEY__PASSWORD_FILE`) and restart the daemon. A daemon with the wrong password logs `config.unavailable` (`ConfigAccessDeniedError`) at each cycle and keeps serving its last configuration; at startup it exits `3` at once.
- **Persistence and backup:** AOF with `appendfsync everysec` (at most one second of writes lost on a crash), data in `/data`. Back it up by copying `/data` (the AOF directory, plus the RDB snapshot Valkey writes on its default save points) from a replica or after `BGSAVE`; or export the configuration with the operator CLI once it exists. The configuration is small: losing the store means re-seeding it.
- **Outage while running:** each cycle logs a `config.unavailable` warning and uses the last valid configuration. Feeds and polling continue, `/readyz` stays 200, the daemon does not exit. A cycle is delayed at most by the 5 s socket timeouts. When Valkey is back the next cycle reads the revision and reloads if it changed.
- **Outage at startup:** the daemon waits up to `valkey.startup_timeout` and exits `3`. Start Valkey first, or let the orchestrator restart the daemon.
- **Unseeded store:** a Valkey without `cvestream:v1:schema` is rejected with `config.rejected` ("the store is not seeded") and exit `2`: seed it (see [config-contract.md](config-contract.md)).

## Security

- Publish only port 8080. `/healthz`, `/readyz` and `/status` are private; the compose file does not publish 9090.
- The token is the only access control. cvestream never logs it (`feed.served` carries the `endpoint_id`), and uvicorn's own access log is off.
- **Reverse-proxy and Ingress access logs contain the token** (it is in the request path). Disable or mask path logging for `/feeds/`, and restrict who can read those logs.
- Rate limiting belongs to the reverse proxy.
- The container runs as UID 999 with a read-only root filesystem and no capabilities.

## Kubernetes

What ships, under `deploy/`:

| Path | What it is |
|---|---|
| `k8s/base` | cvestream alone, with the YAML provider: `Deployment`, `PersistentVolumeClaim`, the `cvestream-public` (8080) and `cvestream-private` (9090) Services, a `NetworkPolicy` |
| `k8s/overlays/example` | the base switched to the Valkey provider, in the namespace `cvestream`, plus an example single-instance Valkey (`StatefulSet`, headless Service, `NetworkPolicy`), an `Ingress` for `/feeds/` only and a Secret example |
| `valkey/kustomization.yaml` | generates the `valkey.conf` ConfigMap and the `users.acl` Secret that the example Valkey mounts |

Build with `kubectl kustomize deploy/k8s/overlays/example`, apply with `kubectl apply -k`. Make your own overlay instead of editing the example: namespace, image tag, Ingress host, class and TLS Secret, NetworkPolicy peers, storage class, Secrets. Kustomize refuses files outside an overlay's directory in a generator, so reference the directories with `resources:` (as the example does for `../../../valkey`).

Prerequisites:

- A namespace with the `restricted` Pod Security Standard: `kubectl create namespace cvestream && kubectl label namespace cvestream pod-security.kubernetes.io/enforce=restricted`. The manifests are admitted under it.
- A CNI that enforces `NetworkPolicy`. The DNS egress rule admits the `k8s-app: kube-dns` pods of `kube-system`: with NodeLocal DNSCache or another DNS deployment, adapt that peer in your overlay, or name resolution, hence every source, fails.
- Direct HTTPS egress to the sources (cvestream ignores proxy variables).
- A block-storage `StorageClass` for the SQLite volume (`cvestream-data`, `ReadWriteOnce`). Never NFS, SMB or CIFS.

Secrets (never commit real values):

- YAML provider (the base): `kubectl -n <ns> create secret generic cvestream-endpoints --from-file=endpoints.yaml=<file>`. It is mounted as the `/config` directory, never with `subPath`, so a Secret update reaches the pod and the daemon reloads at its next cycle.
- Valkey provider (the example overlay): the Secret `cvestream-valkey` (key `password`, the password of `cvestream-daemon`) and the ACL Secret generated from `deploy/valkey/users.acl`. The committed `users.acl` holds the hashes of public development passwords: for production, ship your own `users.acl` (for example a `secretGenerator` with `behavior: replace` in your overlay) and a matching `cvestream-valkey` Secret. `secret.example.yaml` contains placeholders only: its `password` must be the one whose hash is on the `cvestream-daemon` line of the `users.acl` you deploy (with the committed file, the public dev password in `deploy/valkey/dev-secrets/cvestream-daemon.password`, for a test cluster only), otherwise Valkey refuses the daemon and it exits with code 3.

Private image: `wixyvir/cvestream` is a private repository, so its GHCR package is private too. Create a pull Secret with a token that has `read:packages` (`kubectl create secret docker-registry ghcr-pull --docker-server=ghcr.io --docker-username=<user> --docker-password=<token>`) and uncomment `imagePullSecrets` in your overlay, or make the package public.

Invariants, and why (`tests/unit/test_deploy_manifests.py` guards them on the base `Deployment`, except the ConfigMap note):

- One replica and `strategy: Recreate`: SQLite has one writer, and the old pod must be gone before the new one opens the database. A `ReadWriteOnce` volume also keeps a second writer away.
- `terminationGracePeriodSeconds: 60`: the pipeline run in progress finishes on SIGTERM.
- `enableServiceLinks: false`: Services named `cvestream-*` would otherwise inject `CVESTREAM_PUBLIC_PORT` and similar variables into the `CVESTREAM_` namespace that the settings read.
- `automountServiceAccountToken: false`, read-only root filesystem with writable `/data` and `/tmp`, UID 999, all capabilities dropped, `RuntimeDefault` seccomp.
- Exec probes: `startupProbe` and `readinessProbe` run `cvestream healthcheck --ready`, `livenessProbe` runs `cvestream healthcheck`, with 5 s timeouts (each starts Python). The startup budget is 120 s.
- The settings ConfigMap is read at start: after a change run `kubectl -n <ns> rollout restart deploy/cvestream`.

Health probes and the private listener: `cvestream healthcheck` always asks `127.0.0.1`. Keep `http.private.host` at `0.0.0.0` (the default) or `127.0.0.1`: binding it to one specific pod address makes the probes fail. If you change `http.private.port`, pass `--port` to the probes.

Exposure: route only `cvestream-public`, only `/feeds/`, through an Ingress or a Gateway (`overlays/example/ingress.yaml`). The controller's access log contains the token: turn path logging off for that route (Traefik: an `IngressRoute` with `observability.accessLogs: false`). `cvestream-private` is never exposed: use `kubectl port-forward svc/cvestream-private 9090` (it enters the pod directly, `NetworkPolicy` does not filter it) or a pod with the right label.

`NetworkPolicy` labels:

| Label | Opens |
|---|---|
| `cvestream.io/public-access: "true"` | 8080 of cvestream, from that pod in any namespace (the example overlay replaces this peer by the Ingress controller's namespace and labels: adapt it) |
| `cvestream.io/private-access: "true"` | 9090 of cvestream, from that pod in the same namespace |
| `cvestream.io/valkey-access: "true"` | 6379 of the example Valkey, from that pod in the same namespace (connectors, an operator CLI pod) |

cvestream may only reach DNS (`kube-dns` in `kube-system`), TCP 443 and, in the example overlay, Valkey. The example Valkey has no egress.

Valkey (example overlay): a single instance with its AOF on a `volumeClaimTemplates` volume and no TLS inside the cluster; use your own Valkey for anything more (`CVESTREAM_VALKEY__URL`, `valkeys://` across hosts). A change to `valkey.conf` or `users.acl` rolls the pod (Kustomize hashes the generated names). Admin access without a pod of your own: `kubectl -n <ns> exec -it cvestream-valkey-0 -- valkey-cli --user cvestream-admin --pass <password>` (inside the pod, so `NetworkPolicy` does not apply). When Valkey is down at start, the daemon retries for `valkey.startup_timeout` (60 s), then exits with code 3 (`CrashLoopBackOff`); while running it keeps the last configuration.

The example Valkey starts empty: until it is seeded the daemon rejects the store and exits with code 2 (`CrashLoopBackOff`). Seed it as `cvestream-admin` with the transaction of [config-contract.md](config-contract.md#valkey-cli-example-admin), sent through `kubectl -n <ns> exec -i cvestream-valkey-0 -- valkey-cli --no-auth-warning --user cvestream-admin --pass <password>` instead of `docker compose exec`; the pod restarts by itself and becomes ready.

Backup: `kubectl -n <ns> exec deploy/cvestream -- cvestream db backup /data/backup-<date>.db`, then `kubectl -n <ns> cp <pod>:/data/backup-<date>.db .`.

## Releases and image tags

| Tag | Moves when |
|---|---|
| `X.Y.Z`, `X.Y`, `latest` | a release `vX.Y.Z` is published |
| `edge` | every push to `main` |
| `sha-<short>` | every push to `main` |

Pin `X.Y.Z` in production. Releases are multi-arch (`linux/amd64`, `linux/arm64`).

Release process:

1. In a pull request, bump `[project].version` in `pyproject.toml`, run `uv lock` (the lock records the project version) and bump the image tag in `deploy/k8s/base/deployment.yaml` and `deploy/compose/compose.yml`. A test fails when a pin differs from `pyproject.toml`.
2. Merge it, then tag `main`: `git tag vX.Y.Z origin/main && git push origin vX.Y.Z`.
3. `release.yml` runs: it refuses a tag that is not exactly `v` + the `pyproject.toml` version (and any pre-release tag such as `v0.2.0-rc.1`), before building anything; then it builds amd64 and arm64, pushes the tags above with OCI labels, an SBOM and a max-mode provenance attestation, and creates the GitHub Release with generated notes.

A wrong tag fails before anything is pushed: delete it (`git push origin :refs/tags/vX.Y.Z`) and tag again.

Verify a release:

```sh
docker buildx imagetools inspect ghcr.io/wixyvir/cvestream:X.Y.Z
docker buildx imagetools inspect ghcr.io/wixyvir/cvestream:X.Y.Z --format '{{ json .Provenance }}'
docker buildx imagetools inspect ghcr.io/wixyvir/cvestream:X.Y.Z --format '{{ json .SBOM }}'
```

The SBOM and the provenance are BuildKit attestations stored with the image. GitHub artifact attestations need a public repository (or GitHub Enterprise Cloud), so that step runs only on a public repository; then `gh attestation verify oci://ghcr.io/wixyvir/cvestream:X.Y.Z --repo wixyvir/cvestream` works too.

## Backup

`cvestream db backup <file>` copies the SQLite state with the online backup API, a consistent snapshot while the daemon runs. It refuses to overwrite an existing file.

```sh
docker compose exec cvestream cvestream db backup /data/backup-2026-10-26.db
docker compose cp cvestream:/data/backup-2026-10-26.db .
```

Restore: stop the daemon, replace `cvestream.db` in the data volume with the backup, start the daemon. Without the state, nothing remembers what was published: every endpoint would be backfilled with the last 7 days and the team would see those items again.

## Limits

- **Single replica.** Never two daemons on one state database, and never `poll --once` against the database of a running daemon: SQLite allows one writer, and a second one can fail with `SQLITE_BUSY` (see `_bmad-output/implementation-artifacts/deferred-work.md`).
- SQLite needs block storage (a local disk or a block volume), never NFS, SMB or CIFS.
- Source status (`last_success_at`, `last_error`) is not persisted.
