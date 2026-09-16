# Shipping, landed-cost, and return normalization reference

Load this file when destination shipping or landed-cost calculation is needed; it covers thresholds, shipment bases, dimensional weight, marketplace rates, returns, duties, taxes, brokerage, and Incoterms.

## Contract

Represent every cost component separately. “Free shipping” is conditional until destination, threshold, membership, geography, item eligibility, seller, and fulfillment are verified. Missing package, tax, duty, or brokerage data remains `unknown`; calculate a range and require checkout confirmation.

## Normalization sequence

1. Group shipments by seller, origin, fulfillment party, and destination. Apply per-order minimums once; sum per-item and per-weight charges; preserve package count and split shipments.
2. Keep merchandise subtotal before/after discounts, tax, shipping, duties, and import deposits separate. Simulate baseline versus threshold/filler baskets and show filler cost, return risk, and split-order effects.
3. Capture packed dimensions, actual weight, carrier/service divisor, and oversize/residential/remote/fuel/correction surcharges; use packed dimensions as the dimensional-weight input.
4. For cross-border offers, capture HS code, origin, Incoterm (DDP versus DDU/DAP), freight, insurance, duty, VAT/GST/sales tax, brokerage/disbursement, handling/storage, carrier advance, payer, and an uncertainty interval. Convert currencies with timestamped rates and retain originals.
5. Keep outbound shipping, return label, nonrefunded outbound cost, restocking fee, reason-specific responsibility, and non-returnable status separate. Add expected return cost only in the optional ownership-risk view.

## Carrier and retailer rules traced to claim rows

- **Best Buy [C-BBY-BASIS, C-BBY-THRESHOLD].** Shipping can be per order, per item, or per pound; marketplace sellers set rates. The $35 free-shipping threshold is after coupons and before tax, with product and marketplace exclusions.
- **Target [C-TGT-THRESHOLD].** The $35 merchandise subtotal is after discounts and excludes tax/shipping; geography and item exclusions apply.
- **Amazon [C-AMZ-INTL].** International free shipping counts only eligible items; VAT/import deposits and some third-party items do not count, and nonqualifying items incur delivery fees.
- **UPS/USPS [C-UPS-DIM, C-USPS-DIM].** DIM is `(L×W×H)/divisor`, rounded up, and billable weight is the greater of actual and dimensional weight. UPS examples use divisors 139 (daily) and 166 (retail); USPS applies DIM above one cubic foot and lists a $3 dimension-noncompliance fee when dimensions are inaccurate or omitted.
- **eBay [C-EBAY-RATE].** Sellers choose flat or calculated rates, set parameters per item, and may use chargeable DIM weight and regional surcharges.
- **Trade.gov/CBP/DHL [C-TRADE-LANDED, C-CBP-FEES, C-DHL-FEES].** Duty value can include freight and insurance; local taxes and customs fees can be additional and customs officers decide finally. CBP publishes separate user fees. DHL documents recipient-billed duties/taxes, advance-payment, clearance, and storage charges.
- **Returns [C-AMZ-RETURN, C-EBAY-RETURN, C-TGT-RETURN].** Amazon may deduct return shipping and varies heavy/bulky fees; eBay assigns postage by return reason and policy; Target may withhold original shipping when the return is not due to retailer error.
- **FTC [C-FTC-DRIP].** Mandatory fees belong in the presented total because omission late in checkout is the drip-pricing harm identified in the rulemaking.

## Claim-row index

These IDs point to the research worker rows (retrieved 2026-09-15); retain the URL and quote in each run artifact.

| ID | Primary row URL | Row carried into this skill |
|---|---|---|
| C-BBY-BASIS / C-BBY-THRESHOLD | https://www.bestbuy.com/site/help-topics/shipping-costs-and-timing/pcmcat203400050006.c?id=pcmcat203400050006 ; https://www.bestbuy.com/site/help-topics/free-shipping/pcmcat276800050002.c?id=pcmcat276800050002 | Per-order/per-item/per-pound shipping, marketplace seller rates, and conditional $35 threshold. |
| C-TGT-THRESHOLD | https://www.target.com/help/article/000062738 ; https://www.target.com/help/article/000057407 | Merchandise-subtotal threshold, discount/tax exclusions, geography and item exclusions. |
| C-AMZ-INTL | https://us.amazon.com/gp/help/customer/display.html?nodeId=GY48Z9B62JLTAQV2 | Eligible-item minimums, import-deposit/third-party exclusions, and nonqualifying delivery fees. |
| C-UPS-DIM / C-USPS-DIM | https://www.ups.com/us/en/support/shipping-support/shipping-dimensions-weight ; https://www.usps.com/business/verify-postage.htm | DIM formulas, divisors, rounding, greater-of-weight rule, one-cubic-foot USPS threshold, and $3 correction fee. |
| C-EBAY-RATE | https://www.ebay.ca/sellercentre/shipping ; https://www.ebay.com.au/sellercentre/postage-rate-tables | Flat/calculated seller rates, per-item parameters, DIM and regional surcharges. |
| C-TRADE-LANDED / C-CBP-FEES / C-DHL-FEES | https://www.trade.gov/import-tariffs-fees-overview ; https://www.cbp.gov/trade/basic-import-export/user-fee-table ; https://www.dhl.com/us-en/home/express/products-and-solutions/products-and-services-overview/customs-services.html ; https://ebilling.dhl.com/gpp/web/faq/us/ | Freight/insurance duty basis, additional taxes/customs fees, final customs determination, published CBP fees, and DHL recipient-billed advances/clearance/storage. |
| C-AMZ-RETURN / C-EBAY-RETURN / C-TGT-RETURN | https://www.amazon.com/gp/help/customer/display.html?nodeId=GFLBEJCLHMVFEPA8 ; https://www.ebay.ca/help/buying/returns-refunds/returning-item/return-shipping?id=4066 ; https://www.target.com/help/article/000062291 | Return-shipping deductions, reason-specific responsibility, and possible nonrefund of outbound shipping. |
| C-FTC-DRIP | https://www.ftc.gov/system/files/ftc_gov/pdf/r207011unfairjunkfeesnprmfinal.pdf | Mandatory-fee omission as drip-pricing harm; show all known mandatory fees. |

## Canonical fields and formula

```yaml
shipping_components: [{basis, amount_or_range, conditions, evidence_ref}]
surcharges: [{kind, amount_or_range, evidence_ref}]
duties_taxes: [{kind, amount_or_range, included, evidence_ref}]
broker_handling: [{kind, amount_or_range, payer, evidence_ref}]
return_cost: {kind, amount_or_range, responsibility, evidence_ref}
unknowns: []
true_total: {low, expected, high, currency, formula}
```

Apply the canonical `true_total` formula in `SKILL.md`, adding the branch-specific shipping, surcharge, duty, brokerage, and membership fields above. Use checkout quotes as the point estimate. Use policy/rate tables and tariff estimators for bounds; label customs determination and carrier surcharges as uncertain when not observed. Rank with the complete destination-specific total.

## Evidence rows and recovery

For each amount retain URL, exact quote/number, retrieval time, destination, quantity, seller, package inputs, source role, and formula. A missing destination quote, stale feed, or seller change widens the interval and adds a checkout step. A blocked source yields `unknown` plus an approved fallback; no value is coerced to zero.
