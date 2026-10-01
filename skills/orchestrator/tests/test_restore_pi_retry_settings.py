import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/restore-pi-retry-settings.py"


class RestorePiRetrySettingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = self.root / "settings.json"
        self.baseline = self.root / "settings.before.json"
        self.rollback = self.root / "settings.rollback.json"

    def run_script(self, *arguments):
        return subprocess.run([sys.executable, str(SCRIPT), "--settings", str(self.settings),
                               "--baseline", str(self.baseline), "--rollback-file", str(self.rollback),
                               *arguments], capture_output=True, text=True, timeout=10)

    def test_dry_run_shows_only_retry_key_diff_and_leaves_settings_unchanged(self):
        original = {
            "privateEndpoint": "https://secret.example.invalid",
            "retry": {"enabled": True, "maxRetries": 10, "maxAgentDelayMs": 120000},
        }
        self.settings.write_text(json.dumps(original))
        self.baseline.write_text(json.dumps({"retry": {"enabled": True}}))

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retry.maxRetries: 10 -> <absent>", result.stdout)
        self.assertIn("retry.maxAgentDelayMs: 120000 -> <absent>", result.stdout)
        self.assertNotIn("secret.example.invalid", result.stdout)
        self.assertEqual(json.loads(self.settings.read_text()), original)
        self.assertFalse(self.rollback.exists())

    def test_apply_and_rollback_change_only_retry_keys(self):
        self.settings.write_text(json.dumps({
            "model": "unchanged-on-apply",
            "retry": {"enabled": True, "maxRetries": 10, "maxAgentDelayMs": 120000},
        }))
        self.baseline.write_text(json.dumps({"retry": {"enabled": True}}))

        applied = self.run_script("--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        restored = json.loads(self.settings.read_text())
        self.assertEqual(restored, {"model": "unchanged-on-apply", "retry": {"enabled": True}})
        self.assertTrue(self.rollback.is_file())

        restored["model"] = "edited-after-apply"
        self.settings.write_text(json.dumps(restored))
        rolled_back = self.run_script("--rollback")

        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual(json.loads(self.settings.read_text()), {
            "model": "edited-after-apply",
            "retry": {"enabled": True, "maxRetries": 10, "maxAgentDelayMs": 120000},
        })


if __name__ == "__main__":
    unittest.main()
