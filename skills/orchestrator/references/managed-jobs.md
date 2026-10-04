# Managed Herdr jobs

## Contract

Use `scripts/herdr-jobs.py` for independent ordinary-agent and omp-train Codex jobs in an authorized Herdr host session. It validates launch policy, starts up to the configured cap, journals effects, and collects artifacts. The coordinator supplies intent and judges the results. Current explicit instructions take precedence through an `override` record; its instruction must reflect real authorization.

Success has separate layers: a helper operation returned; a worker settled; its receipt and artifacts were collected; and the coordinator accepted its work. A `collected` batch still has `acceptance: pending`. Missing files, malformed receipts, and uncertain effects remain visible. Managed Codex jobs pass the complete prompt as argv to a fresh `agent start`, then verify the owned identity and visible `Working` state before marking submission and observing it. A Herdr startup timeout is expected for this prompt-bearing launch, but is not delivery evidence by itself. The helper never answers approval dialogs or blindly repeats a timed-out prompt.

The structured [launch policy](../launch-policy.json) owns executable routing/defaults. Existing CLI behavior is bound by a verified host-contract file. Read [host integration](host-integration.md) before first live use or when the installed CLI/server changes. Live control is unavailable in a jail. Offline preview and tests work there.

## Call

Paths to the entrypoint and policy are relative to this skill; use absolute paths in calls made from elsewhere. Keep the same run directory for the lifetime of a request.

```sh
python3 scripts/herdr-jobs.py run --manifest request.json --policy launch-policy.json --run-dir RUN_DIR --preview
python3 scripts/herdr-jobs.py run --manifest request.json --policy launch-policy.json --run-dir RUN_DIR --host-contract HOST_CONTRACT --wait-seconds 30
python3 scripts/herdr-jobs.py resume --run-dir RUN_DIR --wait-seconds 30
python3 scripts/herdr-jobs.py status --run-dir RUN_DIR --wait-seconds 30
```

Preview validates the request, policy, and prompts offline; it does not read the host contract and cannot report a missing or unverified binding. Before the first live call, confirm the contract's `jail_export` is non-null, `verified`, and bound to the child's output root; a preview success is not evidence the binding exists.

Execute the returned `next_action.argv` as an argument array. A next action of `inspect` requires reading the per-job issues before continuing; it is not permission to repeat an unresolved effect. `status` performs observations and local checkpoints but never launches or prompts. `resume` may launch queued work or continue a proven-safe startup step.

Each positive call budget is at most 60 seconds, including preflight and host commands. With zero budget, the command reports local state only. A new zero-budget run validates input and returns a continuation without creating a job checkpoint or contacting the host. Preview writes nothing and makes no host calls. Ordinary calls can create the run directory and its lock before discovering a missing capability.

## Request schema

See the runnable [example manifest](../examples/manifest.json). UTF-8 JSON rejects duplicate keys, non-finite numbers, unknown fields, and wrong types.

| Record | Required fields | Optional fields |
|---|---|---|
| Manifest | `schema_version: 1`, `request_id: string`, `jobs: array` | `concurrency: integer`, `retry_override` |
| Job | `job_id`, `name`, `task_kind`, `task_file`, `cwd`, `output_expectation`, `writes_repository: boolean` | `override`, `repository_worktree` |
| Override | `instruction: string` | `runtime: string`, `model: string`, `provider: string` |

Request/job identifiers contain 1–64 letters, digits, underscores or hyphens and begin with a letter or digit. Job IDs are unique within the batch. `task_kind` is `ordinary` or `deep_research`; the coordinator supplies it explicitly. Managed Herdr Jobs refuse `shopping`; use the host pane-command route `omp-train --claude` documented in [PREFERENCES.md](../PREFERENCES.md). Task paths and working directories resolve relative to the manifest. A job's output expectation states what useful work must be in its result.

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

Ordinary runtime has no invented default: either set a settled default in policy or supply an explicit current-request override. The override instruction leads the worker prompt as the current user instruction. A runtime override on an `ordinary` job selects that Herdr-agent runtime. On a `deep_research` job it may only restate the policy runtime; naming a different one is refused (`decision_needed`) rather than converting the job out of its isolation route. When the user explicitly names another runtime for deep-research work, re-issue it as `task_kind: ordinary`. Exact unavailable runtimes/models produce an actionable failure, without substitution. Exact models are checked against discovery and pinned. Exact jail model selection is unsupported until its separate catalog is verified; omit the model for the jail runtime default. For admission, an explicit `provider` override wins; otherwise the provider comes from the resolved model ID prefix or the runtime ID. The model key is the resolved model ID, or `default` when no model was requested.

## State and recovery

The explicit run directory contains `state.json`, a per-run lock, worker output directories, immutable collection revisions, and (for jail work) launcher requests and exit records. Files are written through temporary files and atomic replacement. State retains request/policy digests, attempts, returned IDs, observations, and pending effects. The same request and run directory resume; altered content conflicts. Different directories are independent runs, even if the caller reuses a request ID. Provider admission is the shared exception: all launch paths use the locked host file `~/.local/state/orchestrator/provider-admission.json` and its adjacent lock.

Plan → act → observe → checkpoint: persist an effect intent, issue the command, validate the response, then persist its result. A pane move changes the ID; all later operations use the returned ID. A lost response can leave an effect ambiguous. A matching worker receipt proves prompt delivery. For Codex, identity alone cannot reconcile a prompt-bearing start: require a matching receipt or a visible `Working` state from the owned pane. Other agent kinds retain the verified startup then prompt sequence. An unidentified split or move stays unresolved. Inspect host resources under the original request's authority before deciding how to handle that case; creating a fresh run is not an automatic retry.

If Codex shows a trust or resume/session dialog, or the pane cannot prove `Working`,
the job is blocked or unresolved and the managed launcher sends no dialog input.
Inspect the owned pane with `herdr agent get <name>` and
`herdr pane read <pane-id> --source recent-unwrapped --lines 120`. Never choose
**Use session directory**. A coordinator must verify the exact assigned path
before a human resolves a trust prompt. Reconcile whether the prompt started or
produced output before retrying. An authorized retry requires the old Codex
process to have exited, the pane to be at a shell prompt, and any prior result
to be collected; create a new run directory only for that fresh attempt.

The run concurrency and shared provider cap both apply. Managed jobs reserve provider capacity before the first pane is created and report the same provider-wide active count as the admission CLI. Startup, working, blocked, and ambiguously active workers occupy slots. A worker with proven settled activity releases its slot while collection stays incomplete. Idle/done without observed activity or a valid receipt does not prove that the submitted job ran. A recognized 429 records model-specific backoff; after the response is terminal, the attempt is queued in the same run and a fresh attempt starts after the window expires. Earlier output folders and repository-worktree lease records remain intact. With no Retry-After or reset metadata, the configured default is 60 seconds.

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

Deep-research artifacts keep their content contracts. Receipts and intermediate artifacts are internal state, not publication payloads. The final-report delivery contract remains separate.

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

Host calls can create workspaces/panes, start provider-backed workers, and submit task content under existing task authority. The helper writes only its run state, configured artifact locations, and collected snapshots. It performs no installation, registration, automatic cancellation, background-adapter launch, or successor handoff.

If the helper or required binding is unavailable, return the capability gap and its recovery path. A manual continuation must be explicit, preserve policy and receipt obligations, and account for existing owned resources. Unknown effects are not evidence that nothing happened.
