"""Unit tests for activation event parsing without model calls."""

from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_triggers
from run_triggers import (
    activation_evidence,
    claude_skill_events,
    load_cases,
    matches_shopping_reference,
    read_skill_events,
)


class TriggerFixtureTests(unittest.TestCase):
    def test_fixture_has_the_expected_positive_and_negative_cases(self) -> None:
        cases = load_cases()
        self.assertEqual(len(cases), 13)
        self.assertEqual(sum(case["activation"] == "yes" for case in cases), 8)
        self.assertEqual(sum(case["activation"] == "no" for case in cases), 5)

    def assert_validate_only_rejects(self, payload: object) -> str:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path(temp_dir) / "invalid-trigger-cases.json"
            fixture.write_text(json.dumps(payload), encoding="utf-8")
            error_output = io.StringIO()
            with patch.object(run_triggers, "CASES_FILE", fixture):
                with patch("sys.argv", ["run_triggers", "--validate-only"]):
                    with redirect_stderr(error_output):
                        self.assertEqual(run_triggers.main(), 2)
        return error_output.getvalue()

    def test_validate_only_reports_non_object_fixture_root(self) -> None:
        error = self.assert_validate_only_rejects([])
        self.assertIn("trigger-cases.json must be a JSON object", error)
        self.assertNotIn("Traceback", error)

    def test_validate_only_reports_unhashable_activation(self) -> None:
        payload = {
            "version": 1,
            "cases": [{"id": "bad-activation", "activation": [], "prompt": "Example"}],
        }
        error = self.assert_validate_only_rejects(payload)
        self.assertIn("activation must be 'yes' or 'no'", error)
        self.assertNotIn("Traceback", error)


class ActivationEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.skill_copy = Path(self.temp_directory.name) / "skills" / "shopping"
        self.skill_copy.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def read_events(self, reference: str, *, is_error: bool = False) -> list[dict[str, object]]:
        return [
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "toolCallId": "read-1",
                "args": {"path": reference},
            },
            {
                "type": "tool_execution_end",
                "toolName": "read",
                "toolCallId": "read-1",
                "isError": is_error,
            },
        ]

    def test_recognizes_skill_uri_and_exact_temporary_skill_path(self) -> None:
        self.assertTrue(matches_shopping_reference({"path": "skill://shopping"}, self.skill_copy))
        self.assertTrue(
            matches_shopping_reference(
                {"path": str(self.skill_copy / "SKILL.md")}, self.skill_copy
            )
        )

    def test_ignores_other_files_under_the_skill_directory(self) -> None:
        fixture = self.skill_copy / "tests" / "trigger-cases.json"
        self.assertFalse(matches_shopping_reference({"path": str(fixture)}, self.skill_copy))

    def test_only_a_successful_matching_read_counts_as_activation(self) -> None:
        output = "\n".join(
            json.dumps(record) for record in self.read_events("skill://shopping")
        )
        self.assertTrue(activation_evidence("pi", output, self.skill_copy)[0])

        failed_output = "\n".join(
            json.dumps(record)
            for record in self.read_events("skill://shopping", is_error=True)
        )
        self.assertFalse(activation_evidence("pi", failed_output, self.skill_copy)[0])

    def test_ignores_unrelated_reads(self) -> None:
        records = self.read_events(
            str(self.skill_copy / "tests" / "trigger-cases.json")
        )
        events, structured = read_skill_events(records, self.skill_copy)
        self.assertTrue(structured)
        self.assertEqual(events, [])

    def test_counts_a_successful_claude_plugin_skill_call(self) -> None:
        records = [
            {
                "type": "tool_use",
                "name": "Skill",
                "id": "skill-1",
                "input": {"skill": "shopping-trigger-test:shopping"},
            },
            {"type": "tool_result", "tool_use_id": "skill-1", "is_error": False},
        ]
        events, structured = claude_skill_events(records)
        self.assertTrue(structured)
        self.assertEqual(events[0]["skill"], "shopping-trigger-test:shopping")
        self.assertEqual(events[0]["result"], "success")


if __name__ == "__main__":
    unittest.main()
