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

## Deferred from: code review of 1-2-vulnerability-model-and-matching-engine.md (2026-10-26)

- ~~**Blank terms match almost everything** (`src/cvestream/domain/matcher.py`): `_pattern(" ")` compiles to `(?<!\w)(?!\w)`, so a whitespace-only exclusion silently blocks every item for its entry and a whitespace-only alias matches every description. The domain trusts the boundary; story 1.3 config models must reject whitespace-only `name`, `aliases[]` and `exclude[]` (strip before the length check). **Resolved by story 1.3:** whitespace-only terms are rejected at the config boundary (`Term` strips, then requires one character).~~
- **CVE 5 placeholder lower bounds and compound ranges** (`src/cvestream/domain/version.py`): `unspecified <8.2.29`, `* <8.2.29`, `>=8.0 <8.3` and `<= *` parse as `None` (recall-safe, labelled `versions_unknown`), and a standalone `0` parses as `[0, 0]`, which a `min_version` drops. None was seen in the 600-record EUVD sample; revisit with recorded fixtures in story 1.4 (unbounded lower bound for `unspecified`/`*`, accept `>=X <Y`).

## Deferred from: code review of 1-3-endpoint-configuration-in-yaml.md (2026-10-26)

- **`settings.yaml` loader leaks a bare `ValueError`** (`src/cvestream/settings.py`, `_YamlSource._read_file`): it catches `yaml.YAMLError` only, but PyYAML's constructors raise `ValueError` for an impossible date (`log: {level: 2026-13-45}`) or a bad tagged scalar (`!!int abc`), and deep nesting raises `RecursionError`. `load_settings()` then crashes with a traceback instead of a `SettingsError`. Story 1.3 fixed the same issue in `config/yaml_provider.py`; reuse its loader (or catch `ValueError`/`RecursionError`) when `settings.py` is next touched.
