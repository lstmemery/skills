---
name: code-review
description: Review branches, PRs, fixed commits, or working changes against repository standards and the originating spec.
---

# Code Review

Report two independent axes: **Standards** checks documented conventions and a
smell baseline; **Spec** checks the originating requirements. Review reports
findings; it does not apply them. Completion means every applicable axis has
reported its full findings or none, with skipped axes and blockers explicit.

## 1. Fix the review scope

Choose the mode matching the request. Ask if the target is absent or ambiguous
(for example, “since main” might mean a branch or working changes).

Use [CAPTURE.md](CAPTURE.md) and `scripts/capture.py` for the selected branch,
exact-base-to-HEAD, or WIP mode. Preview names refs and paths; capture pins them
and includes regular non-ignored untracked files in WIP. Save the capture outside
the repo. Captures refuse to write payloads that match secret rules (see
CAPTURE.md); fix a refusal by removing the value or replacing it with a fake,
then recapture. Both axes receive the same capture ID and read surrounding source from
its saved inventories. Review judgment stays with the reviewers.

Find the authorities below before final capture so their versions can be included
with `--authority`. Missing/unsupported content remains visible. Review available
material with partial findings; full-scope review stays incomplete until gaps are
covered or scope is explicitly changed. Preserve the explicit missing-spec process.

An empty committed capture excludes uncommitted work: use its worktree notice to
offer WIP mode when appropriate. An empty WIP capture has neither net changes nor
untracked work. A bad ref blocks capture; report nothing to review only for a
valid empty selected scope.

## 2. Find the authorities

Find the originating spec in this order:

1. Commit issue references, fetched using `docs/agents/issue-tracker.md`.
2. A path supplied by the user.
3. Matching files under `docs/`, `specs/`, or `.scratch/`.
4. Ask where the spec is. Only when the user says there is none, skip Spec and
   state that in the report. An unanswered question is not a skipped axis.

If the tracker instructions are missing, tell the user to run
`/setup-matt-pocock-skills`; continue independent source discovery.
Find repository standards such as `CODING_STANDARDS.md` and `CONTRIBUTING.md`.
Standards always applies, including when no such documents exist.

## 3. Run the axes

With a spec, run **two parallel sub-agents with separate review contexts**.
Give each the same verified capture and its relevant captured authority files. Use bounded
briefs rather than the other axis's findings. Without a spec, after the explicit
missing-spec procedure, perform Standards inline; one axis needs no worker.

- **Standards:** the reviewer reads [STANDARDS.md](STANDARDS.md) and the repository
  standards. Pass the reference path when accessible, otherwise its full content.
  The coordinator need not load this reference unless performing that axis itself.
  When the change is Python and skill `python-expert-best-practices-code-review`
  is installed, this axis also applies its rules; where a rule restates a
  tooling check, the tooling result decides and the rule is not re-reported.
- **Spec:** give only the review inputs and spec for this brief: “Report missing
  or partial requirements, unrequested behavior, and requirements implemented
  incorrectly. Quote the spec support for each finding and locate the file/hunk.”

For either axis, retain the complete finding list. If it fits under about 400
words, return it whole. Otherwise save it under the active profile's output root
and return its path plus a summary of at most 200 words. Request a missing result
or report its blocker; worker lifecycle state does not substitute for findings.

## 4. Present

Use separate `## Standards` and `## Spec` headings. Preserve each axis's findings
verbatim or lightly cleaned; link any complete findings file beside its summary.
An optional single `#` title may precede them; do not add other section headings
or prose outside the two axis sections.
For new reviews used by integration preflight, every nonblank line in either
axis section must use one of these exact forms:

- `- [S1] <finding>` for a finding, with its stable ID also used in
  `review-evidence.json`;
- `No findings.` for an empty axis;
- `Summary: findings=<count>; worst=<description|none>.` as the final line,
  with the count matching the findings. Use `worst=none` only for an empty axis.

Do not put prose, headings, or untagged list entries inside an axis section.
For example:

```markdown
## Standards
- [S1] **MAJOR · CONFIRMED** — Missing validation for captured payloads.
Summary: findings=1; worst=Missing payload verification.

## Spec
No findings.
Summary: findings=0; worst=none.
```

Keep the axes separate without merging or reranking them. The final Summary line
in each section gives its finding count and worst issue. Identify any skipped or
blocked axis outside those sections. Summary limits never discard findings.

Do not produce the older prose format. The preflight's bounded historical
exception applies only to the exact, previously-ready batch9 review records for
tasks 1131, 1132, and 1133; it pins their complete evidence and re-verifies each
capture. Version-1 captures have no trustworthy creation timestamp, so no other
prose review is treated as historical. Do not edit or recreate those records to
use the exception.

Before claiming the current checkout has been reviewed, run capture `check`.
If source or authorities drifted, finish findings for the captured version and
require a fresh review for the current version. Keep Standards and Spec findings
separate, and include the capture ID, coverage gaps, and drift in the result.
