# Story 1.3: Endpoint configuration in YAML

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to declare endpoints, product lists, rules and feed tokens in an `endpoints.yaml` validated against a published schema,
so that I can configure the trial without any extra service and other tools can validate configuration against the same contract.

## Acceptance Criteria

1. **Contract models (FR-10, FR-11).** `config/models.py` defines the v1 contract as Pydantic models with `extra="forbid"`: endpoints (slug ID `[a-z0-9-]{1,40}`, `name`, `enabled`, `min_cvss`, `kev_only`, `token`) and product entries (`name`, `aliases[]`, `exclude[]`, `min_version`). They convert to the domain `Endpoint` / `ProductEntry` / `Rules` objects from Story 1.2, and a product list accepts the `php >= 8.2` shorthand (name `php`, minimum version `8.2`).
2. **Published schema.** `cvestream config schema` writes `schemas/config-v1.json`. The file is committed, and CI fails when it is stale.
3. **YAML provider and revision (FR-21).** `config/provider.py` and `config/yaml_provider.py` exist. When the YAML provider loads `endpoints.yaml`, it returns a `ConfigSnapshot` whose revision is the SHA-256 of the file content.
4. **Invalid change (FR-23).** When the file changes to an invalid configuration, `load()` raises a validation error that names the field, so the caller can keep the last valid snapshot and log `config.rejected`.
5. **`config validate`.** `cvestream config validate <file>` exits 0 when the file is valid, and exits 2 with one plain error line on stderr when it is invalid.
6. **Example.** `endpoints.example.yaml` documents every field, contains a token placeholder (never a real token), and is validated in CI.
7. **Docs.** `docs/configuration.md` describes `settings.yaml`, the `CVESTREAM_*` variables and `endpoints.yaml`.

## Tasks / Subtasks

- [ ] **Task 1: `config/models.py`, the v1 contract (AC: 1)**. See "Contract design".
  - [ ] 1.1 Shared annotated types: `Term` (stripped, 1–200 chars), `EndpointId` (slug), `FeedToken`, `Shorthand` (with the grammar regex in its JSON Schema), plus the `min_version` validator built on `domain.version.parse_version`.
  - [ ] 1.2 `ProductEntryConfig`, `EndpointConfig`, `ConfigDocument` (`version: Literal[1]`, `endpoints: list[EndpointConfig]`), all `extra="forbid", strict=True`, every field with a `description=`.
  - [ ] 1.3 Cross-field rules on `ConfigDocument`: unique endpoint IDs and unique tokens. The error message names the field path and must never contain the token value.
  - [ ] 1.4 `EndpointConfig.to_domain() -> Endpoint` (shorthand expanded, `min_version` parsed to `Version`, lists turned into tuples, `Rules(min_cvss, kev_only)`).
- [ ] **Task 2: `config/provider.py` (AC: 3, 4)**. See "Provider design".
  - [ ] 2.1 `ConfigError(CvestreamError)`, `ConfigUnavailableError(ConfigError)`, `ConfigValidationError(ConfigError)` (one-line `str()`, `errors` tuple).
  - [ ] 2.2 `ConfigSnapshot` (frozen, slotted: `revision`, `endpoints`, token map with `repr=False`, `endpoint_for_token()`).
  - [ ] 2.3 `ConfigProvider` protocol (`revision() -> str`, `load() -> ConfigSnapshot`).
  - [ ] 2.4 `build_snapshot(data, *, revision, source) -> ConfigSnapshot`: validates with `ConfigDocument`, formats the Pydantic errors into field paths, converts to domain objects. Story 3.1 reuses it for Valkey.
- [ ] **Task 3: `config/yaml_provider.py` (AC: 3, 4)**. See "YAML loading rules".
  - [ ] 3.1 `YamlConfigProvider(path: Path)` with `revision()` and `load()`. Read the bytes once, hash those bytes and parse those same bytes.
  - [ ] 3.2 Safe YAML loading with a duplicate-key check, without `yaml.load` (ruff `S506`). YAML errors become `ConfigValidationError` with `line:column` and the problem text, never the source snippet.
- [ ] **Task 4: `config/schema.py` (AC: 2)**
  - [ ] 4.1 `config_schema() -> dict[str, Any]` (`$schema` 2020-12 + `ConfigDocument.model_json_schema()`) and `render_schema() -> str` (deterministic JSON, `indent=2`, trailing newline).
- [ ] **Task 5: CLI `cvestream config` (AC: 2, 5)**. See "CLI design".
  - [ ] 5.1 `cli/config.py`: Typer sub-app with `validate [FILE]` and `schema [--output PATH] [--check]`.
  - [ ] 5.2 `cli/main.py`: register it with `app.add_typer(..., name="config")`. Change nothing else in that file.
- [ ] **Task 6: Settings (AC: 7)**
  - [ ] 6.1 `settings.py`: add `config_provider: Literal["yaml"] = "yaml"` and `endpoints_file: Path = Path("endpoints.yaml")` to `Settings`. Add no other field.
  - [ ] 6.2 `settings.example.yaml`: document both keys (env names `CVESTREAM_CONFIG_PROVIDER`, `CVESTREAM_ENDPOINTS_FILE`).
- [ ] **Task 7: Generated and example files (AC: 2, 6)**
  - [ ] 7.1 Run `uv run cvestream config schema` and commit `schemas/config-v1.json`.
  - [ ] 7.2 Write `endpoints.example.yaml` (see "Example file"). It must pass `cvestream config validate`.
- [ ] **Task 8: CI (AC: 2, 6)**
  - [ ] 8.1 In `.github/workflows/ci.yml`, job `ci`, after `uv run pytest`: add `uv run cvestream config schema --check` and `uv run cvestream config validate endpoints.example.yaml`. Leave the triggers, permissions and other jobs as they are (see "Delivery").
- [ ] **Task 9: Tests (all ACs)**. See "Testing requirements".
  - [ ] 9.1 `tests/unit/config/__init__.py`, `test_models.py`, `test_provider.py`, `test_yaml_provider.py`, `test_schema.py`.
  - [ ] 9.2 `tests/unit/cli/test_config.py`.
  - [ ] 9.3 `tests/unit/test_settings.py`: new fields, defaults, `CVESTREAM_ENDPOINTS_FILE` from the environment (not mistaken for a secret), `config_provider: valkey` rejected.
  - [ ] 9.4 `pyproject.toml`: add `per-file-ignores` entries (`S105`, `S106`) for the new test files that hold fake tokens, following the existing `tests/unit/test_log.py` entry. Change nothing else in `pyproject.toml`.
- [ ] **Task 10: Docs (AC: 7, same PR)**
  - [ ] 10.1 New `docs/configuration.md` (see "Docs content").
  - [ ] 10.2 `README.md`: status line, a link to `docs/configuration.md`, and `cvestream config validate` in the quick start.
  - [ ] 10.3 `CLAUDE.md`: add `uv run cvestream config schema --check` to the gate commands, and one key rule: "`config/models.py` is a public contract: regenerate `schemas/config-v1.json` (`uv run cvestream config schema`) in the same commit".
  - [ ] 10.4 `architecture.md`: in the tree, set the `provider.py` comment to `ConfigProvider protocol, ConfigSnapshot, ConfigError family, build_snapshot()`. Under Data Architecture "Schema", add one sentence on the YAML document shape (`version: 1`, `endpoints: [...]` with an inline `token`, product shorthand `name >= version`).
  - [ ] 10.5 `deferred-work.md`: mark the 1.2 item "Blank terms match almost everything" as resolved by story 1.3 (whitespace-only terms are rejected at the config boundary).
- [ ] **Task 11: Gates**
  - [ ] 11.1 `uv sync --locked && uv run ruff check && uv run ruff format --check && uv run ty check && uv run lint-imports && uv run pytest && uv run cvestream config schema --check && uv run cvestream config validate endpoints.example.yaml`: all green.
  - [ ] 11.2 Manual checks from `epic-1-stack-plan.md` §3.3: exit code 2 and one stderr line for an unknown field, `My_Team`, and `min_cvss: "high"`. Schema freshness: edit a `description=` without regenerating, check that `--check` fails, then revert.
  - [ ] 11.3 `docker build --target runtime -t cvestream:local .` still succeeds. The image is unchanged by this story, but the gate requires the build. If Docker is unavailable, write that in the Completion Notes and do not claim the build ran.

## Dev Notes

### Scope boundaries (read first)

- **In scope:** the v1 contract models, the provider protocol and snapshot, the YAML provider, JSON Schema export, `cvestream config validate|schema`, two settings fields, the example file, the CI checks and the docs.
- **Not in this story:**
  - Logging `config.rejected` / `config.unavailable`, and the "keep the last valid snapshot" loop. This story only raises a precise error. The pipeline run (1.6) catches `ConfigError`, keeps the previous snapshot and logs. Do not configure or call structlog here.
  - The daemon, hot reload wiring and the feed URL / token → feed lookup over HTTP (1.6).
  - The Valkey provider and `config/keys.py` (3.1).
  - `config export|import`, endpoint/product/token CRUD commands and token generation (3.2).
  - A general CLI "unexpected error → exit 1, traceback only with `--debug`" handler (3.2). Typer 0.27.3 already defaults to `pretty_exceptions_show_locals=False`, so a crash never prints a token held in a local variable. Do not change that.
- **No new dependency.** `pydantic` 2.14.1, `pyyaml` 6.0.3 (via `pydantic-settings[yaml]`, which `settings.py` already relies on for `import yaml`), `packaging` 26.3 and `typer` 0.27.3 are already locked. Do not run `uv add`: `pyproject.toml` / `uv.lock` are shared with the parallel story 1.4 and every extra hunk is a rebase conflict.
- `domain` stays untouched. Use `ProductEntry`, `Rules`, `Endpoint` (`domain/endpoint.py`) and `parse_version` (`domain/version.py`) exactly as they are.

### Contract design (`config/models.py`)

The models are the single source of truth for the YAML file, the JSON Schema, and later the Valkey provider (3.1) and the CLI (3.2). Every field has a `description=`: it is what third-party tools read in `schemas/config-v1.json`.

**YAML document (v1):**

```yaml
version: 1                      # required, must be the integer 1
endpoints:                      # list, may be empty
  - id: team-php                # slug ^[a-z0-9-]{1,40}$, unique
    name: PHP team              # required, 1–200 chars after strip
    enabled: true               # default true
    min_cvss: 7.0               # number 0–10 or omitted/null (no threshold)
    kev_only: false             # default false
    token: "<43-char token>"    # ^[A-Za-z0-9_-]{32,128}$, unique across endpoints
    products:                   # list, may be empty; each item is a shorthand string or an object
      - php >= 8.2
      - name: gitlab
        aliases: [gitlab-ce, gitlab-ee]
        exclude: [gitlab-runner]
        min_version: "17.0"     # quoted string, valid for domain.version.parse_version
```

**Models** (pseudo-code; names are binding because 3.1 and 3.2 import them):

```python
_CONFIG = ConfigDict(extra="forbid", strict=True)

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
EndpointId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,40}$")]

class ProductEntryConfig(BaseModel):     # model_config = _CONFIG
    name: Term
    aliases: list[Term] = []
    exclude: list[Term] = []
    min_version: MinVersion | None = None   # str in the contract, parsed in to_domain

ProductItem = Annotated[
    Annotated[Shorthand, Tag("shorthand")] | Annotated[ProductEntryConfig, Tag("entry")],
    Discriminator(lambda v: "shorthand" if isinstance(v, str) else "entry"),
]

class EndpointConfig(BaseModel):
    id: EndpointId
    name: Term
    enabled: bool = True
    min_cvss: Annotated[float, Field(ge=0, le=10)] | None = None
    kev_only: bool = False
    token: FeedToken = Field(repr=False)
    products: list[ProductItem] = []
    def to_domain(self) -> Endpoint: ...

class ConfigDocument(BaseModel):
    version: Literal[1]
    endpoints: list[EndpointConfig] = []
    # model_validator(mode="after"): unique ids, unique tokens
```

Verified behaviour on pydantic 2.14.1 (probed on this branch on 2026-10-26):

- **`strict=True`** keeps the contract predictable for both YAML and the JSON that Valkey connectors will write: `min_cvss: "high"` and `min_cvss: "7.0"` give `Input should be a valid number`, `kev_only: "yes"` (a quoted string) gives `Input should be a valid boolean`, `version: "1"` gives `Input should be 1`, and an int is still accepted for a float (`min_cvss: 7` → `7.0`). Unquoted YAML `yes`/`no` are YAML booleans and are therefore accepted.
- **`Term` with `strip_whitespace=True, min_length=1`** rejects `""` and whitespace-only names, aliases and exclusion terms. This closes the 1.2 deferred item: in the matcher, a blank term compiles to `(?<!\w)(?!\w)` and matches everything. Stripped values are what reach the domain.
- **Product shorthand.** Grammar (ECMA-compatible, no named groups, so third-party JSON Schema validators accept it). It is used both as the schema `pattern` and, compiled with `re`, by the parser:
  ```python
  SHORTHAND_PATTERN = r"^\s*([^<>=\s](?:[^<>=]*[^<>=\s])?)(?:\s*>=\s*([^<>=\s]+))?\s*$"
  ```
  Verified: `php >= 8.2` → (`php`, `8.2`); `php>=8.2` → same; `"  php  "` → (`php`, None); `apache http server >= 2.4` → (`apache http server`, `2.4`); `node.js >= v20.1.0-rc.1` OK. Rejected: `php > 8.2`, `>= 8.2`, `php >= `, `php >= 8.2 extra`, `php == 8`, `php=8`. Only `>=` exists (FR-11: a *minimum* version). Implement `Shorthand` as `Annotated[str, StringConstraints(max_length=200), AfterValidator(_check_shorthand), WithJsonSchema({"type": "string", "maxLength": 200, "pattern": SHORTHAND_PATTERN, "description": ...})]`, so that the error reads `expected "<name>" or "<name> >= <version>"` instead of a raw regex. `_check_shorthand` also validates the version part with `parse_version`. Keep the string as-is in the model (3.2's export round-trips it) and expand it in `to_domain()`.
- **Callable `Discriminator` + `Tag`** (not a plain `str | ProductEntryConfig` union). With a plain union, a bad object reports an error for *both* branches, and the first one (`Input should be a valid string`) is misleading. With the discriminator, only the chosen branch reports: `('products', 1, 'entry', 'unknown') Extra inputs are not permitted`. The JSON Schema becomes `items: {oneOf: [<shorthand string>, {$ref: ProductEntryConfig}]}`. The error formatter must drop the tag segments (`"shorthand"`, `"entry"`) from `loc`.
- **`min_version` must be a quoted string.** YAML 1.1 reads `min_version: 8.10` as the float `8.1` (verified with PyYAML 6.0.3). Add a `field_validator("min_version", mode="before")` that rejects `int`/`float` (but not `bool`, which strict mode already rejects) with `must be a quoted string (YAML reads 8.10 as the number 8.1)`. Then an `AfterValidator` checks `parse_version(value) is not None` and fails with `invalid version '<value>'`. Pydantic prefixes `ValueError` messages with `Value error, `: the formatter strips that prefix.
- **Token (`FeedToken`).** `Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{32,128}$")]` with `Field(repr=False)`. The URL-safe base64 alphabet is what `secrets.token_urlsafe(32)` produces (43 chars). The pattern has no `.`, matching `log.py`'s assumption that "feed tokens never contain a dot". A plain `str` (not `SecretStr`) keeps the pattern visible in the published schema. The `repr=False` and the error-formatting rules below keep the value out of output. Ruff `S105` flags assignments to names containing `token` (e.g. `TOKEN_PATTERN = r"..."`): name the constant `FEED_KEY_PATTERN`, or add a `# noqa: S105` with a reason.
- **Uniqueness** (`model_validator(mode="after")` on `ConfigDocument`): a duplicate `id` → `endpoints[3].id: duplicate endpoint id 'team-php'`. A duplicate token → `endpoints[3].token: same token as endpoint 'team-php'` (**never print the token**). The `loc` of a model-level error is `()`, so the path goes in the message.
- **`to_domain()`**: `Endpoint(id=..., name=..., products=tuple(entries), rules=Rules(min_cvss=..., kev_only=...), enabled=...)`. Each item becomes `ProductEntry(name, aliases=tuple(...), exclude=tuple(...), min_version=parse_version(v) if v else None)`. Shorthand: `name`, `min_version` only. No token in the domain object (1.2 design: no token in `domain`).
- No `min_length` on `products`: an endpoint with no products is valid and matches nothing (the operator may add products later through the 3.2 CLI). No `multiple_of=0.1` on `min_cvss` (float imprecision rejects `7.3`).
- Class names in `$defs` are the Python class names (`EndpointConfig`, `ProductEntryConfig`). Give `ConfigDocument` `title="cvestream configuration v1"` through `ConfigDict(title=...)`. Expose `SCHEMA_VERSION = 1`.

### Provider design (`config/provider.py`)

```python
class ConfigError(CvestreamError): ...
class ConfigUnavailableError(ConfigError): ...   # file missing/unreadable (3.1: Valkey down)
class ConfigValidationError(ConfigError):
    def __init__(self, source: str, errors: Sequence[str]) -> None: ...
    # .source, .errors: tuple[str, ...]
    # str(): f"{source}: {errors[0]}" + (f" (+{n} more)" if n else ""), always ONE line

@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    revision: str
    endpoints: tuple[Endpoint, ...]
    tokens: Mapping[str, str] = field(repr=False)   # token -> endpoint id, MappingProxyType
    def endpoint_for_token(self, token: str) -> Endpoint | None: ...

class ConfigProvider(Protocol):
    def revision(self) -> str: ...      # cheap change check before each pipeline run
    def load(self) -> ConfigSnapshot: ...

def build_snapshot(data: object, *, revision: str, source: str) -> ConfigSnapshot: ...
```

- **Sync, not async**: the pipeline that calls it is synchronous (architecture "Async and sync"). 3.1's Valkey provider uses the sync valkey-py client.
- `build_snapshot`: `ConfigDocument.model_validate(data)`. On `ValidationError`, raise `ConfigValidationError(source, [format(e) for e in exc.errors(include_input=False, include_url=False)]) from None`.
  - **Never use `str(validation_error)`**: it embeds `input_value=`, which can be a token.
  - **`from None`** keeps the Pydantic error, and its inputs, out of the traceback that 1.6 will log with `exc_info`.
  - Field path format: strings joined with `.`, ints as `[i]`, tag segments dropped. Examples: `endpoints[0].products[1].min_version: invalid version '8.x'`, `endpoints[0].unknown_field: Extra inputs are not permitted`, `version: Input should be 1`. An empty `loc` gives just the message. Strip the `Value error, ` prefix.
- The snapshot keeps **every** endpoint, disabled ones included (`Endpoint.enabled`), and maps every token. Whether a disabled endpoint's feed returns `404` is decided in 1.6. Swapping a snapshot means replacing the reference; never mutate one (architecture "Immutability").
- `repr(snapshot)` must not contain any token (test it). `endpoint_for_token` is a plain dict lookup. Constant-time comparison is unnecessary here because the token is a dict key, which is the architecture's design.

### YAML loading rules (`config/yaml_provider.py`)

```python
class YamlConfigProvider:
    def __init__(self, path: Path) -> None: ...
    def revision(self) -> str: ...        # sha256(file bytes).hexdigest()
    def load(self) -> ConfigSnapshot: ... # read bytes ONCE → revision + parse from the same bytes
```

- Missing file, directory, or permission error (`OSError`) → `ConfigUnavailableError(f"{path}: cannot read: {type(exc).__name__}")` `from None`. An editor's atomic save can make the file briefly vanish: 1.6 then keeps the last snapshot.
- Non-UTF-8 bytes (`UnicodeDecodeError`), YAML syntax errors, duplicate keys, a top level that is not a mapping (an empty file loads as `None`) → `ConfigValidationError`.
- **Loader:** subclass `yaml.SafeLoader` and override `construct_mapping` to raise `yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)` on a repeated key. By default PyYAML silently keeps the last value (verified: `a: 1\na: 2` → `{'a': 2}`), so a duplicated `min_cvss` would silently win. Use the loader without `yaml.load`, which ruff flags as `S506` even with a `SafeLoader` subclass (verified):
  ```python
  loader = _UniqueKeyLoader(text)
  try:
      data = loader.get_single_data()
  finally:
      loader.dispose()
  ```
- **YAML error message:** `f"{line}:{column}: {problem}"` from `exc.problem_mark` (0-based, so add 1) and `exc.problem`. **Never `str(exc)`**: PyYAML's message includes a snippet of the source line, which may be a `token:` line. Example: `endpoints.yaml: 2:6: mapping values are not allowed here`. Fall back to the class name when there is no mark.
- The `source` used in messages is `str(path)` as given (relative stays relative).
- Known PyYAML (YAML 1.1) quirks, documented in `docs/configuration.md` and handled by the strict models: `8.10` → float (quote versions); `yes/no/on/off` → booleans; `2026-10-26` → date; unquoted all-digit IDs → int (rejected by strict `str`: quote them).

### CLI design (`cli/config.py`)

- `config_app = typer.Typer(name="config", no_args_is_help=True, help="Configuration contract: validate files and export the JSON Schema.")`, registered in `cli/main.py` with `app.add_typer(config_app)`. Commands are kebab-case nouns then verbs, matching the architecture CLI list (`config validate|schema|export|import`; `export|import` come in 3.2).
- `cvestream config validate [FILE]`:
  - `FILE` is optional. When it is omitted, use `load_settings().endpoints_file`. A `SettingsError` is a user error.
  - Runs `YamlConfigProvider(path).load()`: exactly the daemon's code path, never a separate check.
  - Valid: one line on stdout, `f"{path}: valid ({n} endpoints)"`, exit 0. Never print tokens or the revision in full.
  - `ConfigError` / `SettingsError`: `typer.echo(str(exc), err=True)` then `raise typer.Exit(code=2)`. That gives one line and no traceback.
- `cvestream config schema [--output PATH] [--check]`:
  - Default output: `schemas/config-v1.json` (relative to the current directory, where CI and developers run it). `--output -` writes to stdout. Create the parent directory. Write UTF-8 with `\n` line endings.
  - `--check`: write nothing. If the file is missing or differs from `render_schema()`, print `f"{path} is stale; run: cvestream config schema"` to stderr and exit 2. Otherwise exit 0. Prefer this to `git diff --exit-code`, which misses an untracked new file.
  - An `OSError` while writing → one stderr line, exit 2.
- Verified with Typer 0.27.3 / Click 8.5.0: `typer.echo(..., err=True)` + `typer.Exit(code=2)` gives `CliRunner().invoke(...)` → `exit_code == 2`, `result.stderr == "<line>\n"`, `result.stdout == ""`. `result.stderr` is captured separately by default. Strip ANSI codes as `tests/unit/cli/test_main.py` does (`_plain`) for any help-text assertion: Rich colours help under `GITHUB_ACTIONS`.

### Schema export (`config/schema.py`)

```python
SCHEMA_PATH = Path("schemas/config-v1.json")
def config_schema() -> dict[str, Any]:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **ConfigDocument.model_json_schema()}
def render_schema() -> str:
    return json.dumps(config_schema(), indent=2, ensure_ascii=False) + "\n"
```

- Pydantic 2.14 emits Draft 2020-12, with `additionalProperties: false` for `extra="forbid"`, `const: 1` for `Literal[1]` and `oneOf` for the discriminated product items (verified). Keep `sort_keys=False`: the output is deterministic as is, and field order follows the model, which reads better.
- `schemas/config-v1.json` is the only generated file committed in the repository (architecture "File Organization Patterns"). Regenerate it in the same commit as any `models.py` change.

### Settings (`settings.py`)

- Add to `Settings`, after `sources`:
  - `config_provider: Literal["yaml"] = "yaml"`. Story 3.1 widens it to `Literal["yaml", "valkey"]`. Accepting `valkey` before that exists would be a lie.
  - `endpoints_file: Path = Path("endpoints.yaml")`, relative to the working directory, like `settings.yaml`.
- `CVESTREAM_ENDPOINTS_FILE` ends in `_FILE` but is **not** a secret. `_secret_file_path` returns `None` unless the field is a `SecretStr`, so the env source maps it to `endpoints_file`. Story 1.1 designed it for exactly this field. Add a test that proves it.
- Do not touch `SourcesSettings` / `SourceSettings`: story 1.4 extends them in parallel.

### Example file (`endpoints.example.yaml`)

- Header comment: copy to `endpoints.yaml` (gitignored), generate each token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`, validate with `uv run cvestream config validate endpoints.yaml`, schema in `schemas/config-v1.json`, and changes apply at the next poll cycle once the daemon exists (1.6).
- One comment per field (type, default, constraints), as `settings.example.yaml` does.
- At least two endpoints, so that both product forms and both rules show:
  - one with shorthand items (`php >= 8.2`, `nodejs`) and `min_cvss`;
  - one with full objects (aliases, `exclude`, quoted `min_version`), `kev_only: true` and `enabled: false`.
- Token placeholders must match the token pattern and be distinct, and must look like placeholders, e.g. `REPLACE-ME-with-secrets-token-urlsafe-32-team-php` (49 chars). Never commit a real token.

### Docs content (`docs/configuration.md`)

Three sections, plain and short (user-facing; the README links it):

1. **`settings.yaml`**: lookup (`CVESTREAM_SETTINGS_FILE`, default `./settings.yaml`, a missing default is fine), every current key (`log.level`, `log.format`, `sources.<name>.enabled|interval`, `config_provider`, `endpoints_file`) with defaults, and ISO 8601 durations (env) vs seconds (YAML). Point to `settings.example.yaml` as the annotated reference instead of duplicating every comment.
2. **`CVESTREAM_*` variables**: prefix, `__` nesting (`CVESTREAM_SOURCES__EUVD__INTERVAL=PT30M`), precedence (env > secret files > YAML), `*_FILE` secrets for `SecretStr` fields only (none yet; Valkey password in 3.1). `CVESTREAM_ENDPOINTS_FILE` is a plain path, not a secret.
3. **`endpoints.yaml`**: document shape, every field, the shorthand grammar, quoting rules (versions, numeric IDs), token generation and the warning that the token *is* the access control (anyone with the URL reads the feed), `cvestream config validate` (exit codes 0/2), `schemas/config-v1.json` for other tools' CI, the revision (SHA-256 of the file: any byte change, comments included, triggers a reload), and what happens with an invalid edit (the daemon keeps the last valid configuration and logs `config.rejected`, from 1.6).

### Testing requirements

- Layout: `tests/unit/config/` (new, with `__init__.py`; every test directory has one to avoid basename collisions) mirrors `src/cvestream/config/`. CLI tests go in `tests/unit/cli/test_config.py`. Write the YAML files with `tmp_path`. Hand-written config files are fine: the "recorded fixtures" rule applies to source payloads only.
- Fake tokens: build them as `"a" * 43` or `secrets.token_urlsafe(32)` (no network). Add `S105`/`S106` per-file ignores for the new test files, as for `tests/unit/test_log.py`.
- Use `pytest.mark.parametrize` with `ids=` for the rejection tables.
- **Required cases** (the reviewer checks them; see `epic-1-stack-plan.md` §3.3):
  - models, valid: minimal document (`version: 1`, `endpoints: []`); full endpoint; defaults (`enabled` true, `kev_only` false, `min_cvss` None, empty lists); shorthand `php >= 8.2` → `ProductEntry(name="php", min_version=Version("8.2"))`; `php>=8.2`; bare `php`; a multi-word name; full object with aliases/exclude/`min_version: "17.0"`; stripped terms (`"  php "` → `"php"`); `min_cvss: 7` (int) → `7.0`; `to_domain()` gives tuples, `Rules`, `enabled`, and no token attribute.
  - models, rejected (each asserts the field path in the message): unknown top-level key; unknown endpoint key (`unknown_field`); unknown product key; missing `version`; `version: 2` and `"1"`; bad slug (`My_Team`, `team_php`, 41 chars, `""`); `min_cvss: "high"`, `"7.0"`, `-0.1`, `10.1`; `kev_only: "yes"`; blank `name` / alias / exclude (`"  "`); unquoted float `min_version` (the message mentions quoting); invalid `min_version` (`8.x`); bad shorthand (`php > 8.2`, `>= 8.2`, `php >= 8.x`); a product item that is a number or a list; token too short (31 chars), too long (129), containing `.` or `/`; duplicate ids; duplicate tokens (the message must **not** contain the token).
  - provider: `ConfigValidationError.__str__` is one line with `(+N more)`; tag segments dropped from paths; `Value error, ` stripped; `build_snapshot` maps tokens → endpoint ids; `endpoint_for_token` hit/miss; disabled endpoints kept; `repr(snapshot)` and `repr(EndpointConfig)` contain no token; the `ConfigValidationError` has `__cause__ is None` and `__suppress_context__` set (raised `from None`); every exception subclasses `CvestreamError` and ends in `Error`.
  - yaml provider: same bytes → same revision, equal to `hashlib.sha256(content).hexdigest()`; a one-byte change (a comment) → different revision; `load().revision == revision()`; valid file → valid change → new snapshot; valid → invalid change → `ConfigValidationError` naming the field, while the earlier snapshot object is unchanged (the caller keeps it); missing file and a directory → `ConfigUnavailableError`; empty file and a top-level list → "top level must be a mapping"; non-UTF-8 bytes; YAML syntax error → `line:column` in the message and **no source text** (put a token on the offending line and assert it is absent); duplicate key at any depth → error with its line; `min_version: 8.10` unquoted → the quoting message.
  - schema: `render_schema()` equals the committed `schemas/config-v1.json` (read it from the repository root, `Path(__file__).parents[3]`). This is the pytest-level staleness guard on top of the CI step. Also check that it is deterministic (two calls give the same text), that it has `$schema` 2020-12, and that the shorthand pattern in the schema equals `SHORTHAND_PATTERN`.
  - example: `endpoints.example.yaml` loads through `YamlConfigProvider` (repository root path).
  - CLI: `config validate` on a valid file → 0 and a stdout line; on an unknown field / `My_Team` / `min_cvss: "high"` → 2 and exactly one stderr line, with no `Traceback`; a missing file → 2; no `FILE` → uses `CVESTREAM_ENDPOINTS_FILE` (use `monkeypatch.setenv` plus `monkeypatch.chdir(tmp_path)` so the developer's own `settings.yaml` is never read, as `tests/unit/test_settings.py` does); `config schema --output <tmp>` writes `render_schema()`; `--output -` prints it; `--check` → 0 when fresh, 2 when stale or missing; `cvestream config` with no arguments shows help.
  - settings: defaults; `CVESTREAM_ENDPOINTS_FILE=/x/e.yaml` → `Path("/x/e.yaml")`; `config_provider: valkey` → `ValidationError`.
- Network stays blocked (pytest-socket). No `live` or `valkey` marker in this story.

### Architecture compliance

- **Layering** (import-linter is the gate): `config` imports `cvestream.domain`, `cvestream.errors`, `pydantic`, `yaml`, `packaging` (for the `Version` type only; parsing goes through `domain.version.parse_version`). It never imports `settings`, `sources`, `state`, `pipeline`, `app` or `cli`. `cli/config.py` may import `config` and `settings`. Do not edit `[tool.importlinter]`.
- **Validation only at the boundary**: `config` validates everything the domain trusts (non-blank terms, parsed versions, slug IDs, scores 0–10). The domain never re-validates.
- **Errors**: every new exception inherits from `CvestreamError`, its name ends in `Error`, and it lives with its layer (`config/provider.py`, like `SettingsError` lives in `settings.py`). No bare `except`, no `except Exception` (this is not a supervisor).
- **No time, no randomness, no logging** in this story: the revision is a content hash and tokens are operator-supplied.
- **Security**: never print, log or embed a token in an exception message or a `repr()`. `yaml` only through the safe loader subclass. Unknown token → `None` from `endpoint_for_token`, so 1.6 can answer `404`.
- Modules are singular nouns at the architecture paths (`config/models.py`, `provider.py`, `yaml_provider.py`, `schema.py`, `cli/config.py`). No `utils.py`. `config/__init__.py` keeps only its docstring.
- Fully annotated public functions; `ty check` runs with `error-on-warning = true`. No `from __future__ import annotations`.

### Library / framework requirements

| Package | Locked version | Use in this story |
|---|---|---|
| pydantic | 2.14.1 | contract models, `Discriminator`/`Tag`, `WithJsonSchema`, `model_json_schema()` (Draft 2020-12) |
| pyyaml | 6.0.3 (through `pydantic-settings[yaml]`) | `yaml.SafeLoader` subclass, `MarkedYAMLError.problem` / `.problem_mark` |
| packaging | 26.3 | `Version` type in `ProductEntry` (parsing via `domain.version.parse_version`) |
| typer | 0.27.3 (Click 8.5.0) | `config` sub-app, `typer.Exit(code=2)`, `CliRunner` with separate `stderr` |
| stdlib | Python 3.14 | `hashlib.sha256`, `json`, `re`, `types.MappingProxyType`, `typing.Protocol` |

No new dependency, so no `pyproject.toml` `dependencies` / `uv.lock` change.

### File structure requirements

New:

```
src/cvestream/config/models.py
src/cvestream/config/provider.py
src/cvestream/config/yaml_provider.py
src/cvestream/config/schema.py
src/cvestream/cli/config.py
schemas/config-v1.json                      (generated, committed)
endpoints.example.yaml
docs/configuration.md
tests/unit/config/__init__.py
tests/unit/config/test_models.py
tests/unit/config/test_provider.py
tests/unit/config/test_yaml_provider.py
tests/unit/config/test_schema.py
tests/unit/cli/test_config.py
```

Modified: `src/cvestream/settings.py`, `src/cvestream/cli/main.py`, `settings.example.yaml`, `.github/workflows/ci.yml`, `pyproject.toml` (per-file ignores only), `tests/unit/test_settings.py`, `README.md`, `CLAUDE.md`, `_bmad-output/planning-artifacts/architecture.md`, `_bmad-output/implementation-artifacts/deferred-work.md`, this story file, `sprint-status.yaml`.

### Existing files this story touches

- `src/cvestream/settings.py`: today it holds `Settings` (`log`, `sources`), env/secret/YAML sources and `load_settings()`. Change: two fields on `Settings`. Preserve: source order and the `*_FILE` logic, untouched.
- `src/cvestream/cli/main.py`: today it holds the root Typer app with `--debug` / `--version` and `CliState`. Change: one import and one `add_typer` line. Preserve: `no_args_is_help`, `add_completion=False`, the callback, `main()`. `tests/unit/cli/test_main.py` must stay green unchanged.
- `.github/workflows/ci.yml`: today `on: pull_request` (no `branches:` filter, on purpose, for stacked PRs) plus `push: branches: [main]`, a `ci` job, an `image` job on PRs (no push) and `publish` on push to `main`. Change: two `run:` steps at the end of the `ci` job. Preserve: triggers, `permissions: {}`, SHA-pinned actions, `UV_LOCKED`.
- `settings.example.yaml`: today `log` and `sources` with per-key comments. Change: add `config_provider` and `endpoints_file` near the top, away from the `sources:` block that 1.4 edits.
- `README.md`, `CLAUDE.md`: see Task 10. Keep both short.
- `pyproject.toml`: only `[tool.ruff.lint.per-file-ignores]` entries.

### Delivery (stacked PR, parallel wave)

- Branch `story-1-3-endpoint-configuration-in-yaml`, PR base `story-1-2-vulnerability-model-and-matching-engine` (stack plan `_bmad-output/implementation-artifacts/epic-1-stack-plan.md`).
- `ci.yml` must keep triggering on `pull_request` **without** a `branches:` filter (stacked PRs target story branches, not `main`), and must push images only on push to `main`. This story only adds steps to the `ci` job.
- Story 1.4 is developed **in parallel** from the same 1.2 base and will be rebased onto this branch. Keep the shared files minimal and localized: `settings.py` (two fields after `sources`), `cli/main.py` (one `add_typer`), `settings.example.yaml` (top of file), `pyproject.toml` (per-file ignores only), no `uv.lock` change.
- Commits separated by concern, never squashed, e.g.: `feat(story-1.3): add v1 configuration contract models`, `feat(story-1.3): add config provider protocol and snapshot`, `feat(story-1.3): add YAML configuration provider`, `feat(story-1.3): add config validate and schema commands`, `feat(story-1.3): add config provider and endpoints file settings`, `chore(story-1.3): commit generated config schema and example endpoints`, `ci(story-1.3): check schema freshness and example config`, `docs(story-1.3): document configuration`. Tests go with each feature commit or in `test(story-1.3): ...` commits. Every commit message ends with a blank line and `🤖 Généré avec Claude Code`.

### Previous story intelligence (1.1, 1.2)

- Gate: `uv sync --locked`, ruff (`E,F,W,I,UP,B,DTZ,BLE,S,RUF`), `ruff format --check`, `ty check` (`error-on-warning`), `lint-imports`, `pytest` (sockets blocked, `--strict-markers`). Ruff excludes `_bmad*`, `.agents`, `.claude`. Ruff `S101` (assert) is ignored only under `tests/**`.
- 1.1 `settings.py` already solved env injection (`load_settings(environ)`), `*_FILE` secrets that only touch `SecretStr` fields, and YAML errors turned into `SettingsError`. Reuse its style for `ConfigError` messages: name the file and the error class, never the content.
- 1.1 review: `test_main.py` strips ANSI from help output (`_plain`); `--debug` exists in `CliState` but no handler uses it yet.
- 1.2 domain: `Endpoint(id, name, products, rules=Rules(), enabled=True)`, `ProductEntry(name, aliases=(), exclude=(), min_version: Version | None)`, `Rules(min_cvss: float | None, kev_only: bool)`. `parse_version(text) -> Version | None` strips and returns `None` on `InvalidVersion`. The matcher is case-insensitive and whole-word, and compiles each term with `functools.cache`, so do not lower-case terms in config.
- 1.2 deferred to this story: reject whitespace-only `name`, `aliases[]`, `exclude[]` (strip before the length check). The `Term` type does this.
- 1.2 open review decision (vendor-only hits and `min_version`) is matcher-internal and does not change this story.
- Test helpers: put fixtures in `conftest.py`, never import from it. Test modules declare their own small type aliases.

### Git intelligence

Last commits on the base branch: `chore(story-1.2): record code review`, `chore(story-1.2): mark story ready for review`, `docs(story-1.2): document version parsing rule and matcher signature`, `feat(story-1.2): add vulnerability matcher`, `feat(story-1.2): add endpoint rules and product entries`. Conventions: `type(story-1.x): subject`, one concern per commit, tests committed with their feature, story-file bookkeeping in `chore(...)`, docs in `docs(...)`.

### Latest technical information (verified 2026-10-26 on this branch's lock)

- pydantic 2.14.1: callable `Discriminator` + `Tag` on a `str | Model` union reports only the chosen branch's errors, with the tag in `loc`, and generates `oneOf`. `WithJsonSchema` replaces the schema of an `Annotated` string, so a custom `AfterValidator` message coexists with a schema `pattern`. `ConfigDict(strict=True)` rejects numeric strings for floats and string booleans, and accepts int for float. `StringConstraints(strip_whitespace=True, min_length=1)` works under strict mode. `errors(include_input=False, include_url=False)` drops inputs. `ValueError` messages are prefixed `Value error, `.
- PyYAML 6.0.3: `safe_load` keeps the last duplicate key silently; `8.10` → `8.1`; `""` → `None`; `MarkedYAMLError` exposes `problem` and `problem_mark.line/.column` (0-based), and its `str()` includes a source snippet.
- Typer 0.27.3: `pretty_exceptions_show_locals` defaults to `False`; Rich is installed (coloured help). Click 8.5.0 `CliRunner` captures `stderr` separately.
- Ruff 0.17.2: `S506` flags `yaml.load(..., Loader=<SafeLoader subclass>)`; instantiating the loader and calling `get_single_data()` is not flagged.

### Project Structure Notes

- Aligned with the architecture tree: every new module is listed there (`config/models.py`, `provider.py`, `yaml_provider.py`, `schema.py`, `cli/config.py`, `schemas/config-v1.json`, `endpoints.example.yaml`, `docs/configuration.md`).
- Variance: the `ConfigError` family and `build_snapshot()` live in `config/provider.py` (the tree comment is amended, Task 10.4). They must sit below both providers, and `models.py` must not import `provider.py` (import cycle).
- Decisions taken here that a reviewer may want to revisit (the stack plan flags the token field as a likely decision point):
  - **Token shape**: an inline `token` string per endpoint (pattern `^[A-Za-z0-9_-]{32,128}$`, unique). Alternatives: a `token_file` / env reference per endpoint, which keeps secrets out of the YAML but adds a second secret path; or a separate tokens file.
  - The example placeholders pass validation, so a copied example runs with guessable URLs until the operator replaces them. 1.6 could warn on a `REPLACE-ME` prefix.
  - Top-level `version: 1` is **required**.
  - `strict=True` (no `"7.0"` → `7.0` coercion).
  - Duplicate YAML keys are rejected.
  - The shorthand supports only `>=`.
  - Endpoints with an empty product list are allowed.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.3: Endpoint configuration in YAML]
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md#F3, #F5] (FR-10, FR-11, FR-20, FR-21, FR-23)
- [Source: _bmad-output/planning-artifacts/architecture.md#Data Architecture] (configuration store, schema, YAML revision = SHA-256)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] (token format, unknown token → 404, never log tokens)
- [Source: _bmad-output/planning-artifacts/architecture.md#Format Patterns] (snake_case JSON, `extra="forbid"`)
- [Source: _bmad-output/planning-artifacts/architecture.md#Process Patterns] (CLI errors exit 2 with one line, validation at boundaries)
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure] (config package, `schemas/`, `docs/`)
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (Delivery workflow, stacked PRs, `ci.yml` on any PR base)
- [Source: _bmad-output/project-context.md] (Configuration rules, CLI rules, Security, Testing "Configuration contract")
- [Source: _bmad-output/implementation-artifacts/epic-1-stack-plan.md#3.3 Story 1.3] (manual checks the reviewer runs)
- [Source: _bmad-output/implementation-artifacts/1-2-vulnerability-model-and-matching-engine.md] (domain `Endpoint`, `ProductEntry`, `parse_version`, deferred blank-term item)
- [Source: _bmad-output/implementation-artifacts/1-1-project-skeleton-container-and-ci.md#Settings design] (`config_provider`, `endpoints_file` owned by 1.3; `*_FILE` rule)
- [Source: _bmad-output/implementation-artifacts/sprint-status.yaml] (wave 3: 1-3 | 1-4, shared files)

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created.

### File List
