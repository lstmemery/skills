#!/usr/bin/env python3
"""Offline shopping arithmetic over explicit JSON observations."""

import argparse
from dataclasses import dataclass
from datetime import datetime
from decimal import (Decimal, DecimalException, Inexact, localcontext,
                     ROUND_HALF_UP, ROUND_HALF_EVEN, ROUND_FLOOR, ROUND_CEILING)
import json
from pathlib import Path
import re
import sys


MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}
DECIMAL_PATTERN = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,18})?\Z")
TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


class InputError(ValueError):
    """A rejected input; no offer results may be emitted."""


def require(condition, message):
    if not condition:
        raise InputError(message)


def shape(value, keys, where, optional=()):
    require(isinstance(value, dict), f"{where}: expected an object")
    required = set(keys.split())
    require(required <= value.keys(), f"{where}: missing required fields: {sorted(required - value.keys())}")
    require(value.keys() <= required | set(optional), f"{where}: unexpected fields")


def text_value(value, where):
    require(isinstance(value, str) and 0 < len(value) <= 2000 and value.strip() == value,
            f"{where}: expected a nonempty trimmed string, at most 2000 characters")
    return value


def array(value, where, nonempty=False):
    require(isinstance(value, list) and len(value) <= 1000 and (value or not nonempty),
            f"{where}: expected {'nonempty ' if nonempty else ''}array, at most 1000 entries")
    return value


def choice(value, choices, where):
    require(isinstance(value, str) and value in choices, f"{where}: unsupported value")


def integer(value, where, minimum=1, maximum=1000000):
    require(type(value) is int and minimum <= value <= maximum,
            f"{where}: expected integer {minimum}..{maximum}")


def boolean(value, where):
    require(type(value) is bool, f"{where}: expected boolean")


def number(value, where, positive=False):
    require(isinstance(value, str) and DECIMAL_PATTERN.fullmatch(value) is not None,
            f"{where}: expected nonnegative decimal string (up to 18 integer/fraction digits)")
    result = Decimal(value)
    require(not positive or result > 0, f"{where}: must be positive")
    return result


def currency(value, where):
    require(isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) is not None,
            f"{where}: expected three uppercase currency letters")


def timestamp(value, where):
    require(isinstance(value, str) and TIME_PATTERN.fullmatch(value) is not None,
            f"{where}: expected ISO timestamp with explicit UTC offset")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InputError(f"{where}: invalid timestamp") from error


def observation(value, where, as_of):
    require(timestamp(value["observed_at"], where + ".observed_at") <= as_of,
            f"{where}: observation is later than as_of")
    for ref in array(value["evidence_refs"], where + ".evidence_refs", nonempty=True):
        text_value(ref, where + ".evidence_refs[]")


def money(value, where, interval=False):
    keys = "low expected high currency" if interval else "amount currency"
    shape(value, keys, where)
    currency(value["currency"], where + ".currency")
    if not interval:
        number(value["amount"], where + ".amount")
        return
    low = number(value["low"], where + ".low")
    expected = None if value["expected"] is None else number(value["expected"], where + ".expected")
    high = None if value["high"] is None else number(value["high"], where + ".high")
    require(expected is None or low <= expected, f"{where}: expected below low")
    require(high is None or low <= high, f"{where}: high below low")
    require(expected is None or high is None or expected <= high, f"{where}: expected above high")


def unique(value, seen, where):
    text_value(value, where)
    require(value not in seen, f"{where}: duplicate identifier")
    seen.add(value)


def threshold_rule(value, shipment_ids, as_of):
    if value is None:
        return
    shape(value, "minimum basis shipment_ids eligible_subtotal observed_at evidence_refs", "threshold")
    money(value["minimum"], "threshold.minimum")
    choice(value["basis"], ("before_discount", "after_discount"), "threshold.basis")
    seen = set()
    for shipment_id in array(value["shipment_ids"], "threshold.shipment_ids", nonempty=True):
        unique(shipment_id, seen, "threshold.shipment_ids")
        choice(shipment_id, shipment_ids, "threshold.shipment_ids")
    if value["eligible_subtotal"] is not None:
        money(value["eligible_subtotal"], "threshold.eligible_subtotal")
    observation(value, "threshold", as_of)


def package_rule(package, seen, as_of):
    shape(package, "package_id count dimensions dimension_unit dimension_increment dimension_rounding "
          "actual_weight weight_unit divisor weight_increment weight_rounding dimensional_applies "
          "observed_at evidence_refs", "package")
    unique(package["package_id"], seen, "package.package_id")
    integer(package["count"], "package.count")
    dimensions = array(package["dimensions"], "package.dimensions")
    require(len(dimensions) == 3, "package.dimensions: require three packed dimensions")
    for dimension in dimensions:
        number(dimension, "package.dimensions[]", positive=True)
    for key in ("dimension_increment", "weight_increment", "divisor"):
        number(package[key], "package." + key, positive=True)
    number(package["actual_weight"], "package.actual_weight", positive=True)
    choice(package["dimension_unit"], ("in", "cm"), "package.dimension_unit")
    choice(package["weight_unit"], ("lb", "kg", "g", "oz"), "package.weight_unit")
    choice(package["dimension_rounding"], ("none", "ceil", "half_up", "half_even"), "package.dimension_rounding")
    choice(package["weight_rounding"], ("ceil", "half_up", "half_even"), "package.weight_rounding")
    boolean(package["dimensional_applies"], "package.dimensional_applies")
    observation(package, "package", as_of)


def validate(request):
    shape(request, "schema_version as_of currency rounding fx offers", "request")
    integer(request["schema_version"], "schema_version", maximum=1)
    as_of = timestamp(request["as_of"], "as_of")
    currency(request["currency"], "currency")
    shape(request["rounding"], "places mode", "rounding")
    integer(request["rounding"]["places"], "rounding.places", minimum=0, maximum=6)
    choice(request["rounding"]["mode"], MODES, "rounding.mode")
    fx_sources = set()
    for rate in array(request["fx"], "fx"):
        shape(rate, "from to rate observed_at evidence_refs", "fx")
        currency(rate["from"], "fx.from")
        currency(rate["to"], "fx.to")
        require(rate["from"] != rate["to"] and rate["to"] == request["currency"],
                "fx: require direct source-to-output rates, no identity rates")
        unique(rate["from"], fx_sources, "fx.from")
        number(rate["rate"], "fx.rate", positive=True)
        observation(rate, "fx", as_of)
    offer_ids = set()
    for offer in array(request["offers"], "offers", nonempty=True):
        shape(offer, "offer_id quantity shipments charges promotions return_costs unknowns", "offer")
        unique(offer["offer_id"], offer_ids, "offer.offer_id")
        integer(offer["quantity"], "offer.quantity")
        shipment_ids = set()
        package_ids = set()
        for shipment in array(offer["shipments"], "shipments", nonempty=True):
            shape(shipment, "shipment_id quantity price_basis item_price packages observed_at evidence_refs", "shipment")
            unique(shipment["shipment_id"], shipment_ids, "shipment.shipment_id")
            require(shipment["shipment_id"] != "order", "shipment_id: 'order' is reserved")
            integer(shipment["quantity"], "shipment.quantity")
            choice(shipment["price_basis"], ("per_item", "subtotal"), "shipment.price_basis")
            money(shipment["item_price"], "shipment.item_price")
            observation(shipment, "shipment", as_of)
            for package in array(shipment["packages"], "shipment.packages"):
                package_rule(package, package_ids, as_of)
        require(sum(s["quantity"] for s in offer["shipments"]) == offer["quantity"],
                "offer.quantity: must equal shipment quantities")
        component_ids = set()
        for charge in array(offer["charges"], "charges"):
            shape(charge, "component_id kind scope basis money included threshold observed_at evidence_refs", "charge",
                  optional=("weight_unit",))
            unique(charge["component_id"], component_ids, "charge.component_id")
            choice(charge["kind"], ("shipping", "surcharge", "duty", "tax", "brokerage", "membership"), "charge.kind")
            choice(charge["scope"], shipment_ids | {"order"}, "charge.scope")
            choice(charge["basis"], ("once", "per_item", "per_package", "per_weight"), "charge.basis")
            require(("weight_unit" in charge) == (charge["basis"] == "per_weight"),
                    "charge.weight_unit: required only for per_weight")
            if charge["basis"] == "per_weight":
                choice(charge["weight_unit"], ("lb", "kg", "g", "oz"), "charge.weight_unit")
            require(type(charge["included"]) is bool or charge["included"] == "unknown",
                    "charge.included: expected boolean or 'unknown'")
            threshold_rule(charge["threshold"], shipment_ids, as_of)
            require(charge["threshold"] is None or charge["kind"] == "shipping",
                    "charge.threshold: only shipping waivers are supported")
            money(charge["money"], "charge.money", interval=True)
            observation(charge, "charge", as_of)
        benefit_ids = set()
        seen_rebate = False
        for promotion in array(offer["promotions"], "promotions"):
            shape(promotion, "benefit_id kind status guaranteed threshold conditions allocations observed_at evidence_refs",
                  "promotion", optional=("confirmed",))
            unique(promotion["benefit_id"], benefit_ids, "promotion.benefit_id")
            choice(promotion["kind"], ("discount", "rebate", "cashback"), "promotion.kind")
            require(not (seen_rebate and promotion["kind"] == "discount"),
                    "promotions: discounts must precede rebates/cashback")
            seen_rebate = seen_rebate or promotion["kind"] != "discount"
            choice(promotion["status"], ("validated", "conditional", "rejected", "unknown"), "promotion.status")
            boolean(promotion["guaranteed"], "promotion.guaranteed")
            require(("confirmed" in promotion) == (promotion["kind"] == "cashback"),
                    "promotion.confirmed: required only for cashback")
            if "confirmed" in promotion:
                boolean(promotion["confirmed"], "promotion.confirmed")
            threshold_rule(promotion["threshold"], shipment_ids, as_of)
            for condition in array(promotion["conditions"], "promotion.conditions"):
                text_value(condition, "promotion.conditions[]")
            observation(promotion, "promotion", as_of)
            allocated = set()
            for allocation in array(promotion["allocations"], "promotion.allocations", nonempty=True):
                shape(allocation, "shipment_id reduction", "allocation")
                unique(allocation["shipment_id"], allocated, "allocation.shipment_id")
                choice(allocation["shipment_id"], shipment_ids, "allocation.shipment_id")
                reduction = allocation["reduction"]
                require(isinstance(reduction, dict) and set(reduction) in ({"amount"}, {"rate"}),
                        "allocation.reduction: require exactly one of amount or rate")
                if "amount" in reduction:
                    money(reduction["amount"], "allocation.reduction.amount")
                else:
                    require(number(reduction["rate"], "allocation.reduction.rate") <= 1,
                            "allocation.reduction.rate: must be a fraction from 0 to 1")
        return_ids = set()
        for cost in array(offer["return_costs"], "return_costs"):
            shape(cost, "component_id kind money responsibility observed_at evidence_refs", "return_cost")
            unique(cost["component_id"], return_ids, "return_cost.component_id")
            text_value(cost["kind"], "return_cost.kind")
            text_value(cost["responsibility"], "return_cost.responsibility")
            money(cost["money"], "return_cost.money", interval=True)
            observation(cost, "return_cost", as_of)
        for unknown in array(offer["unknowns"], "unknowns"):
            text_value(unknown, "unknowns[]")


@dataclass(frozen=True)
class Bounds:
    low: Decimal
    expected: Decimal | None
    high: Decimal | None

    @classmethod
    def point(cls, value):
        return cls(value, value, value)

    def add(self, other):
        return Bounds(self.low + other.low,
                      None if self.expected is None or other.expected is None else self.expected + other.expected,
                      None if self.high is None or other.high is None else self.high + other.high)

    def scale(self, factor):
        if factor == 0:
            return Bounds.point(Decimal(0))
        return Bounds(self.low * factor,
                      None if self.expected is None else self.expected * factor,
                      None if self.high is None else self.high * factor)

    def json(self, currency_code, rounding=None):
        values = {"low": self.low, "expected": self.expected, "high": self.high}
        output = {"currency": currency_code}
        for name, value in values.items():
            if value is not None and rounding is not None:
                with localcontext() as context:
                    context.traps[Inexact] = False
                    mode = MODES[rounding["mode"]]
                    if not self.low == self.expected == self.high:
                        mode = {"low": ROUND_FLOOR, "high": ROUND_CEILING}.get(name, mode)
                    value = value.quantize(Decimal(1).scaleb(-rounding["places"]),
                                           rounding=mode)
            output[name] = None if value is None else format(value, "f")
        return output


class Calculator:
    def __init__(self, request):
        self.request = request
        self.currency = request["currency"]
        self.fx = {rate["from"]: rate for rate in request["fx"]}
        self.evidence = set()

    def factor(self, source):
        if source == self.currency:
            return Decimal(1)
        require(source in self.fx, f"fx: missing direct rate for {source} to {self.currency}")
        self.evidence.update(self.fx[source]["evidence_refs"])
        return Decimal(self.fx[source]["rate"])

    def exact(self, amount):
        return Decimal(amount["amount"]) * self.factor(amount["currency"])

    def interval(self, amount):
        return Bounds(*(None if amount[key] is None else Decimal(amount[key])
                        for key in ("low", "expected", "high"))).scale(self.factor(amount["currency"]))

    def threshold(self, rule, before, after):
        if rule is None:
            return None
        self.evidence.update(rule["evidence_refs"])
        if rule["eligible_subtotal"] is not None:
            eligible = self.exact(rule["eligible_subtotal"])
        else:
            subtotals = before if rule["basis"] == "before_discount" else after
            eligible = sum((subtotals[s] for s in rule["shipment_ids"]), Decimal(0))
        minimum = self.exact(rule["minimum"])
        return {"eligible_subtotal": format(eligible, "f"), "minimum": format(minimum, "f"),
                "currency": self.currency, "basis": rule["basis"], "met": eligible >= minimum,
                "source": "supplied" if rule["eligible_subtotal"] is not None else "shipment_subtotals"}

    def package(self, package, shipment_id):
        self.evidence.update(package["evidence_refs"])
        dimensions = [Decimal(value) for value in package["dimensions"]]
        if package["dimension_rounding"] != "none":
            dimensions = [round_ratio(value, Decimal(1), Decimal(package["dimension_increment"]),
                                      package["dimension_rounding"]) for value in dimensions]
        volume = dimensions[0] * dimensions[1] * dimensions[2]
        increment = Decimal(package["weight_increment"])
        actual = round_ratio(Decimal(package["actual_weight"]), Decimal(1), increment, package["weight_rounding"])
        dimensional = (round_ratio(volume, Decimal(package["divisor"]), increment, package["weight_rounding"])
                       if package["dimensional_applies"] else Decimal(0))
        billable = max(actual, dimensional)
        return {"package_id": package["package_id"], "shipment_id": shipment_id,
                "count": package["count"], "rounded_dimensions": [format(d, "f") for d in dimensions],
                "dimension_unit": package["dimension_unit"], "weight_unit": package["weight_unit"],
                "dimensional_expression": {"numerator": format(volume, "f"), "divisor": package["divisor"],
                                           "applied": package["dimensional_applies"]},
                "actual_weight_rounded": format(actual, "f"), "dimensional_weight_rounded": format(dimensional, "f"),
                "billable_weight_each": format(billable, "f"),
                "billable_weight_total": format(billable * package["count"], "f")}

    def offer(self, offer):
        self.evidence = set()
        total = Bounds.point(Decimal(0))
        components = []
        warnings = []
        unknowns = list(offer["unknowns"])
        before = {}
        promotion_results = []
        packages = [self.package(p, s["shipment_id"]) for s in offer["shipments"] for p in s["packages"]]

        def component(identifier, kind, amount, refs, operation="add"):
            nonlocal total
            self.evidence.update(refs)
            components.append({"component_id": identifier, "kind": kind,
                               "operation": operation, "amount": amount.json(self.currency),
                               "evidence_refs": refs})
            if operation == "subtract":
                total = total.add(Bounds.point(-amount.low))
            elif operation == "add":
                total = total.add(amount)

        for shipment in offer["shipments"]:
            multiplier = shipment["quantity"] if shipment["price_basis"] == "per_item" else 1
            value = self.exact(shipment["item_price"]) * multiplier
            before[shipment["shipment_id"]] = value
            component("item:" + shipment["shipment_id"], "merchandise", Bounds.point(value), shipment["evidence_refs"])
        after = dict(before)
        for promotion in offer["promotions"]:
            threshold_result = self.threshold(promotion["threshold"], before, after)
            reason = "guaranteed"
            if promotion["status"] != "validated":
                reason = "status_" + promotion["status"]
            elif not promotion["guaranteed"]:
                reason = "not_guaranteed"
            elif promotion["kind"] == "cashback" and not promotion["confirmed"]:
                reason = "cashback_unconfirmed"
            elif threshold_result is not None and not threshold_result["met"]:
                reason = "threshold_not_met"
            applied = reason == "guaranteed"
            allocations = {}
            for allocation in promotion["allocations"]:
                shipment_id = allocation["shipment_id"]
                reduction = allocation["reduction"]
                value = (self.exact(reduction["amount"]) if "amount" in reduction
                         else after[shipment_id] * Decimal(reduction["rate"]))
                allocations[shipment_id] = value
            value = sum(allocations.values(), Decimal(0))
            if applied and promotion["kind"] == "discount":
                for shipment_id, reduction in allocations.items():
                    require(reduction <= after[shipment_id], "promotion: discount exceeds remaining shipment subtotal")
                    after[shipment_id] -= reduction
            promotion_results.append({"benefit_id": promotion["benefit_id"], "applied": applied,
                                      "reason": reason, "kind": promotion["kind"],
                                      "conditions": promotion["conditions"],
                                      "allocations": {key: format(amount, "f") for key, amount in allocations.items()},
                                      "amount": Bounds.point(value).json(self.currency),
                                      "threshold": threshold_result, "evidence_refs": promotion["evidence_refs"]})
            component("benefit:" + promotion["benefit_id"], promotion["kind"], Bounds.point(value),
                      promotion["evidence_refs"], "subtract" if applied else "excluded")
        for charge in offer["charges"]:
            amount = self.interval(charge["money"])
            multiplier = Decimal(1)
            scoped_shipments = [s for s in offer["shipments"] if charge["scope"] in ("order", s["shipment_id"])]
            if charge["basis"] == "per_item":
                multiplier = Decimal(sum(s["quantity"] for s in scoped_shipments))
            if charge["basis"] in ("per_package", "per_weight"):
                require(all(s["packages"] for s in scoped_shipments), "charge: package data required for every scoped shipment")
                scoped_packages = [p for p in packages if charge["scope"] in ("order", p["shipment_id"])]
                if charge["basis"] == "per_package":
                    multiplier = Decimal(sum(p["count"] for p in scoped_packages))
                else:
                    require(all(p["weight_unit"] == charge["weight_unit"] for p in scoped_packages),
                            "charge: package and rate weight units must match")
                    multiplier = sum((Decimal(p["billable_weight_total"]) for p in scoped_packages), Decimal(0))
            amount = amount.scale(multiplier)
            operation = "add"
            if charge["included"] is True:
                operation = "excluded"
            if charge["included"] == "unknown":
                amount = Bounds(Decimal(0), None, amount.high)
            threshold_result = self.threshold(charge["threshold"], before, after)
            if threshold_result is not None and threshold_result["met"]:
                operation = "excluded"
            if operation == "add" and charge["included"] == "unknown":
                warnings.append(f"{charge['component_id']}: inclusion unknown; expected total unknown")
                unknowns.append(f"{charge['component_id']}: inclusion unknown")
            component("charge:" + charge["component_id"], charge["kind"], amount,
                      charge["evidence_refs"], operation)
            components[-1]["threshold"] = threshold_result
            components[-1].update({"basis": charge["basis"], "scope": charge["scope"],
                                   "multiplier": format(multiplier, "f"), "included": charge["included"]})
            if operation == "add" and (amount.expected is None or amount.high is None):
                unknowns.append(f"{charge['component_id']}: expected and/or upper fee unknown")
        return_costs = [{"component_id": cost["component_id"], "kind": cost["kind"],
                         "responsibility": cost["responsibility"], "evidence_refs": cost["evidence_refs"],
                         "amount": self.interval(cost["money"]).json(self.currency)}
                        for cost in offer["return_costs"]]
        excluded = [p for p in promotion_results if not p["applied"] and p["reason"] != "status_rejected"]
        require(total.low >= 0, "offer: guaranteed benefits exceed minimum payable total")
        return {"offer_id": offer["offer_id"], "original": offer, "components": components,
                "formula": {"operation": "sum", "currency": self.currency,
                            "terms": [{"component_id": c["component_id"], "sign": -1 if c["operation"] == "subtract" else 1}
                                      for c in components if c["operation"] != "excluded"]},
                "true_total": total.json(self.currency, self.request["rounding"]),
                "unrounded_total": total.json(self.currency),
                "applied_evidence_refs": sorted(self.evidence), "warnings": warnings,
                "promotion_results": promotion_results, "unknowns": unknowns, "packages": packages,
                "excluded_conditional_benefits": excluded, "return_costs": return_costs}


def round_ratio(numerator, denominator, increment, mode):
    # Integer ratios avoid rounding a recurring DIM quotient before the carrier's rounding step.
    n_top, n_bottom = numerator.as_integer_ratio()
    d_top, d_bottom = (denominator * increment).as_integer_ratio()
    quotient, remainder = divmod(n_top * d_bottom, n_bottom * d_top)
    divisor = n_bottom * d_top
    if mode == "ceil":
        quotient += remainder > 0
    elif remainder * 2 > divisor or (remainder * 2 == divisor and (mode == "half_up" or quotient % 2)):
        quotient += 1
    return Decimal(quotient) * increment


def calculate(request):
    calculator = Calculator(request)
    return {"schema_version": 1, "as_of": request["as_of"], "currency": request["currency"],
            "rounding": request["rounding"], "fx": request["fx"],
            "offers": [calculator.offer(offer) for offer in request["offers"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        request = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant,
                             parse_int=json_integer)
        with localcontext() as context:
            context.prec = 128
            context.traps[Inexact] = True
            validate(request)
            result = calculate(request)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    except (InputError, json.JSONDecodeError, UnicodeError, OSError, DecimalException, RecursionError) as error:
        print(json.dumps({"error": "invalid_input", "message": str(error) or type(error).__name__}), file=sys.stderr)
        return 2
    return 0


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON: duplicate object key")
        result[key] = value
    return result


def reject_constant(value):
    raise InputError("JSON: nonfinite number is not allowed")


def json_integer(value):
    require(len(value.lstrip("-")) <= 7, "JSON: integer exceeds supported count/version range")
    return int(value)


if __name__ == "__main__":
    sys.exit(main())
