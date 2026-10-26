---
baseline_commit: 20cf3c98f26e2198c6da1aa9ca4d0f40c49f0ae5
---

# Story 1.1: Project skeleton, container and CI

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a buildable, tested container image and a containerized dev loop from day one,
so that every following story ships through the same quality gate and runs the same way under Docker, Kubernetes and locally.

## Acceptance Criteria

1. **Project initialized with uv.** Given an empty repository apart from planning files, when the project is initialized with `uv init --app --build-backend uv_build --python 3.14 --name cvestream .`, then `pyproject.toml`, `uv.lock`, `.python-version` (`3.14`) and `src/cvestream/` exist, and `uv run cvestream --help` prints the Typer root help (`cli/main.py`, `__main__.py`).
2. **Dependencies and tool config.** Runtime and dev dependencies are added with `uv add` at the versions pinned in architecture.md (only those this story uses), and every tool is configured in `pyproject.toml`: ruff with `DTZ`, `BLE`, `S`; ty; pytest with anyio, pytest-socket blocking the network, markers `live` and `valkey`; import-linter contracts for the layering in architecture.md, including `packaging` as the only third-party import allowed in `domain`.
3. **Cross-cutting modules.** `errors.py` (`CvestreamError`), `clock.py` (`Clock` protocol, `SystemClock`), `log.py` (structlog JSON/console, token redaction) and `settings.py` (pydantic-settings, `CVESTREAM_*` env with `__` nesting over optional `settings.yaml`, `*_FILE` secrets, per-source `interval` defaulting to 15 min and rejected above 60 min for NFR-1) exist with unit tests, and `tests/conftest.py` provides a `FakeClock`.
4. **Dockerfile.** When `docker build` runs, it has `builder`, `dev` and `runtime` targets; the runtime image is `python:3.14-slim-trixie`, runs as UID 999 without uv, declares volume `/data`, `EXPOSE 8080 9090`, `ENTRYPOINT ["cvestream"]`; `.dockerignore` excludes `_bmad/`, `_bmad-output/`, `.claude/`.
5. **Dev loop.** `compose.yml` runs the `dev` target with `develop.watch` (sync `src/`, rebuild on `uv.lock`) and `.devcontainer/devcontainer.json` points to it.
6. **CI.** On a pull request, `.github/workflows/ci.yml` runs `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports`, `pytest`, then builds the image (and pushes `ghcr.io/wixyvir/cvestream:edge` + `sha-<short>` on `main`), with every Action pinned by SHA and least-privilege `permissions:`.
7. **Dependabot.** `.github/dependabot.yml` covers `uv`, `github-actions` and `docker` weekly.
8. **Docs.** `README.md` (purpose, dev quick start) and `CLAUDE.md` (points to architecture.md and project-context.md, key MUSTs) exist.

## Tasks / Subtasks

- [x] **Task 1: Initialize the uv project (AC: 1, 2)**
  - [x] 1.1 From the repo root run `uv init --app --build-backend uv_build --python 3.14 --name cvestream .` (uv 0.13.5). It creates `pyproject.toml`, `.python-version` (`3.14`), `src/cvestream/__init__.py` and an empty `README.md`; it keeps the existing `.gitignore` and does not re-init git.
  - [x] 1.2 Edit the generated `pyproject.toml`: real `description` (one line: filtered vulnerability RSS feeds from EUVD and KEV), keep `requires-python = ">=3.14"`, keep `build-backend = "uv_build"`. `uv init` copies `authors` from the git config: keep it or drop it, do not invent one. Do **not** add a `license` field or a `LICENSE` file (architecture gap M1: licence not chosen).
  - [x] 1.3 Replace the generated entry point: `[project.scripts] cvestream = "cvestream.cli.main:main"` (uv init writes `cvestream:main`). Remove the generated `def main()` from `__init__.py`; `__init__.py` only exposes `__version__` via `importlib.metadata.version("cvestream")`.
  - [x] 1.4 Runtime deps (`uv add`): `typer`, `structlog`, `pydantic`, `pydantic-settings[yaml]` (the `yaml` extra pulls `pyyaml`, needed for `settings.yaml`). Nothing else: no httpx, sqlalchemy, alembic, valkey, starlette, uvicorn, packaging, feedparser yet (later stories add them).
  - [x] 1.5 Dev deps (`uv add --dev`): `ruff`, `ty`, `import-linter`, `pytest`, `pytest-socket`, `anyio` (provides the pytest anyio plugin), `watchfiles` (dev loop). Not yet: `respx`, `mypy` (fallback only if ty breaks).
  - [x] 1.6 Commit `uv.lock`. Never edit it by hand.

- [x] **Task 2: Package skeleton and CLI root (AC: 1, 2)**
  - [x] 2.1 Create `src/cvestream/__main__.py`: `from cvestream.cli.main import main` then `main()` (so `python -m cvestream --help` works).
  - [x] 2.2 Create `src/cvestream/cli/__init__.py` and `src/cvestream/cli/main.py`: `app = typer.Typer(name="cvestream", no_args_is_help=True, add_completion=False)`, a root `@app.callback()` with a `--debug` flag (stored for later error handling: traceback only under `--debug`) and a `--version` eager option printing `__version__`; `def main() -> None: app()`. **No subcommands in this story** (`run`, `poll`, `healthcheck`, `config`, `db`... belong to later stories).
  - [x] 2.3 Create the empty layer packages with a one-line module docstring each: `domain/`, `config/`, `sources/`, `state/`, `pipeline/`, `app/` (`__init__.py` only). Reason: import-linter fails a `layers` contract when a listed layer does not exist, and a `forbidden` contract when a source module does not exist (verified on import-linter 2.15). They are in the architecture tree anyway.

- [x] **Task 3: Cross-cutting modules (AC: 3)**
  - [x] 3.1 `errors.py`: `class CvestreamError(Exception)`, docstring stating every exception of ours inherits from it and ends in `Error`. Nothing else (the `SourceError` family lives in `sources/base.py`, story 1.4).
  - [x] 3.2 `clock.py`: `class Clock(Protocol): def now(self) -> datetime: ...` and `class SystemClock` returning `datetime.now(UTC)`. Aware UTC only.
  - [x] 3.3 `settings.py`: see "Settings design" in Dev Notes (models, sources order, `settings.yaml` lookup, `*_FILE` secrets, interval bounds, `load_settings()`).
  - [x] 3.4 `log.py`: see "Logging design" in Dev Notes (`configure_logging()`, JSON/console renderers, redaction processor).
  - [x] 3.5 `settings.example.yaml` at the repo root documenting every key this story defines (see Dev Notes). `settings.yaml` stays gitignored (already in `.gitignore`).

- [x] **Task 4: Tool configuration in `pyproject.toml` (AC: 2)**
  - [x] 4.1 `[tool.ruff]` / `[tool.ruff.lint]`: `select` includes at least `E`, `F`, `W`, `I`, `UP`, `B`, `DTZ`, `BLE`, `S`, `RUF`; `[tool.ruff.lint.per-file-ignores]` `"tests/**" = ["S101"]` (asserts). Keep ruff defaults otherwise.
  - [x] 4.2 `[tool.ty]`: `[tool.ty.environment] python-version = "3.14"`, `[tool.ty.src] include = ["src", "tests"]`, `[tool.ty.terminal] error-on-warning = true` (all three keys verified in the ty configuration reference).
  - [x] 4.3 `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, `addopts = ["--disable-socket", "--allow-unix-socket", "-m", "not live", "--strict-markers"]`, `markers = ["live: hits the real network; deselected by default, run by source-drift.yml", "valkey: needs a Valkey server"]`. **`--allow-unix-socket` is mandatory**: without it every `@pytest.mark.anyio` test fails with `SocketBlockedError` because the asyncio event loop creates a Unix socketpair (verified with pytest-socket 0.8.1 + anyio 4.15.1). TCP/DNS stay blocked.
  - [x] 4.4 `[tool.importlinter]`: see "Import-linter contracts" in Dev Notes.

- [x] **Task 5: Tests (AC: 2, 3)**
  - [x] 5.1 `tests/conftest.py`: `FakeClock` (constructed with an aware UTC datetime, `now()`, `advance(delta)`, `set(dt)`; rejects naive datetimes with `ValueError`), a `fake_clock` fixture, and an `anyio_backend` fixture returning `"asyncio"`.
  - [x] 5.2 `tests/unit/test_clock.py`: `SystemClock().now()` is aware and UTC; `FakeClock` advance/set; naive datetime rejected.
  - [x] 5.3 `tests/unit/test_settings.py`: defaults (interval 15 min); env override with `__` nesting (`CVESTREAM_SOURCES__EUVD__INTERVAL=PT30M`); interval `PT60M` accepted, `PT61M` and `PT0S` rejected; YAML file values, env beats YAML (deep merge: a YAML `interval` and an env `enabled` combine); `CVESTREAM_SETTINGS_FILE` pointing to a missing file raises a `CvestreamError` subclass; `*_FILE` secret read from a tmp file (trailing newline stripped) into a `SecretStr`; both `X` and `X_FILE` set → error; unknown key in YAML → validation error (`extra="forbid"`). Use `monkeypatch.setenv` / `delenv` and `tmp_path`; never depend on the developer's real env (clear `CVESTREAM_*` in a fixture).
  - [x] 5.4 `tests/unit/test_log.py`: the redaction processor masks a `token` key, `*_token` keys and any `/feeds/<token>.xml` substring in string values; JSON output is one valid JSON object per line with `event`, `level`, `timestamp` ending in `Z`.
  - [x] 5.5 `tests/unit/cli/test_main.py`: `typer.testing.CliRunner` on `app`: `--help` exits 0 and shows usage; `--version` prints the version. Plus one subprocess-free check that `cvestream.__main__` imports `main`.
  - [x] 5.6 `tests/unit/test_errors.py` (tiny): `CvestreamError` is an `Exception`.
  - [x] 5.7 One `@pytest.mark.anyio` smoke test (e.g. in `test_clock.py` or `tests/unit/test_pytest_setup.py`) proving async tests run with sockets disabled, and one test proving TCP is blocked (`socket.create_connection` raises `pytest_socket.SocketBlockedError`).
  - [x] 5.8 `tests/unit/domain/test_purity.py`: AST allow-list test: every module under `src/cvestream/domain/` imports only stdlib (`sys.stdlib_module_names`), `packaging` or `cvestream.domain.*`. Passes trivially today; it is the real allow-list (import-linter can only deny a list, see Dev Notes).

- [x] **Task 6: Container (AC: 4, 5)**
  - [x] 6.1 `Dockerfile` with stages in this order: `builder`, `dev`, `runtime` (runtime **last**, so a plain `docker build .` produces the runtime image). See "Dockerfile design" in Dev Notes.
  - [x] 6.2 `.dockerignore`: `_bmad/`, `_bmad-output/`, `.claude/`, `.agents/`, `.git/`, `.github/`, `.venv/`, `**/__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `data/`, `settings.yaml`, `endpoints.yaml`, `.devcontainer/`. **Do not exclude `README.md`** (`pyproject.toml` declares `readme = "README.md"`; `uv_build` fails without it) nor `src/`, `pyproject.toml`, `uv.lock`, `.python-version`.
  - [x] 6.3 `compose.yml` (dev): one service `app`, `build: {context: ., target: dev}`, `develop.watch`: `sync` `./src` → `/app/src`, `rebuild` on `./uv.lock` (and on `./pyproject.toml`). No Valkey service yet (story 3.1 adds it with `deploy/valkey/users.acl`). No published ports needed yet.
  - [x] 6.4 `.devcontainer/devcontainer.json` pointing to the compose file (see "Dev container" in Dev Notes).

- [x] **Task 7: CI and Dependabot (AC: 6, 7)**
  - [x] 7.1 `.github/workflows/ci.yml`: see "CI design" in Dev Notes. Trigger `pull_request` **without a `branches:` filter** + `push` to `main`.
  - [x] 7.2 `.github/dependabot.yml`: `version: 2`, three `updates` entries (`uv` at `/`, `github-actions` at `/`, `docker` at `/`), each `schedule.interval: weekly`.

- [x] **Task 8: Docs (AC: 8)**
  - [x] 8.1 `README.md`: what cvestream is (2–3 sentences from the PRD), status (pre-release, epic 1 in progress), dev quick start: `uv sync`, `uv run cvestream --help`, the gate commands, `docker compose watch`, devcontainer, `docker build --target runtime`. Link `_bmad-output/planning-artifacts/architecture.md` for design.
  - [x] 8.2 `CLAUDE.md`: points to `_bmad-output/planning-artifacts/architecture.md` (authoritative) and `_bmad-output/project-context.md` (rules), lists the gate commands, and the key MUSTs (uv not poetry; layering via import-linter; `None` = unknown; aware UTC via `Clock`; no `from __future__ import annotations`; all tool config in `pyproject.toml`; one story = one branch = one PR, stacked PRs allowed; Conventional Commits split by concern; docs follow behaviour in the same PR; never log tokens).
  - [x] 8.3 Architecture amendment (same PR, required by "Changing a pattern"): add `.devcontainer/compose.devcontainer.yml` to the project tree in `architecture.md` if you create it (see Dev container note), and record any other deviation from the tree.

- [x] **Task 9: Verify locally before pushing (all ACs)**
  - [x] 9.1 `uv sync --locked && uv run ruff check && uv run ruff format --check && uv run ty check && uv run lint-imports && uv run pytest` all green.
  - [x] 9.2 `uv run cvestream --help` and `uv run python -m cvestream --help` print the root help.
  - [x] 9.3 `docker build --target runtime -t cvestream:test .`; `docker run --rm cvestream:test --help` (exit 0); `docker run --rm --entrypoint id cvestream:test` → `uid=999`; `docker run --rm --entrypoint sh cvestream:test -c 'command -v uv'` prints nothing; `docker inspect cvestream:test --format '{{.Config.ExposedPorts}} {{.Config.Volumes}} {{.Config.User}}'`.
  - [x] 9.4 `docker build --target dev .` succeeds; `docker compose config` is valid. If Docker is unavailable in the dev environment, say so in the Completion Notes and rely on CI's image job; do not claim it was run.

## Dev Notes

### Scope boundaries (read first)

- This story builds **only the skeleton and the quality gate**. No domain model, no config models, no sources, no state, no HTTP server, no CLI subcommands. Later stories own them (1.2 domain, 1.3 config + `config` CLI, 1.4 `http.py` + sources, 1.5 state + `db` CLI, 1.6 daemon + `run`/`poll`/`healthcheck` + `HEALTHCHECK` + `deploy/compose/`).
- `cvestream run` does not exist yet. Therefore: Dockerfile `CMD ["--help"]` (exit 0) instead of the architecture's final `CMD ["run"]`, and **no `HEALTHCHECK`** (story 1.6 adds `cvestream healthcheck` and switches both). Put a one-line comment in the Dockerfile saying so.
- The dev compose `command` runs `watchfiles "cvestream --help" src` until story 1.6 switches it to `watchfiles "cvestream run" src` (the architecture's target). Comment it.
- Settings: define only what this story needs and tests (see table below). Each later story adds its own settings fields.
- Settings docs: `settings.example.yaml` documents every key in this story (comments per key, env var name, duration format). `docs/configuration.md` is created by story 1.3 (its AC); do not create `docs/` here. README links `settings.example.yaml`.

### This epic is delivered as stacked PRs

- Branch `story-1-1-project-skeleton-container-and-ci`, PR base `main`. Stories 1.2–1.6 are PRs stacked on the previous story's branch (see `_bmad-output/implementation-artifacts/epic-1-stack-plan.md`).
- Consequence for CI: `ci.yml` must trigger on **every** `pull_request`, with **no `branches:` filter**, otherwise PRs whose base is a story branch get no CI. Images are pushed **only** on `push` to `main`.

### Versions (verified on PyPI / registries on 2026-10-26)

| Package | Version | Kind | Note |
|---|---|---|---|
| typer | 0.27.3 | runtime | only `cli/` imports it |
| structlog | 26.1.0 | runtime | |
| pydantic | 2.14.1 | runtime | |
| pydantic-settings | 2.16.1 | runtime | install with the `[yaml]` extra (`pyyaml>=6.0.1`, latest 6.0.3) |
| ruff | 0.17.2 | dev | |
| ty | 0.0.92 | dev | beta; mypy 2.4.0 is the documented fallback, do not add it unless ty blocks |
| import-linter | 2.15 | dev | console script `lint-imports` |
| pytest | 9.1.1 | dev | |
| pytest-socket | 0.8.1 | dev | was `<to pin>` in project-context.md; 0.8.1 released 2026-08-19, `pytest>=7` |
| anyio | 4.15.1 | dev | provides the `anyio` pytest plugin (not pytest-asyncio) |
| watchfiles | 1.3.0 | dev | dev loop only |
| uv | 0.13.5 | tool | `uv_build>=0.13.5,<0.14.0` is written by `uv init` |

`uv add` writes `>=X.Y.Z` lower bounds; `uv.lock` holds the exact pins. That is the pinning mechanism. Update `project-context.md`'s `pytest-socket <to pin>` to `0.8.1` in the docs commit.

### Settings design (`src/cvestream/settings.py`)

Fields defined in this story (others are added by the story that uses them):

| Field | Type / default | Owner story |
|---|---|---|
| `log.level` | `Literal["debug","info","warning","error"]`, `"info"` | 1.1 |
| `log.format` | `Literal["json","console"]`, `"json"` | 1.1 |
| `sources.euvd` / `sources.cisa_kev` | `SourceSettings` (`enabled: bool = True`, `interval: timedelta = 15 min`, `gt=timedelta(0)`, `le=timedelta(minutes=60)`) | 1.1 (adapter-specific fields in 1.4; `cert_eu`/`certfr_alerte` in 2.1, generic sources in 2.2) |
| `data_dir`, `state.url` | — | 1.5 |
| `config_provider`, `endpoints_file` | — | 1.3 |
| `valkey.*` (`url`, `password`, `startup_timeout`) | — | 3.1 |
| listeners | — | 1.6 |

- `class Settings(BaseSettings)` with `model_config = SettingsConfigDict(env_prefix="CVESTREAM_", env_nested_delimiter="__", extra="forbid")`. Nested models are plain `BaseModel` with `ConfigDict(extra="forbid")`.
- Override `settings_customise_sources` to return, in priority order: `init_settings`, `env_settings`, the `*_FILE` secrets source, `YamlConfigSettingsSource(settings_cls, yaml_file=<path>)`. Drop `dotenv_settings` and `file_secret_settings` (no `.env`, no secrets dir).
- YAML path: `CVESTREAM_SETTINGS_FILE` if set, else `./settings.yaml`. `YamlConfigSettingsSource` silently ignores a missing file (verified): that is correct for the default path, but **when `CVESTREAM_SETTINGS_FILE` is set explicitly and the file is missing, raise** a `SettingsError(CvestreamError)` naming the path. Put the lookup in a `load_settings(environ: Mapping[str, str] | None = None) -> Settings` function so tests can inject the environment.
- Env values and YAML values deep-merge (verified: YAML `interval` + env `enabled` combine).
- **Durations are ISO 8601** (`PT30M`) in env vars. A bare number string like `"1800"` from an env var is **rejected** by pydantic for a nested `timedelta` (verified). YAML may use ISO 8601 or an integer number of seconds. Document both in `settings.example.yaml`.
- **`*_FILE` secrets:** a custom `PydanticBaseSettingsSource` scans the environment for `CVESTREAM_<PATH>_FILE`, maps `<PATH>` (split on `__`, lower-cased) to a field path, and **only if that field is a `SecretStr`** reads the file (UTF-8, strip one trailing newline) into it. Any other `*_FILE` variable is left alone, so a future plain field such as `endpoints_file` (story 1.3) is never mistaken for a secret. Setting both `CVESTREAM_X` and `CVESTREAM_X_FILE` → `SettingsError`. Unreadable file → `SettingsError` naming the variable and path, **never the content**. No `SecretStr` field exists in 1.1: test the mechanism with a test-only `Settings` subclass declaring one.
- No `datetime.now()` here; settings have no time logic.

### Logging design (`src/cvestream/log.py`)

- `configure_logging(level: str, fmt: Literal["json","console"]) -> None` using `structlog.configure(...)`: processors `merge_contextvars`, `add_log_level`, `TimeStamper(fmt="iso", utc=True)` (gives ISO 8601 with `Z`), the redaction processor, `format_exc_info` (JSON only), then `JSONRenderer()` or `ConsoleRenderer()`. `wrapper_class=structlog.make_filtering_bound_logger(level)`, `logger_factory=structlog.PrintLoggerFactory()` (stdout), `cache_logger_on_first_use=True`.
- Redaction processor `redact_tokens(logger, method_name, event_dict)`: replace values of keys `token` or ending in `_token` with `"[redacted]"`; in every string value, replace `/feeds/<anything>.xml` with `/feeds/[redacted].xml` (regex). Pure function, unit-tested directly.
- Event names follow `<area>.<past-participle>` (`app`, `source`, `pipeline`, `config`, `publish`, `feed`, `http`, `state`). This story emits no events yet; do not invent any.
- Do not wire stdlib `logging` / uvicorn yet (story 1.6).
- Tests: build the processor chain with a `StringIO`-backed `PrintLoggerFactory(file=...)` or call processors directly. `structlog.testing.capture_logs()` bypasses the processor chain, so it cannot test redaction.

### Import-linter contracts (`[tool.importlinter]`)

Verified behaviour of import-linter 2.15:
- A `layers` contract is **broken if a listed layer does not exist**, except layers wrapped in parentheses (optional), which also works inside a `:` sibling group.
- A `protected` contract errors when its protected module (even an external one) or an allowed importer is **not in the graph**. External packages are only in the graph once something imports them, so `protected` cannot be used for libraries not installed yet (httpx, sqlalchemy...).
- A `forbidden` contract requires its **source** modules to exist, but silently skips forbidden modules absent from the graph. So library ownership is written as `forbidden` contracts, which stay valid as libraries arrive.

Configuration to write (adapt names, keep the semantics):

```toml
[tool.importlinter]
root_package = "cvestream"
include_external_packages = true

[[tool.importlinter.contracts]]
name = "Layers (architecture.md: Component boundaries)"
type = "layers"
layers = [
  "cvestream.cli",
  "cvestream.app",
  "cvestream.pipeline",
  "cvestream.config | cvestream.sources | cvestream.state",
  "(cvestream.http) : cvestream.settings : cvestream.log : cvestream.clock : cvestream.errors",
  "cvestream.domain",
]

[[tool.importlinter.contracts]]
name = "domain is pure: stdlib and packaging only"
type = "forbidden"
source_modules = ["cvestream.domain"]
forbidden_modules = [
  # every project module outside domain
  "cvestream.cli", "cvestream.app", "cvestream.pipeline", "cvestream.config", "cvestream.sources",
  "cvestream.state", "cvestream.http", "cvestream.settings", "cvestream.log", "cvestream.clock", "cvestream.errors",
  # every third-party package of the stack except packaging
  "pydantic", "pydantic_settings", "structlog", "typer", "yaml", "httpx", "sqlalchemy", "alembic",
  "valkey", "starlette", "uvicorn", "feedparser", "anyio",
]

# Library ownership, one forbidden contract per library family:
# httpx      -> only cvestream.sources and cvestream.http
# sqlalchemy, alembic -> only cvestream.state
# valkey     -> only cvestream.config.valkey_provider (all of cvestream.config may not, see note)
# starlette, uvicorn -> only cvestream.app
# typer      -> only cvestream.cli
```

- For each ownership rule, `source_modules` = every **existing** top-level project module/package except the owner (`cvestream.domain`, `config`, `sources`, `state`, `pipeline`, `app`, `cli`, `settings`, `log`, `clock`, `errors`, minus the owner). Add `cvestream.http` to the non-owner lists in story 1.4 when it exists. The `valkey` rule cannot target `config.valkey_provider` before it exists: forbid `valkey` everywhere except `cvestream.config` now; story 3.1 tightens it to `cvestream.config.valkey_provider` (add an `ignore_imports` or a second contract then). Leave a comment.
- The layer order puts cross-cutting modules (`settings`, `log`, `clock`, `errors`, `http`) above `domain` and below `config | sources | state`; `config`, `sources`, `state` are independent siblings (`|`). This matches "Component boundaries (allowed imports)" in architecture.md, which is authoritative.
- import-linter can only deny a list, so "packaging is the **only** third-party import in domain" is enforced by the list above plus the AST allow-list test (Task 5.8). Keep both.
- Verified locally on a scratch project: these contract shapes report `KEPT` on the skeleton (including forbidden modules that do not exist yet, such as `cvestream.http`, `valkey`, `feedparser`) and `BROKEN` on a planted violation. Plant one violation yourself once to see the contract fire, then remove it.

### Dockerfile design

Model: Astral's `uv-docker-example` `multistage.Dockerfile`.

```dockerfile
FROM ghcr.io/astral-sh/uv:0.13.5-python3.14-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_NO_DEV=1
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked

FROM builder AS dev
ENV UV_NO_DEV=0 PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
# story 1.6 switches to: watchfiles "cvestream run" src
CMD ["watchfiles", "cvestream --help", "src"]

FROM python:3.14-slim-trixie AS runtime
RUN groupadd --system --gid 999 cvestream \
 && useradd --system --uid 999 --gid 999 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin cvestream \
 && mkdir /data && chown 999:999 /data
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080 9090
USER 999:999
WORKDIR /app
ENTRYPOINT ["cvestream"]
# story 1.6 switches to CMD ["run"] and adds HEALTHCHECK CMD ["cvestream", "healthcheck"]
CMD ["--help"]
```

- Both base tags exist (checked on ghcr.io and Docker Hub on 2026-10-26). Pin the uv image to `0.13.5-python3.14-trixie-slim`, never `latest`. The builder and runtime both put Python at `/usr/local/bin/python3.14`, which the venv symlinks to: they must stay the same Python minor and Debian release (`UV_PYTHON_DOWNLOADS=0` keeps uv from downloading another interpreter).
- `COPY --from=builder /app /app` **without `--chown`**: application files stay root-owned and read-only for UID 999 (the deployment uses a read-only root filesystem anyway). Only `/data` is owned by 999.
- The runtime has no uv: only the `builder`/`dev` stages come from the uv image.
- `uv sync` installs the project in editable mode (a `.pth` pointing at `/app/src`), so the runtime needs `/app/src` and `/app/.venv` (architecture: "runtime target holds only `.venv` + `src`"); copying `/app` is the simplest way, `.dockerignore` keeps the rest out.
- `docker run image` without arguments must not fail: `CMD ["--help"]` exits 0. (`no_args_is_help=True` alone exits 2.)
- Multi-arch: `python:3.14-slim-trixie` and the uv image are multi-arch, so the same Dockerfile builds `linux/amd64` and `linux/arm64`.

### Dev container and compose

- `compose.yml` service `app`: `build: {context: ., target: dev}`, `user: root` is **not** needed (the dev stage inherits root from the uv image). `develop.watch`: `- action: sync, path: ./src, target: /app/src` and `- action: rebuild, path: ./uv.lock` (+ `./pyproject.toml`). Run with `docker compose watch`.
- `.devcontainer/devcontainer.json`: `"name": "cvestream"`, `"dockerComposeFile": ["../compose.yml", "compose.devcontainer.yml"]`, `"service": "app"`, `"workspaceFolder": "/app"`, `"overrideCommand": true`, `"shutdownAction": "stopCompose"`, extensions `charliermarsh.ruff`, `astral-sh.ty`, `ms-python.python`. Compose-based devcontainers ignore `workspaceMount`, so the source must be bind-mounted by compose: `.devcontainer/compose.devcontainer.yml` overrides `app` with `volumes: ["..:/app:cached", "/app/.venv"]` (the anonymous volume keeps the image's Linux venv from being shadowed by a host `.venv`). This extra file is not in the architecture tree: add it there (Task 8.3).
- Valkey is **not** part of the dev stack yet (story 3.1).

### CI design (`.github/workflows/ci.yml`)

```yaml
name: ci
on:
  pull_request:          # no branches: filter (stacked PRs target story branches)
  push:
    branches: [main]
permissions: {}
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  ci:                    # job id/name "ci" = the required status check on main
    runs-on: ubuntu-24.04
    permissions: {contents: read}
    env: {UV_LOCKED: "1"}
    steps: checkout → setup-uv (version "0.13.5", enable-cache: true) → uv sync --locked →
           uv run ruff check → uv run ruff format --check → uv run ty check → uv run lint-imports → uv run pytest
  image:
    needs: ci
    runs-on: ubuntu-24.04
    permissions: {contents: read, packages: write}
    steps: checkout → setup-qemu (main only) → setup-buildx → login-action to ghcr.io with GITHUB_TOKEN (main push only) →
           metadata-action (images: ghcr.io/wixyvir/cvestream; tags: type=edge,branch=main and type=sha) →
           build-push-action (target: runtime; platforms: linux/amd64 on PRs, linux/amd64,linux/arm64 on main;
           push: github.event_name == 'push' && github.ref == 'refs/heads/main'; cache-from/to type=gha)
```

- Push condition is exactly `github.event_name == 'push' && github.ref == 'refs/heads/main'`. PRs build without pushing and without logging in.
- `type=sha` in metadata-action yields `sha-<7 chars>` by default; `type=edge,branch=main` yields `edge`.
- Optional but cheap: on PRs, also `load: true` and run `docker run --rm <tag> --help` as a smoke test (single platform only).
- `actions/checkout` with `persist-credentials: false`.
- `packages: write` on the `image` job is unused on PRs. Acceptable as written; for strict least privilege, split into `image` (PRs, `contents: read`, build only) and `publish` (`if:` main push, `packages: write`). Either is fine; say which in the Completion Notes.
- Pinned Actions (SHA resolved from the tag on 2026-10-26; each is the latest release; write the version in a trailing comment):

| Action | Version | Commit SHA |
|---|---|---|
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| astral-sh/setup-uv | v10.3.2 | `07a047e608db84b3d8c06cd9a7c7dcc9c355c18e` |
| docker/setup-qemu-action | v4.4.0 | `99012661954931238ded8c8b007157a8430204e1` |
| docker/setup-buildx-action | v4.4.1 | `f87e5991a6d7451dcb8d9637bfbc97413f497069` |
| docker/login-action | v4.7.0 | `81c6f93d4bc991e510b342a00b419b3bb4efc5c6` |
| docker/metadata-action | v6.2.0 | `dc802804100637a589fabce1cb79ff13a1411302` |
| docker/build-push-action | v7.4.0 | `c3c9e263c25d99ce0380d002d59b67737d91b0dc` |

  Re-check with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` (dereference annotated tags via `git/tags/<sha>`) if anything looks off. `setup-uv` v10.3.2 inputs used here (`version`, `enable-cache`) exist in its `action.yml`.
- No Valkey service, no schema freshness check, no Kustomize check yet (stories 3.1, 1.3, 3.3 add them).

### Testing standards (this story)

- Layout: `tests/unit/` mirrors `src/cvestream/` (`tests/unit/test_settings.py`, `tests/unit/test_log.py`, `tests/unit/test_clock.py`, `tests/unit/cli/test_main.py`, `tests/unit/domain/test_purity.py`). Add an `__init__.py` to every test directory (`tests/`, `tests/unit/`, `tests/unit/cli/`, `tests/unit/domain/`, ...) so pytest's default `prepend` import mode never collides on identical basenames as the tree grows. Tests get the clock through the `fake_clock` fixture rather than importing from `conftest.py`.
- Network blocked by default; async tests via anyio; no sleeps, no real time in logic tests (`FakeClock`).
- No fixtures from real APIs in this story.

### Project Structure Notes

Files created by this story (all at the paths in architecture.md "Complete Project Directory Structure"):

```
pyproject.toml  uv.lock  .python-version  README.md  CLAUDE.md
.dockerignore  Dockerfile  compose.yml  settings.example.yaml
.devcontainer/devcontainer.json  .devcontainer/compose.devcontainer.yml (new, see variance)
.github/workflows/ci.yml  .github/dependabot.yml
src/cvestream/{__init__,__main__,errors,clock,log,settings}.py
src/cvestream/{domain,config,sources,state,pipeline,app}/__init__.py
src/cvestream/cli/{__init__,main}.py
tests/conftest.py  tests/unit/...
```

Variances, with rationale:
- `.devcontainer/compose.devcontainer.yml`: needed to bind-mount the workspace for a compose-based devcontainer; amend the architecture tree.
- Dockerfile `CMD ["--help"]` and no `HEALTHCHECK`; compose command `cvestream --help`: the `run` and `healthcheck` commands arrive in story 1.6, which restores the architecture's values.
- No `CHANGELOG.md`, `LICENSE`, `.pre-commit-config.yaml` (licence not chosen, M1; pre-commit optional). Not required by this story's ACs.
- `.gitignore` already exists from bootstrapping: keep it, add entries only if needed (`.venv/`, caches, `/data/`, `/settings.yaml`, `/endpoints.yaml` are already there).

### Security notes

- Never commit CA bundles, proxy settings or machine paths. If uv/pip/docker hit TLS errors on a TLS-intercepting proxy, set `SSL_CERT_FILE` / `UV_SYSTEM_CERTS=true` in **your shell only** (`UV_NATIVE_TLS` is deprecated in uv 0.13.5 in favour of `UV_SYSTEM_CERTS`). Never disable TLS verification.
- Runtime container: UID 999, app files root-owned, writable `/data` only. Compose/Kubernetes hardening (read-only root fs, caps dropped) comes in 1.6/3.3.
- Redaction is in place from day one: any later code that logs a token or a feed path is masked.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.1: Project skeleton, container and CI]
- [Source: _bmad-output/planning-artifacts/architecture.md#Selected Starter: `uv init` + Astral's uv-docker-example patterns]
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (image, Delivery workflow, GitHub Actions table, runtime configuration, logging, code quality)
- [Source: _bmad-output/planning-artifacts/architecture.md#Structure Patterns] and [#Architectural Boundaries] (Component boundaries = import-linter source of truth)
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure]
- [Source: _bmad-output/planning-artifacts/architecture.md#Development Workflow Integration]
- [Source: _bmad-output/project-context.md] (Technology Stack & Versions, Testing Rules, Code Quality & Style Rules, Development Workflow Rules)
- [Source: _bmad-output/implementation-artifacts/epic-1-stack-plan.md#3.1 Story 1.1] (manual test steps the reviewer will run)
- External: astral-sh/uv-docker-example (`multistage.Dockerfile`, `compose.yml`); import-linter 2.15 docs (`contract_types/layers.md`, `forbidden.md`, `protected.md`); ty configuration reference; pydantic-settings 2.16 (`YamlConfigSettingsSource`, `settings_customise_sources`).

### Previous story intelligence

None: this is the first story. The repository holds only planning artifacts (`_bmad/`, `_bmad-output/`, `.claude/`, `.agents/`, `.gitignore`). Recent commits are all `docs:` planning commits (project context, epics, sprint status with parallel waves). Conventions they establish: Conventional Commits, one concern per commit, English.

### Latest technical information (checked 2026-10-26)

- uv 0.13.5: `uv init --app --build-backend uv_build` creates the `src/` layout and `[project.scripts] cvestream = "cvestream:main"` (to be changed, Task 1.3). `UV_LOCKED`, `UV_FROZEN`, `UV_NO_DEV`, `UV_COMPILE_BYTECODE` are valid env vars.
- pytest-socket 0.8.1 + anyio 4.15.1: `--allow-unix-socket` required for asyncio tests (verified).
- pydantic 2.14 / pydantic-settings 2.16: nested env timedelta must be ISO 8601 (verified); missing YAML file is ignored (verified); env and YAML deep-merge (verified).
- import-linter 2.15: behaviours listed above (verified).
- Typer 0.27.3: a `Typer` with only a callback still renders a group help (`Usage: cvestream [OPTIONS] COMMAND [ARGS]...`) and exits 0 on `--help`; bare `cvestream` with `no_args_is_help=True` prints help and exits 2 (verified).

## Dev Agent Record

### Agent Model Used

claude-sonnet-5-5

### Debug Log References

- `uv run pytest` after adding `tests/`: `cvestream.__main__` called `main()` at import and aborted collection (`SystemExit: 2`); fixed with an `if __name__ == "__main__":` guard.
- `uv run ruff format` also reformatted BMad tooling and the Python blocks of `architecture.md`; reverted those files and added `extend-exclude` for `_bmad`, `_bmad-output`, `.agents`, `.claude` to `[tool.ruff]`.
- `docker compose -f compose.yml -f .devcontainer/compose.devcontainer.yml config` showed that relative paths in the override resolve from the first compose file, so the bind mount is `.:/app:cached` (not `..`).
- First CI run failed in `tests/unit/cli/test_main.py`: with `GITHUB_ACTIONS` set Rich colors the Typer help and splits `--debug` across ANSI codes; the CLI tests now strip escape sequences (reproduced locally with `GITHUB_ACTIONS=true FORCE_COLOR=1`).
- The first `docker build` failed on the proxy TLS interception while fetching the registry token; after `docker pull` of the two base images the build succeeded with the unchanged Dockerfile. Environment only, nothing committed for it.

### Completion Notes List

- Project initialized with `uv init` (uv 0.13.5); runtime and dev dependencies added with `uv add` at the architecture.md versions (`uv.lock` committed). `authors` dropped from `pyproject.toml`; no licence.
- Cross-cutting modules `errors.py`, `clock.py`, `log.py`, `settings.py` with unit tests; `FakeClock` and `anyio_backend` fixtures in `tests/conftest.py`. 41 tests pass with the network blocked (TCP blocked test, anyio smoke test, domain purity AST allow-list).
- `settings.py`: `load_settings(environ)` injects the environment through a `ContextVar` read by `settings_customise_sources` (the pydantic-settings hook has no access to a caller-provided mapping); a private `_load(settings_cls, environ)` lets tests exercise `*_FILE` secrets with a test-only subclass declaring a `SecretStr`. `extra="forbid"` also rejects a nested typo such as `CVESTREAM_LOG__LEVEL_FILE` (it is a nested key, not a secret variable).
- Deviation from the Logging design: `redact_tokens` runs after `format_exc_info` (and `format_exc_info` is used for the console renderer as well), so tokens inside formatted tracebacks are masked too.
- import-linter: 7 contracts (layers, domain purity, five library ownership rules) all KEPT; a planted `import typer` in `domain` made two contracts BROKEN, then removed.
- Dockerfile: builder / dev / runtime as designed; one deviation: the runtime stage copies only `/app/.venv` and `/app/src` (architecture: "runtime target holds only `.venv` + `src`") instead of the whole `/app`. Verified locally: `--help` exit 0 (also with no arguments), `uid=999`, no `uv`, ports 8080/9090, volume `/data` writable by UID 999, `/app` root-owned, `docker build --target dev` works and has ruff and pytest.
- `.dockerignore` also excludes `.import_linter_cache/`.
- CI: split into `ci` (required check), `image` (pull requests: build + `--help` smoke test, `contents: read`) and `publish` (push to `main`: amd64 + arm64, `edge` + `sha-<short>`, `packages: write`) for strict least privilege. All Action SHAs were re-checked against the tags with `gh api`.
- Architecture amendments in the same PR: `.devcontainer/compose.devcontainer.yml` in the tree, the `ci`/`image`/`publish` jobs, `--allow-unix-socket`; `project-context.md` pins pytest-socket 0.8.1.
- Local gates: `uv sync --locked`, ruff check, ruff format --check, ty check, lint-imports, pytest and `docker build --target runtime` all green. CI result is recorded on the pull request.

### File List

- `.dockerignore` (new)
- `.devcontainer/compose.devcontainer.yml` (new)
- `.devcontainer/devcontainer.json` (new)
- `.github/dependabot.yml` (new)
- `.github/workflows/ci.yml` (new)
- `.python-version` (new)
- `CLAUDE.md` (new)
- `Dockerfile` (new)
- `README.md` (new)
- `_bmad-output/implementation-artifacts/1-1-project-skeleton-container-and-ci.md` (modified)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified)
- `_bmad-output/planning-artifacts/architecture.md` (modified)
- `_bmad-output/project-context.md` (modified)
- `compose.yml` (new)
- `pyproject.toml` (new)
- `settings.example.yaml` (new)
- `src/cvestream/__init__.py`, `__main__.py`, `clock.py`, `errors.py`, `log.py`, `settings.py` (new)
- `src/cvestream/cli/__init__.py`, `src/cvestream/cli/main.py` (new)
- `src/cvestream/{domain,config,sources,state,pipeline,app}/__init__.py` (new)
- `tests/__init__.py`, `tests/conftest.py` (new)
- `tests/unit/__init__.py`, `test_clock.py`, `test_errors.py`, `test_log.py`, `test_pytest_setup.py`, `test_settings.py` (new)
- `tests/unit/cli/__init__.py`, `tests/unit/cli/test_main.py` (new)
- `tests/unit/domain/__init__.py`, `tests/unit/domain/test_purity.py` (new)
- `uv.lock` (new)

### Change Log

- 2026-10-26: Implemented story 1.1 (project skeleton, tooling, cross-cutting modules, container, dev loop, CI, Dependabot, docs). Status set to review.
- 2026-10-26: Fixed CLI help assertions that failed under CI color output.
