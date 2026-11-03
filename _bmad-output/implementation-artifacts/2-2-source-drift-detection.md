---
baseline_commit: 0c1903d
---

# Story 2.2: Source-drift detection

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to be told when a source API changes,
so that a silent upstream change never empties our feeds.

Rewritten by the 2026-11-01 course correction (`_bmad-output/planning-artifacts/sprint-change-proposal-2026-11-01-epic-2-scope.md`): the configured generic sources (FR-5) left this story, which is now only the nightly `live` workflow (NFR-5). The `live` contract tests for EUVD and CISA KEV already exist since story 1.4, but nothing runs them.

## TL;DR

- **One new file does the work:** `.github/workflows/source-drift.yml`. It runs nightly (`schedule`) and by hand (`workflow_dispatch`), runs `uv run pytest -m live tests/contract`, and on failure opens **one** issue labelled `source-drift`, or comments on the one already open.
- **No Python change at all:** no `src/` file, no test file, no dependency, no setting, no `uv.lock` change, no `schemas/` change. The live tests already exist (`tests/contract/euvd/test_euvd.py`, `tests/contract/cisa_kev/test_cisa_kev.py`).
- **Select tests by marker** (`-m live`), never by a list of files or sources, so the CERT-EU `live` test from story 2.1 (and any later adapter) runs without editing the workflow.
- **Security:** top-level `permissions: {}`, job `contents: read` + `issues: write`, `GITHUB_TOKEN` only, actions pinned by the **same SHAs as `ci.yml`**, `persist-credentials: false`, `timeout-minutes`, no `${{ }}` inside `run:` scripts (values go through `env:`), issue body written to a file (`--body-file`).
- **Gotcha:** a `run:` step with no `shell:` runs `bash -e` **without `pipefail`**, so `pytest | tee` would always succeed. Set `defaults.run.shell: bash` (which is `bash --noprofile --norc -eo pipefail`).
- **Docs in the same PR:** a `## Source drift` section (and a TL;DR bullet) in `docs/operations.md`; one line in `README.md` (quality gate paragraph on `live` tests). `architecture.md`, `project-context.md` and `CLAUDE.md` already describe this workflow: no change.
- **Verify locally** with `uvx --from actionlint-py actionlint` and `uvx zizmor --offline` (both clean on `ci.yml` and on a prototype of this workflow, checked on 2026-11-03), plus a dry run of the reporting script with a stub `gh`. The real GitHub checks only work after the merge to `main` (scheduled and dispatched workflows run from the default branch).
- **Parallel with story 2.1:** this branch starts from `story-2-1-cert-eu-adapter` at the plan commit and is rebased onto the reviewed 2.1 branch later. Shared files: `README.md` and `sprint-status.yaml` only.

## Acceptance Criteria

1. **Triggers and test run (NFR-5).**
   **Given** `.github/workflows/source-drift.yml`
   **When** it runs nightly (`on.schedule`, one cron entry) or manually (`on.workflow_dispatch`)
   **Then** it installs the project with `uv sync --locked` and runs the `live` contract tests against the real APIs with `uv run pytest -m live tests/contract`, selected by marker only, so the EUVD, CISA KEV and (once 2.1 is below this branch) CERT-EU `live` tests all run.

2. **One issue, opened or updated, with the failing tests (NFR-5).**
   **Given** a run where the live tests (or any earlier step) fail
   **When** the reporting step runs (`if: failure()`)
   **Then** if an **open** issue labelled `source-drift` exists, it adds a comment to it; otherwise it opens one issue labelled `source-drift`; it never opens a second issue while one is open, and two runs never race (workflow-level `concurrency` group, `cancel-in-progress: false`)
   **And** the body names the failing tests (the `FAILED`/`ERROR` lines of pytest's short summary) and links the run; when the job failed before pytest produced a result, the body says so instead of being empty
   **And** a green run opens nothing and comments nothing.

3. **Least privilege and supply chain.**
   **Given** the workflow file
   **Then** top-level `permissions: {}` and the job's `permissions:` are exactly `contents: read` and `issues: write`
   **And** every third-party action is pinned by full commit SHA with the version in a trailing comment, using the same SHAs as `ci.yml` (`actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.3.2); checkout has `persist-credentials: false`
   **And** the job has a `timeout-minutes`
   **And** it uses only `GITHUB_TOKEN` (no extra secret), and no `${{ }}` expression appears inside a `run:` script (inputs reach the script through `env:`).

4. **Operator documentation.**
   **Given** `docs/operations.md`
   **Then** a `## Source drift` section explains what the workflow does and when, what a `source-drift` issue means (an upstream API or payload changed, or the source was down or unreachable that night), how to triage it, how to fix it (adapter, re-recorded fixtures, `docs/sources.md`), how to close it (by hand, once the fix is on `main` and a run is green) and how to run it by hand
   **And** the TL;DR of `docs/operations.md` and the `README.md` quality-gate paragraph mention the nightly run.

5. **No regression.** Every gate in `CLAUDE.md` stays green; `ci.yml`, `pyproject.toml`, `uv.lock`, `src/` and `tests/` are unchanged; the default `uv run pytest` still deselects the `live` tests.

## Tasks / Subtasks

- [ ] **Task 1: write `.github/workflows/source-drift.yml`** (AC: 1, 2, 3)
  - [ ] 1.1 Triggers: `schedule` with one cron entry (recommended `"17 4 * * *"`, see "Decisions taken" below) and `workflow_dispatch` (no inputs).
  - [ ] 1.2 Top level: `name: source-drift`, `permissions: {}`, `concurrency: {group: source-drift, cancel-in-progress: false}`, `defaults: {run: {shell: bash}}`.
  - [ ] 1.3 One job (e.g. `live`), `runs-on: ubuntu-24.04` (same as `ci.yml`), `timeout-minutes: 20`, `permissions: {contents: read, issues: write}`, `env: {UV_LOCKED: "1"}`.
  - [ ] 1.4 Steps, copied from `ci.yml` for the first three: `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1` with `persist-credentials: false`; `astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2` with `version: "0.13.5"` and `enable-cache: true`; `run: uv sync --locked`.
  - [ ] 1.5 Test step: `uv run pytest -m live tests/contract -v -rfE 2>&1 | tee pytest.log` (relies on 1.2's `pipefail`).
  - [ ] 1.6 Report step with `if: failure()` and `env: GH_TOKEN: ${{ github.token }}`, `RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}`: build `body.md` from `grep -E '^(FAILED|ERROR) ' pytest.log` (fallback text when empty or when `pytest.log` does not exist), ensure the label exists (`gh label create source-drift --force ...`), find the open issue (`gh issue list --label source-drift --state open --json number --jq '.[0].number // empty'`), then `gh issue comment <n> --body-file body.md` or `gh issue create --title ... --label source-drift --body-file body.md`. Always pass `--repo "$GITHUB_REPOSITORY"`.
  - [ ] 1.7 Header comment in the file: what it does, that it runs from the default branch only, and that tests are selected by marker on purpose.
- [ ] **Task 2: verify the workflow locally** (AC: 2, 3)
  - [ ] 2.1 `uvx --from actionlint-py actionlint .github/workflows/source-drift.yml` (also runs shellcheck on `run:` scripts when `shellcheck` is installed; it is, at `/usr/bin/shellcheck`): no finding.
  - [ ] 2.2 `uvx zizmor --offline .github/workflows/source-drift.yml`: no finding (no `template-injection`, `excessive-permissions`, `unpinned-uses`, `artipacked`). Behind the proxy, `uvx` needs `SSL_CERT_FILE=$HOME/ca.pem` to download the tools.
  - [ ] 2.3 Dry run of the report script: copy its body to a scratch script, put a stub `gh` first on `PATH` that prints its arguments (and prints a number or nothing for `issue list`), and run it with (a) a `pytest.log` holding two `FAILED` lines, (b) no `pytest.log`, (c) the stub returning an open issue number. Check that (a) and (b) call `issue create` with a sensible `body.md`, and (c) calls `issue comment`. Do not commit the scratch script.
  - [ ] 2.4 `uv run pytest -m live tests/contract --collect-only -q` lists every live test (2 today, 3 or more once 2.1 is below); optionally run them for real (`uv run pytest -m live tests/contract -v -rfE`). Behind the corporate proxy they fail on purpose (`trust_env=False`): that is expected, do not add proxy support.
- [ ] **Task 3: document it** (AC: 4)
  - [ ] 3.1 `docs/operations.md`: new `## Source drift` section (place it after `## Logs`, before `## Feeds`, or after `## Limits`; your call) covering the points of AC 4; see "What `docs/operations.md` must say" below.
  - [ ] 3.2 `docs/operations.md` TL;DR: one bullet ("A nightly GitHub workflow runs the live contract tests and opens or updates one `source-drift` issue when a source changed.").
  - [ ] 3.3 `README.md`, "Quality gate" paragraph: "Tests marked `live` hit real services and are deselected by default" gains "; `.github/workflows/source-drift.yml` runs them nightly (see `docs/operations.md`)".
- [ ] **Task 4: gates and story bookkeeping** (AC: 5)
  - [ ] 4.1 Run every gate from `CLAUDE.md` in the worktree (`docker build --target runtime -t cvestream:story-2-2-source-drift-detection .` included).
  - [ ] 4.2 Fill the Dev Agent Record, the File List, set the story to `review` here and in `sprint-status.yaml`.

## Dev Notes

### Scope

- **In:** `.github/workflows/source-drift.yml` (new), `docs/operations.md`, `README.md`, this story file, `sprint-status.yaml`.
- **Out:** any change to `src/`, `tests/`, `pyproject.toml`, `uv.lock`, `schemas/`, `ci.yml`, `dependabot.yml`, `settings.example.yaml`. No new `live` test (2.1 adds CERT-EU's; EUVD and CISA KEV have theirs since 1.4). No auto-close of the issue on a green run (the AC and the stack plan say "close by hand"). No Slack or e-mail notification (GitHub's issue notifications are the alert). No actionlint/zizmor job in `ci.yml` (could be a later chore; not asked).
- **Not a pattern change:** the workflow is already in `architecture.md` (GitHub Actions table: "`source-drift.yml` | nightly + manual | contract tests against the live APIs; on failure opens/updates an issue labelled `source-drift`"; project tree `.github/workflows/source-drift.yml`; NFR-5 mapping). So `architecture.md` and `project-context.md` are not edited.

### Current state of the files this story touches or relies on

- **`.github/workflows/ci.yml`** (read, not modified): `permissions: {}` at the top, `ci` job with `contents: read`, `UV_LOCKED: "1"`, checkout v7.0.1 `3d3c42e5aac5ba805825da76410c181273ba90b1` with `persist-credentials: false`, setup-uv v10.3.2 `07a047e608db84b3d8c06cd9a7c7dcc9c355c18e` with `version: "0.13.5"` and `enable-cache: true`, `runs-on: ubuntu-24.04`. It runs `uv run pytest`, which deselects `live`. It has no `defaults.run.shell` because it never pipes. Copy its step style; do not touch it. SHAs re-checked against the tags with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` on 2026-11-03 (both lightweight tags pointing at commits).
- **`.github/dependabot.yml`**: `github-actions` ecosystem weekly, directory `/`. It bumps every workflow in `.github/workflows/`, so pinning the same SHAs as `ci.yml` keeps both in step. setup-uv v10.3.3 exists (2026-10-28): do **not** bump it here, Dependabot will do both files together.
- **`pyproject.toml` `[tool.pytest.ini_options]`**: `addopts = ["--disable-socket", "--allow-unix-socket", "-m", "not live", "--strict-markers"]`. A command-line `-m live` overrides the `-m "not live"` from `addopts` (the last `-m` wins; verified: `uv run pytest -m live tests/contract --collect-only -q` gives `2/108 tests collected (106 deselected)`). `--disable-socket` stays on; each live test lifts it with `@pytest.mark.enable_socket`. So the workflow needs **no** extra pytest flag for the network, and must not pass `--allow-hosts` or `--force-enable-socket`.
- **Live tests today:** `tests/contract/euvd/test_euvd.py::test_live_euvd_fetch_matches_the_recorded_shape` (real `HttpClient` + `SystemClock`, cursor = now − 1 day, so it pages one day of EUVD with `page_pause`; expect a minute or two) and `tests/contract/cisa_kev/test_cisa_kev.py::test_live_cisa_kev_catalogue_matches_the_recorded_shape` (full catalogue). They assert shape-level facts only. Story 2.1 adds `tests/contract/cert_eu/` with its own `live` variant(s).
- **`cvestream.http`**: `trust_env=False`, system trust store. On a GitHub-hosted runner there is no proxy and the system store is the Ubuntu one: nothing to configure.
- **`docs/operations.md`**: sections TL;DR, Run, Startup and shutdown, Health, `/status`, Logs, Feeds, Token rotation, Security, Backup, Limits. Add one section and one TL;DR bullet; keep everything else.
- **`README.md`** "Quality gate": the sentence "`pytest` blocks the network (pytest-socket). Tests marked `live` hit real services and are deselected by default; tests marked `valkey` need a Valkey server." Extend it; keep the rest. Story 2.1 will also edit `README.md` (source list in the intro), a different paragraph.
- **Repository facts (checked 2026-11-03):** `wixyvir/cvestream` is **private**, default branch `main`, default workflow token permissions `read`, the label `source-drift` **does not exist yet** (existing labels: the GitHub defaults plus `dependencies`, `docker`, `github_actions`, `python:uv`). Private repos consume Actions minutes: one nightly run of a few minutes is negligible, but say so in the docs.

### Workflow design (reference sketch, verified with actionlint 1.7.12 and zizmor 1.32.0: no finding)

```yaml
name: source-drift

# Runs the `live` contract tests against the real source APIs every night and
# on demand. Tests are selected by marker, not by file: a new adapter's live
# test runs here without editing this file. Scheduled and dispatched runs use
# the workflow file of the default branch.
on:
  schedule:
    - cron: "17 4 * * *"
  workflow_dispatch:

permissions: {}

# One run at a time, so two runs cannot both open an issue.
concurrency:
  group: source-drift
  cancel-in-progress: false

defaults:
  run:
    shell: bash # bash -eo pipefail: `pytest | tee` must fail when pytest fails

jobs:
  live:
    name: live contract tests
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions:
      contents: read
      issues: write
    env:
      UV_LOCKED: "1"
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e # v10.3.2
        with:
          version: "0.13.5"
          enable-cache: true
      - run: uv sync --locked
      - name: Live contract tests
        run: uv run pytest -m live tests/contract -v -rfE 2>&1 | tee pytest.log
      - name: Open or update the source-drift issue
        if: failure()
        env:
          GH_TOKEN: ${{ github.token }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          # build body.md, ensure the label, comment on the open issue or create one
```

Notes on the sketch:

- **`pipefail`.** GitHub runs a `run:` step without `shell:` as `bash -e {0}` (no `pipefail`); with `shell: bash` it is `bash --noprofile --norc -eo pipefail {0}`. Without it the test step is always green and the report step never fires. This is the main trap of the story. Check the current GitHub "Workflow syntax" page (`jobs.<job_id>.steps[*].shell`) before relying on it, as for any configuration item not yet used in this project (`schedule`, `workflow_dispatch`, `concurrency` at workflow level is already used by `ci.yml`, `defaults.run.shell`, `timeout-minutes`, `if: failure()`).
- **`if: failure()`** fires when any earlier step of the job failed: a failed `uv sync` or checkout also reports. That is wanted (it is still "the nightly check is broken"), and the body must then say that no test result exists rather than show an empty block. A job-level timeout (`timeout-minutes` on the job) cancels the job, and `failure()` is false for a cancelled job, so a timed-out run reports nothing. Option, if you can confirm it in the GitHub docs: also put a shorter step-level `timeout-minutes` (e.g. 15) on the test step, so a hung API fails that step and the report step still runs inside the job's 20 minutes. If unsure, keep the job timeout only and document that a timed-out run is visible only in the Actions tab. Do not use `always()`.
- **pytest exit code 5** (no test collected, e.g. the marker was renamed) is a failure too, and gives a body with no `FAILED` line: the fallback text covers it.
- **No template injection.** `${{ }}` is used only in `env:` values, never inside the `run:` text. Failing test names come from `pytest.log` at run time, never from an expression. Write the body to a file and pass `--body-file`, never `--body "$var"` built by string interpolation of untrusted text into a command line (zizmor's `template-injection` audit).
- **Label.** `gh label create source-drift --repo "$GITHUB_REPOSITORY" --color B60205 --description "A live contract test failed: a source API may have changed" --force` creates it or updates it in place (`--force`: "Update the label color and description if label already exists"), so the first failing run works on a repo without the label and no error is swallowed. Creating a label needs `issues: write`, which the job has. Do not use `|| true` or `2>/dev/null` to hide failures.
- **Finding the open issue.** `gh issue list --repo "$GITHUB_REPOSITORY" --label source-drift --state open --json number --jq '.[0].number // empty'` prints the newest open one or nothing (checked: prints nothing on this repo today). If several are open (someone opened one by hand), commenting on the newest one is fine.
- **Body.** Suggested shape: a one-line summary, the run link (`$RUN_URL`), the commit tested (`$GITHUB_SHA`), the failing lines in a fenced block, and "Triage: see `docs/operations.md`, section Source drift". Use the same body for the issue and for later comments. Keep it short: pytest's `-rfE` lines already contain the assertion message. The fixed title: `Source drift: live contract tests failing`.
- **`gh` on the runner.** The GitHub CLI is preinstalled on `ubuntu-24.04` runners and reads `GH_TOKEN`. No third-party action is needed (do **not** add `actions/github-script` or an issue-creation action: one less pinned dependency).
- **Do not upload `pytest.log` as an artifact** (not asked; the run log already holds it).
- **No `if: github.repository == ...` guard** needed: the repo is private and scheduled workflows do not run on forks by default.

### Decisions taken autonomously (the stack plan lists them as points for a human decision)

Recorded here so the reviewer and the user can overturn them; each is a one-line change.

1. **Cron time:** `17 4 * * *` (04:17 UTC, 05:17 or 06:17 Paris). Off the hour, because GitHub delays or drops scheduled runs at busy times (the top of the hour is the busiest); before the working day, so an issue is waiting in the morning.
2. **Issue body format:** summary, run link, commit SHA, the `FAILED`/`ERROR` lines in a code block, pointer to `docs/operations.md`. Later failures add a comment with the same format, so the issue keeps the history of nights.
3. **The workflow creates the `source-drift` label itself** (idempotent `gh label create --force`), so nothing has to be prepared by hand before the first failure. `docs/operations.md` says so.
4. **The workflow never closes the issue.** Closing stays a human action after the fix, as the AC and the stack plan say. Auto-closing on the next green run would hide a flaky source.
5. **One job, one runner, `timeout-minutes: 20`.** The EUVD live test pages one day of records with a 1.5 s pause between pages; 20 minutes leaves a wide margin and stops a hung API from burning the private repo's minutes.

### What `docs/operations.md` must say (section `## Source drift`)

- **What runs:** `.github/workflows/source-drift.yml`, every night at the cron time (UTC) and on demand, from `main`. It runs `uv run pytest -m live tests/contract`: every contract test marked `live` (today EUVD and CISA KEV, plus CERT-EU), against the real APIs, with the same HTTP client as the daemon. It consumes a few Actions minutes per night (private repository).
- **What a `source-drift` issue means:** a live test failed. Either the source changed its API or payload (fields renamed, type changed, ordering, pagination, URL), which would make the daemon skip records or fail the source, or the source was down, slow or rate-limiting that night. The workflow opens one issue, then adds a comment on each later failing night while it stays open. The label is created by the workflow on its first failure. A run that fails before the tests (checkout, `uv sync`) also reports, with "no test result". A run that hits `timeout-minutes` is cancelled and reports nothing: look at the Actions tab.
- **Triage:** open the run linked in the issue; reproduce locally with `uv run pytest -m live tests/contract -v -rfE` (fails behind a TLS-intercepting proxy by design, run it from a direct connection); compare the live payload with the recorded fixtures in `tests/fixtures/<source>/` (how each was recorded: `tests/fixtures/README.md`); check the daemon side: `source.failed` with `SourceContractError` or `SourcePayloadError` in the logs, `/status` `last_error` and `stale` (links to the Logs and `/status` sections). One failing night followed by green ones is a transient outage: close the issue with a note.
- **Fix:** update the adapter in `src/cvestream/sources/`, re-record the fixtures (never hand-edit them), update the contract tests and `docs/sources.md`, in a PR.
- **Close:** by hand, once the fix is on `main` and a run is green (next night, or `gh workflow run source-drift.yml --repo wixyvir/cvestream` then `gh run watch`). The workflow never closes it.
- **Run it by hand:** Actions tab, "source-drift", "Run workflow", or `gh workflow run source-drift.yml --repo wixyvir/cvestream [--ref <branch>]`. A `--ref` other than `main` runs that branch's copy of the workflow (useful to test a change), but the workflow must exist on `main` first.

Write it in the style of the rest of `docs/operations.md` (short sentences, tables where they help, no marketing). It is documentation written by an AI: the PR body carries `:factory:`; the doc itself follows the existing docs, which do not.

### Testing standards for this story

- There is no Python code to test. "Tests" here are: actionlint + shellcheck, zizmor, the stub-`gh` dry run of the report script (Task 2.3), `--collect-only -m live`, and every CLAUDE.md gate (unchanged results: same pass count as the base branch, `live` deselected).
- Do not add a pytest test that parses the workflow YAML: `tests/unit/` mirrors `src/cvestream/`, and the workflow has no module. actionlint and zizmor are the checks.
- The end-to-end checks (green dispatched run opens nothing; two failing runs give one issue plus one comment) can only run once the file is on `main`; they are listed for the user in `_bmad-output/implementation-artifacts/epic-2-stack-plan.md` section 3.2, steps 5 and 6. Note in the Completion Notes that they were not run.

### Parallel work with story 2.1 (files both stories may touch)

Story 2.1 (`story-2-1-cert-eu-adapter`, CERT-EU adapter) is created and developed at the same time on its own branch. This branch was cut from it at the plan commit `0c1903d` and is rebased onto the reviewed 2.1 branch before this story's review. Do not depend on any 2.1 code.

| File | 2.1 | 2.2 | Conflict risk |
|---|---|---|---|
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | `epic-2`, `2-1-...` lines | `epic-2`, `2-2-...` lines | Likely, trivial: keep 2.1's line from the base and 2.2's from this branch; `epic-2: in-progress` on both |
| `README.md` | intro source list (CERT-EU) | "Quality gate" paragraph | Low (different paragraphs) |
| `docs/operations.md` | maybe (a `cert_eu` example in `/status` or logs) | new `## Source drift` section | Low |
| `docs/sources.md`, `settings.example.yaml`, `docs/configuration.md`, `src/cvestream/sources/*`, `tests/contract/cert_eu/`, `tests/fixtures/cert_eu/`, `tests/fixtures/README.md`, `pyproject.toml`, `uv.lock` | yes | no | none |
| `.github/workflows/source-drift.yml` | no | new | none |

After the rebase, re-run `uv run pytest -m live tests/contract --collect-only -q`: the CERT-EU live test(s) must show up without any change to the workflow.

### Previous story intelligence

- **Story 1.1 (CI):** all action SHAs were resolved from the tags and re-checked with `gh api`; `ci` stays the only required check (do not name this job `ci` and do not make it required: it hits the network and would block merges on an upstream outage); `ci.yml` has no `branches:` filter for stacked PRs. `permissions: {}` at the top plus per-job permissions is the established style.
- **Story 1.4 (sources):** each contract module has a `live` variant with `@pytest.mark.live` and `@pytest.mark.enable_socket`; "`source-drift.yml` (story 2.2) will run them"; they may fail behind a proxy by design (do not add proxy support); EUVD is unversioned and already broke older clients (`/api/vulnerabilities` now 404), so the order check, contract checks and live tests are the drift alarms. Review decision: the EUVD short-page stop was replaced by counting against `total`, logging `source.paging_incomplete`.
- **Deferred work** (`deferred-work.md`): "a wrong-typed optional field drops the whole record ... revisit if drift shows up". This workflow is how such drift would show up; you can mention it in the triage text, but do not change `deferred-work.md`.
- Commits so far follow `feat|fix|docs|test|chore(story-x.y): ...`, one concern each, never squashed; CI-only changes use `ci(story-x.y)` (listed in the stack plan).

### Delivery (stacked PR)

- Branch `story-2-2-source-drift-detection`, PR base `story-2-1-cert-eu-adapter`, draft PR opened at story creation; push implementation commits on top.
- Suggested commits: `ci(story-2.2): add the nightly source-drift workflow`, `docs(story-2.2): document source-drift issues in operations`, `docs(story-2.2): mention the nightly live run in the README`, `chore(story-2.2): mark story ready for review`. Every commit message ends with the line `🤖 Généré avec Claude Code`.
- Never push to `main`; `git push --force-with-lease` only after a rebase.

### Project Structure Notes

- `.github/workflows/source-drift.yml` is already in the architecture tree. No `.github/scripts/` directory (it is not in the tree): keep the report logic inline in the step; it is about 20 lines.
- No conflict with the unified structure detected.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.2: Source-drift detection]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-11-01-epic-2-scope.md#4.1 Epics] (story 2.2 rewrite, success criterion "a failing `live` test opens or updates exactly one `source-drift` issue")
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] (GitHub Actions table, pinning, least privilege, Dependabot)
- [Source: _bmad-output/planning-artifacts/architecture.md#Implementation Patterns] (markers: `live` run by `source-drift.yml`; pytest-socket)
- [Source: _bmad-output/planning-artifacts/architecture.md#Requirements to Structure Mapping] (NFR-5 → `sources/base.py`, `tests/contract/*`, `.github/workflows/source-drift.yml`)
- [Source: _bmad-output/project-context.md#Testing Rules] and `#Development Workflow Rules` (GitHub Actions pinned by SHA, least-privilege `permissions:` per job; operational change → `docs/operations.md`)
- [Source: _bmad-output/implementation-artifacts/epic-2-stack-plan.md#3.2 Story 2.2: source-drift detection] (what the user will check)
- [Source: _bmad-output/implementation-artifacts/1-1-project-skeleton-container-and-ci.md] (action SHAs, CI conventions)
- [Source: _bmad-output/implementation-artifacts/1-4-euvd-and-kev-ingestion.md] ("Live variants", drift alarms)
- [Source: .github/workflows/ci.yml], [Source: pyproject.toml#tool.pytest.ini_options], [Source: docs/operations.md], [Source: README.md#Quality gate]
- GitHub docs to check while implementing: "Workflow syntax for GitHub Actions" (`on.schedule`, `on.workflow_dispatch`, `defaults.run.shell`, `jobs.<job_id>.timeout-minutes`, `concurrency`), "Events that trigger workflows" (`schedule` runs on the default branch, may be delayed at busy times), "Controlling permissions for GITHUB_TOKEN"; `gh issue list|create|comment --help`, `gh label create --help`.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Story created by `bmad-create-story` (autonomous run, 2026-11-03). Ultimate context engine analysis completed: comprehensive developer guide created. Choices made without a human: see "Decisions taken autonomously" (cron time, body format, label creation, no auto-close, timeout).

### File List
