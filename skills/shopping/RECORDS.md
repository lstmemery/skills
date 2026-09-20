# Shopping records

Load when normalizing a comparison or serving an existing structured-record
consumer. Focused answers use only applicable fields, with evidence inline.
Store these logical records together by default; file count does not establish
quality. Keep failed observations, contradictions, and earlier timestamps.

```yaml
ProductIdentity: {brand, model, gtin_or_mpn, title, evidence_refs}
Variant: {size, color, flavor, pack_count, condition, seller, fulfillment, quantity_constraints}
Offer:
  offer_id: retailer|seller|sku|variant|condition
  item_subtotal: Money
  discounts: [{kind, amount, evidence_ref}]
  shipping_components: [{basis: per_order|per_item|per_weight|seller_calculated|unknown, amount_or_range, conditions, evidence_ref}]
  surcharges: [{kind, amount_or_range, evidence_ref}]
  duties_taxes: [{kind, amount_or_range, included: true|false|unknown, evidence_ref}]
  broker_handling: [{kind, amount_or_range, payer, evidence_ref}]
  return_cost: {kind, amount_or_range, responsibility, evidence_ref}
  membership_cost_allocated: Money|null
  currency_fx: {source, rate, timestamp}
  delivery_estimate: {handling, transit, confidence}
  stock: {state, quantity, ship_node, as_of, lag, confidence}
  unknowns: []
  true_total: {low, expected, high, currency, formula}
  as_of: timestamp
EvidenceRow: {claim, url, exact_quote_or_number, retrieved_at, source_role, query_parameters, confidence}
```

The promotion schema belongs to [coupon-validation.md](coupon-validation.md).
Use [SKILL.md](SKILL.md)'s total formula; preserve tax inclusion, original
currency, and evidence references. Unknown expected/high totals remain unknown
unless evidence supports a finite bound. Validate currency consistency,
decimal arithmetic, and low <= expected <= high for values that are known.

## Optional calculator input

[CALCULATOR.md](CALCULATOR.md) defines the versioned arithmetic projection for
`scripts/calculate.py`. It is separate from these evidence and shopping records.
Retain `offer_id`, original amounts/currencies, evidence references, observations,
and interpreted rules when preparing it. Use shipment groups and explicit
promotion allocations; map each payable shipping, surcharge, tax/duty, brokerage,
or allocated membership amount to a charge. An unresolved payable fee needs an
interval charge with a supported lower bound and null unsupported expected/high
values. A free-text unknown alone cannot change a numeric total.

Treat empty arrays as an explicit assertion that no applicable components are
omitted, not as a substitute for research. Taxes already included remain visible
with `included: true`; uncertain inclusion uses `"unknown"`. Return costs remain
outside purchase totals. Use the same benefit ID for the same economic benefit
so a discount and rebate cannot both subtract it. The calculator cannot identify
duplicate benefits disguised by different IDs or establish evidence validity.

For existing consumers, keep the original layout and fields. Link the separate
calculation result by `offer_id`; copy its total/formula only if compatible with
that consumer. Do not silently replace legacy Money or Promotion records with
calculator records. No automatic NDJSON/YAML migration is provided.

## Existing consumers and larger runs

When resuming a run or writing for a known consumer, retain its existing layout:
`run.json`, append-only `evidence.ndjson`, `offers.ndjson`, `promotions.ndjson`,
`contradictions.ndjson`, `gaps.ndjson`, and `verification.json`. Preserve the
fields and identifiers the consumer requires; a user must settle any incompatible
migration. Otherwise a single Markdown or structured run artifact is sufficient.

Across turns retain inputs, decisions, assumptions, evidence references, last
refresh, unresolved questions, and next action. If split files/checksums are
needed, verify their references after writing. A persistence failure leaves the
run incomplete; preserve the blocker and finish the required artifact before
claiming delivery. Exclude credentials and full personal addresses throughout.
