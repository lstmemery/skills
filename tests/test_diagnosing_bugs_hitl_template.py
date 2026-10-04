"""Behavioural tests for the diagnosing-bugs human-in-the-loop script.

skills/diagnosing-bugs/scripts/hitl-loop.template.sh is a template copied
into consuming repos, but it is checked-in code, so per the coverage policy
it carries an offline suite: structural checks plus behaviour driven with
piped stdin and synthetic input (no human, no network).
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / "skills" / "diagnosing-bugs" / "scripts" / "hitl-loop.template.sh"
APP_URL = "http://localhost:3000"


def run_script(stdin_text: str, *, app_url: str | None = APP_URL) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin"}
    if app_url is not None:
        env["APP_URL"] = app_url
    return subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


class StructuralTests(unittest.TestCase):
    def test_script_parses(self) -> None:
        result = subprocess.run(
            ["/bin/bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_script_shells_up_strictly_and_documents_helpers(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertIn("step()", text)
        self.assertIn("capture()", text)
        self.assertIn("APP_URL", text)


class BehaviourTests(unittest.TestCase):
    def test_captured_answers_are_reported_as_key_value_pairs(self) -> None:
        result = run_script("\ny\nboom\n")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for expected in (
            f">>> Open the app at {APP_URL} and sign in.",
            ">>> Click the 'Export' button. Did it throw an error? (y/n)",
            ">>> Paste the error message (or 'none'):",
            "--- Captured ---",
            "ERRORED=y",
            "ERROR_MSG=boom",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, result.stdout)

    def test_missing_app_url_fails_loudly(self) -> None:
        result = run_script("\n", app_url=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APP_URL", result.stderr)

    def test_premature_eof_fails_rather_than_prompting_forever(self) -> None:
        result = run_script("")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
