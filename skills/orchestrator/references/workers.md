# Worker contract

Read when preparing an assignment, following up, or validating a result.

## Assignment

Give the worker this compact contract, with concrete values:

```text
Task: <id and requested outcome>
Scope: <owned work, inputs, applicable instructions, dependencies>
Location: <cwd; output folder; permitted repository/worktree if any>
Authority: <allowed edits/commits; exact actions needing a user decision>
Acceptance: <observable result and task/repository verification>
Return: <result record path; full artifact/evidence paths>
Stop/ask: <missing decision or capability that prevents this task>
```

Pass the user's standing pre-PR/pre-integration decision boundary explicitly to
coding workers. Delegate a bounded execution task, not coordinator authority.
Workers may request another assignment or report a dependency; the coordinator
retains queue ownership and records any approved further delegation.

Prepare Herdr fleets as manifests and launch them through the canonical
`scripts/herdr-jobs.py` tool: `run` for the initial batch, `add` for one more
assignment in the same persistent run, `resume` to observe results and settled
panes, and `finish` to validate worker records, disposition, release leases, and
close owned workspaces. Keep coordinator task folders to briefs and manifests;
the helper owns launch state, receipts, results, and collected artifacts. Do not
copy launcher, watcher, or finish scripts into task folders.

For a read-only review of an existing candidate, share its pinned source and put
review output outside that checkout. If the reviewer needs to edit source or run
commands that change checkout files, give it separate isolation first. Preserve
the candidate being inspected by the user.

Follow the coding verification policy in preferences and any additional task or
repository requirements. Load and follow `code-review` for its exact comparison,
Standards/Spec briefs, missing-spec procedure, and findings format. Give both axes
the same candidate and sources. Route its review workers through the coordinator's
selected backend and retain their findings separately. The implementation worker
resolves accepted findings, records reasons for rejected ones, reruns affected
checks, and refreshes review evidence for changed code before presentation.

`implement` retains its workflow when the user invokes it. Apply any stricter
repository verification in addition to the selected review policy.

## Result record

Before launch, register every worker in the run directory's `workers.json` and
give it an output directory under that run. A logical task may have several
launched attempts. Give each launch a unique worker `task_id` (for example,
`audit-42-r1-a1`, `audit-42-r1-a2`), keep the logical task and assignment
revision in `STATE.md`, and append one roster entry for each attempt. A retry
keeps the same assignment revision but gets a new attempt ID and a fresh, empty
directory; never change an earlier roster entry or reuse its directory. Record
an append-only attempt entry in `STATE.md` with the attempt/worker ID, revision,
UTC launch time, directory, disposition, and continuation count, and identify
the current attempt there. The `task_id` in `result.json` is that attempt's
worker ID; validate it against the corresponding roster entry so an earlier
attempt cannot satisfy a later one. A same-worker continuation remains within
the same attempt and directory and does not add a roster entry.

Accept only result and artifact files inside the attempt directory. Require
`result.json` and worker-created outputs to be written after launch; the empty
directory and attempt-specific identity are the primary stale-file boundary, so
mtime alone is not proof of provenance. A deliberately copied evidence file may
retain its source mtime only when the worker identifies the source path and
SHA-256 in its report, and the coordinator verifies that source and records the
hash in disposition evidence. Treat any other pre-launch file as stale. Workers
write a small `result.json` in their task output folder and return its path.
Large findings and logs remain separate artifacts. The roster records expected
attempts, and closeout also scans the run tree for worker-shaped directories
containing `brief.md`, `result.json`, or `disposition.json`; an unrostered match
blocks closeout.

```json
{
  "workers": [
    {
      "task_id": "worker-id",
      "assignment_revision": 1,
      "directory": "tasks/worker-id"
    }
  ]
}
```

`directory` is relative to the run directory and must stay inside it. Keep one
entry per launched worker, with a unique task ID and output directory. A run that
launched no workers may omit the roster or use an empty `workers` array. An
absent roster behaves as empty; discovered worker-shaped directories still block
closeout if they are not registered.

Every worker brief and per-run CONTRACT must include the current result contract
verbatim. Generate it from the shared validator in the orchestrator skill
directory:

```sh
python3 scripts/worker-records.py schema --format md
```

Copy the complete Markdown output without rewriting its example, fields, or
allowed values. This output is the result-record schema source for workers and
coordinators; do not maintain a second prose or JSON copy in a task template.
Artifact and evidence paths must also be accessible in the active profile; the
coordinator checks that separately from structural validation.

Validate each return before accepting it, passing the expected task identity:

Run these commands from the orchestrator skill directory, following its
established relative script path convention:

```sh
python3 scripts/worker-records.py validate-result \
  "$WORKER_DIR/result.json" --task-id "$TASK_ID" --revision "$REVISION"
```

Add `--repository-changes` for a repository assignment. The validator returns a
clear error and nonzero status for a missing, malformed, or non-schema result.

For a known benign legacy shape, preserve the worker's original file and
normalize to a separate file. The command validates the normalized copy before
writing it and prints a JSON audit of the changes to standard output:

```sh
python3 scripts/worker-records.py normalize-result \
  "$WORKER_DIR/result.json" --output "$WORKER_DIR/result.normalized.json" \
  --task-id "$TASK_ID" --revision "$REVISION" [--repository-changes]
```

Normalization handles only `candidate: null` on non-repository work, the
`complete`/`completed` outcome synonyms, bare-string checks (marked `not_run`
because they have no evidence), and artifact objects missing `kind` (filled as
`artifact`). All other defects remain validation errors. Validate the normalized
output with `validate-result` before accepting it.

## Terminal disposition and closeout

The coordinator, not the worker, writes `disposition.json` in each worker's
output directory. Use one terminal value: `completed`, `blocked`, `failed`, or
`cancelled`. Map a `ready` result to `completed`; preserve `blocked` and `failed`
as-is. An explicit stop, cancellation, or superseded worker is `cancelled`, even
when the worker produced no `result.json`. Include a short summary and evidence
of the observed outcome or stop.

```sh
python3 scripts/worker-records.py record-disposition \
  "$WORKER_DIR" --task-id "$TASK_ID" --revision "$REVISION" \
  --status cancelled --summary "Stopped after user cancellation." \
  --evidence "Coordinator observed the stop; no result.json was returned."
```

The record contains `task_id`, `assignment_revision`, `disposition`, `summary`,
and `evidence`. It is written atomically. Existing records are preserved unless
the coordinator explicitly passes `--replace`.

Before closing the run, check every entry in `workers.json`:

```sh
python3 scripts/worker-records.py check-closeout "$RUN_DIR"
```

The check verifies each record's schema and task/revision identity, and compares
the roster with the discovered worker-shaped directories. A missing, malformed,
or mismatched disposition or an unrostered worker makes closeout fail with the
affected directory named; keep the run open until it is reconciled. If neither a
roster nor worker-shaped directories exist, closeout succeeds with a count of 0.
Discovery skips a subtree only when the directory itself carries copy evidence:
a `.git` checkout or linked-worktree marker, or a code-review capture
(`manifest.json` with `COMPLETE`). Names alone never exclude a directory, so an
unrostered worker named like a copy is still reported. An evidence-carrying
directory that also holds worker markers directly (`brief.md`, `result.json`,
or `disposition.json` at its own root) is itself a live worker's task
directory, not a copy, and is reported like any other unrostered worker;
markers nested below an evidence-carrying root stay excluded, and rostered
workers are always validated from the roster regardless of discovery.

Repository changes also require the candidate record in
[review.md](review.md). Use paths for the complete patch, file inventory,
review findings, and check evidence; do not place the patch in this JSON.

## Validate and recover

A worker has finished only when its result record, its terminal output, and
the exit of every process it started agree. If a worker ends its turn while a
background process it started is still running, the task stays `running`:
supervise that process to exit, then grade the result it produced. Briefs for
unsupervised or headless workers say to run commands in the foreground and
write the result record last, before the final message. Track child processes
only through a handle the selected backend exposes (such as the jailed-worker
process check in [runtime operations](runtime.md#herdr-launch)); if child exit
cannot be observed, keep the task `running` or `blocked` rather than `done`.

The coordinator checks task/revision identity, required fields, artifact access,
and that the result accounts for the acceptance criteria. Delegate a substantive
quality check when the task calls for one. A worker's assertion is not a substitute
for the recorded test, diff, report, or other task-specific evidence.

Treat a text-only stop with open acceptance items and no stated blocker as
missing evidence. Continue the same worker through its existing session or
handle, naming the open items; a request to correct missing fields or evidence
uses this same continuation rule. A continuation reuses the current attempt and
worker ID; it is not a new launch, attempt, or blind provider retry. Allow at
most two automatic continuations per task total and record each against the
current attempt in `STATE.md`. Never continue past a refusal, required approval,
or capability gap; keep the task blocked with the reason. After the second
continuation, if any acceptance item remains open, keep it incomplete/blocked
with the reason and do not report it complete. If the existing worker cannot
accept a continuation through its known handle, reconcile its state; do not
launch another worker under this recovery rule. After an ambiguous
prompt/launch timeout, observe the existing worker before any separately
authorized retry. Resume the same worker when it is still the right owner.

Changed user instructions increment the assignment revision. Results from an
earlier revision remain evidence about earlier work, not completion of the new
assignment. User feedback sent directly through the review pane is captured by
the worker in its next result and reconciled before the coordinator acts on it.
