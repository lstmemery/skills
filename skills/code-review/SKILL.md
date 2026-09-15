---
name: code-review
description: "Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes: Standards (does the code follow this repo's documented coding standards?) and Spec (does the code match what the originating issue/spec asked for?). Runs both reviews in parallel sub-agents and reports them side by side. Use when the user wants to review a branch, a PR, work-in-progress changes, or asks to \"review since X\"."
---

Two-axis review of a change the user points at — a branch, a specific earlier commit, or uncommitted work:

- **Standards**: does the code conform to this repo's documented coding standards?
- **Spec**: does the code faithfully implement the originating issue / spec?

The axes stay separate so they don't pollute each other's context: when both apply they run as **parallel sub-agents** and this skill aggregates their findings; when only one applies, it runs inline (step 4).

The issue tracker should have been provided to you. If `docs/agents/issue-tracker.md` is missing, tell the user to run `/setup-matt-pocock-skills`.

## Process

### 1. Pick the review mode

Three review modes exist; they use different diffs and answer different requests. Pick the one matching what the user asked for. If they didn't say what to review, or the request fits more than one mode (e.g. "review since `main`" could mean the branch or the working tree), ask.

- **(a) Committed branch** — "review my branch", "review this PR". `git diff <base>...HEAD` (three-dot, so the comparison is against the merge-base) plus `git log <base>..HEAD --oneline`. `<base>` is the branch this one is meant to land on, usually the mainline. Three-dot is deliberate: unrelated movement on the base does not leak into the review.
- **(b) Exact fixed commit** — "review since commit X", a SHA, tag, or `HEAD~5`. `git diff <commit> HEAD` (two-dot: exactly that commit against `HEAD`, no merge-base) plus `git log <commit>..HEAD --oneline`.
- **(c) Working tree (work-in-progress)** — "review my WIP", "review what I've staged". `git diff HEAD`, which shows staged and unstaged changes together in one diff. Untracked files are **in scope**: list them with `git ls-files --others --exclude-standard` and read each in full — no diff can show them, and new files are often most of a WIP change.

Before going further, confirm every ref the mode uses resolves (`git rev-parse <ref>`), capture the mode's exact commands once (both axes must review the same change), and check the diff is non-empty **for the selected mode**:

- Modes (a)/(b): an empty diff is not automatically an error. If `git status --short` shows uncommitted work, that work simply isn't in the range — say so and offer mode (c). If the tree is clean too, there is nothing to review.
- Mode (c): empty means no uncommitted work and no untracked files — tell the user; there is nothing to review.

A bad ref always fails here, not partway through the review.

### 2. Identify the spec source

Look for the originating spec, in this order:

1. Issue references in the commit messages (`#123`, `Closes #45`, GitLab `!67`, etc.), fetched via the workflow in `docs/agents/issue-tracker.md`.
2. A path the user passed as an argument.
3. A spec file under `docs/`, `specs/`, or `.scratch/` matching the branch name or feature.
4. If nothing is found, ask the user where the spec is. If they say there isn't one, the **Spec** axis is skipped and the final report notes it; step 4 then runs a single axis inline.

### 3. Identify the standards sources

Anything in the repo that documents how code should be written, such as `CODING_STANDARDS.md` or `CONTRIBUTING.md`.

On top of whatever the repo documents, the Standards axis always carries the **smell baseline** below: a fixed set of Fowler code smells (_Refactoring_, ch.3) that applies even when a repo documents nothing. Two rules bind it:

- **The repo overrides.** A documented repo standard always wins; where it endorses something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible Feature Envy"), never a hard violation. Like any standard here, skip anything tooling already enforces.

Each smell reads *what it is* → *how to fix*; match it against the diff:

- **Mysterious Name**: a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no honest name comes, the design's murky.
- **Duplicated Code**: the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
- **Feature Envy**: a method that reaches into another object's data more than its own. → move the method onto the data it envies.
- **Data Clumps**: the same few fields or params keep travelling together (a type wanting to be born). → bundle them into one type, pass that.
- **Primitive Obsession**: a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
- **Repeated Switches**: the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
- **Shotgun Surgery**: one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
- **Divergent Change**: one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
- **Speculative Generality**: abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
- **Message Chains**: long `a.b().c().d()` navigation the caller shouldn't depend on. → hide the walk behind one method on the first object.
- **Middle Man**: a class or function that mostly just delegates onward. → cut it, call the real target direct.
- **Refused Bequest**: a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.

### 4. Run the axes (parallel when there are two)

If both axes apply — step 2 found a spec — spawn both sub-agents in parallel with the prompts below. If only Standards applies, run the Standards brief **inline** in this conversation; a single axis does not justify a sub-agent.

**Standards sub-agent prompt** should include:

- The review inputs for the selected mode: its exact diff command(s) and commit list for modes (a)/(b); the diff command and untracked-file list for mode (c).
- The list of standards-source files you found in step 3, **plus the smell baseline from step 3** pasted in full (a sub-agent has no other access to it).
- The brief: "Report, per file/hunk where relevant, (a) every place the diff violates a documented standard: cite the standard (file + the rule); and (b) any baseline smell you spot: name it and quote the hunk. Distinguish hard violations from judgement calls: documented-standard breaches can be hard, but baseline smells are always judgement calls, and a documented repo standard overrides the baseline. Skip anything tooling enforces. The finding list has no length limit — never drop a finding to stay brief. If the complete list fits in under ~400 words, return it whole; otherwise write it in full to a file under the workspace root (e.g. `code-review-standards-findings.md`) and return the file's path plus a summary of at most 200 words."

**Spec sub-agent prompt** should include:

- The review inputs for the selected mode (as above).
- The path or fetched contents of the spec.
- The brief: "Report: (a) requirements the spec asked for that are missing or partial; (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that look implemented but where the implementation looks wrong. Quote the spec line for each finding. The finding list has no length limit — never drop a finding to stay brief. If the complete list fits in under ~400 words, return it whole; otherwise write it in full to a file under the workspace root (e.g. `code-review-spec-findings.md`) and return the file's path plus a summary of at most 200 words."

When the single Standards axis runs inline, follow its brief yourself; the overflow rule applies unchanged.

### 5. Aggregate

Present each axis's report under its `## Standards` / `## Spec` heading, verbatim or lightly cleaned. Do **not** merge or rerank findings, because the two axes are deliberately separate (see _Why two axes_).

When an axis overflowed to a findings file, present its bounded summary and link the file's path under the workspace root — the file carries the complete finding list. The cap binds the summary only; no finding is ever dropped to fit a word budget.

End with a one-line summary: total findings per axis, and the worst issue _within each axis_ (if any). Don't pick a single winner across axes: that's the reranking the separation exists to prevent.

## Why two axes

A change can pass one axis and fail the other:

- Code that follows every standard but implements the wrong thing → **Standards pass, Spec fail.**
- Code that does exactly what the issue asked but breaks the project's conventions → **Spec pass, Standards fail.**

Reporting them separately stops one axis from masking the other.
