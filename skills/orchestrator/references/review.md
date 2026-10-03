# Human code inspection

Read before presenting repository changes, processing code feedback, opening a
PR, or integrating a local result. Preferences own the human decision boundary.

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

### Machine-readable review evidence

Each `review/` or `review-rN/` directory used for integration needs a
`review-evidence.json` sidecar. The preflight verifies it against that
directory's `review.md`, `done.json`, and completed capture manifest. The first
worker identity listed for the task in batch `workers.txt` is the candidate
author. The author and reviewer identities must both appear there, and they
must differ. Record both axes even when an axis has zero findings:

```json
{
  "schema_version": 1,
  "task_id": "1123",
  "author_identity": "worker-1123",
  "reviewer_identity": "reviewer-1123",
  "capture_id": "capture-id-from-done-and-manifest",
  "axes": {
    "standards": {
      "status": "complete",
      "findings": [
        {"id": "S1", "disposition": "unresolved", "reason": "Awaiting coordinator disposition."}
      ]
    },
    "spec": {"status": "complete", "findings": []}
  }
}
```

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
