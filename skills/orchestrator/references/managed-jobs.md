# Managed Herdr jobs

## Contract

Use `scripts/herdr-jobs.py` for Herdr worker fleets in an authorized host session. It validates launch policy and worktree bindings, enforces shared provider caps, journals effects, observes startup and settled panes, and collects artifacts. Use `run` for an initial manifest, `add` to launch exactly one more job into the same persistent run, `resume` to continue launch and observation, `status` for observation only, and `finish` after collection to validate worker results, record dispositions, release leases, and close owned workspaces. The coordinator supplies intent and judges the results. Current explicit instructions take precedence through an `override` record; its instruction must reflect real authorization.

Success has separate layers: a helper operation returned; a worker settled; its receipt and artifacts were collected; and the coordinator accepted its work. A `collected` batch still has `acceptance: pending`. Missing files, malformed receipts, and uncertain effects remain visible. Managed Codex jobs pass the complete prompt as argv to a fresh `agent start`, then verify the owned identity and visible `Working` state before marking submission and observing it. A Herdr startup timeout is expected for this prompt-bearing launch, but is not delivery evidence by itself. The helper never answers approval dialogs or blindly repeats a timed-out prompt.

The structured [launch policy](../launch-policy.json) owns executable routing/defaults. Existing CLI behavior is bound by a verified host-contract file. Read [host integration](host-integration.md) before first live use or when the installed CLI/server changes. Live control is unavailable in a jail. Offline preview and tests work there.

## Call

Paths to the entrypoint and policy are relative to this skill; use absolute paths in calls made from elsewhere. Keep the same run directory for the lifetime of a request.

```sh
python3 scripts/herdr-jobs.py run --manifest request.json --policy launch-policy.json --run-dir RUN_DIR --preview
python3 scripts/herdr-jobs.py run --manifest request.json --policy launch-policy.json --run-dir RUN_DIR --host-contract HOST_CONTRACT --wait-seconds 30
python3 scripts/herdr-jobs.py add --manifest one-job.json --policy launch-policy.json --run-dir RUN_DIR --host-contract HOST_CONTRACT --wait-seconds 30
python3 scripts/herdr-jobs.py resume --run-dir RUN_DIR --wait-seconds 30
python3 scripts/herdr-jobs.py status --run-dir RUN_DIR --wait-seconds 30
python3 scripts/herdr-jobs.py status --run-dir RUN_DIR --wait-seconds 0 --summary
python3 scripts/herdr-jobs.py finish --run-dir RUN_DIR --wait-seconds 30
python3 scripts/herdr-jobs.py cleanup-stale
python3 scripts/herdr-jobs.py cleanup-stale --apply
python3 scripts/herdr-jobs.py close-workspace WORKSPACE_ID
```

`status --summary` prints one fixed table row per job (`job`, `pane`, `state`,
`disposition`, `lease`); missing values are `-`. With `--wait-seconds 0`, it
reports local checkpoint state only. Plain `status` retains its JSON output.

`cleanup-stale` defaults to a read-only report. It maps held provider leases
for settled jobs to managed run state and, with `--apply`, delegates mapped
settled runs to `finish` so leases are released before owned workspaces close.
Unsettled managed jobs are retained. An orphan lease is released by ID with
reconciliation evidence only when its admission record names a launch pane and
Herdr confirms a finished agent at a shell prompt there; an orphan without a
pending launch effect, or without that confirmation, is retained and refused. The command never closes an unknown workspace. Inspect the report and
reconcile a refused lease through its owning run.
Do not use raw `herdr workspace close` for lease-bound panes; use this command
or `finish --run-dir RUN_DIR` so admission capacity is reconciled first. The
`close-workspace` guard refuses a workspace with a held managed lease and names
the run directory. It also refuses an unclosed managed workspace whose slot was
already released. The coordinator's `finish-task.py` cleanup helper uses this
same guard. For managed jobs, use `herdr-jobs.py finish`; ordinary workspaces
retain Herdr's existing close behavior.

If an owned worker workspace was closed externally, run `finish --run-dir
RUN_DIR` with a positive observation budget to reconcile it, then inspect its
per-job issues. The helper requires a move recorded for the current attempt and
pane, plus an authoritative Herdr workspace list confirming the saved workspace
is absent. A missing agent/pane response alone does not establish absence. It
collects any matching receipt and artifacts and checkpoints an
`external_workspace_closed` history record before releasing that attempt's
provider slot. The record includes the resource identity, receipt digest when
available, remaining pending effect, and the admission lease with its unknown
effect metadata when present. A lease naming a different run or effect owner is
retained for inspection. Later `resume` calls retain that
retirement and never relaunch the attempt or restore its stale reservation.
Positive-budget observation can still collect a late matching receipt for a
retired attempt without settling it or discarding its unresolved effects.

This retires resources, not the worker outcome: `workspace_closed` is true,
`settled` remains false, and `issue_code: external_workspace_closed` requests
coordinator review. `finish_complete` remains false; a matching receipt is still
subject to review. Missing or invalid receipts and unknown start/prompt/jail
effects remain visible as collection errors or unresolved effects. Neither a
`DONE` file nor workspace disappearance proves success. An ambiguous split,
move, or retry-setup effect cannot be retired this way, because the saved
workspace does not establish where all of its resources ended up. Missing
ownership evidence, an open workspace, or a failed workspace-list call also
retains the reservation for inspection.

Loading a checkpoint (including zero-budget `status`) reads actual admission
ownership without acquiring a slot. Positive-budget observation reconciles
retired resources before restoring reservations for workers that remain active
or ambiguous. Do not manually release a live or ambiguously owned worker's slot.

`finish` maps worker outcomes to coordinator dispositions using the worker
contract: `ready` → `completed`, `blocked` → `blocked`, `partial` → `failed`,
and `failed` → `failed`. It validates result records, confirms each receipt lists `result.json`
and every declared artifact file, records dispositions, releases provider leases,
and closes settled workspaces. A successful cleanup returns `next_action.kind`
`review`; it does not accept the work, and `acceptance` remains pending.

For a repository-writing job, `finish` compares the candidate's branch, head,
and base with the leased worktree. It normally releases the repository-worktree
lease only when that candidate is clean and its head is merged into the
repository's local `main` branch. A dirty, mismatched, or unmerged worktree
stays leased and the per-job finish result explains why. Use
`lease-worktree.py status` to inspect it. For managed jobs, a coordinator
disposition override to `failed` with its required reason records abandonment
and lets `finish` force-release that job's unmerged or dirty worktree. Direct
use of `lease-worktree.py release` requires `--who` and `--why`, and requires
`--force` to remove dirty or unmerged worktrees.

For an opted-in `local_lane` job, `finish` also runs the declared independent
validator against the collected output directory. Record coordinator review
with `--coordinator-grade JOB_ID=accepted|rework|rejected`; acceptance is
refused until the validator passes. The final result records both the grade
and validator status. A failed validator keeps an accepted or rework job open;
the coordinator can reject it to close the attempt while preserving the failed
validation evidence.

The coordinator can explicitly change a worker disposition for a specific job
with the repeatable `--coordinator-disposition-override JOB_ID=DISPOSITION`
flag and a non-empty `--coordinator-override-reason`. The reason is recorded in
the run checkpoint and disposition evidence. A saved override remains in force
for later `finish` retries.

```sh
python3 scripts/herdr-jobs.py finish --run-dir RUN_DIR \
  --coordinator-disposition-override job-1=completed \
  --coordinator-override-reason "Reviewed the blocker and approved completion."
```

An added manifest must use the existing `request_id`, pinned policy, and run
concurrency, and contain one unique `job_id`. Its optional `retry_override`,
when supplied, must match the run's pinned value; omit it to inherit that value
for matching workers in the added jobs. Additions append to `state.json`;
they do not replace earlier attempts, receipts, or collections. Use the same
run directory for the lifetime of that fleet. `finish` does not launch work and
normally closes only jobs that are settled and have a valid collected receipt. A missing
workspace ID or failed worker-result validation leaves the job open with an
actionable issue.

Immediately before an ordinary Codex launch, the helper writes trust for the
canonical, exact `-C` cwd to `$CODEX_HOME/config.toml` (default
`~/.codex/config.toml`). The coordinator and Herdr worker must use the same
Codex home. A sibling `config.toml.orchestrator.lock` serializes managed writers.
The TOML-aware edit preserves unrelated bytes, makes a durable mode-0600
`config.toml.orchestrator-backup-*` copy first, and atomically replaces the config.
Already trusted entries require no rewrite or backup. Auth and MCP settings stay
in the existing home; the helper does not read auth files or create an overlay.
The former `-c projects.*.trust_level` override did not suppress the live trust
dialog and is not the prevention mechanism. Jail `exec` workers keep their
existing launcher and config; this pre-trust applies to ordinary host workers.

A Codex startup trust dialog is a failed launch with an actionable config/cwd
message. No dialog input or task replay is sent. The pending start is resolved
as failed, admission is released, and `finish` can close that owned workspace
without a worker receipt. Its disposition defaults to `failed`; a coordinator
may explicitly override it to `blocked` with a reason. `completed` is refused.
The validation status is `not_started`, and no worker result or receipt is
fabricated. Resume/session dialogs remain unresolved for coordinator inspection.

Inspect stale scoped trust entries with:

```sh
python3 scripts/herdr-jobs.py prune-codex-trust
python3 scripts/herdr-jobs.py prune-codex-trust --config TEMP_CODEX_HOME/config.toml --dry-run
```

Only trust entries under `/home/deadhand/.local/share/orchestrator/` whose paths
no longer exist qualify. Existing paths, dangling symlinks, sibling roots,
parent traversals, and unrelated project keys are preserved. The default dry-run
does not write a lock, config, or backup. `--apply` opts into removal under the
same lock/backup/atomic-replacement protocol. Cooperating managed writers share
the lock; other config editors do not. A detected external edit aborts rather
than overwriting it, but an editor racing the final replace cannot be excluded.

A matching receipt and collected artifacts also prove submission after manual
continuation in the same pane, even if the checkpoint still says `moved`,
`retry_ready`, or `ready` with no pending effect. Observation records this
reconciliation before `resume` can launch again. Settlement still requires a
verified owned pane in `idle`, `done`, or `exited`; unresolved resource effects
and invalid receipts do not authorize closure. A `DONE` file alone is insufficient.

Preview validates the request, policy, and prompts offline; it does not read the host contract and cannot report a missing or unverified binding. Before the first live call, confirm the contract's `jail_export` is non-null, `verified`, and bound to the child's output root, and its `jail_default` attests the verified Codex `gpt-6-astra` xhigh default; a preview success is not evidence either binding exists. See [host integration](host-integration.md) for the exact `jail_default` snippet and placement.

Execute the returned `next_action.argv` as an argument array. A next action of `inspect` requires reading the per-job issues before continuing; it is not permission to repeat an unresolved effect. `status` performs observations and local checkpoints but never launches or prompts. `resume` may launch queued work or continue a proven-safe startup step.

Each positive call budget is at most 60 seconds, including preflight and host commands. With zero budget, the command reports local state only. A new zero-budget run validates input and returns a continuation without creating a job checkpoint or contacting the host. Preview writes nothing and makes no host calls. Ordinary calls can create the run directory and its lock before discovering a missing capability.

## Request schema

See the runnable [example manifest](../examples/manifest.json). UTF-8 JSON rejects duplicate keys, non-finite numbers, unknown fields, and wrong types.

| Record | Required fields | Optional fields |
|---|---|---|
| Manifest | `schema_version: 1`, `request_id: string`, `jobs: array` | `concurrency: integer`, `retry_override` |
| Job | `job_id`, `name`, `task_kind`, `task_file`, `cwd`, `output_expectation`, `writes_repository: boolean` | `override`, `repository_worktree`, `worker_result`, `task_class`, `model_level`, `model_fallback`, `worker_mode`, `protected_paths`, `editable_paths`, `acceptance_tests`, `required_context`, `tracker_writes`, `local_lane`, `independent_validator`, `local_lane_scope`, `review_of`, `review_kind`, `prior_head` |
| Override | `instruction: string` | `runtime: string`, `model: string`, `provider: string`, `effort: string` |
| Worker result | `task_id`, `assignment_revision` | none |

### Schedule the independent review up front

Coding work requires an independent review; the author's self-review is optional.
Add this entry alongside the coding job, or supply it as the one job in an `add`
manifest for an existing run:

```json
{"job_id": "review-code", "review_of": "code"}
```

`review_of` must name a distinct coding job with `writes_repository: true` and
`worker_result`. Review jobs are ordinary, read-only jobs. The helper supplies
their name, cwd, output expectation, template and distinct worker task identity;
normal model routing and concurrency/admission limits apply. Optional routing
overrides select the reviewer independently. Do not supply `task_file`,
`repository_worktree`, or `worker_result` on a linked review job.

The reviewer waits without allocating a pane until the author settles with a
complete, unresolved-free collection and a valid, ready worker result. The
helper verifies the receipt-pinned candidate and current branch head, then
renders the [review-worker template](review.md#copyable-review-worker-brief-full-or-delta)
with the coding task ID, author/reviewer identities and candidate base/head.
The originating author brief and required context are supplied as spec evidence.
The reviewer writes its explicit verdict in `done.json`, alongside the task,
capture ID and counts. Its launcher `result.json` uses the distinct reviewer
task ID; the sidecar and `done.json` use the coding task ID.

`review_kind` defaults to `full`. For a delta, also provide
`"review_kind": "delta", "prior_head": "FULL_PREVIOUSLY_REVIEWED_SHA"`.
The helper verifies that `prior_head` lies between the candidate base and head
and uses it as the capture base. Full bundles use `review/`; each separate delta
job uses `review-r1/` in its own output directory. Supply prior review/finding
records through `required_context` when needed. Preparation renumbers the
ordered review chain into one batch and checks coverage through the candidate.

`worker-records.py prepare-preflight` selects jobs linked to `--job-id` by default,
in managed request order. Put full and delta jobs in review order. Explicit
`--review-job` selection remains available. Preparation requires settled,
complete linked reviews and refuses a missing verdict in `done.json`; the
integration gate must still return `ready` before integration.

Request/job identifiers contain 1–64 letters, digits, underscores or hyphens and begin with a letter or digit. Job IDs are unique within the batch. `task_kind` is `ordinary` or `deep_research`; the coordinator supplies it explicitly. Managed Herdr Jobs refuse `shopping`; use the host pane-command workflow documented in [PREFERENCES.md](../PREFERENCES.md), not an unmanaged `herdr agent start` workaround. Claude Code maps to runtime `claude-code` and Herdr kind `claude`; managed launch is available only after the live host contract lists `claude` following the verification steps in [host integration](host-integration.md). Until that binding is verified, the `unavailable_capability` refusal stands. Task paths and working directories resolve relative to the manifest. A job's output expectation states what useful work must be in its result.

Ordinary jobs can select `model_level: 1` through `8`; omission uses the
policy's CSV default unless a legacy ordinary runtime or explicit override is
set. `model_fallback` requires an explicit level and exactly `reason` and
`evidence`, as defined in [model routing](model-routing.md). These fields are
refused on jail routes. Explicit tuple overrides win and cannot silently
fallback. Preview includes effective provider, effort, and the pinned route;
live preflight validates exact native model/provider/effort capabilities before
any pane or worker is created.

The `local_lane` flag is a deliberate opt-in for an ordinary host job outside
the integer ladder. It requires the complete override tuple
`runtime: pi`, `provider: llama-local`, `model: qwen3.8-27b-uncensored`, and
`effort: medium`; do not include `model_level`, fallback, or escalation fields.
It also requires `task_class` from the policy allowlist: `triage`, `summary`,
`contract_artifact`, `bounded_code_edit`, `inventory`, or
`local_file_analysis`. A missing or unsupported class is refused during
request preparation, before host preflight or launch. These classes are for
trivial, self-contained, validator-checkable work with supplied local inputs;
networked, credentialed, destructive, and review-verdict tasks are excluded.
Each request must also declare `local_lane_scope` with `network_access`,
`credential_access`, `credentials_in_environment`, and `destructive_actions`
all set to `false`; any missing or true field is refused during preparation.
Do not put credentials or secrets in task files or override instructions. The
final prompt is checked for common secret-value patterns before launch. The
worker instructions prohibit reading the process environment or secret files;
the launcher adds no credential values to the worker prompt. Before a local Pi
start, the launcher replaces the moved pane's shell with an `env -i` shell and
runs its Herdr CLI calls with the same allowlist: `HOME`, `PATH`, `TERM`,
`LANG`, Herdr pane integration variables, and Pi agent/session directory
variables when present. API keys and tokens from the coordinator environment
are not passed to the worker.
The job must set `writes_repository: true`, use its recorded throwaway linked
worktree for `cwd`, declare acceptance tests, and provide
`independent_validator: {"argv": ["...", "{output_dir}"]}`. The command is
run without a shell at `finish`. Admission caps `llama-local` at one active
lease across managed and direct launches. Preflight also requires the active
`llama-server.service` user unit and at least 2048 MiB free GPU memory. The
service is expected to pin `reasoning_effort=medium`; the route records medium
but does not send Pi's unsupported `--thinking` value. The route never uses the
jail.

Pi starts with its normal `PI_CODING_AGENT_DIR` (default `~/.pi/agent`), so its
configured MCP servers and extensions remain available. The local route does
not add `--no-mcp` or `--no-extensions`, replace the agent directory, or edit
live Pi configuration. Configured tools remain available, but their use must
stay within the declared bounded class and must not disclose credential
material. Read-only setup inspection and any future config changes belong in
the task's `live-config-proposal.md`.

For a jailed Codex job, optional `task_class` selects the per-run provider
stream idle timeout from `launch-policy.json`: `short_routine` uses 300000 ms
and `long_form_research` uses 600000 ms. A missing or unrecognized class uses
the 300000 ms default, so long-form research must be marked explicitly; the
`deep_research` task kind alone does not select 600000 ms. The helper passes
`-c model_providers.train-openai.stream_idle_timeout_ms=<value>` to that
Codex invocation. This limits an idle stream interval, not total job time, and
does not modify shared Codex configuration.

Every prepared job carries `worker_mode` (`literal` or `inferential`) and the
default protected-path list. The mode changes how ambiguity is handled in the
brief; it never changes the CSV ladder or routing. `acceptance_tests` are added
to the protected list automatically, and a declared `editable_paths` entry
that overlaps any protected path is rejected before launch. Jailed jobs must
also provide `required_context` entries with at least one test and one fixture;
the helper resolves and checks each entry before the jail launcher runs, and
the launcher repeats that check from its launch request.

Offline `run --preview` and `add --preview` include per-job
`edit_scope_warnings` when local symlinks in declared editable paths or literal
task path references resolve outside the declared editable locations. The
warning does not grant access or determine whether a reference is an edit.
Follow the [worker pre-dispatch checklist](workers.md#pre-dispatch-checklist)
to inventory every required edit and verify its destination; jail namespaces
are reported as unverified by this host-only check.

`worker_result` opts a job into the shared worker-record contract. The helper
then asks for a `result.json` bound to that task ID and assignment revision,
collects it and every artifact it names, and lets `finish` validate it before
writing `disposition.json`. Jobs without `worker_result` keep the original
receipt contract (`result.md` plus `receipt.json`) and remain valid with
existing manifests and receipts. `finish` reports that worker-result
validation was not requested for these receipt-only jobs.

For integration evidence, enable `worker_result` on coding jobs and have review
jobs collect their `review/` (or `review-rN/`) Markdown, sidecar and completed
capture as a single uncompressed `capture.tar` artifact. This avoids consuming
one receipt slot per capture file; retain the `review/` or `review-rN/` prefix
on capture paths inside the archive. The usual artifact size limits still apply.
Run `integration-preflight.py --managed-run RUN_DIR` once per source
run, with the usual `--repo`, `--target`, `--run-dir` evidence destination and
`--candidate` arguments. The managed adapter generates the roster, derives
author/reviewer identities and panes from helper state, and checks collected
snapshots directly. Follow [the managed review brief contract](review.md) for
sidecar task IDs, omitted identity fields, completion counts and dispositions;
receipt-only coding jobs do not supply the required candidate record.

Effort overrides are
validated against each runtime's supported values and mapped to that runtime's
native argument:

| Runtime | Accepted effort values | Native argument |
|---|---|---|
| `codex` | `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | `-c model_reasoning_effort=<value>` |
| `pi` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | `--thinking <value>` |
| `claude-code` | `low`, `medium`, `high`, `xhigh` | `--effort <value>` |
| `omp` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `auto` | `--thinking <value>` |

Every prompt names one canonical output directory in both its worker brief and
collection contract. Workers must write the expected result, receipt, and
supporting files there; the collector reads that same directory. Preflight
rejects task text that explicitly names a different output or editable
directory and reports the conflicting declaration and source task file.
This includes prose such as `Place the report at /tmp/review.md` and
`Save the report under ./out`, including quoted paths. Refer to the assigned
output directory in task text instead of choosing a destination.

Other runtimes, including Gemini CLI, reject an effort override before launch;
the launcher does not assume every runtime accepts a generic `--effort` flag.
Effort values are runtime-level capabilities; a selected model may support a
smaller subset. Claude Code receives the resolved exact model as
`--model <id>` when a model is selected, and its requested effort as
`--effort <value>`; the host binding must verify these arguments. Effort is
preserved as requested. Claude effort is capped at `xhigh`: `max` is refused
without downgrading, including when a saved route is preflighted for a new
start. A Pi provider override is also passed to Pi as `--provider` and
used for admission, and requires an explicit model because Pi requires
`--model` with `--provider`.

Managed Claude jobs use `herdr agent start NAME --kind claude --pane PANE --
--model EXACT_ID --effort VALUE`. The adapter checks the bound kind again before
starting or prompting. Startup must produce the owned idle Claude identity
before one prompt is sent; a visible `Working` observation or an attempt-bound
receipt proves delivery. Trust and authentication dialogs remain blocked.
For a selected Haiku model (including its native alias), the launcher first
exports `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` in that owned pane and verifies
an attempt-specific readback. An unverified environment leaves the start effect
unresolved and sends no runtime start or task prompt. The environment is scoped
to the worker pane; shared Claude configuration is not edited.

Claude leases and backoffs use the `claude` provider pool, independently of
Codex. The usual receipt/artifact collector applies to Claude: list `DONE` as a
receipt artifact when it must be collected. A `DONE` marker alone neither
proves delivery nor replaces the receipt or coordinator acceptance.

`retry_override` is an optional run-level object, pinned in `state.json` and
applied only to matching workers in that run. Pi accepts `max_retries` (0–100)
and `max_agent_delay_ms` (0–600000). The helper creates a private pi settings
and session directory under the run folder and selects them in the worker pane;
it uses Pi's documented [`PI_CODING_AGENT_DIR` and
`PI_CODING_AGENT_SESSION_DIR`](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/environment-variables.md)
variables. The shared `~/.pi/agent/settings.json` is read but never edited. A project
`.pi/settings.json` that overrides the selected retry keys stops preflight.
Codex accepts `provider_id` plus `request_max_retries` and/or
`stream_max_retries` (0–100). It requires that provider to be the already
selected, configured custom provider in `$CODEX_HOME/config.toml`; Codex's
retry fields are provider-scoped ([provider implementation](https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs)).
The built-in `openai`, `ollama`, and `lmstudio` provider IDs are reserved
([validation source](https://github.com/openai/codex/blob/main/codex-rs/config/src/config_toml.rs)),
so those providers and the jail route are refused.

```json
{
  "retry_override": {
    "pi": {"max_retries": 10, "max_agent_delay_ms": 120000}
  }
}
```

If retry tuning is needed and no supported run override exists, leave shared
settings alone. Record the proposed settings change, affected run and worker,
and exact staged command in the run's decision record; wait for authorization
before applying it or retrying work under a new shared setting.

The coordinator writes launcher-owned `telemetry.json` (schema version 1) as a
sidecar in each task output directory. This keeps the schema-v1 `state.json`
checkpoint and public engine result byte-compatible. Older checkpoints that
embedded telemetry in job records remain loadable; the next checkpoint moves
that data to the sidecar. The sidecar records the selected route (`lane`,
`level`, `choice`, `model`, `effort`, `harness`, `provider`), start/end times,
continuation count and reasons, quota signals, a `coordinator_grade` slot that
must remain `pending`, and independent-validation status. A non-pending grade in
worker-writable telemetry is treated as tampering and ignored. `finish` stores
the actual grade in coordinator-owned `coordinator-grades.json` in the run
directory. A local-lane `finish` requires a coordinator grade; an `accepted`
grade additionally requires a passing independent validator.

Limits: 1–128 jobs, an expanded request of at most 8 MiB, and each final worker prompt of at most 100,000 UTF-8 bytes. JSON input files are bounded to 1 MiB. Preview and execution both validate the complete batch and every synthesized prompt before any launch. The run checkpoint has a separate 64 MiB limit. Receipts and collected metadata are each bounded to 256 KiB per job, with 1–32 relative artifact paths, 16 MiB per artifact and 64 MiB total per job.

Every job must explicitly declare `writes_repository` as `true` or `false`.
Existing manifests without this field are rejected; omission never means
non-writer. Set it to `false` for jobs that do not write repository files. A
repository-writing job sets it to `true`, must include `repository_worktree`,
and must set `cwd` to that record's `path`. A record on a `false` job is rejected.
Produce a writer record with:

```sh
python3 scripts/lease-worktree.py acquire --repo <expected-repository> \
  --lease-holder <run-task-id> --expected-base <full-commit-id>
```

Omit `--expected-base` only when the assignment does not pin a base. The record has
`schema_version`, `path`, `lease_id`, `lease_holder`, `repo_root`,
`git_common_dir`, and `base_commit`. A managed writer preflight confirms the
current Treehouse lease ID and holder, then verifies that the canonical cwd is a
registered linked worktree of the recorded repository and that `HEAD` still
matches `base_commit`. It does this before the first pane is created. Preview
checks the explicit writer intent, record shape, and cwd pairing offline; it
does not claim that the live lease is valid. Every writer record is verified
before the first pane is created.

Treehouse allocation can reserve a slot before later verification fails. If the
allocator exits unsuccessfully after `get`, reconcile `treehouse status --json`
before another allocation attempt. The helper never retries and emits a record
only after allocation and verification succeed.

Ordinary runtime/model/effort defaults come from the configured CSV ladder, as described in [model routing](model-routing.md). A legacy policy without the ladder still requires a settled ordinary runtime or explicit request override. The override instruction leads the worker prompt as the current user instruction. A runtime override on an `ordinary` job selects that Herdr-agent runtime. On a `deep_research` job it may only restate the policy runtime; naming a different one is refused (`decision_needed`) rather than converting the job out of its isolation route. When the user explicitly names another runtime for deep-research work, re-issue it as `task_kind: ordinary`. Exact unavailable runtimes/models produce an actionable failure, without substitution. Exact models are checked against discovery and pinned. Exact jail model selection is unsupported until its separate catalog is verified; omit the model for the jail runtime default. For admission, an explicit `provider` override wins; otherwise the provider comes from the resolved model ID prefix or the runtime ID. The model key is the resolved model ID, or `default` when no model was requested.

## State and recovery

For desktop unattended fleets, the tested out-of-session driver is
[`codex-heartbeat.timer`](unattended.md#desktop-codex-heartbeat). It calls
`resume` when quota permits, labels workspaces, and feeds an approved `ORDER`
backlog up to the run's stored concurrency.
It uses the recorded coordinator pane as the worker split origin and retries
`finish` until every returned job either has a closed workspace or no pane.
`DONE` and `CANCELLED` markers count as terminal for this feeder; cancelled jobs
are skipped during pane supervision. JSON add errors stop feeding for the pass
and queue a wake. These markers and wakes do not replace receipts, acceptance, or
`check-closeout`. The canonical heartbeat uses per-job route files (defaulting
to Codex `gpt-6-luna`/low) and requires fresh quota above 10% in every reported
window before launching; check routes and authorization before enrolling a run.
It is separate from the helper's own recovery and does not install itself when
this skill is loaded.

The explicit run directory contains `state.json`, a per-run lock, worker output directories, immutable collection revisions, and (for jail work) launcher requests and exit records. Files are written through temporary files and atomic replacement. State retains request/policy digests, attempts, returned IDs, observations, and pending effects. The same request and run directory resume; altered content conflicts. Different directories are independent runs, even if the caller reuses a request ID. Provider admission is the shared exception: all launch paths use the locked host file `~/.local/state/orchestrator/provider-admission.json` and its adjacent lock.

Plan → act → observe → checkpoint: persist an effect intent, issue the command, validate the response, then persist its result. A pane move changes the ID; all later operations use the returned ID. A lost response can leave an effect ambiguous. A matching worker receipt proves prompt delivery. For a pending prompt-bearing Codex start, require a matching receipt, or verified owned identity plus visible `Working` activity or an observed `done` lifecycle. The timed Codex status can sit above its input/footer and be truncated in narrow panes. A completed turn proves delivery even when no active turn was sampled; it does not prove result collection. Idle, unknown, blocked, and unverified identities remain unresolved. Other agent kinds retain the verified startup then prompt sequence. An unidentified split or move stays unresolved. Inspect host resources under the original request's authority before deciding how to handle that case; creating a fresh run is not an automatic retry.

If Codex shows a trust or resume/session dialog, or delivery remains unproven,
the job is blocked or unresolved and the managed launcher sends no dialog input.
Inspect the owned pane with `herdr agent get <name>` and
`herdr pane read <pane-id> --source recent-unwrapped --lines 120`. Never choose
**Use session directory**. A coordinator must verify the exact assigned path
before a human resolves a trust prompt. Reconcile whether the prompt started or
produced output before retrying. An authorized retry requires the old Codex
process to have exited, the pane to be at a shell prompt, and any prior result
to be collected; create a new run directory only for that fresh attempt.

The run concurrency and shared provider cap both apply. Managed jobs reserve provider capacity before the first pane is created and report the same provider-wide active count as the admission CLI. Capacity and backoff waits appear as `admission_wait` and do not count as task failures. Startup, working, blocked, and ambiguously active workers occupy slots. A worker with proven settled activity releases its slot while collection stays incomplete. Idle without a matching receipt or previously observed visible `Working` marker does not prove that the submitted prompt landed. An owned Codex `done` observation reconciles its pending prompt-bearing start and releases capacity while a missing receipt remains a collection error. The heartbeat may feed unrelated backlog when every unresolved effect is a verified live Codex start; those workers still count toward the concurrency cap. Other unresolved effects and unknown identities block new backlog. For non-Codex agents, the helper verifies the owned worker is idle after startup before sending the prompt, detects trust and authentication dialogs, then requires a matching receipt or visible `Working` marker. It never sends input to a startup dialog. A recognized 429 records model-specific backoff; after the response is terminal, the attempt is queued in the same run and a fresh attempt starts after the window expires. Earlier output folders and repository-worktree lease records remain intact. With no Retry-After or reset metadata, the configured default is 60 seconds.

For finite jail jobs, the internal `jail-runner.py` runs the fixed launcher command and writes an attempt-bound exit record on the host after it returns. This survives disappearance of Herdr's live agent classification. It is not a general shell runner. A missing or mismatched exit record establishes no completion. Created panes/workspaces remain available for inspection.

## Worker receipt

The helper appends exact output instructions and generated identities to each worker prompt. Workers write their result and supporting artifacts, then atomically write `receipt.json` in the supplied output directory. The schema is:

```json
{
  "schema_version": 1,
  "request_id": "REQUEST_ID_FROM_PROMPT",
  "job_id": "JOB_ID_FROM_PROMPT",
  "attempt_id": "ATTEMPT_ID_FROM_PROMPT",
  "outcome": "complete",
  "artifacts": ["result.md"],
  "unresolved": []
}
```

Outcome is `complete`, `partial`, `blocked`, or `failed`; unresolved items are strings. Artifact paths are bounded relative paths and may not traverse symlinks. The collector hashes and copies the receipt and artifacts into a revision directory, preserving old revisions. The receipt's existence or valid structure does not establish factual correctness.

Deep-research artifacts keep their content contracts. Receipts and intermediate
artifacts are internal state, not publication payloads. The final-report delivery
contract remains separate. Before a jailed research report is queued for delivery,
follow the [canonical deep-research fact-check gate](../../deep-research/SKILL.md#check-and-deliver).

## Results, effects, and fallback

Every successful operation emits one JSON object naming request/run identity, batch state, cap, active count, per-job state, collection evidence, and next-action arguments. Error objects name `error` and `message`; when a validated checkpoint exists, it is included for inspection.

| Exit | Meaning |
|---|---|
| 0 | Operation completed: preview, checkpoint, active work, or collected results; inspect `batch_state` |
| 2 | Invalid input or record |
| 3 | Request/state conflict, changed binding/session, or concurrent caller |
| 4 | Missing/unverified capability or preflight budget exhausted |
| 5 | Unsettled runtime choice |
| 6 | Unresolved external effect |
| 7 | File/encoding error |
| 10 | Partial batch or work needing attention |
| 130 | Interrupted; resume from durable state |

Host calls can create workspaces/panes, start provider-backed workers, and submit task content under existing task authority. The helper writes only its run state, configured artifact locations, collected snapshots, and explicit finish dispositions. `finish` releases the job's provider lease and closes the exact workspace ID recorded from its pane-move response; Herdr 0.9.3 exposes that ID at `result.move_result.created_workspace.workspace_id` (the helper also accepts `workspace.workspace_id` or `workspace_id`). For repository writers, it releases the separate worktree lease after verifying that the candidate branch/head/base matches the lease and is cleanly merged into `main`; it keeps an unmerged lease for review unless the coordinator explicitly abandons it. It performs no installation, registration, automatic cancellation, background-adapter launch, or successor handoff.

If the helper or required binding is unavailable, return the capability gap and its recovery path. A manual continuation must be explicit, preserve policy and receipt obligations, and account for existing owned resources. Unknown effects are not evidence that nothing happened.
