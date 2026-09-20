# Retailer adapters and cart probes

Load before using a retailer/API adapter or a checkout observation. Current
first-party documentation, terms, and observed responses govern the operation;
the links below are discovery starting points, not current quotas or prices.

## Adapter

Input: intent, destination, quantity, exact variant constraints, membership,
and requested fields. Output: offers, promotions, request metadata, timestamps,
and explicit unavailable fields. Declare auth mode, permitted access, quota,
pagination, and source freshness before retrieval. Throttle to current quotas;
retry one transient failure with backoff. Stop on policy/rate blocks. Validate
seller, SKU, currency, and destination before retaining observations.

## Cart/checkout probe

Input: exact offer and cart lines, destination, authorized session, and a
bounded probe scope. Output: before/after totals, line discounts, shipping,
taxes/duties, messages, and observation time. A probe may change a temporary
cart only within the granted scope. Account access requires explicit user
authorization; no order placement or payment submission is permitted.

Use the smallest basket and one attempt per hypothesis. Promotion-specific
retest limits and statuses belong to [coupon-validation.md](coupon-validation.md).
Capture rejections as evidence. A blocked, personalized, or incomplete result
remains conditional or unknown and yields a user-run checkout step.

## First-party adapter starting points

- [Shopify ProductVariant](https://shopify.dev/docs/api/storefront/latest/objects/productvariant): variant identity, saleability, quantities, and purchase constraints.
- [Amazon competitive pricing](https://developer-docs.amazon.com/sp-api/reference/getcompetitivepricing): marketplace scope and current rate limits.
- [Walmart inventory](https://developer.walmart.com/us-marketplace/docs/inventory-api-overview): ship-node identity and update lag.
- [Google shipping data](https://support.google.com/merchants/answer/6324484?hl=en): destination, price, handling, and transit.
- [Schema.org Offer](https://schema.org/Offer) and [OfferShippingDetails](https://schema.org/OfferShippingDetails): structured discovery fields to corroborate.
- [Target terms](https://www.target.com/c/terms-conditions/-/N-4sr7l) and [Best Buy developer terms](https://developer.bestbuy.com/legal): current access and quota constraints.

Record the opened policy/API version and relevant parameters without
credentials or full personal addresses. A historical claim-row ID is not a
substitute for current source evidence.
