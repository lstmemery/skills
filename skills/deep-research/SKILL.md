---
name: deep-research
description: Investigate broad comparisons, literature, landscapes, buy/build decisions, and high-stakes questions; use research for focused lookups.
---

# Deep Research

Produce a source-grounded report whose material claims can be audited.
Finish when the scoped questions are supported or explicitly unresolved,
claims pass the shared evidence check, and the finished report is delivered
through the active runtime's output and delivery contract.

Single facts and document lookups belong to `research`. Within this recipe,
execute all slices in the current thread. Workers do not spawn subagents;
any authorized delegation is a coordinator decision outside this recipe.

## Scope and decompose

Inspect supplied prior art before searching. Record audience, decision,
recency window, comparison axes, definitions of “best,” exclusions, effort
budget, and a stop rule. Prior reports remain prior-note evidence under the
shared [research evidence contract](../research/EVIDENCE.md); read it before
gathering or checking claims.

Choose the execution shape from the question:

- **Wide:** independent items or slices with comparable outputs, processed
  one at a time in the current thread.
- **Deep:** a dependent source/causal chain, investigated in rounds with a
  replan after each round.
- **Mixed:** enumerate candidates, then investigate a shortlist deeply.

For a large or difficult decomposition, read
[execution shapes](REFERENCE.md#execution-shapes). Give each slice one objective,
source classes, boundary, owner, acceptance condition, and artifact path.
Every requested part belongs to a slice; overlapping searches need an explicit
reason. Keep shared definitions, date window, and comparison axes consistent.

## Gather and synthesize

Open sources that own the claims and record rows using the shared evidence
contract. Each slice must return support or an unresolved item for every
requested part, plus contradictions and its stopping reason. Read artifacts
directly; passing their paths preserves evidence better than a prose relay.

When executing a delegated slice, also read the
[worker contract](REFERENCE.md#worker-contract). Follow its scope and stop rules;
return the slice artifact to the coordinator without publishing it.

Build findings from the captured evidence. Synthesize independent sources
where a claim has multiple owners; for a single-owner fact explain why that
source is sufficient. Preserve conflicting claims and either resolve them with
evidence or name the unresolved disagreement. Replan only around remaining gaps.

## Check and deliver

Apply the shared claim check to the whole report, including uncited prose and
summary claims. Captured, sufficiently current source text can satisfy the
check; refetch when evidence is missing, incomplete, stale, or conflicting.
Stop when the scoped coverage is met, further retrieval repeats evidence,
and remaining uncertainty is explicit. Fix or qualify unsupported wording;
a gap is acceptable only after the relevant search/check work was attempted.

Write one dated report with goal/scope/date, a brief method note, a cited
summary of at most eight bullets, findings organized by the question, a
comparison table when useful, caveats, an answer or recommendation with its
tradeoff, a gap statement, and sources annotated by support/role/retrieval date.
The report must stand alone and its summary must stay within the evidence.

Before delivery, verify scope and slice coverage, evidence links and claim-check
results, conflict dispositions, named gaps, and report date/filename. Use the
runtime's delivery contract for the finished report only; plans, slice artifacts,
and check notes are working material. Report the delivery state actually observed.

For evidence rationale and historical cost observations when maintaining this
skill, read [REFERENCE.md](REFERENCE.md#evidence-and-maintenance).
