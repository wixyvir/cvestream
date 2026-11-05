---
baseline_commit: 22d0ef0
---

# Story 3.2: Operator CLI for endpoints, products and tokens

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want CLI commands to manage endpoints, product entries, rules and tokens on whichever provider is active,
so that I never edit Valkey keys by hand and can rotate a leaked feed URL in one command.

## TL;DR

- **New commands** (Typer, kebab-case, noun then verb): `cvestream endpoint create|list|show|edit|delete`, `cvestream product add|remove|list`, `cvestream token rotate <endpoint>`, `cvestream config export|import`. They read and write **through the active provider** (`config_provider: yaml` or `valkey`), never around it.
- **Provider write API (new, both providers):** a `ConfigStore` protocol in `config/provider.py` with `document()` (the validated current `ConfigDocument`), `update(change)` (read → `change` → validate → write, retried on conflict; Valkey already has it from 3.1) and `replace(document)` (write a whole document **without** validating the current content: the repair path for `config import`, deferred from the 3.1 review). The YAML provider gains an atomic writer (temporary file in the same directory, `fsync`, `os.replace`, file mode kept, `0600` for a new file) with an optimistic conflict check (SHA-256 re-read just before the rename).
- **Pure edits** live in a new `config/edit.py` (`ConfigDocument → ConfigDocument`, no I/O, no randomness): create, edit, delete an endpoint; add, remove a product; set a token. The CLI passes a token produced by an **injectable** function (`secrets.token_urlsafe(32)` by default, carried by `CliState.new_token`; tests inject a fixed one with `CliRunner.invoke(..., obj=CliState(new_token=...))`).
- **Feed URL:** `endpoint show`, `endpoint create` and `token rotate` print `<base>/feeds/<token>.xml`. New optional setting `http.public.base_url` (`CVESTREAM_HTTP__PUBLIC__BASE_URL`, e.g. `https://feeds.example.org`); without it the base is `http://localhost:<http.public.port>`.
- **Errors:** a user error (unknown or duplicate id, invalid value, invalid document, provider unavailable or access denied, write conflict) prints **one line** on stderr and exits **2**; an unexpected error exits **1** with the traceback only under `--debug` (existing `main()` supervisor). Never a token, a password or a raw value in a message or a log.
- **YAML rewrite drops comments** (PyYAML, no new dependency: a round-trip library would parse YAML 1.2 while the daemon reads YAML 1.1). The CLI writes a one-line header saying so and prints a stderr notice the first time it rewrites a file that had comments. Hand-editing keeps working: every command reads the file fresh.
- **Docs:** `docs/operations.md` (token rotation with `cvestream token rotate`, configuration backup with `config export` + Valkey AOF/RDB, running the CLI against Valkey as `cvestream-admin`), `docs/configuration.md` (CLI section, `http.public.base_url`), `docs/config-contract.md` (the CLI is the reference writer), `README.md`, `CLAUDE.md`, `settings.example.yaml`, `architecture.md`, `project-context.md`.
- **Stack:** this branch starts from the reviewed 3.1 branch and will be **rebased onto story 3.3** before review. Expect conflicts on `docs/operations.md`, `README.md`, `CLAUDE.md`, `architecture.md`, `project-context.md`, `deferred-work.md`, `sprint-status.yaml` (and `ci.yml` only if this story touches it, which it should not). See "Stack and rebase onto 3.3".

## Acceptance Criteria

1. **CRUD through the active provider (FR-22, FR-23).**
   **Given** the active provider (YAML or Valkey)
   **When** the operator runs `cvestream endpoint create|list|show|edit|delete`, `cvestream product add|remove|list` or `cvestream token rotate <endpoint>`
   **Then** the change is written through that provider: the YAML file is rewritten atomically and stays valid (`cvestream config validate` passes on it), Valkey is written through the 3.1 write protocol (`WATCH revision` → `MULTI` → writes → `INCR revision` → `EXEC`, retried on conflict), so the revision grows by exactly 1 per command
   **And** every change is validated with the v1 contract models **before** anything is written: an invalid change leaves the file bytes (YAML) or the keys and the revision (Valkey) untouched
   **And** the daemon applies the change at its next run, with no restart (the YAML SHA-256 or the Valkey revision changed; nothing new in the daemon)

2. **Tokens and feed URL (FR-20).**
   **Given** `endpoint create` or `token rotate`
   **When** a token is needed
   **Then** it comes from `secrets.token_urlsafe(32)` through an injectable function (tests inject a fixed value), never from a command-line option
   **And** `endpoint show <id>` (text and `--json`), `endpoint create` and `token rotate` print the feed URL `<base>/feeds/<token>.xml`, where `<base>` is `http.public.base_url` or `http://localhost:<http.public.port>`
   **And** after `token rotate`, once the daemon has reloaded, the old URL returns `404` and the new one serves the feed (test through the feed cache and the public app)
   **And** no token appears in a log line or an error message; on stdout it appears only inside the feed URL of these three commands and in `config export`

3. **CLI conventions.**
   **Given** any new command
   **Then** options are kebab-case (`--min-cvss`, `--kev-only/--no-kev-only`, `--enable/--disable`, `--min-version`, `--match-description/--no-match-description`), `endpoint list` and `product list` support `--json` (and `endpoint show` too), a user error prints one line on stderr and exits `2`, an unexpected error exits `1` with a traceback only under `--debug`, and `--help` of every command reads well

4. **Export and import.**
   **Given** `cvestream config export [--output FILE]` and `cvestream config import FILE`
   **When** the operator moves the configuration between a YAML file and the active provider
   **Then** export writes the whole active configuration (tokens included) as a valid v1 YAML document, endpoints sorted by id, to stdout or to a new file created with mode `0600`
   **And** import validates the file first (same checks as `config validate`), then **replaces** the whole configuration of the active provider in one write (Valkey: one write-protocol transaction; endpoints absent from the file are removed), even when the current content is invalid or the store is unseeded; a failure leaves the provider unchanged
   **And** `export` from YAML, `import` into Valkey, `export` from Valkey gives the same bytes as the first export

5. **Hand-editing keeps working.**
   **Given** an `endpoints.yaml` edited by hand
   **When** a CLI command runs afterwards
   **Then** it starts from the current file content (the hand edit is kept), and the daemon keeps reloading hand edits as before

6. **Docs.**
   `docs/operations.md` covers token rotation with `cvestream token rotate` (replacing the manual steps) and configuration backup (`cvestream config export`, Valkey AOF/RDB); `docs/configuration.md` and `README.md` list the commands; `architecture.md` and `project-context.md` describe what was built.

## Tasks / Subtasks

- [x] **Task 1: Provider write API (`config/provider.py`, `config/yaml_provider.py`, `config/valkey_provider.py`)** (AC: 1, 4, 5)
  - [x] 1.1 In `config/provider.py`, add a `ConfigStore(ConfigProvider, Protocol)` with `document() -> ConfigDocument`, `update(change: Callable[[ConfigDocument], ConfigDocument]) -> str` and `replace(document: ConfigDocument) -> str` (both return the new revision). `open_provider()` returns `tuple[ConfigStore, Callable[[], None]]` (the daemon keeps typing against `ConfigProvider`; a `ConfigStore` is one). Keep the lazy import of the Valkey provider.
  - [x] 1.2 Extract a helper in `provider.py` that validates raw data into a `ConfigDocument` with the same error formatting as `build_snapshot()` (no `str(ValidationError)`, `include_input=False`), and make `build_snapshot()` use it. `document()` of both providers uses it.
  - [x] 1.3 YAML `document()`: read + parse + validate like `load()` (refactor `load()` to share the code; same `ConfigUnavailableError` for an unreadable or missing file).
  - [x] 1.4 YAML `update(change)`: read the bytes (a **missing file** starts from `ConfigDocument(version=1, endpoints=[])`, as the Valkey provider does for an unseeded store), validate the current document (invalid → `ConfigValidationError`, nothing written), apply `change`, validate the result (`build_snapshot` on `model_dump(mode="json")`, so duplicate ids and tokens are caught), serialize, then write atomically (1.6). Before the rename, re-read the file: if its SHA-256 differs from the one read at the start, start over (at most 5 attempts, then `ConfigConflictError`). Return the SHA-256 of the written bytes (the new revision).
  - [x] 1.5 YAML `replace(document)`: validate `document`, serialize, write atomically; the current content is not parsed (it may be invalid or missing).
  - [x] 1.6 Atomic write: resolve the path (`Path.resolve()`, so a symlinked file is written in place of its target), `tempfile.mkstemp(dir=<target dir>, prefix=".<name>.", suffix=".tmp")`, write UTF-8 with `\n`, `os.fsync`, `os.chmod` to the old file's permission bits (`stat.S_IMODE`) or `0o600` for a new file, `os.replace`, then `fsync` the directory where the platform allows it. Remove the temporary file on any failure. An `OSError` becomes `ConfigUnavailableError(f"{path}: cannot write: {type(exc).__name__}")` (read-only mount, permission). Never a partial file.
  - [x] 1.7 Serialization (`_dump_yaml(document)`): a fixed header line `# Written by cvestream; comments are not kept (docs/configuration.md#cli).`, then `yaml.safe_dump(document.model_dump(mode="json", exclude_defaults=True), sort_keys=False, allow_unicode=True, default_flow_style=False)`. `version` and the required fields always appear. Check that the output reloads to an equal document for: a token starting with `-` or made of digits only, an all-digit id (`"123"`), `min_version: "8.10"`, a name with `:` or `#`, non-ASCII names. Endpoint order: keep the document's order (new endpoints are appended).
  - [x] 1.8 Comment notice: `update()`/`replace()` report (return value or attribute, your choice; keep it testable) whether the previous file had a comment line other than the cvestream header, so the CLI prints once on stderr `<path>: comments were not kept (the CLI rewrites the file)`.
  - [x] 1.9 Valkey `document()`: the same consistent read as `load()` (revision bracket, `SNAPSHOT_RETRIES`), returning the validated `ConfigDocument`; share the loop with `load()`.
  - [x] 1.10 Valkey `replace(document)`: validate `document` first; then in the write protocol: `WATCH revision`, `SMEMBERS endpoints` (members that are not valid slugs are dropped, never used to build a key), `MULTI`, `DEL endpoints` + `DEL` of the three keys of every valid old id, `SET schema 1`, then the same writes as `update()` for the new document, `INCR revision`, `EXEC`; retried like `update()`. Factor the write part of `_attempt` so `update` and `replace` share it. Works on an unseeded store and on an invalid one (resolves the 3.1 deferred item "`update()` cannot repair an invalid store").
  - [x] 1.11 Valkey `update(change)`: an exception raised by `change` itself (a `CvestreamError` from `config/edit.py`, or anything else) must reach the caller unchanged, never be mapped to "valkey: cannot reach ..." (3.1 deferred item). Run `change` outside `_mapped()`, or re-raise anything that is not a `ValkeyError`.
  - [x] 1.12 Valkey `update`/`replace`: a `WatchError` whose `__context__` is a valkey `ConnectionError` or `TimeoutError` (valkey-py raises `WatchError("A ConnectionError occurred on while watching one or more keys")` when the connection drops after `WATCH`, `valkey/client.py` `_disconnect_raise_reset`) is mapped to `ConfigUnavailableError("valkey: cannot reach <address>: ConnectionError")` at once instead of being retried as a conflict (3.1 deferred item). A real `WatchError("Watched variable changed.")` is still retried.
  - [x] 1.13 Unit tests: `tests/unit/config/test_yaml_provider.py` (update, replace, document, missing file, invalid current refuses `update` but `replace` works, mode kept and `0600` for a new file, no temporary file left after a failure, read-only directory → `ConfigUnavailableError` one line, conflict when the file changes between read and rename (inject a hook or monkeypatch the re-read), quoting round trips of 1.7, comment notice); `tests/unit/config/test_valkey_provider.py` (replace writes on an invalid store with the existing fake-reader seam or a small fake pipeline, change errors pass through, the two `WatchError` cases); `tests/unit/config/test_provider.py` (`open_provider` returns a store, document validation helper).

- [x] **Task 2: Pure document edits (`config/edit.py`, new)** (AC: 1, 2)
  - [x] 2.1 Functions over `ConfigDocument`, returning a new document (`model_copy(update=...)` or `model_validate` of a modified dump; never mutate the input): `create_endpoint(doc, *, id, name, token, enabled, min_cvss, kev_only)`, `edit_endpoint(doc, id, *, name=UNSET, enabled=UNSET, min_cvss=UNSET, kev_only=UNSET)` (`min_cvss=None` clears the threshold, so use a sentinel for "not given"), `delete_endpoint(doc, id)`, `add_product(doc, id, item)`, `remove_product(doc, id, name) -> (doc, removed_count)`, `set_token(doc, id, token)`, `find_endpoint(doc, id)`.
  - [x] 2.2 Errors: a new `ConfigEditError(ConfigError)` (in `config/provider.py` next to the family, or in `edit.py`; name ends in `Error`) for "unknown endpoint 'x'", "endpoint 'x' already exists", "endpoint 'x' already has product 'php'" (names compared case-insensitively after stripping), "endpoint 'x' has no product 'php'", "nothing to change". Messages quote ids and product names (not secrets), never a token.
  - [x] 2.3 Validation of the new values goes through the models (`EndpointConfig`, `ProductEntryConfig`, the `Shorthand` type): an invalid slug, `--min-cvss 11`, a blank name or an invalid version raise `ConfigValidationError` with the formatted field errors (reuse the helper of 1.2). The final document is validated again by the provider before writing (duplicate tokens etc.).
  - [x] 2.4 Tests: `tests/unit/config/test_edit.py`, one per function and per error, plus "the input document is unchanged".

- [x] **Task 3: CLI plumbing (`cli/main.py`, `cli/context.py` new, `cli/output.py` new)** (AC: 2, 3)
  - [x] 3.1 Move `CliState` out of `cli/main.py` into `cli/context.py` (imported by `main.py` and the new command modules, which avoids a `main` ↔ `endpoint` import cycle). Add `new_token: Callable[[], str] = default_token` where `default_token()` returns `secrets.token_urlsafe(32)`. The root callback uses `ctx.ensure_object(CliState)` and sets `debug` on it, so a test-injected `obj` survives (checked: Typer 0.27.3 / Click 8.5 keep the `obj` passed to `CliRunner.invoke`).
  - [x] 3.2 In `cli/context.py`: `user_error(message) -> NoReturn` (one line on stderr, exit 2; the existing `_fail` copies in `config.py`/`db.py`/`main.py` may stay or move to it, your choice, one concern per commit) and a context manager or helper `open_store()` that loads the settings (`load_settings()`, `SettingsError`/`ValidationError` → one line exit 2, the `ValidationError` formatted without input as `_start()` does), opens the provider with `open_provider()`, always closes it, and turns every `ConfigError` into one line exit 2. Do **not** call `configure_logging()` in these commands and log nothing: stdout carries the command output (`--json` must stay parseable).
  - [x] 3.3 `cli/output.py`: `print_json(value)` (`json.dumps(..., indent=2, ensure_ascii=False, sort_keys=False)`), a plain-text table helper (left-aligned columns, no Rich, no ANSI), `feed_url(settings, token) -> str` (`<base>/feeds/<token>.xml`), and a `product_view(item)` that normalizes a product item to `{name, aliases, exclude, min_version, match_description}` (a shorthand string is expanded with the shorthand parser of `config/models.py`).
  - [x] 3.4 Register the new sub-apps in `cli/main.py` (`endpoint`, `product`, `token`), update the root help text (it still says "from EUVD and CISA KEV") and `config_app`'s help ("validate, schema, export, import").

- [x] **Task 4: `http.public.base_url` setting (`settings.py`)** (AC: 2)
  - [x] 4.1 Add `base_url: str | None = None` to `PublicListenerSettings`. Validate: scheme `http` or `https`, a host, no user info, no query, no fragment; strip one trailing `/`; a path prefix is allowed (`https://example.org/cvestream`). Messages never repeat the value. Not a secret.
  - [x] 4.2 Tests in `tests/unit/test_settings.py` (valid, trailing slash, path prefix, each rejection, env `CVESTREAM_HTTP__PUBLIC__BASE_URL`).
  - [x] 4.3 Document it in `settings.example.yaml` (commented, under `http.public`) and `docs/configuration.md`.

- [x] **Task 5: `cvestream endpoint` (`cli/endpoint.py`, new)** (AC: 1, 2, 3)
  - [x] 5.1 `endpoint create ID --name NAME [--min-cvss N] [--kev-only] [--disabled]`: token from `CliState.new_token()`; `store.update(lambda doc: create_endpoint(doc, ...))`; prints on **stdout** the feed URL as the last line (`feed: <url>`), after at most one confirmation line (`created endpoint ID`), so a script can take the last line. Duplicate id → exit 2. Products are added with `product add` (keep `create` small).
  - [x] 5.2 `endpoint list [--json]`: text table `ID NAME ENABLED MIN_CVSS KEV_ONLY PRODUCTS` (count), sorted by id; `--json`: an array of `{"id", "name", "enabled", "min_cvss", "kev_only", "product_count"}`. **No token, no URL** in `list`. Works with the read-only `cvestream-daemon` user.
  - [x] 5.3 `endpoint show ID [--json]`: every field, the products (normalized view of 3.3) and `feed_url`; `--json` object keys `id, name, enabled, min_cvss, kev_only, products, feed_url`. Unknown id → exit 2.
  - [x] 5.4 `endpoint edit ID [--name NAME] [--min-cvss N | --no-min-cvss] [--kev-only/--no-kev-only] [--enable/--disable]`: tri-state options (`bool | None = None`, checked with Typer 0.27.3), no option given → exit 2 "nothing to change"; `--min-cvss` together with `--no-min-cvss` → exit 2. The token is changed only by `token rotate`.
  - [x] 5.5 `endpoint delete ID [--yes]`: asks for confirmation (`typer.confirm`, default No) unless `--yes`; without a TTY and without `--yes` it exits 2 ("pass --yes to delete without a prompt") instead of hanging. Removes the endpoint, its products and its token (Valkey: `SREM` + `DEL` of the three keys, done by the provider).
  - [x] 5.6 Tests `tests/unit/cli/test_endpoint.py` on the YAML provider (`tmp_path`, `monkeypatch.chdir`, `CVESTREAM_ENDPOINTS_FILE`), injecting `CliState(new_token=lambda: "t" * 43)`: each command, each error (exit 2, one stderr line, empty stdout), `--json` parses, no token in `list` output, the file still validates after each write, an invalid change leaves the file bytes unchanged.

- [x] **Task 6: `cvestream product` (`cli/product.py`, new)** (AC: 1, 3)
  - [x] 6.1 `product add ENDPOINT PRODUCT [--alias A]... [--exclude T]... [--min-version V] [--no-match-description]`: `PRODUCT` is a name or the shorthand `name >= version`. Without options the item is stored as given (shorthand string, keeps the file short); with any option it is stored as a full entry (the shorthand's version becomes `min_version`; a shorthand version **and** `--min-version` → exit 2). Same product name already on the endpoint → exit 2.
  - [x] 6.2 `product remove ENDPOINT NAME`: removes every item whose name matches (case-insensitive, stripped; aliases are not considered); none → exit 2; prints how many were removed.
  - [x] 6.3 `product list ENDPOINT [--json]`: text `NAME MIN_VERSION ALIASES EXCLUDE MATCH_DESCRIPTION`; `--json` an array of the normalized view (3.3), so scripts get one shape whatever the storage form.
  - [x] 6.4 Tests `tests/unit/cli/test_product.py`.

- [x] **Task 7: `cvestream token rotate` (`cli/token.py`, new)** (AC: 2)
  - [x] 7.1 `token rotate ENDPOINT`: new token from `CliState.new_token()`, `store.update(set_token)`, prints the new feed URL on stdout and on stderr one line: "the old URL stops working once the daemon reloads (next cycle)". Unknown endpoint → exit 2. A generated token that collides with another endpoint's is caught by validation (exit 2; practically impossible with 256 bits).
  - [x] 7.2 Tests `tests/unit/cli/test_token.py`, plus `tests/integration/test_token_rotation.py` (no server, YAML provider): create an endpoint, build the feed cache from the provider's snapshot (`app/feed_cache.py`, look at how `tests/unit/app/test_public_api.py` builds the app and the cache), rotate with the CLI, reload (`pipeline.run.reload_config`), rebuild the cache: the public app answers `404` for the old token and `200` for the new one.

- [x] **Task 8: `cvestream config export|import` (`cli/config.py`)** (AC: 4)
  - [x] 8.1 `config export [--output FILE]`: `store.document()`, endpoints sorted by id, serialized with the same YAML dumper as the provider (1.7). Default stdout. `--output FILE` refuses an existing file (exit 2, like `db backup`) and creates it with mode `0600` (`os.open(..., O_CREAT | O_EXCL | O_WRONLY, 0o600)`).
  - [x] 8.2 `config import FILE`: load and validate `FILE` with `YamlConfigProvider(FILE).document()` (exit 2 with the same one line as `config validate` when invalid), then `store.replace(document)`. Print a summary on stdout: `imported N endpoints (added A, updated U, removed R), revision <rev>` (compute A/U/R against `store.document()` read just before, or against the endpoint ids only when the current content is invalid; never print tokens). With the YAML provider, importing the file that **is** `endpoints_file` (same resolved path) → exit 2 "nothing to import".
  - [x] 8.3 Tests in `tests/unit/cli/test_config.py`: export → validate → import round trip on two YAML files; export to an existing file refused; mode `0600`; import of an invalid file leaves the target unchanged; import replaces (an endpoint absent from the file is removed); import repairs an invalid target file.

- [x] **Task 9: Valkey integration tests (`tests/integration/test_cli_valkey.py`, marker `valkey`)** (AC: 1, 2, 3, 4)
  - [x] 9.1 Reuse `tests/integration/valkey_support.py` and the `admin` fixture (clean namespace). Use ids from `KNOWN_IDS` (`team-php`, `team-go`, `other`, `x`) or add yours there: the cleanup has no `SCAN`. Set the settings through the CLI environment (`CVESTREAM_CONFIG_PROVIDER=valkey`, `CVESTREAM_VALKEY__URL=support.url(ADMIN)`, `CVESTREAM_VALKEY__PASSWORD_FILE` pointing at `deploy/valkey/dev-secrets/cvestream-admin.password`) with `CliRunner.invoke(..., env=...)` and `@pytest.mark.allow_hosts(support.ALLOWED_HOSTS)`.
  - [x] 9.2 Cases: create on an unseeded store seeds it (`schema` = 1); each write command grows the revision by exactly 1 and leaves keys matching `docs/config-contract.md`; `delete` removes the set member and the three keys; `list`/`show` work as `cvestream-daemon`; any write as `cvestream-daemon` exits 2 with one stderr line naming the user, no traceback; export (Valkey) equals export (YAML) after an import of the same file (AC 4 round trip, byte for byte); `config import` repairs a store where a connector wrote invalid products JSON; a provider `update()` racing a connector `INCR` retries (already covered by 3.1's provider test: do not duplicate, reference it).
  - [x] 9.3 Run them locally against a disposable server (see "Running the valkey tests locally"); they must skip without `VALKEY_TEST_ADDRESS` and fail on CI without it, like 3.1's.

- [x] **Task 10: Docs** (AC: 6)
  - [x] 10.1 `docs/operations.md`: rewrite "Token rotation" around `cvestream token rotate <id>` (prints the new URL; the old URL answers `404` from the next cycle, that is the next source fetch, within the polling interval; resubscribe the channel); keep a short manual fallback for YAML. In "Backup", add the configuration: `cvestream config export -o endpoints-<date>.yaml` (holds tokens: mode `0600`, keep it private, encrypt it if it goes to git) and restore with `cvestream config import` (replaces everything, works on a broken store); with Valkey also the AOF/RDB of `/data`. In "Valkey", replace "until the operator CLI exists" and "once it exists" wording; add "Running the CLI against Valkey": `CVESTREAM_CONFIG_PROVIDER=valkey`, `CVESTREAM_VALKEY__URL=valkey://cvestream-admin@<host>:6379/0`, `CVESTREAM_VALKEY__PASSWORD_FILE=<admin password file>`, from a host or pod that may reach Valkey (dev stack: see 10.5). Mention that the CLI as `cvestream-daemon` can read (`list`, `show`, `export`) but not write.
  - [x] 10.2 `docs/configuration.md`: a `## CLI` section (anchor `#cli`, referenced by the YAML header) listing the commands and options with one example each, exit codes, what the YAML rewrite does (comments dropped, header line, key order, mode kept, atomic), where the feed URL base comes from (`http.public.base_url`); the "Tokens" paragraph points to `endpoint create`/`token rotate` instead of the `python -c` one-liner (keep the one-liner for hand editing).
  - [x] 10.3 `docs/config-contract.md`: one paragraph saying `cvestream` CLI writes follow the write protocol and rewrite every key of the configuration in one transaction (so a connector's concurrent `INCR` makes the CLI retry), and that `cvestream config export|import` exist for backups.
  - [x] 10.4 `README.md`: status paragraph (epic 3 in progress: Valkey provider and operator CLI), the quick start uses `cvestream endpoint create` / `product add` as an alternative to editing YAML by hand, link to `docs/configuration.md#cli`. `CLAUDE.md`: add the CLI rules that bind future agents (tokens only from the injectable generator; CLI writes only through `ConfigStore`; CLI commands log nothing on stdout). `settings.example.yaml`: `http.public.base_url`.
  - [x] 10.5 Dev stack (`compose.yml`): optional. If you add a way to run the CLI as `cvestream-admin` in the dev stack (for example a `valkey-admin-password` compose secret mounted only for a `docker compose run --rm -e ...` recipe in the docs), keep the daemon on `cvestream-daemon` and record the choice. A docs-only recipe (`docker compose run --rm -e CVESTREAM_VALKEY__URL=valkey://cvestream-admin@valkey:6379/0 -e CVESTREAM_VALKEY__PASSWORD=... app cvestream endpoint list`) is enough; never commit a real password.
  - [x] 10.6 Mark every paragraph you write in docs with `:factory:` (project convention for AI-written text).

- [x] **Task 11: Architecture and project context** (AC: 6)
  - [x] 11.1 `architecture.md`: Project Structure tree (`config/edit.py`, `cli/context.py`, `cli/output.py`, `cli/endpoint.py`, `cli/product.py`, `cli/token.py`, new tests); Data Architecture: YAML writes (atomic rename, mode kept, conflict re-check, comments dropped, header), `ConfigStore` (`document`/`update`/`replace`), import = replace; API & Communication Patterns "CLI (Typer)": exit codes (every `ConfigError` → 2), `--json` shapes, `http.public.base_url`, token generator injected through `CliState`; Infrastructure "Runtime configuration": `http.public.base_url`.
  - [x] 11.2 `project-context.md`: CLI rules (above), configuration rules (`replace` for import, YAML writer), testing rules (`CliRunner.invoke(obj=CliState(new_token=...))`).
  - [x] 11.3 `deferred-work.md`: mark the three 3.1 items this story resolves (`update()` cannot repair an invalid store, change errors mapped as Valkey failures, connection loss reported as a write conflict); keep "Configuration reload waits for a source fetch" open and note that `token rotate` documents the delay (see Decision 7).
  - [x] 11.4 `pyproject.toml` ruff per-file ignores `S105`/`S106` for the new test files that hold fake tokens (pattern of `tests/unit/cli/test_config.py`).

- [x] **Task 12: Gates and delivery**
  - [x] 12.1 Gates in the worktree: `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check`, `uv run ty check`, `uv run lint-imports`, `uv run pytest` (with **and** without `VALKEY_TEST_ADDRESS`), `uv run cvestream config schema --check` (no model change is expected: if you change `config/models.py`, regenerate the schema in the same commit), `uv run cvestream config validate endpoints.example.yaml`, `kubectl kustomize deploy/k8s/overlays/example` once `deploy/k8s` exists (after the rebase onto 3.3), `docker build --target runtime -t cvestream:story-3-2-operator-cli-for-endpoints-products-and-tokens .` (expected to fail behind the proxy with the ghcr.io x509 error: report it, rely on CI).
  - [x] 12.2 Manual run on the YAML provider and on a disposable Valkey: the commands of `epic-3-stack-plan.md`, "PR 3 of 3", steps 2 to 7.
  - [x] 12.3 Commits one concern each (see "Delivery"), story status `review` in this file and in `sprint-status.yaml`, push to the PR branch.

### Review Findings

_(filled by `bmad-code-review`)_

## Dev Notes

### Scope

In: the CLI commands of the ACs, the provider write API they need (YAML writer, `document`, `replace`), the `http.public.base_url` setting, the three 3.1 review items deferred to this story, docs. Out: a config-revision poll independent of the source fetches (Decision 7), changing the v1 contract models or the key layout, ACL changes (the "connectors can `SET` the revision" hardening stays deferred), a `product edit` command (not in the ACs: remove then add), Kubernetes manifests (story 3.3).

### Current state of the files this story touches (read them first)

| File | Today (on the 3.1 branch) | This story | Preserve |
|---|---|---|---|
| `src/cvestream/config/provider.py` | `ConfigProvider` protocol (`revision`, `load`), `ConfigSnapshot`, error family (`ConfigError` ← `ConfigUnavailableError` ← `ConfigAccessDeniedError`, `ConfigConflictError`; `ConfigValidationError(source, errors)`), `build_snapshot()` with token-safe error formatting, `open_provider()` (lazy Valkey import) | `ConfigStore` protocol, document validation helper, `ConfigEditError` (or in `edit.py`), `open_provider` return type | the daemon's use of `ConfigProvider`; message formatting (`_format_error`, product tag segments hidden); no logging in this module |
| `src/cvestream/config/yaml_provider.py` | read-only: `revision()` (SHA-256 of the bytes), `load()` with `_UniqueKeyLoader` (duplicate keys rejected, PyYAML messages never quoted) | `document`, `update`, `replace`, atomic writer, dumper | revision semantics (any byte change reloads), every existing error message |
| `src/cvestream/config/valkey_provider.py` | `load()` (revision bracket), `update(change)` (WATCH/MULTI/INCR, 5 attempts, full rewrite of every endpoint's keys), `_current_document` validates the stored doc (invalid → refuses), error mapping `_map`/`_mapped` | `document`, `replace`, shared write body, change errors pass through, `WatchError` after a dropped connection → unavailable | only module importing `valkey`; JSON written with `_dump` (`sort_keys=True`, compact); unseeded store seeded by the first write; messages never quote a value |
| `src/cvestream/config/models.py` | v1 contract (`ConfigDocument`, `EndpointConfig`, `ProductEntryConfig`, `Shorthand`, `_split_shorthand`), `extra="forbid"`, `strict=True` | **no change expected** (reuse `_split_shorthand` through a small public wrapper if needed; that is not a contract change) | `schemas/config-v1.json` stays identical (`config schema --check`) |
| `src/cvestream/cli/main.py` | root app, `CliState(debug)`, `_start()` (settings + logging), `run`, `poll --once`, `main()` supervisor (exit 1, `--debug` re-raises) | sub-apps registered, `CliState` moved, `ensure_object` | `pretty_exceptions_enable=False`, the `_DEBUG` context variable read by `main()`, `healthcheck` stays stdlib-only and fast (do not import providers at module level in a way that loads `valkey`) |
| `src/cvestream/cli/config.py` | `validate` (YAML file, exit 0/2), `schema [--check] [-o]` | `export`, `import` | `validate` semantics and output (tests assert its exact line) |
| `src/cvestream/settings.py` | `PublicListenerSettings(host, port=8080)`, `extra="forbid"` | `base_url` | everything else |
| `docs/operations.md` | "Token rotation" (manual, "until the operator CLI exists"), "Valkey" (backup "once it exists"), "Backup" (SQLite only) | rewritten sections | other sections; 3.3 adds Kubernetes sections here (rebase) |

### Provider write API (shape)

```python
class ConfigStore(ConfigProvider, Protocol):
    def document(self) -> ConfigDocument: ...                 # validated current content
    def update(self, change: Callable[[ConfigDocument], ConfigDocument]) -> str: ...
    def replace(self, document: ConfigDocument) -> str: ...  # ignores the current content
```

- `change` is pure and may run several times (once per attempt): never generate the token inside it. Generate it once in the CLI, then pass it in (`lambda doc: set_token(doc, endpoint_id, token)`).
- Both `update` implementations validate the current content first: an invalid file or store refuses every edit with the field errors (exit 2) and the operator repairs it with `config import` or by hand. Say so in the error? The `ConfigValidationError` already names the field; the CLI may append "(fix it by hand or with: cvestream config import)". Keep it one line.
- YAML revision returned by `update`/`replace` = SHA-256 of the bytes written, i.e. what the daemon's `revision()` will read.

### YAML writer details

- Rename semantics: `os.replace` in the same directory is atomic on POSIX; the daemon reads the whole file in one `read_bytes()`, so it sees the old or the new file, never a mix. A temp file in another directory (e.g. `/tmp`) would make `os.replace` fail across filesystems: always `dir=<target dir>`.
- A **read-only** directory (`deploy/compose/compose.yml` mounts `./config:/config:ro`; Kubernetes mounts the file from a Secret, read-only, through `..data` symlinks): the write fails with `ConfigUnavailableError` → one line, exit 2. Docs: with those deployments, run the CLI where the file is writable (on the host, then the daemon reloads it through the mount), or use the Valkey provider.
- A single-file bind mount (`-v ./endpoints.yaml:/app/endpoints.yaml`) cannot be replaced by rename (`EBUSY`): same error path. The self-hosted compose already mounts a directory for that reason.
- Keep `chmod 600` (docs ask for it); a new file gets `0o600` because `mkstemp` creates it so.
- Conflict check: two CLI writers or an editor saving between read and rename. Re-read the SHA-256 just before `os.replace`; on mismatch, retry the whole read-change-validate cycle (5 attempts). Not a lock: a tiny window remains, acceptable for an operator tool (record it in the docs).

### Exit codes and messages

| Situation | Exit | Output |
|---|---|---|
| success | 0 | command output on stdout |
| unknown/duplicate id, product exists/missing, nothing to change, bad option combination, missing `--yes` without a TTY | 2 | one stderr line |
| `ConfigValidationError` (invalid value, invalid current file/store, invalid import file) | 2 | `str(exc)`: `<source>: <first error> (+N more)` |
| `ConfigUnavailableError` (file unreadable/unwritable, Valkey down), `ConfigAccessDeniedError` (wrong password, daemon user writing), `ConfigConflictError` (5 lost races) | 2 | `str(exc)`, built by the provider, never a valkey-py message |
| settings error | 2 | as `_start()` formats it |
| anything else | 1 | `unexpected error: <Class> (run with --debug for the traceback)`; `--debug` re-raises |

Exit 2 for an unreachable Valkey is a choice (the daemon uses 3 for that): the CLI has no retry and one-line errors are what the architecture asks for. Recorded as Decision 4.

### `--json` shapes (snake_case, no envelope)

```json
// endpoint list --json
[{"id": "team-php", "name": "PHP team", "enabled": true, "min_cvss": 7.0, "kev_only": false, "product_count": 2}]
// endpoint show team-php --json
{"id": "team-php", "name": "PHP team", "enabled": true, "min_cvss": 7.0, "kev_only": false,
 "products": [{"name": "php", "aliases": [], "exclude": [], "min_version": "8.2", "match_description": true}],
 "feed_url": "https://feeds.example.org/feeds/<token>.xml"}
// product list team-php --json
[{"name": "php", "aliases": [], "exclude": [], "min_version": "8.2", "match_description": true}]
```

`null` for an unknown/absent `min_cvss` and `min_version` (project rule: unknown is `None`, never `0` or `""`).

### Logging and secrets

- The new commands log nothing (no `configure_logging()`), so stdout stays clean for `--json` and pipes. Errors go to stderr as one line.
- A token is printed only as part of the feed URL (`endpoint create`, `endpoint show`, `token rotate`) and in `config export`. Tests assert that `endpoint list`, `product *`, every error line and `config import` output contain no token.
- `config export --output` creates the file `0600`; stdout export is the operator's responsibility (docs warn).

### Valkey specifics the CLI relies on (3.1 code, read it)

- `update()` rewrites **every** endpoint's three keys and `schema` in one transaction (not only the edited endpoint). Consequence: a connector that changed products and `INCR`ed between the CLI's `WATCH` and `EXEC` makes `EXEC` fail and the CLI retries with fresh data, so the connector's change is never lost. Do not "optimize" to per-key writes without keeping that property.
- JSON written by the provider is compact and sorted (`_dump`); the CLI never writes keys itself.
- ACL: `cvestream-admin` has `get mget smembers exists set del sadd srem incr incrby watch unwatch multi exec discard`; `replace` must use only those (no `KEYS`/`SCAN`/`UNLINK`/`RENAME`).
- Error mapping order matters (`AuthenticationError` subclasses `ConnectionError`; `NoPermissionError` subclasses `ResponseError`); a write as `cvestream-daemon` fails with `NoPermissionError` on `WATCH`/`MULTI` or is aborted in `EXEC` (`ExecAbortError`) → `ConfigAccessDeniedError` with the user name.
- `WatchError` after a dropped connection: `valkey/client.py` `_disconnect_raise_reset` raises it from inside the `except (ConnectionError, TimeoutError)` handler, so `exc.__context__` is the connection error. Unit-test it with a fake that raises `WatchError` from a `valkey.exceptions.ConnectionError`.

### Settings: `http.public.base_url`

- Why a setting: the daemon only knows its listener (`0.0.0.0:8080`); the URL readers use is behind a reverse proxy or an Ingress (`https://feeds.example.org`). It is static, not part of the v1 contract, not secret.
- Default `None` → `http://localhost:<http.public.port>` (useful in dev and with `docker compose`; the docs tell production to set the setting). Printing a path only was the alternative (Decision 3).
- Kubernetes (after the rebase onto 3.3): consider adding `CVESTREAM_HTTP__PUBLIC__BASE_URL` (commented) to `deploy/k8s/base/configmap-settings.yaml`, which 3.3 creates. Only if it reads well; record it.

### Stack and rebase onto 3.3

- This branch: `story-3-2-operator-cli-for-endpoints-products-and-tokens`, started from the reviewed `origin/story-3-1-valkey-configuration-provider` (`22d0ef0`). PR base: the 3.1 branch for now. Story 3.3 (`story-3-3-release-and-kubernetes-deployment`) is being reviewed in parallel; after its review this branch is rebased with `git rebase --onto origin/story-3-3-release-and-kubernetes-deployment origin/story-3-1-valkey-configuration-provider`, pushed with `--force-with-lease`, and the PR retargeted to the 3.3 branch (`epic-3-stack-plan.md`, section 1).
- Files both stories touch (3.3's diff against 3.1 changes them too): `docs/operations.md` (3.3 adds Kubernetes, release and image sections, and mentions "an operator CLI pod" with the `cvestream.io/valkey-access: "true"` label that may reach the example Valkey), `README.md`, `CLAUDE.md`, `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/project-context.md`, `_bmad-output/implementation-artifacts/deferred-work.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml`, `.github/workflows/ci.yml` (3.3 adds the Kustomize step; **this story should not need to touch `ci.yml`**: the new `valkey` tests run in the existing pytest step), `deploy/compose/compose.yml` (3.3 changes it; this story should not need to). Keep both sides on conflict; check that each doc's TL;DR and section order still read well.
- After the rebase, add to `docs/operations.md` (Kubernetes part from 3.3) how to run the CLI against the example Valkey: a one-off pod with the `cvestream.io/valkey-access: "true"` label, `CVESTREAM_CONFIG_PROVIDER=valkey`, the admin user URL and the admin password from a Secret; or `kubectl port-forward` to Valkey and the CLI on the operator's machine. And run `kubectl kustomize deploy/k8s/overlays/example` as a gate.
- `uv.lock`: this story adds **no dependency** (PyYAML and Typer are already locked). If you end up adding one, on a rebase conflict take the base's `uv.lock` and run `uv lock`, never merge it by hand.

### Decisions taken autonomously (the stack plan lists them as points for a human decision)

Recorded here and in the PR; the code review raises them as "decision needed" if it disagrees.

1. **YAML rewrite drops comments; no round-trip library.** PyYAML `safe_dump` (already locked) with a header line and a stderr notice. `ruamel.yaml` (0.19.1 on PyPI) would keep comments but parses YAML 1.2 while the daemon's loader is YAML 1.1 (`yes`, `8.10`, `0777` read differently), adds a dependency and a second YAML code path for the same file. Hand editing stays supported; operators who want comments keep their file in git and use `config import`.
2. **Import replaces** the whole configuration (endpoints absent from the file are removed), through `replace()`, which also repairs an invalid or unseeded store. Merge semantics would make a backup restore inexact. The summary line shows added/updated/removed counts.
3. **Feed URL base:** new setting `http.public.base_url`, default `http://localhost:<port>`.
4. **Exit codes:** every `ConfigError` (including Valkey unreachable or access denied) is a user error, exit 2, one line. The CLI does not retry an unreachable Valkey and does not use the daemon's exit 3.
5. **`endpoint delete` asks for confirmation** unless `--yes`; without a TTY it refuses (exit 2) instead of hanging.
6. **Endpoint ID rules** stay the contract's slug `^[a-z0-9-]{1,40}$` (validated by the model, message names the field); no extra rule (e.g. no leading hyphen ban) since that would be a contract change.
7. **No config-revision poll** in the daemon (deferred item "Configuration reload waits for a source fetch" stays open): a rotated token takes effect at the next cycle (next source fetch, within the polling interval, 15 min by default). `token rotate` says so on stderr and the docs say so. Adding a poll touches `app/scheduler.py` and is a separate concern.
8. **`--json` output** uses the normalized product view (always objects), not the stored form (shorthand string or object).
9. **`endpoint create` takes no products** (use `product add`) and **no `--token` option** (a token on the command line ends up in the shell history).
10. **Duplicate product names** are refused by `product add` (case-insensitive), not by the contract (hand-written files may still have them); `product remove` removes every match.

### Testing standards for this story

- Unit tests mirror `src/` (`tests/unit/config/test_edit.py`, `tests/unit/cli/test_endpoint.py`, `test_product.py`, `test_token.py`, additions to `test_config.py`, `test_main.py` if the root callback changes, `tests/unit/test_settings.py`); CLI tests use `typer.testing.CliRunner` like `tests/unit/cli/test_config.py` (`monkeypatch.chdir(tmp_path)`, delete `CVESTREAM_SETTINGS_FILE`/`CVESTREAM_ENDPOINTS_FILE`, strip ANSI). Assert `result.stdout`, `result.stderr` and `result.exit_code` separately.
- Deterministic tokens: `runner.invoke(app, [...], obj=CliState(new_token=lambda: "t" * 43))`; for several tokens in one test, an iterator-backed function. Never patch `secrets`.
- No network: YAML tests need none; `valkey` tests use `allow_hosts` (3.1 pattern), skip locally without a server, fail on CI.
- File-mode tests: `stat.S_IMODE(path.stat().st_mode)`; a read-only directory test must restore permissions in a `finally` (and skip when running as root, where `chmod` does not block writes).
- Guardrail tests stay green: `tests/integration/test_restart_no_repost.py`, `tests/integration/test_startup_requires_valkey.py`.
- Fake tokens need the ruff `S105`/`S106` per-file ignores (Task 11.4).
- `uv run pytest` must pass with and without `VALKEY_TEST_ADDRESS`.

### Running the valkey tests locally

```sh
docker run -d --rm --name cvs-valkey-story32 -p 127.0.0.1::6379 \
  -v "$PWD/deploy/valkey:/usr/local/etc/valkey:ro" \
  valkey/valkey:9.1.3 valkey-server /usr/local/etc/valkey/valkey.conf
export VALKEY_TEST_ADDRESS=$(docker port cvs-valkey-story32 6379 | head -1)   # 127.0.0.1:<port>
uv run pytest -m valkey -v
docker stop cvs-valkey-story32
```

Story-specific container name and a random host port: 3.3 may run its own Valkey at the same time. Behind the proxy, `uv sync` needs `SSL_CERT_FILE=$HOME/ca.pem` (TLS interception); Docker Hub pulls work, ghcr.io pulls do not (x509, the daemon does not trust the proxy CA).

### Previous story intelligence

- **3.1 (Valkey provider):** the write protocol exists (`update`), unseeded store seeded by the first write, `ConfigAccessDeniedError`/`ConfigConflictError` extend `ConfigUnavailableError`, messages built by hand (never `str(valkey_exc)`), socket timeouts 5 s, `WATCH` reset by the pipeline context manager. Three review items were deferred **to this story** (Tasks 1.10–1.12). The `valkey` test helpers (`valkey_support.py`, `admin` fixture, `KNOWN_IDS` cleanup) are ready to reuse. valkey-py sends `INCR` as `INCRBY`.
- **1.3 (YAML provider):** messages never quote input (PyYAML marks, `errors(include_input=False)`); duplicate keys rejected; `config validate` exit 0/2 contract; YAML 1.1 pitfalls documented in `docs/configuration.md`.
- **1.6 (daemon / CLI root):** `main()` supervisor prints one line, exit 1, `--debug` re-raises; `pretty_exceptions_enable=False`; `healthcheck` must stay stdlib-only and fast (it is in the same Typer app: do not make module-level imports of `cli/endpoint.py` etc. load heavy things beyond what `cli/config.py` already loads; `config/provider.py` already imports pydantic and settings, that is fine).
- **2.x:** commits `feat|fix|docs|test|chore|ci|build|refactor(story-x.y): ...`, one concern each; local `docker build` fails behind the proxy, CI is the gate.

### Git intelligence

- Base `22d0ef0 chore(story-3.1): record review findings` (top of the reviewed 3.1 branch: `0c7b0d8 fix(story-3.1): verify the Valkey TLS host name`, `3029b30 chore(story-3.1): mark story ready for review`, 3.1 feature commits `8464f80`…`7e7a155`). The 3.1 commit style is the model: small `feat`, `test`, `docs` commits, the story file updated in a final `chore` commit.

### Latest technical information (checked 2026-11-05)

- **Typer 0.27.3 / Click 8.5.0 / Rich 15.0.0** (locked). Checked in the locked environment: `CliRunner.invoke(app, args, obj=...)` passes `obj` to the root context and `ctx.ensure_object(CliState)` keeps it; a repeated `Annotated[list[str] | None, typer.Option("--alias")] = None` collects every value; tri-state `Annotated[bool | None, typer.Option("--kev-only/--no-kev-only")] = None` gives `None`/`True`/`False`. `typer.confirm(..., default=False)` reads stdin (`CliRunner.invoke(..., input="y\n")` in tests).
- **PyYAML 6.0.3** (locked, via `pydantic-settings[yaml]`): `yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)` quotes strings that would resolve to another type (`"8.10"`, `"123"`, `"yes"`), so a dump reloads to the same values; it writes no comments.
- **ruamel.yaml 0.19.1** (PyPI): the round-trip alternative, not chosen (Decision 1).
- **valkey-py 6.2.0** (locked): `Pipeline.watch()` switches to immediate mode until `multi()`; `execute()` raises `WatchError("Watched variable changed.")` on a lost race, and `WatchError("A ConnectionError occurred on while watching one or more keys")` when the connection drops while watching (`valkey/client.py`).
- **Python 3.14:** `os.replace` is atomic within one filesystem on POSIX; `tempfile.mkstemp` creates the file `0o600`; `except A, B:` without parentheses is valid 3.14 syntax and already used in this code base (ruff formats it so).

### Project Structure Notes

- New modules: `config/edit.py`, `cli/context.py` (not in the architecture tree: add them), `cli/output.py`, `cli/endpoint.py`, `cli/product.py`, `cli/token.py` (already in the tree). New tests: `tests/unit/config/test_edit.py`, `tests/unit/cli/test_endpoint.py`, `test_product.py`, `test_token.py`, `tests/integration/test_cli_valkey.py`, `tests/integration/test_token_rotation.py`.
- Layering (import-linter): `cli` → `config`, `settings` (allowed); `config/edit.py` imports only `config.models`/`config.provider` and the stdlib; `typer` stays in `cli/`, `valkey` stays in `config/valkey_provider.py`. `cli` must not import `cvestream.app` at module level for these commands (the integration test of Task 7.2 may import `app` and `pipeline`: tests are not under the contracts).
- No `utils.py`/`helpers.py`: shared CLI code goes in `cli/context.py` and `cli/output.py`.

### Delivery

- Branch `story-3-2-operator-cli-for-endpoints-products-and-tokens`, draft PR against `story-3-1-valkey-configuration-provider` (opened at story creation), later rebased onto 3.3 and retargeted (see above).
- Suggested commits, one concern each: `refactor(story-3.2): share document validation between providers`, `feat(story-3.2): add the YAML configuration writer`, `feat(story-3.2): add document and replace to the Valkey provider`, `fix(story-3.2): let change errors through the Valkey write protocol`, `fix(story-3.2): report a connection lost while watching as unavailable`, `feat(story-3.2): add pure configuration edits`, `feat(story-3.2): add the public base URL setting`, `refactor(story-3.2): move the CLI state to its own module`, `feat(story-3.2): add endpoint commands`, `feat(story-3.2): add product commands`, `feat(story-3.2): add token rotation`, `feat(story-3.2): add config export and import`, `test(story-3.2): add Valkey CLI integration tests`, `test(story-3.2): prove a rotated token stops serving`, `docs(story-3.2): document the operator CLI`, `docs(story-3.2): amend the architecture for the operator CLI`, `chore(story-3.2): mark story ready for review`. Every commit message ends with the line `🤖 Généré avec Claude Code`.
- Never push to `main`; `git push --force-with-lease` only after a rebase.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.2: Operator CLI for endpoints, products and tokens]
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Architecture] (key layout, write protocol "the CLI (story 3.2) builds on it", YAML provider)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] (token `secrets.token_urlsafe(32)` stored in plaintext so `endpoint show` can print the URL; rotation at the next reload; `cvestream-admin` is the CLI's user)
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns] (CLI command list)
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (backups: Valkey RDB + `cvestream config export`)
- [Source: _bmad-output/planning-artifacts/architecture.md#Naming Patterns] (CLI kebab-case, noun then verb), `#Process Patterns` (CLI exit codes, injectable randomness), `#Project Structure & Boundaries` (`cli/endpoint.py`, `product.py`, `token.py`, `output.py`; `cli` imports `config`, `settings`)
- [Source: _bmad-output/project-context.md] (CLI, Configuration, Randomness, Security rules)
- [Source: _bmad-output/implementation-artifacts/epic-3-stack-plan.md] (stack order, rebase onto 3.3, manual checks of PR 3 of 3, points needing a decision for 3.2)
- [Source: _bmad-output/implementation-artifacts/3-1-valkey-configuration-provider.md#Review Findings] (three items deferred to 3.2), `#Write protocol (admin, story 3.2 builds on it)`
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] ("Deferred from: code review of 3-1", "Configuration reload waits for a source fetch")
- [Source: src/cvestream/config/provider.py], [Source: src/cvestream/config/yaml_provider.py], [Source: src/cvestream/config/valkey_provider.py], [Source: src/cvestream/config/models.py], [Source: src/cvestream/config/keys.py], [Source: src/cvestream/cli/main.py], [Source: src/cvestream/cli/config.py], [Source: src/cvestream/settings.py], [Source: src/cvestream/app/feed_cache.py], [Source: deploy/valkey/users.acl], [Source: deploy/compose/compose.yml], [Source: tests/integration/valkey_support.py], [Source: tests/unit/cli/test_config.py]
- External: valkey-py 6.2.0 `valkey/client.py` (`Pipeline.watch`, `_disconnect_raise_reset`, `_execute_transaction`), PyPI `ruamel.yaml` 0.19.1, Typer 0.27.3 / Click 8.5.0 behaviour checked locally.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5.5 (`claude-sonnet-5-5`), `bmad-dev-story`, autonomous run, 2026-11-05.

### Debug Log References

- Baseline before the change: 1314 passed, 28 skipped. After: 1582 passed with `VALKEY_TEST_ADDRESS` (disposable `valkey/valkey:9.1`, story-specific container and random port), 1541 passed and 41 skipped without it.
- `tests/unit/cli/test_db.py::test_migrate_creates_the_database_and_is_idempotent` failed once in a full run and passed in every other run and alone (not touched by this story: looks like a pre-existing flake).
- `uv run` rebuilds the package when `pyproject.toml` changes; behind the proxy that needs `SSL_CERT_FILE=$HOME/ca.pem`.

### Completion Notes List

- Story created by `bmad-create-story` (autonomous run, 2026-11-05). Ultimate context engine analysis completed: comprehensive developer guide created. Choices made without a human: see "Decisions taken autonomously".
- **Provider write API.** `ConfigStore` (`document`, `update`, `replace`) in `config/provider.py`; `validate_document()` shared with `build_snapshot()`; `ConfigEditError` next to the family. YAML: atomic writer with SHA-256 conflict re-check, mode kept (`0600` for a new file), `dump_document()` (header line, defaults omitted), `dropped_comments` flag for the CLI notice. Valkey: `document()` shares `_consistent_read()` with `load()`; `replace()` is the write-protocol transaction without parsing the stored content (it `SMEMBERS` the set, drops invalid members, `DEL`s the set and the three keys of every valid old id, then writes the new document); errors raised by `change` reach the caller unchanged (`_ChangeFailedError` wrapper, `except Exception` only to re-raise it); a `WatchError` whose `__context__` is a connection or timeout error is `ConfigUnavailableError`. The three 3.1 deferred items are resolved (`deferred-work.md`).
- **Pure edits** in `config/edit.py` (sentinel `UNSET` so `min_cvss=None` clears the threshold; values validated by building the endpoint through `validate_document`, messages `endpoint: <field>: ...`). `models.py` gained `split_shorthand()`, a public wrapper (no contract change: `config schema --check` passes).
- **CLI.** `cli/context.py` (`CliState` with `new_token`, `user_error`, `open_store()`, `warn_dropped_comments`, `is_interactive`), `cli/output.py`, `cli/endpoint.py`, `cli/product.py`, `cli/token.py`, `config export|import`. `http.public.base_url` added to the settings (documented in `settings.example.yaml` and `docs/configuration.md`).
- **Tests.** Unit tests for each module, a fake WATCH/MULTI/EXEC client for the Valkey write protocol, `tests/integration/test_token_rotation.py` (no server) and `tests/integration/test_cli_valkey.py` (marker `valkey`, 13 tests: seeding, revision +1 per command, key layout, delete, daemon user read-only, wrong credentials, YAML export to Valkey import to Valkey export byte for byte, replace, repair of an invalid store).
- **Docs.** `docs/operations.md` (token rotation, configuration backup, running the CLI against Valkey), `docs/configuration.md` (`## CLI`, `http.public.base_url`), `docs/config-contract.md`, `README.md`, `CLAUDE.md`, `settings.example.yaml`, `architecture.md`, `project-context.md`, `deferred-work.md`.
- **Autonomous choices and deviations from the story text:**
  1. `endpoint delete` without `--yes` and without a terminal exits 2; the confirmation path is tested by monkeypatching `cvestream.cli.endpoint.is_interactive` (`CliRunner` stdin is never a TTY).
  2. `config import` prints no added/updated/removed counts when the current content cannot be read (invalid, unseeded or missing): the `ConfigStore` API has no way to list the ids of an invalid store. It says "(the previous content could not be read)" instead. Recorded in `deferred-work.md`.
  3. An unreadable YAML file (`chmod 000`) is a `ConfigUnavailableError`, so exit 2 with one line, not exit 1 as the stack plan's manual check expects (the story's exit-code table puts every `ConfigError` at 2).
  4. `product add` options `--match-description/--no-match-description` are tri-state (`None` = not given), so passing either stores a full entry.
  5. Rejected values print `endpoint: <field>: <message>` (source `endpoint`), e.g. `endpoint: min_cvss: Input should be less than or equal to 10`.
  6. `tests/unit/cli/conftest.py` holds a shared `cli` fixture; per-file ruff ignores `S105`/`S106` were added for the new test files.
  7. The root help text no longer says "from EUVD and CISA KEV".
  8. 3.1 gained the plan PR-number commit (`638d57d`) after this branch was cut, so `git merge --ff-only origin/story-3-1-valkey-configuration-provider` was not possible (diverged): skipped as instructed. That commit touches only the stack plan and `sprint-status.yaml`; the later rebase onto 3.3 takes it.
- **Gates.** `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports` (7 kept), `pytest` (with and without `VALKEY_TEST_ADDRESS`), `config schema --check`, `config validate endpoints.example.yaml`: all green. `kubectl kustomize deploy/k8s/overlays/example`: not applicable (`deploy/k8s` does not exist on this branch, it comes with 3.3). `docker build --target runtime`: fails in this sandbox while `uv sync` downloads packages in the build (`dns error`, no network inside the build), not with the ghcr.io x509 error; rely on the CI `ci` check.
- Manual run (YAML provider, scratch copy of `endpoints.example.yaml`): every command of the stack plan step 2 and the exit-code checks of step 3 behave as documented; the file stays `0600`. The Valkey side (step 4) is covered by `test_cli_valkey.py` against a real server. Step 5 (daemon plus curl) is covered by `test_token_rotation.py`; not run with a live daemon.

### File List

- `CLAUDE.md`
- `README.md`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/3-2-operator-cli-for-endpoints-products-and-tokens.md`
- `_bmad-output/planning-artifacts/architecture.md`
- `_bmad-output/project-context.md`
- `docs/config-contract.md`
- `docs/configuration.md`
- `docs/operations.md`
- `pyproject.toml`
- `settings.example.yaml`
- `src/cvestream/cli/config.py`
- `src/cvestream/cli/context.py` (new)
- `src/cvestream/cli/endpoint.py` (new)
- `src/cvestream/cli/main.py`
- `src/cvestream/cli/output.py` (new)
- `src/cvestream/cli/product.py` (new)
- `src/cvestream/cli/token.py` (new)
- `src/cvestream/config/edit.py` (new)
- `src/cvestream/config/models.py`
- `src/cvestream/config/provider.py`
- `src/cvestream/config/valkey_provider.py`
- `src/cvestream/config/yaml_provider.py`
- `src/cvestream/settings.py`
- `tests/integration/test_cli_valkey.py` (new)
- `tests/integration/test_token_rotation.py` (new)
- `tests/unit/cli/conftest.py` (new)
- `tests/unit/cli/test_config_io.py` (new)
- `tests/unit/cli/test_context.py` (new)
- `tests/unit/cli/test_endpoint.py` (new)
- `tests/unit/cli/test_output.py` (new)
- `tests/unit/cli/test_product.py` (new)
- `tests/unit/cli/test_token.py` (new)
- `tests/unit/config/test_edit.py` (new)
- `tests/unit/config/test_provider.py`
- `tests/unit/config/test_valkey_provider.py`
- `tests/unit/config/test_yaml_provider.py`
- `tests/unit/test_settings.py`

### Change Log

- 2026-11-05: story created (`ready-for-dev`).
- 2026-11-05: story implemented (provider write API, pure edits, `http.public.base_url`, endpoint/product/token/config commands, tests, docs); status `review`.
