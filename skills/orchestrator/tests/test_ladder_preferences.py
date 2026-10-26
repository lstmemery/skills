from pathlib import Path
import unittest


PACKAGE = Path(__file__).resolve().parents[1]


class LadderPreferencesTest(unittest.TestCase):
    def test_luna_guidance_uses_current_level_four_fallback(self):
        preferences = (PACKAGE / "PREFERENCES.md").read_text()
        luna_guidance = next(
            paragraph for paragraph in preferences.split("\n- ")
            if paragraph.startswith('"Luna agents"')
        )
        guidance = " ".join(luna_guidance.split())

        self.assertIn(
            "The level 4 fallback uses `claude-sonnet-5-5` at high effort "
            "through the Claude harness and provider.",
            guidance,
        )
        self.assertNotIn("opencode-go", guidance.lower())
        self.assertNotIn("opencode go", guidance.lower())


if __name__ == "__main__":
    unittest.main()
