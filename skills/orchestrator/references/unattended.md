# Unattended runs

Read before the user leaves on a run that continues without them (overnight,
travel, an "AFK" or night batch), and again when preparing the decision doc that
ends it. These rules add to the branch that runs the work; they do not replace
the worker contract, state ledger, or review boundary.

**Job.** Turn the time the user is away into verified findings and a short list
of decisions, without effects the user would have to undo.
**Done.** Every worker has a terminal disposition, every decision item has
passed the evidence gate, and the decision doc has been checked against the
ledger and delivered. Stopping early ends the coordinator's turn, not the run:
record the gap, retained work, and resume instruction in `STATE.md` and the
doc's Exceptions, and keep the run open until every worker has a terminal
disposition and `check-closeout` passes.
**Authority.** Only the standing authorization in preferences and the briefs.
Findings are proposals. An unattended worker's approval to act comes only from
a later user reply naming its item.

The ranked rules and failure evidence behind this file are in
`~/agents/research/afk-orchestration-reliability-2026-10-03.md`; failure numbers
(F1–F10) below refer to its case list. Values marked *starting* are design
choices to calibrate from run outcomes, not measured optima.

## Admit the night

Before dispatch, record a `Night budget` block in `STATE.md`:

```text
Night budget: window <start–end local>; max concurrent <n>; per-task wall clock <min>;
transient retries <n per task>; decision items <n>; supervision <mechanism + handle, or gap>
```

Confirm the local time is inside the window and that no other run for this
night is active; refuse a late catch-up start or a duplicate night. The
coordinator enforces the per-task wall clock itself: at the limit, inspect the
worker, stop it by its owned identity per
[runtime operations](runtime.md#observe-stop-and-clean-up), and record it
`failed` with the timeout as reason. Add a token or turn cap only when the
selected runtime enforces one, and name that mechanism.

Admit only bounded tasks, each with a question, expected evidence, and the
person who decides on it. Prefer tasks with a fresh trigger and objective inputs
(a new advisory, a reproducible CI failure, a decision due tomorrow) over
periodic maintenance. Write a `queued` row for every admitted task before the
first launch. *Starting* values: up to two transient retries per task, and a
decision-item cap the user can review in one sitting (about five). When the
decision cap is reached, stop dispatching tasks that could add decisions;
leave them queued for the next run rather than growing the morning queue (F8).

## Supervision that outlives the coordinator

In-session cron jobs, background watchers, and timer tools end with the
coordinator session, and some cap their own runtime (a background watcher
stopped at its 2-hour cap during an earlier night run). Workers then keep
running unsupervised (F1).

Durable supervision requires a mechanism outside the coordinator session — for
example a host systemd user timer running a finite reconcile pass over
`STATE.md`, live workers, and result files — that has been tested on this host
against coordinator death. Record it and its last observation in the
`Supervision` line. If no such tested mechanism is installed, record
`Supervision: in-session only (gap)` and tell the user before they leave what
happens if the coordinator stops: running workers continue, nobody grades
their results, and the morning resume reconciles through the active branch's
normal procedure — [the successor procedure](../SKILL.md#persistent-herdr-successor-replacement)
when replacing a coordinator in a persistent Herdr run.
Do not describe in-session timers as overnight supervision.

Spend coordinator context on the ledger, not on evidence. Open a worker's report
only to verify or compile it, and renew the coordinator per
[state and continuity](state.md#renew-early) rather than letting a long night
accumulate in one context (F8).

## Workers

Unattended workers are read-only except for their own attempt directory.
Changes to live systems or repositories are staged as reviewable commands in the
report, never applied. Give each worker the full brief: do not trim sections for
a smaller or cheaper model, because sections a strong model could skip were
load-bearing for a smaller one (F10).

Use the strongest available boundary: the research jail for research, and a
pinned commit with output outside the checkout for repository reads (see the
[worker contract](workers.md#assignment)). A pinned commit and separate output
directory give provenance, not write protection. Before dispatch, record whether
the selected route enforces read-only source, from its observed mounts or
sandbox mode rather than its name; where nothing enforces it, record the
isolation gap and do not call the run read-only-enforced. After each repository-reading worker
is terminal, compare the checkout's `HEAD` and `git status --porcelain` with the
values recorded before launch; a difference quarantines that worker's result
and becomes an Exceptions item. This check cannot detect a write that was later
restored, so it supplements the boundary rather than replacing it.

Apply the worker contract's [attempt isolation and freshness](workers.md#result-record)
checks and its [completion barrier](workers.md#validate-and-recover) (F2, F4).
When accepting an artifact, record its SHA-256 in the disposition evidence and
compile the doc from those bytes. Launch counts only when the worker is observed working, per
[runtime operations](runtime.md#herdr-launch) (F5).

## Failures and retries

Classify each failure before acting on it:

- **Transient provider or network failure** (for example "model service
  temporarily unavailable", a 5xx, or a rate limit): retry in a new attempt
  directory through the admission backoff in
  [runtime operations](runtime.md#provider-admission), within the task's
  retry budget and the night window.
- **Anything else** — schema, provenance, policy, premise, or fact-check
  failure: do not retry. (A known benign legacy result shape is normalized and
  revalidated per [the worker contract](workers.md#result-record) first; only
  what still fails counts as a schema failure.) Mark the task `blocked` or `failed`, keep its last
  error and evidence, and list it under Exceptions.

When a provider fails three consecutive launches (*starting* value; F7 observed
3 failures in 6 attempts, not a consecutive streak), stop new launches on that
provider for the rest of the night and record it. One
endpoint failing while another succeeds is a routing fact for the doc, not a
reason to change a requested harness or model.

## Evidence gate

Before any finding enters the decision doc as ready, a worker other than its
author reopens each cited source or code location for every claim the user
might act on, quotes the supporting passage, and marks the claim `verified`,
`contradicted`, or `unverified`. Agreement between two model runs is not
evidence: runs of the same brief cited nearly disjoint sources and got 7 of 20
claims wrong (F7). Only verified claims may appear as ready or FYI. Withdraw a
contradicted claim or replace it with a verified correction; list unverified
claims under Exceptions with the missing evidence. Research workers keep their own
mandatory check in preferences; this gate covers every decision item.

## Decision doc

The format is the user's preference in [PREFERENCES.md](../PREFERENCES.md)
(quick answer sheet first, then per item: what is ready, recommendation, exact
commands, one-line reply). Build it from the ledger, not from worker prose:

1. Deduplicate findings by issue and source snapshot before counting
   decisions, keeping every supporting report path on the surviving item.
   Group items as **Decisions** (needs a reply), **FYI** (verified, no reply),
   and **Exceptions** (blocked, failed, quarantined, or unsupervised work, with
   the missing evidence). End with an audit line: run ID, task IDs, source
   commits, and report paths.
2. Give each decision item an ID and make the reply format name the run and
   item IDs (`<run>: A1=approve; A2=defer; A3=reject <reason>`). An approval
   covers only the named item at the recorded snapshot. It becomes a new
   assignment under the normal review boundary; it never extends to the whole
   report or authorizes a merge.
3. A drafting worker may write the prose. Before upload, the coordinator checks
   every item's status, task ID, and artifact path against `STATE.md` and the
   dispositions, and corrects any mismatch; in an earlier night run a drafted
   doc stated one item's status wrongly (F9). Also scan the compiled doc for
   secrets and account numbers. Upload only after every item matches and the
   scan is clean.
