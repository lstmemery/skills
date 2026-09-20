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

## Prepare the location

For repository changes, prefer Treehouse. Inspect installed `treehouse get --help`,
`treehouse status --help`, and `treehouse return --help` before relying on flags.
Its documented automation route is a durable lease:

```sh
treehouse get --lease --lease-holder <run-task-id> --json
```

Select the task's intended base explicitly when it differs from the default.
Retain returned path, lease identity, and actual Git base commit. Verify the
path is an isolated linked worktree of the intended repository before launching
a writer. Lease ownership persists through worker exit and human review.

For research or other work with no repository writes, create an ordinary output
folder and supply any read-only source paths. A Git repository is unnecessary.
If scope expands to repository writes, isolate them before proceeding.

If Treehouse is unavailable, ordinary Git worktrees can meet the isolation
contract when permitted; record their ownership and cleanup responsibility.
Preserve work rather than silently share the primary checkout. A read-only
reviewer may inspect a pinned candidate without another worktree, with output
elsewhere and any checkout-changing commands separately isolated.

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

Label the coordinator `Orchestrator` where the backend supports naming, using
a unique legal agent name. Preserve existing names owned by others. Names are
presentation; actual IDs and recorded ownership determine control.

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
