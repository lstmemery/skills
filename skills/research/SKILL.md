---
name: research
description: Answer focused questions from documents, APIs, or a few sources; route broad comparisons and high-stakes investigations to deep-research.
---

# Research

Answer the user's focused question from opened sources. Finish when each material
claim matches its evidence, contradictions and missing evidence are explicit,
and the active runtime's output and delivery contract is satisfied.

Use the request first, then relevant supplied prior art and sources that own
the claims: official documentation, code, specifications, APIs, or original
papers. Search snippets and model memory are leads. A prior report is a
`[PRIOR-NOTE]`, not fresh verification; reopen material or changeable claims.

## Choose the path

- **Single lookup:** one fact or owner document. Inspect relevant supplied
  context, open the source, answer from the supporting passage, cite it, and
  state material limitations. Compare the final wording, units, qualifiers,
  and date with that passage before returning. Keep the evidence with the
  answer; no separate plan, evidence table, or audit artifact is required.
- **Focused investigation:** several disputed claims, a few sources, or a short
  causal chain. Follow the investigation below.
- **Broad or high-stakes investigation:** load and follow skill `deep-research`
  before gathering sources. Scope, rather than a fixed source count, decides.

A required saved report may be the same concise answer as a single lookup.
Use the runtime's output root and delivery mechanism. Queue only a finished
report when that mechanism exists; confirm only the delivery state observed.

## Focused investigation

1. **Scope.** Inspect supplied prior art before searching. Record the question,
   audience, recency boundary, relevant definitions, exclusions, and stop rule
   in a compact note. Infer ordinary details; ask only when a missing decision
   would materially change the answer.
2. **Gather.** Read [EVIDENCE.md](EVIDENCE.md) for the shared source, evidence-row,
   and claim-check contract. Find and open owner sources. Keep one evidence
   table with support or an explicit gap for each requested part.
3. **Synthesize and check.** Map findings to evidence; resolve contradictions
   or carry both claims into the gap statement. Separate recommendations from
   source claims. Apply the shared claim check to all retained material claims,
   including uncited prose. A captured passage can satisfy the check; fetch
   again when freshness, incomplete capture, or a contradiction requires it.
4. **Deliver.** Use [REFERENCE.md](REFERENCE.md#investigation-report) for the
   report fields. Stop when the scoped parts are supported or unresolved,
   further retrieval repeats existing evidence, and the claim check passes.
   Preserve limitations rather than implying that missing evidence proves a
   negative. Deliver through the active runtime's contract.

For long, independent reading that earns a worker's overhead, or explicit
user-requested delegation, read [REFERENCE.md](REFERENCE.md#worker-contract).
A single-owner fact stays inline or uses at most one worker. If scope grows,
record why the route changed.

## Continuity and recovery

Across turns, preserve scope, selected route, evidence/artifact locations,
decisions, contradictions, gaps, check results, and the next action. Keep these
in the existing run artifact rather than creating a second state system.

For retrieval failure, try one transient retry or an alternate owner source;
record a continuing failure as a gap. Read
[REFERENCE.md](REFERENCE.md#retrieval-and-worker-recovery) when retrieval or a
worker result is incomplete. Missing evidence may narrow the answer; it cannot
justify an unsupported claim.
