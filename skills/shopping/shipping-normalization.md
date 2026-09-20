# Shipping and return normalization

Read for conditional thresholds, split shipments, returns, weight-based rates,
or imports. A simple quoted domestic charge follows SKILL.md's evidence and
total rules without this reference.

“Free shipping” is conditional until destination, threshold, membership,
geography, item eligibility, seller, and fulfillment match. Missing costs
remain unknown. Keep merchandise before/after discounts, tax inclusion,
shipping, surcharges, duties, and deposits distinct.

1. Group shipments by seller, origin, fulfillment, and destination. Apply
   per-order minimums once; sum per-item/weight charges and retain package count.
2. For threshold/filler comparisons, compute both baskets, including filler
   cost, split orders, changed discounts, and return risk. Verify whether the
   threshold counts subtotal before or after discounts and excludes tax.
3. Use observed checkout quotes as point estimates when valid for the basket;
   policy/rate schedules support bounds. Record unknown/unbounded totals
   rather than manufacturing an expected value.
4. Keep return label, nonrefunded outbound shipping, restocking, responsibility
   by return reason, and non-returnable status separate. Return risk belongs
   in the optional ownership-cost view, with assumptions explicit.

For imports or package/weight-based calculation, read
[COST-DETAILS.md](COST-DETAILS.md) and use only the applicable branch. For a
named retailer/carrier policy, [RETAILER-NOTES.md](RETAILER-NOTES.md#shipping)
contains historical source leads to reopen, not current rates.

Use the canonical formula in [SKILL.md](SKILL.md). Every amount retains URL,
quote/value, time, seller, destination/quantity, relevant package inputs,
source role, and formula. Seller changes, stale feeds, or missing quotes
require wider bounds and a checkout check. A policy-blocked source yields a
gap and permitted fallback, never zero cost.
