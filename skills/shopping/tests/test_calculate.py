"""Public CLI tests with independent worked results and synthetic observations."""

import json
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calculate.py"
AS_OF = "2026-09-20T12:00:00Z"
OBSERVATION = {"observed_at": "2026-09-20T10:00:00Z", "evidence_refs": ["synthetic-quote"]}


def money(amount, currency="USD"):
    return {"amount": amount, "currency": currency}


def bounds(low, expected, high, currency="USD"):
    return {"low": low, "expected": expected, "high": high, "currency": currency}


def charge(identifier, amount, kind="shipping", **changes):
    result = {"component_id": identifier, "kind": kind, "scope": "order", "basis": "once",
              "money": bounds(amount, amount, amount), "included": False, "threshold": None,
              **OBSERVATION}
    result.update(changes)
    return result


def promotion(identifier, amount, **changes):
    result = {"benefit_id": identifier, "kind": "discount", "status": "validated",
              "guaranteed": True, "threshold": None, "conditions": [],
              "allocations": [{"shipment_id": "parcel", "reduction": {"amount": money(amount)}}],
              **OBSERVATION}
    result.update(changes)
    return result


def threshold(amount, basis="after_discount", shipments=None):
    return {"minimum": money(amount), "basis": basis,
            "shipment_ids": ["parcel"] if shipments is None else shipments,
            "eligible_subtotal": None, **OBSERVATION}


def request():
    return {"schema_version": 1, "as_of": AS_OF, "currency": "USD",
            "rounding": {"places": 2, "mode": "half_up"}, "fx": [],
            "offers": [{"offer_id": "synthetic-offer", "quantity": 1,
                        "shipments": [{"shipment_id": "parcel", "quantity": 1,
                                       "price_basis": "per_item", "item_price": money("100"),
                                       "packages": [], **OBSERVATION}],
                        "charges": [], "promotions": [], "return_costs": [], "unknowns": []}]}


class CalculatorCLI(unittest.TestCase):
    def run_cli(self, payload, *, raw=False, stdin=False):
        encoded = payload if raw else json.dumps(payload)
        if stdin:
            return subprocess.run([sys.executable, "-B", str(SCRIPT), "--input", "-"],
                                  input=encoded, text=True, capture_output=True, timeout=10)
        with tempfile.TemporaryDirectory(prefix="shopping-test-", dir=Path.cwd()) as directory:
            path = Path(directory) / "input.json"
            path.write_text(encoded, encoding="utf-8")
            return subprocess.run([sys.executable, "-B", str(SCRIPT), "--input", str(path)],
                                  text=True, capture_output=True, timeout=10)

    def calculate(self, payload):
        result = self.run_cli(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["offers"][0]

    def assert_invalid(self, payload, *, raw=False):
        result = self.run_cli(payload, raw=raw)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        diagnostic = json.loads(result.stderr)
        self.assertEqual(diagnostic["error"], "invalid_input")
        self.assertTrue(diagnostic["message"])

    def test_audit_worked_total(self):
        payload = request()
        offer = payload["offers"][0]
        offer["promotions"] = [promotion("coupon", "10")]
        offer["charges"] = [charge("delivery", "5"), charge("sales-tax", "18", "tax")]
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("113.00", "113.00", "113.00"))

    def test_fractional_money_and_explicit_rounding(self):
        payload = request()
        offer = payload["offers"][0]
        offer["quantity"] = offer["shipments"][0]["quantity"] = 3
        offer["shipments"][0]["item_price"] = money("0.335")
        self.assertEqual(self.calculate(payload)["true_total"], bounds("1.01", "1.01", "1.01"))
        payload["rounding"]["mode"] = "half_even"
        self.assertEqual(self.calculate(payload)["true_total"], bounds("1.00", "1.00", "1.00"))

    def test_malformed_input_is_rejected_without_partial_results(self):
        cases = ["{", "[]", '{"schema_version":1,"schema_version":1}', '{"amount":NaN}']
        for value in cases:
            with self.subTest(value=value):
                self.assert_invalid(value, raw=True)
        for field, value in [("quantity", 0), ("quantity", True), ("quantity", 1.5),
                             ("quantity", "1"), ("unexpected", "ignored?"),
                             ("charges", None)]:
            payload = request()
            payload["offers"][0][field] = value
            with self.subTest(field=field, value=value):
                self.assert_invalid(payload)
        for value in [0.1, "NaN", "Infinity", "-1", "1e3", True, None, "1,000"]:
            payload = request()
            payload["offers"][0]["shipments"][0]["item_price"]["amount"] = value
            with self.subTest(amount=value):
                self.assert_invalid(payload)
        for value in ["2026-09-20", "yesterday", "2026-09-20T12:00:00"]:
            payload = request()
            payload["as_of"] = value
            with self.subTest(as_of=value):
                self.assert_invalid(payload)
        payload = request()
        del payload["offers"][0]["charges"]
        self.assert_invalid(payload)
        payload = request()
        payload["offers"].append(copy.deepcopy(payload["offers"][0]))
        payload["offers"][1]["offer_id"] = "second"
        payload["offers"][1]["quantity"] = -1
        self.assert_invalid(payload)

    def test_mixed_currencies_use_explicit_fx_and_preserve_originals(self):
        payload = request()
        payload["offers"][0]["shipments"][0]["item_price"] = money("10", "EUR")
        payload["offers"][0]["charges"] = [charge("shipping", "2")]
        payload["fx"] = [{"from": "EUR", "to": "USD", "rate": "1.25", **OBSERVATION}]
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("14.50", "14.50", "14.50"))
        self.assertEqual(result["original"]["shipments"][0]["item_price"], money("10", "EUR"))
        self.assertEqual(result["applied_evidence_refs"], ["synthetic-quote"])
        payload["fx"] = []
        self.assert_invalid(payload)
        payload["fx"] = [{"from": "USD", "to": "EUR", "rate": "0.8", **OBSERVATION}]
        self.assert_invalid(payload)
        payload["fx"][0].update({"from": "EUR", "to": "USD", "rate": "1.25",
                                 "observed_at": "2026-09-21T00:00:00Z"})
        self.assert_invalid(payload)

    def test_included_tax_and_uncertain_inclusion(self):
        payload = request()
        offer = payload["offers"][0]
        offer["charges"] = [charge("tax", "20", "tax", included=True),
                            charge("deposit", "5", "duty", included=True)]
        self.assertEqual(self.calculate(payload)["true_total"], bounds("100.00", "100.00", "100.00"))
        offer["charges"][0]["included"] = "unknown"
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("100.00", None, "120.00"))
        self.assertTrue(any("inclusion" in warning for warning in result["warnings"]))

    def test_split_shipments_preserve_per_order_and_per_item_bases(self):
        payload = request()
        offer = payload["offers"][0]
        offer["quantity"] = 3
        shipment = offer["shipments"][0]
        shipment.update({"quantity": 2, "item_price": money("20"), "price_basis": "subtotal"})
        second = copy.deepcopy(shipment)
        second.update({"shipment_id": "second", "quantity": 1, "item_price": money("30")})
        offer["shipments"].append(second)
        offer["charges"] = [charge("order-fee", "5"),
                            charge("parcel-fee", "3", scope="parcel"),
                            charge("second-fee", "4", scope="second"),
                            charge("packing", "2", "surcharge", basis="per_item")]
        self.assertEqual(self.calculate(payload)["true_total"], bounds("68.00", "68.00", "68.00"))
        offer["quantity"] = 4
        self.assert_invalid(payload)

    def test_ordered_discounts_and_threshold_shipping(self):
        payload = request()
        offer = payload["offers"][0]
        offer["shipments"][0]["item_price"] = money("40")
        offer["promotions"] = [promotion("ten-percent", "0"), promotion("five", "5")]
        offer["promotions"][0]["allocations"][0]["reduction"] = {"rate": "0.10"}
        offer["promotions"][1]["threshold"] = threshold("36")
        offer["charges"] = [charge("delivery", "7", threshold=threshold("35")),
                            charge("tax", "20", "tax")]
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("58.00", "58.00", "58.00"))
        self.assertEqual([p["amount"]["expected"] for p in result["promotion_results"]], ["4.00", "5"])
        offer["charges"][0]["threshold"]["basis"] = "before_discount"
        self.assertEqual(self.calculate(payload)["true_total"], bounds("51.00", "51.00", "51.00"))
        offer["promotions"][1]["threshold"]["minimum"] = money("37")
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("56.00", "56.00", "56.00"))
        self.assertFalse(result["promotion_results"][1]["applied"])
        self.assertEqual(result["promotion_results"][1]["reason"], "threshold_not_met")

    def test_unknown_fees_and_outward_interval_rounding(self):
        payload = request()
        offer = payload["offers"][0]
        offer["charges"] = [charge("broker", "0", "brokerage", money=bounds("2.001", None, None))]
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("102.00", None, None))
        self.assertIn("broker", " ".join(result["unknowns"]))
        offer["charges"][0]["money"] = bounds("2.001", "3.125", "4.001")
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("102.00", "103.13", "104.01"))
        self.assertEqual(result["unrounded_total"], bounds("102.001", "103.125", "104.001"))
        offer["charges"][0]["money"] = bounds("2", None, "4")
        self.assertEqual(self.calculate(payload)["true_total"], bounds("102.00", None, "104.00"))
        offer["charges"][0]["money"] = bounds("2", "3", None)
        self.assertEqual(self.calculate(payload)["true_total"], bounds("102.00", "103.00", None))

    def test_dimensional_rounding_applies_per_package_then_sums(self):
        payload = request()
        shipment = payload["offers"][0]["shipments"][0]
        shipment["packages"] = [{"package_id": "box", "count": 2,
                                 "dimensions": ["10.1", "10", "10"], "dimension_unit": "in",
                                 "dimension_increment": "1", "dimension_rounding": "ceil",
                                 "actual_weight": "6.1", "weight_unit": "lb", "divisor": "139",
                                 "weight_increment": "1", "weight_rounding": "ceil",
                                 "dimensional_applies": True, **OBSERVATION}]
        payload["offers"][0]["charges"] = [charge("weight", "0.5", basis="per_weight", weight_unit="lb"),
                                           charge("box-fee", "1", "surcharge", basis="per_package")]
        result = self.calculate(payload)
        # Packed 11 x 10 x 10 / 139 is above 7; ceiling gives 8 lb per box.
        self.assertEqual(result["true_total"], bounds("110.00", "110.00", "110.00"))
        package = result["packages"][0]
        self.assertEqual(package["billable_weight_each"], "8")
        self.assertEqual(package["billable_weight_total"], "16")
        shipment["packages"][0].update({"dimensional_applies": False, "weight_increment": "0.5"})
        self.assertEqual(self.calculate(payload)["true_total"], bounds("108.50", "108.50", "108.50"))
        shipment["packages"][0]["divisor"] = "0"
        self.assert_invalid(payload)

    def test_conditional_cashback_and_returns_are_separate(self):
        payload = request()
        offer = payload["offers"][0]
        offer["promotions"] = [promotion("terms-only", "20", status="conditional", guaranteed=False),
                                promotion("cashback", "10", kind="cashback", confirmed=False,
                                          conditions=["portal attribution must succeed"])]
        offer["return_costs"] = [{"component_id": "return-label", "kind": "return_shipping",
                                  "money": bounds("8", None, "20"), "responsibility": "buyer if changed mind",
                                  **OBSERVATION}]
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("100.00", "100.00", "100.00"))
        self.assertEqual([p["benefit_id"] for p in result["excluded_conditional_benefits"]],
                         ["terms-only", "cashback"])
        self.assertEqual(result["return_costs"][0]["amount"], bounds("8", None, "20"))
        offer["promotions"][1]["confirmed"] = True
        self.assertEqual(self.calculate(payload)["true_total"], bounds("90.00", "90.00", "90.00"))

    def test_duplicate_benefits_overdiscounts_and_invalid_rules_fail(self):
        for promotions in [[promotion("same", "10"), promotion("same", "10", kind="rebate")],
                           [promotion("too-much", "101")],
                           [promotion("one", "60"), promotion("two", "60")],
                           [promotion("rebate", "101", kind="rebate")],
                           [promotion("rebate", "10", kind="rebate"), promotion("late", "10")]]:
            payload = request()
            payload["offers"][0]["promotions"] = promotions
            with self.subTest(promotions=promotions):
                self.assert_invalid(payload)
        for interval in [bounds("3", "2", "4"), bounds("3", None, "2"), bounds("1", "4", "3")]:
            payload = request()
            payload["offers"][0]["charges"] = [charge("bad-fee", "0", money=interval)]
            with self.subTest(interval=interval):
                self.assert_invalid(payload)
        payload = request()
        payload["offers"][0]["charges"] = [charge("shipping", "5", threshold=threshold("20", basis="unknown"))]
        self.assert_invalid(payload)
        payload["offers"][0]["charges"][0]["threshold"] = threshold("20", shipments=["missing"])
        self.assert_invalid(payload)

    def test_file_and_stdin_are_deterministic_and_formula_is_auditable(self):
        payload = request()
        payload["offers"][0]["promotions"] = [promotion("coupon", "10")]
        payload["offers"][0]["charges"] = [charge("delivery", "5")]
        from_file = self.run_cli(payload)
        from_stdin = self.run_cli(payload, stdin=True)
        self.assertEqual(from_stdin.returncode, 0, from_stdin.stderr)
        self.assertEqual(from_file.stdout, from_stdin.stdout)
        self.assertEqual(from_file.stdout, self.run_cli(payload).stdout)
        result = json.loads(from_file.stdout)["offers"][0]
        self.assertEqual(result["formula"], {"currency": "USD", "operation": "sum",
                         "terms": [{"component_id": "item:parcel", "sign": 1},
                                   {"component_id": "benefit:coupon", "sign": -1},
                                   {"component_id": "charge:delivery", "sign": 1}]})

    def test_oversized_json_integer_has_structured_diagnostic(self):
        self.assert_invalid('{"schema_version":' + '9' * 5000 + '}', raw=True)

    def test_discount_allocation_and_eligible_thresholds_stay_with_shipments(self):
        payload = request()
        offer = payload["offers"][0]
        offer["quantity"] = 2
        offer["shipments"][0]["item_price"] = money("40")
        second = copy.deepcopy(offer["shipments"][0])
        second.update({"shipment_id": "second", "item_price": money("20")})
        offer["shipments"].append(second)
        offer["promotions"] = [promotion("first-only", "10")]
        offer["charges"] = [charge("first-shipping", "5", scope="parcel", threshold=threshold("35")),
                            charge("second-shipping", "7", scope="second", threshold=threshold("15", shipments=["second"]))]
        self.assertEqual(self.calculate(payload)["true_total"], bounds("55.00", "55.00", "55.00"))
        offer["charges"][1]["threshold"]["eligible_subtotal"] = money("10")
        self.assertEqual(self.calculate(payload)["true_total"], bounds("62.00", "62.00", "62.00"))

    def test_zero_and_three_decimal_output_currencies(self):
        for code, places, expected in [("JPY", 0, "101"), ("KWD", 3, "100.555")]:
            with self.subTest(currency=code):
                payload = request()
                payload["currency"] = code
                payload["rounding"]["places"] = places
                payload["offers"][0]["shipments"][0]["item_price"] = money("100.555", code)
                self.assertEqual(self.calculate(payload)["true_total"], bounds(expected, expected, expected, code))

    def test_rejected_and_unknown_promotions_never_reduce_price(self):
        payload = request()
        payload["offers"][0]["promotions"] = [promotion("rejected", "10", status="rejected"),
                                               promotion("unknown", "20", status="unknown"),
                                               promotion("not-guaranteed", "30", guaranteed=False)]
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("100.00", "100.00", "100.00"))
        self.assertEqual([p["reason"] for p in result["promotion_results"]],
                         ["status_rejected", "status_unknown", "not_guaranteed"])

    def test_package_data_and_units_are_required_for_weight_charges(self):
        payload = request()
        payload["offers"][0]["charges"] = [charge("weight", "2", basis="per_weight", weight_unit="kg")]
        self.assert_invalid(payload)
        payload["offers"][0]["shipments"][0]["packages"] = [
            {"package_id": "box", "count": 1, "dimensions": ["1", "1", "1"], "dimension_unit": "in",
             "dimension_increment": "1", "dimension_rounding": "none", "actual_weight": "10.5",
             "weight_unit": "lb", "divisor": "139", "weight_increment": "1", "weight_rounding": "half_even",
             "dimensional_applies": True, **OBSERVATION}]
        self.assert_invalid(payload)
        payload["offers"][0]["charges"][0]["weight_unit"] = "lb"
        self.assertEqual(self.calculate(payload)["true_total"], bounds("120.00", "120.00", "120.00"))
        payload["offers"][0]["shipments"][0]["packages"][0]["weight_rounding"] = "half_up"
        self.assertEqual(self.calculate(payload)["true_total"], bounds("122.00", "122.00", "122.00"))

    def test_included_unknown_fee_and_waived_unknown_shipping_do_not_pollute_total(self):
        payload = request()
        payload["offers"][0]["charges"] = [charge("tax", "0", "tax", included=True, money=bounds("0", None, None)),
                                           charge("shipping", "0", money=bounds("0", None, None), threshold=threshold("100"))]
        result = self.calculate(payload)
        self.assertEqual(result["true_total"], bounds("100.00", "100.00", "100.00"))
        self.assertEqual(result["formula"]["terms"], [{"component_id": "item:parcel", "sign": 1}])

    def test_missing_file_and_invalid_cli_fail_without_output(self):
        for args in [["--input", "missing-shopping-input.json"], []]:
            result = subprocess.run([sys.executable, "-B", str(SCRIPT), *args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertTrue(result.stderr)

    def test_documented_example_has_the_worked_total_and_separate_cashback(self):
        example = SCRIPT.parent.parent / "examples" / "calculation.json"
        result = self.calculate(json.loads(example.read_text(encoding="utf-8")))
        self.assertEqual(result["true_total"], bounds("113.00", "113.00", "113.00"))
        self.assertEqual(result["excluded_conditional_benefits"][0]["amount"], bounds("3", "3", "3"))


if __name__ == "__main__":
    unittest.main()
