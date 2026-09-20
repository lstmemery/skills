---
name: handoff
description: Compact a conversation into a durable, evidence-linked handoff for a fresh agent.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

# Handoff

Write the smallest complete state a fresh agent can resume, including an explicitly requested fresh session in the same directory. A directory, repository, harness, or machine move is another valid reason; migration is not required.

Use the current request and newest explicit decisions first, then durable specs, plans, ADRs, issues, commits, and diffs. The invocation argument sets the next session's focus. Link evidence instead of reproducing artifacts or conversation history. Ask only when a missing objective, destination, or safe next action cannot be resolved from available context.

## State schema

Write one Markdown file at the active runtime profile's output root. Use this schema once; omit inapplicable optional fields.

```markdown
# Handoff: <objective and next-session focus>

- destination: <workspace/repo/harness/machine, or same workspace in a fresh session>; reason=<why>
- status: <in progress|blocked|ready>; handoff_at=<UTC>
- decision: <current decision and why it governs the next step>
- constraints: <accepted constraints and assumptions>
- verified: <important facts; links/dates and what the evidence proves>
- hypotheses: <working hypotheses still being tested>
- unresolved: <blockers, evidence gaps, or decisions needed>
- artifacts: <paths/URLs needed to continue>
- freshness: <conditions that invalidate facts; versions/branches/dates to recheck>
- resume test: <first executable check>; expected observation=<...>
- next action: <one executable step after that check, or the decision needed to unblock it>
- suggested skills: <names, with their load conditions>
```

For a **provider transition or capacity-related handoff**, add a `context` field: provider, exact model ID/snapshot, endpoint, context limit, counting method and included content, estimated input, and reserved output/thinking budget. Use observed values or `unknown`; never infer limits or counts. Ordinary continuity does not require this inventory.

## Compose and release

Read only the artifacts needed to reconstruct this state. Classify important facts under verified, hypotheses, or unresolved; unavailable evidence remains explicit, not a negative finding. Keep the decision and next action easy to find without duplicating them in a second summary.

Reopen the file and check that the destination is usable, links resolve or are marked unavailable, the resume test has an observable expected result, and freshness checks cover material dependencies. Redact secrets and personal data. If resumption is blocked, name the blocking decision and the action that can obtain it; preserve the handoff as blocked rather than inventing a runnable step.

Done when another agent has the objective, authority, evidence, uncertainty, and actionable resumption state above. Return the handoff path; writing it does not authorize altering source artifacts or sending external messages.
