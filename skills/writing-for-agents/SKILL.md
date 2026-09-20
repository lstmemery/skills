---
name: writing-for-agents
disable-model-invocation: true
description: Write and review documents that agents consume.
---

Write agent-facing documents as useful contracts: the outcome, constraints, and evidence of completion should be clear without prescribing unnecessary work. Apply this reference to skills, `AGENTS.md` / `CLAUDE.md`, and documents reached from them.

When editing a skill, read [SKILL-MECHANICS.md](SKILL-MECHANICS.md) for discovery, explicit loading, and composition. For explanations of the vocabulary and design tradeoffs, read [RATIONALE.md](RATIONALE.md). When evaluating instruction changes or model-dependent behavior, read [MAINTENANCE.md](MAINTENANCE.md).

## Contract and completion

Put the job, audience, trusted inputs, and observable success conditions near the top. Include output shape, authorization boundaries, fallbacks, and durable state only where the task needs them. A contract can be a sentence; use schemas or detailed tool policies when a consumer or consequential operation needs that precision.

A **completion criterion** distinguishes done from unfinished. Name the evidence that closes the work: a supported claim, passing test, validated artifact, observed effect, or recorded blocker. Distinguish missing evidence from a factual negative. State when a missing decision requires an ask and when an explicit limitation is sufficient. Match the criterion's **demand**—the coverage it requires—to the task; preserve intentional exhaustiveness without manufacturing work.

## Pointers and information hierarchy

A **context pointer** names material outside the current context and the condition for reading it. State its purpose and distinct trigger branches; collapse synonyms for the same branch. Repair a weak pointer before inlining its target. Verify that the needed file is reachable through the active runtime.

**Steps** are ordered actions; **reference** supplies definitions, rules, and facts. Keep shared instructions in the main file. Use **progressive disclosure** to put substantial branch-specific detail in linked references, with its load condition beside the relevant decision. Flat reference is appropriate when all its rules apply together; it need not become a recipe.

Use **co-location** to keep a concept's definition, rules, and caveats together. If **sprawl** makes a path hard to follow, split by a real branch or sequence boundary. Avoid fragmenting a short, coherent reference merely to shorten its entrypoint.

## Process and authority

Start from the outcome and let the agent choose a sufficient route. Require ordering where dependencies, authorization, isolation, or an intermediate check demand it. Add delegation or parallel perspectives when they resolve independent substantial work or a meaningful uncertainty; preserve established review and isolation requirements.

For consequential or fragile tool operations, document non-obvious selection rules, input constraints, effects, existing authorization requirements, retry limits, and observable recovery. Use a **tool card**—a compact interface contract—when those details need a stable home. Ordinary calls can rely on available tool schemas and runtime policy. A document describes authority; it does not grant permissions beyond the user's task and active environment.

For work spanning handoffs or compaction, keep durable decisions, constraints, artifact identifiers, verification, blockers, and the next action. Use the current state record rather than reconstructing stable facts from a full transcript.

## Ownership and pruning

A **single source of truth** owns each rule. Other documents point to it instead of restating it. The environment owns discoverable configuration and command syntax; a prose **cache** of that information earns its cost only when lookup is expensive or the document adds a non-obvious convention or gotcha.

Prune by decision value:

- Remove **duplication**: the same meaning maintained in multiple places.
- Remove **sediment**: stale or irrelevant instructions retained through habit.
- Treat a suspected **no-op**—an instruction that changes no behavior—as a model-relative hypothesis. Check affected outcomes before deleting a safeguard on that basis.
- Use a **leading word**, a concise familiar term for a defined behavior, when it replaces repeated explanation without losing precision. Define unfamiliar terms; test whether the shorthand remains clear.
- Prefer a direct target behavior. Keep explicit prohibitions when they clearly express a real boundary; positive phrasing is not evidence that a guardrail is redundant.

**Context load** is the cost of material actually loaded into the agent's context. **Cognitive load** is the human effort of remembering documents and choosing when to use them. Spend either where it supports useful decisions; neither document length nor a shorter pointer alone proves better behavior.

Finish an edit when the changed contract and branch pointers are coherent, links resolve, and checks appropriate to the change preserve its required outcomes. Use a source, artifact diff, validator, test, or human decision as the **oracle** for verification; untested self-critique remains a hypothesis.
