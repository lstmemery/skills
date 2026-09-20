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

Workers write a small `result.json` in their task output folder and return its
path. Large findings and logs remain separate artifacts.

```json
{
  "task_id": "task-id",
  "assignment_revision": 1,
  "outcome": "ready",
  "summary": "What was produced and what it establishes.",
  "artifacts": [{"path": "artifact-path", "kind": "report"}],
  "checks": [{"name": "check", "status": "passed", "evidence": "log-path"}],
  "unresolved": [],
  "next_action": "Present the artifact to the user."
}
```

Required fields are shown. `assignment_revision` is a positive integer matching
the brief. `outcome` is `ready`, `blocked`, or `failed`; checks use `passed`,
`failed`, or `not_run`. Artifact/evidence locations must be accessible in the
active profile. A ready result needs an artifact. `blocked` and `failed` require
an explanatory unresolved item. Record missing evidence explicitly.

Repository changes also require the candidate record in
[review.md](review.md). Use paths for the complete patch, file inventory,
review findings, and check evidence; do not place the patch in this JSON.

## Validate and recover

The coordinator checks task/revision identity, required fields, artifact access,
and that the result accounts for the acceptance criteria. Delegate a substantive
quality check when the task calls for one. A worker's assertion is not a substitute
for the recorded test, diff, report, or other task-specific evidence.

Request a corrected result once when fields or evidence are missing. If still
incomplete, keep the task blocked or assign bounded recovery work; do not report
it complete. After an ambiguous prompt/launch timeout, observe the existing
worker before retrying. Resume the same worker when it is still the right owner.

Changed user instructions increment the assignment revision. Results from an
earlier revision remain evidence about earlier work, not completion of the new
assignment. User feedback sent directly through the review pane is captured by
the worker in its next result and reconciled before the coordinator acts on it.
