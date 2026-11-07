# Sprint Change Proposal: Valkey administration UI (Story 3.4)

- **Date:** 2026-11-07
- **Trigger:** found while manually testing the epic 3 stack (`main` <- wixyvir/cvestream#22 <- wixyvir/cvestream#21 <- wixyvir/cvestream#23): no shipped Valkey user can browse the configuration store, so no off-the-shelf administration UI works with it
- **Decision:** add Story 3.4 to Epic 3. It ships an off-the-shelf web UI for Valkey that is **on by default in the dev compose stack** and **opt-in on Kubernetes** (a Kustomize component), and connects as a new `cvestream-ui` ACL user with **read/write access to `cvestream:v1:*`, tokens included**
- **Scope:** Moderate: a new story at the top of the epic 3 stack, plus wording changes in the PRD, the architecture and the project context
- **Mode:** batch

## TL;DR

- `deploy/valkey/users.acl` gives each user an explicit command list: `GET`, `MGET`, `SMEMBERS`, `SET`, `WATCH`/`MULTI`/`EXEC` and so on. `SCAN`, `TYPE`, `TTL` and `INFO` are denied to everyone, and `default` is off. A GUI such as Redis Insight, Tiny RDM or Redis Commander that connects as `cvestream-admin` therefore shows only `NOPERM` errors and an empty key tree.
- The only workaround today is a scratch ACL file with a `+@all` debug user. That is fine for a throwaway container and must never be shipped.
- **Story 3.4** adds:
  - a dedicated `cvestream-ui` ACL user, with admin rights plus the browse commands the UI needs;
  - a pinned web UI service in `compose.yml`, started by default, published on `127.0.0.1` only and pre-connected;
  - an opt-in Kustomize component `deploy/k8s/components/valkey-ui` (ClusterIP Service, no Ingress, access by `kubectl port-forward`, hardened pod, NetworkPolicy);
  - tests and docs.
- **Known trade-off (accepted):** UI writes bypass the CLI's validation and the `WATCH`/`INCR revision` protocol. The daemon still catches them:
  - the 12-run forced reload from story 3.1 picks up an edit that did not bump the revision;
  - FR-23 rejects an invalid edit and keeps the last valid configuration.

  The docs say to prefer the CLI and to `INCR cvestream:v1:revision` after a UI edit.
- **PRD:** the out-of-scope item "A graphical configuration interface" is clarified to mean a GUI **built into cvestream**. The story deploys a third-party tool, which that item does not exclude.
- **Order:** wave 11, after 3.2. The PR stacks on top of 3.2: `main <- 3.1 <- 3.3 <- 3.2 <- 3.4`. Epic 3 stays `in-progress` until 3.4 is `done`.

## 1. Issue summary

**Problem.** The ACL design in story 3.1 is least-privilege on purpose. Every user gets an explicit command list, and `+@read`/`+@write` are banned because they include `KEYS`, `SCAN` and `FLUSHALL`. As a side effect, no user can enumerate or inspect keys:

```
user cvestream-admin on #<sha256> resetchannels ~cvestream:v1:* -@all +get +mget +smembers +exists +set +del +sadd +srem +incr +incrby +watch +unwatch +multi +exec +discard +ping +select +client|setinfo
```

A GUI's key browser calls `SCAN` (plus `TYPE`, `TTL`, `STRLEN`/`SCARD` and often `INFO`, `DBSIZE` and `CLIENT SETNAME`). All of these are denied, so the GUI is unusable with every shipped user.

**Evidence.** Reproduced on 2026-11-07 against `valkey/valkey:9.1` started with `deploy/valkey/` from the 3.1 branch:

- `valkey-cli` with no user returns `NOAUTH`;
- browse commands as `cvestream-admin` return `NOPERM`;
- the store becomes browsable only after adding `user debug on >debug ~* &* +@all` to a scratch copy of `users.acl`. That line has a plaintext password and every command, so it must never be committed.

**Category:** a new operator need that emerged while testing. Inspecting the store meant raw `valkey-cli` with the right user and password on every call.

## 2. Impact analysis

### Checklist

| Item | Status | Notes |
|---|---|---|
| 1.1 Triggering story | [x] | Manual test of story 3.1 (wixyvir/cvestream#22), with the epic 3 stack checked out |
| 1.2 Core problem | [x] | No ACL user can browse the store, so no administration UI works |
| 1.3 Evidence | [x] | Reproduced with `valkey/valkey:9.1` and the repo's `users.acl` (section 1) |
| 2.1 Current epic | [x] | Epic 3 can still finish as planned; it gains one story |
| 2.2 Epic-level change | [x] | Add Story 3.4 to Epic 3 |
| 2.3 Future epics | [N/A] | Epic 3 is the last epic in `epics.md` |
| 2.4 New or obsolete epics | [N/A] | None |
| 2.5 Epic order | [x] | Wave 11 (3.4) after wave 10 (3.2) |
| 3.1 PRD | [!] | "Out of Scope (v1): A graphical configuration interface" needs a clarification (section 4). No new FR: this is tooling around FR-21 and FR-23 |
| 3.2 Architecture | [!] | Valkey ACL users and their rules, the interface list, deployment targets and the project tree change (section 4) |
| 3.3 UX | [N/A] | No UX specification. The UI is a third-party product, not designed here |
| 3.4 Other artifacts | [!] | `deploy/valkey/users.acl`, `dev-secrets/`, `compose.yml`, a new `deploy/k8s/components/valkey-ui/`, CI Kustomize build, `tests/unit/test_deploy_*`, Valkey integration tests, `docs/config-contract.md`, `docs/operations.md`, README, `project-context.md`, the epic 3 stack plan |
| 4.1 Direct adjustment | Viable | Effort: medium. Risk: low to medium (a broader ACL user, a new third-party image) |
| 4.2 Rollback | Not viable | Nothing to roll back: the restrictive ACLs are correct |
| 4.3 MVP review | Not viable | The MVP is unaffected; this is operator comfort and safety |
| 4.4 Path | [x] | Direct adjustment: new Story 3.4 |

### Epic and story impact

- **Epic 3:** gains Story 3.4. The epic description adds "an optional administration UI for the store". FR coverage doesn't change.
- **Stories 3.1, 3.2 and 3.3:** not reopened. 3.4 builds on their files: `users.acl` and the Valkey test helpers from 3.1, the overlay and `test_deploy_manifests.py` from 3.3, and the CLI the docs point to from 3.2.

### Technical impact

- **New ACL user `cvestream-ui`.** It is separate from `cvestream-admin` so that the CLI keeps a minimal command list and the UI password can be revoked on its own.
  - Keys: `~cvestream:v1:*` read/write, tokens included (operator decision 2026-11-07).
  - Commands: the admin list plus the browse commands the chosen UI needs, settled empirically by the story. Expected: `SCAN`, `TYPE`, `TTL`, `PTTL`, `STRLEN`, `SCARD`, `SSCAN`, `DBSIZE`, `INFO`, `CLIENT SETNAME`.
  - Never granted: `KEYS`, `FLUSHALL`, `FLUSHDB`, `CONFIG`, `ACL`, `DEBUG`, `MODULE`, `EVAL*`/`FUNCTION`, `SHUTDOWN`, `REPLICAOF`, `MIGRATE`.
  - `SCAN` is not bound to key patterns, so it lists every key name in the DB. That is acceptable: the DB holds only `cvestream:v1:*`, and a token key name carries only the endpoint id.
- **Bypassing the write protocol.** A UI write neither bumps `revision` nor validates. Effects:
  - a missing bump: the edit applies at the latest on the next forced reload (`FULL_RELOAD_EVERY = 12` runs, story 3.1);
  - an invalid edit: `config.rejected` is logged and the last valid configuration is kept (FR-23).

  No code change is needed. The docs tell the operator to prefer the CLI and to `INCR cvestream:v1:revision` (from the UI's command console) after an edit, for an immediate reload.
- **Dev compose.** The `config` network is `internal: true`, and an internal network can't publish a port. The UI service therefore joins `config` (to reach Valkey) and `default` (to publish `127.0.0.1:<port>` only). If the UI has telemetry, it is disabled, and so is any first-run EULA or analytics prompt, if the image allows it.
- **Kubernetes.** A Kustomize `Component` under `deploy/k8s/components/valkey-ui/`, which the example overlay does **not** list:
  - a `Deployment` that passes the `restricted` Pod Security Standard;
  - a ClusterIP `Service`, with no `Ingress`;
  - a `NetworkPolicy` that allows UI egress to Valkey `6379` and DNS only, plus a patch to `networkpolicy-valkey.yaml` that admits the UI pod;
  - a placeholder Secret for the `cvestream-ui` password.

  CI builds the example overlay with the component enabled through a test-only overlay, so the component cannot rot.
- **Image choice.** One web UI, pinned to an exact version (never `latest`), with a licence that allows this use, and support for a pre-configured connection with a user name and a password read from a file or the environment. Candidates to evaluate, with the result recorded in the story:
  - Redis Insight (`redis/redisinsight`, port 5540);
  - Redis Commander;
  - any Valkey-native web UI available at story time.

  Per the operator's rules, each environment variable or option the story uses for the chosen image is checked online before use.
- **Pin drift:** Dependabot doesn't bump image pins in Kustomize or compose (already in `deferred-work.md` for the Valkey pins). The UI pin joins that item.

## 3. Recommended approach

**Direct adjustment: add Story 3.4 to Epic 3, at the top of the PR stack.**

- It needs 3.1 (`users.acl`, the test helpers) and 3.3 (`deploy/k8s/`, the manifest tests). Basing it on 3.2 keeps the stack linear (`main <- 3.1 <- 3.3 <- 3.2 <- 3.4`) and avoids conflicts on `sprint-status.yaml`, `operations.md` and `README.md`.
- 3.1, 3.3 and 3.2 can still merge without waiting for 3.4. If they merge first, the 3.4 PR is retargeted to `main`.
- **Effort:** medium. Mostly deployment files, ACL tests and docs, with no application code.
- **Risk:** low to medium.
  - A new read/write user is a new credential to protect. It ships with a public dev password, and production gets its own hash, as for connectors.
  - A third-party image enters the dev stack.
  - The Kubernetes part is opt-in.

## 4. Detailed change proposals

### PRD (`prds/prd-cvestream-2026-10-26/prd.md`, Out of Scope (v1))

```
OLD:
- A graphical configuration interface.

NEW:
- A graphical configuration interface built into cvestream. An off-the-shelf Valkey administration UI may run next to the store (Story 3.4); it writes outside the CLI's validation, and FR-23 (keep the last valid configuration) covers its mistakes.
```

Rationale: the item excludes building a GUI, not deploying a third-party tool. Without the clarification, Story 3.4 would look like a scope violation.

### Architecture (`architecture.md`)

**Authentication & Security, Interfaces:**

```
OLD:
... Valkey is on a private network only. The CLI has no network endpoint.

NEW:
... Valkey is on a private network only. The CLI has no network endpoint. The optional Valkey administration UI (Story 3.4) is never exposed: loopback-only in the dev compose stack, ClusterIP plus `kubectl port-forward` on Kubernetes, no Ingress.
```

**Authentication & Security, Valkey ACL users** (insert after the `cvestream-admin` sentence, and amend the command-list rule):

```
OLD:
... `cvestream-admin` (CLI): read/write on `cvestream:v1:*`. `connector-<name>`: ...
... Rules: explicit command lists, never `+@read`/`+@write` (they include `KEYS`, `SCAN`, `FLUSHALL`); ...

NEW:
... `cvestream-admin` (CLI): read/write on `cvestream:v1:*`. `cvestream-ui` (administration UI, Story 3.4): read/write on `cvestream:v1:*`, tokens included, plus the browse commands the UI needs (`SCAN`, `TYPE`, `TTL`, ...); it is the only user allowed `SCAN`. `connector-<name>`: ...
... Rules: explicit command lists, never `+@read`/`+@write` (they include `KEYS`, `SCAN`, `FLUSHALL`); `KEYS`, `FLUSHALL`/`FLUSHDB`, `CONFIG`, `ACL`, `DEBUG`, `MODULE` and scripting are granted to no user; UI writes skip validation and the revision bump: the forced reload every 12 runs and `config.rejected` (FR-23) are the safety net; ...
```

**Infrastructure & Deployment, deployment targets:**

```
OLD:
  - Dev: `compose.yml` (`app` target `dev` with `develop.watch`, `valkey` with `deploy/valkey/users.acl`, private network) + `.devcontainer/` pointing to it.

NEW:
  - Dev: `compose.yml` (`app` target `dev` with `develop.watch`, `valkey` with `deploy/valkey/users.acl`, private network, `valkey-ui` web UI connected as `cvestream-ui` and published on `127.0.0.1` only) + `.devcontainer/` pointing to it.
```

and append to the Kubernetes bullet: "`deploy/k8s/components/valkey-ui` is an opt-in Kustomize component (Valkey administration UI: hardened `Deployment`, ClusterIP `Service`, `NetworkPolicy`, no Ingress); the example overlay does not enable it, and CI builds it through a test overlay."

**Project tree:** `users.acl` comment adds `cvestream-ui`; `dev-secrets/` says "four users"; under `deploy/k8s/` add:

```
│       ├── components/valkey-ui/      # opt-in Kustomize component: administration UI (Story 3.4)
```

### Project context (`project-context.md`)

```
OLD:
- Valkey: the `default` user stays disabled, the daemon user is read-only, and ACLs are never widened to cover `cvestream:v1:token:*`.

NEW:
- Valkey: the `default` user stays disabled, the daemon user is read-only, and connector ACLs never cover `cvestream:v1:token:*` (only `cvestream-daemon`, `cvestream-admin` and `cvestream-ui` read tokens). `KEYS`, `FLUSHALL`/`FLUSHDB`, `CONFIG`, `ACL` and scripting are granted to no user; `SCAN` only to `cvestream-ui`.
```

Also add the UI image to the stack line, once the story has chosen it.

### Epics (`epics.md`)

Epic list, Epic 3 summary:

```
OLD:
Configuration moves to Valkey so other tools (BoM connectors, CD workers) can edit product lists; the operator manages endpoints, products and tokens from the CLI with either provider; versioned images and Kubernetes manifests are published.

NEW:
Configuration moves to Valkey so other tools (BoM connectors, CD workers) can edit product lists; the operator manages endpoints, products and tokens from the CLI with either provider, and can inspect the store in an optional administration UI; versioned images and Kubernetes manifests are published.
```

The same sentence changes in the Epic 3 section header. Append after Story 3.3:

### Story 3.4: Valkey administration UI

As the operator,
I want a web UI to browse and edit the Valkey configuration store, ready to use in the dev stack and available on Kubernetes when I enable it,
So that I can inspect and fix the configuration without typing `valkey-cli` commands with a user and password each time.

**Acceptance Criteria:**

**Given** `deploy/valkey/users.acl`
**When** Valkey starts with it
**Then** a `cvestream-ui` user (SHA-256 hash, dev password in `deploy/valkey/dev-secrets/cvestream-ui.password`) can read and write every `cvestream:v1:*` key, tokens included, and run the browse commands the chosen UI needs (`SCAN`, `TYPE`, `TTL`, ...)
**And** it cannot touch keys outside `cvestream:v1:*` or run `KEYS`, `FLUSHALL`, `FLUSHDB`, `CONFIG`, `ACL`, `DEBUG`, `MODULE` or scripting; a `valkey`-marked integration test proves each grant and each refusal
**And** the existing users are unchanged

**Given** the dev stack
**When** the operator runs `docker compose up` (no profile, no extra variable)
**Then** a `valkey-ui` service starts with a pinned web UI image (exact version, licence recorded in the story), already connected to `valkey` as `cvestream-ui` with the password read from a compose secret, and published on `127.0.0.1` only; telemetry is disabled when the image offers it
**And** Valkey itself still publishes no host port

**Given** `deploy/k8s/components/valkey-ui`
**When** an overlay lists it under `components:`
**Then** `kubectl kustomize` produces a `Deployment` admitted under the `restricted` Pod Security Standard, a ClusterIP `Service`, no `Ingress`, a `NetworkPolicy` (UI egress to Valkey and DNS only) and a patch admitting the UI pod in the Valkey `NetworkPolicy`, with the password from a placeholder Secret
**And** the example overlay does not enable it, CI builds an overlay that does, and `tests/unit/test_deploy_manifests.py` guards these properties

**Given** a write from the UI
**When** it skips the revision bump or writes an invalid value
**Then** the daemon applies it at the latest on its next forced reload, or rejects it with `config.rejected` and keeps the last valid configuration (FR-23), as already implemented; no daemon change
**And** `docs/operations.md` ("Valkey administration UI") explains access (`http://127.0.0.1:<port>`, `kubectl port-forward`), recommends the CLI for writes and `INCR cvestream:v1:revision` after a UI edit, and how to add `cvestream-ui` to a production `users.acl` with its own hash; `docs/config-contract.md` lists the user; `compose.yml`'s header and `README.md` mention the UI

### Sprint status (`sprint-status.yaml`)

The status line lands with `bmad-create-story` in the 3.4 PR; the wave and dependency comments land on `main` now.

```
  epic-3: in-progress
  3-1-valkey-configuration-provider: done
  3-2-operator-cli-for-endpoints-products-and-tokens: done
  3-3-release-and-kubernetes-deployment: review
+ 3-4-valkey-administration-ui: backlog
  epic-3-retrospective: optional
```

Wave notes:

```
#     Wave 10: 3-2
+#     Wave 11: 3-4                  (course correction 2026-11-07, Valkey administration UI)
...
-#   3-1: 1-3, 1-6 · 3-2: 1-3, 3-1 · 3-3: 1-6
+#   3-1: 1-3, 1-6 · 3-2: 1-3, 3-1 · 3-3: 1-6 · 3-4: 3-1, 3-3
```

### Epic 3 stack plan (`implementation-artifacts/epic-3-stack-plan.md`)

- TL;DR and diagram: `main <- 3.1 <- 3.3 <- 3.2 <- 3.4`, 4 PRs; the 3.4 PR is based on the 3.2 branch.
- Table row for 3.4 (branch `story-3-4-valkey-administration-ui`, base: 3.2 branch, depends on 3.1 and 3.3).
- Section 3, "PR 4 of 4": ACL grants and refusals with `valkey-cli`; `docker compose up` then the UI on `127.0.0.1` browses `cvestream:v1:*`; a UI edit without `INCR` is applied by the forced reload; an invalid UI edit is rejected; on minikube, the component through a throwaway overlay, then `port-forward` and a check that the NetworkPolicy blocks other pods.
- Section 4: after merging 3.2, retarget 3.4 to `main`; on a lower-branch change, rebase 3.4 last.

## 5. Implementation handoff

**Where each change lands (approved 2026-11-07).** The epic 3 stack already changes `architecture.md`, `project-context.md`, `epic-3-stack-plan.md` and the epic 3 status lines of `sprint-status.yaml`. Changing them on `main` would make the stack conflict with `main`. So only the files the stack does not touch go to `main` now (this proposal, the PRD, `epics.md`, the sprint-status wave and dependency comments). The architecture and project-context amendments, the 3.4 status line and the stack plan go in the 3.4 PR, as the delivery rule already requires ("changing a pattern means amending `architecture.md` in the same PR").

**Scope: Moderate.** It adds a story to the backlog and moves the epic's stack. No replan.

| Step | Who | Deliverable |
|---|---|---|
| 1 | This workflow | One commit on `main`, `docs(story-3.4): add story 3.4 to epic 3 by course correction`: this proposal, the PRD clarification, Story 3.4 in `epics.md`, the wave 11 and dependency notes in `sprint-status.yaml`. The rest waits for the 3.4 PR (see below) |
| 2 | `bmad-create-story` | Branch `story-3-4-valkey-administration-ui` from the 3.2 branch (rebased onto `main` once the stack is merged); `3-4-valkey-administration-ui.md`, the `3-4-valkey-administration-ui: ready-for-dev` line in `sprint-status.yaml`, the 3.4 rows of `epic-3-stack-plan.md`; draft PR against the 3.2 branch |
| 3 | `bmad-dev-story` | Implementation, the `architecture.md` and `project-context.md` amendments above (same PR as the pattern change), gates, `valkey` tests against a disposable server, `kubectl kustomize` of the test overlay; status `review` |
| 4 | `bmad-code-review` | Review findings on the PR |
| 5 | Operator | Manual tests (stack plan, PR 4 of 4); merge bottom-up after 3.2 |

**Success criteria:**

- After `docker compose up` on a fresh clone, the UI on `127.0.0.1` lists and edits `cvestream:v1:*` with no manual setup.
- `cvestream-ui` can do exactly what the ACL tests say, and nothing in `KEYS`/`FLUSH*`/`CONFIG`/`ACL`/scripting.
- The example overlay's output is unchanged; the component builds in CI and deploys under the `restricted` Pod Security Standard.
- All gates are green; no plaintext password in any committed ACL file.

> :factory: Generated by Claude Code (bmad-correct-course) from the 2026-11-07 epic 3 manual test session, `deploy/valkey/users.acl`, `architecture.md`, `project-context.md`, `prd.md`, `epics.md`, `sprint-status.yaml` and `epic-3-stack-plan.md` on the 3.2 branch.
