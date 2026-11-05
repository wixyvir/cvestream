---
baseline_commit: 6c02c2c
---

# Story 3.1: Valkey configuration provider

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want cvestream to read its configuration from a Valkey service on a private network, with per-role access rules,
so that other tools (BoM connectors, CD workers) can maintain product lists through a documented, versioned contract.

## TL;DR

- **New code:** `config/keys.py` (the `cvestream:v1:*` key layout, no I/O, no `valkey` import) and `config/valkey_provider.py` (the only module that imports `valkey`): consistent snapshot read, write protocol, error mapping. `settings.py` gains `config_provider: valkey` and a `valkey` section (`url`, `password` secret, `startup_timeout`). `app/runtime.py` builds the provider from the settings and, for Valkey, retries at startup until `valkey.startup_timeout` then exits with code 3.
- **No contract change:** `config/models.py` and `schemas/config-v1.json` stay as they are. The Valkey snapshot is assembled into the same `ConfigDocument` dict the YAML provider validates, through the existing `build_snapshot()`.
- **Server files (story 3.3 mounts them, keep these exact paths):** `deploy/valkey/valkey.conf` (AOF `everysec`, `aclfile /usr/local/etc/valkey/users.acl`, `dir /data`) and `deploy/valkey/users.acl` (`default` off, `cvestream-daemon` read-only, `cvestream-admin` read/write on `cvestream:v1:*`, `connector-example` limited to `endpoint:*:products` and `revision`, never `token:*`). **Valkey 9.1 ACL files accept no comment line**: every non-blank line must start with `user`. Dev-only passwords live in `deploy/valkey/dev-secrets/*.password`; `users.acl` holds their SHA-256 hashes.
- **Tests:** unit tests need no server. `valkey`-marked integration tests (one per ACL restriction, snapshot read, write conflict retry, auth failure) run against a server named by `VALKEY_TEST_ADDRESS=127.0.0.1:<port>` and get through pytest-socket with `@pytest.mark.allow_hosts`. Without the variable they skip, except on CI (`CI` set), where they fail. `tests/integration/test_startup_requires_valkey.py` proves exit code 3 against a closed port (no server needed, always runs).
- **Infra:** `compose.yml` gains a `valkey` service on an internal network (no host port, AOF volume, repo config mounted read-only); `ci.yml` starts `valkey/valkey:9.1.3` with **a `docker run` step after checkout** (a `services:` container starts before checkout, so it cannot mount the repo's `deploy/valkey/`) and exports `VALKEY_TEST_ADDRESS`.
- **Gotchas found while preparing this story:** valkey-py 6.2.0 has **no socket timeout by default** (a paused Valkey would hang the pipeline thread forever: set `socket_timeout` and `socket_connect_timeout`); its `AuthenticationError` **subclasses** `ConnectionError` (catch it first); `INCR` needs read **and** write key permission (`%RW~`), `WATCH` needs the key to match any pattern; if `users.acl` has no `default` line, Valkey recreates `default` as `on nopass +@all`.
- **Docs:** new `docs/config-contract.md`; `docs/configuration.md`, `docs/operations.md`, `settings.example.yaml`, `README.md`, `CLAUDE.md`, `architecture.md` (ACL lines, CI step, error classes, full reload) and `project-context.md` updated in the same PR.

## Acceptance Criteria

1. **Key layout and snapshot read (FR-21, FR-23).**
   **Given** `config/keys.py` and `config/valkey_provider.py`
   **When** the provider reads the configuration
   **Then** it uses only the `cvestream:v1:*` keys of `config/keys.py` (layout in Dev Notes, "Key layout and values"), and reads a consistent snapshot: `GET revision` → pipelined read of `schema`, the `endpoints` set and every `endpoint:{id}`, `endpoint:{id}:products`, `token:{id}` → `GET revision`; when the two revisions differ it retries, up to 3 retries, then raises `ConfigConflictError` so the caller keeps the last valid snapshot
   **And** the raw values are assembled into the v1 document (`{"version": <schema>, "endpoints": [...]}`) and validated with the Story 1.3 models through `build_snapshot()`; an invalid snapshot (bad JSON, wrong type, missing key, model error) raises `ConfigValidationError` naming the key or field, never a value, and the daemon keeps the last valid configuration and logs `config.rejected`.

2. **Write protocol.**
   **Given** `ValkeyConfigProvider.update(change)`
   **When** it writes
   **Then** it runs `WATCH cvestream:v1:revision` → reads the current document → applies `change` → validates the result with the same models → `MULTI` → writes → `INCR cvestream:v1:revision` → `EXEC`, and retries the whole sequence on `WatchError` (a concurrent writer), up to 5 attempts, then raises `ConfigConflictError`
   **And** an integration test (marker `valkey`) proves the retry with a second client that `INCR`s the revision between `WATCH` and `EXEC`.

3. **Settings.**
   **Given** `config_provider: valkey`
   **Then** `valkey.url` is required (`valkey://<user>@<host>:<port>/<db>` or `valkeys://...` for TLS), a URL carrying a password, a query string or another scheme is rejected with a message that does not echo the URL, the password comes from `valkey.password` (`CVESTREAM_VALKEY__PASSWORD_FILE`, or `CVESTREAM_VALKEY__PASSWORD`; both set is an error), and `valkey.startup_timeout` defaults to 60 s (ISO 8601 in the environment, `PT60S`)
   **And** `config_provider: yaml` keeps working exactly as before (default), with no Valkey import at runtime.

4. **Startup requires Valkey (exit code 3).**
   **Given** `config_provider: valkey`
   **When** the daemon (`cvestream run`, and `cvestream poll --once`) starts and Valkey is unreachable
   **Then** it retries with a capped exponential backoff (1 s doubling, at most 10 s, the last wait cut to the deadline) for `valkey.startup_timeout`, logging a `config.unavailable` warning per failed attempt, `/healthz` 200 and `/readyz` 503 meanwhile, then logs `config.unavailable` (error), prints one stderr line and exits with code **3** (`tests/integration/test_startup_requires_valkey.py`)
   **And** an authentication or permission failure (wrong password, unknown or disabled user, `NOPERM`) is not retried: it logs `config.unavailable` with `error_type=ConfigAccessDeniedError` and exits 3 at once, with a message that tells it apart from a network failure
   **And** an invalid configuration in Valkey at startup (including an unseeded store with no `schema` key) logs `config.rejected` and exits 2, as the YAML provider does.

5. **Outage while running.**
   **Given** a running daemon on the Valkey provider
   **When** Valkey becomes unreachable
   **Then** each pipeline run logs a `config.unavailable` warning and keeps using the last valid configuration; polling and feeds continue, `/readyz` stays 200, the daemon does not exit; a cycle is delayed at most by the client socket timeouts (5 s), never blocked
   **And** when Valkey is back, the next cycle reads the revision again and reloads if it changed
   **And** every 12th pipeline cycle reloads the configuration even when the revision did not change (safety net for a writer that forgot `INCR`).

6. **Server configuration and access rules.**
   **Given** `deploy/valkey/valkey.conf` and `deploy/valkey/users.acl`
   **When** `valkey/valkey:9.1.3` starts with them (`valkey-server /usr/local/etc/valkey/valkey.conf`, `deploy/valkey/` mounted on `/usr/local/etc/valkey`)
   **Then** AOF is on (`appendonly yes`, `appendfsync everysec`), `default` is disabled, `cvestream-daemon` can only read `cvestream:v1:*` (tokens included), `cvestream-admin` can read and write `cvestream:v1:*` and nothing else, and `connector-example` can read `schema`, `revision`, `endpoints`, `endpoint:*`, write only `endpoint:*:products` and `revision` (and run the write protocol), and can neither read nor write `token:*`
   **And** one `valkey`-marked integration test per restriction proves it (list in Dev Notes, "Integration tests"), and the provider itself works end to end as `cvestream-daemon` (read) and as `cvestream-admin` (write).

7. **Dev stack and CI.**
   **Given** `compose.yml`
   **Then** it has a `valkey` service on an internal network, no host port, the repo's `deploy/valkey/` mounted read-only, an AOF volume on `/data`; the app can switch to it with `CVESTREAM_CONFIG_PROVIDER=valkey` and reads its password from a compose secret file (`CVESTREAM_VALKEY__PASSWORD_FILE`), never an inline variable; `docker compose config` parses
   **And** the `ci` job starts `valkey/valkey:9.1.3` with the repo's `valkey.conf` and `users.acl` before `pytest`, sets `VALKEY_TEST_ADDRESS`, and the `valkey` tests run there (they cannot be skipped silently on CI); the `ci` job stays the only required check and its `permissions` are unchanged.

8. **Contract documentation.**
   **Given** `docs/config-contract.md`
   **Then** a third-party tool can seed and edit the configuration from it alone: key layout and value formats, the read protocol, the write protocol (with a `valkey-cli` example), the ACL users and an example connector line, how to generate a password hash, a pointer to `schemas/config-v1.json` for the JSON values, and the compatibility rules (`v1` namespace, `schema` = `1`).

9. **Layering, docs and no regression.**
   **Then** `valkey` is imported only by `cvestream.config.valkey_provider` (import-linter contract tightened), `schemas/config-v1.json` is unchanged, `settings.example.yaml`, `docs/configuration.md`, `docs/operations.md`, `README.md`, `CLAUDE.md`, `architecture.md` and `project-context.md` describe the new behaviour, and every gate in `CLAUDE.md` stays green (YAML provider behaviour, `test_restart_no_repost.py` included).

## Tasks / Subtasks

- [ ] **Task 1: dependency and layering** (AC: 9)
  - [ ] 1.1 `uv add 'valkey>=6.2.0'` (valkey-py 6.2.0, released 2026-10-12, `py.typed`, requires Python >= 3.11). Never edit `uv.lock` by hand. Do **not** add `valkey[libvalkey]` (C parser): not needed.
  - [ ] 1.2 `pyproject.toml` `[tool.importlinter]`: replace the "valkey only in config" contract by "valkey only in config.valkey_provider": add `cvestream.config.models`, `cvestream.config.provider`, `cvestream.config.keys`, `cvestream.config.yaml_provider`, `cvestream.config.schema` (and `cvestream.config` itself, the package `__init__`) to `source_modules`; keep `allow_indirect_imports = true`. Remove the "story 3.1 tightens this" comment and say that a new `config/` module must be added to the list. Run `uv run lint-imports`.
- [ ] **Task 2: settings** (AC: 3)
  - [ ] 2.1 `settings.py`: `ValkeySettings(BaseModel, extra="forbid")` with `url: str | None = None`, `password: SecretStr | None = None`, `startup_timeout: timedelta = Field(default=timedelta(seconds=60), gt=timedelta(0), le=timedelta(hours=1))`. `Settings.valkey: ValkeySettings = Field(default_factory=ValkeySettings)`; `config_provider: Literal["yaml", "valkey"] = "yaml"`.
  - [ ] 2.2 `url` validator (see Dev Notes, "Settings"): scheme `valkey` or `valkeys`, a host, an optional port, an optional `/<db>` path of digits, no password, no query, no fragment. Error messages never include the URL.
  - [ ] 2.3 `Settings` `model_validator(mode="after")`: `config_provider == "valkey"` requires `valkey.url`.
  - [ ] 2.4 `tests/unit/test_settings.py`: delete `test_valkey_config_provider_is_rejected_until_it_exists`; replace the test-only `_SettingsWithNestedSecret` by the real field (`CVESTREAM_VALKEY__PASSWORD_FILE` read, both forms set → `SettingsError`, trailing newline stripped); add URL validation cases (password in URL rejected and not echoed, `redis://` rejected, query rejected, `valkeys://` accepted, missing url with provider valkey rejected), `startup_timeout` default and `PT10S` from the environment.
- [ ] **Task 3: key layout** (AC: 1)
  - [ ] 3.1 `config/keys.py`: `PREFIX = "cvestream:v1:"`, `SCHEMA`, `REVISION`, `ENDPOINTS`, and `endpoint(id)`, `products(id)`, `token(id)` builders (they assume a valid slug; callers validate ids first). Plus `SCHEMA_VALUE = 1` taken from `config.models.SCHEMA_VERSION`. No I/O, no `valkey` import.
  - [ ] 3.2 `tests/unit/config/test_keys.py`: the exact strings (they are the public contract), and that no builder can produce a key under `token:` other than `token(id)`.
- [ ] **Task 4: provider errors** (AC: 1, 2, 4)
  - [ ] 4.1 `config/provider.py`: add `ConfigAccessDeniedError(ConfigUnavailableError)` (authentication or ACL refusal: not retried at startup) and `ConfigConflictError(ConfigUnavailableError)` (revision kept moving during a read, or write retries exhausted). Docstrings say which is retried.
  - [ ] 4.2 `tests/unit/test_errors.py` or `tests/unit/config/test_provider.py`: both inherit `ConfigError` → `CvestreamError` and end in `Error`.
- [ ] **Task 5: Valkey provider** (AC: 1, 2, 5)
  - [ ] 5.1 `config/valkey_provider.py`: `ValkeyConfigProvider` with `revision()`, `load()`, `update(change)`, `close()`, built by `ValkeyConfigProvider.from_settings(settings.valkey)` (see Dev Notes, "Provider design"). Client built once with `valkey.Valkey.from_url(url, password=..., socket_timeout=5, socket_connect_timeout=5, decode_responses=False)`.
  - [ ] 5.2 Snapshot read with the revision bracket and `SNAPSHOT_RETRIES = 3`; assembly of the document in a **pure function** (`assemble(raw) -> dict`, no client) so it is unit-tested without a server.
  - [ ] 5.3 Error mapping (Dev Notes, "Error mapping"): `AuthenticationError`/`AuthorizationError`/`NoPermissionError` → `ConfigAccessDeniedError`; other `ConnectionError`, `TimeoutError`, `BusyLoadingError` → `ConfigUnavailableError`; `WRONGTYPE`, bad JSON, bad UTF-8, missing keys → `ConfigValidationError(source="valkey", ...)` naming the key; `WatchError` → retry, then `ConfigConflictError`. Messages never contain a password, a token or a raw value; host, port and user name are fine.
  - [ ] 5.4 `update(change: Callable[[ConfigDocument], ConfigDocument]) -> str`: the write protocol of AC 2, writes every key of the new document, `SREM`/`DEL` the keys of removed endpoints, `SET schema 1`, `INCR revision`; returns the new revision. Validates before `MULTI` (an invalid result raises `ConfigValidationError` and writes nothing). Story 3.2 builds the CLI on it.
  - [ ] 5.5 `tests/unit/config/test_valkey_provider.py` (no server): `assemble()` cases (happy path, shorthand products, missing `schema` → validation error, bad JSON in one key names the key and not the value, endpoint JSON carrying `id`/`token`/`products` rejected, missing products key → `[]`, missing token → error naming the endpoint, invalid UTF-8, ids sorted, revision absent → `"0"`), the revision-bracket retry logic through a fake reader (Dev Notes, "Testability seam"), the error-message hygiene (a token value never appears in any message).
- [ ] **Task 6: pipeline and runtime wiring** (AC: 3, 4, 5)
  - [ ] 6.1 `config/provider.py`: `open_provider(settings) -> tuple[ConfigProvider, Callable[[], None]]`, the provider and its close function (YAML → `YamlConfigProvider(settings.endpoints_file)` and a no-op; Valkey → `ValkeyConfigProvider.from_settings(...)` and its `close`, imported **inside** the function so YAML mode never imports `valkey`). The `ConfigProvider` protocol and every test fake stay unchanged.
  - [ ] 6.2 `pipeline/run.py`: `reload_config(provider, current, *, force=False)` and `cycle(..., force_reload=False)`; with `force`, skip the revision comparison; log `config.reloaded` (with `count` and `revision`) only when the new snapshot differs from `current`. Add `revision` to `config.reloaded`.
  - [ ] 6.3 `app/scheduler.py` `PipelineWorker`: count runs; pass `force_reload=True` every `FULL_RELOAD_EVERY = 12` runs (not on the first). Unit test with the injected `run_cycle`.
  - [ ] 6.4 `app/runtime.py`: use `open_provider(settings)` in `_start` and `poll_once`; new `provider_factory` and `monotonic` injectables on `serve`/`poll_once`; a startup loader that, for Valkey, retries `ConfigUnavailableError` (not `ConfigAccessDeniedError`) with the backoff of AC 4 until `startup_timeout`, using the injected `sleep` and `monotonic`; YAML keeps today's behaviour (exit 3 at once when the file is unreadable). Log `config.loaded` (info, `count`, `revision`) once loaded. Call the close function returned by `open_provider` in `_shutdown` and in the `finally` of `poll_once`.
  - [ ] 6.5 `tests/unit/app/test_runtime.py` and `tests/unit/pipeline/test_run.py`: startup retries then exit 3 (fake provider + fake sleep/monotonic, count of attempts and waits `1, 2, 4, ...` capped and cut to the deadline), access denied → exit 3 with no retry, validation error → exit 2, `/readyz` 503 while retrying, `force_reload`, `config.unavailable` warning per run during an outage with the last snapshot kept.
- [ ] **Task 7: server files** (AC: 6)
  - [ ] 7.1 `deploy/valkey/valkey.conf` exactly as in Dev Notes ("Server files"), comments allowed there.
  - [ ] 7.2 `deploy/valkey/users.acl` exactly as in Dev Notes: four `user` lines, **no comment**, hashes of the dev passwords.
  - [ ] 7.3 `deploy/valkey/dev-secrets/{cvestream-daemon,cvestream-admin,connector-example}.password`: dev-only passwords (one line, no trailing newline). Hash: `printf '%s' "$(cat file)" | sha256sum`.
  - [ ] 7.4 `tests/unit/test_deploy_valkey.py` (no server): `users.acl` has only `user` lines, has `user default off ...`, each hash equals the SHA-256 of its dev secret file, no `>` plaintext password, no `allkeys`/`~*`/`+@all`, and the `connector-example` line has no pattern that matches `cvestream:v1:token:x` (use `fnmatch.fnmatchcase`); `valkey.conf` has `appendonly yes`, `appendfsync everysec` and `aclfile /usr/local/etc/valkey/users.acl`.
- [ ] **Task 8: integration tests against a real Valkey** (AC: 2, 4, 6)
  - [ ] 8.1 `tests/integration/conftest.py` (or a fixture module): `VALKEY_TEST_ADDRESS` → skip with a clear reason when unset, **fail** when unset and `CI` is set; admin cleanup fixture that deletes every `cvestream:v1:*` key the test wrote (via the `endpoints` set, then `schema`, `revision`); clients per user built from `deploy/valkey/dev-secrets/`.
  - [ ] 8.2 `tests/integration/test_valkey_provider.py` (`pytestmark = [pytest.mark.valkey, pytest.mark.allow_hosts([<host>])]`): every case of Dev Notes, "Integration tests".
  - [ ] 8.3 `tests/integration/test_startup_requires_valkey.py`: `serve()` with `config_provider=valkey`, a closed local port (bind a socket, read its port, close it), `startup_timeout=PT3S`, fake `sleep`/`monotonic` → returns 3, `config.unavailable` logged, `/readyz` never ready; this case needs no server and is **not** marked `valkey`. Add the wrong-password and disabled-`default` cases marked `valkey` (exit 3 at once, `ConfigAccessDeniedError`).
  - [ ] 8.4 Run them locally against a disposable container (Dev Notes, "Running the valkey tests locally"), then stop it.
- [ ] **Task 9: dev stack and CI** (AC: 7)
  - [ ] 9.1 `compose.yml`: `valkey` service and secrets as in Dev Notes ("compose.yml"); update the header comment ("Valkey joins in story 3.1" → how to switch the app to Valkey and seed it). `docker compose config` must parse.
  - [ ] 9.2 `.github/workflows/ci.yml`: a "Start Valkey" step after checkout and before `pytest`, a readiness wait, `VALKEY_TEST_ADDRESS` on the pytest step (Dev Notes, "CI"). Do not add a second job.
- [ ] **Task 10: documentation** (AC: 8, 9)
  - [ ] 10.1 New `docs/config-contract.md` (outline in Dev Notes).
  - [ ] 10.2 `docs/configuration.md`: `config_provider` (`yaml` | `valkey`), the `valkey.*` keys, secrets sentence ("the Valkey password will follow" → it exists now), "Reloading" for Valkey (revision key, full reload every 12 cycles).
  - [ ] 10.3 `settings.example.yaml`: `config_provider` comment and a commented `valkey:` section with every key and its `Env:` line.
  - [ ] 10.4 `docs/operations.md`: TL;DR bullet, exit code 3 now covers Valkey, `config.loaded`, `config.unavailable` wording for Valkey (startup retries, outage), a `## Valkey` section (running it, ACL users, rotating a Valkey password, AOF and backup, outage behaviour), token rotation note for Valkey (until story 3.2: `SET token:<id>` + `INCR revision` as `cvestream-admin`).
  - [ ] 10.5 `README.md`: one paragraph on the Valkey provider, and how to run the `valkey` tests (`VALKEY_TEST_ADDRESS`).
  - [ ] 10.6 `CLAUDE.md`: library ownership `valkey` only in `config/valkey_provider.py`; gates: the `valkey` tests need `VALKEY_TEST_ADDRESS`.
  - [ ] 10.7 `_bmad-output/planning-artifacts/architecture.md` and `_bmad-output/project-context.md`: amend the patterns changed here (Dev Notes, "Architecture amendments").
  - [ ] 10.8 `_bmad-output/implementation-artifacts/deferred-work.md`: update "Configuration reload waits for a source fetch" (still deferred, see Decisions) and record anything new you defer.
- [ ] **Task 11: gates and bookkeeping** (AC: 9)
  - [ ] 11.1 Every gate of `CLAUDE.md` in the worktree, plus the `valkey` tests against a disposable container. `docker build --target runtime -t cvestream:story-3-1-valkey-configuration-provider .`: try once; behind the proxy it fails on `ghcr.io` with x509: report it, do not disable verification, rely on CI.
  - [ ] 11.2 Dev Agent Record, File List, status `review` here and in `sprint-status.yaml`.

## Dev Notes

### Scope

- **In:** the Valkey provider (read and write protocol), its settings, startup and outage behaviour, the server files, the dev stack, the CI service, the contract doc.
- **Out (story 3.2):** every CLI write command (`endpoint`, `product`, `token`, `config export|import`). `update()` exists for 3.2 and for the tests; no CLI command calls it in 3.1. `cvestream config validate` keeps validating YAML files only.
- **Out (story 3.3):** Kubernetes manifests (the example Valkey `StatefulSet`), `release.yml`, `deploy/compose/compose.yml` (self-hosted, stays on YAML). 3.3 consumes `deploy/valkey/valkey.conf` and `users.acl` from this story: see "Interface for story 3.3".
- **Out (deferred, decision below):** a configuration-revision poll independent of source fetches.
- **No contract change:** `config/models.py` is untouched, so `schemas/config-v1.json` is untouched. If you find you must change a model, that is a contract change: regenerate the schema in the same commit and say why in the Completion Notes.

### Current state of the files this story touches

- `src/cvestream/config/provider.py`: `ConfigError` → `ConfigUnavailableError`, `ConfigValidationError(source, errors)`; `ConfigSnapshot(revision, endpoints, tokens)` (`tokens` is `repr=False`); `ConfigProvider` protocol (`revision()`, `load()`); `build_snapshot(data, *, revision, source)` validates with `ConfigDocument` and formats Pydantic errors without input values. Keep all of it; add the two error classes and `open_provider()`.
- `src/cvestream/config/yaml_provider.py`: `YamlConfigProvider(path)`; revision = SHA-256 of the bytes. Untouched: closing goes through the callable that `open_provider()` returns with the provider, so neither `YamlConfigProvider` nor the `ConfigProvider` protocol (and its test fakes) change.
- `src/cvestream/config/models.py`: `ConfigDocument(version: Literal[1], endpoints: list[EndpointConfig])`, strict, `extra="forbid"`. `EndpointConfig`: `id`, `name`, `enabled`, `min_cvss`, `kev_only`, `token` (`^[A-Za-z0-9_-]{32,128}$`), `products` (shorthand string or `ProductEntryConfig`: `name`, `aliases`, `exclude`, `min_version` (quoted string), `match_description`). Unique ids and tokens checked in `_unique`. `SCHEMA_VERSION = 1`. **Strict mode:** `min_cvss` must be a JSON number, `enabled` a JSON boolean, `version` the integer `1`; JSON from Valkey keeps these types, so strict mode works unchanged.
- `src/cvestream/settings.py`: `config_provider: Literal["yaml"]`; `_FileSecretsSource` already maps `CVESTREAM_<PATH>_FILE` to any nested `SecretStr` field and rejects the plain and file forms together, and strips one trailing newline. A `valkey.password: SecretStr | None` field gets `CVESTREAM_VALKEY__PASSWORD_FILE` for free (the existing test `_SettingsWithNestedSecret` was written for exactly this).
- `src/cvestream/pipeline/run.py`: `reload_config()` compares `provider.revision()` with `current.revision`, loads on change, logs `config.rejected` (warning, `errors`) or `config.unavailable` (warning, `error_type`) and keeps `current`; `cycle()` calls it first. Only `pipeline/run.py` opens `State.transaction()`: the provider never touches state.
- `src/cvestream/app/scheduler.py`: `PipelineWorker` runs `cycle` in a thread (`asyncio.to_thread`), keeps `self.snapshot`, swaps the feed cache. `run_cycle` is injectable (tests use it).
- `src/cvestream/app/runtime.py`: `_start` builds `YamlConfigProvider(settings.endpoints_file)` and `_load_snapshot` maps `ConfigUnavailableError` → exit 3, `ConfigValidationError` → exit 2; `poll_once` does the same. `serve` already takes injectable `clock`, `stop`, `random`, `sleep`, `server_factory`, `sources_factory`; `/readyz` turns 200 only after the configuration is loaded (`status.ready = True` at the end of `_start`). `EXIT_CONFIG_UNAVAILABLE = 3` exists.
- `src/cvestream/cli/main.py`: `run` and `poll` pass the exit code through; a `SettingsError`/`ValidationError` is one stderr line, exit 2, errors listed **without input** (so a bad `valkey.url` is never echoed by this path either).
- `pyproject.toml`: marker `valkey` already declared; `addopts = ["--disable-socket", "--allow-unix-socket", "-m", "not live", "--strict-markers"]` (so `valkey` tests are **selected** by default and must skip or connect); import-linter "valkey only in config" contract to tighten.
- `compose.yml`: only the `app` service (target `dev`, YAML provider with `endpoints.example.yaml`, ports 8080/9090 on loopback). `.devcontainer/` uses `compose.yml` + `compose.devcontainer.yml`: adding a service there is fine (it starts with the devcontainer).
- `.github/workflows/ci.yml`: one `ci` job (checkout, setup-uv, gates, image build, smoke test), `permissions: contents: read`, then `publish` on `main`.
- `docs/configuration.md` says "`config_provider` ... only `yaml` exists for now" and "the Valkey password will follow with the Valkey provider"; `settings.example.yaml` says the same; `docs/operations.md` exit code table says `3` = "`endpoints.yaml` missing or unreadable".

### Key layout and values (public contract v1)

Defined once in `config/keys.py`; `docs/config-contract.md` repeats it for third parties.

| Key | Type | Value | Notes |
|---|---|---|---|
| `cvestream:v1:schema` | string | `1` (JSON integer) | contract version; missing → `config.rejected` ("store not seeded") |
| `cvestream:v1:revision` | string (integer) | `INCR` by every writer | missing → revision `"0"` |
| `cvestream:v1:endpoints` | SET | endpoint ids (`^[a-z0-9-]{1,40}$`) | the list of endpoints; ids are sorted after reading |
| `cvestream:v1:endpoint:{id}` | string | JSON object `{"name", "enabled", "min_cvss", "kev_only"}` | `id`, `token` and `products` are **not** allowed here (they come from the key names); missing → `config.rejected` |
| `cvestream:v1:endpoint:{id}:products` | string | JSON array of product items, same as YAML: `"php >= 8.2"` or `{"name", "aliases", "exclude", "min_version", "match_description"}` | missing → `[]` (an endpoint without products matches nothing, as in YAML) |
| `cvestream:v1:token:{id}` | string | the feed token, **raw** (not JSON-quoted) | own namespace: no connector pattern covers it; missing → `config.rejected` |

- Values are UTF-8 JSON (no Valkey JSON module), except `token:{id}` (raw text) and `revision` (decimal integer).
- Assembly: `{"version": json(schema), "endpoints": [{"id": id, **json(endpoint), "token": token, "products": json(products)} for id in sorted(ids)]}` (omit `products` when the key is missing), then `build_snapshot(document, revision=<revision>, source="valkey")`. Rewrite the `endpoints[<i>]` prefix of each validation error to `endpoint:<id>` so the operator knows which key to fix (a regex on the error strings, inside the provider).
- Never quote a value in an error: a JSON decode error says `cvestream:v1:endpoint:team-php: invalid JSON`, not the content. The endpoint and products keys hold no token, but keep the same rule everywhere.

### Read protocol (daemon)

```
revision()                      GET revision                           (cheap, every cycle)
load():
  for attempt in 0..SNAPSHOT_RETRIES (3 retries = 4 attempts at most):
    r1 = GET revision
    pipeline (transaction=False): GET schema, SMEMBERS endpoints        -> ids
    pipeline (transaction=False): for id in ids: GET endpoint:id, GET endpoint:id:products, GET token:id
    r2 = GET revision
    if r1 == r2: assemble, validate, return snapshot(revision=r1)
  raise ConfigConflictError("valkey: the revision kept changing during the read")
```

- The daemon user has no `MULTI`/`WATCH`: the revision bracket is the consistency check. A writer that follows the protocol `INCR`s inside its transaction, so a read that straddles a write sees two revisions and retries.
- `ConfigConflictError` is a `ConfigUnavailableError`: `reload_config()` logs it as `config.unavailable` (warning, `error_type=ConfigConflictError`) and keeps the last snapshot; at startup it is retried like a network error.
- `decode_responses=False`: decode each value yourself (`bytes.decode("utf-8")`, `UnicodeDecodeError` → validation error naming the key), so one bad key never surfaces as a client exception.

### Write protocol (admin, story 3.2 builds on it)

```
for attempt in 1..WRITE_ATTEMPTS (5):
  with client.pipeline(transaction=True) as pipe:
    pipe.watch(REVISION)                     # immediate mode from here
    current = <read the document like load(), through pipe, no revision bracket needed>
    new = change(current)                    # pure; may be called several times
    validate new (build_snapshot)            # invalid -> ConfigValidationError, nothing written
    pipe.multi()
    SET schema 1
    SREM endpoints <removed ids>; DEL endpoint:<id>, endpoint:<id>:products, token:<id> for removed ids
    SADD endpoints <ids>; for each endpoint: SET endpoint:<id> <json>, SET endpoint:<id>:products <json>, SET token:<id> <token>
    INCR revision
    pipe.execute()                           # WatchError -> next attempt
    return str(new revision)
raise ConfigConflictError("valkey: write conflict, gave up after 5 attempts")
```

- JSON written by the provider: `json.dumps(..., separators=(",", ":"), ensure_ascii=False, sort_keys=True)` of `endpoint.model_dump(mode="json", exclude={"id", "token", "products"})` and of the products list (`model_dump(mode="json")` keeps shorthand strings as strings). Write the full object, defaults included: it is simpler to read for third parties.
- `change` receives and returns a `ConfigDocument` (tokens included). Story 3.2's YAML writer can offer the same signature.
- `WATCH` is reset by `pipe.reset()` (the context manager does it); never leave a connection in `WATCH` state on an exception.

### Error mapping (`valkey.exceptions`, valkey-py 6.2.0)

Order matters: `AuthenticationError` and `AuthorizationError` **subclass** `ConnectionError`, and `NoPermissionError` subclasses `ResponseError`.

| valkey-py exception | Raised as | Message (example) |
|---|---|---|
| `AuthenticationError`, `AuthorizationError`, `NoPermissionError`, a `ResponseError` starting with `NOAUTH` or `WRONGPASS` | `ConfigAccessDeniedError` | `valkey: authentication failed for user 'cvestream-daemon'` / `valkey: user 'cvestream-daemon' is not allowed to run GET on a configuration key` |
| `BusyLoadingError`, `ConnectionError`, `TimeoutError` | `ConfigUnavailableError` | `valkey: cannot reach valkey:6379: ConnectionError` |
| `ResponseError` starting with `WRONGTYPE` | `ConfigValidationError(source="valkey")` | `cvestream:v1:endpoints: wrong type (expected a set)` |
| `WatchError` | retried, then `ConfigConflictError` | `valkey: write conflict, gave up after 5 attempts` |
| any other `ValkeyError` | `ConfigUnavailableError` with the class name | never `str(exc)` blindly: build the message yourself |

- Raise with `from None` (as `yaml_provider.py` does) so a traceback never carries a value.
- `revision()` maps errors the same way: during an outage `reload_config()` gets `ConfigUnavailableError` and logs the warning (AC 5).
- **Timeouts are mandatory:** valkey-py's default `socket_timeout` is `None` (blocks forever) and its default retry is `Retry(NoBackoff(), 0)`. Use `socket_timeout=5.0`, `socket_connect_timeout=5.0` (constants in the provider). With `docker pause`, a cycle then waits at most about 5 s per call before logging `config.unavailable`.
- valkey-py sends `CLIENT SETINFO LIB-NAME/LIB-VER` after `AUTH` and ignores a `ResponseError`: the ACL lines grant `+client|setinfo` anyway so the server's ACL log stays clean.

### Testability seam

Keep the protocol logic testable without a server: the provider's read loop calls a small internal reader (`_read_revision()`, `_read_raw(ids)`), and `assemble(raw)` is a pure module-level function. Unit tests subclass or replace the reader with a fake returning scripted revisions (`1, 2` then `2, 2` → one retry; `1, 2` four times → `ConfigConflictError`). Do not fake `valkey.Valkey` itself (its typing is loose and ty would fight you).

**ty and valkey-py typing:** command methods are typed with broad unions (`ResponseT`, which includes `Awaitable`). Narrow results with `isinstance` checks (or one small `_as_bytes()` helper) rather than `cast`, and keep `ty check` (with `error-on-warning`) green. Do not add `# type: ignore` without a comment saying why.

### Settings

```yaml
config_provider: valkey            # Env: CVESTREAM_CONFIG_PROVIDER
valkey:
  url: valkey://cvestream-daemon@valkey:6379/0   # Env: CVESTREAM_VALKEY__URL (user in the URL, never the password)
  # password: never here; Env: CVESTREAM_VALKEY__PASSWORD_FILE=/run/secrets/valkey-password (or CVESTREAM_VALKEY__PASSWORD)
  startup_timeout: PT60S           # Env: CVESTREAM_VALKEY__STARTUP_TIMEOUT
```

- `url` validator with `urllib.parse.urlsplit`: scheme in `{"valkey", "valkeys"}`; `hostname` present; `port` parses (a bad port raises `ValueError` in `urlsplit().port`: catch it); `password is None`; `query == ""` and `fragment == ""`; path empty or `/<digits>`. Messages: "valkey.url must not contain a password: use valkey.password (CVESTREAM_VALKEY__PASSWORD_FILE)", "valkey.url: scheme must be valkey:// or valkeys://", etc. Never include the value.
- `valkeys://`: valkey-py verifies the server certificate by default (`ssl_cert_reqs="required"`, system CA store). Never add an option that disables verification. TLS is documented as supported but not tested in CI (no TLS server); say so in `docs/configuration.md`.
- The user name comes from the URL (`valkey://cvestream-daemon@...`); without one, valkey-py authenticates as `default`, which `users.acl` disables. Document that.
- `startup_timeout`: `timedelta`, greater than 0, at most 1 hour.

### Startup behaviour (`app/runtime.py`)

- Order unchanged: migrate state → private listener up (`/healthz` 200, `/readyz` 503) → **load the configuration** → feeds → worker and loops → public listener → `/readyz` 200.
- Loader for Valkey (YAML keeps calling `provider.load` once):

```
deadline = monotonic() + startup_timeout
delay = 1.0
loop:
  try: snapshot = await to_thread(provider.load); log config.loaded; return
  except ConfigAccessDeniedError: log error config.unavailable; report; return exit 3
  except ConfigValidationError: log error config.rejected; report; return exit 2
  except ConfigUnavailableError as exc:
     remaining = deadline - monotonic()
     if remaining <= 0: log error config.unavailable (error_type); report(str(exc)); return exit 3
     wait = min(delay, remaining, 10.0); log warning config.unavailable (error_type, retry_in=wait); await sleep(wait); delay *= 2
```

- `monotonic` defaults to `time.monotonic`, injected for tests (a fake `sleep` that advances a fake `monotonic`). Never `datetime.now()`.
- A `stop` (SIGTERM) during the retries ends the startup cleanly with exit 0: race the `sleep` with `stop.wait()` the way `_wait_for_stop` does, or check `stop` between attempts.
- `poll_once` uses the same loader.
- The stderr line at the end is the provider's message (`valkey: cannot reach valkey:6379: ConnectionError`, `valkey: authentication failed for user 'cvestream-daemon'`).

### Full reload safety net

- Architecture: "Full reload every N cycles as a safety net." N = `FULL_RELOAD_EVERY = 12` pipeline cycles (each source fetch triggers a cycle; with three sources at 15 min that is about once an hour). Counted in `PipelineWorker`, passed as `cycle(..., force_reload=True)` → `reload_config(..., force=True)`.
- With YAML a forced reload just re-reads the file (harmless). `config.reloaded` is logged only when the snapshot changed (`ConfigSnapshot` is a frozen dataclass: compare `endpoints` and `dict(tokens)`), so the safety net is silent when nothing changed.

### Server files (`deploy/valkey/`), consumed by story 3.3

`deploy/valkey/valkey.conf` (comments are fine in this file):

```conf
# cvestream configuration store (story 3.1). Mounted with deploy/valkey/ on
# /usr/local/etc/valkey and started with:
#   valkey-server /usr/local/etc/valkey/valkey.conf
# Contract and ACL users: docs/config-contract.md.

# The container network is the boundary: Valkey must never be published.
bind * -::*
port 6379
protected-mode yes

# Access rules: the ACL file holds every user, `default` included (disabled).
aclfile /usr/local/etc/valkey/users.acl

# Persistence: AOF, fsync every second (architecture.md, Data Architecture).
dir /data
appendonly yes
appendfsync everysec
appenddirname "appendonlydir"
```

- `bind * -::*` listens on every IPv4 interface and on IPv6 when available (the `-` makes it optional).
- Keep the default RDB save points (the backup story relies on an RDB snapshot plus AOF).
- Do not set `user` lines in `valkey.conf`: Valkey refuses to start with both `user` directives and `aclfile` ("mutually incompatible").

`deploy/valkey/users.acl` (Valkey 9.1 `ACLLoadFromFile` accepts **only** blank lines and lines starting with `user`: a `#` comment line makes the whole file fail to load; checked in `src/acl.c` on the `9.1` branch). Replace each `<sha256-...>` by the SHA-256 of the matching dev secret file:

```
user default off resetpass resetkeys resetchannels -@all
user cvestream-daemon on #<sha256-daemon> resetchannels %R~cvestream:v1:* -@all +get +mget +smembers +exists +ping +select +client|setinfo
user cvestream-admin on #<sha256-admin> resetchannels ~cvestream:v1:* -@all +get +mget +smembers +exists +set +del +sadd +srem +incr +watch +unwatch +multi +exec +discard +ping +select +client|setinfo
user connector-example on #<sha256-connector> resetchannels %R~cvestream:v1:schema %R~cvestream:v1:endpoints %R~cvestream:v1:endpoint:* %W~cvestream:v1:endpoint:*:products %RW~cvestream:v1:revision -@all +get +mget +smembers +exists +set +incr +watch +unwatch +multi +exec +discard +ping +select +client|setinfo
```

Why each rule (put this in `docs/config-contract.md`, not in the file):

- **The `default` line is required:** when the file has no `default` user, Valkey recreates it as `on nopass ~* &* +@all` (an open server).
- **Explicit command lists, not categories:** `+@read`/`+@write` include `KEYS`, `SCAN`, `SORT`, `FLUSHALL` (some only removed by `-@dangerous`), and `SCAN` lists key names regardless of key patterns. The lists above are exactly what the provider and the write protocol use. Story 3.2 may need more (add them there, with a test).
- **Key permissions per command (Valkey 9.1 key specs):** `GET`, `MGET`, `SMEMBERS` need read; `SET` (without `GET`), `SADD`, `DEL` need write; **`INCR` needs read and write** (`RW ACCESS UPDATE`), hence `%RW~` on `revision`; `WATCH` only needs the key to match one of the user's patterns. Commands queued in `MULTI` are checked when queued (a refused one makes `EXEC` fail with `EXECABORT`).
- `%R~cvestream:v1:endpoint:*` also matches `cvestream:v1:endpoint:<id>:products` (glob `*` crosses `:`), so connectors read products; it does **not** match `cvestream:v1:endpoints` (hence its own pattern) nor `cvestream:v1:token:<id>`.
- The daemon reads tokens (`%R~cvestream:v1:*` covers `token:*`): it serves the feeds.
- `#<hash>` is the SHA-256 (hex, lower case, 64 characters) of the password. Never `>plaintext` in a committed file.

`deploy/valkey/dev-secrets/*.password`: dev-only passwords for compose and the tests (e.g. `dev-only-cvestream-daemon`, `dev-only-cvestream-admin`, `dev-only-connector-example`), one line, no trailing newline. The file names and `docs/config-contract.md` say they are public and for development only; production deployments ship their own `users.acl` (story 3.3: a Kubernetes Secret) with their own hashes.

### Interface for story 3.3 (Kubernetes), keep it stable

Story 3.3 (branch `story-3-3-release-and-kubernetes-deployment`, developed in parallel) mounts these files in its example Valkey `StatefulSet`. It relies on:

- the paths `deploy/valkey/valkey.conf` and `deploy/valkey/users.acl` (no other file is needed by the server);
- the server command `valkey-server /usr/local/etc/valkey/valkey.conf` with both files visible under `/usr/local/etc/valkey/` (the config names the ACL file by its absolute path `/usr/local/etc/valkey/users.acl`; a `projected` volume can combine a ConfigMap for `valkey.conf` and a Secret for `users.acl` into that one directory);
- data in `/data` (AOF directory `appendonlydir`), port `6379`;
- the user names `cvestream-daemon`, `cvestream-admin`, `connector-example` and the daemon settings `CVESTREAM_CONFIG_PROVIDER=valkey`, `CVESTREAM_VALKEY__URL=valkey://cvestream-daemon@<service>:6379/0`, `CVESTREAM_VALKEY__PASSWORD_FILE=<mounted secret file>`.
- the image `valkey/valkey:9.1.3` (UID 999 inside the image, like cvestream).
If you must change any of these, write it in the Completion Notes and in the PR so 3.3's rebase picks it up.

### compose.yml

```yaml
services:
  app:
    # ... unchanged; add:
    networks: [default, config]
    secrets: [valkey-daemon-password]
    # To use Valkey: CVESTREAM_CONFIG_PROVIDER=valkey docker compose watch
    environment:
      CVESTREAM_CONFIG_PROVIDER: ${CVESTREAM_CONFIG_PROVIDER:-yaml}
      CVESTREAM_VALKEY__URL: valkey://cvestream-daemon@valkey:6379/0
      CVESTREAM_VALKEY__PASSWORD_FILE: /run/secrets/valkey-daemon-password
  valkey:
    image: valkey/valkey:9.1.3
    command: ["valkey-server", "/usr/local/etc/valkey/valkey.conf"]
    volumes:
      - ./deploy/valkey:/usr/local/etc/valkey:ro
      - valkey-data:/data
    networks: [config]          # internal: no host port, no outside access
networks:
  config:
    internal: true
secrets:
  valkey-daemon-password:
    file: ./deploy/valkey/dev-secrets/cvestream-daemon.password
volumes:
  valkey-data:
```

- Default stays YAML so `docker compose watch` keeps serving `endpoints.example.yaml` with no seeding. The header comment explains how to switch and how to seed with `valkey-cli` as `cvestream-admin` (`docker compose exec valkey valkey-cli --user cvestream-admin --pass "$(cat deploy/valkey/dev-secrets/cvestream-admin.password)"`).
- No `depends_on` health condition: the daemon's startup retry is the dependency handling (and a Valkey healthcheck would need a password on its command line).
- `docker compose config` must parse; `docker compose up -d valkey` then `docker compose exec valkey valkey-cli --user cvestream-daemon --pass <p> PING` → `PONG` (manual check of the stack plan).

### CI (`.github/workflows/ci.yml`)

- A `services:` container starts before `actions/checkout`, so it cannot bind-mount the repo's `deploy/valkey/` (the directory does not exist yet). Start Valkey with a step **after checkout**, in the `ci` job:

```yaml
      - name: Start Valkey
        run: |
          docker run -d --name valkey -p 127.0.0.1:6379:6379 \
            -v "$PWD/deploy/valkey:/usr/local/etc/valkey:ro" \
            valkey/valkey:9.1.3 valkey-server /usr/local/etc/valkey/valkey.conf
          for _ in $(seq 30); do
            docker exec valkey valkey-cli --no-auth-warning --user cvestream-daemon \
              --pass "$(cat deploy/valkey/dev-secrets/cvestream-daemon.password)" PING | grep -q PONG && exit 0
            sleep 1
          done
          docker logs valkey; exit 1
      - run: uv run pytest
        env:
          VALKEY_TEST_ADDRESS: 127.0.0.1:6379
```

- No `${{ }}` in `run:`; no new permission; no new job. `actions/checkout` keeps `persist-credentials: false`.
- The image tag is pinned to the exact patch release (`9.1.3`, the current 9.1 patch on Docker Hub on 2026-11-05), in `compose.yml` and `ci.yml` alike. Dependabot's `docker` ecosystem does not watch these files: bump them by hand.

### Integration tests (`tests/integration/test_valkey_provider.py`, marker `valkey`)

Fixture: `VALKEY_TEST_ADDRESS` (`<ip>:<port>`, an IP literal so `allow_hosts` matches) → `pytest.mark.allow_hosts([ip])`; skip with "VALKEY_TEST_ADDRESS not set (start a disposable valkey, see README)" when unset, `pytest.fail` when unset and `os.environ.get("CI")`. Do **not** use a `CVESTREAM_` prefix for the test variable (settings read `CVESTREAM_*`). One client per user, passwords from `deploy/valkey/dev-secrets/`. Cleanup as `cvestream-admin` before and after each test.

Cases (one test each; assert `NoPermissionError` / `NOPERM`, `AuthenticationError`):

1. Unauthenticated `GET cvestream:v1:revision` → `NOAUTH`; `AUTH default <anything>` → auth error (`default` is off).
2. Wrong password for `cvestream-daemon` → `AuthenticationError`; the provider maps it to `ConfigAccessDeniedError`.
3. `cvestream-daemon`: `GET revision`, `SMEMBERS endpoints`, `GET token:<id>` work; `SET revision 1`, `DEL`, `SADD`, `INCR`, `MULTI` → refused.
4. `cvestream-admin`: `SET`/`DEL`/`SADD`/`INCR` on `cvestream:v1:*` work; `SET other 1` → `NOPERM`; `FLUSHALL`, `CONFIG GET *`, `KEYS *`, `SCAN 0` → refused.
5. `connector-example`: `GET schema`, `GET endpoint:<id>`, `GET endpoint:<id>:products`, `SMEMBERS endpoints`, `SET endpoint:<id>:products '[]'`, `INCR revision`, and the full `WATCH`/`MULTI`/`SET`/`INCR`/`EXEC` work; `GET token:<id>`, `SET token:<id>`, `SET endpoint:<id>`, `SADD endpoints x`, `DEL endpoint:<id>` → refused; a `SET token:<id>` queued inside `MULTI` makes the transaction fail.
6. Provider as `cvestream-daemon`: seed as admin with `update()`, then `load()` returns the expected snapshot (endpoints, rules, products incl. shorthand and `match_description`, tokens) and `revision()` equals it.
7. Hot reload contract: as `connector-example`, replace the products and `INCR`; `revision()` changed and `load()` shows the new products. Then write invalid JSON + `INCR`: `load()` raises `ConfigValidationError` naming `cvestream:v1:endpoint:<id>:products`, and `pipeline.run.reload_config()` keeps the previous snapshot and logs `config.rejected`.
8. Write conflict: `update(change)` where `change` `INCR`s the revision through a second client on its first call → `change` called twice, the write lands, revision grew by 2.
9. `update()` with an invalid result (duplicate token) → `ConfigValidationError`, revision unchanged, nothing written.
10. Unseeded store (`schema` missing) → `ConfigValidationError` ("store not seeded" wording).

Plus `tests/integration/test_startup_requires_valkey.py` (Task 8.3): closed port → exit 3 after the timeout with backoff (no server, unmarked, `allow_hosts(["127.0.0.1"])`); wrong password and disabled `default` user (`valkey://default@...`) → exit 3 at once (marked `valkey`).

### Running the valkey tests locally

```sh
docker run -d --rm --name cvs-valkey-story31 -p 127.0.0.1::6379 \
  -v "$PWD/deploy/valkey:/usr/local/etc/valkey:ro" \
  valkey/valkey:9.1.3 valkey-server /usr/local/etc/valkey/valkey.conf
export VALKEY_TEST_ADDRESS=$(docker port cvs-valkey-story31 6379 | head -1)   # 127.0.0.1:<port>
uv run pytest -m valkey -v
docker stop cvs-valkey-story31
```

A random host port and a story-specific container name: story 3.3 may run its own Valkey at the same time. The image pull works behind the proxy only if the Docker daemon trusts the proxy CA (Docker Hub, not ghcr.io); if it does not, report it and rely on CI.

### Logging

- New: `config.loaded` (info, startup and `poll --once`; `count`, `revision`). `config.reloaded` gains `revision`. `config.unavailable` at startup: warning per failed attempt (`error_type`, `retry_in`), error on the last one. Never log the URL with credentials (it has none by validation), a password, a token or a raw value. Areas stay in the fixed list (`config`).
- Revisions are not secrets (a Valkey integer, or the SHA-256 of the YAML file).

### `docs/config-contract.md` outline

TL;DR; who uses it (daemon read, CLI/admin read-write, connectors); key layout table (above) with value formats and one full example; JSON values validated by `schemas/config-v1.json` (`$defs/EndpointConfig` without `id`, `token`, `products`; `$defs/ProductEntryConfig` or the shorthand string); read protocol (revision bracket); write protocol with a complete `valkey-cli` session as `cvestream-admin` and a connector example that only replaces one product list; ACL users (the four lines, why each rule, how to add `connector-<name>` and generate `#<sha256>`: `printf '%s' "$PASSWORD" | sha256sum`), no comment lines in `users.acl`; compatibility (`v1` namespace, `schema` stays `1`, a breaking change gets `cvestream:v2:*`); what happens with invalid data (`config.rejected`, last valid kept) and when `INCR` is forgotten (picked up by the full reload within about 12 cycles); tokens are secrets and never under a connector pattern. Starts with a TL;DR (the doc is long). Docs written by an AI carry `:factory:` (convention: `docs/sources.md` ends a paragraph with it): add it to the new document and to the sections you write in the other docs.

### Architecture amendments (same PR)

- `architecture.md`, Authentication & Security, "Valkey ACL users": replace "Exact ACL lines are written and tested in the Valkey provider story" by a pointer to `deploy/valkey/users.acl` and `docs/config-contract.md`, explicit command lists, `%RW~` on `revision`, the `default` line requirement and the no-comment rule.
- Data Architecture: token value is raw text (not JSON); `schema` missing = not seeded; products key missing = `[]`; full reload every 12 cycles; `ConfigConflictError` / `ConfigAccessDeniedError`.
- Infrastructure & Deployment, `ci.yml` row: Valkey started by a `docker run` step after checkout (not a `services:` container), image `valkey/valkey:9.1.3`.
- Structure Patterns: `valkey` imported only by `config/valkey_provider.py` is already written; make sure the import-linter wording matches.
- `project-context.md`: Configuration section (token raw, error classes, startup: access denied not retried, full reload), Testing Rules (`VALKEY_TEST_ADDRESS`, skip locally / fail on CI, `allow_hosts`).

### Decisions taken autonomously (the stack plan lists them as points for a human decision)

Recorded here and in the PR; the code review may raise them as "decision needed".

1. **User and password:** user name in `valkey.url`, password only through `valkey.password` (`*_FILE` preferred); a password inside the URL is rejected. Keeps the URL loggable and non-secret.
2. **`users.acl` passwords:** `#<sha256>` hashes only, of public dev-only passwords committed under `deploy/valkey/dev-secrets/`; production ships its own `users.acl`. Compose provisions the daemon password through a compose secret file.
3. **ACL command sets:** explicit command lists per user instead of categories (least privilege; `SCAN`/`KEYS` would list key names).
4. **TLS:** `valkeys://` accepted and passed to valkey-py with certificate verification on; not tested in CI.
5. **Startup backoff:** 1 s doubling, capped at 10 s, last wait cut to the deadline; access denied is not retried (a wrong password does not heal; fail fast with exit 3); an unseeded or invalid store exits 2 like an invalid `endpoints.yaml`.
6. **Full reload:** every 12 pipeline cycles.
7. **Revision poll independent of source fetches** (`deferred-work.md`, "Configuration reload waits for a source fetch"): **not** in this story. FR-23 says "at the next poll cycle"; a rotated token (story 3.2) takes effect at the next fetch of any source (at most one interval). Keep the deferred entry and note that story 3.2 or a later one may add a short revision poll.
8. **`valkey` tests without a server:** skip locally with a clear reason, fail on CI (`CI` set); pytest-socket allows only the Valkey IP through `allow_hosts`.
9. **Image tag:** `valkey/valkey:9.1.3` (exact patch) in `compose.yml` and `ci.yml`.
10. **Empty products key:** missing `endpoint:{id}:products` means `[]` (same default as YAML); a missing `endpoint:{id}` or `token:{id}` rejects the snapshot.

### Testing standards for this story

- Unit tests mirror `src/` (`tests/unit/config/test_keys.py`, `tests/unit/config/test_valkey_provider.py`, `tests/unit/test_settings.py`, `tests/unit/app/test_runtime.py`, `tests/unit/app/test_scheduler.py`, `tests/unit/pipeline/test_run.py`); `tests/unit/test_deploy_valkey.py` checks the server files without a server.
- No real sleeps, no real time: fake `sleep` and `monotonic`, `FakeClock` from `tests/conftest.py`. Async tests with the anyio plugin (`anyio_backend` fixture exists).
- Fake tokens in tests need the ruff per-file ignores `S105`/`S106` (see `pyproject.toml`): add the new test files there, the same way as `tests/unit/config/test_provider.py`.
- `uv run pytest` (default) must stay green **with and without** a server: without one, only the `valkey` tests skip.
- Guardrail tests stay green: `test_restart_no_repost.py`, and the new `test_startup_requires_valkey.py`.

### Previous story intelligence

- **1.3 (YAML provider):** error messages never quote input (PyYAML and Pydantic messages embed values; `build_snapshot` uses `errors(include_input=False)`); the same discipline applies to valkey-py exceptions. Strict models: a JSON `"7.0"` string for `min_cvss` is rejected, by design.
- **1.6 (daemon):** startup order, exit codes (`3` = configuration unavailable), `reload_config()` keeps the last valid snapshot, `/readyz` independent of sources, `serve()` injectables for tests, one stderr line on failure, `config.*` events. The worker never cancels a run in its thread: provider calls must time out on their own (hence the socket timeouts).
- **2.1/2.2:** commits `feat|fix|docs|test|chore|ci|build(story-x.y): ...`, one concern each; CI-only changes use `ci(story-x.y)`; action pins stay identical across workflows; local `docker build` fails behind the proxy (x509 on ghcr.io), CI is the gate.
- **Settings secrets (1.1/1.5):** `_FileSecretsSource` already reads nested `*_FILE` secrets and rejects both forms; reuse it, do not write a new secret loader.

### Git intelligence

- `6c02c2c docs(story-3.1): add epic 3 stacked PR plan` is the base of this branch (on top of `adb3968 chore(epic-2): mark epic 2 done`). Dependabot recently bumped pydantic 2.14.2, ruff 0.17.3, pytest 9.2.0, setup-uv 10.3.3 (`68559e99...` in `ci.yml`): use the SHAs already in `ci.yml`, add no new action.

### Latest technical information (checked 2026-11-05)

- **valkey-py 6.2.0** (PyPI, 2026-10-12, `requires_python >=3.11`, ships `py.typed`): URL schemes `valkey://`, `valkeys://`, `unix://`; `from_url(url, **kwargs)` (URL query options override kwargs, hence no query string allowed in `valkey.url`); default RESP2 (`AUTH user pass`), `CLIENT SETINFO` errors ignored; no default socket timeout and no default retry; exceptions in `valkey.exceptions` (`ConnectionError`, `TimeoutError`, `AuthenticationError(ConnectionError)`, `AuthorizationError(ConnectionError)`, `BusyLoadingError(ConnectionError)`, `ResponseError`, `NoPermissionError(ResponseError)`, `WatchError`, `ExecAbortError(ResponseError)`); `Pipeline.watch()`, `.multi()`, `.execute()`.
- **Valkey 9.1:** Docker Hub tags `9.1`, `9.1.3` (latest patch), `9.1.3-trixie`, `9.1.3-alpine`. ACL syntax `%R~`, `%W~`, `%RW~`, `~` (= `%RW~`), `#<sha256>`, `resetpass`, `resetkeys`, `resetchannels`, `+cmd|subcmd` (valkey.io/topics/acl). ACL file: only `user` lines, no comments; missing `default` → recreated open; `aclfile` and `user` directives in `valkey.conf` are mutually exclusive. Default `valkey.conf` of 9.1: `appendonly no`, `appendfsync everysec`, `appenddirname "appendonlydir"`, `dir ./`, `bind 127.0.0.1 -::1`, `protected-mode yes`.
- **pytest-socket 0.8.1:** with `--disable-socket`, a test with `@pytest.mark.allow_hosts([...])` gets sockets to those hosts only; `enable_socket` would open everything (do not use it for `valkey` tests).
- **GitHub Actions:** service containers now support `command` and `entrypoint`, but start before the job's steps (no checkout yet), so they cannot mount repo files.

### Project Structure Notes

- New files are all in the architecture tree: `config/keys.py`, `config/valkey_provider.py`, `deploy/valkey/valkey.conf`, `deploy/valkey/users.acl`, `docs/config-contract.md`, `tests/integration/test_valkey_provider.py`, `tests/integration/test_startup_requires_valkey.py`. Additions not in the tree: `deploy/valkey/dev-secrets/` (dev passwords), `tests/unit/config/test_keys.py`, `tests/unit/config/test_valkey_provider.py`, `tests/unit/test_deploy_valkey.py`, possibly `tests/integration/conftest.py`: add `dev-secrets/` to the architecture tree.
- `open_provider()` lives in `config/provider.py` (not in `app/`) so story 3.2's CLI reuses it.

### Delivery (stacked PR)

- Branch `story-3-1-valkey-configuration-provider`, PR base `main` (bottom of the epic 3 stack: `main <- 3.1 <- 3.3 <- 3.2`, see `_bmad-output/implementation-artifacts/epic-3-stack-plan.md`). The draft PR is opened at story creation; push implementation commits on top.
- Suggested commits, one concern each: `build(story-3.1): add valkey-py`, `refactor(story-3.1): restrict the valkey import to the provider module` (import-linter), `feat(story-3.1): add Valkey settings`, `feat(story-3.1): add the Valkey key layout`, `feat(story-3.1): add the Valkey configuration provider`, `feat(story-3.1): wait for Valkey at startup and exit with code 3`, `feat(story-3.1): reload the configuration every 12 cycles`, `feat(story-3.1): add the Valkey server configuration and ACL users`, `test(story-3.1): add Valkey integration tests`, `build(story-3.1): add Valkey to the dev compose stack`, `ci(story-3.1): run the valkey tests against Valkey 9.1`, `docs(story-3.1): document the configuration contract`, `docs(story-3.1): document the Valkey provider`, `docs(story-3.1): amend the architecture for the Valkey provider`, `chore(story-3.1): mark story ready for review`. Every commit message ends with the line `🤖 Généré avec Claude Code`.
- Story 3.3 is created and developed at the same time on its own branch (from the plan commit); expect conflicts on `ci.yml`, `docs/operations.md`, `README.md`, `compose.yml`, `sprint-status.yaml` when 3.3 rebases (resolved there, not here).
- Never push to `main`; `git push --force-with-lease` only after a rebase.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.1: Valkey configuration provider]
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Architecture] (key layout, write and read protocols, AOF, full reload)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] (Valkey ACL users, credentials from a secret file, TLS optional)
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns] (startup behaviour, outage while running, exit code 3)
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (`ci.yml` Valkey service, compose with Valkey, runtime configuration)
- [Source: _bmad-output/planning-artifacts/architecture.md#Structure Patterns] (library ownership, test markers, pytest-socket)
- [Source: _bmad-output/project-context.md#Configuration (Valkey / YAML)], `#Testing Rules`, `#Security`
- [Source: _bmad-output/implementation-artifacts/epic-3-stack-plan.md] (stack, schedule, manual checks for PR 1 of 3, points needing a decision)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] ("Configuration reload waits for a source fetch", "valkey only in config" contract)
- [Source: _bmad-output/implementation-artifacts/1-3-endpoint-configuration-in-yaml.md], [Source: _bmad-output/implementation-artifacts/1-6-feeds-served-by-the-daemon.md]
- [Source: src/cvestream/config/provider.py], [Source: src/cvestream/config/models.py], [Source: src/cvestream/settings.py], [Source: src/cvestream/pipeline/run.py], [Source: src/cvestream/app/runtime.py], [Source: src/cvestream/app/scheduler.py], [Source: pyproject.toml], [Source: compose.yml], [Source: .github/workflows/ci.yml]
- External: valkey.io/topics/acl (ACL rules, ACL file, default user), `valkey-io/valkey` branch `9.1` (`valkey.conf`, `src/acl.c` `ACLLoadFromFile`, `src/commands/*.json` key specs), PyPI `valkey` 6.2.0 (`valkey/connection.py`, `valkey/exceptions.py`), Docker Hub `valkey/valkey` tags, GitHub Docs "Workflow syntax" (`jobs.<job_id>.services`), pytest-socket 0.8.1 (`pytest_runtest_setup`).

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Story created by `bmad-create-story` (autonomous run, 2026-11-05). Ultimate context engine analysis completed: comprehensive developer guide created. Choices made without a human: see "Decisions taken autonomously".

### File List

### Change Log

- 2026-11-05: story created (`ready-for-dev`).
