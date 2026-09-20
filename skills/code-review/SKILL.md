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

| Mode | Change and history |
|---|---|
| Committed branch / PR | `git diff <base>...HEAD` and `git log <base>..HEAD --oneline`; base is the intended landing branch. |
| Exact commit, SHA, tag, or `HEAD~N` | `git diff <commit> HEAD` and `git log <commit>..HEAD --oneline`; compare exactly these endpoints. |
| Working tree / staged / WIP | `git diff HEAD` includes staged and unstaged work. Also list `git ls-files --others --exclude-standard` and read every untracked file in full. |

Resolve every used ref with `git rev-parse <ref>` before reviewing. Record the
mode, resolved refs, exact commands, and untracked-file list where relevant;
give both axes the same captured change. A bad ref blocks review.

An empty committed diff excludes any uncommitted work: check `git status --short`
and offer WIP mode when that is where the change lives. With no changes in the
selected range, report nothing to review. WIP is empty only when both its diff
and untracked-file list are empty.

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
Give each the captured change and its relevant authority files. Use bounded
briefs rather than the other axis's findings. Without a spec, after the explicit
missing-spec procedure, perform Standards inline; one axis needs no worker.

- **Standards:** the reviewer reads [STANDARDS.md](STANDARDS.md) and the repository
  standards. Pass the reference path when accessible, otherwise its full content.
  The coordinator need not load this reference unless performing that axis itself.
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
