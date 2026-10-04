"""Enforce the skill coverage policy inside a normal unittest run.

The policy and its enforcement rules live in CONTRIBUTING.md and
tests/check_skill_coverage.py; this wrapper keeps a single source of truth
by running the checker as a subprocess. Unit tests here cover the
checker's registry/CONTRIBUTING drift check against synthetic fixtures;
the real repository is enforced end-to-end by running the checker.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

import check_skill_coverage as checker

BASE = Path(__file__).resolve().parents[1]
CHECKER = BASE / "tests" / "check_skill_coverage.py"
DISCOVER = "python3 -m unittest discover"


class SkillCoveragePolicyTests(unittest.TestCase):
    def test_every_skill_has_suite_or_recorded_rationale(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            capture_output=True,
            text=True,
            cwd=BASE,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                "skill coverage policy violated:\n"
                f"{result.stdout}{result.stderr}"
            ),
        )
        self.assertIn("skill-coverage: OK", result.stdout)


def suite_entry(name: str, *suites: str) -> dict:
    return {"name": name, "coverage": "suite", "suites": list(suites)}


class ContributingSuiteBlockTests(unittest.TestCase):
    """The 'Full local check set' block in CONTRIBUTING.md must stay in sync
    with the per-skill suite inventory in tests/skill-coverage.json."""

    def check(self, entries: list, block: list[str] | None) -> list[str]:
        errors: list[str] = []
        checker.check_contributing_block(entries, block, errors)
        return errors

    def test_missing_registered_suite_fails(self) -> None:
        entries = [suite_entry("example", "tests/test_example.py", "skills/example/tests")]
        block = [f"cd tests && {DISCOVER}"]
        errors = self.check(entries, block)
        self.assertEqual(len(errors), 1, msg=errors)
        self.assertIn("skills/example/tests", errors[0])
        self.assertIn("no 'cd skills/example/tests", errors[0])

    def test_unregistered_discover_directory_fails(self) -> None:
        entries = [suite_entry("example", "skills/example/tests")]
        block = [
            f"cd tests && {DISCOVER}",
            f"cd skills/example/tests && {DISCOVER}",
            f"cd skills/ghost/tests && {DISCOVER}",
        ]
        errors = self.check(entries, block)
        self.assertEqual(len(errors), 1, msg=errors)
        self.assertIn("skills/ghost/tests", errors[0])
        self.assertIn("no registry entry registers", errors[0])

    def test_suites_under_tests_need_no_individual_line(self) -> None:
        entries = [
            suite_entry("example", "tests/test_example.py"),
            suite_entry("other", "tests/test_other.py", "tests/fixtures/tests"),
        ]
        errors = self.check(entries, [f"cd tests && {DISCOVER}"])
        self.assertEqual(errors, [])

    def test_agreed_block_passes_and_ignores_non_discover_lines(self) -> None:
        entries = [
            suite_entry("example", "tests/test_example.py", "skills/example/tests"),
            suite_entry("shared", "skills/shared/tests"),
        ]
        block = [
            f"cd tests && {DISCOVER}",
            f"cd skills/example/tests && {DISCOVER}",
            f"cd skills/shared/tests && {DISCOVER}",
            "python3 skills/example/tests/run_triggers.py --validate-only",
            "python3 tests/check_skill_coverage.py",
        ]
        self.assertEqual(self.check(entries, block), [])

    def test_missing_block_fails_closed(self) -> None:
        entries = [suite_entry("example", "skills/example/tests")]
        errors = self.check(entries, None)
        self.assertEqual(len(errors), 1, msg=errors)
        self.assertIn("Full local check set", errors[0])

    def test_discover_line_accepts_trailing_arguments(self) -> None:
        entries = [suite_entry("example", "skills/example/tests")]
        block = [
            f"cd tests && {DISCOVER}",
            f"cd skills/example/tests && {DISCOVER} -v",
        ]
        self.assertEqual(self.check(entries, block), [])

    def test_non_suite_and_malformed_entries_are_ignored_here(self) -> None:
        entries = [
            {"name": "prose", "coverage": "no-suite", "suites": [], "rationale": "x"},
            {"name": "broken", "coverage": "suite"},
            "not-a-dict",
        ]
        self.assertEqual(self.check(entries, [f"cd tests && {DISCOVER}"]), [])


if __name__ == "__main__":
    unittest.main()
