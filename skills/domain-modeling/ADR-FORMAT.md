# ADR Format

ADRs live beside the `CONTEXT.md` whose vocabulary the decision uses, in `docs/adr/`, with sequential names such as `0001-event-sourced-orders.md`. Create the directory lazily. A single-context repository keeps it at the root; a multi-context repository keeps system-wide and cross-context decisions in the root `docs/adr/` and context-scoped decisions beside that context's `CONTEXT.md`.

## Required record

```md
# {Short decision title}

## Context
{Problem, forces, affected context(s), and evidence.}

## Decision
{The choice and its scope.}

## Status
proposed | accepted | deprecated | superseded by ADR-NNNN

## Consequences
{Material benefits, costs, constraints, and follow-up work.}
```

For high-uncertainty or high-lock-in choices, add `Alternatives`, `Confidence`, and `Reevaluate when`. Keep accepted records immutable; supersede them with a new ADR and preserve the link and old status.

## Eligibility

Apply [ADR eligibility in SKILL.md](SKILL.md#adr-eligibility) before creating a
record. This file owns the record format and numbering, not a second eligibility gate.

## Numbering

Scan the selected context's `docs/adr/` for the highest existing number and increment it. Link the ADR from the affected glossary or context map when discoverability matters.
