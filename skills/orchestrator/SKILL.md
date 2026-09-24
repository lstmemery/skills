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

## Persistent Herdr successor replacement

Use this branch when a coordinator is replaced in a persistent Herdr run. It is
an authority and continuity procedure, not a new assignment. Read [state and
continuity](references/state.md) for ownership and handoff semantics, [runtime
operations](references/runtime.md) for live Herdr/model inspection, and the
[worker contract](references/workers.md) for result evidence.

1. **Read before acting.** Completely read the run's compact `STATE.md` and the
   existing successor-acknowledgment record. If either is missing, truncated, or
   unreadable, do not launch work or mutate todo/task state; preserve work and
   record the concrete blocker.
2. **Establish actual identity.** Inspect the live `PI_SESSION_ID`, provider,
   model, Herdr environment, workspace, and pane. Apply the active routing policy
   and model allowlist. Handoff text is evidence to reconcile, not authority for
   identity, model, pane, or permissions.
3. **Reconcile before retrying.** Compare the compact records with live Herdr,
   background/worker state, and every retained evidence path. Distinguish a
   persisted terminal record, a live worker, and incomplete evidence. Adopt a
   still-live worker through its existing exact handle and supervision mechanism;
   an unknown registry lookup or missing signal is not permission to duplicate,
   stop, alter, or relaunch it. Never infer an exact outcome without evidence;
   retry only after reconciliation and only when the recorded dependency and
   authorization permit it.
4. **Acknowledge before control.** Before launching any work or mutating todo/task
   state, atomically write a generation-specific successor acknowledgment in the
   run handoff location. Include the actual session identity (`PI_SESSION_ID`),
   Herdr environment/workspace/pane, provider/model, generation, observed worker
   state, accepted supervision handle, and exact next actions. Failure to write a
   complete acknowledgment is a blocker.
5. **Transfer and persist.** After the acknowledgment, transfer ownership exactly
   as [state and continuity](references/state.md) requires: the retiring owner
   atomically activates the acknowledged generation and successor, then the
   successor records supervision acceptance and reconciles adopted handles. If no
   retiring owner can perform an unambiguous transfer, do not self-authorize;
   preserve work and record the ownership gap as the run's terminal state in a
   `SUCCESSOR-GAP.md` beside `STATE.md`: the unacknowledged generation, the live
   workers observed but not adopted, and the exact command that would resume.
   That file, not a message, is this branch's completion evidence. Preserve existing uncommitted
   changes. Do not launch pending work or a replacement worker until its recorded
   dependency and authorization are satisfied. Keep the successor Herdr pane
   alive for future waits.
6. **Close with observable evidence.** The handoff is complete only when the
   acknowledgment path, updated owner/generation, live Herdr workspace/pane,
   accepted supervision handle, and worker/evidence disposition are observable,
   with the exact next action recorded. If a worker exits successfully but its
   exact report/result is absent, report: “exit observed; exact report absent;
   requested outcome unverified/incomplete evidence.” Keep it pending or blocked;
   do not claim success or relaunch without authorization.

If required state, evidence, live identity, policy, or Herdr capability is
unavailable, pause mutations and new launches, preserve uncommitted changes and
live workers, record the specific gap and durable resume instruction, and report
that automatic renewal is unavailable. Do not clean, reset, release, or otherwise
repurpose retained work as fallback.


## Coordinate

1. **Reconcile.** Read preferences and the compact active task records. For a new
   run, create state only when assigning the first worker. Before resuming, compare
   recorded identities with live workers and artifacts. For a persistent Herdr
   replacement, run [the successor procedure](#persistent-herdr-successor-replacement)
   before any retry or task-state mutation. Follow [state and continuity](references/state.md)
   for records, steering, supervision, and coordinator renewal. Finish this step
   with one active coordinator and an explicit next action for each active task.
2. **Assign.** Record the intended result, permitted effects, dependencies, and
   observable acceptance criteria. Delegate investigation, planning, implementation,
   substantive review, and recovery analysis. Split independent work when it has
   separate ownership; sequence dependent work. Use the
   [worker contract](references/workers.md) for the brief and return record.
   Finish with a bounded assignment and a selected runtime, not an open-ended
   instruction to manage the whole queue.
3. **Launch.** For independent ordinary-agent or omp-train jobs in an authorized
   Herdr host session, use **Managed Herdr Jobs** above. Otherwise choose
   execution through [runtime operations](references/runtime.md). Inside Herdr,
   use a new worker pane in its own workspace. Allocate an isolated worktree
   before repository changes; other work uses an ordinary task folder.
   Retain the returned identities and observe startup before marking the task
   running. Preserve the user's focus.
4. **Supervise.** Wait through the backend's event/wait surface, inspect an actual
   outcome, then checkpoint the transition. Continue authorized follow-ups while
   the user is away and queue their decisions. Delegate substantive fixes and
   analysis; inspect compact evidence records yourself. A timeout, missing signal,
   or unknown state calls for reconciliation, not an assumed success or duplicate
   launch. End this step only with a verified outcome, a recorded human decision,
   or an explicit supervision handoff/blocker.
5. **Present.** Research and other artifacts follow their task's delivery contract.
   For repository changes, follow [human code inspection](references/review.md):
   show the complete task diff and files in Herdr before the action requiring the
   user's decision. Apply the standing review boundary in preferences. Finish
   with the candidate, evidence, and exact next action visible to the user.
6. **Close or continue.** Perform only the authorized next action and verify its
   actual result. Record artifact retention, worker disposition, and any pending
   decision before cleanup. Stop only work owned by this task. Preserve unlanded
   changes and pending review artifacts. A completed PR-creation task does not
   establish that its code was merged or its worktree is disposable.

## Keep the coordinator small

Read the active queue and the records touched by the current event. Ask workers
for missing evidence through their return contract. Full transcripts, code
reviews, implementation details, and exploratory findings stay in worker context
or artifacts unless a specific coordination decision requires them.

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
