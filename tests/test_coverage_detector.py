"""Tests for the coverage checker's executable-code detector.

A skill carrying a shell script — including a *.template.sh copied into
consuming repos before it runs — must not hide behind a no-suite rationale;
it needs a suite (tests/test_diagnosing_bugs_hitl_template.py and
tests/test_wizard_template.py are the worked examples).
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from check_skill_coverage import has_executable_code, validate_entry


class HasExecutableCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="coverage-detector-")
        self.addCleanup(tmp.cleanup)
        self.skill_dir = Path(tmp.name)

    def write(self, rel: str, text: str = "x\n", *, mode: int | None = None) -> Path:
        path = self.skill_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)
        return path

    def test_prose_only_skill_has_no_executable_code(self) -> None:
        self.write("SKILL.md", "# skill\n")
        self.assertFalse(has_executable_code(self.skill_dir))

    def test_shell_scripts_count_including_templates(self) -> None:
        for rel in ("scripts/run.sh", "template.sh", "wizard/wizard.template.bash"):
            with self.subTest(rel=rel):
                self.write("SKILL.md")
                self.write(rel, "#!/bin/bash\n")
                self.assertTrue(has_executable_code(self.skill_dir), msg=rel)

    def test_python_and_execute_bit_count(self) -> None:
        self.write("SKILL.md")
        self.write("scripts/tool.py")
        self.assertTrue(has_executable_code(self.skill_dir))
        self.write("scripts/tool", "#!/bin/bash\n", mode=0o755)
        self.assertTrue(has_executable_code(self.skill_dir))

    def test_tests_directory_is_exempt(self) -> None:
        self.write("SKILL.md")
        self.write("tests/trigger-cases.json", "{}\n")
        self.write("tests/test_nothing.py")
        self.assertFalse(has_executable_code(self.skill_dir))


class NoSuiteRationaleTests(unittest.TestCase):
    def test_script_bearing_skill_cannot_carry_no_suite_rationale(self) -> None:
        with tempfile.TemporaryDirectory(prefix="coverage-policy-") as tmp:
            skill_dir = Path(tmp)
            (skill_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")
            (skill_dir / "template.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            errors: list[str] = []
            validate_entry(
                {
                    "name": "scripted",
                    "coverage": "no-suite",
                    "suites": [],
                    "routing_critical": False,
                    "triggers": None,
                    "rationale": "the consuming repo owns its runtime behavior",
                },
                skill_dir,
                errors,
            )
        self.assertTrue(
            any("executable code" in error for error in errors),
            msg=f"expected executable-code violation: {errors}",
        )


if __name__ == "__main__":
    unittest.main()
