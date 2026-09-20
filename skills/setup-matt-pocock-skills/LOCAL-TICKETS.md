# Local ticket command

Use [scripts/local-tickets.py](scripts/local-tickets.py) when the configured
tracker uses this suite's local Markdown format. Run it with Python 3 on a
POSIX filesystem supporting directory `flock` and atomic file replacement.
All agents working on one feature use the **same canonical feature directory**,
even when their code edits live in different worktrees. Separate copied trackers
have separate claims. The feature directory must already exist.

```sh
python3 <skill-dir>/scripts/local-tickets.py frontier --feature-root <feature>
python3 <skill-dir>/scripts/local-tickets.py claim --feature-root <feature> --request claim.json
python3 <skill-dir>/scripts/local-tickets.py claim --feature-root <feature> --request claim.json --expect-plan <returned-plan-hash> --apply
```

`validate` and `frontier` read only. Mutations preview by default, including
concrete diffs and a plan hash; `--apply --expect-plan HASH` executes that exact
preview within existing authorization. Applying without a preview hash is rejected.
`--expect-plan` detects changes since a reviewed preview. All output is JSON.
Invalid graph/input exits 2, conflict 3, unfinished operation 4, unavailable I/O 5.
A valid empty frontier exits 0 with reasons per ticket; it does not imply that
all work is done or that the remaining tickets are substantively ready.

## Requests

Pass multiline content in a JSON file. Every mutation requires a stable,
caller-chosen `operation_id` (letters/digits/underscore/hyphen, at most 80
characters). Repeat the same identity and content to continue/retrieve that
operation; changed content under it conflicts. A different identity means a
new operation. Request fields are exact; unknown fields are rejected.

| Operation | Fields beyond `operation_id` |
| --- | --- |
| `publish` | `approval`: accepted-draft reference/text; `tickets`: list of `{filename, body}` |
| `claim` | `ticket`: integer; `owner`: stable session/agent identity |
| `release` | `ticket`, current `owner`, current `claim_id`, explicit `reason` |
| `reassign` | Same as release, plus `new_owner` |
| `resolve` | `ticket`, `owner`, `claim_id`, accepted `answer`, one-line `gist`, `evidence` |
| `complete` | `ticket`, `owner`, `claim_id`, `evidence` |

A claim request:

```json
{"operation_id":"claim-ticket-01-session-a","ticket":1,"owner":"session-a"}
```

An implementation completion request:

```json
{
  "operation_id": "finish-ticket-01-session-a",
  "ticket": 1,
  "owner": "session-a",
  "claim_id": "<claim_id returned by claim>",
  "evidence": {
    "ticket": 1,
    "accepted": true,
    "summary": "Requested behavior verified; review findings resolved.",
    "checks": ["tests/test_feature.py passed; full verification record: verification.md"]
  }
}
```

`evidence` identifies the same ticket, includes `accepted: true` and a nonempty
`summary`; completion also needs nonempty `checks`. The caller supplies the
actual substantive acceptance and verification. The command validates and saves
that statement; it does not verify its truth or authorize its own acceptance.
Human decision tickets still require the live human exchange in Wayfinder.

## Records and lifecycles

Ticket files are `issues/NN-slug.md`; their leading number is their identity.
Plain and bold metadata fields are supported before the first `##` section;
examples inside fenced code are ignored. Duplicate fields/IDs and unknown
lifecycles are errors. `Blocked by` accepts comma-separated numbers, semicolon-
separated `NN: Title` references, or `None (can start immediately).` Missing
`Blocked by` means no declared dependencies. IDs are scoped to this feature.

Implementation tickets omit `Type:` and use triage roles plus `done`.
Wayfinding tickets use `research`, `prototype`, `grilling`, or `task` and have
no status while open, `claimed` while owned, and `resolved` when answered.
The agent frontier contains eligible unclaimed tickets in numeric order.
A predecessor satisfies a dependency only at `done` or `resolved` for its kind.
`wontfix` never unblocks a dependent; explicitly revise the dependency.

Claims add `Claimed by:` and `Claim ID:` to either kind. Implementation triage
status is preserved. Claims have no expiry. Release/reassignment is explicit;
old claim IDs cannot complete work after reassignment. Completion/resolution
removes the active claim while retaining history in operation records. A legacy
`Status: claimed` without ownership remains unresolved; deliberately reconcile
its ownership before using it. Reassignment does not stop an old agent process.

For custom implementation labels, pass `--labels FILE` on every inspection and
new mutation: a JSON object mapping all six canonical roles (`needs-triage`,
`needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`, `done`) to distinct
configured strings. Do not infer a mapping from arbitrary prose. `resume` uses
the recorded operation's mapping.

## Recovery

The helper serializes cooperative callers with a lock on the feature directory.
It records intended changes under `.ticket-operations/` before applying them.
Each file replacement is atomic; the ticket-plus-map update is recoverable,
not atomically visible to external editors. Readers report pending operations
instead of treating their mixed state as a settled graph. Keep operation records
with the canonical tracker; do not edit/delete them to bypass a conflict.

```sh
python3 <skill-dir>/scripts/local-tickets.py resume --feature-root <feature> --operation-id <id>
python3 <skill-dir>/scripts/local-tickets.py resume --feature-root <feature> --operation-id <id> --apply
```

Resume completes recorded effects only when all inputs still match their
recorded before/after states. Independent edits or ownership changes conflict;
retain those edits and reconcile deliberately. No blind retry or automatic
rollback is performed. An `already_applied` response is historical evidence of
that operation, not proof that its claim is still current; use `frontier` or
`validate` for current ownership.

Publication accepts approved bodies and writes blockers first. New implementation
tickets must be `ready-for-agent`; new wayfinding tickets must be open. Resolution
updates the answer and `map.md`'s single `## Decisions so far` heading. Unrelated
prose is retained. The helper never changes remote boards, commits, starts agents,
or decides that a dependency is genuine.

Bounds: 512 feature files, 2 MiB per file, 32 MiB per snapshot, 16 MiB per request.
Symlinks in managed paths and unsupported formats are explicit failures. Stop
using the helper for an unsupported format and report the specific gap; do not
silently bypass its ownership/recovery contract with ad hoc writes.
