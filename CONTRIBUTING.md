# Contributing to cvestream

Thanks for your interest in contributing. This document describes how to set
up a development environment, run the quality gates, and submit a pull
request.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) 0.13 or later (manages Python 3.14 and all
  dependencies).
- Docker with Compose (optional, for the container workflow).
- Git.

## Setup

```sh
git clone https://github.com/wixyvir/cvestream.git
cd cvestream
uv sync --locked
```

## Quality gate

CI runs exactly these commands. Run them locally before pushing:

```sh
uv run ruff check
uv run ruff format --check
uv run ty check
uv run lint-imports
uv run pytest
uv run cvestream config schema --check
uv run cvestream config validate endpoints.example.yaml
```

`pytest` blocks network access by default (pytest-socket). Tests marked `live`
hit real services and are deselected by default; tests marked `valkey` need a
Valkey server.

## Branching and commits

- One story = one branch = one pull request to `main`.
- Branch name: `story-<epic>-<story>-<slug>` (e.g. `story-1-1-project-init`).
  No `/` in branch names.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(story-1.1): ...`, `fix(story-2.1): ...`, `docs: ...`, `chore: ...`.
  One concern per commit, never squashed.
- Pull request titles follow the same convention, with the story ID:
  `feat(story-1.1): initialize project skeleton`.

## Pull request checklist

- [ ] Conventional commit title with the story ID.
- [ ] All quality gate commands pass locally.
- [ ] Tests added or updated for the change.
- [ ] Documentation updated in the same PR if behaviour changed
      (`README.md`, `docs/`, `settings.example.yaml`, `endpoints.example.yaml`).
- [ ] Linked issue (use the full form `wixyvir/cvestream#12`).

## Architecture

Read these before making changes:

- [`_bmad-output/planning-artifacts/architecture.md`](_bmad-output/planning-artifacts/architecture.md) —
  the authoritative design document.
- [`_bmad-output/project-context.md`](_bmad-output/project-context.md) —
  the condensed rule set (layering, state, configuration, testing).
- [`CLAUDE.md`](CLAUDE.md) — quick reference for AI agents.

Key rules in brief:

- **Layering** is enforced by import-linter. `domain` imports only stdlib and
  `packaging`. Library ownership: `httpx` only in `sources/` and `http`,
  `sqlalchemy`/`alembic` only in `state/`, `starlette`/`uvicorn` only in
  `app/`, `typer` only in `cli/`, `valkey` only in `config/`.
- **Unknown is `None`**, never `0`, `""`, or `"n/a"`.
- **Time** is aware UTC from the injected `Clock`; never `datetime.now()`.
- **Exceptions** inherit from `CvestreamError` and end in `Error`.
- **Tests** are pure for the domain (no database, no network, no mocks).
  Fixtures are recorded from real APIs, never hand-written.
- **Never commit** `settings.yaml`, `endpoints.yaml`, or `data/`.

## Adding a new vulnerability source

See the "new source" issue template. Each source needs:

- One adapter module in `src/cvestream/sources/` implementing the
  `SourceAdapter` protocol.
- Contract tests in `tests/contract/<source>/` with recorded fixtures in
  `tests/fixtures/<source>/`.
- Documentation in `docs/sources.md`.
- An entry in `settings.example.yaml`.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating, you agree to uphold it.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
