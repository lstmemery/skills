---
name: shopping
description: Compare exact product variants by delivered total, shipping, coupons, and cashback; research current purchase offers and answer “which should I buy?” or ticket/pass price comparisons.
---

# Shopping

Answer a focused offer question or produce an auditable purchase shortlist.
Bind observations to the exact variant, seller, fulfillment, quantity, and
destination. Show evidence, freshness, applicable costs, and explicit unknowns.
The skill never places an order or submits payment. Account access requires
explicit authorization; a research request alone does not grant it.

## Scope and route

Capture only inputs that affect the answer: product/variant, destination
(country and postal code or a user-approved coarse location), currency,
quantity, seller/fulfillment, membership/payment constraints, deadline, and
return tolerance. Ask only for missing inputs that can change the result;
a coarse location can support a range with a checkout check.

- **Focused query:** one coupon, offer, stock observation, or shipping question.
  Read the applicable sources, follow only the needed validation/cost reference,
  and return the result, terms, timestamped support, and limitations. A ranked
  shortlist, slices, and comparison records are unnecessary. Expand to the
  comparison path if answering actually requires choosing among offers.
- **Purchase comparison:** use the comparison loop below. Rank exact offers by
  destination-specific total and confidence, then delivery and return fit.

Use one durable run artifact containing inputs, evidence, decisions, and gaps
by default; the concise answer/report can serve for a focused query. Follow the
runtime's output and delivery contract. If an existing consumer requires the
seven-file record format, preserve it using [RECORDS.md](RECORDS.md). Split
records only for that consumer, substantial volume, or independent workers.

## Evidence and cost rules

Prefer current authorized cart/checkout observations for the exact session and
basket, then first-party retailer terms/APIs/feeds, carrier/customs schedules,
structured commerce data, and independent reporting. Aggregators and snippets
are leads. Preserve conflicting observations and their disposition.

Each material field needs a source URL, exact quote/value, retrieval timestamp,
source role, and relevant query/basket inputs. Keep credentials and full personal
addresses out of artifacts. Unknown fees never become zero; use defensible
bounds and leave the upper/expected value unknown when it cannot be bounded.

Use decimal arithmetic and timestamped FX, retaining original currencies:

`true_total = item_subtotal - validated_guaranteed_discounts + outbound_shipping + carrier_surcharges + duties + taxes_not_already_included + brokerage_handling + allocated_membership_cost - guaranteed_rebates`

Show tax inclusion and every included component to prevent double counting.
Potential cashback stays separate until confirmed. Return-risk costs belong in
an optional ownership-cost view, not a disguised purchase price. A guaranteed
rebate and a discount must not subtract the same benefit twice.

## Comparison loop

1. **Scope and gather.** Record inputs and an effort/stop rule. Separate retailer,
   identity, cost/return, and promotion questions only when useful; bounded work
   stays inline. Delegation must earn its overhead and follow the calling
   workflow; delegated research follows the deep-research worker contract,
   including its fanout bounds.
2. **Normalize.** Match identifiers (GTIN/UPC/EAN/ISBN or brand plus MPN), then
   exact attributes; label fuzzy matches. Preserve pack count, condition,
   seller, fulfillment, and quantity distinctions. Load
   [RECORDS.md](RECORDS.md) when normalizing comparison records.
3. **Calculate and validate.** Compute reproducible total ranges with linked
   inputs. For supported arithmetic, use the offline calculator and explicit
   input contract in [CALCULATOR.md](CALCULATOR.md). Keep existing run records;
   derive a calculation input without migrating the consumer's layout. The
   command checks structure and computes supplied rules; product selection,
   terms interpretation, promotion validation, evidence quality, and policy
   decisions remain this workflow's responsibility. If the helper is absent or
   a rule is unsupported, retain the Decimal/manual formula and explicit gaps;
   do not install anything or force unsupported rules into the schema.
   For promotions, read [coupon-validation.md](coupon-validation.md).
   For conditional thresholds, split shipments, returns, dimensional weight,
   or import costs, read [shipping-normalization.md](shipping-normalization.md).
   A straightforward quoted domestic shipping charge needs only the core
   evidence and total rules.
4. **Refresh and check.** Immediately before presenting finalists, refresh
   price, stock, seller, shipping, and delivery. Compare all material claims,
   including fee/date/superlative claims, against this captured evidence. The
   refresh can satisfy the claim check; fetch again only for changed, missing,
   incomplete, or conflicting support. Label stale or unavailable observations.
5. **Return.** Give a compact ranked table with exact offer, seller, total range,
   currency, delivery, stock/as-of, confidence, and link; then finalist cost
   components, promotion status, assumptions/gaps, and checkout checks. Disclose
   affiliate economics near links. Finish when every recommendation is supported
   or narrowed to reflect explicit gaps and further searching adds little.

Promotion status is `validated`, `conditional`, `rejected`, or `unknown` under
the coupon reference. Never rank an unverified claim as a guaranteed saving.
State the basis for confidence rather than inventing a numeric score.

## Retrieval, probes, and recovery

Use approved APIs, feeds, and permissioned retrieval. Respect current terms,
quotas, and access denials. One retry with backoff is allowed for a transient
retrieval failure; a repeated failure becomes a gap. A 403/429/CAPTCHA/robots
block closes that source branch; use a permitted fallback.

Before using a retailer adapter or probing a cart, read
[ADAPTERS.md](ADAPTERS.md) for the operation contract. Probes need retailer
permission and any required account/session authorization; use only finalists
or a field that can change the answer. Record observed rejection and uncertainty
rather than treating an unavailable checkout as validation.
