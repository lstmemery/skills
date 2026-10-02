# Task state and continuity

Read at run start, resumption, steering, supervision, and coordinator handover.

## Durable records

Use one run directory under the active profile's output root. Keep a compact
`STATE.md` and `tasks/<id>/` folders outside disposable worktrees. The coordinator
alone updates the ledger; workers write briefs' result locations and artifacts.
Replace the ledger atomically. Read active rows and the records touched by an
event; archive terminal rows with links to retained evidence.

Before launching a worker, add it to the run-level `workers.json` roster described
in [the worker contract](workers.md). The coordinator owns this roster and each
worker's `disposition.json`; a run is not closed until the worker-contract
`check-closeout` command accepts the roster and discovers no unrostered
worker-shaped directory. A run with no workers may omit the roster; closeout
treats it as empty but still scans the run tree.

## Current-state premise check

Before launching implementation for an assignment that depends on mutable live
state, check the material premise from a current read-only source. This includes
host or service state, repository or data paths, backup/protection coverage,
installed versions, and existing owner decisions. The ticket text and older
evidence describe intent or past state; they do not confirm current state.

Record each check in `STATE.md` before launch, with enough detail to reproduce
what was checked:

```text
Task | Ticket premise | Read-only source (exact command/path/lookup) |
Checked at (UTC ISO 8601) | Observed value | Verdict (confirmed|contradicted|unknown)
<id> | <claim that determines scope> | <source and query/target> |
<YYYY-MM-DDTHH:MM:SSZ> | <current result> | <verdict>
```

If a premise is contradicted, add a dated rescope request to `STATE.md` naming
the contradicted claim, the observed value and source, the decision needed from
the owner, and that no implementation worker was launched. Keep the assignment
queued or blocked pending that decision. If the source is unavailable or the
observation is inconclusive, record `unknown` and the evidence gap; do not
launch implementation against an unverified premise. The 2026-10-01 batch
preflight, recorded in its run `STATE.md`, is a recent example: #1080, #1046,
and #1088 were already done, while #1081 and #1082 remained under Matt's
wait-for-upstream decision and were assigned a read-only upstream recheck.

```text
Run: <id>; updated: <UTC>; owner: <harness + actual session/pane identity>
Generation: <positive integer>; phase: <active|transferring>
Supervision: <mechanism + handle + last observation, or explicit gap>
Premise checks: <task + source + checked-at UTC + observed value + verdict, or none>

Task | Revision | Status | Worker/location | Brief/result | Next action
<id> | <positive integer> | <lifecycle status> | <actual handles> | <paths> | <action>

Decisions/steering: <task + candidate/action + user instruction + disposition>
Rescope requests: <task + contradicted premise + owner decision requested + status, or none>
Retained resources: <task + worktree/lease + review location + cleanup condition>
```

The brief owns scope, acceptance, dependencies, and authority; the result owns
artifact/check evidence. Link these rather than copying them into the ledger.
Record exact harness and actual backend IDs, model or `unknown`, cwd, and any
worktree/base/lease identity in the task's location record; long records may live
beside the brief. A new queued task has no worker yet. Unknown values stay
explicit. Backend storage owns live process state; the ledger owns the requested
outcome and pending decisions. Reconcile the two before dependent mutations.

Record retry overrides in the run's durable state and pin them to that run's
workers. If retry tuning is needed but that runtime has no supported run-scoped
override, record the proposed values, affected run and worker, staged command,
and `awaiting authorization` in Decisions/steering. Do not change shared agent
settings or retry a worker with new shared settings before authorization.

## Lifecycle and steering

`queued` becomes `running` after an observed launch. A returned result becomes
`needs_review` when its human inspection package is ready, or `blocked` when a
decision/capability is missing. `done` requires the requested artifact/action and
its evidence. `failed` records unsuccessful work requiring a new plan; `cancelled`
records a user stop or superseded assignment and resource disposition.

A human-held review is not an agent failure. Continue independent tasks. A backend
`idle`, `done`, or exited process does not directly set task `done`; inspect the
result contract. Record inconsistent or unreadable worker state as unknown
evidence and reconcile before acting.

Record user steering promptly in task order. Apply a cancellation or correction
to the current unit at once, safely stopping the exact affected work. Other
steering takes effect at the next safe boundary. Answer status questions promptly;
do not silently defer them behind execution. Increment the assignment revision
when its substance changes and invalidate approvals/results that no longer match.

## Supervision

For every running task, retain an observable wait/event handle and an active
coordinator owner. Use backend events or server-owned waits; harvest ready
results across workers so one long task cannot hide another's decision.

When the harness can yield an active tool and resume on completion, retain that
handle. When verified automatic wake support exists, record its owner and current
health. Otherwise stay in active, bounded foreground waits. Use the tool's yield
mechanism and keep individual blocking intervals at most 60 seconds; avoid busy
model polling. A quiet timeout is an observation point, not evidence of a stalled
worker. Inspect state before retrying a mutation or declaring work stuck.

Continue authorized follow-ups while the user is away. End an orchestration turn
with live workers only when a verified supervision mechanism or successor owns
them, or when explicitly reporting the missing supervision capability. If all
remaining work needs the user, checkpoint those decisions and return them.

Herdr terminal persistence alone cannot wake a stopped coordinator. A killed
session can be reconciled from records on restart, but automatic recovery exists
only if a tested runtime mechanism provides it. State the actual guarantee.

## Renew early

Check a reliable harness context indicator at task boundaries. Use the user's
configured working-context budget when present; prepare renewal before the next
assignment would exceed it or the harness reports impending compaction. With no
reliable budget/indicator, renew between completed bounded task batches instead
of accumulating another batch. A large advertised context window is not a reason
to defer renewal to a universal token count.

A safe boundary has no coordinator mutation in flight, durable records for every
worker and pending user decision, and no result left only in the retiring
coordinator's context. Workers may still be running if the successor can adopt
their exact handles and supervision.

1. Stop new assignments and checkpoint. Set phase `transferring`; the old owner
   remains responsible for observation, with task mutations paused.
2. Start the same harness/profile in a new pane when supported. Pass only the
   run directory and a compact objective/constraint/next-action summary. The
   successor starts as an observer, reconciles live IDs and artifacts, and writes
   an acknowledgement for the pending generation in a separate handoff file.
3. After that acknowledgement, the old owner atomically changes coordinator
   ownership to the successor, increments generation, and sets phase `active`.
   The old owner immediately ceases task control. Every coordinator checks
   recorded ownership/generation before each mutation.
4. The successor establishes its waits and records supervision acceptance.
   Only then may the old coordinator close its own pane. Keep workers and their
   workspaces alive through the transfer; worker completion during transfer is
   reconciled from result files and live state.

If startup fails before transfer, the original owner resumes. After transfer,
the former owner remains read-only; it must not reclaim ownership merely after
a timeout. Establish the successor's actual state before recovery. If safe
ownership cannot be established, preserve work, pause mutations, and report the
gap. Outside a capable pane backend, provide the durable resume instruction and
report that automatic renewal is unavailable.

## Tool card

**Purpose/selection:** record task transitions and hand off a live run.
**Inputs/outputs:** the ledger shape above; atomic state file replacement,
actual backend observations, and generation-specific acceptance receipts.
**Effects/authority:** local state writes; successor launch and retirement of
the coordinator's own pane only within the current task/profile. No unrelated
worker cleanup or outside-profile launch. **Execution:** one coordinator writer;
checkpoint after each material transition; bounded observation of launches and
handoffs. **Recovery:** reconcile durable state with live identities before any
retry; unknown ownership stops mutations. Test the handoff on the actual harness
before relying on unattended renewal.
