---
name: shopping
description: Destination-aware shopping research; compare exact variants, validate coupons and cashback, calculate true landed totals, and report fresh stock and delivery evidence. Trigger for product finding, deal comparison, coupon checking, shipping-cost analysis, or “best price” requests.
---
# Shopping

## Executable contract

**Job and audience.** Produce an auditable shortlist of purchasable offers for a person deciding what to buy. The reader can see assumptions, evidence, uncertainty, and the final checkout checks. The skill never places an order.

**Inputs.** Capture, or mark unknown: product intent; destination country and postal code (or user-approved coarse location); currency; quantity; exact variant requirements (brand, model, size, color, flavor, pack count, condition); seller/fulfillment preference; membership; payment constraints; delivery deadline; and return-cost tolerance. Ask only for a missing input that can change ranking or safety. A coarse location yields ranges and a checkout ask.

**Trusted-source precedence.** Resolve conflicts in this order: (1) current cart/checkout quote for the exact variant, seller, destination, quantity, and session; (2) first-party retailer terms, APIs, and approved feeds; (3) carrier and customs authority schedules; (4) structured commerce data; (5) independent reporting; (6) aggregators and snippets as discovery leads. Keep both values and record the contradiction when sources at one level disagree.

**Success predicates.** A run is successful when every recommended offer has:

- an exact or explicitly uncertain product/variant identity and seller/fulfillment binding;
- a timestamped price, stock, and delivery observation for the requested destination and quantity;
- a `true_total` interval with a formula, currency, tax-inclusion flag, every known fee, and explicit unknowns;
- promotion status (`validated`, `conditional`, `rejected`, or `unknown`) with terms and observed cart evidence;
- field-level evidence rows (`url`, quote/number, retrieved_at, source_role, query parameters);
- a confidence level and a checkout verification checklist; and
- no unresolved contradiction hidden from the reader.

**Output shape.** Return, in this order: (1) a short ranked table (`rank`, offer, seller, delivered `true_total` low/expected/high, delivery, stock freshness, confidence, link); (2) a component table for each finalist; (3) validated and conditional promotions, including affiliate/cashback disclosure; (4) assumptions, excluded fees, and gaps; (5) “verify at checkout” steps. Rank by destination-specific total range and confidence, then delivery and return fit; use item price as one component of that decision.

**Process choice.** Use a tight evidence loop: scope → parallel retailer/cost/identity/promotion research → normalize and deduplicate → re-open finalists → contradiction and citation audit → render output. Use deep-research orchestration when available for parallel slices and artifact persistence; the runtime supplies its mechanism. Probe checkout only for finalists or when a required field can change the ranking. Finish when each success predicate holds or the stop rule records why it cannot.

## Run steps

1. **Scope.** Write a compact run record with inputs, date/time zone, destination, quantity, currency, deadline, membership, return tolerance, and effort budget. Completion: every ranking-sensitive input is present or an explicit ask/assumption is recorded.
2. **Plan slices.** Create non-overlapping slices for retailer offers, product identity, costs/returns, and promotions. Give each the shared evidence-row schema and source precedence. Completion: each slice names its fields, source roles, and stop condition.
3. **Collect evidence.** Use approved retailer APIs, affiliate feeds, permissioned pages, carrier/customs schedules, and structured data. Store rows append-only; include cart inputs and request metadata. Completion: each candidate has at least one identity row and one current offer row, or is listed as unavailable.
4. **Normalize.** Match exact GTIN/UPC/EAN/ISBN plus brand or MPN first; use cautious title/attribute/image similarity second. Preserve size, color, pack, condition, seller, fulfillment, and refurbished/new distinctions. Convert money with decimal arithmetic and timestamped ISO-4217 FX. Completion: every retained offer has a stable `offer_id` and normalized components.
5. **Calculate.** Apply the formula below and the shipping reference when that branch fires. Keep unknowns as unknown and widen the interval. Completion: each finalist has reproducible low/expected/high totals and linked inputs.
6. **Validate promotions.** Follow the coupon pointer for any code, sale, loyalty offer, cashback, or stacking question. Completion: each promotion has a status, terms, validation event, and rejection/uncertainty reason.
7. **Refresh finalists.** Re-fetch price, stock, seller, shipping, and delivery immediately before presenting links. Completion: every displayed freshness timestamp is from the final refresh; stale data is labeled and downgraded.
8. **Audit and render.** Re-open load-bearing price, date, fee, stock, delivery, and superlative claims. Record contradictions and unresolved gaps, then render the output shape. Completion: all success predicates pass, or the gap statement names the blocked predicate and the safe fallback.

## Canonical records

Use these fields (YAML/JSON is acceptable) and preserve evidence references:

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
Promotion: {code, source_url, source_role, expiry, minimum_subtotal, eligibility, exclusions, stacking_group, stacking_order, limits, validation, status, confidence}
EvidenceRow: {claim, url, exact_quote_or_number, retrieved_at, source_role, query_parameters, confidence}
```

For quantity `q`, use decimal arithmetic:

`true_total = item_subtotal − validated_guaranteed_discounts + outbound_shipping + carrier_surcharges + duties + import_taxes + brokerage_handling + allocated_membership_cost − guaranteed_rebates`.

Show potential cashback separately until portal confirmation. Optional ownership risk is `purchase_total + probability(return) × unreimbursed_return_cost`; keep refundable tax and shipping as separate fields.

## Tool cards and execution policy

The runtime maps these concepts to its available mechanisms; preserve the schemas and observations.

**Evidence retrieval.** Purpose: fetch a source or approved feed selected by source precedence. Input: `{url_or_query, destination?, quantity?, marketplace?, fields[]}`. Output: `{status, retrieved_at, content_or_quote, headers, source_role}`. Read authority only; no purchase effect. Use a bounded timeout, one retry for transient failure with backoff, and a per-source quota. Validate URL, timestamp, and quote before writing a row. On 403/429/CAPTCHA/robots denial, record the response, stop that source, and use an approved fallback or mark unavailable.

**Retailer adapter.** Purpose: map an approved retailer interface to `Offer` and promotion records. Input: `{intent, destination, quantity, variant_constraints, membership}`. Output: `{offers[], promotions[], request_log[]}`. It may read public or user-authorized account data only within declared terms; it never submits an order. Declare auth mode, ToS/robots status, rate limit, pagination, freshness, and unavailable fields. Throttle with a token bucket, retry only transient errors, and open a circuit on repeated policy/rate blocks. Validate seller, SKU, currency, and destination before persistence.

**Cart/checkout probe.** Purpose: observe a price, fee, promotion, stock, or delivery result for a finalist where retailer permission allows. Input: `{offer_id, cart_lines, destination, session_authorization, probe_scope}`. Output: `{pre_total, post_total, line_discounts, shipping, taxes, duties, messages, observed_at}`. It can change a temporary cart but has no order authority; account access requires explicit user authorization. Use the smallest cart, one bounded attempt per hypothesis, and no payment submission. Record rejection messages and recover by returning `conditional` or `unknown`.

**Deterministic calculator.** Purpose: compute normalized totals, FX, thresholds, dimensional weight, and intervals. Input: canonical `Offer`, quantity, formulas, and evidence refs. Output: totals plus an expression tree and warnings. Pure computation with no external effects. Validate decimal precision, currency consistency, and interval monotonicity; on missing inputs emit `unknown` and widen bounds.

**Artifact store.** Purpose: persist run state and evidence for hand-off or compaction. Input: `{run_id, record_type, payload}`. Output: `{artifact_id, checksum, written_at}`. Append-only writes; credentials and full personal addresses are excluded. Verify checksum and schema; on write failure keep the in-memory blocker and stop publication until durable state exists.

## Stop, ask, and fallback

- **Ask** for destination, exact variant, quantity, membership, or deadline when its absence can change the ranking; explain the field needed and offer a coarse-location/range fallback.
- **Stop a branch** after a policy block, quota exhaustion, CAPTCHA, login wall, or three consecutive transient failures. Record status, last response, and the approved fallback.
- **Report evidence missing** when a required field lacks a primary observation. Use an interval and checkout confirmation instead of zero or a guessed value.
- **Downgrade** dynamic price/stock, personalized carts, stale feeds, fuzzy identity, and conditional cashback; remove unverified claims from the headline ranking.
- **Finish** when finalists pass every success predicate and the marginal evidence gain is low, or when remaining blockers are explicitly listed with safe next steps.

## Durable state and disclosure pointers

Persist `run.json`, append-only `evidence.ndjson`, `offers.ndjson`, `promotions.ndjson`, `contradictions.ndjson`, `gaps.ndjson`, and `verification.json` under the run artifact directory. Each hand-off record carries decisions, assumptions, source precedence, artifact IDs/checksums, last refresh, unresolved questions, and next predicate.

**Promotion validation.** If the run needs to discover or validate any promotion (code, sale, loyalty, stacking, or cashback), load [coupon-validation.md](coupon-validation.md) before validation.

**Cost normalization.** If the run needs destination shipping or landed-cost calculation, load [shipping-normalization.md](shipping-normalization.md) before calculation; it covers thresholds, shipment bases, dimensional weight, marketplace rates, returns, duties, taxes, brokerage, and Incoterms.

## Evidence and policy guardrails

Use official APIs, feeds, and permissioned retrieval; disclose affiliate economics near links and rank by user value after fees. Keep request logs without credentials or full addresses. Preserve source quotes, retrieval time, query parameters, formulas, and observed cart results so another agent can rerun the decision. The FTC drip-pricing rationale supports showing mandatory fees in the total and naming exclusions [C-FTC-DRIP]. Retailer and API terms, quotas, robots rules, and customs decisions remain authoritative for each run; Target’s agent/scraping boundary and Best Buy’s 50,000/day, 5/second quota with 403 overage are tracked as [C-TGT-TERMS, C-BBY-QUOTA].

Adapter evidence anchors: Shopify `ProductVariant` exposes variant saleability, price, SKU, quantity, and purchase constraints ([C-SHOPIFY-VARIANT](https://shopify.dev/docs/api/storefront/latest/objects/productvariant)); Amazon competitive pricing is marketplace-scoped and rate-limited to 0.5 requests/second with burst 1 ([C-AMZ-RATE](https://developer-docs.amazon.com/sp-api/reference/getcompetitivepricing)); Walmart inventory is ship-node-specific and can lag ([C-WMT-LAG](https://developer.walmart.com/us-marketplace/docs/inventory-api-overview)); Google shipping data carries destination, service, price, handling, and transit ([C-GOOGLE-SHIPPING](https://support.google.com/merchants/answer/6324484?hl=en)); Schema.org `Offer`/`OfferShippingDetails` supplies availability, currency, destination, and delivery fields ([C-SCHEMA-OFFER](https://schema.org/Offer), [C-SCHEMA-SHIPPING](https://schema.org/OfferShippingDetails)).
