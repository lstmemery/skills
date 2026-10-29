"""Exercise the reviewer-facing command with complete, independently pinned fixtures."""

import json
from pathlib import Path
import subprocess
import runpy
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE.parents[1] / "tests"))
from review_fixtures import review_bundle, write_json

SCRIPT = PACKAGE / "scripts/worker-records.py"


class ValidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.review, self.evidence = review_bundle(self.root, "empty")

    def call(self, kind="full"):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "validate-review", str(self.review),
             "--kind", kind, "--task-id", "task-1", "--author-identity", "coder-1",
             "--reviewer-identity", "reviewer-1"],
            capture_output=True, text=True, timeout=15,
        )

    def test_valid_full_review_is_checked_without_writing_artifacts(self):
        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VALID REVIEW", result.stdout)
        after = {p.relative_to(self.root): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_unresolved_reviewer_findings_pass_but_integration_still_blocks(self):
        self.review, self.evidence = review_bundle(self.root, "v2-reproducer")
        self.evidence["axes"]["standards"]["findings"][0]["disposition"] = "unresolved"
        write_json(self.review / "review-evidence.json", self.evidence)
        (self.review.parent / "review-response.md").unlink()
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        gate = runpy.run_path(str(PACKAGE / "scripts/integration-preflight.py"))
        reviews, diagnostics = gate["review_records"](
            self.review.parent, "task-1", self.root / "workers.txt")
        self.assertEqual(reviews, [])
        self.assertTrue(any("terminal disposition" in item for item in diagnostics), diagnostics)

    def test_delta_uses_revision_counts_and_requires_matching_directory_kind(self):
        self.review = self.review.rename(self.review.with_name("review-r1"))
        done = json.loads((self.review / "done.json").read_text())
        done.pop("standards_findings")
        done.pop("spec_findings")
        done.update(new_findings=0, unfixed=0)
        write_json(self.review / "done.json", done)
        result = self.call("delta")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.call("full").returncode, 2)
        for change in ({"new_findings": 1}, {"unfixed": 1}, {"unfixed": False}):
            with self.subTest(change=change):
                write_json(self.review / "done.json", done | change)
                self.assertEqual(self.call("delta").returncode, 2)
        write_json(self.review / "done.json", {
            "task_id": "task-1", "capture_id": done["capture_id"],
            "standards_findings": 0, "spec_findings": 0})
        self.assertEqual(self.call("delta").returncode, 2)

    def test_rejects_incomplete_or_inconsistent_bundles(self):
        changes = {
            "missing-capture": lambda done, evidence: done.pop("capture_id"),
            "missing-full-count": lambda done, evidence: done.pop("spec_findings"),
            "wrong-count": lambda done, evidence: done.update(standards_findings=1),
            "wrong-task": lambda done, evidence: evidence.update(task_id="another"),
            "wrong-reviewer": lambda done, evidence: evidence.update(reviewer_identity="coder-1"),
            "extra-field": lambda done, evidence: evidence.update(unexpected=True),
            "invalid-axis-reviewers": lambda done, evidence: evidence.update(axis_reviewers={}),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self.review, evidence = review_bundle(self.root, "empty")
                done = json.loads((self.review / "done.json").read_text())
                change(done, evidence)
                write_json(self.review / "done.json", done)
                write_json(self.review / "review-evidence.json", evidence)
                result = self.call()
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("ERROR:", result.stderr)

    def test_rejects_markdown_disagreement_and_damaged_capture(self):
        (self.review / "review.md").write_text(
            "## Standards\n- [S1] Missing guard.\nSummary: findings=1; worst=Missing guard.\n"
            "## Spec\nNo findings.\nSummary: findings=0; worst=none.\n")
        result = self.call()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Markdown finding IDs disagree", result.stderr)
        self.review, _ = review_bundle(self.root, "empty")
        (self.review / "capture/COMPLETE").write_text("")
        result = self.call()
        self.assertEqual(result.returncode, 2)
        self.assertIn("COMPLETE marker is empty", result.stderr)

    def test_retains_supported_axis_attribution(self):
        self.evidence["axis_reviewers"] = {"standards": "/root/standards", "spec": "/root/spec"}
        write_json(self.review / "review-evidence.json", self.evidence)
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
