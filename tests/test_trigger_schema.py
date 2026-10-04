"""Tests for the shared trigger-fixture schema validator.

covers the strict kebab-case id rule (no leading, trailing, or doubled
hyphens) and the rest of the schema documented in CONTRIBUTING.md, plus the
coverage checker's use of the shared validator (single source of truth with
skills/shopping/tests/run_triggers.py).
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import check_skill_coverage
import trigger_schema

BASE = Path(__file__).resolve().parents[1]


def valid_case(case_id: str = "valid-id") -> dict[str, str]:
    return {"id": case_id, "activation": "yes", "prompt": "a prompt"}


def valid_payload() -> dict[str, object]:
    return {
        "version": 1,
        "cases": [
            valid_case("positive-id"),
            {"id": "negative-id", "activation": "no", "prompt": "another prompt"},
        ],
    }


class KebabCaseIdTests(unittest.TestCase):
    def test_valid_ids_accepted(self) -> None:
        for case_id in ("a", "route-to-deep-research", "id2-with-3-digits"):
            with self.subTest(case_id=case_id):
                self.assertEqual(trigger_schema.schema_errors(valid_payload()), [])

    def test_malformed_ids_rejected(self) -> None:
        for case_id in ("--", "-bad", "bad-", "two--words", "-lead--trail-"):
            with self.subTest(case_id=case_id):
                payload = valid_payload()
                payload["cases"] = [valid_case(case_id), valid_payload()["cases"][1]]
                errors = trigger_schema.schema_errors(payload)
                self.assertTrue(
                    any("invalid id" in error for error in errors),
                    msg=f"expected invalid-id error for {case_id!r}: {errors}",
                )

    def test_non_kebab_characters_rejected(self) -> None:
        for case_id in ("Bad-id", "bad_id", "bad.id", "", "yes "):
            with self.subTest(case_id=case_id):
                payload = valid_payload()
                payload["cases"] = [valid_case(case_id)]
                errors = trigger_schema.schema_errors(payload)
                self.assertTrue(
                    any("invalid id" in error for error in errors),
                    msg=f"expected invalid-id error for {case_id!r}: {errors}",
                )


class SchemaRuleTests(unittest.TestCase):
    def test_valid_payload_has_no_errors(self) -> None:
        self.assertEqual(trigger_schema.schema_errors(valid_payload()), [])

    def test_non_object_payload_rejected(self) -> None:
        self.assertEqual(len(trigger_schema.schema_errors([1, 2])), 1)

    def test_wrong_or_missing_version_rejected(self) -> None:
        for payload in ({}, {"version": 2, "cases": [valid_case()]}):
            with self.subTest(payload=payload):
                self.assertTrue(
                    any("version 1" in error for error in trigger_schema.schema_errors(payload))
                )

    def test_empty_or_missing_cases_rejected(self) -> None:
        for payload in ({"version": 1}, {"version": 1, "cases": []}):
            with self.subTest(payload=payload):
                self.assertTrue(
                    any(
                        "non-empty cases array" in error
                        for error in trigger_schema.schema_errors(payload)
                    )
                )

    def test_non_object_case_rejected(self) -> None:
        errors = trigger_schema.schema_errors({"version": 1, "cases": ["nope"]})
        self.assertTrue(any("case 0 must be an object" in error for error in errors))

    def test_invalid_activation_rejected(self) -> None:
        case = valid_case()
        case["activation"] = "maybe"
        errors = trigger_schema.schema_errors({"version": 1, "cases": [case]})
        self.assertTrue(any("activation must be 'yes' or 'no'" in error for error in errors))

    def test_unhashable_activation_rejected_without_traceback(self) -> None:
        case = valid_case()
        case["activation"] = ["yes"]
        errors = trigger_schema.schema_errors({"version": 1, "cases": [case]})
        self.assertTrue(any("activation must be 'yes' or 'no'" in error for error in errors))

    def test_empty_prompt_rejected(self) -> None:
        for prompt in ("", "   \t "):
            with self.subTest(prompt=prompt):
                case = valid_case()
                case["prompt"] = prompt
                errors = trigger_schema.schema_errors({"version": 1, "cases": [case]})
                self.assertTrue(any("prompt must be non-empty" in error for error in errors))

    def test_duplicate_id_and_prompt_rejected(self) -> None:
        payload = {
            "version": 1,
            "cases": [
                valid_case("same-id"),
                {"id": "same-id", "activation": "no", "prompt": "A  shared prompt!"},
                {"id": "other-id", "activation": "no", "prompt": "a shared   prompt!"},
            ],
        }
        errors = trigger_schema.schema_errors(payload)
        self.assertTrue(any("duplicates id same-id" in error for error in errors))
        self.assertTrue(any("duplicates an earlier prompt" in error for error in errors))

    def test_needs_positive_and_negative(self) -> None:
        only_yes = {"version": 1, "cases": [valid_case("one"), valid_case("two")]}
        errors = trigger_schema.schema_errors(only_yes)
        self.assertTrue(any("at least one positive and one negative" in error for error in errors))

    def test_count_activations(self) -> None:
        counts = trigger_schema.count_activations(valid_payload()["cases"])
        self.assertEqual(counts, {"yes": 1, "no": 1})


class CheckerIntegrationTests(unittest.TestCase):
    def test_checker_rejects_malformed_id(self) -> None:
        payload = valid_payload()
        payload["cases"] = [valid_case("-bad-id"), valid_payload()["cases"][1]]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "trigger-cases.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            errors: list[str] = []
            check_skill_coverage.validate_trigger_fixture(fixture, errors, "entry 'x'")
        self.assertTrue(
            any("invalid id" in error for error in errors),
            msg=f"checker accepted a malformed id: {errors}",
        )

    def test_checker_accepts_valid_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "trigger-cases.json"
            fixture.write_text(json.dumps(valid_payload()), encoding="utf-8")
            errors: list[str] = []
            check_skill_coverage.validate_trigger_fixture(fixture, errors, "entry 'x'")
        self.assertEqual(errors, [])

    def test_all_repo_fixtures_pass_strict_schema(self) -> None:
        fixtures = sorted(BASE.glob("skills/*/tests/trigger-cases.json"))
        self.assertGreaterEqual(len(fixtures), 11)
        for fixture in fixtures:
            with self.subTest(fixture=str(fixture.relative_to(BASE))):
                payload = json.loads(fixture.read_text(encoding="utf-8"))
                self.assertEqual(trigger_schema.schema_errors(payload), [])


if __name__ == "__main__":
    unittest.main()
