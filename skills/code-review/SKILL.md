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
the repo. Both axes receive the same capture ID and read surrounding source from
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
Keep the axes separate without merging or reranking them. End with each axis's
finding count and its own worst issue, or “none”; identify any skipped or blocked
axis. Summary limits never discard findings.

Before claiming the current checkout has been reviewed, run capture `check`.
If source or authorities drifted, finish findings for the captured version and
require a fresh review for the current version. Keep Standards and Spec findings
separate, and include the capture ID, coverage gaps, and drift in the result.
