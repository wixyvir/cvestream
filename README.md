# cvestream

cvestream turns the noisy public vulnerability feeds (EUVD, CISA KEV and CERT-EU
advisories) into small, filtered RSS feeds. Each team gets a feed that
matches its own stack, so a Slack RSS integration only posts what is relevant
to them, with CVSS score, known-exploited status and affected versions
in each item.

**Status:** pre-release. Epic 1 (a first working pipeline) and epic 2
(European advisory sources) are done and epic 3 (the Valkey provider, the
operator CLI and the Kubernetes deployment) is in progress; this repository
currently holds
the project skeleton, the container image, the CI quality gate, the domain
model, the endpoint configuration contract, the EUVD, CISA KEV and CERT-EU
source adapters, and the state store with the correlation and publication
decisions (each vulnerability is merged across sources and decided
once per endpoint, surviving restarts), and the daemon that polls the sources
and serves one token-protected RSS 2.0 feed per endpoint.

The endpoint configuration can also live in a **Valkey** service
(`CVESTREAM_CONFIG_PROVIDER=valkey`): a documented, versioned key layout with
per-role access rules, so that other tools (BoM connectors, CD workers) can
maintain product lists. The daemon waits for Valkey at startup, rides out an
outage on its last valid configuration and reloads when the revision changes.
Contract and access rules: [`docs/config-contract.md`](docs/config-contract.md);
server files: [`deploy/valkey/`](deploy/valkey/). :factory:

The **operator CLI** manages endpoints, products and tokens on whichever
provider is active, so nobody edits Valkey keys by hand: `cvestream endpoint
create|list|show|edit|delete`, `cvestream product add|remove|list`,
`cvestream token rotate <endpoint>`, `cvestream config export|import`.
Commands and options: [`docs/configuration.md`](docs/configuration.md#cli).
:factory:

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

Instead of editing the file by hand you can use the CLI where the file is
writable (the compose file mounts `./config` read-only into the container, so
run it on the host): `uv run cvestream endpoint create team-php --name "PHP
team"` prints the new feed URL, `uv run cvestream product add team-php "php >=
8.2"` adds a product (see [`docs/configuration.md`](docs/configuration.md#cli)).
The daemon picks the change up at its next cycle. :factory:

Each endpoint's feed is `http://<host>:8080/feeds/<token>.xml`: subscribe your
Slack channel to it with the RSS app. The first items appear after the first
poll (within 15 minutes). Put an HTTPS reverse proxy in front of port 8080; only
that port is published. Running, logs, health, token rotation and backups are in
[`docs/operations.md`](docs/operations.md).

## Kubernetes quick start

`deploy/k8s/base` is cvestream alone (YAML provider); `deploy/k8s/overlays/example`
adds the Valkey provider, an example Valkey and an Ingress for `/feeds/`. Make
your own overlay rather than editing the example.

```sh
kubectl create namespace cvestream
kubectl label namespace cvestream pod-security.kubernetes.io/enforce=restricted
# the example overlay reads its configuration from Valkey: the password in
# deploy/k8s/overlays/example/secret.example.yaml must match the cvestream-daemon
# hash of deploy/valkey/users.acl (public dev passwords: test clusters only;
# ship your own users.acl otherwise). Never commit a real password.
kubectl apply -k deploy/k8s/overlays/example
# the store starts empty (the daemon exits with code 2 until then): seed it
# through `kubectl -n cvestream exec -i cvestream-valkey-0 -- valkey-cli ...`
```

One replica with `Recreate`, a `ReadWriteOnce` block-storage volume for the
state, restricted Pod Security, `NetworkPolicy`. The image is a private GHCR
package: add an `imagePullSecrets` or make it public. Details, Secrets,
seeding and Ingress access logs:
[`docs/operations.md`](docs/operations.md#kubernetes).

The optional Valkey administration UI ships as the opt-in Kustomize component
`deploy/k8s/components/valkey-ui` (not enabled by the example overlay): list it
under `components:` in your own overlay, create its Secret from
`secret.example.yaml`, and reach it with
`kubectl -n <ns> port-forward svc/cvestream-valkey-ui 5540:5540`. Never expose it —
it has no authentication of its own
([docs/operations.md](docs/operations.md#valkey-administration-ui)). :factory:

## Releases and image tags

Pushing a tag `vX.Y.Z` (equal to the `pyproject.toml` version) runs
`.github/workflows/release.yml`: a multi-arch image (`linux/amd64`,
`linux/arm64`) with an SBOM and a provenance attestation is pushed to
`ghcr.io/wixyvir/cvestream` as `X.Y.Z`, `X.Y` and `latest`, and a GitHub
Release is created. `edge` and `sha-<short>` follow `main`. Pin `X.Y.Z`. The
release process is in [`docs/operations.md`](docs/operations.md#releases-and-image-tags).

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
kubectl kustomize deploy/k8s/base              # all must build
kubectl kustomize deploy/k8s/overlays/example
kubectl kustomize tests/k8s/example-with-valkey-ui
```

`pytest` blocks the network (pytest-socket). Tests marked `live` hit real
services and are deselected by default;
`.github/workflows/source-drift.yml` runs them nightly (see
`docs/operations.md`); tests marked `valkey` need a Valkey server: they skip
without one, and fail on CI. To run them, start a disposable server with the
repo's files and point `VALKEY_TEST_ADDRESS` at it:

```sh
docker run -d --rm --name cvs-valkey -p 127.0.0.1::6379 \
  -v "$PWD/deploy/valkey:/usr/local/etc/valkey:ro" \
  valkey/valkey:9.1.3 valkey-server /usr/local/etc/valkey/valkey.conf
export VALKEY_TEST_ADDRESS=$(docker port cvs-valkey 6379 | head -1)   # 127.0.0.1:<port>
uv run pytest -m valkey -v
docker stop cvs-valkey
```

### In a container

```sh
docker compose watch                          # dev target running the daemon, src/ synced, rebuild on uv.lock
docker build --target runtime -t cvestream:local .
docker run --rm cvestream:local --help        # runtime image: UID 999, no uv, volume /data
                                              # (its default command is `run`, with a HEALTHCHECK)
```

The stack also starts a Valkey administration UI (Redis Insight) on
`http://127.0.0.1:5540`, already connected to the `valkey` service as
`cvestream-ui`; see [docs/operations.md](docs/operations.md#valkey-administration-ui).
Loopback only: it has no authentication of its own. :factory:

The repository also ships a [devcontainer](.devcontainer/devcontainer.json)
that uses the same `app` service as `docker compose watch`; it starts the
Valkey UI too.

Work is delivered one story per branch and per pull request; see
`CLAUDE.md` for the rules.
