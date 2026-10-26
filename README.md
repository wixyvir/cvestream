# cvestream

cvestream turns the noisy public vulnerability feeds (EUVD, CISA KEV, and later
CERT-EU and CERT-FR) into small, filtered RSS feeds. Each team gets a feed that
matches its own stack, so a Slack RSS integration only posts what is relevant
to them, with CVSS score, known-exploited status and affected versions
in each item.

**Status:** pre-release. Epic 1 (a first working pipeline) is in progress; this
repository currently holds the project skeleton, the container image and the
CI quality gate. There are no feeds yet.

Design: [`_bmad-output/planning-artifacts/architecture.md`](_bmad-output/planning-artifacts/architecture.md).
Static settings: [`settings.example.yaml`](settings.example.yaml) (every key is
documented there; environment variables use the `CVESTREAM_` prefix, `__` for
nesting).

## Development quick start

Requirements: [uv](https://docs.astral.sh/uv/) 0.13 (Python 3.14 is installed
by uv) and, for the container workflow, Docker with Compose.

```sh
uv sync                    # create .venv with runtime and dev dependencies
uv run cvestream --help    # root command (no subcommands yet)
```

### Quality gate

CI runs exactly these commands; run them before pushing.

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run ty check
uv run lint-imports
uv run pytest
```

`pytest` blocks the network (pytest-socket). Tests marked `live` hit real
services and are deselected by default; tests marked `valkey` need a Valkey
server.

### In a container

```sh
docker compose watch                          # dev target, src/ synced, rebuild on uv.lock
docker build --target runtime -t cvestream:local .
docker run --rm cvestream:local --help        # runtime image: UID 999, no uv, volume /data
```

The repository also ships a [devcontainer](.devcontainer/devcontainer.json)
that uses the same `app` service as `docker compose watch`.

Work is delivered one story per branch and per pull request; see
`CLAUDE.md` for the rules.
