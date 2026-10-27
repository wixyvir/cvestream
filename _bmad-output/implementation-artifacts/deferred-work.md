# Deferred work

Findings deferred by code reviews, to pick up in a later story or when they bite.

## Deferred from: code review of 1-1-project-skeleton-container-and-ci.md (2026-10-26)

- **Tokens hidden in non-string log values** (`src/cvestream/log.py`): `redact_tokens` only rewrites strings, dicts and plain containers; an object whose `repr()` carries a token (a URL object, an exception passed as a value, a `?token=` query string) reaches `JSONRenderer`, which falls back to `repr`. Address with the HTTP access log in story 1.6.
- **Private pydantic-settings hooks** (`src/cvestream/settings.py`): `_EnvironSource` overrides `EnvSettingsSource._load_env_vars` and `_YamlSource` overrides `YamlConfigSettingsSource._read_file`. pydantic-settings 2.16 has no public hook for either; a Dependabot upgrade may change them (the settings unit tests would fail first).
- **Editable install in the runtime image** (`Dockerfile`): `UV_COMPILE_BYTECODE` only compiles site-packages, so `src/` is recompiled in memory at every start on a read-only root filesystem. Consider `uv sync --no-editable` when stories 1.6/3.3 harden the image.
- **No `timeout-minutes` on CI jobs** (`.github/workflows/ci.yml`): a hung step runs until GitHub's 6 h default.
- **Stale devcontainer venv** (`.devcontainer/compose.devcontainer.yml`): the anonymous `/app/.venv` volume can survive a container recreate and keep the old venv after a `uv.lock` change; document `docker compose up --renew-anon-volumes` if it happens.
- **Root package outside the ownership contracts** (`pyproject.toml`, `[tool.importlinter]`): `cvestream/__init__.py` and `cvestream/__main__.py` are not source modules of any ownership contract, so they could import `typer` or `httpx` unnoticed. Low risk while they stay one-liners.
- **Unknown top-level `CVESTREAM_*` variables are ignored** (`src/cvestream/settings.py`): pydantic-settings drops a variable that maps to no field (`CVESTREAM_LOG_LEVEL`, single underscore), while unknown nested keys and YAML keys fail with `extra="forbid"`. Review decision on wixyvir/cvestream#1 (option 3): accept them, but log a warning once logging is configured, in story 1.6 (`_start()` in `cli/main.py`, right after `configure_logging`). Allow-list `CVESTREAM_SETTINGS_FILE` and the secret `*_FILE` variables; name the variable, never its value.
