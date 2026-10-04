# Runtime operations

Read for launch, observation, stop, runtime/model discovery, or worktree lifecycle.
Use standalone installed tools. Learn the relevant command contract from live
help; examples below are starting points, not evidence of installation.

## Select the backend and harness

- Inside an authorized Herdr-managed session, load and follow skill `herdr`.
  Its generic topology default is overridden by the user's Orchestrator
  preference for a new workspace per worker. The active profile still wins:
  a jail cannot connect to or create work in a host session.
- Outside Herdr, use the Orchestrator CLI when available and permitted. Exact
  shell jobs also use its shell route when appropriate. Report when this route
  cannot provide the user's preferred visible panes.
- Preserve a named harness exactly. A missing CLI process registration can be
  repaired as below; it does not establish a missing Herdr agent kind. Use only
  a verified matching route, or report the specific gap. `omp` and `pi` remain
  distinct. Use native current-session child tools only when task policy permits
  that route and its harness identity is known.

For CLI-backed operations, start with `orchestrator --help` and
`orchestrator help --json --compact`. Inspect `orchestrator doctor --json --compact`
when availability is uncertain. Its registered `runtimeSummary.availableIds`
applies to CLI launches, not all possible Herdr kinds.

If the CLI is missing, installation via the published `@backnotprop/orchestrator-cli`
package is a setup action only where the current task/profile permits it. Preserve
existing configuration. A setup failure leaves the affected route unavailable;
avoid expanding this task into unrelated host provisioning.

## Models and quotas

Omit an explicit model when the user has no requirement. Before passing one, use
`orchestrator models <runtime> --json --compact` or the selected harness's live
equivalent. Match requested labels to returned IDs/aliases; preserve provider-native
values. Resolve latest/default through returned metadata, not version-name sorting.
Partial discovery is incomplete evidence. An unavailable exact model requires a
configured fallback or a reported blocker.

When preferences depend on usage, inspect `orchestrator limits --json --compact`
or the provider's available read-only equivalent. Unknown limits are not exhausted.
Apply only specified fallbacks; pause affected new work if policy requires it.

## Run-scoped retry tuning

Retry values belong to a worker run. In a managed-job manifest, set the optional
run-level `retry_override`; the helper pins it in `state.json` and applies it
only to matching workers. Pi uses a run-private `PI_CODING_AGENT_DIR` and
`PI_CODING_AGENT_SESSION_DIR` selected in the owned pane. Codex receives native
`-c model_providers.<id>.*` arguments only when the selected provider is already
a configured custom provider. The built-in Codex providers and jail route have
no supported per-run retry override; stop with a capability gap instead of
editing a shared Codex or pi settings file.

If a worker needs tuning and the requested harness has no supported per-run
override, record a staged proposal and wait for authorization before applying a
shared setting or restarting work. For the known pi retry change, the staged
restore helper is dry-run by default:

```sh
python3 scripts/restore-pi-retry-settings.py
python3 scripts/restore-pi-retry-settings.py --apply
python3 scripts/restore-pi-retry-settings.py --rollback
```

The helper reads only `retry.maxRetries` and `retry.maxAgentDelayMs` from the
live file and the `settings.json.bak-orch-retry-20260930` baseline. It prints a
diff of those two keys, preserves all other settings, and keeps a private
pre-apply snapshot for rollback. `--apply` and `--rollback` are separate
authorized live actions; tests use temporary files.

## Prepare the location

For repository changes, prefer Treehouse. Inspect installed `treehouse get --help`,
`treehouse status --help`, and `treehouse return --help` before relying on flags.
Use the checked allocator for repository writers; do not pass raw Treehouse output
straight to Git or a launcher:

```sh
python3 scripts/lease-worktree.py acquire --repo <expected-repository> \
  --lease-holder <run-task-id> --expected-base <full-commit-id>
```

The helper calls `treehouse get --lease --lease-holder <run-task-id> --json`,
rejects failed, empty, or unrecognized results before any Git command, confirms
the active lease through `treehouse status --json`, then checks the canonical
path, linked-worktree registration, repository identity, and base commit without
changing branches. Include its complete JSON output as the job's
`repository_worktree` record, set `writes_repository: true`, and set `cwd` to
that record's `path`. Every other job must explicitly set
`writes_repository: false`; manifests that omit writer intent are rejected.
Supply `--expected-base` whenever the assignment pins a base; otherwise the
helper records the worktree's current `HEAD`.

The managed launcher repeats the lease, repository, linked-worktree, and base
checks before creating a pane for a repository writer. If allocation reports an
error after Treehouse may have reserved a slot, inspect `treehouse status --json`
and reconcile that lease before retrying; do not launch from an unverified path.
Lease ownership persists through worker exit and human review.

For research or other work with no repository writes, create an ordinary output
folder and supply any read-only source paths. A Git repository is unnecessary.
If scope expands to repository writes, isolate them before proceeding.

If Treehouse is unavailable, ordinary Git worktrees can meet the isolation
contract when permitted; record their ownership and cleanup responsibility.
They cannot be used as a managed repository-writer location until an equivalent
lease record and verification adapter are available.
Preserve work rather than silently share the primary checkout. A read-only
reviewer may inspect a pinned candidate without another worktree, with output
elsewhere and any checkout-changing commands separately isolated.

## Provider admission

Before a direct or ad-hoc launch creates a pane, reserve a slot through the
shared admission CLI shipped with this skill. Managed jobs use the same host
state and configured provider caps:

```sh
python3 /path/to/orchestrator/scripts/herdr-admission.py acquire \
  --provider zai --model glm-4.5 --lease-id TASK_ATTEMPT --run-id TASK_ID
python3 /path/to/orchestrator/scripts/herdr-admission.py status --provider zai
python3 /path/to/orchestrator/scripts/herdr-admission.py release --lease-id TASK_ATTEMPT
```

Only create the pane and start the worker when `acquire` returns
`"admitted":true`. A denied acquisition reports whether the provider cap or a
provider/model backoff blocked it. Keep the lease through the worker's active
lifetime; release it after the owned worker is terminal. `status` reports the
provider-wide active count used by both direct and managed launches.

When a direct worker reports HTTP 429 or a rate-limit response, record its
Retry-After or reset metadata before another start, then release the completed
attempt's admission lease:

```sh
python3 /path/to/orchestrator/scripts/herdr-admission.py rate-limit \
  --provider zai --model glm-4.5 --retry-after 30
```

Use `--reset-at` when the provider supplies reset metadata. If neither is
available, the policy's bounded 60-second default applies. A new acquisition
for that provider/model remains denied until the recorded time expires. The
CLI reads provider caps from the adjacent `launch-policy.json`; set an explicit
`provider` in a managed job override when its model ID does not identify the
provider.

## Herdr launch

Use current help to inspect supported kinds. For a normal recognized harness:

```sh
herdr pane split --current --direction right --cwd <worker-cwd> --no-focus
herdr pane move <returned-pane-id> --new-workspace --label <task-name> --no-focus
herdr agent start <unique-name> --kind <verified-kind> --pane <moved-pane-id>
herdr agent prompt <unique-name> <brief> --wait --timeout 30000
```

Choose split geometry per the Herdr skill. Parse `.result.pane.pane_id`, then
the moved `.result.move_result.pane.pane_id`; movement changes the public ID.
Record workspace/pane/name before prompting. Pass native arguments only through
the installed CLI's supported argument boundary. Do not turn a brief into shell
code: use structured argv where possible and shell-quote any necessary command.

For a managed Codex job, start a fresh Codex session with the complete managed
prompt as the final native argument. Scope project trust to the assigned working
directory and pass a resolved model only when one was requested:

```python
import json

argv = [
    "herdr", "agent", "start", name, "--kind", "codex", "--pane", pane,
    "--timeout", "5000", "--", "-C", cwd,
    "-c", f'projects.{json.dumps(cwd)}.trust_level="trusted"',
]
if resolved_model is not None:
    argv += ["-m", resolved_model]
argv.append(prompt)
```

Construct this as an argv array; the prompt is one argument. Do not follow this launch with
`herdr agent prompt`, `pane send-text`, or text keystrokes. Herdr may time out
while waiting for an argv-started Codex session to become idle. Treat that
timeout as an ambiguous start until `herdr agent get` confirms the owned
pane/name/kind and `herdr pane read <pane-id> --source visible --lines 200`
shows `Working`.
Only then mark the prompt submitted and begin normal observation. A blocked
state, a trust/resume dialog, or output that does not prove `Working` is a
launch failure or unresolved effect; do not answer the dialog or replay the
prompt. Other harnesses continue to use the verified `agent start` then
`agent prompt` sequence above.

Some non-repository run folders may still trigger Codex's trust screen despite
the scoped trust override. In that case the managed launch stops for inspection;
it does not auto-accept the folder.

Recovery for a blocked Codex launch:

1. Inspect the owned pane and path with `herdr agent get <name>` and
   `herdr pane read <pane-id> --source recent-unwrapped --lines 120`.
2. Never choose **Use session directory**. If Codex shows a resume/session
   picker, leave the existing session untouched. If it shows a trust dialog,
   have the coordinator verify the exact assigned path before a human resolves
   it interactively; the launcher does not press Enter or choose a trust option.
3. Reconcile whether the managed prompt started or produced output. Do not
   launch a duplicate while that attempt may still be working. Before an
   authorized retry, confirm the old Codex process is gone, the pane is at a
   shell prompt, and no result from the prior attempt needs collection. A
   pending/blocked managed run is never replayed automatically; retry from a
   fresh Codex session and a new run directory.

The runtime id `claude-code` typically maps to Herdr kind `claude`; verify the
installed mapping for every selected harness. Check startup readiness before
submission. A prompt timeout or stalled response can occur after delivery;
read agent state/output before any resend.

For a host-launched jailed job, use the pane command route for the exact
`omp-train --harness codex exec ...` process. Starting a bare Codex agent would
lose the selected jail boundary. Observe that command's actual foreground
process, exit/result, and supported lifecycle; do not assume a native agent API
owns a wrapper-launched process. This branch is unavailable from a jail lacking
the host launcher/session.

Jailed `claude -p` workers (`omp-train --claude -p "…"`):

- Put this in every brief: run sub-agents and shells in the foreground only
  (parallel Agent calls in one message are fine), and write the report and
  result JSON before the final message. Headless Claude ends when its turn
  ends; before the jail policy set `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` it
  also killed background tasks at 600 s. That lost two deep-research runs on
  2026-09-30.
- The first session starts the container; later concurrent sessions use
  `--join`. Launch a join only after the primary pane prints `detached jail
  started` and its `claude -p Task: …` process exists. A too-early join
  exits with "no jail container to join".
- Supervise by process and output, not by Herdr state: `-p` panes show
  `idle` throughout. Wait on the worker process itself, e.g.
  `while pgrep -f '^claude -p Task: <task-id>' >/dev/null; do sleep 60; done`.
  Anchor the pattern (`^claude -p`) so it cannot match the watcher's own
  shell command line; an unanchored `pgrep -f "<brief text>"` inside a
  `bash -c`/`zsh -c` watcher matches itself and never exits. After exit,
  treat a missing report/result as a failure and read the pane output.

Label the coordinator `Orchestrator` where the backend supports naming, using
a unique legal agent name. Preserve existing names owned by others. Names are
presentation; actual IDs and recorded ownership determine control.

## Durable overnight watch

When a coordinator session may end before workers finish, arm one finite
systemd user timer for the run directory. It reconciles the listed task
directories every 30 seconds, records each newly observed `result.json` once
in `flags/events.jsonl`, and stops itself after every listed task has a
`disposition.json`. It survives the shell that armed it; it still requires the
user's systemd manager to remain running. If `NTFY_URL` is set, it publishes
through the shared `claude-settings/ntfy/publisher.sh` route. Notifications are
best-effort; the event file is the durable record.

```sh
RUN_DIR=/absolute/path/to/run
python3 -B skills/orchestrator/scripts/durable_watch.py arm "$RUN_DIR" task-1 task-2 --interval-seconds 30
python3 -B skills/orchestrator/scripts/durable_watch.py status "$RUN_DIR"
systemctl --user list-units 'orch-watch*'
python3 -B skills/orchestrator/scripts/durable_watch.py disarm "$RUN_DIR"
```

Pass task directory names relative to the run directory. Repeating `arm` with
the same configuration is safe and reuses the existing timer. `status` reports
the exact timer and each task's result/disposition presence. `disarm` stops the
timer and its active reconcile service; normal completion disarms the timer
automatically. `arm` accepts `--ntfy-url` or the `ORCH_WATCH_NTFY_URL` /
`NTFY_URL` environment variable when notification routing differs from the
shared publisher's configuration.

## CLI launch and observation

```sh
orchestrator launch <runtime> --name <task-name> --json --compact --brief <brief>
orchestrator read <task-id> --wait --json --compact
orchestrator ps --json --compact --active --brief
```

Use `shell` for exact commands, an AI harness for judgment. For independent
fan-out, the current CLI's manifest contract can launch several named tasks;
each retains its own ID. Do not collapse multiple task IDs into one argv value.
Use persistent sessions only when repeated steering requires them; for example,
inspect the installed `codex-app-server --session` and `send` contract. Start a
native goal only when the user requested one; set a hard budget only if requested.

Prefer returned `commands.*.args` and `stop.args` as argv arrays. Use events/watch
for normalized transitions, read for results, and raw logs for bounded diagnosis.
Use the execution harness's yield capability around long waits. Resume a finished
task only when the selected backend records support and provider metadata.

## Observe, stop, and clean up

In Herdr, use exact-identity `agent get`, `agent read --source recent-unwrapped`,
and `agent wait`. Its state is a lifecycle observation; inspect task artifacts
for completion. If terminal output is incomplete, use the worker's durable result
instead of repeatedly requesting longer scrollback.

Stop stale or cancelled work only by its recorded owned identity: a Herdr worker
pane/process or the CLI's exact task stop command. Confirm disposition before
reusing a location. All-workspace interruption requires an explicit user request;
the skill's ordinary cleanup never sweeps unrelated work.

Release a worktree only after preserving its outputs, settling review/feedback,
and proving its code is safely retained or integrated under the task's authority.
Dirty or unlanded changes remain held. Treehouse return can terminate lingering
processes and reset files; condition it on the recorded lease identity when the
installed version supports that guard. Never blindly repeat a destructive return
or use force flags to bypass missing evidence. Pane closure alone is not worktree
release. Inspect an ambiguous outcome before any retry.

## Missing CLI runtime registration

Inspect current CLI config discovery and the named harness's own help. A custom
process adapter uses `agents.<id>` with `adapter: "process"`, `command`, prompt
transport (`argv-last`, `argv-first`, or `stdin`), output contract, and any verified
model flag/timeout. Preserve unrelated settings and built-in identity rules.
Keep credentials in the harness's existing supported mechanism, not the brief.
After registration, doctor must show the requested id available before launch.
If registration is outside the active profile or fails, record the missing id.

## Tool cards

| Tool class | Inputs → observations | Effects, validation, and recovery |
|---|---|---|
| Discovery | Relevant help/runtime/model/limit query → current schema and capabilities. | Read-only; use bounded command timeout, retry one transient failure, retain unknowns. |
| Worktree allocation | Repository/base/task holder → path, actual base, lease ID. | Creates/fetches/reserves filesystem state. Verify identity; reconcile allocations after ambiguous failure before retrying. |
| Worker launch/prompt | Exact harness/model, cwd, name, brief/argv → identity and observed startup/submission. | Starts execution within the brief's authority. Observe startup within the backend timeout; inspect before retrying a possibly delivered action. |
| Wait/read | Owned identity and timeout/cursor → event/state/result or timeout. | Observation only. Use waits/yields; reconcile unknown or replacement identities. Keep foreground blocking intervals bounded. |
| Stop/release | Owned identity and recorded lease, explicit reason → observed stop/retention outcome. | Terminates processes and may reset a worktree. Validate scope and retained code first; never assume idempotency after identity changes. |

Live help/JSON schema owns exact machine fields. Record the tool/version used
with validation evidence when installing or testing a route. The skill owns
selection, authority, and observation requirements rather than a cached manual.
