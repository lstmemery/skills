---
name: handoff
description: Compact the current conversation into a durable, evidence-linked handoff another agent can resume.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

# Handoff

## Contract

**Job.** Give a fresh agent the smallest complete state needed to continue work across a directory, repository, harness, or machine transition. The handoff is state, not a transcript.

**Inputs and authority.** Use the current user request and conversation first, then durable workspace artifacts (specs, plans, ADRs, issues, commits, and diffs). Treat the user's argument as the next session's focus. When sources conflict, prefer the newest explicit decision or ask; never replace a source with model memory. Link artifacts instead of copying them.

**Success.** The handoff is complete when:

- the transition is feasible and its destination and reason are named;
- objective, status, constraints, assumptions, decision, and next action are explicit;
- every important fact is marked `verified`, `working hypothesis`, or `unresolved`, with evidence links and dates where applicable;
- provider, exact model ID or snapshot, endpoint, handoff time, context limit, counting method, estimated input, and reserved output/thinking budget are recorded, or explicitly `unknown`;
- one executable resume test names its expected observation;
- freshness or invalidation conditions are named;
- suggested skills are listed;
- the file is at the profile's workspace-root output path, links resolve or are marked unavailable, and secrets and personal data are redacted.

**Output.** Write one Markdown file at the workspace root (the profile's output root), using this smallest complete shape:

```markdown
# Handoff: <objective>

- transition: <destination>; reason=<why>
- status: <in progress|blocked|ready>; handoff_at: <UTC>
- context: provider=<...>; model=<ID or snapshot>; endpoint=<...>; limit=<...>; counted_by=<...>; estimated_input=<...>; reserve=<...>
- decision: <compact decision spine>
- constraints: <accepted constraints and assumptions>
- verified: <links, dates, and what each proves>
- hypotheses: <items still being tested>
- unresolved: <evidence gaps, blockers, or decisions needed>
- artifacts: <paths or URLs; do not inline their contents>
- freshness: <aliases, API versions, branches, dates, and what to recheck>
- resume test: <first action>; expected observation=<...>
- next action: <one executable step>
- suggested skills: <names to load when relevant>

## Resume

<repeat the compact decision spine, unresolved items, and next action>
```

## Route

1. **Gate feasibility.** Decide before asking about capacity or preference. A handoff is warranted only when work must move to another directory, repository, harness, or machine. Otherwise stop and report that condition.
2. **Load state.** Read only the current task and durable artifacts needed to reconstruct the decision. Separate facts, hypotheses, blockers, and verification; omit transcript narration.
3. **Capture context.** Record provider, model snapshot, endpoint, date, and what counts (history, tools, files, images, output, thinking, or cache). Never infer limits or counts; use `unknown`. Record the next-response and reasoning reserve when known.
4. **Compose and test.** Put the decision spine at the top and in `## Resume`. Link evidence, label uncertainty, name invalidation conditions, and give a first action with an expected observation. If that action is not executable, record the blocker and ask for the missing decision.
5. **Release-check.** Confirm destination, links, redaction, unresolved items, and safe next action. The handoff is the only payload.

## File-write card

- **Purpose and selection:** write the single handoff Markdown file only after the feasibility gate passes.
- **Input/output:** input is the completed shape above; output is the file path and a readable document.
- **Effects and authority:** workspace write only; do not alter source artifacts or send external messages.
- **Validation and recovery:** reopen the file and run the release-check. If the destination is missing or a required fact cannot be safely represented, preserve the draft as incomplete, record the blocker, and ask for a destination or decision rather than guessing.

## Stop, ask, and fallback

Stop when every success predicate passes. Ask one concise question when the objective, destination, or safe next action is genuinely missing. If a source, token count, link, or verification tool is unavailable, write `evidence missing` or `unknown`, preserve the uncertainty, and schedule a recheck; absence of evidence is not a negative result. Redact API keys, passwords, tokens, and personally identifying details even when they appear in source artifacts.

## Rationale

Context limits and token accounting vary by provider and snapshot, while long inputs can lose information in the middle and increase latency. A short decision spine, durable links, explicit uncertainty, and a resume test preserve recoverable state without replaying the transcript.
