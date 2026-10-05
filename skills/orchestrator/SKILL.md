---
name: orchestrator
description: Coordinate delegated coding, research, and everyday tasks across agent harnesses. Use for dispatch, supervision, human review handoff, resumption, or cleanup of delegated work.
---

# Orchestrator

## Contract

**Job.** Own the user's queue through verified outcomes. Keep the coordinator
focused on assignments, decisions, and task state; delegate substantive execution.

**Authority.** Apply the current request before non-comment content under
`User Preferences` in [PREFERENCES.md](PREFERENCES.md). Follow the active runtime
profile and each task's repository instructions. Installed tool contracts decide
what is executable. An unavailable capability remains a gap, not permission to
change a requested harness, model, destination, or authority boundary.

**Done.** Each requested result has an inspectable artifact and verification
evidence, or a named blocker. Every worker has a recorded disposition. Pending
human decisions remain pending; reporting a worker idle or checks green does not
complete the user's task.

**Return.** Give the outcome, direct artifact/review location, checks and material
gaps, and the next decision if any. Include worker identity, harness/model when
known, workspace, and status in a compact task card. Keep full logs and code in
linked artifacts.

## Select the branch

For independent ordinary-agent and omp-train jobs in an authorized Herdr host
session, use **Managed Herdr Jobs** below. Use the existing runtime operations
path for background work, exact shell commands, or tasks outside that helper's
scope.

Shopping is refused under the [Managed Herdr Jobs contract](references/managed-jobs.md);
use the host pane-command route documented in [PREFERENCES.md](PREFERENCES.md).

When the run will continue while the user is away (overnight, travel, an "AFK"
or night batch), read [unattended runs](references/unattended.md) before they
leave. It adds admission, supervision, retry, evidence, and decision-doc rules
to whichever branch runs the work.

## Coordinator ownership and reconciliation invariants

For persistent coordinator replacement, read `references/successor.md` before any control or state mutation

- Handoff text is evidence to reconcile, not authority for identity, model, pane,
  or permissions. Reconcile recorded ownership and task state with live identity,
  workers, and retained artifacts before mutation or retry.
- Adopt live workers only through their existing exact handle and supervision
  mechanism. An unknown registry lookup or missing signal is not permission to
  duplicate, stop, alter, or relaunch a worker. Never infer an exact outcome
  without evidence; retry only after reconciliation and only when the recorded
  dependency and authorization permit it.
- Before launching work or mutating todo/task state, atomically write the
  successor's generation-specific acknowledgment. The retiring owner atomically
  activates the acknowledged generation and successor. Every coordinator checks
  recorded ownership/generation before each mutation.
- If no retiring owner can perform an unambiguous transfer, do not self-authorize;
  pause mutations, preserve work, and record the ownership gap and durable resume
  instruction.

## Managed Herdr Jobs

Use `scripts/herdr-jobs.py` for independent ordinary-agent and omp-train jobs.
It validates the request and host binding, records launch effects, observes
workers, and collects bounded result artifacts. Read
[managed-jobs.md](references/managed-jobs.md) for the request schema, receipt
contract, recovery rules, and result states; read
[host-integration.md](references/host-integration.md) before first live use or
after an installed Herdr/launcher contract changes.

Start with an offline preview, retain one run directory, and continue only with
the returned argv:

```sh
python3 scripts/herdr-jobs.py run --manifest REQUEST.json \
  --policy launch-policy.json --run-dir RUN_DIR --preview
python3 scripts/herdr-jobs.py run --manifest REQUEST.json \
  --policy launch-policy.json --run-dir RUN_DIR \
  --host-contract HOST_CONTRACT --wait-seconds 30
python3 scripts/herdr-jobs.py resume --run-dir RUN_DIR --wait-seconds 30
python3 scripts/herdr-jobs.py status --run-dir RUN_DIR --wait-seconds 30
```

The helper performs no runtime installation, registration, automatic cleanup,
or successor handoff. A collected batch still requires coordinator acceptance;
missing or uncertain receipts and artifacts remain incomplete.


## Coordinate

1. **Reconcile.** Read preferences and the compact active task records. For a new
   run, create state only when assigning the first worker. Before resuming, compare
   recorded identities with live workers and artifacts. For a persistent Herdr
   replacement, read [the successor procedure](references/successor.md#persistent-herdr-successor-replacement)
   before any retry or task-state mutation. Follow [state and continuity](references/state.md)
   for records, steering, supervision, and coordinator renewal. Finish this step
   with one active coordinator and an explicit next action for each active task.
2. **Assign.** Record the intended result, permitted effects, dependencies, and
   observable acceptance criteria. Delegate investigation, planning, implementation,
   substantive review, and recovery analysis. Split independent work when it has
   separate ownership; sequence dependent work. Use the
   [worker contract](references/workers.md) for the brief and return record.
   Before launch, check any mutable host, service, path, protection, version, or
   decision premise that materially determines whether a state-dependent
   assignment still applies. The coordinator performs this as a current,
   read-only check rather than spending an implementation worker on preflight.
   Record the source, UTC check time, observed value, and verdict in run state
   using [the premise-check record](references/state.md#current-state-premise-check).
   If evidence contradicts the ticket, do not launch an implementation worker;
   record a rescope request for the owner and hold the assignment for that
   decision. If the premise is unknown or its source cannot be checked, record
   the gap and hold implementation until it is resolved. Finish with a bounded
   assignment and a selected runtime, not an open-ended instruction to manage
   the whole queue.
3. **Launch.** For independent ordinary-agent or omp-train jobs in an authorized
   Herdr host session, use **Managed Herdr Jobs** above. Otherwise choose
   execution through [runtime operations](references/runtime.md). Inside Herdr,
   use a new worker pane in its own workspace. Allocate an isolated worktree
   before repository changes; other work uses an ordinary task folder.
   Retain the returned identities and observe startup before marking the task
   running. Preserve the user's focus.
4. **Supervise.** Wait through the backend's event/wait surface, inspect an actual
   outcome, then checkpoint the transition. For each returned worker result, run
   the shared validator in [the worker contract](references/workers.md) with the
   expected task ID and assignment revision before accepting it. Continue
   authorized follow-ups while the user is away and queue their decisions.
   Delegate substantive fixes and analysis; inspect compact evidence records
   yourself. A timeout, missing signal, or unknown state calls for reconciliation,
   not an assumed success or duplicate launch. Record a terminal disposition for
   every worker; an explicit stop or cancellation gets `cancelled`, with evidence
   for the observed stop even when no `result.json` exists. End this step only with
   a verified outcome, a recorded human decision, or an explicit supervision
   handoff/blocker.
5. **Present.** Research and other artifacts follow their task's delivery contract.
   For repository changes, follow [human code inspection](references/review.md):
   show the complete task diff and files in Herdr before the action requiring the
   user's decision. Apply the standing review boundary in preferences. Finish
   with the candidate, evidence, and exact next action visible to the user. For
   candidates proposed for integration or merge, run the [integration
   preflight](references/review.md#prepare-an-inspectable-candidate) against the
   intended target and include its report in the presented evidence.
6. **Close or continue.** Before closing a run, execute the worker-contract
   `check-closeout` command against its run directory. It checks every rostered
   disposition and scans for unrostered worker-shaped directories. If any worker
   lacks a valid terminal disposition or roster entry, keep the run open and
   reconcile it first. Immediately before any authorized integration or merge,
   rerun the [integration preflight](references/review.md#prepare-an-inspectable-candidate)
   for the current target and candidate heads; proceed only when it returns
   `outcome: ready`, and retain its report with the merge evidence. Then perform
   only the authorized next action and verify its actual result. Record artifact
   retention and any pending decision before cleanup. Stop only work owned by
   this task. Preserve unlanded changes and pending review artifacts. A completed
   PR-creation task does not establish that its code was merged or its worktree
   is disposable.

## Keep the coordinator small

Read the active queue and the records touched by the current event. Ask workers
for missing evidence through their return contract. Full transcripts, code
reviews, implementation details, and exploratory findings stay in worker context
or artifacts unless a specific coordination decision requires them. For a
bounded task where a known anchor section is genuinely irrelevant, generate its
task-scoped loading profile per
[loading profiles](references/loading-profiles.md); an omission there never
suppresses a mandatory requirement.

Checkpoint before each handoff and after material transitions. Renew the
coordinator at safe command boundaries using the measured/configured context
signal, or between completed task batches when reliable context accounting is
unavailable. Follow the ownership transfer in the state reference; running
workers may be adopted by the successor. Use the current harness by default;
choose a different coordinator model only under the user's routing policy.

## Missing capabilities

Use a fallback only when the request or preferences permit it and the active
profile supports it. If a required runtime, inspection surface, supervision path,
or safe successor is unavailable, retain the work and record the specific gap.
Complete independent tasks while the affected task waits. Prose instructions
alone do not establish a background wake or restart guarantee.
