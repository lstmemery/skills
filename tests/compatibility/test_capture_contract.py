"""Captured manifests remain readable and writer fields stay pinned."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility.builders import SKILLS_ROOT, capture_bundle, git_history, golden, write_json


CLI = SKILLS_ROOT / "skills/code-review/scripts/capture.py"


def invoke(*args):
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True,
                          text=True, check=False, env=os.environ | {"CODE_REVIEW_SECRETS_SCANNER": "stdlib-only"})


class CaptureCompatibilityTests(unittest.TestCase):
    def test_sealed_v1_capture_can_be_verified_and_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            manifest = capture_bundle(directory)
            result = invoke("verify", "--capture", directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), golden("capture-v1")["verification"])
            for side, expected in (("before", "first\n"), ("after", "second\n")):
                result = invoke("read", "--capture", directory, "--side", side, "--path", "file.txt")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {
                    "outcome": "content", "capture_id": manifest["capture_id"],
                    "side": side, "path": "file.txt", "text": expected,
                })

    def test_current_writer_preserves_the_entire_v1_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            base, head = git_history(repo)
            directory = root / "capture"
            result = invoke("capture", "--repo", repo, "--mode", "since", "--base", base, "--out", directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(invoke("verify", "--capture", directory).returncode, 0)
            actual = json.loads((directory / "manifest.json").read_text())
            expected = copy.deepcopy(golden("capture-v1")["manifest"])
            self.assertEqual(actual["capture_id"], json.loads(result.stdout)["capture_id"])
            self.assertEqual(actual["head"], head)
            self.assertEqual(actual["repo"], str(repo))
            actual["repo"] = "fixture/repository"
            actual["commands"] = [["fixture/repository" if part == str(repo) else part for part in command]
                                  for command in actual["commands"]]
            # The capture digest includes the absolute repository identity.
            actual["capture_id"] = expected["capture_id"]
            self.assertEqual(actual, expected)
            for name, contents in golden("capture-v1")["files"].items():
                if name != "COMPLETE":
                    self.assertEqual((directory / name).read_text(), contents, name)

    def test_future_version_missing_payload_and_mismatched_completion_fail(self):
        for mutation in ("future-version", "missing-blob", "completion"):
            with self.subTest(case=mutation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / "capture"
                manifest = capture_bundle(directory)
                if mutation == "future-version":
                    manifest["version"] = 2
                    write_json(directory / "manifest.json", manifest)
                elif mutation == "missing-blob":
                    (directory / "blobs" / manifest["after"]["file.txt"]["sha256"]).unlink()
                else:
                    (directory / "COMPLETE").write_text("different\n")
                self.assertNotEqual(invoke("verify", "--capture", directory).returncode, 0)


if __name__ == "__main__":
    unittest.main()
