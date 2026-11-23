# cvestream

[![CI](https://github.com/wixyvir/cvestream/actions/workflows/ci.yml/badge.svg)](https://github.com/wixyvir/cvestream/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.14-blue.svg)](pyproject.toml)

cvestream turns noisy public vulnerability feeds (EUVD, CISA KEV, CERT-EU
advisories) into small, filtered RSS 2.0 feeds. Each team gets a feed that
matches its own stack, so a Slack RSS integration only posts what is relevant
to them — with CVSS score, known-exploited status, and affected versions in
each item, and an emoji-flagged title (🔥 exploited, 🔴 critical, 🔺 severity
raised, 🆕 new that day) so the ones that matter stand out while scrolling.
A burst of several vulnerabilities of the same product in one poll can be
grouped into a single message (`group_by_product`).

**Status:** v0.x — early development. The API and configuration format may
change between releases.

## How it works

```
EUVD ─┐
CISA KEV ─┼─→ poll → normalize → correlate → match per endpoint → publish once → RSS 2.0 per team
CERT-EU ─┘                                                            (token-protected, :8080)
```

Each vulnerability is merged across sources (CVE/EUVD aliases), matched
against each endpoint's product list and rules, and published once. It is
re-posted only when it becomes known-exploited or its severity band rises.
State survives restarts; a restart never re-posts.

## Self-hosted quick start

One container, the YAML configuration provider, and a named volume for the
state. Needs Docker with Compose.

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
services and are deselected by default;
`.github/workflows/source-drift.yml` runs them nightly (see
`docs/operations.md`); tests marked `valkey` need a Valkey server.

### In a container

```sh
docker compose watch                          # dev target running the daemon, src/ synced, rebuild on uv.lock
docker build --target runtime -t cvestream:local .
docker run --rm cvestream:local --help        # runtime image: UID 999, no uv, volume /data
                                              # (its default command is `run`, with a HEALTHCHECK)
```

The repository also ships a [devcontainer](.devcontainer/devcontainer.json)
that uses the same `app` service as `docker compose watch`.

## Documentation

| Document | What it covers |
|---|---|
| [`docs/configuration.md`](docs/configuration.md) | `settings.yaml`, `CVESTREAM_*` env vars, `endpoints.yaml` |
| [`docs/operations.md`](docs/operations.md) | Running, logs, health, backups, token rotation, source drift |
| [`docs/sources.md`](docs/sources.md) | Each source's API assumptions (timezone, pagination, placeholders) |
| [`settings.example.yaml`](settings.example.yaml) | Annotated reference for all static settings |
| [`endpoints.example.yaml`](endpoints.example.yaml) | Annotated reference for endpoint configuration |
| [`_bmad-output/planning-artifacts/architecture.md`](_bmad-output/planning-artifacts/architecture.md) | Design and layering (authoritative) |

## Contributing

We welcome contributions. Please read:

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup, quality gates, branching, commits
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — community standards
- [`SECURITY.md`](SECURITY.md) — how to report a security issue

Work is delivered one story per branch and per pull request; see
[`AGENTS.md`](AGENTS.md) for the rules.

## License

cvestream is licensed under the [Apache License 2.0](LICENSE).
