---
name: setup-matt-pocock-skills
description: Configure a repo's issue tracker, triage vocabulary, and domain-document layout for the engineering skills.
disable-model-invocation: true
---

# Setup Avery Pocock's Skills

Create or update the repo configuration consumed by the engineering skills: the issue tracker, five triage-role labels, and domain-doc layout/reading rules. Explore first, batch independent choices, then show a concrete configuration for review before applying it.

## Explore and select the instruction file

Inspect existing remote/repo configuration, root `AGENTS.md` and `CLAUDE.md` (including any `## Agent skills` block), `CONTEXT.md`, `CONTEXT-MAP.md`, root/context ADR directories, `docs/agents/`, and `.scratch/` conventions. Check monorepo signals such as `pnpm-workspace.yaml`, package workspaces, or populated packages with their own source.

**File selection:** edit `CLAUDE.md` if it exists; otherwise edit existing `AGENTS.md`. If neither exists, include which to create among the unresolved choices. When both exist, use `CLAUDE.md`; do not create a second file or overwrite surrounding user content.

## Settle only open choices

Present findings and recommendations together so independent decisions can be answered in one reply. Reuse already established preferences. Follow up only on choices whose answers depend on that reply.

- **Tracker:** propose GitHub for a GitHub remote, GitLab for a GitLab remote, or local Markdown when appropriate. For another tracker, obtain the user's workflow in a short paragraph. This selects `docs/agents/issue-tracker.md`.
- **Triage vocabulary (always included):** recommend keeping `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. If no accepted vocabulary exists, ask whether to keep these defaults as part of the same batch. Collect overrides only when the user wants different strings; reuse existing tracker labels rather than creating duplicates.
- **Domain layout:** use single-context (root `CONTEXT.md` and `docs/adr/`) without asking when exploration settles it. Offer multi-context, with root `CONTEXT-MAP.md` and per-context documents, only when monorepo signals justify the choice.
- **Instruction file:** ask which file to create only when neither candidate exists.

## Draft, review, and apply

Load only the selected tracker seed: [GitHub](issue-tracker-github.md), [GitLab](issue-tracker-gitlab.md), or [local Markdown](issue-tracker-local.md). For another tracker, draft from the user's workflow. Use [triage-labels.md](triage-labels.md) and [domain.md](domain.md) for the always-required vocabulary and domain rules.

Show the exact proposed contents of `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, `docs/agents/domain.md`, and the instruction-file block below. Let the user correct the concrete draft before writing; apply existing authorization and do not repeat settled questions.

```markdown
## Agent skills

### Issue tracker

<One-line tracker summary>. See `docs/agents/issue-tracker.md`.

### Triage labels

<One-line vocabulary summary>. See `docs/agents/triage-labels.md`.

### Domain docs

<Single-context or multi-context summary>. See `docs/agents/domain.md`.
```

Update an existing block in place, preserving surrounding sections. Always write the triage doc and its sub-block. The domain configuration describes where future records belong; it does not require creating empty domain records.

Done when the chosen instruction file and all three configuration docs agree with the reviewed choices, pointers resolve, and no duplicate block exists. Report the files and which engineering skills consume them. They can be edited directly; rerunning is useful for changing trackers or restarting configuration.

When **adding or evaluating a candidate skill for the suite**, read [VETTING.md](VETTING.md). Routine repo setup does not load that branch.

## Local ticket operations

For local Markdown tracker mechanics, use [LOCAL-TICKETS.md](LOCAL-TICKETS.md).
It defines the shared command used by ticket publication, Wayfinder, and
implementation closeout. It does not automate the setup choices above.
