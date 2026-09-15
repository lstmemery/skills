---
name: deep-research
description: >-
  Use for a broad, multi-source or high-stakes investigation: comparisons,
  landscape scans, literature reviews, buy/build decisions, or any question
  requiring parallel evidence and an auditable report. Route a single fact,
  one source, or a “what does this document say?” lookup to `research`.
---

# Deep Research

Use this recipe when the question needs several searches, independent evidence,
and a report that another person can audit. Keep the working vocabulary small:
`scope`, `contract`, `slice`, `artifact`, `audit`, `gap`, and `stop rule`.

## 1. Scope

- Inspect the provided prior-art locations with the available filesystem/search
  tools before the first web search. Reuse relevant prior work as a
  `[PRIOR-NOTE]` delta instead of re-deriving it.
- State the audience, decision, recency window, comparison axes, and what
  “best” means when those are implicit in the request.
- Choose an execution shape and an effort budget before delegating:
  - **wide:** independent slices run concurrently;
  - **deep:** a few slices run in rounds, with a replan after each round;
  - **mixed:** enumerate candidates first, then investigate a shortlist deeply.
  Set the worker ceiling to the smaller of the independent-slice count and the
  collaboration slots available at runtime (leaving a slot for the
  orchestrator). Do not create a single-fact swarm.
- Write a plan containing the question, sub-questions, success criteria, date
  window, execution shape, effort budget, and explicit out-of-scope items.

**Done when:** the plan records implicit context, prior-art reuse or absence,
the execution shape, the effort budget, and the out-of-scope boundary.

## 2. Decompose

Create non-overlapping `slice`s. For each, record its objective, output schema,
preferred source classes, explicit boundary, owner, and acceptance condition.
Put the shared `contract` (definitions, comparison axes, date window, and
citation format) in delegation context once.

**Done when:** every requested sub-question or decision belongs to exactly one
slice, and every slice has an owner, boundary, and acceptance condition.

## 3. Gather

- For **wide**, delegate independent slices concurrently.
- For **deep**, delegate the first round, inspect artifacts, and replan the
  next round from what remains unverified.
- For **mixed**, enumerate candidates first, then run a deep pass on the
  shortlist.
- When delegating, load the worker contract in
  [REFERENCE.md](REFERENCE.md#worker-contract).
- Require each artifact to use rows of `claim | URL | exact quote or number |
  retrieval date | source role`, plus an explicit unresolved-items list. Pass
  artifact paths and structured rows; do not relay findings through prose.

**Done when:** every slice has an artifact in the agreed schema, each
acceptance condition is met or explicitly unresolved, and the `stop rule` is
recorded.

## 4. Synthesize

Read artifacts directly and build findings from their evidence. Synthesize
multiple independent sources when a claim has multiple owners. For a
single-owner fact, explain why one primary source is sufficient. Record
contradictions with both claims and URLs, then resolve them or carry them into
the gap statement.

**Done when:** every finding maps to artifact rows, every contradiction has a
disposition, and every single-source finding has a sufficiency explanation.

## 5. Audit

Load the citation-audit checklist from
[REFERENCE.md](REFERENCE.md#citation-audit). Reopen load-bearing numbers,
dates, versions, prices, and superlatives. Audit uncited load-bearing claims,
label source roles, and apply the stop rule.

**Done when:** every retained load-bearing claim has a source opened during
this run (or an explicit prior-note label), unresolved items are in the gap
list, and no contradiction remains unreviewed.

## 6. Report

Write one dated report in the runtime’s designated output directory. Include:

1. Goal, date, and method note (verified at source versus merely discovered)
2. A cited TL;DR of no more than eight bullets
3. Findings, one section per slice
4. A comparison table when the question is comparative
5. Failure modes and caveats
6. An answer or recommendation; state the tradeoff when making a decision
7. A gap statement explaining what could not be verified and why
8. Sources, with each URL annotated by support, source role, and retrieval date

**Done when:** the report stands alone, the TL;DR has no unsupported claim, and
the filename/date and required sections are present.

## 7. Hand off

Deliver only the finished report through the runtime's delivery contract. Do not
deliver plans, slice artifacts, or audit scratch work.

**Done when:** the delivery step names exactly the final report and the
release gate below passes.

## Release gate

Record pass/fail before handoff. All gates must pass: prior art checked; scope,
shape, and effort recorded; slices non-overlapping with acceptance conditions;
artifacts complete or unresolved items named; findings traceable to artifacts;
load-bearing and uncited claims audited; contradictions resolved or in gaps;
gap statement present; report schema, date, and filename valid; and handoff
points only to the finished report. A failed gate must be fixed before queuing;
document an evidence gap in the report only after the required search and audit
work has been completed.

For the worker contract, detailed audit checklist, evidence rationale, and
wide/deep/mixed guidance, use [REFERENCE.md](REFERENCE.md).
