# CONTEXT.md Format

`CONTEXT.md` is a concise, context-scoped glossary. It records language, not implementation, behavior specifications, or architecture decisions.

## Structure

```md
# {Context Name}

{One or two sentences describing this context and why it exists.}

## Language

**Order**:
{What the concept is.}
_Avoid_: Purchase, transaction
_Context_: Ordering
_Owner_: Ordering team
_Confidence_: accepted
_Lifecycle_: active

**Invoice**:
{What the concept is.}
_Avoid_: Bill, payment request
```

Keep the definition and `_Avoid_` fields for every term. Add `_Context_`, `_Owner_`, `_Confidence_`, and `_Lifecycle_`/`_Replaced by_` only when scope, uncertainty, or a rename makes them useful. Definitions are one or two sentences and describe what a thing is. Group terms under natural subheadings.

Only include concepts specific to this context. Put scenarios, invariants, lifecycle behavior, integration contracts, classes, database fields, broker topics, and other mechanisms in dedicated records or ADRs, linked from the glossary when useful.

## Single versus multiple contexts

Use a root `CONTEXT.md` when the repository has one context. When `/CONTEXT-MAP.md` exists, read it to select the relevant context; if selection is unclear, ask. Create a root glossary lazily when the first term is resolved.

`CONTEXT-MAP.md` records relationships, not shared vocabulary:

```md
# Context Map

- [Ordering](./src/ordering/CONTEXT.md): receives and tracks orders
- [Billing](./src/billing/CONTEXT.md): issues invoices and records payments

- **Ordering → Billing**
  - Purpose: request an invoice after shipment
  - Translation owner: Billing
  - Consistency: eventual
  - Contract/evidence: [ADR-0003](./docs/adr/0003-order-events.md)
```

Every relationship names source, target, purpose, translation owner, consistency mode (`synchronous` or `eventual`), and supporting evidence when available. Do not encode technology choices here; record those in ADRs. Revisit the map when language, invariants, ownership, or non-functional requirements change.
