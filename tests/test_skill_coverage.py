"""Enforce the skill coverage policy inside a normal unittest run.

The policy and its enforcement rules live in CONTRIBUTING.md and
tests/check_skill_coverage.py; this wrapper keeps a single source of truth
by running the checker as a subprocess.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

BASE = Path(__file__).resolve().parents[1]
CHECKER = BASE / "tests" / "check_skill_coverage.py"


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


if __name__ == "__main__":
    unittest.main()
