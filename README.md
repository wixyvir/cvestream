# cvestream

cvestream turns the noisy public vulnerability feeds (EUVD, CISA KEV and CERT-EU
advisories) into small, filtered RSS feeds. Each team gets a feed that
matches its own stack, so a Slack RSS integration only posts what is relevant
to them, with CVSS score, known-exploited status and affected versions
in each item.

**Status:** pre-release. Epic 1 (a first working pipeline) is done and epic 2
(European advisory sources) is in progress; this repository currently holds
the project skeleton, the container image, the CI quality gate, the domain
model, the endpoint configuration contract, the EUVD, CISA KEV and CERT-EU
source adapters, and the state store with the correlation and publication
decisions (each vulnerability is merged across sources and decided
once per endpoint, surviving restarts), and the daemon that polls the sources
and serves one token-protected RSS 2.0 feed per endpoint.

Design: [`_bmad-output/planning-artifacts/architecture.md`](_bmad-output/planning-artifacts/architecture.md).
Sources and their assumptions: [`docs/sources.md`](docs/sources.md).
Static settings: [`settings.example.yaml`](settings.example.yaml) (every key is
documented there; environment variables use the `CVESTREAM_` prefix, `__` for
nesting). Endpoint configuration (`endpoints.yaml`) and settings are described
in [`docs/configuration.md`](docs/configuration.md).

## Self-hosted quick start

One container, the YAML configuration provider and a named volume for the state.
Needs Docker with Compose.

```sh
cd deploy/compose
mkdir config && cp ../../endpoints.example.yaml config/endpoints.yaml
# edit config/endpoints.yaml: your teams, products and one secret token each
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up -d
```

Each endpoint's feed is `http://<host>:8080/feeds/<token>.xml`: subscribe your
Slack channel to it with the RSS app. The first items appear after the first
poll (within 15 minutes). Put an HTTPS reverse proxy in front of port 8080; only
that port is published. Running, logs, health, token rotation and backups are in
[`docs/operations.md`](docs/operations.md).

## Development quick start

Requirements: [uv](https://docs.astral.sh/uv/) 0.13 (Python 3.14 is installed
by uv) and, for the container workflow, Docker with Compose.

```sh
uv sync                    # create .venv with runtime and dev dependencies
uv run cvestream --help    # root command
cp endpoints.example.yaml endpoints.yaml   # then replace the placeholder tokens
uv run cvestream config validate endpoints.yaml
uv run cvestream db migrate                # creates ./data/cvestream.db (gitignored)
uv run cvestream run                       # the daemon: feeds on :8080, health on :9090
uv run cvestream poll --once               # or: fetch every source once, run one cycle, exit
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
uv run cvestream config schema --check
uv run cvestream config validate endpoints.example.yaml
```

`pytest` blocks the network (pytest-socket). Tests marked `live` hit real
services and are deselected by default; tests marked `valkey` need a Valkey
server.

### In a container

```sh
docker compose watch                          # dev target running the daemon, src/ synced, rebuild on uv.lock
docker build --target runtime -t cvestream:local .
docker run --rm cvestream:local --help        # runtime image: UID 999, no uv, volume /data
                                              # (its default command is `run`, with a HEALTHCHECK)
```

The repository also ships a [devcontainer](.devcontainer/devcontainer.json)
that uses the same `app` service as `docker compose watch`.

Work is delivered one story per branch and per pull request; see
`CLAUDE.md` for the rules.
