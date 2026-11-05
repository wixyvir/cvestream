---
baseline_commit: 6c02c2c
---

# Story 3.3: Release and Kubernetes deployment

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want versioned multi-arch images and Kubernetes manifests,
so that I can deploy a pinned release under Docker or Kubernetes with a hardened, single-replica setup.

## TL;DR

- **Three deliverables, almost no Python:** `.github/workflows/release.yml` (tag `vX.Y.Z` → GHCR multi-arch image + GitHub Release), `deploy/k8s/base` + `deploy/k8s/overlays/example` (Kustomize), and the docs. No `src/` change, no new dependency, `uv.lock` unchanged. One new pytest module guards the deployment invariants and the release pins.
- **`release.yml`** = three jobs `check` → `image` → `release`. `check` refuses any tag that is not exactly `v<pyproject.toml version>` (pre-release tags are refused, so `latest` and `X.Y` never move on an RC). `image` builds `linux/amd64,linux/arm64` with `target: runtime`, pushes `X.Y.Z`, `X.Y`, `latest` (metadata-action `type=semver` + `latest=auto`), OCI labels and annotations, `sbom: true`, `provenance: mode=max`. `release` runs `gh release create --verify-tag --generate-notes`. A sketch is in Dev Notes, **verified with actionlint and `zizmor --offline` (no finding)**.
- **Gotcha found while researching:** `wixyvir/cvestream` is a **private, user-owned** repository. GitHub artifact attestations (`actions/attest`, `gh attestation verify`) need a public repo or GitHub Enterprise Cloud, and storage records need an org-owned repo. So the SBOM and provenance are **BuildKit attestations** (stored with the image, work everywhere) and the GitHub attestation step runs only `if: !github.event.repository.private`, with `create-storage-record: false`. The GHCR package of a private repo is **private** too: the Kubernetes docs must cover an `imagePullSecret`.
- **Kustomize:** `base` = cvestream with the **YAML provider** (works without Valkey): Deployment (1 replica, `Recreate`, UID 999, read-only root, caps dropped, `terminationGracePeriodSeconds: 60`, exec probes `cvestream healthcheck [--ready]`, plus the harness findings `enableServiceLinks: false`, `automountServiceAccountToken: false`, `fsGroup: 999`, Secret mounted as a directory), RWO PVC, Services `cvestream-public` (8080) and `cvestream-private` (9090), NetworkPolicy. `overlays/example` = the **Valkey provider** + example Valkey `StatefulSet`, example `Ingress` (only `/feeds/`), Secret example with placeholders, NetworkPolicies for Valkey.
- **3.1's Valkey files:** the overlay cannot read `deploy/valkey/*` through a generator (Kustomize load restrictor, checked locally). **Chosen option:** a new `deploy/valkey/kustomization.yaml` generates the `valkey.conf` ConfigMap and the `users.acl` Secret, and the overlay lists `../../../valkey` under `resources:` (verified locally: the hashed names are rewritten in the overlay's StatefulSet). CI runs plain `kubectl kustomize`, never `--load-restrictor LoadRestrictionsNone`.
- **Story 3.1 is developed in parallel:** do not depend on its unmerged code. The files `deploy/valkey/valkey.conf` and `deploy/valkey/users.acl` arrive with 3.1; this branch is rebased onto the reviewed 3.1 before review. Until then the overlay build needs those two files: see "Dependency on story 3.1" for the exact procedure (never commit stubs of them).
- **CI:** the `ci` job gains `kubectl kustomize deploy/k8s/base` and `kubectl kustomize deploy/k8s/overlays/example` (kubectl 1.37.1 / kustomize 5.8.3 are preinstalled on `ubuntu-24.04`), so `ci` stays the only required check.
- **Pins:** the base image becomes `ghcr.io/wixyvir/cvestream:0.1.0` and `deploy/compose/compose.yml` moves from `:edge` to `:0.1.0`; a test keeps both equal to the `pyproject.toml` version, so a version bump PR must bump them too.
- **Docs in the same PR:** `docs/operations.md` (Kubernetes deployment, release process, image tags), `README.md` (Kubernetes quick start, releases), `CLAUDE.md` and `README.md` gate lists (+ `kubectl kustomize`), `architecture.md` (tree + `actions/attest` + release tag rule), `deferred-work.md` (healthcheck `--host`: documented; editable install: kept).
- **Shared files with 3.1:** `.github/workflows/ci.yml`, `docs/operations.md`, `README.md`, `CLAUDE.md`, `sprint-status.yaml`, `deferred-work.md`, maybe `architecture.md`. Keep edits in separate sections to make the rebase easy.

## Acceptance Criteria

1. **Release trigger and version check (NFR-2).**
   **Given** `.github/workflows/release.yml`
   **When** a tag matching `v*` is pushed (the only trigger: no `pull_request`, no branch push, no `workflow_dispatch`)
   **Then** a first job compares the tag with `v` + the `[project].version` of `pyproject.toml` and fails, before any build or push, when they differ or when the tag is not exactly `vX.Y.Z` (pre-release tags such as `v0.2.0-rc.1` are refused)
   **And** the tag reaches the script through `env:`, never through a `${{ }}` expression inside `run:`.

2. **Multi-arch image on GHCR (NFR-2).**
   **Given** a valid tag `vX.Y.Z`
   **When** the image job runs
   **Then** it builds the `runtime` target of `Dockerfile` for `linux/amd64` and `linux/arm64` and pushes `ghcr.io/<owner>/<repo>` (derived from `github.repository`, lower-cased, not hard-coded) with the tags `X.Y.Z`, `X.Y` and `latest`
   **And** the image carries the OCI labels (and index annotations) generated by `docker/metadata-action`, an SBOM attestation (`sbom: true`) and a provenance attestation in max mode (`provenance: mode=max`)
   **And** a GitHub artifact attestation of the pushed digest (`actions/attest`, `push-to-registry: true`) is created when the repository is public, and skipped (with a comment explaining why) when it is private.

3. **GitHub Release (NFR-2).**
   **Given** the image job succeeded
   **When** the release job runs
   **Then** it creates the GitHub Release of the tag with `GITHUB_TOKEN` only (`gh release create "$TAG" --verify-tag --generate-notes`).

4. **Least privilege and supply chain for `release.yml`.**
   **Given** the workflow file
   **Then** top-level `permissions: {}`; `check` has `contents: read`; `image` has exactly `contents: read`, `packages: write`, `id-token: write`, `attestations: write`; `release` has exactly `contents: write`
   **And** every action is pinned by full commit SHA with the version in a trailing comment, using the same SHAs as `ci.yml` for the actions both use; checkout has `persist-credentials: false`; every job has `timeout-minutes`; a `concurrency` group per tag with `cancel-in-progress: false`; no build cache (a release must not restore a cache that a PR could have written)
   **And** `actionlint` and `zizmor --offline` report no finding.

5. **Kustomize base: the cvestream Deployment (NFR-2, NFR-6).**
   **Given** `deploy/k8s/base`
   **When** `kubectl kustomize deploy/k8s/base` builds it
   **Then** it produces a `Deployment` `cvestream` with `replicas: 1`, `strategy.type: Recreate`, `terminationGracePeriodSeconds: 60`, `enableServiceLinks: false`, `automountServiceAccountToken: false`, pod `securityContext` (`runAsNonRoot: true`, `runAsUser: 999`, `runAsGroup: 999`, `fsGroup: 999`, `seccompProfile.type: RuntimeDefault`) and container `securityContext` (`allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`), so that it is admitted under the `restricted` Pod Security Standard
   **And** `startupProbe` and `readinessProbe` exec `cvestream healthcheck --ready`, `livenessProbe` execs `cvestream healthcheck`, each with a `timeoutSeconds` of a few seconds (each probe starts Python)
   **And** `/data` is the PVC, `/tmp` an `emptyDir`, and the endpoints Secret is mounted as a directory (no `subPath`) on `/config`, read-only, with `CVESTREAM_CONFIG_PROVIDER=yaml` and `CVESTREAM_ENDPOINTS_FILE=/config/endpoints.yaml` from the `cvestream-settings` ConfigMap
   **And** the image is `ghcr.io/wixyvir/cvestream:<pyproject.toml version>`, never `edge` or `latest`.

6. **Kustomize base: storage, Services and network policy (NFR-2).**
   **Given** the same build
   **Then** it also produces a `PersistentVolumeClaim` `cvestream-data` (`ReadWriteOnce`, with a comment that it must be block storage, never NFS/SMB/CIFS), a `Service` `cvestream-public` on 8080 and a `Service` `cvestream-private` on 9090
   **And** a `NetworkPolicy` that selects only the cvestream pods, admits 8080 only from pods labelled `cvestream.io/public-access: "true"`, admits 9090 only from pods labelled `cvestream.io/private-access: "true"` in the same namespace, and allows egress only to DNS (`kube-system` / `k8s-app: kube-dns`, UDP and TCP 53) and TCP 443.

7. **Example overlay (NFR-2).**
   **Given** `deploy/k8s/overlays/example`
   **When** `kubectl kustomize deploy/k8s/overlays/example` builds it (plain, without `--load-restrictor LoadRestrictionsNone`)
   **Then** it contains everything from the base, switched to the Valkey provider (`CVESTREAM_CONFIG_PROVIDER=valkey`, `CVESTREAM_VALKEY__URL`, `CVESTREAM_VALKEY__PASSWORD_FILE` pointing into a Secret mounted as a directory; the endpoints Secret volume removed)
   **And** an example Valkey `StatefulSet` (1 replica, `valkey/valkey` image pinned as in 3.1, `restricted`-compatible security context, UID 999) that mounts story 3.1's `deploy/valkey/valkey.conf` (ConfigMap) and `deploy/valkey/users.acl` (Secret) through `deploy/valkey/kustomization.yaml`, keeps its AOF on a `volumeClaimTemplates` volume, and is reached through a headless `Service`
   **And** NetworkPolicies so that Valkey accepts 6379 only from the cvestream pods and from pods labelled `cvestream.io/valkey-access: "true"`, Valkey has no egress, and cvestream may reach Valkey on 6379
   **And** an example `Ingress` that routes only the `/feeds/` prefix to `cvestream-public`, with a comment that controller access logs contain the token
   **And** a Secret example with placeholders only (no real token or password)
   **And** `docs/operations.md` explains how to adapt it (namespace, image, Secrets, Ingress controller, NetworkPolicy peers).

8. **CI validates the Kustomize build.**
   **Given** a pull request
   **When** the `ci` job of `.github/workflows/ci.yml` runs
   **Then** it runs `kubectl kustomize deploy/k8s/base` and `kubectl kustomize deploy/k8s/overlays/example` inside the existing `ci` job (no new required check), and fails when either build fails.

9. **Pinned release everywhere, kept in sync.**
   **Given** `pyproject.toml` version `X.Y.Z`
   **Then** `deploy/k8s/base` and `deploy/compose/compose.yml` use the image tag `X.Y.Z`
   **And** a pytest test fails when either differs from the `pyproject.toml` version, and also checks the Deployment invariants of AC 5 (replicas, `Recreate`, grace period, `enableServiceLinks`, `automountServiceAccountToken`, `readOnlyRootFilesystem`, probe commands) on the raw manifest files.

10. **Documentation (NFR-2, NFR-6).**
    **Given** `docs/operations.md` and `README.md`
    **Then** they cover Kubernetes deployment (base vs example overlay, how to adapt the overlay, Secrets, private GHCR package and `imagePullSecrets`, Ingress and access logs, NetworkPolicy labels, Valkey admin through `kubectl exec`), the block-storage requirement for the SQLite volume, the single-replica constraint and `Recreate`, the release process (bump `pyproject.toml` + the pins, `uv lock`, PR, tag `vX.Y.Z` on `main`) and the image tags (`X.Y.Z`, `X.Y`, `latest`, `edge`, `sha-<short>`)
    **And** the gate lists of `CLAUDE.md` and `README.md` include `kubectl kustomize deploy/k8s/overlays/example`
    **And** `architecture.md` matches what was built (tree, attestation action, release tag rule), and the two `deferred-work.md` items aimed at this story are closed or explicitly kept.

11. **No regression.** Every gate in `CLAUDE.md` stays green; `src/`, `pyproject.toml` (the version stays `0.1.0`), `uv.lock`, `schemas/` and `settings.example.yaml` are unchanged; no `v*` tag is pushed and no GitHub Release is created.

## Tasks / Subtasks

- [ ] **Task 1: `.github/workflows/release.yml`** (AC: 1, 2, 3, 4)
  - [ ] 1.1 Write the workflow from the verified sketch in Dev Notes ("Release workflow sketch"): header comment, `on.push.tags: ["v*"]`, `permissions: {}`, `concurrency: {group: release-${{ github.ref }}, cancel-in-progress: false}`, `defaults.run.shell: bash`.
  - [ ] 1.2 Job `check` (`contents: read`, `timeout-minutes: 5`): checkout, then the version script (tag through `env:`, regex `^v[0-9]+\.[0-9]+\.[0-9]+$`, `tomllib` from the runner's `python3 -I`, `::error::` messages).
  - [ ] 1.3 Job `image` (`needs: check`, permissions exactly as AC 4, `timeout-minutes: 60`): checkout, lower-cased image name step, setup-qemu, setup-buildx, login to `ghcr.io` with `GITHUB_TOKEN`, metadata-action (`type=semver,pattern={{version}}`, `type=semver,pattern={{major}}.{{minor}}`, default `latest=auto`), build-push-action (`target: runtime`, both platforms, `push: true`, tags, labels, annotations, `sbom: true`, `provenance: mode=max`, **no** `cache-from`/`cache-to`), then `actions/attest` guarded by `if: ${{ !github.event.repository.private }}` with `subject-name` (no tag), `subject-digest: ${{ steps.build.outputs.digest }}`, `push-to-registry: true`, `create-storage-record: false`.
  - [ ] 1.4 Job `release` (`needs: image`, `contents: write`, `timeout-minutes: 5`): `gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --verify-tag --generate-notes` with `GH_TOKEN` and `TAG` in `env:`.
  - [ ] 1.5 Pin SHAs: reuse `ci.yml`'s SHAs for checkout, setup-qemu, setup-buildx, login, metadata, build-push (check `ci.yml` at the time you write it: Dependabot may have bumped some). `actions/attest` v4.2.2 = `1e69f48acb82d1966a394da916b4c1698aa569d6` (resolved with `gh api repos/actions/attest/git/ref/tags/v4.2.2` on 2026-11-05, type `commit`); re-check it.
  - [ ] 1.6 Lint: `SSL_CERT_FILE=$HOME/ca.pem uvx --from actionlint-py actionlint .github/workflows/release.yml` and `SSL_CERT_FILE=$HOME/ca.pem uvx zizmor --offline .github/workflows/`: no finding.
  - [ ] 1.7 Dry-run the version check locally: copy the script to the scratchpad, run it from the worktree root with `TAG=v0.1.0` (passes), `TAG=v9.9.9`, `TAG=v0.1.0-rc.1`, `TAG=0.1.0` (each fails with its message). Do not commit the scratch script.
- [ ] **Task 2: `deploy/k8s/base`** (AC: 5, 6)
  - [ ] 2.1 `kustomization.yaml` (`apiVersion: kustomize.config.k8s.io/v1beta1`, `kind: Kustomization`, `resources:` in the order of the tree, `labels:` with `app.kubernetes.io/part-of: cvestream` and `includeSelectors: false`). No `namespace:` in the base.
  - [ ] 2.2 `configmap-settings.yaml`: `cvestream-settings` with `CVESTREAM_CONFIG_PROVIDER: yaml`, `CVESTREAM_ENDPOINTS_FILE: /config/endpoints.yaml`, `CVESTREAM_LOG__FORMAT: json`, `CVESTREAM_LOG__LEVEL: info`; comment that `CVESTREAM_DATA_DIR=/data` comes from the image and that a ConfigMap change needs `kubectl rollout restart deploy/cvestream` (env vars are read at start).
  - [ ] 2.3 `deployment.yaml`: start from the harness Deployment (Dev Notes, "Harness input"), change the image to `ghcr.io/wixyvir/cvestream:0.1.0`, drop `imagePullPolicy: Never` (default `IfNotPresent` for a tag), keep every hardening line and its comment, volume `endpoints` → Secret `cvestream-endpoints` (`defaultMode: 0440`, directory mount on `/config`).
  - [ ] 2.4 `pvc.yaml` (`cvestream-data`, RWO, 1Gi, no `storageClassName`, block-storage comment), `service-public.yaml`, `service-private.yaml` (port names, `targetPort` by container port name).
  - [ ] 2.5 `networkpolicy.yaml` as AC 6 (see Dev Notes, "NetworkPolicy design").
  - [ ] 2.6 `kubectl kustomize deploy/k8s/base` builds; read the output.
- [ ] **Task 3: `deploy/valkey/kustomization.yaml` and `deploy/k8s/overlays/example`** (AC: 7) — read "Dependency on story 3.1" first
  - [ ] 3.1 `deploy/valkey/kustomization.yaml`: `configMapGenerator` `cvestream-valkey-config` from `valkey.conf`, `secretGenerator` `cvestream-valkey-acl` from `users.acl` (a Secret: it holds password hashes). A comment says it exists so overlays can mount these files despite the load restrictor.
  - [ ] 3.2 `overlays/example/kustomization.yaml`: `namespace: cvestream`, `resources:` `../../base`, `../../../valkey`, `valkey-statefulset.yaml`, `ingress.yaml`, `secret.example.yaml` (and `networkpolicy-valkey.yaml` / `valkey-service.yaml` if you split them out); `patches:` for the ConfigMap (Valkey provider), the Deployment (volumes) and the cvestream NetworkPolicy (egress to Valkey, 8080 peer = the Ingress controller). Patch forms verified locally are in Dev Notes.
  - [ ] 3.3 `valkey-statefulset.yaml`: headless `Service` `cvestream-valkey` (6379, `clusterIP: None`) + `StatefulSet` `cvestream-valkey` (see Dev Notes, "Valkey StatefulSet").
  - [ ] 3.4 `ingress.yaml`: `Ingress` `cvestream`, `ingressClassName` (example value with a comment), host `cvestream.example.com`, TLS secret placeholder, one rule `path: /feeds/`, `pathType: Prefix` → `cvestream-public:8080`; comment on access logs (Traefik: an `IngressRoute` with `observability.accessLogs: false`, harness finding 3).
  - [ ] 3.5 `secret.example.yaml`: Secret `cvestream-valkey` with `stringData.password: REPLACE-ME-...`; comment: replace it (or remove it from the kustomization and create the Secret out of band), never commit real values; the value must match the `cvestream-daemon` entry of the `users.acl` you deploy.
  - [ ] 3.6 `kubectl kustomize deploy/k8s/overlays/example` builds; check the output against AC 7 (hashed ConfigMap/Secret names rewritten in the StatefulSet, no `CVESTREAM_ENDPOINTS_FILE`, no `endpoints` volume).
- [ ] **Task 4: CI and pins** (AC: 8, 9)
  - [ ] 4.1 `ci.yml`, `ci` job: after `uv run cvestream config validate endpoints.example.yaml` and before the Docker steps, add `run: kubectl version --client` (log only) and one step that builds both: `kubectl kustomize deploy/k8s/base >/dev/null` and `kubectl kustomize deploy/k8s/overlays/example >/dev/null`. No other change to `ci.yml` (3.1 edits it too).
  - [ ] 4.2 `deploy/compose/compose.yml`: `image: ghcr.io/wixyvir/cvestream:0.1.0`; replace the "Pin a release tag ... (story 3.3)" comment with one that says the tag follows releases and `edge` follows `main`.
  - [ ] 4.3 `tests/unit/test_deploy_manifests.py` (see Dev Notes, "Testing standards"): version pins of `deploy/k8s/base/deployment.yaml` and `deploy/compose/compose.yml` vs `pyproject.toml`; Deployment invariants of AC 5 on the raw YAML. stdlib `tomllib` + PyYAML (already a runtime dependency), no new dependency, no network, no `kubectl`.
- [ ] **Task 5: documentation** (AC: 10)
  - [ ] 5.1 `docs/operations.md`: new `## Kubernetes` section (after `## Run`/`## Startup and shutdown`, your call) and new `## Releases and image tags` section; TL;DR bullets; replace the `:edge` in the "Container" row of `## Run` with the release tag; extend `## Health` with the `127.0.0.1` probe note (deferred `--host` item). Content checklist in Dev Notes, "What the docs must say".
  - [ ] 5.2 `README.md`: a short `## Kubernetes quick start` after the self-hosted one, one paragraph on releases and image tags, `kubectl kustomize deploy/k8s/overlays/example` in the "Quality gate" block. Do not rewrite the `**Status:**` paragraph (story 3.1 is likely to edit it).
  - [ ] 5.3 `CLAUDE.md`: add `kubectl kustomize deploy/k8s/overlays/example` to "Gate commands" (before `docker build`).
  - [ ] 5.4 `architecture.md`: tree (`deploy/valkey/kustomization.yaml`, the overlay's actual files, `tests/unit/test_deploy_manifests.py` if the tree lists tests), the Actions versions line (`actions/attest` v4.2.2 instead of `attest-build-provenance`, which is now a wrapper), the release row (tag must be exactly `vX.Y.Z`; GitHub attestation only on a public repository; SBOM/provenance are BuildKit attestations). Same for `project-context.md`'s "Release" bullet if wording changes.
  - [ ] 5.5 `deferred-work.md`: mark "`cvestream healthcheck` always probes `127.0.0.1`" as documented by 3.3 (no `--host`: the Kubernetes manifests keep the default `0.0.0.0` bind), and "Editable install in the runtime image" as kept for later with the reason (see Decisions). Use the existing strike-through + "Resolved by" style for a closed item.
- [ ] **Task 6: gates, rebase and bookkeeping** (AC: 11)
  - [ ] 6.1 Every gate of `CLAUDE.md` in the worktree, plus both `kubectl kustomize` builds, plus actionlint/zizmor. `docker build --target runtime -t cvestream:story-3-3-release-and-kubernetes-deployment .` once: behind the proxy it fails pulling `ghcr.io` with x509; report it and rely on the PR's `ci` check.
  - [ ] 6.2 Optional, if you can: `kubeconform -strict -summary` on both builds (not installed here; skip if unavailable, say so in the Completion Notes).
  - [ ] 6.3 Fill the Dev Agent Record (decisions, what was not run: the release workflow itself, minikube), the File List, set the story to `review` here and in `sprint-status.yaml`.

## Dev Notes

### Scope

- In: `release.yml`; `deploy/k8s/**`; `deploy/valkey/kustomization.yaml` (new file only, 3.1 owns the rest of `deploy/valkey/`); the `ci.yml` Kustomize step; the compose image pin; one test module; docs (`docs/operations.md`, `README.md`, `CLAUDE.md`, `architecture.md`, `deferred-work.md`, maybe `project-context.md`).
- Out: any `src/` change (no `healthcheck --host`, see Decisions); the Dockerfile (editable install kept, see Decisions); Helm (rejected in architecture); a `Namespace` manifest (the operator creates it, with the PSS label; a Namespace inside the overlay would fight with a parent overlay's `namespace:`); `timeout-minutes` on `ci.yml` jobs (deferred item, not this story; avoid widening the `ci.yml` diff that 3.1 also edits); pushing any tag; creating any Release; the operator CLI (story 3.2).

### Current state of the files this story touches or relies on

- **`.github/workflows/ci.yml`** (read in full): one `ci` job (`contents: read`, `UV_LOCKED=1`): checkout `3d3c42e5...` v7.0.1, setup-uv `68559e99...` v10.3.3 (`version: "0.13.5"`), the 8 gate commands, setup-buildx `f87e5991...` v4.4.1, build-push `c3c9e263...` v7.4.0 (amd64, `load: true`, gha cache), smoke `docker run --rm cvestream:ci --help`. Then `publish` (push to `main` only, `packages: write`): setup-qemu `99012661...` v4.4.0, setup-buildx, login `81c6f93d...` v4.7.0, metadata `dc802804...` v6.2.0 (`type=edge,branch=main`, `type=sha`), build-push amd64+arm64 to the **hard-coded** `ghcr.io/wixyvir/cvestream`, no SBOM/provenance. No `timeout-minutes`. Preserve: `ci` stays the only required check, no `branches:` filter on `pull_request` (stacked PRs). Change: one Kustomize step in `ci`. Story 3.1 adds a `valkey/valkey:9.1` service and the `valkey` tests to the same job.
- **`Dockerfile`**: `builder` (uv 0.13.6 image, `uv sync --locked`, editable project), `dev`, `runtime` (`python:3.14-slim-trixie`, UID/GID 999, `/data` owned by 999, venv + `src` copied, `CVESTREAM_DATA_DIR=/data`, `VOLUME /data`, `EXPOSE 8080 9090`, `HEALTHCHECK cvestream healthcheck`, `ENTRYPOINT ["cvestream"]`, `CMD ["run"]`). Unchanged by this story. `release.yml` builds `target: runtime` from it.
- **`deploy/compose/compose.yml`**: GHCR image `:edge` with the comment "Pin a release tag (X.Y.Z) once release.yml publishes one (story 3.3)", `read_only`, `tmpfs /tmp`, `cap_drop: ALL`, `no-new-privileges`, `stop_grace_period: 60s`, only 8080 published, `./config:/config:ro` as a directory. Change only the tag and that comment.
- **`src/cvestream/cli/healthcheck.py`**: stdlib only, probes `http://127.0.0.1:<port>/healthz|/readyz`, `--ready`, `--port` (default 9090), 2 s timeout, exit 0/1, ignores proxies. Unchanged. The exec probes call it; the private listener binds `0.0.0.0:9090` by default (`settings.py`, `PrivateListenerSettings`), so `127.0.0.1` answers inside the pod.
- **`settings.py` / `settings.example.yaml`**: `config_provider` is `yaml` only on this branch; story 3.1 adds `valkey` and the `valkey.*` settings (`valkey.url`, password through `CVESTREAM_VALKEY__PASSWORD_FILE`, `valkey.startup_timeout`, default 60 s). The overlay uses those names (architecture.md, "Runtime configuration"); they only have to be right once the branch sits on 3.1 (the Kustomize build does not run the app).
- **`docs/operations.md`**: sections TL;DR, Run, Startup and shutdown, Health, `/status`, Logs, Source drift, Feeds, Token rotation, Security, Backup, Limits. It already says: grace period 60 s (`terminationGracePeriodSeconds: 60`), single replica, block storage, access logs contain the token, `HEALTHCHECK` + exec probes. The "Container" row and the compose text use `:edge`. Exit code 3 is "`endpoints.yaml` missing or unreadable" (3.1 extends it to Valkey).
- **`README.md`**: status paragraph (stale: "epic 2 in progress"; leave it to 3.1 or a later chore), Self-hosted quick start, Development quick start, Quality gate (8 commands), In a container.
- **`CLAUDE.md`**: "Gate commands" block (8 uv commands + `docker build`). Add the Kustomize line.
- **`_bmad-output/implementation-artifacts/deferred-work.md`**: two items name this story: `healthcheck` always probes `127.0.0.1` ("Document it or add `--host` with the Kubernetes manifests (story 3.3)") and "Editable install in the runtime image ... Consider `uv sync --no-editable` when stories 1.6/3.3 harden the image".
- **Repository facts checked on 2026-11-05:** `wixyvir/cvestream` is **private** and owned by a **User** (`gh api repos/wixyvir/cvestream`); an anonymous pull token for `ghcr.io/wixyvir/cvestream` is refused (401), so the package is private. `pyproject.toml` version is `0.1.0`.

### Release workflow sketch (verified on 2026-11-05: `actionlint` 1.7.x clean, `zizmor --offline` v1.32.0 "No findings")

```yaml
# Publishes a release when a version tag is pushed: checks that the tag is
# exactly v<pyproject.toml version>, builds the runtime image for amd64 and
# arm64, pushes X.Y.Z, X.Y and latest to GHCR with OCI labels, an SBOM and a
# max-mode provenance attestation, then creates the GitHub Release.
name: release

on:
  push:
    tags: ["v*"]

permissions: {}

concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false

defaults:
  run:
    shell: bash

jobs:
  check:
    name: check
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Tag matches the pyproject.toml version
        env:
          TAG: ${{ github.ref_name }}
        run: |
          version=$(python3 -I -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
          if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "::error::tag $TAG is not vX.Y.Z (pre-release tags are not published)"
            exit 1
          fi
          if [[ "$TAG" != "v$version" ]]; then
            echo "::error::tag $TAG does not match pyproject.toml version $version"
            exit 1
          fi

  image:
    name: image
    needs: check
    runs-on: ubuntu-24.04
    timeout-minutes: 60
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - id: name
        name: Image name (GHCR needs lower case)
        env:
          REPOSITORY: ${{ github.repository }}
        run: echo "image=ghcr.io/${REPOSITORY,,}" >> "$GITHUB_OUTPUT"
      - uses: docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1 # v4.4.0
      - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1
      - uses: docker/login-action@81c6f93d4bc991e510b342a00b419b3bb4efc5c6 # v4.7.0
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302 # v6.2.0
        with:
          images: ${{ steps.name.outputs.image }}
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
      - id: build
        uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0
        with:
          context: .
          target: runtime
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          annotations: ${{ steps.meta.outputs.annotations }}
          sbom: true
          provenance: mode=max
      # GitHub artifact attestations need a public repository (or GitHub
      # Enterprise Cloud for a private one); the SBOM and the provenance above
      # are BuildKit attestations stored with the image and work everywhere.
      - if: ${{ !github.event.repository.private }}
        uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6 # v4.2.2
        with:
          subject-name: ${{ steps.name.outputs.image }}
          subject-digest: ${{ steps.build.outputs.digest }}
          push-to-registry: true
          create-storage-record: false

  release:
    name: release
    needs: image
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    permissions:
      contents: write
    steps:
      - name: Create the GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ github.ref_name }}
        run: gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --verify-tag --generate-notes
```

Notes on the sketch:

- `type=semver` + the default `flavor: latest=auto` gives `X.Y.Z`, `X.Y` and `latest` for `v0.1.0` (`0.1.0`, `0.1`, `latest`). metadata-action never extends `{{major}}.{{minor}}` for a pre-release, and the `check` job refuses pre-release tags anyway. No `{{major}}` tag: major version zero (metadata-action README, "Major version zero").
- `id-token: write` and `attestations: write` stay on `image` even though the attest step is skipped on a private repository: making the repository public then needs no workflow change. If the reviewer prefers, drop the step and those two permissions (BuildKit attestations alone satisfy the AC).
- `steps.build.outputs.digest` is the digest of the multi-platform index; that is what `push-to-registry` attests.
- No `cache-from`/`cache-to`: a release build should not restore a GitHub Actions cache that a pull-request run could have written (cache poisoning). The build takes longer (QEMU arm64); `timeout-minutes: 60` covers it.
- `gh` is preinstalled on the runner; the `release` job needs no checkout (`--repo` + `--verify-tag`).
- Optional, not required by the AC: refuse a tag that is not on `main` (`git merge-base --is-ancestor "$GITHUB_SHA" origin/main` needs `fetch-depth: 0`). Only the owner pushes tags; keep it out unless trivial.

### Kubernetes design

#### Layout

```
deploy/
├── valkey/
│   ├── valkey.conf            # story 3.1
│   ├── users.acl              # story 3.1
│   └── kustomization.yaml     # NEW (3.3): ConfigMap cvestream-valkey-config + Secret cvestream-valkey-acl
└── k8s/
    ├── base/                  # cvestream alone, YAML provider
    │   ├── kustomization.yaml
    │   ├── configmap-settings.yaml
    │   ├── deployment.yaml
    │   ├── pvc.yaml
    │   ├── service-public.yaml
    │   ├── service-private.yaml
    │   └── networkpolicy.yaml
    └── overlays/example/      # namespace cvestream, Valkey provider, Valkey, Ingress
        ├── kustomization.yaml
        ├── ingress.yaml
        ├── valkey-statefulset.yaml   # headless Service + StatefulSet (+ its NetworkPolicy, or a separate file)
        └── secret.example.yaml
```

#### Kustomize load restrictor (verified with kubectl 1.28.2 / kustomize v5.0.4 on 2026-11-05)

- A `configMapGenerator` in `overlays/example` pointing at `../../../valkey/valkey.conf` fails: `security; file '.../deploy/valkey/valkey.conf' is not in or below '.../overlays/...'`.
- A directory under `resources:` may be anywhere: `../../../valkey` with its own `kustomization.yaml` works, and the overlay's StatefulSet references (`configMap: {name: cvestream-valkey-config}`, `secret: {secretName: cvestream-valkey-acl}`, also inside a `projected` volume) are rewritten to the hashed names (`cvestream-valkey-config-<hash>`). A change to `valkey.conf` or `users.acl` therefore rolls the Valkey pod.
- The other option (copies under `deploy/k8s/` + a test keeping them identical) is rejected: two copies of the ACLs is exactly what drifts.
- A `kustomization.yaml` inside `deploy/valkey/` is harmless for 3.1's compose service, which mounts that directory read-only into the Valkey container.

#### Patch forms (verified locally)

ConfigMap (strategic merge; `null` deletes a key):

```yaml
- patch: |-
    apiVersion: v1
    kind: ConfigMap
    metadata: {name: cvestream-settings}
    data:
      CVESTREAM_CONFIG_PROVIDER: valkey
      CVESTREAM_ENDPOINTS_FILE: null
      CVESTREAM_VALKEY__URL: valkey://cvestream-daemon@cvestream-valkey:6379/0
      CVESTREAM_VALKEY__PASSWORD_FILE: /secrets/valkey/password
```

Deployment (strategic merge; `volumeMounts` merge on `mountPath`, `volumes` on `name`):

```yaml
spec:
  template:
    spec:
      containers:
        - name: cvestream
          volumeMounts:
            - {mountPath: /config, $patch: delete}
            - {name: valkey, mountPath: /secrets/valkey, readOnly: true}
      volumes:
        - {name: endpoints, $patch: delete}
        - name: valkey
          secret: {secretName: cvestream-valkey, defaultMode: 0440}
```

NetworkPolicy: add the Valkey egress with a JSON 6902 `op: add, path: /spec/egress/-`, and replace the 8080 peer with `op: replace` on its `from` (index 0 of `/spec/ingress`, keep the base's rule order stable and say so in a comment).

#### Deployment (base)

Start from the harness Deployment (below) and keep its comments. Differences: image `ghcr.io/wixyvir/cvestream:0.1.0`, no `imagePullPolicy: Never`, no `namespace:` (the overlay sets it), the `endpoints` volume is the Secret `cvestream-endpoints`. Optional: a commented `imagePullSecrets` example (private GHCR package). Keep `startupProbe` `periodSeconds: 5`, `timeoutSeconds: 5`, `failureThreshold: 24` (120 s: covers the migration and Valkey's 60 s `startup_timeout`), `readinessProbe` 10 s / 5 s, `livenessProbe` 30 s / 5 s / 3. Resources `requests: {cpu: 50m, memory: 128Mi}`, `limits: {memory: 512Mi}` (no CPU limit).

#### NetworkPolicy design

- Base `cvestream`: `podSelector: {matchLabels: {app.kubernetes.io/name: cvestream}}`, `policyTypes: [Ingress, Egress]`.
  - Ingress 8080 (rule 0): from pods `cvestream.io/public-access: "true"` in any namespace (`namespaceSelector: {}` + `podSelector`). The operator labels the Ingress controller pods, or the overlay narrows the peer to the controller's namespace.
  - Ingress 9090 (rule 1): from pods `cvestream.io/private-access: "true"` in the same namespace (`podSelector` only). The probes are `exec`: the kubelet needs neither port. `kubectl port-forward` enters the pod network namespace directly and is not filtered (harness note).
  - Egress: DNS to `kube-system` pods `k8s-app: kube-dns` on UDP and TCP 53; TCP 443 to anywhere (the sources; cvestream ignores proxies, so the cluster needs direct egress).
- Example overlay:
  - cvestream: egress to pods `app.kubernetes.io/name: valkey` on TCP 6379; 8080 peer replaced by the Ingress controller (example: namespace `traefik`, pods `app.kubernetes.io/name: traefik`, as in the user's cluster and the harness; a comment says to change it).
  - Valkey (`cvestream-valkey`): `policyTypes: [Ingress, Egress]`, ingress 6379 from `app.kubernetes.io/name: cvestream` pods and pods labelled `cvestream.io/valkey-access: "true"` (connectors, a CLI pod), both same namespace; **no egress rule** (deny all: Valkey needs no outbound traffic). The kubelet's `tcpSocket` probes still work (Calico lets host traffic reach local pods; harness finding 4 shows the same for `httpGet`).
- Pod labels: cvestream `app.kubernetes.io/name: cvestream`; Valkey `app.kubernetes.io/name: valkey`. Never let one selector match the other's pods.

#### Valkey StatefulSet (example overlay)

- Headless `Service` `cvestream-valkey` (`clusterIP: None`, port 6379 named `valkey`), used as `serviceName` and as the client host. With `enableServiceLinks: false` on the cvestream pod, the `cvestream-` prefix injects nothing into `CVESTREAM_*`.
- `StatefulSet` `cvestream-valkey`: `replicas: 1`, image as 3.1 pins it in `compose.yml`/`ci.yml` (architecture: `valkey/valkey:9.1`; Docker Hub's 9.1 line is at 9.1.3 on 2026-11-05; a 9.2 RC exists, do not use it), `args: ["valkey-server", "/usr/local/etc/valkey/valkey.conf"]` (or the path 3.1's `valkey.conf`/`aclfile` imply).
- Security: the official Debian image creates `valkey` with UID/GID **999**, `WORKDIR /data`, entrypoint `tini -- docker-entrypoint.sh` which only `chown`s and drops privileges when started as root; started as 999 it runs directly (checked in `valkey-io/valkey-container` `9.1/debian/Dockerfile` and `docker-entrypoint.sh`). So: pod `runAsNonRoot`, `runAsUser/runAsGroup/fsGroup: 999`, `fsGroupChangePolicy: OnRootMismatch`, `seccompProfile: RuntimeDefault`, `automountServiceAccountToken: false`, `enableServiceLinks: false`; container `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]`.
- Volumes: `volumeClaimTemplates` `data` (RWO, 1Gi) on `/data` (AOF; check 3.1's `dir`); one `projected` volume on `/usr/local/etc/valkey` with the ConfigMap `cvestream-valkey-config` and the Secret `cvestream-valkey-acl` (read-only, `defaultMode: 0440`). Valkey only reads `aclfile` at start (nobody should run `ACL SAVE`; the mount is read-only anyway).
- Probes: `tcpSocket` on 6379 (no credentials needed; `default` is off, so `valkey-cli ping` would answer `NOAUTH`).
- Admin access: `kubectl -n <ns> exec -it cvestream-valkey-0 -- valkey-cli --user cvestream-admin --pass <p>` (inside the pod, not subject to NetworkPolicy). The operator CLI from story 3.2 can later run in a pod labelled `cvestream.io/valkey-access: "true"`.

### Dependency on story 3.1 (developed in parallel)

- This branch starts from `story-3-1-valkey-configuration-provider` at the plan commit `6c02c2c`. Do **not** depend on 3.1's unmerged Python code. The only file-level dependency is `deploy/valkey/valkey.conf` + `deploy/valkey/users.acl` (paths fixed by architecture.md), wired through `deploy/valkey/kustomization.yaml`.
- At the start of the dev step, run `git fetch origin && git ls-tree origin/story-3-1-valkey-configuration-provider deploy/valkey/`:
  - **Present:** rebase onto the current 3.1 tip first (`git rebase origin/story-3-1-valkey-configuration-provider`, then `git push --force-with-lease`), so the overlay builds for real and the setting names, `aclfile`/`dir` paths and image tag can be read from 3.1's files. The wave 9 caveat in `sprint-status.yaml` asks for exactly this.
  - **Absent:** write everything against the architecture names. To check the overlay build locally, create **untracked** stand-ins (`printf 'appendonly yes\n' > deploy/valkey/valkey.conf; printf 'user default off\n' > deploy/valkey/users.acl`), build, then delete them. **Never commit them**: an add/add conflict with 3.1 would follow. Commit the Valkey wiring (`deploy/valkey/kustomization.yaml` + the overlay) as its own last commit; the PR's `ci` check will fail on the overlay build until the rebase onto 3.1 (the PR is a draft; say so in the Completion Notes).
- After the rebase onto the **reviewed** 3.1 (done by the workflow before this story's review), check and adjust in a `fix(story-3.3)` commit if needed: `valkey.conf`'s `aclfile` path and `dir` vs the mount paths; the `users.acl` password format (`#<sha256>` or `>plaintext`) and the doc text about the Secret; 3.1's setting names (`settings.example.yaml`: `config_provider: valkey`, `valkey.url` with or without the user name, `CVESTREAM_VALKEY__PASSWORD_FILE`, `valkey.startup_timeout`); the Valkey image tag; then `kubectl kustomize deploy/k8s/overlays/example` again.

### Parallel work with story 3.1 (files both stories may touch)

| File | 3.1 | 3.3 | Conflict risk |
|---|---|---|---|
| `.github/workflows/ci.yml` | `valkey/valkey:9.1` service, `valkey` tests | one Kustomize step | Likely, easy: keep both in the one `ci` job |
| `docs/operations.md` | Valkey provider, exit code 3, outage behaviour | new `## Kubernetes` and `## Releases and image tags` sections, TL;DR bullets, `:edge` row | Medium: keep both, re-read the TL;DR and section order |
| `README.md` | status paragraph, Valkey mentions, maybe gate list | Kubernetes quick start, releases paragraph, gate list | Medium (gate list) |
| `CLAUDE.md` | maybe the `valkey` test command in the gate list | Kustomize gate line | Low/medium |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | `epic-3`, `3-1-...` | `epic-3`, `3-3-...` | Trivial: keep both stories' lines, `epic-3: in-progress` |
| `_bmad-output/implementation-artifacts/deferred-work.md` | appends, maybe the "reload waits for a fetch" item | the two 3.3 items | Low |
| `_bmad-output/planning-artifacts/architecture.md` | maybe (provider details) | tree, Actions line, release row | Low |
| `deploy/valkey/*` | `valkey.conf`, `users.acl` | `kustomization.yaml` only | None if stubs are never committed |
| `compose.yml` (root) | `valkey` service | untouched | None |
| `deploy/compose/compose.yml` | maybe (self-hosted Valkey) | image tag + comment | Low |
| `pyproject.toml`, `uv.lock` | `valkey` dependency | untouched | None (on conflict: base `uv.lock`, then `uv lock`) |

### Decisions taken autonomously (the stack plan lists them as points for a human decision)

Recorded so the reviewer and the user can overturn them.

1. **Valkey files reach the overlay through `deploy/valkey/kustomization.yaml`** listed under the overlay's `resources:` (load restrictor; see above). Not copies, not `LoadRestrictionsNone`.
2. **Base = YAML provider, example overlay = Valkey provider.** The base is the smallest working deployment (no Valkey, like `deploy/compose`); the overlay shows the full target setup and is what the manual minikube test exercises (seed with `valkey-cli`, scale Valkey to 0 → exit 3).
3. **Image pinned to `0.1.0`** in the base and in `deploy/compose/compose.yml`, enforced by a test against `pyproject.toml`. Consequence: until the owner pushes `v0.1.0` after the merges, `0.1.0` does not exist on GHCR (the self-hosted quick start fails until then). The release process says: bump the version and the pins in one PR, merge, tag.
4. **Generic `Ingress`** in the example overlay (`ingressClassName` example value `traefik`), with a comment pointing to Traefik's `IngressRoute` + `observability.accessLogs: false` (harness finding 3) for access-log hygiene. The user's own cluster uses IngressRoutes; ingress-nginx was retired in March 2026.
5. **NetworkPolicy peers by label** (`cvestream.io/public-access`, `cvestream.io/private-access`, `cvestream.io/valkey-access`) in the base; the example overlay narrows 8080 to the `traefik` namespace. Egress: DNS + TCP 443 anywhere (no CIDR list: the source hosts are behind CDNs).
6. **Pre-release tags refused** by the version check (only `vX.Y.Z`). PEP 440 (`0.2.0rc1`) and SemVer (`0.2.0-rc.1`) spell pre-releases differently, and a refused tag cannot move `latest` or `X.Y`. Pre-releases can be added later with an explicit rule.
7. **Release notes:** `gh release create --generate-notes` (GitHub's PR-based notes). No `CHANGELOG.md` in this story.
8. **GitHub artifact attestation only on a public repository**; SBOM and provenance are BuildKit attestations, verifiable with `docker buildx imagetools inspect <image>:<tag> --format '{{ json .Provenance }}'` and `'{{ json .SBOM }}'`. `actions/attest` (v4.2.2) is used instead of `actions/attest-build-provenance`, which upstream now documents as a wrapper ("new implementations should use `actions/attest`"); amend architecture.md's Actions line.
9. **`cvestream healthcheck --host`: not added.** The manifests keep `http.private.host` at its default `0.0.0.0`, so the `127.0.0.1` probe answers; `docs/operations.md` says that binding the private listener to one specific non-loopback address breaks the probes. Close the deferred item as "documented".
10. **Editable install in the runtime image: kept for later.** `uv sync --no-editable` changes how the package and its Alembic migrations land in the image, the `dev` stage shares the `builder` stage, and the image cannot be built on this machine (ghcr.io x509 behind the proxy) while CI only smoke-tests `--help`. Record it in `deferred-work.md` as kept, with that reason.
11. **No build cache in `release.yml`** (cache poisoning), unlike `ci.yml`.

### What the docs must say

`docs/operations.md`, `## Kubernetes`:

- What ships: `deploy/k8s/base` (cvestream, YAML provider) and `deploy/k8s/overlays/example` (namespace `cvestream`, Valkey provider, example Valkey, Ingress, Secret example). Build with `kubectl kustomize`, apply with `kubectl apply -k`. Make your own overlay rather than editing the example (namespace, image tag, Ingress host/class/TLS, NetworkPolicy peers, storage class, Secrets).
- Prerequisites: create the namespace with `pod-security.kubernetes.io/enforce=restricted`; a CNI that enforces NetworkPolicy; direct HTTPS egress (no proxy support); a block-storage `StorageClass` (never NFS/SMB/CIFS) for the SQLite PVC.
- Secrets: YAML provider → `kubectl create secret generic cvestream-endpoints --from-file=endpoints.yaml=<file>` (mounted as the `/config` directory, so an update reaches the pod and the daemon reloads at its next cycle; never `subPath`); Valkey provider → `cvestream-valkey` (`password` of `cvestream-daemon`) and the ACL Secret generated from `users.acl` (production: generate your own `users.acl`, e.g. in your overlay with a `secretGenerator` `behavior: replace`). Never commit real values.
- Private image: the GHCR package of a private repository is private; add an `imagePullSecrets` (a `docker-registry` Secret with a token that has `read:packages`) or make the package public.
- Invariants and why: 1 replica + `Recreate` (SQLite single writer, RWO volume), `terminationGracePeriodSeconds: 60`, `enableServiceLinks: false` (Services named `cvestream-*` would inject `CVESTREAM_*_PORT` variables that pydantic-settings reads), `automountServiceAccountToken: false`, read-only root with writable `/data` and `/tmp`, exec probes (`startupProbe`/`readinessProbe` `--ready`, `livenessProbe` plain). A settings ConfigMap change needs `kubectl rollout restart`.
- Health probes and the private listener: keep `http.private.host` at `0.0.0.0` (or `127.0.0.1`); `cvestream healthcheck` asks `127.0.0.1`, so binding a pod IP breaks the probes; `--port` if you change the port.
- Exposure: only `cvestream-public` behind an Ingress, only `/feeds/`; access logs contain the token (Traefik: `observability.accessLogs: false` on the route; others: mask or disable path logging for `/feeds/`). `cvestream-private` is never exposed; reach it with `kubectl port-forward svc/cvestream-private 9090` or from a pod labelled `cvestream.io/private-access: "true"`.
- NetworkPolicy labels: `cvestream.io/public-access`, `cvestream.io/private-access`, `cvestream.io/valkey-access`, and what each opens.
- Valkey: example only (single instance, AOF on its PVC, no TLS inside the cluster); admin through `kubectl exec ... valkey-cli --user cvestream-admin`; daemon behaviour when Valkey is down (exit 3 after `valkey.startup_timeout` at start, `CrashLoopBackOff`; keeps the last configuration while running). Or point `CVESTREAM_VALKEY__URL` at an existing Valkey.
- Backup in Kubernetes: `kubectl exec deploy/cvestream -- cvestream db backup /data/backup-<date>.db` then `kubectl cp`.

`docs/operations.md`, `## Releases and image tags`:

- Tags: `X.Y.Z`, `X.Y`, `latest` (release), `edge` (each push to `main`), `sha-<short>`; pin `X.Y.Z` in production.
- Release process: in a PR, bump `[project].version` in `pyproject.toml`, run `uv lock` (the lock records the project version), bump the image tag in `deploy/k8s/base/deployment.yaml` and `deploy/compose/compose.yml` (the test enforces it); merge; then `git tag vX.Y.Z origin/main && git push origin vX.Y.Z`. `release.yml` checks the tag, builds amd64 + arm64, pushes, attests, creates the Release. A wrong tag fails before anything is pushed; delete it (`git push origin :refs/tags/vX.Y.Z`) and tag again.
- Verify: `docker buildx imagetools inspect ghcr.io/wixyvir/cvestream:X.Y.Z` (two platforms, SBOM and provenance), and on a public repository `gh attestation verify oci://ghcr.io/wixyvir/cvestream:X.Y.Z --repo wixyvir/cvestream`.

`README.md`: a `## Kubernetes quick start` (5-8 lines: namespace with the PSS label, Secret, `kubectl apply -k deploy/k8s/overlays/example` or your own overlay, pointer to `docs/operations.md#kubernetes`), one paragraph on releases and tags, the gate line.

Write it in the style of the existing docs (short sentences, tables where they help). The docs themselves do not carry `:factory:` (existing convention); the PR body does.

### Harness input (`origin/test-early-k8s`, `test-k8s/`, read on 2026-11-05)

The user's minikube harness (Calico, Traefik v3.6.15, `restricted` PSS on `apps`, image `cvestream:test-k8s`, `imagePullPolicy: Never`) already runs cvestream with the YAML provider; its `test-k8s/manifests/cvestream/*.yaml` are the starting point of `deploy/k8s/base`. Read them with `git show origin/test-early-k8s:test-k8s/manifests/cvestream/deployment.yaml` (and `networkpolicy.yaml`, `pvc.yaml`, `service-*.yaml`, `configmap-settings.yaml`, `ingressroute.yaml`). Carry over:

- Deployment: everything in AC 5, including `fsGroupChangePolicy: OnRootMismatch`, the Secret `defaultMode: 0440` (owner root, group 999), `emptyDir` `/tmp` with `sizeLimit: 64Mi`, the probe timings, the comments explaining `Recreate`, `enableServiceLinks: false` and the 5 s probe timeouts.
- Findings that apply here: (3) feed tokens in access logs → `observability.accessLogs: false` on a Traefik route; (4) deny-all ingress keeps kubelet probes working on Calico; `kubectl port-forward` is not filtered by NetworkPolicy; ingress-nginx retired in March 2026; the cluster needs direct HTTPS egress (cvestream ignores proxies).
- Not carried over: the harness's `PT2M` intervals (production default `PT15M`), the namespace `apps`, the Traefik deployments and RBAC, the `private` namespace with its ExternalName Service (findings 1 and 2 are about the user's Traefik setup, wixyvir/services#1, not about cvestream's manifests).
- `smoke.sh` targets the harness namespace `apps`; the stack plan (`epic-3-stack-plan.md` section 3, "PR 2 of 3", step 3) describes how the user reuses the harness cluster to apply this overlay in a namespace `cvs33` through a throwaway overlay (`images:` → `cvestream:test-k8s`, `imagePullPolicy: Never`). Keep the base image name exactly `ghcr.io/wixyvir/cvestream` so that `images: - name: ghcr.io/wixyvir/cvestream` matches.

### Testing standards for this story

- **`tests/unit/test_deploy_manifests.py`** (one module, like `tests/unit/test_pytest_setup.py` which also tests the repository rather than a `src/` module): locate the repo root from `__file__`; read `pyproject.toml` with `tomllib`; `yaml.safe_load` / `safe_load_all` the raw files (no `kubectl` in pytest, no network). Tests:
  - the base Deployment's image is `ghcr.io/wixyvir/cvestream:<version>`; `deploy/compose/compose.yml`'s `services.cvestream.image` too;
  - Deployment invariants: `replicas == 1`, `strategy.type == "Recreate"`, `terminationGracePeriodSeconds == 60`, `enableServiceLinks is False`, `automountServiceAccountToken is False`, pod `runAsNonRoot`/`runAsUser == 999`, container `readOnlyRootFilesystem`, `allowPrivilegeEscalation is False`, `capabilities.drop == ["ALL"]`, the three probe commands, no `subPath` in any volume mount.
  Use plain asserts with readable messages; parametrize where it helps. Typed (ty runs on tests too), ruff-clean.
- Do not parse `release.yml` in pytest (story 2.2 precedent): `actionlint` + `zizmor` are its checks, plus the local dry run of the version script (Task 1.7).
- Kustomize: both builds must succeed with the plain command; read the overlay output once against AC 7. `kubeconform` is optional (not installed).
- Not runnable here and left to the user (stack plan, "PR 2 of 3", steps 3 and 5): the minikube deployment of the overlay and the release dry-run in a throwaway repository. Say in the Completion Notes that they were not run. Never push a `v*` tag to `wixyvir/cvestream`.
- `uv run pytest` count: base count + the new tests; `live` still deselected; the `valkey` tests are 3.1's.

### Previous story intelligence

- **2.2 (source-drift workflow):** the established workflow style: top-level `permissions: {}`, per-job least privilege, same SHAs as `ci.yml`, `persist-credentials: false`, `timeout-minutes`, `defaults.run.shell: bash` (gives `pipefail`), values into scripts only through `env:`, `--repo "$GITHUB_REPOSITORY"` on every `gh` call; verify with `uvx --from actionlint-py actionlint` and `uvx zizmor --offline` (`SSL_CERT_FILE=$HOME/ca.pem` for `uvx` downloads behind the proxy). Workflows triggered off-PR cannot be tested before merge: list what the user must check.
- **1.6 (daemon, compose):** `deploy/compose/compose.yml` hardening and the directory-mount rule for hot reload; `docs/operations.md` already documents the 60 s grace period, the exec probes and the token-in-access-logs warning: extend, do not duplicate.
- **1.1 (CI):** `ci` must stay the only required check; `ci.yml` has no `branches:` filter on `pull_request` (stacked PRs); the image smoke test only runs `--help`.
- **Delivery precedent (epic 2):** the upper story of a wave is created from the lower branch at the plan commit, developed in parallel, rebased onto the reviewed lower branch, then reviewed; shared-file conflicts were trivial when each story kept to its own sections.
- Commits: `feat|fix|docs|test|ci|build|chore(story-3.3): ...`, one concern each, never squashed, each message ending with `🤖 Généré avec Claude Code`.

### Latest technical information (checked on 2026-11-05)

- Actions releases: `actions/checkout` v7.0.1, `docker/setup-qemu-action` v4.4.0, `docker/setup-buildx-action` v4.4.1, `docker/metadata-action` v6.2.0, `docker/build-push-action` v7.4.0 (inputs `sbom`, `provenance`, `annotations`, `platforms` confirmed in its `action.yml`), `docker/login-action` v4.8.0 published today (ci.yml pins v4.7.0: keep ci.yml's SHA, Dependabot will bump both), `actions/attest` v4.2.2 (`1e69f48acb82d1966a394da916b4c1698aa569d6`), `actions/attest-build-provenance` v4.2.2 (`4d101475d8b20a2381f78447822ac1eab6504dd8`, now a wrapper of `actions/attest`).
- `actions/attest`: permissions `id-token: write` + `attestations: write` (+ `packages: write` for `push-to-registry`; + `artifact-metadata: write` only for storage records, which need an org-owned repository); "To use artifact attestations in private or internal repositories, you must be on a GitHub Enterprise Cloud plan"; `subject-name` must be the fully-qualified image name **without a tag**.
- `docker/metadata-action` v6.2.0: `type=semver` needs a valid SemVer tag; `latest=auto` adds `latest` for `type=semver`; pre-releases only extend `{{version}}`; "Major version zero" section recommends not emitting `{{major}}` for `0.y.z`.
- `gh release create`: `--verify-tag` (abort if the tag does not exist remotely), `--generate-notes`, `--latest` (automatic by default).
- GitHub runner `ubuntu-24.04` image: Kubectl 1.37.1, Kustomize 5.8.3, Minikube 1.39.0, Kind 0.33.0 preinstalled. Local machine: kubectl 1.28.2 with kustomize v5.0.4 (old, but the features used here exist since kustomize v4/v5).
- `valkey/valkey` image: Debian `9.1` line at 9.1.3, user `valkey` UID/GID 999, `WORKDIR /data`, `EXPOSE 6379`, `CMD ["valkey-server"]`, entrypoint drops root only when started as root. 9.2 is at RC.
- Kubernetes fields used (all stable core API, already exercised by the harness on minikube): `enableServiceLinks`, `automountServiceAccountToken`, `fsGroupChangePolicy`, `seccompProfile`, `projected` volumes with `configMap` and `secret` sources, `emptyDir.sizeLimit`, `networking.k8s.io/v1` `NetworkPolicy` and `Ingress` (`pathType: Prefix`), `apps/v1` `StatefulSet` `volumeClaimTemplates`.

### Delivery (stacked PR)

- Branch `story-3-3-release-and-kubernetes-deployment`, PR base `story-3-1-valkey-configuration-provider` (draft PR opened at story creation), final base after the 3.1 merge: `main`.
- Suggested commits: `ci(story-3.3): add the release workflow`, `feat(story-3.3): add the Kubernetes base manifests`, `feat(story-3.3): add the example Kubernetes overlay with Valkey` (the 3.1-dependent part), `test(story-3.3): guard deployment invariants and release pins`, `build(story-3.3): pin the self-hosted compose image to the release`, `ci(story-3.3): validate the Kustomize build`, `docs(story-3.3): document Kubernetes deployment and releases`, `docs(story-3.3): amend architecture for releases and manifests`, `chore(story-3.3): mark story ready for review`. Every message ends with `🤖 Généré avec Claude Code`.
- Never push to `main`, never push a tag, never create a Release; `git push --force-with-lease` only after a rebase.

### Project Structure Notes

- Matches the architecture tree (`deploy/k8s/base/*`, `deploy/k8s/overlays/example/{kustomization,ingress,valkey-statefulset,secret.example}.yaml`, `.github/workflows/release.yml`). Additions to record in architecture.md: `deploy/valkey/kustomization.yaml`; any extra overlay file you split out (Valkey NetworkPolicy, patches).
- `tests/unit/test_deploy_manifests.py` does not mirror a `src/` module; precedent `tests/unit/test_pytest_setup.py`. Mention it in architecture.md's tree only if the tree lists such files.
- No new top-level directory, no `utils`, no tool config outside `pyproject.toml` (the Kustomize files are deployment configuration under `deploy/`, as the architecture says).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.3: Release and Kubernetes deployment]
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (image tags, delivery workflow, Actions versions, workflow table, deployment targets, scaling)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] (container hardening, Valkey ACL users, access logs)
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns] (`cvestream healthcheck`, startup behaviour with Valkey, exit code 3)
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure] (`deploy/` tree)
- [Source: _bmad-output/project-context.md#Development Workflow Rules] (release, deployment invariants, GitHub Actions)
- [Source: _bmad-output/implementation-artifacts/epic-3-stack-plan.md] (stack, schedule, "Kustomize load restrictor", expected conflicts, "PR 2 of 3" test steps, points needing a decision)
- [Source: _bmad-output/implementation-artifacts/sprint-status.yaml] (wave 9 caveat: rebase 3-3 on 3-1 before finishing it)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] (healthcheck `127.0.0.1`; editable install)
- [Source: _bmad-output/implementation-artifacts/2-2-source-drift-detection.md] (workflow conventions and local verification)
- [Source: origin/test-early-k8s:test-k8s/README.md] and `test-k8s/manifests/cvestream/*.yaml` (harness manifests and findings)
- [Source: .github/workflows/ci.yml], [Source: Dockerfile], [Source: deploy/compose/compose.yml], [Source: src/cvestream/cli/healthcheck.py], [Source: docs/operations.md], [Source: README.md], [Source: CLAUDE.md]
- External: `docker/metadata-action` README v6.2.0 (`type=semver`, "Latest tag", "Major version zero"); `actions/attest` README v4.2.2 (permissions, private repositories, container images, storage records); `docker/build-push-action` `action.yml` v7.4.0; `gh release create --help`; `actions/runner-images` `images/ubuntu/Ubuntu2404-Readme.md`; `valkey-io/valkey-container` `9.1/debian/Dockerfile` and `docker-entrypoint.sh`; Kubernetes docs (Pod Security Standards "restricted", NetworkPolicy, StatefulSet, projected volumes); Kustomize docs (`resources`, generators, load restrictor, `patches`).

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created (bmad-create-story, claude-opus-5-5, autonomous run, 2026-11-05).

### File List
