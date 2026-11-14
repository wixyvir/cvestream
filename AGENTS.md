# AGENTS.md

Rules for AI agents working on cvestream. Keep this file short; the details live
in two documents that you MUST read before changing code:

- `_bmad-output/planning-artifacts/architecture.md`: authoritative design
  (layering, state, configuration, delivery workflow).
- `_bmad-output/project-context.md`: the implementation rules, condensed.

Stories live in `_bmad-output/implementation-artifacts/`.

## Gate commands

Run all of them before pushing (CI runs the same):

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run ty check
uv run lint-imports
uv run pytest
uv run cvestream config schema --check
uv run cvestream config validate endpoints.example.yaml
docker build --target runtime -t cvestream:local .
```

## Key rules

- Use **uv**, not poetry or pip: `uv add`, `uv sync --locked`, `uv run`. Never edit `uv.lock` by hand.
- Python 3.14: never add `from __future__ import annotations`.
- Layering is enforced by import-linter (`[tool.importlinter]`); `domain` imports only stdlib and `packaging`. Library ownership: `typer` only in `cli/`, `httpx` only in `sources/` and `http`, `sqlalchemy`/`alembic` only in `state/`, `starlette`/`uvicorn` only in `app/`, `valkey` only in `config/`.
- Outbound HTTP goes only through `cvestream.http` (`trust_env=False`, system trust store, retries live there and nowhere else).
- Unknown is `None`, never `0`, `""` or `"n/a"`.
- Free-text versions are parsed only by `domain/version.py`; `packaging` is the only third-party import allowed in `domain`.
- Publication GUIDs are built only in `domain/publication.py`, stored in `publication.guid` and never recomputed; state is written only inside `State.transaction()`.
- Only `pipeline/run.py` opens `State.transaction()`: `app/` reads state through `pipeline.run` functions in a thread, and HTTP handlers read only the in-memory feed cache.
- Time is aware UTC from the injected `Clock`; never call `datetime.now()` elsewhere.
- Every exception inherits from `CvestreamError` and its name ends in `Error`.
- All tool configuration lives in `pyproject.toml` (no `ruff.toml`, `pytest.ini`, `.importlinter`).
- `config/models.py` is a public contract: regenerate `schemas/config-v1.json` (`uv run cvestream config schema`) in the same commit.
- Never log a feed token or a feed path (`log.py` redacts them as a safety net).
- Never commit `settings.yaml`, `endpoints.yaml`, `data/`, CA bundles or proxy settings.
- Documentation follows behaviour in the same PR: README, this file, `settings.example.yaml`, and `docs/` when it exists.

## Delivery

- One story = one branch `story-<epic>-<story>-<slug>` (no `/`) = one pull request. An epic may be a linear stack of PRs, each based on the previous story's branch.
- Conventional Commits with the story ID (`feat(story-1.1): ...`), one concern per commit, never squashed.
- Changing a pattern means amending `architecture.md` in the same PR.
- Write GitHub references in full form (`wixyvir/cvestream#12`), never `#12`.
