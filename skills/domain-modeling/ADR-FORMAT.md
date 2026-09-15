# ADR Format

ADRs live beside the `CONTEXT.md` whose vocabulary the decision uses, in `docs/adr/`, with sequential names such as `0001-event-sourced-orders.md`. Create the directory lazily. A single-context repository keeps it at the root; a multi-context repository may also have system-wide ADRs for cross-context decisions.

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

Offer an ADR only when all three are true:

1. changing the choice later has meaningful cost;
2. a future reader would be surprised without the rationale; and
3. real alternatives were weighed.

Qualifying examples include context ownership and integration, aggregate consistency boundaries, architectural shape, durable technology choices, deliberate deviations, and constraints absent from code. Routine naming changes and obvious choices stay in the glossary or ordinary documentation.

## Numbering

Scan the selected context's `docs/adr/` for the highest existing number and increment it. Link the ADR from the affected glossary or context map when discoverability matters.
