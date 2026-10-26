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

The F-number references below summarize supplied case observations inline;
they are examples, not measured population rates. Values marked *starting* are
design choices to calibrate from run outcomes, not measured optima.

## Admit the night

Before dispatch, record a `Night budget` block in `STATE.md`:

```text
Night budget: night_id <YYYY-MM-DD in profile timezone>; status <admitted|closed>;
reservation <run-root>/.night-admission/<night_id>.lock; window <start–end local + zone>;
max concurrent <n>; per-task wall clock <min>; transient retries <n per task>;
decision items <n>; supervision <mechanism + handle, or gap>
```

Use the active profile's run root. Confirm the local time is inside the window,
then reserve the night atomically by creating its `.lock` directory with an
exclusive `mkdir`; record the run ID and `STATE.md` path in the reservation.
If it already exists, refuse admission unless it names this same run being
resumed; an unreadable or different owner's reservation blocks admission until
that run is reconciled. After reserving, scan sibling run directories for a
`STATE.md` with the same `night_id`. Any matching budget whose status is not
`closed` is active, even if it has no launched workers yet; refuse the duplicate
and release only the reservation this admission attempt just created. Keep an
admitted run's reservation until every worker is terminal and `check-closeout`
passes, then set the budget status to `closed` and remove its reservation.
Never clear a stale reservation without reconciling its recorded run. Refuse a
late catch-up start. The coordinator enforces the per-task wall clock itself:
at the limit, inspect the worker, stop it by its owned identity per
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
leave them queued for the next run rather than growing the morning queue (F8:
  coordinator context/quota proved scarcer than worker quota).

## Supervision that outlives the coordinator

In-session cron jobs, background watchers, and timer tools end with the
coordinator session, and some cap their own runtime (a background watcher
stopped at its 2-hour cap during an earlier night run). Workers then keep
running unsupervised (F1: in-session timers also vanish with the coordinator).

On Matt's desktop, the standard tested durable mechanism is the systemd user
`codex-heartbeat.timer`, described below. It runs outside the coordinator
session; it can resume managed work and queue an identity-checked wake even
while the coordinator is busy or absent. Other hosts need their own tested
out-of-session mechanism. Record the mechanism and its last observation in the
`Supervision` line. If no such tested mechanism is installed, record
`Supervision: in-session only (gap)` and tell the user before they leave what
happens if the coordinator stops: running workers continue, nobody grades
their results, and the morning resume reconciles through the active branch's
normal procedure — [the successor procedure](successor.md#persistent-herdr-successor-replacement)
when replacing a coordinator in a persistent Herdr run.
Do not describe in-session timers as overnight supervision.

Spend coordinator context on the ledger, not on evidence. Open a worker's report
only to verify or compile it, and renew the coordinator per
[state and continuity](state.md#renew-early) rather than letting a long night
accumulate in one context (F8: coordinator context/quota was the scarce resource).

### Desktop codex-heartbeat

D9 (2026-10-25) adopts the desktop heartbeat as the tested unattended mechanism.
After rebasing onto n3 (`d6c2a7e`), the canonical heartbeat is
[`scripts/codex-heartbeat.sh`](../scripts/codex-heartbeat.sh); its SHA-256 matches
the live desktop copy checked on 2026-10-26. The wake helper remains vendored in
[scripts/supervision](../scripts/supervision/README.md). These host-specific
scripts are not an installer. D9's original documentation check observed the
timer enabled and active; this rebase did not kill a coordinator or restart the
timer to repeat the earlier operational test. A live timer is evidence of
scheduling, not proof that every recovery branch succeeded.

| Desktop file | Role |
|---|---|
| `/home/deadhand/.local/share/orchestrator/supervision/codex-heartbeat.sh` | One locked supervision pass over enrolled runs; invokes the wake helper at the end. |
| `/home/deadhand/.local/share/orchestrator/supervision/wake-coordinator.py` | Delivers queued events only to the recorded idle agent/session/pane. |
| `/home/deadhand/.local/share/orchestrator/supervision/coordinator.json` | Coordinator split origin and wake target; keys: `pane_id`, `agent`, `session`; currently Claude. |
| `/home/deadhand/.local/share/orchestrator/supervision/active-runs.txt` | One absolute run-root path per line; read literally, including spaces. |
| `/home/deadhand/.config/systemd/user/codex-heartbeat.service` | Runs the finite heartbeat pass outside the coordinator session. |
| `/home/deadhand/.config/systemd/user/codex-heartbeat.timer` | Schedules passes at roughly two-minute intervals; inspect actual scheduling on the host. |

By default the heartbeat points the helper to
`/home/deadhand/.claude/skills/orchestrator/scripts/herdr-jobs.py`, policy to
`/home/deadhand/.claude/skills/orchestrator/launch-policy.json`, host contract to
`/home/deadhand/.config/orchestrator/host-contract.json`, and Herdr socket to
`/home/deadhand/.config/herdr/herdr.sock`. It sets its own executable `PATH`, including
`/home/deadhand/.local/bin` for the orchestrator binary and
`/home/deadhand/.local/npm-global/bin` for installed CLI tools, so systemd can
resolve the launch dependencies without an interactive shell. Offline replay
can override `SUP`, `HERDR_JOBS`, `HERDR`, `QUOTA_AXI`, `WAKE_COORDINATOR`, `HC`,
`P`, `HEARTBEAT_PATH`, `HEARTBEAT_NOW`, and `HEARTBEAT_SEND_DELAY`; the default
socket remains host-specific. It reads
`HERDR_PANE_ID` from `coordinator.json` before acquiring the pass lock and derives
`HERDR_WORKSPACE_ID` from the prefix before `:`. `herdr-jobs` splits the current
pane to launch workers; systemd supplies no pane, so the recorded coordinator
pane is also the launch origin. It attempts this read even when no runs are
enrolled. Keep the file valid and current: a failed read leaves the pane empty
without stopping the pass.
It uses a nonblocking `flock` on
`/home/deadhand/.local/share/orchestrator/supervision/codex-heartbeat.lock` to
avoid overlapping passes. No live installation, enrollment, or unit change is
performed by copying the skill.

For each enrolled run root, the current script:

1. Requires its `run` subdirectory and queries `quota-axi --provider codex
   --json --no-credential-refresh`. Unknown, stale, invalid, or failed quota
   evidence blocks both `resume` (which can launch pending jobs) and backlog
   `add`. The first provider's minimum remaining percentage across all windows
   must exceed **10%**. At or below that threshold it queues one quota-gate wake,
   tracked by the supervision-wide `quota-gate.notified`; evidence above 30%
   clears that notification marker. Existing jobs can still be supervised.
2. Requires a positive integer concurrency from `run/state.json` at
   `request.concurrency`. When quota permits, calls managed `resume` with a
   20-second budget, retaining `last-resume.json`. A nonzero exit, JSON error,
   malformed JSON, or missing `jobs` list disables backlog launches for that
   pass. It reads `status --summary --wait-seconds 0`; this is local checkpoint
   evidence, not another live observation pass. Status exits 6 and 10 preserve
   supervision of unaffected jobs but disable launches; other nonzero exits or
   a missing summary header skip the run. It appends to `heartbeat.log`.
3. Clears `quota-<job-id>.until` and its `.sent` marker for `DONE` or `CANCELLED`
   jobs. Cancelled jobs consume no active slot and receive no pane reads,
   labels, quota continuation, or per-job DONE wake. DONE jobs queue a wake once,
   tracked by `out-<job-id>/.woke`. Other jobs with a pane are counted as active;
   their workspaces are renamed `codex · <run-basename> <job-id>` once, tracked
   by the run's `labeled` file.
4. Reads the last 15 unwrapped lines of each unfinished worker pane and
   normalizes whitespace, including wrapped banners. The current grep matches
   `you've hit your usage` with optional ` limit`, case-insensitively, when
   `Working (` is absent. Echoed continuation text alone does not match. It
   parses a `try again at … AM/PM` timestamp into `quota-<job-id>.until`, using
   the host timezone; parse failure falls back to one hour, and a time over ten
   minutes in the past is advanced a day. At reset plus five minutes it sends
   continuation text to the same pane, waits **two seconds** by default, then
   sends Enter. Immediate Enter did not reliably submit in the operational test.
   Only successful text/delay/Enter delivery creates `.until.sent`; failures
   log `continue-failed` and can retry on later passes. The reset and sent files
   remain while the banner persists, preventing repeated successful sends for
   that stored reset. They clear when the banner disappears, Working appears,
   or the job becomes terminal. A later banner can create another reset.
5. Feeds `backlog/task-<job-id>.md` in `ORDER` order only when launches remain
   allowed, quota exceeds 10%, no `quota-*.until` files remain, and the active
   pane count is below the **stored concurrency**. It copies the brief into the
   run root, writes `add-<job-id>.json` for one managed `add`, and retains
   `add-<job-id>.out`. The manifest preserves `request.concurrency` and reads
   the explicit route from `backlog/route-<job-id>.json`; without that file it
   defaults to Codex `gpt-6-luna`, provider `codex`, effort `low`, with the
   instruction `Ladder level 1 default (no route file).`. It sets
   `writes_repository: false`, with output and worktree editable paths plus
   nonempty entries from `backlog/editable-<job-id>.txt`. Routes and briefs must
   already be authorized; these files cannot grant authority or replace a
   repository-writer lease. A nonzero add exit, truthy JSON `error`, malformed
   response, or missing `jobs` list queues a wake, logs `add-failed`, and stops
   feeding for that pass without logging added or incrementing the active count.
6. Reads status again, accepting exits 0, 6, and 10 with a summary header.
   When all status rows have `DONE` or `CANCELLED` and every `ORDER` entry has
   been added, calls `finish` with a ten-second budget. Cancellation counts as
   terminal so deliberately stopped jobs do not keep the run enrolled forever.
   It retries on later passes until the response has no truthy `error`,
   `finish_complete` is exactly `true`, and top-level `jobs` is a nonempty list.
   Every job must explicitly contain `pane_id` and `workspace_closed`, with
   `workspace_closed` exactly `true` or `pane_id` exactly `null`. There is no
   `checkpoint.jobs` fallback. Finish exits 0, 6, and 10 are accepted only with
   that complete evidence. Only then does it remove the exact run line from
   `active-runs.txt` and queue a review wake. The wake text still says all jobs
   are DONE, even when some were cancelled; inspect their markers and reports.
   `DONE` and `CANCELLED` are feeder signals, not acceptance: collected receipts,
   dispositions, coordinator review, and the worker-contract `check-closeout`
   still decide task/run completion.

After processing all runs, the wake helper reads
`/home/deadhand/.local/share/orchestrator/supervision/wake-queue.txt`. It queries
`herdr agent list` and requires **all three** recorded values to match:
`pane_id`, agent kind (`claude` for the current coordinator), and
`agent_session.value` equal to `session`, with `agent_status` exactly `idle`.
A missing, busy, or replaced session leaves events queued. It never intentionally
types into an unmatched shell. A successful `herdr agent prompt` sends at most
the last eight queued lines, marks them as heartbeat evidence rather than user
input, appends to `wake-sent.log`, and clears the queue. Prompt failure retains
the queue. This wakes an existing coordinator; it does not create or resurrect
a dead Claude session.

#### Successor wake target

Complete the ownership/generation acknowledgment and transfer in
[state and continuity](state.md#renew-early) and
[the successor procedure](successor.md#persistent-herdr-successor-replacement)
first. After becoming the recorded owner, the successor observes its actual
identity with `herdr agent list`, then atomically replaces
`/home/deadhand/.local/share/orchestrator/supervision/coordinator.json` using a
temporary file in that same directory followed by rename. Preserve exactly this
shape with the successor's observed values, never the predecessor's identifiers:

```json
{"pane_id": "<observed successor pane>", "agent": "claude", "session": "<observed agent_session.value>"}
```

Read it back and compare all three values against the live agent before
recording supervision acceptance. Updating this file changes the wake target
and the pane/workspace used for subsequent worker splits; it does not transfer
coordinator authority. Keep `active-runs.txt` and pending wake events intact during succession. A non-Claude successor needs a
separately verified prompt route; do not infer compatibility from the three-key
file. This reference describes the authorized handoff procedure; D9 does not
change the live target.

#### Limits and pending detector integration

- **s1 integration:** the shared Codex detector from
  `d1024b/s1-codex-usage-banner` is already merged (`72e7710`), but n3's
  canonical heartbeat and the matching live copy still use their own grep.
  The remaining follow-up is wiring the heartbeat to
  `herdr_jobs.admission.rate_limit_from_output(..., source="pane", runtime="codex")`
  and validating recovery before an authorized deployment. This rebase keeps
  n3's script unchanged and does not deploy that follow-up.
- This is a desktop Codex/Claude bridge, not a generic scheduler. Bash, GNU
  `date`, `flock`, Python, `quota-axi`, the installed helper/Herdr contract, and
  a running systemd user manager are prerequisites. Suspend, logout/user-manager shutdown,
  host reboot, provider availability, and a dead coordinator can delay or prevent
  progress; boot/login and lingering guarantees require separate host testing.
- Pane-tail text and localized reset timestamps are heuristics. Old transcript
  text can re-trigger the grep after its wait markers clear, and a 15-line
  tail can miss a banner. Failed continuation delivery remains eligible for
  later retries; there is no lifetime retry bound.
  The wake identity check and prompt are separate calls, so session replacement
  between them remains a race, not an atomic identity guarantee.
- The script parses the helper's summary table, not a versioned API. It has no
  aggregate pass deadline, per-task wall-clock enforcement, retry budget, or
  independent result grading. Its active count is a local heuristic; managed
  concurrency/provider leases remain authoritative. A failed or malformed
  add response may follow an already-issued effect, so reconcile managed state
  and receipts before any retry.
- The heartbeat lock serializes heartbeat passes, not coordinator edits to the
  enrollment file, target file, or queue. The enrollment rewrite and queue clear
  can race with external writers. Coordinate such changes at a safe boundary;
  after a partial pass or a missing wake, inspect run logs, receipts, queued
  events, live identities, and managed state before retrying. `finish` cleanup
  is not coordinator acceptance and cannot replace the run closeout gate.

## Workers

Unattended workers are read-only except for their own attempt directory.
Changes to live systems or repositories are staged as reviewable commands in the
report, never applied. Give each worker the full brief: do not trim sections for
a smaller or cheaper model, because sections a strong model could skip were
load-bearing for a smaller one (F10: a smaller model omitted such sections).

Use the strongest available boundary: the research jail for research, and a
fresh, clean worktree pinned to the candidate commit with output outside the
checkout for repository reads (see the [worker contract](workers.md#assignment)).
A pinned commit and separate output directory give provenance, not write
protection. Before dispatch, record whether the selected route enforces
read-only source, from its observed mounts or sandbox mode rather than its name;
where nothing enforces it, record the isolation gap and do not call the run
read-only-enforced. Verify the worktree is clean with
`git status --porcelain --untracked-files=all`, and record that output and its
`HEAD` before launch. After each repository-reading worker is terminal, compare
the checkout's `HEAD` and status with those launch values; a difference
quarantines that worker's result and becomes an Exceptions item. The clean
baseline makes persistent tracked edits and untracked additions visible even
when the coordinator's own checkout is dirty. This check still cannot detect a
write restored before the comparison or writes to ignored paths, so it
supplements the boundary rather than replacing it.

Apply the worker contract's [attempt isolation and freshness](workers.md#result-record)
checks and its [completion barrier](workers.md#validate-and-recover) (F2: a
worker turn ended while its background driver still ran; F4: a shared output
folder exposed an earlier run's artifact). When accepting an artifact, record
its SHA-256 in the disposition evidence and compile the doc from those bytes.
Launch counts only when the worker is observed working, per
[runtime operations](runtime.md#herdr-launch) (F5: a terminal warning overlay
swallowed a prompt that appeared to send).

## Failures and retries

Classify each failure before acting on it:

- **Transient provider or network failure** (for example "model service
  temporarily unavailable", a 5xx, or a rate limit): retry in a new attempt
  directory through the admission backoff in
  [runtime operations](runtime.md#provider-admission), within the task's
  retry budget and the night window.
- **Anything else** — schema, provenance, policy, premise, or fact-check
  failure: do not launch a new attempt or retry the provider. First normalize a
  known benign legacy result shape and revalidate it per
  [the worker contract](workers.md#result-record). If required fields or
  acceptance evidence are missing, use only the bounded same-worker correction
  or continuation allowed by [Validate and recover](workers.md#validate-and-recover);
  that repairs the current attempt's evidence and is not a task retry. After
  that allowance is exhausted, or when correction is ineligible, mark the task
  `blocked` or `failed`, keep its last error and evidence, and list it under
  Exceptions.

When three failed launches occur within the latest six attempts on a provider
(*starting* window; F7 observed 3 failures in 6 attempts, including scattered
failures), stop new launches on that provider for the rest of the night and
record it. One endpoint failing while another succeeded 4/4 in the same case
(F7) is a routing fact for the doc, not a reason to change a requested harness
or model.

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

## Verify behavior of human-run procedures

For executable procedures delivered for a later human run, syntax and lint
success do not establish readiness. Retain a separate behavioral review before
presenting the procedure as ready. Exercise, with synthetic credentials and
mocked effects, the current installed configuration shape, environment/secret
activation, no-op or already-repaired path, partial failure, rollback, and final
revocation ordering where applicable. A read-only selected-field probe can
establish the installed shape without executing the procedure.

Retain this matrix with the candidate hash and review record. Mark inapplicable
rows with a reason; a missing applicable case leaves readiness unverified.

| Case | Evidence to retain |
| --- | --- |
| Installed representation | Selected fields or source showing literal, environment, or file-backed values |
| Activation | Mock that distinguishes persistence from reload/restart and confirms the active value |
| Already repaired | Existing working state is retained without forced replacement |
| Partial failure | Failed write/activation/check preserves a recoverable state and reports failure |
| Rollback | Synthetic failure restores the prior configuration and activation state |
| Revocation order | Old credentials remain available until the replacement's operation is verified |

Give the verifier the candidate bytes and acceptance claims, require it to
reopen the cited implementation or live selected fields, and keep its findings
separate from the author's report. Test both positive and negative cases.
Record exactly what remains future human execution; a mock passing does not
prove credential rotation, notification arrival, or a future scheduled run.
After a correction, identify the corrected artifact by absolute path and hash
and remove superseded copies from the decision document's runnable commands.

Record verifier yield as concrete confirmed defects and independent claims
checked, alongside unknowns and duplicated work. Do not infer a general defect
rate from one batch. Preserve checks that expose deployed behavior, such as an
actual encoder operation, alongside HTTP and systemd health checks.
