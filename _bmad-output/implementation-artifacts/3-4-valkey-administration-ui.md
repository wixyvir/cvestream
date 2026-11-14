---
baseline_commit: f7bf4fc
---

# Story 3.4: Valkey administration UI

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a web UI to browse and edit the Valkey configuration store, ready to use in the dev stack and available on Kubernetes when I enable it,
so that I can inspect and fix the configuration without typing `valkey-cli` commands with a user and password each time.

## TL;DR

- **Why:** every user in `deploy/valkey/users.acl` has an explicit command list without `SCAN`, `TYPE`, `TTL` or `INFO`, and `default` is off. Any GUI therefore shows only `NOPERM` and an empty key tree. Source: sprint change proposal 2026-11-07, `planning-artifacts/sprint-change-proposal-2026-11-07-valkey-admin-ui.md`.
- **No Python application code changes.** The story adds:
  - one ACL line (`cvestream-ui`) and its dev secrets;
  - one compose service (`valkey-ui`);
  - one Kustomize `Component` (`deploy/k8s/components/valkey-ui`) and one test-only overlay that CI builds;
  - tests: unit tests on the raw files, plus `valkey` integration tests for each grant and refusal;
  - docs, the `architecture.md` and `project-context.md` amendments, and the stack plan rows.
- **Image chosen: Redis Insight `redis/redisinsight:3.8.0`** (released 2026-07-21, amd64 and arm64, port 5540, `USER node` = UID 1000, data in `/data`). Researched and checked in the 3.8.0 source on 2026-11-07; see "Image choice".
  - It is the only candidate that can be **pre-connected** with an ACL user name and with the password **read from a file**: `RI_PRE_SETUP_DATABASES_PATH` points to a JSON file.
  - `RI_ACCEPT_TERMS_AND_CONDITIONS=true` skips the EULA screen and turns analytics off. `RI_DATABASE_MANAGEMENT=false` locks the connection list.
  - Every introspection command it sends on connect is optional (it degrades on `NOPERM`).
  - **Licence: SSPL-1.0 (or the Redis EULA).** Running the unmodified image internally is allowed. Never vendor, modify or offer it as a service. Record this in the docs.
- **ACL:**
  - `cvestream-ui` = the `cvestream-admin` command list on `~cvestream:v1:*` (tokens included), plus the browse commands the UI needs. **Settle the list empirically with `ACL LOG`** (procedure in Dev Notes).
  - It is the **only** user with `SCAN`.
  - It never gets `KEYS`, `FLUSHALL`/`FLUSHDB`, `CONFIG`, `ACL`, `DEBUG`, `MODULE`, scripting, `SHUTDOWN`, `REPLICAOF`/`SLAVEOF`, `MIGRATE`, `MONITOR`, `SLOWLOG`, `CLIENT LIST`/`KILL`, `RENAME*`, or the `EXPIRE` family (a TTL on a config key would make the configuration vanish).
  - The other users stay byte-identical.
- **Dev compose:**
  - `valkey-ui` starts with a plain `docker compose up`, on the `config` network (to reach Valkey) and on `default` (to publish a port).
  - It is published on `127.0.0.1:5540` only and pre-connected through a compose secret (the JSON pre-setup file).
  - Valkey still publishes no port.
- **Kubernetes:**
  - The opt-in component holds a hardened `Deployment` (UID 1000, read-only root, `emptyDir` on `/data` and `/tmp`), a ClusterIP `Service`, a `NetworkPolicy` (ingress: none; egress: Valkey 6379 and DNS) and a placeholder `Secret`. It has no Ingress.
  - Access is `kubectl port-forward`, which NetworkPolicy does not filter.
  - **The UI pod is admitted by the Valkey NetworkPolicy through the existing opt-in label `cvestream.io/valkey-access: "true"`** (decision 4). The example overlay does not list the component. A test overlay under `tests/k8s/` does, and CI builds it.
- **UI writes skip validation and the revision bump, and nothing changes in the daemon to cover that.** Two existing safety nets do:
  - the forced reload (`FULL_RELOAD_EVERY = 12` runs, `src/cvestream/app/scheduler.py:41`) picks up an edit made without `INCR`;
  - `config.rejected` (FR-23) rejects an invalid edit and keeps the last valid configuration.

  Two new `valkey` tests prove both, writing as `cvestream-ui`.
- **Branch:** `story-3-4-valkey-administration-ui`, cut from the 3.2 branch (`356d293`). It carries a cherry-pick of `main`'s course-correction commit `bcc52b7` as `f7bf4fc`; a later rebase onto `main` drops the duplicate. The PR is based on the 3.2 branch: `main <- 3.1 <- 3.3 <- 3.2 <- 3.4`.

## Acceptance Criteria

1. **ACL user.**
   - **Given** `deploy/valkey/users.acl`, **when** Valkey starts with it, **then** a `cvestream-ui` user can read and write every `cvestream:v1:*` key, tokens included, and can run the browse commands Redis Insight needs; the exact grant list is settled empirically in T5 and recorded in `docs/config-contract.md`. The user is stored as a SHA-256 hash, with its dev password in `deploy/valkey/dev-secrets/cvestream-ui.password` (one line, no trailing newline).
   - It cannot read or write a key outside `cvestream:v1:*` (key access is namespace-bound; `SCAN` is keyspace-wide and lists every key name of the database — accepted, see Dev Notes). It cannot run any command of the never-grant list in Dev Notes: `KEYS`, `FLUSHALL`/`FLUSHDB`, `CONFIG`, `ACL`, `DEBUG`, `MODULE`, scripting (`EVAL`/`EVALSHA`/`SCRIPT`/`FUNCTION`/`FCALL`), `SHUTDOWN`, `REPLICAOF`/`SLAVEOF`, `MIGRATE`, `MONITOR`, `SLOWLOG`, `LATENCY`, `CLIENT LIST`/`KILL`, `RENAME`/`RENAMENX`, `MOVE`, `COPY`, `RESTORE`, `DUMP`, `SWAPDB`, the `EXPIRE` family, `SAVE`/`BGSAVE`/`BGREWRITEAOF`, `CLUSTER`, `FAILOVER`.
   - A `valkey`-marked integration test proves each grant and each refusal.
   - The lines of `default`, `cvestream-daemon`, `cvestream-admin` and `connector-example` are unchanged, and no user other than `cvestream-ui` has `SCAN`. The unit tests in `tests/unit/test_deploy_valkey.py` guard both.
2. **Dev stack.**
   - **Given** the dev stack, **when** the operator runs `docker compose up` (no profile, no extra variable), **then** a `valkey-ui` service starts with `redis/redisinsight:3.8.0`.
   - It is already connected to `valkey` as `cvestream-ui`. The password comes from a compose secret file, never from an inline environment value in `compose.yml`.
   - It is published on `127.0.0.1` only, with the EULA screen skipped, analytics off (`RI_ACCEPT_TERMS_AND_CONDITIONS=true`), connection management locked (`RI_DATABASE_MANAGEMENT=false`) and statics auto-update off (`RI_STATICS_AUTO_UPDATE=false`).
   - The `valkey` service still publishes no host port and stays only on the internal `config` network.
   - The story records the image's licence (SSPL-1.0 / Redis EULA) in `docs/operations.md`.
3. **Kubernetes component.**
   - **Given** `deploy/k8s/components/valkey-ui` (a `kind: Component`), **when** an overlay lists it under `components:`, **then** `kubectl kustomize` produces:
     - a `Deployment` admitted under the `restricted` Pod Security Standard;
     - a ClusterIP `Service`, and no `Ingress`;
     - a `NetworkPolicy` (UI egress to the Valkey pods on 6379 and to DNS only; no ingress rule);
     - the password from a placeholder `Secret`.
   - The UI pod is admitted by the Valkey `NetworkPolicy`.
   - The example overlay does not enable the component, and its output is unchanged except for the `users.acl` Secret (which now holds the `cvestream-ui` line) and its hashed name.
   - CI builds a test overlay that enables the component. `tests/unit/test_deploy_manifests.py` guards these properties on the raw files.
4. **UI writes and the daemon.**
   - **Given** a write as `cvestream-ui`, **when** it skips the revision bump, **then** the daemon applies it at the latest on its next forced reload.
   - **When** it writes an invalid value, **then** the daemon rejects it with `config.rejected` and keeps the last valid configuration (FR-23).
   - There is no daemon change. Two `valkey` integration tests prove this, writing as `cvestream-ui`.
5. **Docs.**
   - `docs/operations.md` gains a "Valkey administration UI" section. It covers:
     - access: `http://127.0.0.1:5540` in dev, `kubectl port-forward` on Kubernetes;
     - why the UI must never be exposed (it has no authentication of its own);
     - a recommendation to write with the CLI, and to run `INCR cvestream:v1:revision` from the UI's CLI after a UI edit;
     - how to add `cvestream-ui` to a production `users.acl` with its own hash, and how to enable the component;
     - the licence;
     - the daily GitHub fetch of feature flags.
   - `docs/config-contract.md` lists the user (the "Who uses it" table, the ACL block and its rules).
   - The header of `compose.yml` and `README.md` mention the UI.
   - `CLAUDE.md` and `README.md` gate lists gain the test overlay build.
6. **Pattern amendments** (same PR).
   - `architecture.md` and `project-context.md` are amended as the change proposal says: ACL users and rules, dev compose, Kubernetes component, project tree, and the image in the stack line. These amendments are a gate for AC 1 and land in the same PR as the `cvestream-ui` ACL line (project-context rule: a rule change ships with the change it governs): the token rule is reworded to "connector ACLs never cover `cvestream:v1:token:*`" (only the daemon, the admin and the UI read tokens), and `architecture.md`'s Interfaces and "Assets: none (no UI)" lines are updated.
   - `deferred-work.md` adds the UI pin to the "image pins are not bumped by Dependabot" item.
   - `epic-3-stack-plan.md` gains the 3.4 rows and a "PR 4 of 4" test section.

## Tasks / Subtasks

- [x] **T1. ACL user and dev secrets** (AC 1)
  - [x] Add `deploy/valkey/dev-secrets/cvestream-ui.password` containing `dev-only-cvestream-ui`, with no trailing newline (`printf '%s'`).
  - [x] Add the `cvestream-ui` line to `users.acl`, after `cvestream-admin` and before `connector-example`, with no comment line. Start from the candidate list in Dev Notes and settle it with the `ACL LOG` procedure. Hash: `printf '%s' dev-only-cvestream-ui | sha256sum`.
  - [x] Add `deploy/valkey/dev-secrets/cvestream-ui.redisinsight.json`, the Redis Insight pre-setup file for compose. It is a JSON array with one object `{"id": "cvestream", "name": "cvestream", "host": "valkey", "port": 6379, "username": "cvestream-ui", "password": "dev-only-cvestream-ui"}`. Check the field names against the 3.8.0 `populateDefaultValues` (`api/src/modules/database-discovery/utils/pre-setup.discovery.util.ts`).
- [x] **T2. Unit tests on the raw ACL files** (AC 1), in `tests/unit/test_deploy_valkey.py`
  - [x] Add `cvestream-ui` to `USERS`. The existing hash and "no blanket access" tests then cover it.
  - [x] New test: `cvestream-ui` has exactly `[("RW", "cvestream:v1:*")]`, `-@all`, no `+@` category, and none of the forbidden commands (both `+cmd` and `+cmd|sub` forms; see the list in Dev Notes).
  - [x] New test: `+scan` appears only on the `cvestream-ui` line.
  - [x] New test: the pre-setup JSON matches the dev secret and the ACL user (`password == cvestream-ui.password`, `username == "cvestream-ui"`, `host == "valkey"`, `port == 6379`, exactly one entry).
- [x] **T3. Integration tests** (AC 1, AC 4): new module `tests/integration/test_valkey_ui.py`, with `pytestmark` as in `test_valkey_provider.py`
  - [x] In `valkey_support.py`, add `UI = "cvestream-ui"`. In the new module, add a `ui` fixture (a client of `support.client(support.UI)`, closed after the test). Reuse the `admin` fixture of `tests/integration/conftest.py` for the cleanup.
  - [x] **Grants:**
    - `GET`/`SET`/`DEL` of `schema` and of `token:team-php`;
    - `SADD`/`SREM`/`SMEMBERS` of `endpoints`;
    - `INCR revision`;
    - `SCAN 0 MATCH cvestream:v1:*` returns the seeded keys;
    - `TYPE`, `TTL`, `PTTL`, `STRLEN`, `SCARD` on namespace keys;
    - every other browse command kept in the final line (`DBSIZE`, `INFO`, `MEMORY USAGE`, `CLIENT SETNAME`, ...).
  - [x] **Refusals:**
    - parametrized over the commands of AC 1, plus every forbidden command of Dev Notes;
    - for `CONFIG`, `ACL`, `CLIENT` and `MODULE`, the forbidden subcommands named in Dev Notes (for example `CONFIG GET *`, `ACL LIST`, `CLIENT LIST`, `MODULE LIST`);
    - `GET`/`SET`/`TYPE`/`TTL` of a key outside the namespace (`other`, `cvestream:v2:schema`) are refused too.

    Use `execute_command` for commands valkey-py has no method for, and the existing `_refused` helper pattern (`NoPermissionError`).
  - [x] **The full write protocol works as `cvestream-ui`:** `WATCH revision`, `MULTI`, `SET`, `INCR`, `EXEC`.
  - [x] **Safety net, forgotten bump.** Seed with the admin provider (`support.provider(support.ADMIN).update(...)`, as `_seed` does in `test_valkey_provider.py`), then load a daemon snapshot. As `cvestream-ui`, change an endpoint JSON without `INCR`. Then `reload_config(daemon, snapshot)` is the same snapshot, while `reload_config(daemon, snapshot, force=True)` returns the edit. Mirror `test_a_forgotten_revision_is_picked_up_by_the_forced_reload`.
  - [x] **Safety net, invalid edit.** As `cvestream-ui`, write invalid JSON (or `min_cvss: 11`) and `INCR`. Then `reload_config` keeps the last valid snapshot and `capture_logs()` holds one `config.rejected`. Mirror `test_a_connector_edit_is_loaded_and_an_invalid_one_is_rejected`.
  - [x] Write only under `support.KNOWN_IDS` so that `support.wipe` cleans up. The admin still has no `SCAN`; do not change `wipe`.
  - [x] The `ui` fixture runs a `SCAN cvestream:v1:*` + `DEL` cleanup in setup and teardown, so residue outside `KNOWN_IDS` from a crashed test cannot break the `SCAN` grant assertion.
  - [x] `DEL` of a structural key (`cvestream:v1:revision`, `cvestream:v1:schema`, `cvestream:v1:endpoints`) as `cvestream-ui` is granted; assert the daemon behavior afterwards (snapshot read, `config.rejected`, last valid kept) and document it in `docs/operations.md`.
  - [x] A UI `SET` of a non-integer `cvestream:v1:revision` makes every daemon write fail until repaired: assert `reload_config` keeps the last valid snapshot, and that `SET cvestream:v1:revision <integer>` (or `cvestream config import`) recovers.
- [x] **T4. Compose service** (AC 2), in `compose.yml`
  - [x] Add a `valkey-ui` service with:
    - `image: redis/redisinsight:3.8.0`;
    - `networks: [default, config]` and `depends_on: [valkey]`;
    - `ports: ["127.0.0.1:5540:5540"]`;
    - `secrets: [valkey-ui-databases]`;
    - environment: `RI_ACCEPT_TERMS_AND_CONDITIONS: "true"`, `RI_DATABASE_MANAGEMENT: "false"`, `RI_STATICS_AUTO_UPDATE: "false"`, `RI_FILES_LOGGER: "false"` and `RI_PRE_SETUP_DATABASES_PATH: /run/secrets/valkey-ui-databases`.

    Quote every value: `"true"`, because the code tests `=== 'true'`.
  - [x] Add the top-level secret `valkey-ui-databases: {file: ./deploy/valkey/dev-secrets/cvestream-ui.redisinsight.json}`.
  - [x] Apply the hardening — it is not optional: `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `read_only: true` with tmpfs `/data` and `/tmp` owned by UID 1000. If tmpfs ownership blocks, record the blocker in the Completion Notes and propose the alternative in the PR; never ship the dev stack without `read_only` silently (Kubernetes covers it with `fsGroup`).
  - [x] Update the header comment: the UI URL and the fact that it uses the public dev password. Also say that its writes skip validation and the revision bump (prefer the CLI; `INCR cvestream:v1:revision` after an edit).
  - [x] Check: `docker compose config` parses; `docker compose up -d valkey valkey-ui`; open `http://127.0.0.1:5540`. The `cvestream` database is listed and connected with no click, and the browser lists `cvestream:v1:*` once seeded. Also check `docker compose port valkey 6379` (it must publish nothing).
- [x] **T5. Settle the ACL line empirically** (AC 1): the procedure is in Dev Notes, "ACL LOG procedure". Keep the smallest list with which these UI features work: the key browser (list, type, TTL, size), viewing and editing a string, viewing and editing a set, deleting a key, and the CLI tab running `INCR cvestream:v1:revision`. Write the final list and the commands left refused (with what the UI shows) in the Completion Notes and in `docs/config-contract.md`.
- [x] **T6. Kubernetes component** (AC 3), under `deploy/k8s/components/valkey-ui/`
  - [x] `kustomization.yaml`: `apiVersion: kustomize.config.k8s.io/v1alpha1`, `kind: Component`, `resources: [deployment.yaml, service.yaml, networkpolicy.yaml, secret.example.yaml]`. Add a header comment on how to enable it and why it is never exposed.
  - [x] `deployment.yaml`: `cvestream-valkey-ui`, 1 replica. The full shape is in Dev Notes, "Component manifests".
  - [x] `service.yaml`: ClusterIP `cvestream-valkey-ui`, port 5540, named `http`.
  - [x] `networkpolicy.yaml`: `cvestream-valkey-ui` (Dev Notes).
  - [x] `secret.example.yaml`: `cvestream-valkey-ui`, key `databases.json`, placeholder password `REPLACE-ME-valkey-password-of-cvestream-ui`, host `cvestream-valkey`.
  - [x] Test overlay `tests/k8s/example-with-valkey-ui/kustomization.yaml`: `resources: [../../../deploy/k8s/overlays/example]`, `components: [../../../deploy/k8s/components/valkey-ui]`. Add a comment saying it exists for CI only.
  - [x] `.github/workflows/ci.yml`: in the "Kustomize builds" step, add `kubectl kustomize tests/k8s/example-with-valkey-ui >/dev/null`.
  - [x] Read the full output of the test overlay. Check:
    - the component's resources are in namespace `cvestream`;
    - they carry the overlay label `app.kubernetes.io/part-of: cvestream`;
    - the Secret is referenced by its plain name. A plain resource is not hashed: only generator output is.
- [x] **T7. Manifest unit tests** (AC 2, AC 3), in `tests/unit/test_deploy_manifests.py`. Raw files, no kubectl. Check:
  - [x] the component file is `kind: Component` and lists the four files;
  - [x] the UI pod and container `securityContext` satisfy `restricted`:
    - `runAsNonRoot`, numeric non-zero `runAsUser`, `seccompProfile.type: RuntimeDefault`;
    - `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `readOnlyRootFilesystem: true`;
    - `automountServiceAccountToken: false`, `enableServiceLinks: false`;
    - no `hostPath`/`hostNetwork`/`hostPort`, and no `privileged`;
  - [x] the image is an exact tag (not `latest`, a digit in the tag) and **equals the compose `valkey-ui` image**;
  - [x] the Service is ClusterIP (type absent or `ClusterIP`) and no document of the component is an `Ingress`;
  - [x] the NetworkPolicy has `policyTypes: [Ingress, Egress]`, no ingress rule, and exactly two egress rules: DNS 53 UDP/TCP to kube-dns, and 6379 TCP to `app.kubernetes.io/name: valkey`;
  - [x] the pod labels include `cvestream.io/valkey-access: "true"`, and the example Valkey NetworkPolicy still admits that label;
  - [x] the example overlay has no `components` entry naming `valkey-ui`, and the test overlay has one;
  - [x] in compose, every `valkey-ui` port starts with `127.0.0.1:`, `valkey` has no `ports`, and `valkey-ui` does not set `RI_REDIS_PASSWORD`;
  - [x] the component's Secret holds only a `REPLACE-ME` placeholder.
- [x] **T8. Docs** (AC 5): the outline is in Dev Notes, "What the docs must say". Files: `docs/operations.md`, `docs/config-contract.md`, `README.md`, the `compose.yml` header, and the `CLAUDE.md` gate list.
- [x] **T9. Pattern amendments and tracking** (AC 6)
  - [x] `architecture.md`: apply the OLD to NEW blocks of the change proposal (section 4, "Architecture"). Mind the 3.2 branch's actual text; the proposal quotes it.
  - [x] Tree: `users.acl` comment (`cvestream-ui` added), `dev-secrets/` ("four users", plus the Redis Insight pre-setup file), `deploy/k8s/components/valkey-ui/`, `tests/k8s/`.
  - [x] "Assets: none (no UI)": add "the optional Valkey administration UI is a third-party image".
  - [x] `project-context.md`: replace the Valkey security bullet with the proposal's NEW text. Add `redis/redisinsight:3.8.0` (SSPL, unmodified; dev compose and opt-in component) to the Images line.
  - [x] `deferred-work.md`: extend the "Valkey image pins are not bumped by Dependabot" item with the UI pin (compose and component; the test keeps them equal), and add the note "revisit Valkey Admin 1.3.0 when it supports a pre-configured browser connection (it would remove the SSPL dependency)".
  - [x] `epic-3-stack-plan.md`: the 3.4 rows already land with this story file (create-story). Check them against what was built.
- [x] **T10. Gates and status**: run every gate of `CLAUDE.md`, plus `kubectl kustomize tests/k8s/example-with-valkey-ui`, plus the `valkey` tests against a disposable server (README, "Running the tests"). Docker build: x509 behind the proxy, so rely on CI. Then set the story to `review` in the story file and in `sprint-status.yaml`.

### Review Findings

Code review 2026-11-07, baseline `f7bf4fc` (diff: this story file + `epic-3-stack-plan.md` + `sprint-status.yaml`, 539 insertions). Layers: blind, edge, auditor — all completed. 2 decision-needed (both resolved 2026-11-07), 23 patch, 1 defer, 2 dismissed.

- [x] [Review][Decision] `+info` contradicts the no-client-exposure rationale — resolved 2026-11-07: keep `+info` (option 1); the accepted client-listing exposure is documented via the patch bullet below. [3-4-valkey-administration-ui.md:231]
- [x] [Review][Decision] Valkey NetworkPolicy admission: label vs literal patch — resolved 2026-11-07: keep the opt-in label `cvestream.io/valkey-access: "true"` (option 1, reviewer's pick: the admission rule is verified in `deploy/k8s/overlays/example/networkpolicy-valkey.yaml:22`, one source of truth, and the component stays buildable with any overlay that names its Valkey NetworkPolicy differently). The epic/proposal AC text is amended via the patch bullet below. [epics.md Story 3.4 AC 3; "Decisions taken autonomously" #4]

- [x] [Review][Patch] Document the accepted `+info` exposure (INFO lists connected clients) in `docs/config-contract.md`'s ACL rules. [docs/config-contract.md; 3-4-valkey-administration-ui.md:231]
- [x] [Review][Patch] Amend epics.md Story 3.4 AC 3 and the change proposal §2 to name the opt-in label `cvestream.io/valkey-access: "true"` as the admission mechanism instead of "a patch admitting the UI pod". [epics.md; sprint-change-proposal-2026-11-07-valkey-admin-ui.md]
- [x] [Review][Patch] Contradictory `RI_DATABASE_MANAGEMENT` semantics: the Image choice row says `!== 'false'` locks the connection list; TL;DR, AC 2 and the compose shape say `=false` locks it. Align the Image choice row to `=false`. [3-4-valkey-administration-ui.md:208]
- [x] [Review][Patch] Stack plan status table is stale vs `sprint-status.yaml`: 3.3 and 3.2 read "pending review" while sprint-status has 3-3 `review`, 3-2 `done`. [epic-3-stack-plan.md:48-49]
- [x] [Review][Patch] `users.acl` line count wrong: the "Current state" table says "5 lines" but names 4 users and the file has 4 `user` lines. [3-4-valkey-administration-ui.md Dev Notes table]
- [x] [Review][Patch] AC 1 command scope is unsettled and self-inconsistent: grants are an open-ended "SCAN, TYPE, TTL, ..." settled empirically in T5, while the AC refusal list is narrower than the Dev Notes never-grant list (MOVE, COPY, RESTORE, DUMP, SLOWLOG, LATENCY, SAVE/BGSAVE, SWAPDB, FAILOVER, CLIENT subcommands). Pin the refusal list in AC 1; record the final grant list in `docs/config-contract.md` after T5. [3-4-valkey-administration-ui.md AC 1, T3, Dev Notes]
- [x] [Review][Patch] AC 1 overpromises key isolation: "cannot touch a key outside `cvestream:v1:*`" conflicts with the keyspace-wide `SCAN` (conceded in Dev Notes). Reword the AC: key access is namespace-bound; `SCAN` lists every key name. [3-4-valkey-administration-ui.md AC 1]
- [x] [Review][Patch] Unverified factual claims recorded as fact: image release date, arch support, `default.ts` line numbers, the sha256 digest ("reported by the research", then waved through), "the features-config fetch cannot be turned off". Mark them verified-with-source or add a verification step to T5/T10. [3-4-valkey-administration-ui.md Image choice]
- [x] [Review][Patch] Inconsistent citation of the same file: `app/scheduler.py:41` (TL;DR) vs `src/cvestream/app/scheduler.py:41` (References). [3-4-valkey-administration-ui.md TL;DR vs References]
- [x] [Review][Patch] Valkey pin ambiguity: procedures use `valkey/valkey:9.1.3`, the stack plan's 3.1 row says `valkey/valkey:9.1`. Clarify tag vs version. [3-4-valkey-administration-ui.md Dev Notes; epic-3-stack-plan.md]
- [x] [Review][Patch] Non-integer `revision` written from the UI breaks every daemon write until manual repair (3.2 intelligence); it appears only in "Previous story intelligence", not in AC 5's docs outline or T3. Add the recovery (`SET cvestream:v1:revision <integer>` or `cvestream config import`) to the docs outline and a `valkey` test to T3. [3-4-valkey-administration-ui.md AC 4/AC 5, T3]
- [x] [Review][Patch] `DEL` of a structural key (`revision`, `schema`, `endpoints`) is granted but untested; daemon behavior after such a delete is undefined. Add a T3 test and a docs note. [3-4-valkey-administration-ui.md T1/T3]
- [x] [Review][Patch] Malformed or absent pre-setup JSON at container start leaves the UI with zero databases and a locked connection list (`RI_DATABASE_MANAGEMENT=false`); startup/recovery undocumented. Document in `docs/operations.md`. [3-4-valkey-administration-ui.md T4]
- [x] [Review][Patch] Component placeholders not pinned: `emptyDir` 256Mi, resources "measure, adjust", probes "an httpGet ... if 3.8.0 has one (check), else tcpSocket". Pin concrete values in the story before implementation. [3-4-valkey-administration-ui.md Dev Notes "Component manifests"]
- [x] [Review][Patch] Host port 5540 already bound makes `docker compose up` fail to start `valkey-ui`; failure mode undocumented. Add a pre-check or a doc line. [3-4-valkey-administration-ui.md T4]
- [x] [Review][Patch] Test residue outside `KNOWN_IDS` breaks the `SCAN` grant assertion and survives `wipe` (admin has no `SCAN`): the `ui` fixture in the new module needs a `SCAN`+`DEL` cleanup pass. [3-4-valkey-administration-ui.md T3]
- [x] [Review][Patch] Egress DNS rule assumes kube-system kube-dns; other clusters need an adjusted rule. Note the assumption in the component comment and docs. [3-4-valkey-administration-ui.md networkpolicy shape]
- [x] [Review][Patch] "Loopback only / never reachable" is imprecise: `valkey-ui` shares the `default` compose network, so other dev containers (e.g. `app`) can reach the unauthenticated UI. Clarify the exposure claim in `docs/operations.md` (or isolate the UI on its own network). [3-4-valkey-administration-ui.md T4]
- [x] [Review][Patch] The "revisit Valkey Admin 1.3.0" note for `deferred-work.md` has no owning task (T9 assigns only the Dependabot-pin amendment). Add it to T9. [3-4-valkey-administration-ui.md "Latest technical information" vs T9]
- [x] [Review][Patch] Stack plan "PR 4 of 4" step 5 says "Fill both Secrets" without naming them (the `users.acl` Secret is kustomize-generated, not filled by hand). Name the two Secrets. [epic-3-stack-plan.md:365]
- [x] [Review][Patch] Stack plan headings omit 3.4: "Why this order: `main <- 3.1 <- 3.3 <- 3.2`" and "Run the stories in stack order (3.1, 3.3, 3.2)". [epic-3-stack-plan.md:52, section 3]
- [x] [Review][Patch] Pattern amendments must land in the same PR as the ACL line: `project-context.md`'s token rule ("ACLs are never widened to cover `cvestream:v1:token:*`") is contradicted by the planned `~cvestream:v1:*` UI grant and is already imprecise today (`cvestream-admin` has `~cvestream:v1:*`); `architecture.md` Interfaces/"Assets: none (no UI)" amendments are unchecked T9 tasks. Make T9 an explicit AC and reword the rule ("connector ACLs never cover tokens"); add the dev-proxy note for the daily GitHub feature-flag fetch. [project-context.md Security; 3-4-valkey-administration-ui.md T9]
- [x] [Review][Patch] T4's hardening is non-deterministic: `read_only`/tmpfs "optional, if it works", with a fallback that drops `read_only`; no test pins the outcome. Decide now and record it. [3-4-valkey-administration-ui.md T4]

- [x] [Review][Defer] Placeholder Secret can be applied as-is; the only guard is a raw-file unit test — deferred, pre-existing pattern (same as `overlays/example/secret.example.yaml`); the placeholder password fails closed against a real `users.acl` hash. [3-4-valkey-administration-ui.md T6]

### Review Findings (code review of the implementation, 2026-11-07)

:factory: Blind Hunter, Edge Case Hunter and Acceptance Auditor on `f7bf4fc..4d0eae3` (planning documents excluded); key claims re-checked against `redis/redisinsight:3.8.0` and `src/cvestream/config/valkey_provider.py`.

- [x] [Review][Decision] `SET ... EX|PX|EXAT|PXAT` puts a TTL on any config key despite the `EXPIRE` ban — `+set` is granted and a Valkey ACL cannot filter arguments; Redis Insight's "Add key" form with a TTL sends `SET key value EX n` (verified by the Edge Case Hunter: `POST /api/databases/cvestream/string {"expire":100}` → TTL 100). On expiry an endpoint or token key fails closed (`config.rejected`), but an expired `products` key silently empties that endpoint's product list. `docs/operations.md` ("cannot set TTLs") and the spec are wrong. Options: (1) accept, correct the docs and add a `valkey` test pinning `SET ... EX` as allowed; (2) drop `+set` (breaks string editing in the UI). Resolved 2026-11-07: option 1.
- [x] [Review][Decision] `secret.example.yaml` is a resource of the component, so every `kubectl apply -k` overwrites an out-of-band `cvestream-valkey-ui` Secret with the `REPLACE-ME` placeholder — the file header and `docs/operations.md` both suggest creating it out of band; Redis Insight re-reads the pre-setup file on each start, so the UI breaks at the next restart. A component is referenced, not copied, so the operator cannot drop the file without forking it. Options: (1) move the placeholder Secret into `tests/k8s/example-with-valkey-ui` and keep `secret.example.yaml` in the component as a non-resource template; (2) keep it and document "edit a copy of the component"; (3) keep it and use a `secretGenerator` with `behavior: merge` in the operator overlay. Resolved 2026-11-07: option 1.
- [x] [Review][Decision] `DEL cvestream:v1:endpoints` from the UI yields a valid empty configuration, not `config.rejected` — `assemble()` builds endpoints from `SMEMBERS endpoints`, so an empty set is a valid document with zero endpoints and every feed disappears at the next reload; T3 expected "snapshot read, `config.rejected`, last valid kept" for all three structural keys, but only `schema` is tested and documented (`DEL revision` reads as `"0"`). Options: (1) accept, test and document the three cases (recovery: `cvestream config import`); (2) add a daemon guard (Python change, out of this story's "no application code" scope: defer to a hardening story). Resolved 2026-11-07: option 1 now; the daemon guard is deferred (bullet below).

- [x] [Review][Patch] (from D1) Correct the docs ("cannot set TTLs" is false: `SET ... EX` is allowed, `EXPIRE` and friends are not) and add a `valkey` test pinning `SET ... EX` as granted, with the consequence of an expired key [docs/operations.md:226, tests/integration/test_valkey_ui.py]
- [x] [Review][Patch] (from D2) Move the placeholder Secret out of the component's `resources:` into the test overlay `tests/k8s/example-with-valkey-ui`; keep `secret.example.yaml` in the component as a template only; update the unit tests, the component header and `docs/operations.md` [deploy/k8s/components/valkey-ui/kustomization.yaml:17]
- [x] [Review][Patch] (from D3) Test and document `DEL` of each structural key as `cvestream-ui`: `schema` (rejected, last valid kept), `revision` (read as `"0"`), `endpoints` (valid empty configuration, every feed disappears; recover with `cvestream config import`) [tests/integration/test_valkey_ui.py:279, docs/operations.md:227]
- [x] [Review][Patch] Redis Insight has no log transport: `RI_FILES_LOGGER=false` without `RI_STDOUT_LOGGER=true` (default `false` in `api/dist/config/default.js:199`, verified in the 3.8.0 image); every line goes to winston's "no transports" warning. Add `RI_STDOUT_LOGGER: "true"` to compose and the component [compose.yml:75, deploy/k8s/components/valkey-ui/deployment.yaml:38]
- [x] [Review][Patch] Docs give a repair that does not work: `cvestream config import` cannot repair a non-integer revision (its `INCR` runs inside `MULTI`, `valkey_provider.py:555`; still open in `deferred-work.md`); only `SET cvestream:v1:revision <integer>` works [docs/operations.md:227]
- [x] [Review][Patch] Refusal tests for `RENAME`, `RENAMENX`, `COPY`, `RESTORE` and `MIGRATE` pass for the wrong reason: their second key (`x`) is outside the namespace, so the key pattern refuses them even if the command were granted. Use a `cvestream:v1:*` target; also add `EVAL_RO`, `EVALSHA_RO`, `FCALL_RO` and `CLIENT NO-EVICT` from the Dev Notes list [tests/integration/test_valkey_ui.py:49]
- [x] [Review][Patch] Unit ACL guard is weak: the never-grant set is checked only for `cvestream-ui` (comment and `config-contract.md` say every user), case-sensitively, with every `client|*` subcommand allowed except three; `test_only_the_ui_user_has_scan` checks only the literal `+scan` (a `+@keyspace` on another user passes); `sync`, `psync`, `replconf` are missing; no test pins the four other lines unchanged (AC 1). Check every user, lower-case the rules, allow-list `client|setinfo`/`client|setname`, refuse `+@` categories for every user, pin the other lines [tests/unit/test_deploy_valkey.py:168]
- [x] [Review][Patch] Grant coverage gaps (AC 1 "proves each grant", T3): no `SET`/`DEL` of `token:team-php`; `MGET`, `EXISTS`, `PING`, `SELECT`, `DISCARD` never exercised [tests/integration/test_valkey_ui.py:116]
- [x] [Review][Patch] The `emptyDir` eviction note announced in the Deployment comment is missing from `docs/operations.md`; the "rebuilt at start" comment is wrong for container restarts (an `emptyDir` survives a liveness or OOM restart) [deploy/k8s/components/valkey-ui/deployment.yaml:77,84]
- [x] [Review][Patch] A Secret change (password rotation) never reaches the running UI: Redis Insight reads the pre-setup file only at start and the Secret has no hashed name; document `kubectl rollout restart deploy/cvestream-valkey-ui` [docs/operations.md:223]
- [x] [Review][Patch] The `INFO` exposure rationale is wrong: `INFO clients` has aggregate counters only, client addresses and last commands are `CLIENT LIST`; the real exposure is server version, executable/config paths, persistence and replication details [docs/config-contract.md:182]
- [x] [Review][Patch] Component deployment assumptions undocumented: Valkey in the same namespace, pod label `app.kubernetes.io/name: valkey`, port 6379, no TLS in `databases.json`; the overlay that lists the component must set `namespace:` (the example overlay's transformations do not reach it); `SCAN` (with `+select`, every logical DB) assumes a Valkey dedicated to cvestream [docs/operations.md:223]
- [x] [Review][Patch] The NetworkPolicy test does not check the DNS `namespaceSelector` (kube-system), nor that the Valkey egress peer matches the example StatefulSet's pod labels [tests/unit/test_deploy_manifests.py:227]
- [x] [Review][Patch] `ui_component_files` loads one document per file (`_load`), so an `Ingress` appended as a second document is not seen; `_load_all` is dead code — use it [tests/unit/test_deploy_manifests.py:139]
- [x] [Review][Patch] The placeholder-Secret test is near-trivial (`"password" in value` always holds): parse the JSON and assert the `password` field is the `REPLACE-ME` value [tests/unit/test_deploy_manifests.py:292]
- [x] [Review][Patch] One `SCAN` call is assumed to return everything (`assert cursor == 0`): loop like `_clean_namespace` [tests/integration/test_valkey_ui.py:139]
- [x] [Review][Patch] Assertion whose first half can never matter: `entry["errors"] == [...] or "not seeded" in str(...)`; keep one exact check [tests/integration/test_valkey_ui.py:297]
- [x] [Review][Patch] README `port-forward` lacks `-n <ns>` (component header and `operations.md` have it) [README.md:95]
- [x] [Review][Patch] Unverified behaviour stated as fact: with the GitHub feature-flag fetch blocked on Kubernetes "the UI still works and only logs a warning" (never run on minikube, per the Completion Notes); verify or word it as expected [docs/operations.md:229, deploy/k8s/components/valkey-ui/networkpolicy.yaml:4]

- [x] [Review][Defer] Daemon guard against a UI `DEL cvestream:v1:endpoints` (an empty `endpoints` set is a valid empty configuration that drops every feed) [src/cvestream/config/valkey_provider.py:140] — deferred: a Python change, outside story 3.4's "no application code" scope; for a Valkey hardening story

## Dev Notes

### Scope

- This story is mostly deployment files, ACL tests and docs. Do not touch `src/`.
- Do not add a dependency (`uv.lock` unchanged). `pyyaml` is already used by `test_deploy_manifests.py`.
- No new setting, so `settings.example.yaml` and `docs/configuration.md` are unchanged.
- The `config/models.py` contract and `schemas/config-v1.json` are unchanged.
- Out of scope:
  - TLS between the UI and Valkey;
  - authentication in front of the UI (it is never exposed; decision 3);
  - a UI on the self-hosted `deploy/compose/compose.yml` (that stack is YAML-only and has no Valkey);
  - ACL selectors to restrict a connector's `SET revision` (still deferred).

### Current state of the files this story touches (read them first)

| File | Today (3.2 branch) | This story |
|---|---|---|
| `deploy/valkey/users.acl` | 4 lines: `default` off, `cvestream-daemon` (`%R~cvestream:v1:*`), `cvestream-admin` (`~cvestream:v1:*`, 17 commands + `client|setinfo`), `connector-example`. No comment line is allowed (Valkey 9.1 rejects the whole file) | + `cvestream-ui` line; others byte-identical |
| `deploy/valkey/dev-secrets/` | `cvestream-daemon`, `cvestream-admin`, `connector-example` `.password` (`dev-only-<user>`, no newline) | + `cvestream-ui.password`, + `cvestream-ui.redisinsight.json` |
| `deploy/valkey/kustomization.yaml` | generates the `valkey.conf` ConfigMap and the `users.acl` Secret for the example overlay | unchanged (the new ACL line flows into the Secret; its hash changes, so the Valkey pod rolls) |
| `compose.yml` | `app` (default + config), `valkey` (config only, `internal: true`, no ports), secret `valkey-daemon-password` | + `valkey-ui`, + secret `valkey-ui-databases`, header comment |
| `deploy/k8s/overlays/example/networkpolicy-valkey.yaml` | Valkey ingress 6379 from `app.kubernetes.io/name: cvestream` **or** `cvestream.io/valkey-access: "true"` (same namespace), `egress: []` | unchanged (the UI pod carries the opt-in label) |
| `deploy/k8s/overlays/example/kustomization.yaml` | base + `../../../valkey` + StatefulSet + NP + Ingress + Secret example, `labels` part-of | unchanged (does not list the component) |
| `.github/workflows/ci.yml` | "Kustomize builds" step: base + example | + test overlay build |
| `tests/unit/test_deploy_valkey.py` | `USERS` tuple; hash, pattern and command tests on the raw file | + `cvestream-ui` cases |
| `tests/unit/test_deploy_manifests.py` | base Deployment invariants, release pins, `test_example_valkey_image_matches_the_dev_stack` | + component and compose checks |
| `tests/integration/valkey_support.py` | `DAEMON`, `ADMIN`, `CONNECTOR`, `client()`, `provider()`, `wipe()` (admin, `KNOWN_IDS`, no SCAN) | + `UI` |
| `tests/integration/test_valkey_provider.py` | ACL tests 3 to 5, safety-net tests (`reload_config`, `force=True`) | unchanged; the new module mirrors its patterns |
| `.devcontainer/devcontainer.json` | `dockerComposeFile: ../compose.yml + compose.devcontainer.yml`, no `runServices` | unchanged: the devcontainer now also starts `valkey-ui` (mention it in README) |

### Image choice (researched 2026-11-07, facts checked in the RedisInsight 3.8.0 source)

| Candidate | Verdict |
|---|---|
| **Redis Insight** `redis/redisinsight:3.8.0` (2026-07-21; amd64 and arm64) | **Chosen.** Pre-connection with user name and password from a file (`RI_PRE_SETUP_DATABASES_PATH`, JSON array; `config/default.ts:81`). EULA and analytics: `RI_ACCEPT_TERMS_AND_CONDITIONS === 'true'` (`default.ts:117`) stores `analytics:false, notifications:false, eula:true`. `RI_DATABASE_MANAGEMENT: 'false'` (`:134`) locks the list. `RI_STATICS_AUTO_UPDATE` (`:147`) and `RI_FILES_LOGGER` (`:242`) are both off when set to `false`. `Dockerfile`: `USER node` (UID 1000 in `node:alpine`), `EXPOSE 5540`, `RI_APP_FOLDER_ABSOLUTE_PATH=/data`, `ENTRYPOINT ["./docker-entry.sh", "node", "redisinsight/api/dist/src/main"]`. Licence `LICENSE` = **SSPL v1** (plus the Redis EULA) |
| Valkey Admin `valkey/valkey-admin:1.3.0` (Apache-2.0, 2026-10-29) | Rejected: the browser UI **cannot be pre-connected** (`VALKEY_*` variables feed only its metrics process; users type host, user and password). It requires `INFO` and `CLIENT SETNAME` to connect, about 1 GB of RAM, and has no `*_FILE` option |
| Redis Commander `ghcr.io/joeferner/redis-commander:0.9.1` (MIT) | Fallback only: good variables (`REDIS_USERNAME`, `REDIS_PASSWORD_FILE`, HTTP basic auth, UID 10000), but stale (last tag 2025-07, last commit 2025-09), and its entrypoint writes `config/local-production.json` (read-only root needs a pre-mounted file) |
| P3X Redis UI `patrikx3/p3x-redis-ui:2026.10.100` | Rejected: runs as root, and its AI feature is on by default and routes queries through `network.corifeus.com` |

Redis Insight behaviour that matters:

- **Connection:** the client is ioredis over RESP2 and sends `AUTH user pass`.
  - A `CLIENT SETNAME` error is ignored, and the `INFO` ready check is skipped on `NOPERM`. `SELECT` is sent only for db > 0.
  - On connect each step degrades: `CLUSTER INFO`, `SENTINEL MASTERS` (an error means "not a cluster or sentinel"), `MODULE LIST` (falls back to `COMMAND INFO`), `INFO` (falls back to `HELLO`, then to static data), `CONFIG GET databases`, `CLIENT LIST`. **Grant none of the dangerous ones**; check that the UI still works.
- **Browser:** `SCAN` (with `MATCH`/`COUNT`/`TYPE`), `DBSIZE` (falls back to `INFO keyspace`), then pipelined `TTL`, `TYPE` and `MEMORY USAGE <key> SAMPLES 0`, where each error becomes null. Strings use `EXISTS`, `STRLEN`, `GET`/`GETRANGE`. Sets probably use `SSCAN`/`SCARD`: confirm.
- **No authentication of its own** (no `RI_*` variable for it). This is why it is loopback-only in compose, and has no Ingress and a deny-all ingress NetworkPolicy on Kubernetes.
- **Phone-home:** a features-config fetch from `raw.githubusercontent.com` every 24 h cannot be turned off (only redirected with `RI_FEATURES_CONFIG_URL`). The Segment key is baked in, but analytics stay off once the terms are accepted through the variable. On Kubernetes the egress NetworkPolicy blocks the fetch; check that the UI still works and only logs a warning. In compose the `default` network has egress; document it.
- **Without `RI_ENCRYPTION_KEY`** it stores connection passwords in clear text in its SQLite file under `/data`. This is acceptable: `/data` is an `emptyDir` (Kubernetes) or the container layer or tmpfs (compose), and the connection is re-created from the pre-setup file at start. Do not set an encryption key.
- Pre-setup connections disappear when the file or the variables disappear: the file is the single source.
- **Verify, do not trust:** run any variable or option not listed above against the docs at <https://redis.io/docs/latest/operate/redisinsight/configuration/> before using it (operator rule). Pin the exact tag `3.8.0`. The same applies to every factual claim in the table above (release date, architectures, `default.ts` line numbers, the reported digest, the phone-home behaviour): re-check each at the `3.8.0` tag and record the result in the Completion Notes — never record unverified research output as fact. The digest `sha256:b5e19ee240abef6edb435871b90ff8a210995422e8e018ab61c0339d318a1f84` was reported by the research. If you add it (`image: redis/redisinsight:3.8.0@sha256:...`), re-check it with `docker buildx imagetools inspect redis/redisinsight:3.8.0`; the project pins Valkey by tag only, so the tag alone is acceptable.
- If Redis Insight 3.8.0 turns out unusable against Valkey 9.1 (it detects Valkey from `INFO`, but there is no official support statement), switch to Redis Commander 0.9.1 with `REDIS_PASSWORD_FILE` and `HTTP_USER`/`HTTP_PASSWORD_FILE`. Record the switch as a deviation in the Completion Notes and in the PR.

### ACL: candidate line and rules

Start from this line (`<sha256-ui>` = `printf '%s' dev-only-cvestream-ui | sha256sum`). Remove what the UI does not need; add only what `ACL LOG` shows it needs and what is not forbidden:

```
user cvestream-ui on #<sha256-ui> resetchannels ~cvestream:v1:* -@all +get +mget +smembers +exists +set +del +sadd +srem +incr +incrby +watch +unwatch +multi +exec +discard +ping +select +client|setinfo +client|setname +scan +type +ttl +pttl +strlen +getrange +scard +sscan +sismember +dbsize +info +memory|usage
```

- **Base:** the admin list, unchanged, so that the write protocol works from the UI's CLI tab.
- **Browse:** `scan`, `type`, `ttl`, `pttl`, `strlen`, `getrange`, `scard`, `sscan` and `sismember` are key-bound reads, covered by `~cvestream:v1:*`, except `SCAN` (keyspace-wide). `dbsize`, `info` and `memory|usage` make the UI nicer, but each is optional: keep them only if the UI needs them or is clearly better with them.
- **`HELLO`:** ioredis does not send it unless `INFO` is refused. Test whether the UI needs it, and whether Valkey 9.1 checks the ACL for it once authenticated. Grant `+hello` only if `ACL LOG` shows a refusal that hurts.
- **Never grant** (unit test list; refused in the integration test):
  - `keys`, `flushall`, `flushdb`, `config`, `acl`, `debug`, `module`;
  - `eval`, `evalsha`, `eval_ro`, `evalsha_ro`, `script`, `function`, `fcall`, `fcall_ro`;
  - `shutdown`, `replicaof`, `slaveof`, `migrate`, `monitor`, `slowlog`, `latency`;
  - `client|list`, `client|kill`, `client|no-evict`, plus the `client` parent without a subcommand;
  - `rename`, `renamenx`, `move`, `copy`, `swapdb`, `restore`, `dump`;
  - `expire`, `pexpire`, `expireat`, `pexpireat`, `persist`, `getex`;
  - `save`, `bgsave`, `bgrewriteaof`, `cluster`, `failover`.

  Rationale:
  - `KEYS` and `FLUSH*` are the proposal's list.
  - `MONITOR`, `SLOWLOG` and `CLIENT LIST` show other clients' arguments or addresses (tokens, passwords).
  - `EXPIRE` and friends would let a stray TTL make a configuration key vanish.
  - `RENAME`, `MOVE` and `COPY` would let a key leave the contract layout (`MOVE` changes the database).
- **`SCAN` is not bound to key patterns:** it lists every key name of the database. This is acceptable because the database holds only `cvestream:v1:*`, and a token key name carries only the endpoint id (`token:<id>`). Say so in `docs/config-contract.md`. `cvestream-ui` is the only user allowed `SCAN`.
- Channels: `resetchannels` (no pub/sub). The UI's Pub/Sub page then shows an error, which is acceptable.
- **Tokens:** `cvestream-ui` reads them, as the operator decided on 2026-11-07. `project-context.md` must say that only the daemon, the admin and the UI read tokens.

### ACL LOG procedure (T5)

Use a **scratch copy** of `deploy/valkey/` in the scratchpad or under `/tmp`, never committed. Add a debug user in that copy only, to read the log. Then run the UI against it:

```bash
SCRATCH=$(mktemp -d)/valkey && cp -r deploy/valkey "$SCRATCH"
echo 'user debug on >debug ~* &* +@all' >>"$SCRATCH/users.acl"     # scratch only, NEVER in the repo
docker network create cvs-ui-test
docker run -d --rm --name cvs-ui-valkey --network cvs-ui-test \
  -v "$SCRATCH:/usr/local/etc/valkey:ro" valkey/valkey:9.1.3 \
  valkey-server /usr/local/etc/valkey/valkey.conf
# seed through the admin user (docs/config-contract.md transaction), then:
docker run -d --rm --name cvs-ui --network cvs-ui-test -p 127.0.0.1:5540:5540 \
  -e RI_ACCEPT_TERMS_AND_CONDITIONS=true -e RI_DATABASE_MANAGEMENT=false \
  -e RI_STATICS_AUTO_UPDATE=false -e RI_FILES_LOGGER=false \
  -e RI_PRE_SETUP_DATABASES_PATH=/run/db.json \
  -v "$PWD/<pre-setup json with host cvs-ui-valkey>:/run/db.json:ro" redis/redisinsight:3.8.0
# click through: key list, a string (view, edit), the endpoints set (view, add, remove), delete a key, CLI tab: INCR cvestream:v1:revision
docker exec cvs-ui-valkey valkey-cli --user debug --pass debug ACL LOG 50
docker stop cvs-ui cvs-ui-valkey && docker network rm cvs-ui-test && rm -rf "$SCRATCH"
```

`ACL LOG` lists each refused command, with its `username`, `reason` (`command` or `key`) and `object`. Decide per entry: grant it (if allowed and needed), or leave it refused and note what the UI shows. Repeat until the features of T5 work. Run `ACL LOG RESET` between rounds.

### Compose service (shape)

```yaml
  # Valkey administration UI (story 3.4): http://127.0.0.1:5540, already
  # connected as cvestream-ui. No authentication of its own: loopback only.
  # Redis Insight is SSPL-licensed: used unmodified, never redistributed.
  valkey-ui:
    image: redis/redisinsight:3.8.0
    depends_on: [valkey]
    environment:
      RI_ACCEPT_TERMS_AND_CONDITIONS: "true"   # skips the EULA screen, analytics off
      RI_DATABASE_MANAGEMENT: "false"          # the connection list is fixed
      RI_STATICS_AUTO_UPDATE: "false"
      RI_FILES_LOGGER: "false"                 # logs on stdout only
      # host, user and password of the cvestream connection, from a secret file
      RI_PRE_SETUP_DATABASES_PATH: /run/secrets/valkey-ui-databases
    secrets: [valkey-ui-databases]
    # `config` reaches Valkey; it is internal and cannot publish a port, hence `default`.
    networks: [default, config]
    ports:
      - "127.0.0.1:5540:5540"
```

- The compose secret file is mounted read-only under `/run/secrets/`. Check that UID 1000 can read it. With file-based compose secrets the mode is that of the host file. A `0644` file in git is fine because the password is public; the real-password case only exists on Kubernetes.
- `docker compose watch` and `docker compose up` both start it. The `app` service does not depend on it.

### Component manifests (shape)

`deployment.yaml` (keep the comment style of `deploy/k8s/base/deployment.yaml`):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cvestream-valkey-ui
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: valkey-ui
  template:
    metadata:
      labels:
        app.kubernetes.io/name: valkey-ui
        # Admitted by the example Valkey NetworkPolicy (opt-in label).
        cvestream.io/valkey-access: "true"
    spec:
      automountServiceAccountToken: false
      enableServiceLinks: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000          # `node` in the image: a name, so restricted needs the number
        runAsGroup: 1000
        fsGroup: 1000
        seccompProfile: {type: RuntimeDefault}
      containers:
        - name: redisinsight
          image: redis/redisinsight:3.8.0   # same pin as compose.yml (test)
          env: [RI_ACCEPT_TERMS_AND_CONDITIONS "true", RI_DATABASE_MANAGEMENT "false",
                RI_STATICS_AUTO_UPDATE "false", RI_FILES_LOGGER "false",
                RI_PRE_SETUP_DATABASES_PATH /secrets/valkey-ui/databases.json]
          ports: [{name: http, containerPort: 5540}]
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: ["ALL"]}
          resources: {requests: {cpu: 50m, memory: 256Mi}, limits: {memory: 512Mi}}   # adjust only with a measured value in the Completion Notes
          # probes: tcpSocket on the `http` port (no health endpoint assumed in 3.8.0)
          volumeMounts:
            - {name: data, mountPath: /data}      # SQLite, content; rebuilt at start
            - {name: tmp, mountPath: /tmp}
            - {name: databases, mountPath: /secrets/valkey-ui, readOnly: true}
      volumes:
        - {name: data, emptyDir: {sizeLimit: 256Mi}}   # exceeding the limit evicts the pod; documented in operations.md
        - {name: tmp, emptyDir: {sizeLimit: 64Mi}}
        - name: databases
          secret: {secretName: cvestream-valkey-ui, defaultMode: 0440}
```

- The `env` line above is shorthand: write proper `- name:`/`value:` items, with every value a quoted string.
- Check on minikube that the app starts with a read-only root. If it writes elsewhere (for example under `/usr/src/app` or `~/.npm`), add an `emptyDir` for that path and write it in the Completion Notes. Never drop `readOnlyRootFilesystem`.
- `networkpolicy.yaml`: `podSelector` `app.kubernetes.io/name: valkey-ui`, `policyTypes: ["Ingress", "Egress"]`, `ingress: []`.
  - Write a comment: nothing in the cluster reaches the UI, and `kubectl port-forward` enters the pod directly, so NetworkPolicy does not filter it.
  - Egress: the base's DNS rule (kube-dns in kube-system, UDP and TCP 53 — this assumes kube-dns in kube-system; other clusters must adjust it, noted in the component comment and `docs/operations.md`), plus `to: podSelector app.kubernetes.io/name: valkey`, TCP 6379.
  - Also comment that the GitHub feature-flag fetch is blocked on purpose.
- `secret.example.yaml`: the header comment of `deploy/k8s/overlays/example/secret.example.yaml`, adapted. The password must be the one whose hash is on the `cvestream-ui` line of the deployed `users.acl`. Give a `kubectl create secret generic cvestream-valkey-ui --from-file=databases.json=<file>` alternative.
- **Labels:** do not add `labels:` in the component. The overlay that lists it applies `app.kubernetes.io/part-of: cvestream` (check this in the build).
- **Selector isolation:** `app.kubernetes.io/name: valkey-ui` matches neither `valkey` (the Valkey NP and StatefulSet selector) nor `cvestream`. Check that the example's Valkey `podSelector` (`app.kubernetes.io/name: valkey`, exact match) does not select the UI pod.

### Decisions taken autonomously (flag them in the PR)

1. **Redis Insight 3.8.0, SSPL**: the only candidate that meets "already connected" with a password file. Fallback: Redis Commander 0.9.1 (MIT, stale). **The operator accepted the SSPL licence on 2026-11-07** (a tool run unmodified next to the store, not shipped as part of cvestream): not an open decision for the PR.
2. **Password through the pre-setup JSON file**: a compose secret, and a Secret on Kubernetes. Not `RI_REDIS_PASSWORD` (inline in compose) and not an entrypoint wrapper (couples to image internals). The JSON duplicates the dev password; a unit test keeps it equal to `cvestream-ui.password`.
3. **No authentication in front of the UI.** It is never reachable from outside: loopback in dev, `ingress: []` plus port-forward on Kubernetes. An auth proxy would be a new component for little gain.
4. **Admission through the existing label `cvestream.io/valkey-access: "true"` instead of a patch on `cvestream-valkey`.** The epic text says "a patch admitting the UI pod". The label rule already exists for exactly this ("pods that opt in"), adds no second source of truth, and keeps the component buildable with an overlay that names its Valkey NetworkPolicy differently. If the reviewer prefers the literal patch, it is a JSON 6902 `add` on `/spec/ingress/0/from/-` with `podSelector: {matchLabels: {app.kubernetes.io/name: valkey-ui}}`.
5. **Deny `EXPIRE`/`RENAME`/`MOVE`/`COPY`** on top of the proposal's list: they would let the UI break the layout or make keys vanish.
6. **Test overlay under `tests/k8s/`**, not `deploy/k8s/overlays/`, so that nobody mistakes it for a supported overlay.
7. **The committed `users.acl` gains `cvestream-ui` whether or not the UI is enabled.** It is a public dev hash, like the others. Production ships its own file and adds the line only when it enables the UI (docs).

### What the docs must say

- **`docs/operations.md`:**
  - TL;DR: one bullet.
  - New `## Valkey administration UI` after `## Valkey`, with a `:factory:` mark like the other AI-written sections:
    - **Dev:** `docker compose up` (or `watch`), then `http://127.0.0.1:5540`, already connected as `cvestream-ui`.
    - **Kubernetes:** add `../../components/valkey-ui` to your overlay's `components:`, create the `cvestream-valkey-ui` Secret, and add a `cvestream-ui` line with your own hash to your `users.acl`. Then `kubectl -n <ns> port-forward svc/cvestream-valkey-ui 5540:5540` and open `http://127.0.0.1:5540`.
    - **Never expose it:** no Ingress, and no Service of type `LoadBalancer`/`NodePort`. It has no login: whoever reaches it controls the store and sees every token. In dev it is loopback-only on the host, but any container on the compose `default` network can reach it.
    - **Writing:** prefer the CLI, which validates and bumps the revision. After a UI edit, run `INCR cvestream:v1:revision` in the UI's CLI tab. Otherwise the daemon applies the edit at its next forced reload (every 12th run). An invalid edit is rejected (`config.rejected`) and the last valid configuration stays in use. Fix it from the UI or with `cvestream config import`.
    - **What it cannot do, on purpose:** set TTLs, rename keys, flush, run scripts, read the server configuration.
    - **Licence:** Redis Insight is SSPL-1.0 / Redis EULA. It is used as the unmodified upstream image, for internal administration only.
    - **Network:** it fetches feature flags from GitHub daily as direct egress from the UI container (in dev only, not through any proxy configuration; blocked by the NetworkPolicy on Kubernetes). Analytics are off.
    - **Port conflict:** if `127.0.0.1:5540` is already bound, `docker compose up` fails to start `valkey-ui`.
    - **Startup:** a missing or malformed pre-setup file leaves the UI with zero databases and a locked connection list; fix the secret and restart.
    - **Broken revision:** a non-integer `cvestream:v1:revision` (possible from a UI `SET`) makes every daemon write fail until repaired with `SET cvestream:v1:revision <integer>` or `cvestream config import`.
  - The `## Kubernetes` "What ships" table gains a `k8s/components/valkey-ui` row. The NetworkPolicy labels table says the UI pod uses `cvestream.io/valkey-access`.
- **`docs/config-contract.md`:**
  - TL;DR access bullet: the UI user reads tokens too.
  - "Who uses it" table: a row `Administration UI (Story 3.4) | cvestream-ui | everything under cvestream:v1:*, tokens included, plus key names through SCAN | everything under cvestream:v1:*`.
  - "Access rules (ACL)": "five users", the new line (with `<sha256>`), and rule bullets:
    - only `cvestream-ui` has `SCAN` (it is keyspace-wide);
    - the never-granted list;
    - `cvestream-ui` is granted `INFO`, whose clients section lists connected clients — this exposure is accepted (refusing `INFO` degrades the UI) and recorded here;
    - UI writes skip validation and the revision bump.
  - "Adding a connector user" step 4 mentions nothing new. A short "Production: the UI user" note: generate a hash as in step 1, and put the password in the component Secret's JSON.
- **`README.md`:** in the development part, after `docker compose watch`, say that the stack also starts the Valkey UI on `http://127.0.0.1:5540`, and link to operations. In the Quality gate list, add `kubectl kustomize tests/k8s/example-with-valkey-ui`. The Kubernetes quick start gets one line about the optional UI component.
- **`CLAUDE.md`:** in the gate list, add `kubectl kustomize tests/k8s/example-with-valkey-ui`. Optionally, in the key rules: "`SCAN` only for `cvestream-ui`; never widen another ACL user". Keep the file short.
- **`compose.yml` header:** the UI paragraph (see the compose shape).

### Testing standards for this story

- Unit tests read the raw files (`yaml.safe_load`, `json.loads`), with no kubectl, no network and no Docker. `tests/unit/test_deploy_manifests.py` already uses `cast(Manifest, ...)` for ty, so keep the typing style.
- Integration tests:
  - marker `valkey`, plus `pytest.mark.allow_hosts(support.ALLOWED_HOSTS)`;
  - without `VALKEY_TEST_ADDRESS` they skip locally and **fail on CI** (`support.require_server()` through the `admin` fixture; make the `ui` fixture call it too, or depend on `admin`);
  - CI starts Valkey with the repo's `deploy/valkey/`, so the new ACL line is live on CI without a workflow change.
- Use `capture_logs()` from `structlog.testing` for `config.rejected`, as the existing tests do. No sleeps, no real time.
- Refusals: `valkey.exceptions.NoPermissionError`. A command queued in `MULTI` fails with `ExecAbortError`, as in `_refused`.
- The manual checks (UI clicks, minikube admission) are recorded in the Completion Notes, not automated.

### Running the valkey tests locally

```bash
docker run -d --rm --name cvs-valkey -p 127.0.0.1::6379 \
  -v "$PWD/deploy/valkey:/usr/local/etc/valkey:ro" \
  valkey/valkey:9.1.3 valkey-server /usr/local/etc/valkey/valkey.conf
export VALKEY_TEST_ADDRESS=$(docker port cvs-valkey 6379 | head -1)
uv run pytest -m valkey -v
docker stop cvs-valkey
```

### Previous story intelligence

- **3.1:**
  - An ACL file takes **no comment line**.
  - valkey-py sends `INCRBY` for `incr()`, which is why both commands are granted.
  - valkey-py sends `CLIENT SETINFO` after `AUTH` (granted, so the ACL log stays clean). ioredis sends `CLIENT SETNAME` instead: grant `client|setname`, not the whole `client` command.
  - `SCAN` ignores key patterns; this was the reason it was banned for everyone.
  - The full reload runs every 12 pipeline runs (one run per source fetch).
- **3.3:**
  - The Kustomize load restrictor refuses generator files outside the overlay, but `resources:` and `components:` directories may be anywhere.
  - `kubectl kustomize` on this machine is 1.28.2 / kustomize v5.0.4, which supports `kind: Component`. The CI runner has kubectl 1.37.
  - The restricted PSS needs a numeric `runAsUser` when the image's `USER` is a name.
  - `kubectl port-forward` is not filtered by NetworkPolicy (harness finding).
  - The review added a pin-equality test for Valkey. Do the same for the UI pin (compose vs component).
  - Docs must say which password goes in which Secret, and how it must match `users.acl`.
- **3.2:**
  - The CLI is the recommended writer.
  - `config import` replaces a broken store.
  - A non-integer `revision` (possible from a UI `SET`) makes every write fail until it is fixed. `docs/operations.md` should say to fix it with `SET cvestream:v1:revision <integer>` or `cvestream config import`.
- **Reviews in epic 3** consistently flagged doc gaps around passwords and seeding. Have the docs answer "which password goes where" for the UI explicitly.

### Git intelligence

- The stack tip is `356d293 chore(story-3.2): record review findings`.
- Commit style per concern, for example:
  - `feat(story-3.4): add the cvestream-ui ACL user`
  - `test(story-3.4): prove the cvestream-ui grants and refusals`
  - `build(story-3.4): add the Valkey UI to the dev compose stack`
  - `feat(story-3.4): add the opt-in Kubernetes component for the Valkey UI`
  - `ci(story-3.4): build the Valkey UI component overlay`
  - `docs(story-3.4): document the Valkey administration UI`
  - `docs(story-3.4): amend the architecture for the Valkey UI`
  - `chore(story-3.4): mark story ready for review`
- `f7bf4fc` is the cherry-pick of `main`'s `bcc52b7` (course correction). When the stack merges and this branch is rebased onto `main`, git drops it as already applied.

### Latest technical information (checked 2026-11-07)

- Redis Insight `3.8.0` (2026-07-21), the latest on GitHub and Docker Hub. Docs: <https://redis.io/docs/latest/operate/redisinsight/configuration/>. The variables used here were checked in `redisinsight/api/config/default.ts` at tag `3.8.0`.
- Valkey image tag `9.1.3` (server version 9.1 — the `valkey/valkey:9.1` pin quoted in the stack plan and `project-context.md`), unchanged.
- Valkey Admin `1.3.0` (2026-10-29), Apache-2.0. Revisit it when it supports a pre-configured browser connection, because it would remove the SSPL dependency (add this to `deferred-work.md`).
- Kustomize `Component`: `apiVersion: kustomize.config.k8s.io/v1alpha1`, `kind: Component`, listed by an overlay under `components:`. Supported since kustomize v3.7; fine with kubectl 1.28 and later.

### Project Structure Notes

- New directories: `deploy/k8s/components/valkey-ui/` and `tests/k8s/example-with-valkey-ui/`. Both go in the `architecture.md` tree (the tree is authoritative: "no new top-level packages without amending it"; `tests/k8s/` is a test fixture directory, not a Python package, so it needs no `__init__.py`).
- New dev-secret file type: the `cvestream-ui.redisinsight.json` sits next to the `.password` files. The `dev-secrets/` comment in the tree changes.
- No `src/` change, so import-linter and the layering rules are unaffected.

### References

- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-07-valkey-admin-ui.md] (all sections; section 4 holds the exact OLD to NEW amendments)
- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.4: Valkey administration UI]
- [Source: _bmad-output/planning-artifacts/prds/prd-cvestream-2026-10-26/prd.md#Out of Scope (v1)] (clarified on 2026-11-07)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] (Interfaces, Valkey ACL users)
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (deployment targets), #Complete Project Directory Structure
- [Source: _bmad-output/project-context.md#Critical Don't-Miss Rules] (Security: Valkey bullet)
- [Source: _bmad-output/implementation-artifacts/3-1-valkey-configuration-provider.md#Server files] and #Full reload safety net
- [Source: _bmad-output/implementation-artifacts/3-3-release-and-kubernetes-deployment.md#Kubernetes design]
- [Source: docs/config-contract.md#Access rules (ACL)], [Source: docs/operations.md#Valkey], #Kubernetes
- [Source: src/cvestream/app/scheduler.py:41] (`FULL_RELOAD_EVERY = 12`), [Source: src/cvestream/pipeline/run.py] (`reload_config`)
- [Source: tests/integration/test_valkey_provider.py] (`_refused`, safety-net tests), [Source: tests/integration/valkey_support.py]
- Redis Insight 3.8.0: <https://github.com/redis/RedisInsight/tree/3.8.0> (`Dockerfile`, `LICENSE`, `redisinsight/api/config/default.ts`, `.../database-discovery/utils/pre-setup.discovery.util.ts`)

## Dev Agent Record

### Agent Model Used

- Mistral Vibe (bmad-dev-story), 2026-11-07.

### Debug Log References

- T5 empirical ACL pass: scratch copy of `deploy/valkey/` + a `debug` user (never committed), `valkey/valkey:9.1.3` and `redis/redisinsight:3.8.0` on a throwaway docker network; UI features driven through its HTTP API (`/api/databases/cvestream/keys`, `string`, `set`, `cli`), `ACL LOG` read after each pass. Containers and scratch removed afterwards.
- Compose runtime check: `docker compose up -d valkey valkey-ui`, seeded as `cvestream-admin`, browsed `cvestream:v1:*` through `http://127.0.0.1:5540/api/databases/cvestream/keys`; `docker compose port valkey 6379` publishes nothing. Stack torn down afterwards.

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created (bmad-create-story, claude-opus-5-5, 2026-11-07).
- T1: `cvestream-ui` line added after `cvestream-admin`, before `connector-example`; hash `f2725541f2eda15018b82945db0ba9411b0cfd0748c5ac8b0cbc9743f4c69f08` = `printf '%s' dev-only-cvestream-ui | sha256sum`. `cvestream-ui.password` (21 bytes, no trailing newline) and `cvestream-ui.redisinsight.json` added under `deploy/valkey/dev-secrets/`, both mode 0644 (the JSON was initially 0600, which made the UID-1000 container refuse to read the compose secret: "Permission denied", zero databases listed; chmod 644 fixed it — the unit test checks content, not mode).
- T5, settled empirically: the candidate line is final, unchanged. With it, the key browser (list, type, TTL, size), string view/edit, set view, key delete and the CLI tab (`INCR cvestream:v1:revision` returned 2) all work against `valkey/valkey:9.1.3` + `redis/redisinsight:3.8.0`. `ACL LOG` shows exactly three refused commands, all in the never-grant list and all degraded gracefully: `config|get` (falls back to `INFO`), `cluster|nodes` ("not a cluster"), `acl|cat`. No `hello` refusal observed, so `+hello` is not granted.
- T4 hardening verified at runtime: `read_only: true` + tmpfs `/data` and `/tmp` (uid/gid 1000), `cap_drop: [ALL]`, `no-new-privileges` — the container starts and serves with all of them; nothing had to be dropped. The pre-setup database `cvestream` is listed and connects with no click; `RI_DATABASE_MANAGEMENT=false` locks the list; no EULA screen with `RI_ACCEPT_TERMS_AND_CONDITIONS=true`.
- T6: the component builds via `tests/k8s/example-with-valkey-ui`. Note: the overlay that lists a component must itself set `namespace:` and `labels:` — the example overlay's transformations do not reach component resources (checked with kubectl 1.28.2 / kustomize v5.0.4); the test overlay therefore repeats `namespace: cvestream` and the `part-of` label. Built output: all four component resources in namespace `cvestream` with `app.kubernetes.io/part-of: cvestream`, the pod additionally labelled `cvestream.io/valkey-access: "true"`, the Secret kept under its plain name `cvestream-valkey-ui` (not hashed). `kubectl kustomize deploy/k8s/overlays/example` contains no `valkey-ui` resource.
- Tests: 16 unit tests in `test_deploy_valkey.py` (hash, namespace-only patterns, no `+@` category, the never-grant list in both `+cmd` and `+cmd|sub` forms, `+scan` only on `cvestream-ui`, pre-setup JSON == dev secret), 13 in `test_deploy_manifests.py` (component shape, restricted PSS contexts, pin equality compose vs component, ClusterIP/no Ingress, NetworkPolicy egress rules, opt-in label, overlay wiring, loopback-only ports, no `RI_REDIS_PASSWORD`, REPLACE-ME-only Secret), and `tests/integration/test_valkey_ui.py` (grants, 37 parametrized refusals, MULTI abort, outside-namespace keys, full write protocol, both safety nets, DEL of a structural key, non-integer revision and its repair). `DEBUG SLEEP 0` is refused by `enable-debug-command` before the ACL is consulted, so it is asserted as `ResponseError` separately; the ACL-side refusal is unit-tested.
- Gates 2026-11-07: `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check`, `lint-imports`, `pytest` (1661 passed, 6 deselected, with `VALKEY_TEST_ADDRESS` against a disposable `valkey/valkey:9.1.3` running the repo's `deploy/valkey/`), `cvestream config schema --check`, `cvestream config validate endpoints.example.yaml`, `kubectl kustomize` of base, example and `tests/k8s/example-with-valkey-ui` — all green. `docker build` not run locally (x509 behind the proxy); CI builds it.
- Not done here (manual, stack plan "PR 4 of 4"): browser click-through, minikube admission of the component under the restricted PSS.

### File List

- `deploy/valkey/users.acl` (modified: the `cvestream-ui` line)
- `deploy/valkey/dev-secrets/cvestream-ui.password` (new)
- `deploy/valkey/dev-secrets/cvestream-ui.redisinsight.json` (new)
- `compose.yml` (modified: `valkey-ui` service, `valkey-ui-databases` secret, header)
- `deploy/k8s/components/valkey-ui/kustomization.yaml` (new)
- `deploy/k8s/components/valkey-ui/deployment.yaml` (new)
- `deploy/k8s/components/valkey-ui/service.yaml` (new)
- `deploy/k8s/components/valkey-ui/networkpolicy.yaml` (new)
- `deploy/k8s/components/valkey-ui/secret.example.yaml` (new; a template, not a resource of the component, since the code review)
- `tests/k8s/example-with-valkey-ui/kustomization.yaml` (new)
- `tests/k8s/example-with-valkey-ui/secret-valkey-ui.yaml` (new, code review: the test overlay's placeholder Secret)
- `.github/workflows/ci.yml` (modified: kustomize build of the test overlay)
- `tests/unit/test_deploy_valkey.py` (modified)
- `tests/unit/test_deploy_manifests.py` (modified)
- `tests/integration/valkey_support.py` (modified: `UI`)
- `tests/integration/test_valkey_ui.py` (new)
- `docs/operations.md` (modified: "Valkey administration UI", What-ships row, labels table)
- `docs/config-contract.md` (modified: UI row, five users, new line, rule bullets, production note)
- `README.md` (modified: dev stack UI, gate list, Kubernetes quick start)
- `CLAUDE.md` (modified: gate list, SCAN/valkey-ui rule)
- `_bmad-output/planning-artifacts/architecture.md` (modified: Interfaces, ACL users, Dev/Kubernetes targets, tree, Assets)
- `_bmad-output/project-context.md` (modified: Security Valkey bullet, Images line)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified: UI pin + Valkey Admin note)
- `_bmad-output/implementation-artifacts/epic-3-stack-plan.md` (modified: 3.4 status row)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified: 3-4 → review)

### Change Log

- 2026-11-07: story created (bmad-create-story).
- 2026-11-07: implemented (bmad-dev-story): ACL user and dev secrets, unit and integration tests, compose service, Kustomize component and test overlay, CI build, docs, pattern amendments; gates green; status review.
- 2026-11-07: code review (bmad-code-review): 3 decisions resolved (`SET ... EX` accepted and documented; Secret template out of the component; `DEL endpoints` tested and documented, daemon guard deferred), 19 patches applied (Redis Insight stdout logger, docs corrections, stricter ACL unit guard for every user, refusal and grant coverage, structural-key deletions, manifest tests); gates green with a local Valkey 9.1.3; status done.
