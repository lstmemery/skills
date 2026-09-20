---
name: domain-modeling
description: Resolve domain terms, scenarios, ownership, boundaries, context maps, and ADRs; routine vocabulary lookup stays in project docs.
---

# Domain Modeling

## Contract

**Job:** turn ambiguous domain language into a shared, context-scoped model aligned with behavior and code.

**Evidence order:** domain-expert or maintainer decisions, requirements and scenarios, code and tests, then inference. Preserve uncertainty when evidence is missing.

**Success:** the context and owner are identified (or ambiguity is recorded and asked); changed terms/events have definitions, scope, useful confidence/lifecycle, a normal scenario, and an edge case; invariants and lifecycle rules have verifiable records; relevant glossary/map/ADR files are updated as decisions crystallise; code, tests, scenarios, and docs were searched and every mismatch has a disposition; proposed aggregates, invariants, ownership, and architectural mappings have human confirmation before acceptance.

## Loop

1. **Locate.** Read root `CONTEXT-MAP.md` when present, then the relevant `CONTEXT.md`, ADRs, requirements, and code/tests. Close with the relevant context and owner identified, or a recorded ambiguity to resolve before assigning ownership. Create files lazily.
2. **Discover.** Extract terms, past events, commands, policies, and hypotheses. Close with normal and edge scenarios for changed terms/events and their relationships, plus relevant invariants, lifecycle rules, or consistency reasons. Use EventStorming or another collaborative workshop for complex cross-team flows only when its cost is justified.
3. **Validate.** Search symbols, tests, examples, and docs. Check behavior rather than shared fields. Mark items `proposed` or `accepted`, with confidence when useful. On disagreement, stop acceptance, record the contradiction and resolution (or ask), and preserve alternatives that explain it.
4. **Record.** Update the glossary immediately when a term resolves; read [CONTEXT-FORMAT.md](CONTEXT-FORMAT.md) when creating or editing a glossary/map. For multiple contexts, update the map with source, target, purpose, translation owner, and `synchronous` or `eventual` consistency. Apply [ADR eligibility](#adr-eligibility); when a decision qualifies, use [ADR-FORMAT.md](ADR-FORMAT.md).
5. **Check.** Run terminology, link, build, or test checks available in the repository. Otherwise leave a dated manual check naming searched paths and evidence. Confirm affected code can discover its glossary or ADR.

A step closes only with evidence or an explicit blocker/ask. If expert confirmation is unavailable, leave the artifact `proposed` and name the reviewer.

## Rules

- Keep `CONTEXT.md` implementation-free: define what a concept is, its `_Avoid_` aliases, and its context. Classes, columns, topics, protocols, behavior, invariants, scenarios, and integration contracts belong in dedicated records or ADRs linked from the glossary.
- Be opinionated and concise. Add `_Context_`, `_Owner_`, `_Confidence_`, and `_Lifecycle_`/`_Replaced by_` metadata when multiple contexts or a rename make them useful; omit empty ceremony. Shared fields do not establish shared meaning.
- Treat past domain events as immutable observations. Label commands, policies, and hypotheses so discovery is not mistaken for an implementation contract.
- `CONTEXT-MAP.md` is required for multi-context relationships and must be revisited when language, invariants, ownership, or non-functional needs change. Keep technology choices in ADRs.
- AI may propose terms, events, and boundaries. Humans confirm accepted aggregates, invariants, ownership, and architectural mappings; never invent confidence.

## ADR eligibility

Offer an ADR only when all three apply: changing the choice has meaningful
cost, its rationale would surprise a future reader, and real alternatives
were weighed. Boundary ownership/integration, aggregate consistency boundaries,
durable technology, deliberate deviations, and invisible constraints can qualify.
Routine naming and obvious choices remain ordinary documentation. Accepted
ADRs are immutable; supersede them with a linked record. This section owns the
gate for other skills and the format companion.

Create only the file and directory needed by the first resolved term or qualifying decision.
