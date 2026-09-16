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

## Retailer rules traced to claim rows

- **Target [C-TGT-STACK, C-TGT-EXACT, C-TGT-LIMIT].** One manufacturer, one category, and one item offer may combine per item; exact brand/size/quantity/color/flavor matching and master-file validation apply; expired/copied/altered coupons fail; identical coupons are limited to four per household/day and one storewide offer per transaction.
- **Walgreens [C-WAL-EXPIRY, C-WAL-STACK, C-WAL-DIGITAL].** Expiry is 11:59 p.m. local in stores and 11:59 p.m. CST online; one manufacturer plus applicable Walgreens coupons may apply to one item, with no coupon against a free BOGO item; digital offers must be attached before purchase and cannot be added at pickup/delivery.
- **Rakuten [C-RAK-ATTR, C-RAK-NET, C-RAK-EXCL].** Start from the portal link, stay in the same session, and use approved codes; other-site visits or unlisted codes can void attribution. Net eligible spend excludes taxes, fees, shipping, discounts, returns, cancellations, and extended warranties; rates and exclusions vary. Cashback is a delayed, conditional rebate until confirmed.
- **FTC [C-FTC-AFFILIATE].** A commission, free product, or discount is a material connection. Disclose it clearly and near the recommendation.

## Claim-row index

These IDs point to the research worker rows (retrieved 2026-09-15); retain the URL and quote in each run artifact.

| ID | Primary row URL | Row carried into this skill |
|---|---|---|
| C-TGT-STACK / C-TGT-EXACT / C-TGT-LIMIT | https://www.target.com/help/article/000063717 | Per-item stacking, exact match/master-file validation, expiry/authenticity, and four-identical-coupon/transaction limits. |
| C-WAL-EXPIRY / C-WAL-STACK / C-WAL-DIGITAL | https://www.walgreens.com/topic/help/generalhelp/coupon_policy_main.jsp | Local/CST expiry, manufacturer-plus-store stacking and BOGO rule, and pre-order digital attachment. |
| C-RAK-ATTR / C-RAK-NET / C-RAK-EXCL | https://www.rakuten.com/help/article/terms-conditions ; https://www.rakuten.com/help/article/why-didnt-i-earn-cash-back-360036367393 | Same-session portal attribution, net-spend exclusions, variable terms, and unlisted-code/other-site tracking risk. |
| C-FTC-AFFILIATE | https://www.ftc.gov/business-guidance/resources/ftcs-endorsement-guides-what-people-are-asking ; https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers | Material-connection and near-link disclosure requirement. |
| C-TGT-TERMS / C-BBY-QUOTA | https://www.target.com/c/terms-conditions/-/N-4sr7l ; https://developer.bestbuy.com/legal | Approved-agent/scraping boundary; Best Buy 50,000 calls/day and 5 calls/second limits with 403 after limit. |

## Policy and recovery

Use approved APIs, affiliate feeds, and permissioned browsing. Honor each source quota with bounded retries and backoff. A 403/429/CAPTCHA/robots block closes that source branch and yields an unavailable or conditional result; the profile supplies the retrieval mechanism. Never infer a code’s validity from popularity, copied text, or an unchanged cart total.

## Evidence rows

Every status links to a row with URL, exact quote/number, retrieval date, source role, cart inputs, validation method, observed result, and confidence. Preserve failed tests and contradictions (for example, terms promise a discount while checkout rejects it).
