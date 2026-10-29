# Human code inspection

Read before presenting repository changes, processing code feedback, opening a
PR, or integrating a local result. Preferences own the human decision boundary.

## Bounded review/fix loops

Track review/fix rounds per candidate branch. The initial review establishes
the baseline and is not a fix/review round. Each subsequent cycle in which the
author addresses findings and the candidate receives a re-review counts as one
round. Record the round number and candidate head in the task record or review
artifact so a changed branch or resumed worker cannot silently reset the count.

Re-reviews default to the diff since the prior reviewed candidate plus all
previously reported findings that remain open. Recheck each prior finding
against the new candidate, including findings whose disposition was rejected;
preserve the recorded rejection and rationale unless the coordinator changes
it. Review the full branch when the coordinator requests it or when changes
cannot be safely assessed from that delta and finding set, and record why.

After round 3, stop before another fix/review cycle and require a coordinator
checkpoint. The checkpoint lists every remaining finding with its finding ID
and current disposition, classifying it as a new defect, previously rejected
scope, or a previously accepted finding that remains open. The coordinator
must choose exactly one next step:

- authorize one additional bounded fix/re-review round, specifying its scope;
  this authorization permits only round 4 and does not reset the count; or
- send the candidate for spec-level adjudication.

Do not begin another round without that recorded choice. After round 4, stop
again and return for spec-level adjudication; no further fix/re-review round is
authorized by this procedure. Record the checkpoint decision and resulting
candidate head with the review history.

## Prepare an inspectable candidate

Delegate preparation to the coding worker or a bounded review worker. Produce:

- intended repository/target branch, explicit comparison base, and candidate
  commit SHA; identify the exact comparison used;
- the complete task patch and changed-file inventory, including new files;
- access to full current files and a list of binary/large/otherwise omitted
  changes requiring a different inspection method;
- checks and agent-review findings with their evidence and unresolved items;
- the intended next action and any existing authorization for that action.

Before integrating or merging coding candidates, run the integration preflight
for the intended target and every candidate in the batch. It is a gate in
addition to human inspection; it does not merge anything or authorize a merge.
The command must return `outcome: ready`. Preserve its report with the merge
evidence. Run it again if the target branch or any candidate head changes.

```sh
python3 scripts/integration-preflight.py \
  --repo REPO --target TARGET_BRANCH --run-dir RUN_DIR \
  --candidate BRANCH_A=SHA_A [--candidate BRANCH_B=SHA_B] \
  [--defer TASK_ID[:REASON]]
```

Use one `--candidate` per coding branch. Pin each branch to its current commit
with `BRANCH=SHA`; the command verifies the local branch still points to that
SHA. A commit SHA can also be supplied without a branch name. The run directory
is the batch folder containing `workers.txt` and each task's `result.json`.
The preflight discovers coding task IDs from `workers.txt` and task `result.json`
files that have a `candidate` block. Every discovered task must be supplied as
a `--candidate` or explicitly deferred with a repeatable `--defer TASK_ID[:REASON]`.
Use a reason to make the handoff clear; deferred tasks appear in the report.
Omitted tasks, unknown deferrals, and tasks both selected and deferred block the
batch, while allowing the supplied candidates to proceed as a partial landing.

For managed coding and review jobs, pass their run directories directly instead
of assembling a batch roster or copying records:

```sh
python3 scripts/integration-preflight.py \
  --repo REPO --target TARGET_BRANCH --run-dir EVIDENCE_DIR \
  --managed-run CODING_RUN [--managed-run REVIEW_RUN] \
  --candidate BRANCH=SHA
```

`EVIDENCE_DIR` must exist. Repeat `--managed-run` for separate runs; supply a
shared run only once. All supplied jobs must be settled with complete,
unresolved-free collections. Coding jobs must enable `worker_result` and collect
their assignment-bound `result.json` with its candidate block and declared
artifacts. Review jobs collect `review/review.md`,
`review/review-evidence.json`, and `review/capture.tar` in their receipt. Create
the uncompressed archive from the completed capture directories, preserving
their `review/` or `review-rN/` prefixes inside the archive:

```sh
tar -cf OUTPUT_DIR/review/capture.tar -C OUTPUT_DIR review/capture
```

When the bundle includes delta captures, add each `review-rN/capture` path as
another input to `tar`.

The archive counts as one receipt artifact regardless of the capture's file
count. Normal receipt limits still apply: 1–32 artifacts, at most 16 MiB per
artifact and 64 MiB in total. The adapter permits at most 20002 archive entries,
16 MiB per payload file and 64 MiB of expanded evidence per job. It accepts
only regular capture files and the `review/capture/` and
`review-rN/capture/` directories; links, special files, unsafe paths, duplicate
members and collisions with individually collected evidence are refused. The
manifest, completion marker, diff, history and source blobs still undergo full
capture verification. Small captures may also collect every capture file
individually, as before. Use `review-rN/` for delta reviews and preserve that
prefix in the archive.
Link each sidecar to the coding job's `worker_result.task_id`; the review job
may have a different job/task ID.

In managed review briefs, supply the coding and reviewing jobs' full `agent_name`
values as `author_identity` and `reviewer_identity` for `validate-review`.
The adapter can fill omitted identities in older collected sidecars and takes
panes from helper state.
Supplied identities must agree with that state. The adapter refuses a shared
author/reviewer identity or pane. New reviewer briefs require `done.json`,
including the explicit verdict. For older collected outputs, the adapter can
derive counts and capture ID from the sidecar when `done.json` is absent;
a supplied record is checked as usual. For findings, collect the coordinator-owned
`review-response.md` with the coding job and recollect sidecars after disposition sync. The adapter never
decides a finding's disposition.

The adapter verifies receipt request/job/attempt identity, collection revision,
artifact hashes and sizes, and worker-result assignment before using the
collected snapshots. Archives are read exclusively from receipt-pinned bytes;
worker output directories are not needed after collection. It writes a durable
projection under
`EVIDENCE_DIR/managed-inputs/DIGEST/` and runs the same capture, review-chain,
disposition, candidate-head and target-head checks as the legacy layout. The
report records `managed_input_dir` and `managed_jobs`, including original state
digests, collection provenance, identities and panes. Managed input is explicit;
legacy files in `EVIDENCE_DIR` are not combined with it. No job state, worker
output, or collected evidence is rewritten.

### Prepare older managed-job outputs

When the author job collected only a report (no `worker_result`), or reviewers
left loose output directories instead of receipt-pinned review artifacts, use
the staging helper. Prefer `--managed-run` above for jobs that already satisfy
that contract; this fallback reads mutable local review output and does not
claim receipt-verified provenance for it.

```sh
python3 scripts/worker-records.py prepare-preflight \
  --managed-run AUTHOR_RUN --job-id AUTHOR_JOB --task-id CODING_TASK \
  --repo REPO --target TARGET_BRANCH --candidate BRANCH=SHA \
  --review-output INITIAL_REVIEW_OUTPUT \
  --review-output DELTA_REVIEW_OUTPUT \
  --output NEW_BATCH_DIR
```

For a single managed run, select reviewers by job ID instead of looking up
output directories and repairing sidecars by hand:

```sh
python3 scripts/worker-records.py prepare-preflight \
  --managed-run MANAGED_RUN --job-id AUTHOR_JOB --task-id CODING_TASK \
  --repo REPO --target TARGET_BRANCH --candidate BRANCH=SHA \
  --review-job INITIAL_REVIEW_JOB --review-job DELTA_REVIEW_JOB \
  --review-response COORDINATOR_RESPONSE.md --output NEW_BATCH_DIR
```

Repeat `--review-job` in review order; it cannot be combined with
`--review-output`. With neither option, the helper selects jobs whose `review_of`
names `AUTHOR_JOB`, in managed request order, and requires their settled,
complete, unresolved-free collections. Each job ID must resolve exactly once in
managed state.
The helper looks only in that job's `host_output` and declared `editable_paths`
(relative paths use the job's `cwd`), directly or under `review/` or `review-r*/`.
Exactly one sidecar directory must exist per job; multiple matching directories,
duplicate job IDs and two jobs resolving to one directory are refused. Loose
review outputs need not have been collected, so this remains the mutable-local
fallback, without receipt-verified provenance. The author's existing settled,
complete collection requirement still applies.

This mode requires v2 evidence and writes exactly `schema_version`, `task_id`,
`author_identity`, `reviewer_identity`, `capture_id`, and `axes`. Top-level
extensions, including `axis_reviewers`, are omitted from the normalized copy;
finding evidence is retained and validated. Missing author/reviewer identities
come from managed state. A shortened identity is expanded only when it has at
least 16 characters and uniquely prefixes a known managed identity. Other
identity disagreements are refused. If a delta sidecar names a known fix author
instead of the task author, explicitly add `--author-alias-job FIX_AUTHOR_JOB`;
repeat for additional fix authors. These aliases authorize only that identity
normalization. Reviewers must have distinct identities and panes from the task
author and every declared fix author. The assembly provenance records source
identities and alias job IDs.

Every generated `done.json` includes task ID, capture ID, regenerated round
counts, and the reviewer's explicit verdict. For scheduled `review_of` jobs the
verdict must appear in source `done.json`. For older manually assigned reviewers
it may also appear as a standalone `Verdict: approve.` (also
`approve-with-nits` or `changes-requested`) line in `review.md` or the output's
`report.md`. Missing or conflicting verdicts, and supplied task/capture IDs that
disagree with the sidecar, are refused. Verdicts retain the reviewer's judgment
even after findings are fixed.

The job-ID mode always runs the same `sync-dispositions` implementation as
`worker-records.py sync-dispositions` before publishing. If any review has
findings, supply `--review-response` with every decision in this exact form:

```md
- `review/P1`: fixed — explanation
```

Use `rejected` for a rejection and `review-rN/ID` for later rounds.
Missing, duplicate, unknown or malformed decisions refuse publication; no
disposition is inferred. A clean review can omit the response
file. Source files remain unchanged; the new batch retains disposition-sync
backups and provenance. Run the gate below afterward: `prepared` still does not
establish readiness.

`AUTHOR_RUN` contains managed `state.json`; select exactly one author job with
`--job-id`. It must be settled with a complete, unresolved-free collection.
`--task-id` is the coding task ID in every review sidecar, which may differ
from the managed job ID. Repeat `--review-output` in round order; each path is
either a review directory containing `review-evidence.json`, `review.md` and
`capture/`, or a job output directory containing those under `review/`.
The helper names them `review`, `review-r1`, etc. It supports delta captures
and full-branch re-reviews, requires ordered coverage through the candidate,
and verifies each completed capture's payload. A bare commit SHA also works
as `--candidate`; prefer `BRANCH=SHA` to detect a moved branch.

The helper creates a **new** batch with `workers.txt`, a minimal gate-facing
`result.json` candidate block, review bundles, regenerated `done.json` counts,
`review-response.md`, and `assembly.json` source/state provenance. This
`result.json` is assembly metadata, not a fabricated full worker report or a
substitute for worker-result validation. Author identity/pane come from managed
state; with `--review-output`, reviewer identities come from the supplied
sidecars. Sidecar task and
author must match, and a reviewer cannot be the author. The final gate retains
its independent-review checks. `--review-output` preserves `axis_reviewers`.
Existing outputs are refused, source evidence is unchanged, links are refused, and failures do
not publish a partial batch. Unrelated probes and report files are excluded.

A successful command prints `outcome: prepared`, which is **not** integration
readiness. With findings, the response contains commented finding references
and leaves all dispositions untouched. The coordinator writes the explicit
`review/FINDING` and `review-rN/FINDING` decisions using the response grammar
below, then reruns preparation into another new directory with
`--review-response COORDINATOR_RESPONSE.md`. The helper copies that response,
applies only its recorded decisions through the shared disposition validator,
and recalculates initial counts or delta `new_findings`/`unfixed` counts.
Missing, unknown or malformed decisions fail preparation. Without that flag,
the path-based mode infers no dispositions or rationale, even if the sidecar is
already terminal. Pending findings continue to block the gate. The job-ID mode
requires explicit decisions before publication, as described above.

Finally run the existing gate against the generated batch:

```sh
python3 scripts/integration-preflight.py \
  --repo REPO --target TARGET_BRANCH --run-dir NEW_BATCH_DIR \
  --candidate BRANCH=SHA
```

Only `outcome: ready` from this command establishes readiness. It still checks
Markdown/sidecar agreement, terminal dispositions, counts, capture ancestry,
and live target/candidate heads. No merge or disposition decision is automated.

The preflight checks the worker result against the actual candidate commit and
the candidate's merge base with the target branch. It requires a complete
Standards and Spec review, an independent reviewer identity, current capture
coverage, and a recorded disposition for each finding. A full review may be
followed by contiguous delta reviews; every capture in that chain must cover
both axes. Missing, blocked, stale, ambiguous, or unresolved evidence refuses
the entire batch. The preflight writes a ready or blocked JSON report under
`RUN_DIR/integration-preflight/`; retain the report with the merge record.
Each selected capture is checked with `code-review/scripts/capture.py verify`;
the manifest, completion marker, source blobs, diff, and history must pass their
recorded digest checks, and the verified capture ID and complete coverage must
match the review records.

### Copyable review-worker brief (full or delta)

Include this block in every independent review assignment, filling the pinned
identities and paths before dispatch:

```text
Review kind: FULL (directory TASK_DIR/review/) or DELTA (TASK_DIR/review-rN/, N >= 1).
Task ID: TASK_ID. Candidate author: AUTHOR_ID. Responsible reviewer: REVIEWER_ID.
Use the supplied pinned candidate base/head and independent reviewer identity.
For DELTA, inspect the change since the preceding reviewed head, including whether
prior fixes hold; report new findings and any still-unfixed prior findings.

Write review.md, review-evidence.json, done.json, and one completed capture under
that review directory. Preserve the actual capture_id from capture/manifest.json
in BOTH JSON records. Capture evidence must include capture/COMPLETE and every
payload required by capture.py verify. Do not fabricate or repair capture metadata.
Put the explicit verdict in done.json: approve, approve-with-nits, or changes-requested.
Use the exact Markdown grammar and JSON fields below; counts must match findings.
Review findings start unresolved; the coordinator owns terminal dispositions.

Before emitting DONE, run from the orchestrator skill directory:
python3 scripts/worker-records.py validate-review REVIEW_DIR --kind full|delta --task-id TASK_ID --author-identity AUTHOR_ID --reviewer-identity REVIEWER_ID
Choose one literal kind, full or delta. Retain the command, exit code, and output
in the worker's evidence. Emit DONE only after exit 0; fix and rerun on failure.
If prior delta findings remain unfixed, report the blocker rather than claiming
unfixed=0. This validator checks reviewer output, not integration readiness.
```

Use these minimal records for a full review with zero findings. Substitute the
actual task, identities, capture ID and counts; do not copy placeholder values:

`review/done.json`:

```json
{
  "task_id": "TASK_ID",
  "capture_id": "CAPTURE_ID",
  "verdict": "approve",
  "standards_findings": 0,
  "spec_findings": 0
}
```

For a delta review, replace the full count fields with exactly these delta
fields in `review-rN/done.json`:

```json
{
  "task_id": "TASK_ID",
  "capture_id": "CAPTURE_ID",
  "verdict": "approve",
  "new_findings": 0,
  "unfixed": 0
}
```

Both kinds use the same `review-evidence.json` envelope:

```json
{
  "schema_version": 2,
  "task_id": "TASK_ID",
  "author_identity": "AUTHOR_ID",
  "reviewer_identity": "REVIEWER_ID",
  "capture_id": "CAPTURE_ID",
  "axes": {
    "standards": {"status": "complete", "findings": []},
    "spec": {"status": "complete", "findings": []}
  }
}
```

The minimal `review.md` for either kind is:

```markdown
## Standards

No findings.
Summary: findings=0; worst=none.

## Spec

No findings.
Summary: findings=0; worst=none.
```

For nonempty axes, use the finding records and Markdown grammar below, increasing
the corresponding full count or delta total. Required envelope fields are exactly
those in the sidecar example; `axis_reviewers` is the only optional envelope
field. It belongs in the sidecar, not in a finding or axis record. Each axis
record has exactly `status` and `findings`. New reviewer contracts require an
explicit verdict in `done.json`; scheduled-job preparation enforces it. The
bundle validator retains compatibility with historical count-only records.
The task, capture ID and counts for the chosen kind are always required;
a prose verdict cannot replace these fields.

The command reuses the preflight's `ReviewBundle` checks and capture verifier.
It permits `disposition: "unresolved"` for reviewer-owned findings before
coordinator response. Terminal findings still need matching recorded responses.
It never edits the bundle or syncs dispositions. The supplied author/reviewer
identities are checked against the sidecar; integration separately verifies the
actual `workers.txt` roster, author consistency, capture ancestry, candidate heads,
and terminal dispositions. A successful reviewer check cannot authorize integration.

### Machine-readable review evidence

Each `review/` or `review-rN/` directory used for integration needs a
`review-evidence.json` sidecar. The preflight verifies it against that
directory's `review.md`, `done.json`, and completed capture manifest. The first
worker identity listed for the task in batch `workers.txt` is the candidate
author. The author and reviewer identities must both appear there, and they
must differ. Record both axes even when an axis has zero findings:

```json
{
  "schema_version": 2,
  "task_id": "1123",
  "author_identity": "worker-1123",
  "reviewer_identity": "reviewer-1123",
  "axis_reviewers": {"standards": "/root/standards", "spec": "/root/spec"},
  "capture_id": "capture-id-from-done-and-manifest",
  "axes": {
    "standards": {
      "status": "complete",
      "findings": [
        {
          "id": "S1",
          "disposition": "unresolved",
          "reason": "Awaiting coordinator disposition.",
          "confidence": "high",
          "reproducer": "Run the focused command; observe the missing validation."
        }
      ]
    },
    "spec": {"status": "complete", "findings": []}
  }
}
```

`axis_reviewers` is optional in schema versions 1 and 2. When supplied, it must
map exactly `standards` and `spec` to nonblank reviewer identity strings of at
most 512 characters; neither may be the candidate author. Review briefs must
request identities returned by the agent helper, such as sub-agent task paths,
and preserve them verbatim. This is per-axis attribution: sub-agents need not
appear in `workers.txt`, and this metadata does not replace the independently
rostered `reviewer_identity` responsible for the bundle. The preflight retains
the attribution in its selected-review records. Omit the field when these
identities are unavailable; do not invent names.

Finding IDs must be unique within a review directory. Each must have a
`review-response.md` line in the task directory in this form:

```text
- `review/S1`: fixed — Corrected and verified.
```

Use the directory name in place of `review` for a revision review. The only
terminal dispositions are `fixed` and `rejected`; a rejection must include a
reason in both records. Missing dispositions, deferred findings, and unresolved
review findings block integration. The required `done.json` count fields depend
on the review directory type. A full `review/` requires `standards_findings`
and `spec_findings`, each matching its axis in the sidecar. A revision
`review-rN/` requires `new_findings` matching the total sidecar finding count
and `unfixed` equal to `0`. All count fields are required in their respective
review type; a missing count is invalid.

New review-evidence sidecars use `schema_version: 2`. New finding entries retain
`id`, `disposition`, and (when rejected) `reason`, and also record `confidence`
as `high`, `medium`, or `low`. Include a `reproducer` string with executable
steps or a focused check; when no executable check applies, include `evidence`
and `unresolved_assumption` strings instead. The preflight requires and validates
this metadata on every version-2 finding. For backward compatibility, existing
version-1 entries without evidence metadata remain valid; metadata present in a
version-1 entry is still validated. Do not write new findings in version-1
sidecars. This paragraph owns the finding-evidence field contract; the
restatements in the code-review skill (`SKILL.md`, `STANDARDS.md`) point here,
and both validators enforce it through the shared module
`herdr_jobs/review_evidence.py`.

**Ownership:** the independent reviewer owns `review.md`, `done.json`, capture
evidence, and the initial `review-evidence.json`; every finding starts as
`disposition: "unresolved"` until the coordinator records a decision. The
coordinator owns terminal dispositions and reasons in `review-response.md` and
syncs them into every matching sidecar before preflight:

```sh
python3 scripts/worker-records.py sync-dispositions RUN_DIR/TASK_ID
```

The helper requires exactly one response for every sidecar finding, refuses
unknown or missing finding references before writing, and preserves the first
pre-sync sidecar at `review-evidence.json.pre-disposition-sync`. Re-running it
with the same response is safe.

From this contract change on, every nonblank line in a Standards or Spec section
of `review.md` must follow the axis grammar below. Put each finding on its own
line under the matching axis with the same ID as the sidecar. A single level-one
document title may precede the two axis sections; use no other headings or prose
outside them.

```markdown
## Standards

- [S1] **MAJOR · CONFIRMED** — Missing validation for captured payloads.
Summary: findings=1; worst=Missing payload verification.

## Spec

No findings.
Summary: findings=0; worst=none.
```

Each line must be a stable-ID finding entry (`- [ID] <finding>`), the exact
no-findings line (`No findings.`), or a summary line in the form
`Summary: findings=<count>; worst=<description|none>.` The summary count must
match the entries in that axis, and `worst=none` is reserved for an empty axis.
The preflight compares Markdown IDs with sidecar IDs per axis. Missing, extra,
duplicate, differently placed IDs, headings, prose, and untagged finding entries
block integration. New reviews must use this grammar. The only historical prose
exception is the exact completed batch9 review record for each of tasks 1131,
1132, and 1133, as selected by ready preflight
`preflight-9706b575d1279316.json` (SHA-256
`9706b575d12793163f61b5120fcd728b74ee8a2b73961fa1b341b1cc272ea8a7`). The
gate pins each task, reviewer, base, head, capture ID, Markdown, sidecar, done
record, capture manifest, and completion marker digest, and it still runs the
capture verifier and all current sidecar, axis, identity, count, and disposition
checks. These three records have complete Standards and Spec axes and no
findings. Version-1 capture manifests have no trustworthy creation timestamp,
so the gate does not infer legacy status from dates or prose shape; any other
prose-format review, including an edited or copied variant, fails closed and
needs a fresh independent review in the current format. Do not rewrite an
original review or capture to use this exception.

The preflight output is evidence of the check, not approval to integrate. It
records the target head, candidate base/head, selected review captures, and
digests of the records checked. It never updates a branch or performs a merge.

Prefer a committed candidate, since local commits are permitted. Before marking
it ready, account for task changes still staged, unstaged, or untracked. Commit
them or provide an explicit immutable snapshot; a HEAD SHA cannot identify
uncommitted work. Generated artifacts outside the candidate remain separately
identified. Use the task's actual base, not an assumed `main` or a last-turn diff.

## Close or merge

For an authorized merge into a checkout with local changes, preserve them by
merging in a clean temporary worktree and fast-forwarding the target, or by
`git stash push` (which includes the index), merging, then
`git stash pop --index`. Record the pre-merge status and verify the restored
status matches it. If a failed merge resets dirty files, recover the stash
with `git fsck --unreachable`, identify the `WIP on` commit, then run
`git stash apply --index SHA`.

## Herdr view

Use the installed reviewr contract if compatible. Its documented branch scope
includes commits plus uncommitted changes; default uncommitted scope can be empty
after the worker commits. Open one diff/file view next to the worker with the
correct worktree and base, preserving the user's focus. Verify its target and
diff coverage against the candidate record.

Discover available plugin/pane commands through current Herdr help. Prefer an
explicit target or standalone viewer process in an explicitly created pane.
An action that acts on the UI-focused workspace is unsuitable for unattended
placement unless the installed contract provides a safe explicit target. Do not
assume a Treehouse allocation fires a Herdr worktree-created plugin event.

If reviewr cannot run, expose the saved patch and files through a usable local
viewer/pager and report the missing preferred surface. Preserve the candidate
and requested decision boundary. Omitted files stay visible in the inventory;
an unavailable or partial viewer is not proof the user has inspected the code.

## Present and revise

Return a short card:

```text
Ready for your code review: <task>
Open: <workspace / review pane / fallback location>
Candidate: <base> -> <candidate>, <changed-file count>
Checks: <summary; links to evidence and omissions>
Next decision: <open PR / apply locally / another named action>
```

Let the user inspect actual code and send feedback to the worker. Record submitted
feedback and disposition durably; reviewr documents comments as in-memory until
sent/copied. Pending comments must not be silently lost during cleanup. A review
decision is keyed to the candidate and action, not a generic permission to ship.

Before executing an authorized action, verify that candidate identity and scope
still match. A changed patch returns to inspection; preserve earlier approval as
history. Run any newly necessary checks. Opening a PR and merging it have separate
authority and observable results. Verify the actual PR/local integration outcome
before claiming completion.

## Tool card

**Purpose/selection:** expose code for human inspection at the task's review
boundary; use reviewr when available, otherwise an explicit local artifact view.
**Input:** task identity, worktree, comparison base/candidate, intended pane and
inspection artifacts. **Output:** actual viewer location, target verification,
candidate record, omissions, and user decision if one was provided.
**Effects:** creates a pane/process; reviewr may write private baseline refs and
submits comments only on user action. Opening a view does not authorize publish,
merge, or cleanup. **Execution:** one viewer per candidate/worktree; bounded
startup observation. Inspect an ambiguous start before retrying to avoid duplicate
panes. **Recovery:** retain artifacts, report viewer gaps, and preserve the gate.

Reference: [reviewr](https://github.com/persiyanov/herdr-reviewr). The installed
version is authoritative for exact flags and capabilities.
