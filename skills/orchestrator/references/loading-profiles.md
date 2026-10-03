# Task-scoped loading profiles

A task-scoped loading profile is a generated, per-task copy of tracked
instruction documents with named anchor sections omitted. It reduces what one
task loads; it never changes a source document and never removes a
requirement. Night decision N09 adopted exactly one profile, **task-a**
(bundle A), for tasks where all three of its anchors are genuinely irrelevant.
No other profile exists.

## Anchors and when omission applies

Selection is explicit in `scripts/loading-profile.py` (`python3
scripts/loading-profile.py list` prints it). Each anchor may be omitted only
under its stated condition, which the task's declaration must attest:

| Anchor | Source | Omit only when |
|---|---|---|
| `workers.assignment` | [workers.md](workers.md) `## Assignment` | the task will not prepare assignments or briefs for further workers (no sub-delegation) |
| `workers.terminal-disposition-closeout` | [workers.md](workers.md) `## Terminal disposition and closeout` | the worker will not record dispositions or run closeout checks (coordinator-owned; the worker's own result-record contract still applies) |
| `tdd.test-quality` | the `tdd` skill's `SKILL.md` `## Test quality` | the task writes no tests and runs no TDD cycles; any test work loads the full `tdd` skill |

## Never suppress mandatory requirements

Omission from the loaded copy is not exemption. The governing contracts keep
their force: assignment and admission still come from the coordinator's brief
and the launch policy, the [worker contract](workers.md) result-record schema
and its validator still bind every return, the coordinator still owns
dispositions and closeout, and testing still follows the task's repository
policy and the `tdd` skill. If an omitted anchor becomes relevant mid-task,
load the full source file and continue under it.

## Generate and verify

Write a declaration naming the task, the profile, and a concrete reason plus
required fact per anchor; generate the profile into the task's output folder;
verify the artifact against the sources:

```sh
python3 scripts/loading-profile.py generate --profile task-a \
  --declaration DECLARATION.json --out "$TASK_DIR/loading-profile-a.md"
python3 scripts/loading-profile.py verify --profile task-a \
  --declaration DECLARATION.json --candidate "$TASK_DIR/loading-profile-a.md"
```

The declaration is a JSON object: `schema_version: 1`, `task_id`, `profile`,
and `applicability` naming exactly the profile's anchors. Each anchor entry
needs a non-empty `reason` plus the anchor's required fact —
`subdelegation: "none"`, `disposition: "coordinator-owned"`, or
`tests: "none"` respectively. Unknown anchors, missing facts, or empty
reasons are rejected with a nonzero status. A reason must also be a single
printable line without `-->`: it is rendered inside the artifact header's HTML
comment, so it must not be able to terminate or extend that comment. Any
character `str.splitlines()` would split a line on — including carriage
return, vertical tab, form feed, NEL (U+0085), and U+2028/U+2029 — and any
other non-printable character is refused.

The generator is deterministic: the same sources and declaration produce
byte-identical output, recorded with source and declaration SHA-256 digests in
the artifact header. Sources are read, never modified: `generate --out`
refuses output paths that resolve inside this repository, including through
symlinks, so a mistyped output path cannot overwrite a tracked source.
`verify` regenerates
and byte-compares, so an edited or stale profile fails loudly. Keep the
declaration beside the generated profile as part of the task's evidence.

## Evidence context (report honestly)

In a 210-cell evaluation of bundle A, the reduced profile cut loaded
instruction tokens by about 13% and total run context by about 4.5–5.1%. No
latency improvement was demonstrated. Profile A is a context-cost reduction
for genuinely irrelevant anchors; nothing more should be claimed for it.
