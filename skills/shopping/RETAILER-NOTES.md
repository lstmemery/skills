# Historical retailer source notes

Read only when one of the named retailers or services is relevant. These are
source leads and inherited observations from 2026-09-15, not current policy,
price, threshold, or rate evidence. Open the current first-party source and
record the applicable quote in the run before relying on it.

## Shipping

### Carrier and retailer rules traced to claim rows

- **Best Buy [C-BBY-BASIS, C-BBY-THRESHOLD].** Shipping can be per order, per item, or per pound; marketplace sellers set rates. The $35 free-shipping threshold is after coupons and before tax, with product and marketplace exclusions.
- **Target [C-TGT-THRESHOLD].** The $35 merchandise subtotal is after discounts and excludes tax/shipping; geography and item exclusions apply.
- **Amazon [C-AMZ-INTL].** International free shipping counts only eligible items; VAT/import deposits and some third-party items do not count, and nonqualifying items incur delivery fees.
- **UPS/USPS [C-UPS-DIM, C-USPS-DIM].** DIM is `(L×W×H)/divisor`, rounded up, and billable weight is the greater of actual and dimensional weight. UPS examples use divisors 139 (daily) and 166 (retail); USPS applies DIM above one cubic foot and lists a $3 dimension-noncompliance fee when dimensions are inaccurate or omitted.
- **eBay [C-EBAY-RATE].** Sellers choose flat or calculated rates, set parameters per item, and may use chargeable DIM weight and regional surcharges.
- **Trade.gov/CBP/DHL [C-TRADE-LANDED, C-CBP-FEES, C-DHL-FEES].** Duty value can include freight and insurance; local taxes and customs fees can be additional and customs officers decide finally. CBP publishes separate user fees. DHL documents recipient-billed duties/taxes, advance-payment, clearance, and storage charges.
- **Returns [C-AMZ-RETURN, C-EBAY-RETURN, C-TGT-RETURN].** Amazon may deduct return shipping and varies heavy/bulky fees; eBay assigns postage by return reason and policy; Target may withhold original shipping when the return is not due to retailer error.
- **FTC [C-FTC-DRIP].** Mandatory fees belong in the presented total because omission late in checkout is the drip-pricing harm identified in the rulemaking.

### Claim-row index

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


## Promotions

### Retailer rules traced to claim rows

- **Target [C-TGT-STACK, C-TGT-EXACT, C-TGT-LIMIT].** One manufacturer, one category, and one item offer may combine per item; exact brand/size/quantity/color/flavor matching and master-file validation apply; expired/copied/altered coupons fail; identical coupons are limited to four per household/day and one storewide offer per transaction.
- **Walgreens [C-WAL-EXPIRY, C-WAL-STACK, C-WAL-DIGITAL].** Expiry is 11:59 p.m. local in stores and 11:59 p.m. CST online; one manufacturer plus applicable Walgreens coupons may apply to one item, with no coupon against a free BOGO item; digital offers must be attached before purchase and cannot be added at pickup/delivery.
- **Rakuten [C-RAK-ATTR, C-RAK-NET, C-RAK-EXCL].** Start from the portal link, stay in the same session, and use approved codes; other-site visits or unlisted codes can void attribution. Net eligible spend excludes taxes, fees, shipping, discounts, returns, cancellations, and extended warranties; rates and exclusions vary. Cashback is a delayed, conditional rebate until confirmed.
- **FTC [C-FTC-AFFILIATE].** A commission, free product, or discount is a material connection. Disclose it clearly and near the recommendation.

### Claim-row index

These IDs point to the research worker rows (retrieved 2026-09-15); retain the URL and quote in each run artifact.

| ID | Primary row URL | Row carried into this skill |
|---|---|---|
| C-TGT-STACK / C-TGT-EXACT / C-TGT-LIMIT | https://www.target.com/help/article/000063717 | Per-item stacking, exact match/master-file validation, expiry/authenticity, and four-identical-coupon/transaction limits. |
| C-WAL-EXPIRY / C-WAL-STACK / C-WAL-DIGITAL | https://www.walgreens.com/topic/help/generalhelp/coupon_policy_main.jsp | Local/CST expiry, manufacturer-plus-store stacking and BOGO rule, and pre-order digital attachment. |
| C-RAK-ATTR / C-RAK-NET / C-RAK-EXCL | https://www.rakuten.com/help/article/terms-conditions ; https://www.rakuten.com/help/article/why-didnt-i-earn-cash-back-360036367393 | Same-session portal attribution, net-spend exclusions, variable terms, and unlisted-code/other-site tracking risk. |
| C-FTC-AFFILIATE | https://www.ftc.gov/business-guidance/resources/ftcs-endorsement-guides-what-people-are-asking ; https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers | Material-connection and near-link disclosure requirement. |
| C-TGT-TERMS / C-BBY-QUOTA | https://www.target.com/c/terms-conditions/-/N-4sr7l ; https://developer.bestbuy.com/legal | Approved-agent/scraping boundary; Best Buy 50,000 calls/day and 5 calls/second limits with 403 after limit. |
