# Offline shopping calculator

Use `python3 skills/shopping/scripts/calculate.py --input calculation.json`
from a source checkout, or resolve `scripts/calculate.py` relative to this skill.
Use `--input -` for one JSON document on stdin. Python 3.10+ and its standard
library are sufficient; no package installation is needed. JSON is always the
output format. Save stdout only to an authorized artifact path if needed.

The command reads only the chosen input or stdin, writes its result to stdout,
and writes errors to stderr. It has no network, retailer, account, cart, purchase,
configuration, environment-value, clock, or publishing operations. It never
evaluates expressions supplied as text. No preview or apply flag is needed.

Exit 0 means every offer was computed; exit 2 means invalid input or input I/O
failure, with empty stdout and `{"error":"invalid_input","message":"..."}` on
stderr. Invalid CLI arguments use argparse's usage diagnostic and exit 2.
There are no retries. Correct the rejected input; identical accepted input
produces identical output. Callers should use a bounded process timeout (the
tests use 10 seconds) and preserve a failed run as a gap, not a zero total.

## Decisions that remain in prose

Match the exact product, seller, fulfillment, basket, quantity, and destination;
group shipments and establish complete costs before calculation. Interpret
eligibility, tax inclusion/payer, stacking order, promotion status and guarantees,
cashback attribution, package rules, and threshold exclusions under the existing
[coupon](coupon-validation.md), [shipping](shipping-normalization.md), and
[cost](COST-DETAILS.md) references. Evidence references point to retained source
observations with URL, quote/value, timestamp, role, and relevant basket inputs.
The calculator checks their presence, not their truth, quality, freshness, or
support. It does not validate coupons, choose policies, classify imports, choose
FX rates, estimate return probability, rank offers, or authorize any action.

Use the profile's output root. Exclude credentials, account/session identifiers,
full personal addresses, and unrelated records from calculation inputs. The
output intentionally preserves the supplied offer; it is not a redaction tool.

## Input version 1

One JSON object has exactly these required keys:

| Field | Value |
| --- | --- |
| `schema_version` | Integer `1` |
| `as_of` | Explicit ISO timestamp with seconds and UTC offset, e.g. `2026-09-20T12:00:00Z` |
| `currency` | Output currency, three uppercase letters; use the correct ISO code |
| `rounding` | `{"places":2,"mode":"half_up"}`; places 0–6, mode `half_up` or `half_even` |
| `fx` | Array of direct conversion observations; `[]` when unnecessary |
| `offers` | Nonempty array of Offer objects below |

All schemas reject unknown keys and duplicate JSON keys. Every described field
is required unless specifically optional. Empty arrays are explicit assertions
that no relevant entries are missing. Strings must be nonempty and trimmed,
at most 2,000 characters; array limits are 1,000 entries. Quantities and counts
are JSON integers 1–1,000,000; booleans and fractional/string counts are rejected.
IDs are unique within their record category per offer; offer IDs are unique in
the request. Shipment ID `order` is reserved.

Money, rates, lengths, weights, divisors, and increments use nonnegative decimal
**strings**, never JSON numbers. Accepted syntax is `0`, `10`, or `0.335`, with
up to 18 integer and 18 fractional digits; signs, exponents, leading zeros,
commas, NaN and Infinity are rejected. Rates/divisors and relevant physical
measurements must be positive, except a promotion rate can be zero.

| Common structure | Required fields / meaning |
| --- | --- |
| Money | `{"amount":"10.00","currency":"USD"}` |
| Interval | `{"low":"0","expected":null,"high":null,"currency":"USD"}`; low is a defensible finite bound; expected/high may independently be null |
| Observation fields | `observed_at` timestamp and nonempty `evidence_refs` string array |
| FX observation | `from`, `to`, `rate`, plus observation fields; multiply source by rate; `to` must be output currency |

Known interval values must satisfy low ≤ expected ≤ high. `expected` is a
supplied estimate, never a manufactured midpoint. Unsupported expected/high
values remain null. No inverted or chained FX rates are inferred. Every foreign
amount, including excluded components and return costs, requires a direct rate.
Duplicate source rates and identity rates are rejected. All observations must
be at or before `as_of`; age/expiry acceptability remains a prose decision.
Currency codes are syntax-checked; no live ISO registry or currency minor-unit
table is consulted. The caller explicitly chooses rounding precision.

### Offer and shipment

Offer keys: `offer_id`, `quantity`, `shipments`, `charges`, `promotions`,
`return_costs`, `unknowns`. The last field is an array of descriptive strings.
It preserves contextual gaps but does not alter arithmetic: every possible
payable fee must also be represented as a charge with appropriate null bounds.

Shipment keys: `shipment_id`, `quantity`, `price_basis`, `item_price`, `packages`,
plus observation fields. Shipment quantities must sum to Offer quantity.
`item_price` is Money; `price_basis` is `per_item` (multiply by shipment quantity)
or `subtotal` (already covers this shipment's quantity). Values are **before the
promotions supplied here**. Never submit a net price and subtract its discount
again. Package counts are separate from merchandise quantities.

Shipment grouping is already settled by the caller. Each offer is one comparison
basket/order. Use shipment scopes for separate seller charges and one order
charge for a cost shared by all shipments. The program does not infer seller,
origin, fulfillment, or destination relationships.

### Charges

Keys: `component_id`, `kind`, `scope`, `basis`, `money`, `included`, `threshold`,
plus observation fields. `kind` is `shipping`, `surcharge`, `duty`, `tax`,
`brokerage`, or `membership`. The amount/rate is an Interval.

| Field | Meaning |
| --- | --- |
| `scope` | `order` or an existing shipment ID |
| `basis` | `once`, `per_item`, `per_package`, or `per_weight` |
| `included` | Boolean true if already in merchandise/another declared component; false if additional; string `"unknown"` if unresolved |
| `threshold` | null, or a Threshold below; only shipping charge waivers support thresholds |
| `weight_unit` | Required only for `per_weight`: `lb`, `kg`, `g`, or `oz` |

`once` applies once to the scoped group. `per_item` multiplies by its merchandise
quantity. `per_package` multiplies by the supplied package count; `per_weight`
multiplies by the sum of rounded billable weights. Both package bases require
package data for **every** scoped shipment; weight units must match the rate.
Unknown weight-dependent costs without package data must instead be supplied
as a bounded/unknown `once` charge. A fixed valid quote needs no package data.

Included charges remain visible but add nothing. Uncertain inclusion widens
the payable interval to `[0, null, supplied_high]`; it never assumes the fee
is already paid. A met shipping threshold waives that entire charge. No other
charges are waived implicitly. Record import deposits/taxes and inclusion
correctly to avoid counting both a deposit and its already-covered tax.

### Promotions and thresholds

Promotion keys: `benefit_id`, `kind`, `status`, `guaranteed`, `threshold`,
`conditions`, `allocations`, plus observation fields. `kind` is `discount`,
`rebate`, or `cashback`. `status` is `validated`, `conditional`, `rejected`, or
`unknown`; `guaranteed` is boolean; `conditions` is a string array. Cashback
also requires boolean `confirmed`; that field is forbidden on other kinds.

Only `validated` + `guaranteed: true` + a satisfied threshold reduces the total.
Cashback additionally requires `confirmed: true`. This consumes caller judgments
under the coupon contract; it does not upgrade evidence. Conditional/unconfirmed
benefits stay separate; rejected observations are retained with their reason.
Use one `benefit_id` per economic benefit across all kinds. Different IDs for
the same benefit cannot be detected. Guaranteed rebates reduce the skill's
net true total; they are not a claim that the checkout cash payment is lower.

`allocations` is a nonempty array of `{"shipment_id":"parcel","reduction":...}`.
Each shipment appears at most once per promotion. Reduction is **one** of:

- `{"amount":{"amount":"5","currency":"USD"}}`: an explicitly allocated total.
- `{"rate":"0.10"}`: fraction 0–1 of that shipment's remaining merchandise subtotal.

All discounts precede rebates/cashback. Discounts run in supplied order, so a
percentage uses the subtotal after earlier applied discounts. Rebate/cashback
rates use the final discounted merchandise subtotal, excluding separately
listed charges. For caps, item exclusions, embedded tax exclusions, retailer
line rounding, or other bases, supply settled fixed allocations instead. Applied
discounts cannot exceed a shipment's remaining subtotal. Benefits producing a
negative lower total are rejected; they are never silently clamped.

Threshold keys: `minimum` (Money), `basis` (`before_discount` or `after_discount`),
`shipment_ids` (nonempty unique eligible group IDs), `eligible_subtotal` (Money or
null), plus observation fields. Eligibility is inclusive: subtotal ≥ minimum.
When `eligible_subtotal` is null, sum the selected shipment merchandise subtotals;
tax/shipping charges are excluded. For discount thresholds, after means after
**earlier** applied discounts, before the current one; shipping and rebate
thresholds see all applied discounts. A nonnull eligible subtotal overrides
that sum with a supplied observation in the stated basis. Use it when embedded
tax or item exclusions make the merchandise sum unsuitable. It is not itself
subtracted or adjusted by discounts. Unknown threshold basis/eligibility is
unsupported: retain a conditional promotion or supply a shipping-fee interval.

### Package rules

Each package entry is a group of identical packed parcels. Keys:
`package_id`, `count`, `dimensions` (three positive decimal strings),
`dimension_unit` (`in` or `cm`), `dimension_increment`, `dimension_rounding`
(`none`, `ceil`, `half_up`, `half_even`), `actual_weight`, `weight_unit`
(`lb`, `kg`, `g`, `oz`), `divisor`, `weight_increment`, `weight_rounding`
(`ceil`, `half_up`, `half_even`), `dimensional_applies` (boolean), plus
observation fields. Increments and divisor are positive even when unused.

The supplied divisor must match length-unit³/weight-unit. No unit conversions
or carrier defaults are inferred. Round each dimension by its increment unless
mode is `none`, multiply to get volume, divide by the supplied divisor, then
round dimensional weight to its increment. Round actual weight to the same
increment. Billable weight is their maximum; multiply by parcel count only
after rounding each identical parcel. `dimensional_applies: false` uses actual
weight only; applicability thresholds remain a caller decision. The rational
DIM quotient is rounded directly with integer ratios from Decimal values, so
a repeating division cannot cause premature rounding. Oversize, fuel, remote,
residential, correction, and other surcharges are explicit separate charges.

### Returns

Return-cost keys: `component_id`, `kind` (descriptive string), `money` (Interval),
`responsibility` (string), plus observation fields. These are whole-basket
scenario costs; the calculator converts them but never adds them to purchase
totals or invents a return probability. Interpret nonreturnability and build
any ownership-cost scenario in prose.

## Arithmetic and output

Calculation uses Decimal with 128 significant digits; unintended precision loss
is rejected. Multiply quantities, allocate ordered discounts, convert each
currency, and sum components **without intermediate monetary rounding**.
Known point totals round once using the chosen mode/places. Uncertain interval
lows round down and highs round up; known expected values use the selected mode.
Nulls propagate independently. Unrounded totals and original currencies remain
available. If checkout rounds lines, tax, fees, or coupons earlier, supply the
observed rounded component/allocated amounts rather than assuming this policy
matches that checkout. Interval extrema are conservative sums of supplied
bounds, not a model of correlated fees or statistical confidence.

Output echoes version, `as_of`, output currency, rounding, and FX observations.
Offers retain input order. Each result includes:

- `offer_id`, `original` (the supplied offer), `true_total`, `unrounded_total`.
- `components`: unrounded converted amounts, add/subtract/excluded operation,
  evidence references, and charge scope/basis/multiplier/inclusion/threshold.
- `formula`: a generated sum of signed component IDs; excluded components are
  absent. It is data for inspection, not an executable expression.
- `promotion_results`: amount, numeric allocation by shipment, threshold result,
  applied flag, reason, and conditions; `excluded_conditional_benefits` collects
  unapplied benefits except rejected promotions. Alternatives are not a jointly
  validated stack and must not be summed into guaranteed savings.
- `packages`: rounded dimensions, exact DIM numerator/divisor, rounded weights,
  and per-package/total billable weight.
- `return_costs`, descriptive `unknowns`, and inclusion `warnings`.
- `applied_evidence_refs`: sorted references consumed by the calculation and its
  inclusion/exclusion decisions; this is not a source-validation attestation.

An unknown fee such as `{"low":"0","expected":null,"high":null,"currency":"USD"}`
preserves a lower-bound total while leaving expected/high totals unknown.
Do not present that lower bound as a guaranteed final checkout price.

## Worked example and fallback

The synthetic [example input](examples/calculation.json) supplies merchandise
100, a validated guaranteed discount 10, shipping 5, additional tax 18, and
unconfirmed cashback 3. Its true total is USD 113.00; cashback stays separate.
It is arithmetic test data, not retailer evidence.

Version 1 deliberately accepts a calculation projection, not the older NDJSON
or YAML record files directly. It supports point merchandise prices, bounded
additional costs, fixed/rate allocations, and deterministic thresholds/package
rules. Tiered rates, mixed eligibility within one percentage base, uncertain
merchandise prices, cyclic/post-current-discount thresholds, unknown promotion
magnitudes, and automatic tax/duty derivation require settled component amounts
or the existing manual formula with explicit gaps. Do not discard uncertainty
to make a request fit. Keep the evidence/consumer records and prose fallback.
