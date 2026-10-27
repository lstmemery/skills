# Provider usage-limit recovery

Use this procedure only after observing a provider's explicit usage-limit or
quota banner/status, or a structured provider 429. A generic timeout, slow
response, echoed prompt, or report discussing quotas is not evidence. The
read-only classifier `rate_limit_from_output` recognizes supported Codex and
Claude terminal banners and structured Pi provider errors; preserve the exact
redacted status excerpt and runtime as the evidence.

## Worker recovery

1. Stop treating the worker as active work. Record its exact task/attempt ID,
   runtime/model/provider, observed time, redacted banner/status excerpt, and
   provider-supplied reset timestamp in the run's task record. Do not copy
   credential-bearing output. If the provider gives a reset timestamp, persist
   it through `herdr-admission.py rate-limit --reset-at`; use
   `--retry-after` for a relative delay. If a concrete limit has no reset
   metadata, use the existing bounded 60-second default. Keep the attempt and
   output directory; do not create a duplicate attempt merely because its
   pane is idle. For a direct/unmanaged worker, write a durable continuation
   record in its canonical task directory when recording the backoff:

   ```sh
   python3 /path/to/orchestrator/scripts/herdr-admission.py rate-limit \
     --provider codex --model gpt-6-luna --reset-at '2026-10-11T18:00:00Z' \
     --continuation-record "$RUN_DIR/tasks/$TASK_ID/quota-continuation.json" \
     --task-id "$TASK_ID" --pane-id "$PANE_ID" --attempt-id "$ATTEMPT_ID"
   ```

   The record contains `task`, `pane`, `reset_at` (Unix seconds), and `attempt`.
   The CLI writes it atomically and refuses a conflicting existing record.
   Managed jobs persist their reset in the managed run state and do not need
   this direct-worker record.
2. Reconcile the exact worker handle and result/artifacts before acting. For a
   direct worker, when the recorded reset has passed, allow **one same-worker
   continuation** on that exact handle, with the original brief, assignment
   revision, worktree, and acceptance criteria. Record continuation count 1
   and reset evidence in `STATE.md`. Keep the existing `durable_watch.py` armed
   for `tasks/$TASK_ID`; it reads `quota-continuation.json`, records one
   `worker-continuation-due` event once `reset_at` passes, and sends the existing
   optional notification. At that watcher wake, verify the event's task, pane,
   attempt, current worker identity, and remaining continuation budget before
   queueing the original brief on the same pane. Record count 1 in `STATE.md`
   before sending, then archive or remove the pending record. The watcher only
   reports the due record; it does not restart or prompt the worker. Without
   the existing watcher/notification, the coordinator must poll the durable
   record itself. If the handle cannot accept a continuation, do not silently
   relaunch. For a managed Codex job, use the journaled `continue` operation
   below when the owned pane is still present. Managed recovery by a fresh
   attempt uses a new output directory after the recorded reset, with the same
   pinned route and assignment, subject to the mutation-reconciliation fence
   below. Both paths consume the same single recovery budget.
3. If the reset is unknown, unavailable, or the primary remains exhausted,
   when a reset cannot be awaited, the only quota fallback is the exact same-level configured model in
   `model-ladder.csv`, with concrete limit evidence and the normal explicit
   fallback fields. Treat that as one new attempt with a fresh directory,
   provider admission, and the same assignment revision. Explicit user-pinned
   routes do not fall back without new user authorization. Never escalate a
   reasoning level to solve quota exhaustion. Do not do both the continuation
   and fallback for the same observed stall; once either path is used, further
   recovery needs coordinator disposition or user direction.

## Managed same-pane Codex continuation

Reconcile existing artifacts and production effects first, then call the helper
on the existing managed run and job:

```sh
python3 /path/to/orchestrator/scripts/herdr-jobs.py continue \
  --run-dir "$RUN_DIR" --job "$JOB_ID" --wait-seconds 30
```

The command uses the saved host contract (or an explicit `--host-contract`),
checks its server/session and pinned route, and verifies the job's owned pane,
agent kind and agent name where the host contract exposes it. It accepts only
an idle Codex agent with the s1 classifier's explicit usage-limit banner and a
parseable reset. It refuses receipts (including newly written receipts), closed
or settled jobs, other runtimes, active workers, trust dialogs, incompatible
pending effects and a consumed recovery budget. An interrupted initial Codex
`start` or `prompt` can be reconciled by the owned quota banner; split, move and
workspace-close ambiguity must be resolved separately.

A future reset is journaled in `state.json` history as
`continue_quota_observed`; exit 5 means no input was sent. Invoke `continue`
again after that reset. The saved evidence is bound to the attempt, pane and
visible-output digest. For clock-only and relative banners, call once while
waiting so the reset is anchored to the original observation, including across
midnight. With no earlier observation, the s1 detector conservatively chooses
the next local clock occurrence; the command does not guess that a clock-only
reset already passed. An absolute expired reset can proceed on its first call.
Missing or unparseable reset metadata never authorizes input.

Once due, the helper durably checkpoints `pending_effect.action = continue`,
`continue_intent`, the redacted evidence, and the consumed recovery budget
before sending input. Launcher-owned telemetry records one continuation and
its reset/attempt/pane evidence; coordinator grade stays pending. The helper
sends a fixed continuation instruction preserving the original assignment,
waits **two seconds**, sends Enter once, and verifies both owned-agent Working
and the visible Working marker. Immediate Enter does not reliably submit in
Codex. The attempt, route, assignment, worktree and output directory are kept.
Use a call budget greater than two seconds (30 seconds is the default).

Success appends `continue` to history. Failed text/Enter, a timeout, interruption
or missing Working leaves the journaled effect unresolved and spends the
budget. Exit 6 requires inspection; **do not resend manually or reset the
counter**. `status` and `resume` retain the pending effect without replaying it;
a later verified Working observation or a valid matching receipt can reconcile
it. A matching receipt still requires a verified idle, done, or exited worker
before settlement. Normal receipt and
settlement guards still govern collection and `finish`. Neither same-pane
continuation nor a fresh automatic retry may spend a second quota recovery.
Supervision should invoke this command instead of an unjournaled
send-text/sleep/Enter sequence; this change does not install or alter a live
supervision timer.

## Heartbeat adoption and effect reconciliation

The repository heartbeat uses Q9's supported `continue` operation. It does not
parse another quota banner or maintain a quota reset timer. Its positive-budget
`status` observation uses the shared classifier; `continue` owns reset anchoring,
identity checks, delayed submission, shared recovery accounting and Working
verification. The heartbeat retains `last-continue-<job>.json` and reports success
only for a verified Working result on the same attempt and pane.

Every heartbeat `resume`, `status`, `add`, `finish`, and `continue` uses
`--defer-quota-recovery`. This flag keeps a quota-stalled attempt available for
same-pane recovery, without preparing a fresh attempt or settling an idle quota
pane without a receipt. It also holds a fresh retry already prepared by an older
helper invocation. Returned follow-up argv preserves the flag. It is an
invocation policy (also exposing recovery counts in result JSON), not a
persisted change to the run or permission for a later unflagged command. A separately authorized fresh retry still requires the
mutation preflight below and shares Q9's single recovery budget.

Before the supervisor may send a quota continuation, the coordinator must
inspect receipts, declared artifacts and completed local/remote effects, then
atomically write `RUN_DIR/quota-recovery/JOB_ID.json` (the heartbeat's run root
contains `run`, so this is `run/quota-recovery/JOB_ID.json` there):

```json
{
  "schema_version": 1,
  "request_id": "the-managed-request-id",
  "job_id": "the-job-id",
  "attempt_id": "the-existing-attempt-id",
  "pane_id": "the-owned-pane-id",
  "assignment_sha256": "sha256-of-encoded-job-spec",
  "quota_sha256": "sha256-from-continue_quota_observed-evidence",
  "mutation_state": "mutation-checkpointed",
  "observed_at": "2026-10-27T00:00:00Z",
  "artifact_evidence": "Paths and observations for the receipt and declared artifacts inspected.",
  "effect_evidence": "Completed effects, checkpoint and why the remaining assignment is safe to continue."
}
```

These values are a schema example, not a ready reconciliation record. Use the
actual job from `state.json`; compute `assignment_sha256` with
`herdr_jobs.records.digest(herdr_jobs.records.encoded(job["spec"]))`. Take
`quota_sha256` from that attempt/pane's latest `continue_quota_observed` history
entry. A first heartbeat `continue` can record that evidence while waiting or
refusing a missing reconciliation record; no input or budget is spent. The
record's observation timestamp needs a timezone. Both evidence fields must be
nonempty and describe observed state, not intentions. `writes_repository: false`
does not establish that a task has no production effects.

The heartbeat passes `--reconciliation run/quota-recovery/JOB_ID.json` to
`continue`. Under the run lock, the helper verifies the request, job, attempt,
pane, unchanged assignment digest and current quota-output digest. It accepts
only `read-only`, `mutation-not-started`, or `mutation-checkpointed`;
`mutation-state-unknown`, missing/malformed records and stale identities block
input. Accepted evidence is copied into `continue_intent` before input. This is
a coordinator attestation checked for consistency, not automatic proof of the
external effects it describes. Direct coordinator calls may omit the flag, but
still require the mutation preflight in this reference.

An unresolved `continue`, exhausted budget or collected result holds recovery;
it never falls through to capacity input. Status can reconcile a prior delivery
from verified Working or a matching receipt, without replay. Missing receipts
alone do not block the helper's preflight; existing receipts still refuse
continuation. Failed/malformed continuation replies are retained for inspection
and are never reported as successful delivery. Legacy `quota-<job>.until` or
`.until.sent` files hold automatic recovery and backlog admission until the
coordinator reconciles the old unjournaled delivery. Do not erase them just to
rearm recovery or edit launcher telemetry to manufacture a budget.

This is a repository candidate, not a live deployment. Deploying the heartbeat
requires the matching helper supporting both new flags; no timer, enrollment,
host-contract or live supervisor change is performed here.

## Coordinator recovery and ownership

A coordinator that sees its own usage-limit banner/status stops task and run
mutations immediately. Before it becomes unable to act, it records the reset,
generation, exact live worker handles and last observations, pending decisions,
and one next action per task. It must not claim an automatic wake or restart.

Continue coordination only after the recorded reset on the same session, or
use the configured same-level coordinator fallback when concrete quota evidence
and the active routing policy allow it. A replacement coordinator is a
persistent successor: follow [successor.md](successor.md), including a
generation-specific acknowledgment and transfer by the retiring owner. The
successor first reconciles live workers and retained artifacts and adopts
workers only through their existing handles. The prior coordinator stops
mutating after transfer. If quota exhaustion prevents an unambiguous retiring
owner transfer, do not self-authorize a replacement or mutate task state; record
the ownership gap and durable resume instruction as specified by the
successor procedure. Human takeover is the authority when no retiring owner can
transfer control.

One reset wait or one same-level fallback is the entire automatic quota
recovery budget for that stalled owner. After it is consumed, stop and record a
blocked state with evidence; further retries require an explicit coordinator
or user decision. Human approval remains required for any provider/model change
outside the configured same-level fallback.

## Reconcile mutations before managed quota recovery

This is a coordinator preflight requirement. The heartbeat additionally passes
the reconciliation record described above; the helper validates its identity
and classification, while the coordinator owns the truth of its effect evidence.

Before any fresh managed attempt, inspect the existing receipt, declared
artifacts, and mutation checkpoint. Classify the task as read-only,
mutation-not-started, mutation-checkpointed, or mutation-state-unknown. A fresh
attempt is eligible only when its unchanged assignment can be executed safely
from that observed state. Unknown mutation state blocks automatic replay; keep
the original attempt and record the reconciliation needed. Never duplicate a
production action merely because the quota reset has passed.

An explicit coordinator decision may select the existing exact Codex pane when
a fresh attempt could repeat production effects. Use `herdr-jobs.py continue`
after reconciling those effects; preserve the before-and-after mutation
checkpoint alongside its journaled task/attempt/route, reset source, exact pane,
continuation budget and Working observation. Workers and ad hoc scripts must
not edit launcher-owned telemetry.

When an operator observes a quota banner that the helper does not classify,
retain the bounded redacted excerpt and mark detection as unknown or missed,
not false. Persist provider backoff through the supported admission command and
record the affected task/attempt separately. The supported same-pane helper
accepts only classified Codex evidence, binds it to the existing attempt and
pane, and consumes the same single recovery
budget as automatic detection. Unsupported evidence still needs coordinator
reconciliation; do not convert it into permission to send input. A manually
submitted continuation must not be reported as zero continuations.
