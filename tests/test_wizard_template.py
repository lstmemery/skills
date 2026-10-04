"""Behavioural tests for the wizard script template.

skills/wizard/template.sh is the library + example-stages file the /wizard
skill copies into consuming repos. It is checked-in code, so per the
coverage policy it carries an offline suite: structural checks plus
behaviour driven with piped stdin, a scrubbed PATH (no gh, no browsers, no
tput) and a temp ENV_FILE — nothing leaves the machine.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / "skills" / "wizard" / "template.sh"
# stdin lines consumed by the example stage: pause, ask, ask_secret.
ANSWER_INPUT = "go\npk_test_123\nsk_test_456\n"


class WizardTemplateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="wizard-template-test-")
        self.addCleanup(self.tmp.cleanup)
        self.workdir = Path(self.tmp.name)
        self.bin_dir = self.workdir / "bin"
        self.bin_dir.mkdir()
        for tool in ("grep", "tail", "mktemp", "mv", "touch"):
            source = shutil.which(tool)
            self.assertTrue(source, f"required tool missing: {tool}")
            (self.bin_dir / tool).symlink_to(source)
        self.env_file = self.workdir / "out" / ".env"

    def run_wizard(self, stdin_text: str) -> subprocess.CompletedProcess[str]:
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": str(self.bin_dir),
            "HOME": str(self.workdir),
            "TMPDIR": str(self.workdir),
            "ENV_FILE": str(self.env_file),
        }
        return subprocess.run(
            ["/bin/bash", str(SCRIPT)],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(self.workdir),
        )

    def env_lines(self) -> list[str]:
        return self.env_file.read_text(encoding="utf-8").splitlines()


class StructuralTests(WizardTemplateTestCase):
    def test_script_parses(self) -> None:
        result = subprocess.run(
            ["/bin/bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_library_surface_is_present(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertIn("STAGES", text)
        for helper in (
            "banner()",
            "stage()",
            "ask()",
            "ask_secret()",
            "write_env()",
            "set_secret()",
            "finish()",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, text)


class BehaviourTests(WizardTemplateTestCase):
    def test_answers_are_written_to_env_and_secret_skip_is_reported(self) -> None:
        result = self.run_wizard(ANSWER_INPUT)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = self.env_lines()
        self.assertIn("STRIPE_PUBLISHABLE_KEY=pk_test_123", lines)
        self.assertIn("STRIPE_SECRET_KEY=sk_test_456", lines)
        self.assertIn("Setup complete", result.stdout)
        # gh is unreachable under the scrubbed PATH, so the secret must be
        # reported as skipped for manual follow-up, never silently dropped.
        self.assertIn("still to do by hand", result.stdout)
        self.assertIn("GitHub secret STRIPE_SECRET_KEY", result.stdout)

    def test_rerun_keeps_existing_values_and_upserts_single_lines(self) -> None:
        self.run_wizard(ANSWER_INPUT)
        # Enter on both prompts keeps the current .env values.
        rerun = self.run_wizard("go\n\n\n")
        self.assertEqual(rerun.returncode, 0, msg=rerun.stderr)
        lines = self.env_lines()
        key_lines = [
            line for line in lines if line.startswith("STRIPE_PUBLISHABLE_KEY=")
        ]
        self.assertEqual(key_lines, ["STRIPE_PUBLISHABLE_KEY=pk_test_123"])
        self.assertEqual(
            [line for line in lines if line.startswith("STRIPE_SECRET_KEY=")],
            ["STRIPE_SECRET_KEY=sk_test_456"],
        )

    def test_rerun_overwrites_changed_values_without_duplicates(self) -> None:
        self.run_wizard(ANSWER_INPUT)
        rerun = self.run_wizard("go\npk_test_rotated\n\n")
        self.assertEqual(rerun.returncode, 0, msg=rerun.stderr)
        lines = self.env_lines()
        self.assertIn("STRIPE_PUBLISHABLE_KEY=pk_test_rotated", lines)
        self.assertNotIn("STRIPE_PUBLISHABLE_KEY=pk_test_123", lines)
        self.assertEqual(
            len([line for line in lines if line.startswith("STRIPE_PUBLISHABLE_KEY=")]), 1
        )
        self.assertIn("STRIPE_SECRET_KEY=sk_test_456", lines)


if __name__ == "__main__":
    unittest.main()
