from pathlib import Path
import unittest


PACKAGE = Path(__file__).resolve().parents[1]


class PremisePreflightDocsTest(unittest.TestCase):
    def test_assign_step_gates_state_dependent_implementation(self):
        skill = (PACKAGE / "SKILL.md").read_text()
        assign_step = skill.split("2. **Assign.**", 1)[1].split("3. **Launch.**", 1)[0]

        self.assertIn("Before launch", assign_step)
        self.assertIn("current,", assign_step)
        self.assertIn("read-only check", assign_step)
        self.assertIn("references/state.md#current-state-premise-check", assign_step)
        self.assertIn("do not launch an implementation worker", assign_step)
        self.assertIn("rescope request for the owner", assign_step)
        self.assertIn("hold implementation", assign_step)

    def test_run_state_template_records_check_and_rescope_evidence(self):
        state = (PACKAGE / "references/state.md").read_text()
        section = state.split("## Current-state premise check", 1)[1].split(
            "\n## Lifecycle and steering", 1
        )[0]
        preflight = " ".join(section.split())

        for field in (
            "Read-only source (exact command/path/lookup)",
            "Checked at (UTC ISO 8601)",
            "Observed value",
            "Verdict (confirmed|contradicted|unknown)",
            "add a dated rescope request to `STATE.md`",
            "no implementation worker was launched",
            "#1080, #1046, and #1088",
            "#1081 and #1082",
        ):
            with self.subTest(field=field):
                self.assertIn(field, preflight)


if __name__ == "__main__":
    unittest.main()
