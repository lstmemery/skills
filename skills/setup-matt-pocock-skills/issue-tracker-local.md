# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files under `.scratch/`.
One feature uses one directory: `.scratch/<feature-slug>/`, with `PRD.md`,
`issues/NN-slug.md` records, a `Status:` field near the top, and comments under
`## Comments`. The leading issue number is the ticket identity. Use the shared
[local-ticket command](LOCAL-TICKETS.md) for parsing, validation, frontier
selection, claims, publication, resolution, closeout, and recovery. It is the
single authority for these mechanics; this file defines storage and domain
lifecycle only.

## Ticket storage

- Implementation issues are one file per ticket under
  `.scratch/<feature-slug>/issues/`, numbered from `01`.
- A feature's Wayfinder map is `.scratch/<effort-slug>/map.md`; its child tickets
  use the same `issues/NN-slug.md` shape and a `Type:` field.
- `publish` creates a file under the selected feature root. A referenced ticket
  path is read directly; agents do not infer it by searching arbitrary files.
- All agents working on the same feature use one canonical feature directory,
  including agents in separate code worktrees. A copied tracker has separate
  state and cannot prevent duplicate claims.

## Lifecycles

Implementation tickets **omit** `Type:`. They use the configured triage labels:
`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, or `wontfix`.
Publication sets `ready-for-agent`; verified implementation closeout sets `done`.
`wontfix` means closed without action and does not satisfy a dependent ticket.
Revise that dependency explicitly when the work should proceed another way.

Wayfinding child tickets **always** carry one of `research`, `prototype`,
`grilling`, or `task` in `Type:`. They have no `Status:` while open and
unclaimed, `claimed` while owned, and `resolved` after the answer is recorded.
Only a `done` implementation predecessor or `resolved` Wayfinding predecessor
satisfies a dependency. A cycle, self-edge, dangling ID, duplicate number, or
ambiguous field is invalid rather than an empty frontier.

The helper records `Claimed by:` and `Claim ID:` for both ticket kinds, separate
from implementation triage. Claims never expire. Explicit release or
reassignment is required; reassignment invalidates the previous claim. Active
claim history stays in `.ticket-operations/`; resolution and completion retire
active ownership while retaining that history.

## Wayfinding operations

The map is an index: its `Destination`, `Notes`, `Decisions so far`, `Not yet
specified`, and `Out of scope` sections orient the effort. Child detail lives in
the ticket. A frontier is open, unblocked, and unclaimed. Wayfinder claims a
child before work, then records an accepted answer, marks it `resolved`, and
adds a linked gist to the map. The session limit and human-in-the-loop rule stay
in `wayfinder/SKILL.md`; the helper performs only the supplied file transitions.

## Recovery and remote boundaries

Mutation previews report concrete diffs and a plan hash. Apply requires that
hash; the helper rechecks current file hashes and dependencies. Cooperative
callers use the feature directory lock. Interrupted multi-file updates can be
resumed only when current files match their recorded before/after states;
independent edits produce a conflict. Remote trackers, board moves, assignment,
comments, and commits are outside this local backend.
