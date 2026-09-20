# Coupon and cashback validation reference

Load this file when promotion validation is needed: codes, sales, loyalty offers, stacking, or cashback.

## Contract

A promotion is `validated` only when current first-party terms and an observed redemption (cart/API result) agree. Terms alone produce `conditional` unless the retailer explicitly exposes a deterministic automatic discount. Aggregator, newsletter, and search results are `DISCOVERED_UNVERIFIED` leads.

Record:

```yaml
Promotion:
  code: string|null
  source_url: string
  source_role: PRIMARY|INDEPENDENT|DISCOVERED_UNVERIFIED
  retailer: string
  discovered_at: timestamp
  expiry: {value, timezone, confidence}
  minimum_subtotal: {amount, currency, basis: before_discount|after_discount|unknown}
  eligible_skus_or_categories: []
  exclusions: []
  stacking_group: manufacturer|category|item|order|cashback|unknown
  stacking_order: []
  account_or_quantity_limits: []
  cashback_requirements: {portal_link, same_session, approved_codes_only}
  validation: {method: terms|api|cart_test, validated_at, observed_result}
  status: validated|rejected|conditional|unknown
  rejection_reason: expired|minimum|exclusion|stack_conflict|account_limit|invalid|blocked|unknown
  confidence: high|medium|low
```

## Validation sequence

1. Check expiry in the retailer’s stated time zone, “while supplies last,” cancellation, and account/region conditions.
2. Match brand, model, size, quantity, color/flavor, seller, fulfillment, and condition exactly. Keep a variant mismatch as a rejection reason.
3. Check minimum-subtotal basis, discount allocation, taxes/shipping exclusions, payment or membership requirements, and one-time/household limits.
4. Check exclusions: sale items, gift cards, digital goods, marketplace sellers, subscriptions, geography, and non-combinable clauses.
5. Solve stacking as constraints, then test only permitted permutations. Record pre/post totals, line allocation, and rejection messages.
6. Treat a failed test as durable evidence and allow at most one bounded retest for a changed hypothesis. Personalized/account-only results remain `conditional` with a user-run step.

## Source policies and recovery

For named retailer or portal rules, use
[RETAILER-NOTES.md](RETAILER-NOTES.md#promotions) only as historical source
leads. Open current first-party terms before relying on them. Retrieval,
account authority, and stop rules belong to [SKILL.md](SKILL.md) and the
[adapter contract](ADAPTERS.md). A blocked observation remains unavailable
or conditional. Popularity, copied code text, or an unchanged cart total does
not establish validity.

Every status links to evidence with URL, exact quote/value, retrieval time,
source role, relevant basket inputs, method, result, and confidence. Preserve
failed tests and contradictions, including terms that promise a discount while
checkout rejects it. For a focused question these fields can live in the answer;
the schema does not require a separate promotion file.
