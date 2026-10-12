"""Both review consumers validate complete shared bundles at public seams."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility.builders import SKILLS_ROOT, golden, load_module, review_bundle, write_json

sys.path.insert(0, str(SKILLS_ROOT / "skills/orchestrator/scripts"))
from herdr_jobs import records, review_dispositions

PREFLIGHT = load_module(
    "compat_preflight", SKILLS_ROOT / "skills/orchestrator/scripts/integration-preflight.py"
)


class ReviewCompatibilityTests(unittest.TestCase):
    def test_both_consumers_accept_v1_and_v2_golden_bundles(self):
        for name in golden("review")["cases"]:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                review, expected = review_bundle(root, name)
                actual, diagnostics = PREFLIGHT.review_records(review.parent, "task-1", root / "workers.txt")
                self.assertEqual(diagnostics, [])
                self.assertEqual(len(actual), 1)
                self.assertEqual(actual[0]["markdown_compatibility"], "strict")
                self.assertEqual(actual[0]["finding_count"], sum(len(expected["axes"][axis]["findings"])
                                                              for axis in ("standards", "spec")))
                self.assertEqual(review_dispositions.sync_dispositions(review.parent), 0)
                observed = json.loads((review / "review-evidence.json").read_text())
                self.assertEqual(observed, expected)

    def test_sync_preserves_evidence_and_backup_then_preflight_accepts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review, expected = review_bundle(root, "v2-reproducer")
            path = review / "review-evidence.json"
            pending = json.loads(path.read_text())
            pending["axes"]["standards"]["findings"][0].update(disposition="unresolved", reason="Awaiting response.")
            write_json(path, pending)
            original = path.read_bytes()
            self.assertEqual(review_dispositions.sync_dispositions(review.parent), 1)
            self.assertEqual(json.loads(path.read_text()), expected)
            backup = path.with_name(path.name + ".pre-disposition-sync")
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(review_dispositions.sync_dispositions(review.parent), 0)
            self.assertEqual(backup.read_bytes(), original)
            output, diagnostics = PREFLIGHT.review_records(review.parent, "task-1", root / "workers.txt")
            self.assertEqual(diagnostics, [])
            self.assertEqual(output[0]["finding_count"], 1)

    def test_both_consumers_reject_incompatible_evidence_without_updates(self):
        for case in golden("review")["invalid"]:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                review, evidence = review_bundle(root, "v2-reproducer")
                evidence.update(case["top_level"])
                finding = evidence["axes"]["standards"]["findings"][0]
                finding.update(case["finding"])
                for field in case["remove"]:
                    del finding[field]
                path = review / "review-evidence.json"
                write_json(path, evidence)
                original = path.read_bytes()
                with self.assertRaises(records.JobError):
                    review_dispositions.sync_dispositions(review.parent)
                output, diagnostics = PREFLIGHT.review_records(review.parent, "task-1", root / "workers.txt")
                self.assertEqual(output, [])
                self.assertTrue(diagnostics)
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse(path.with_name(path.name + ".pre-disposition-sync").exists())

    def test_duplicate_dispositions_rejected_by_both_consumers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review, _ = review_bundle(root)
            response = review.parent / "review-response.md"
            response.write_text(response.read_text() * 2)
            with self.assertRaisesRegex(records.JobError, "duplicate disposition"):
                review_dispositions.sync_dispositions(review.parent)
            with self.assertRaisesRegex(PREFLIGHT.PreflightError, "duplicate disposition"):
                PREFLIGHT.review_records(review.parent, "task-1", root / "workers.txt")

    def test_preflight_rejects_bundle_disagreement_and_nonallowlisted_legacy_prose(self):
        for mutation in ("markdown", "done", "identity", "legacy", "capture"):
            with self.subTest(case=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                review, evidence = review_bundle(root)
                if mutation == "markdown":
                    (review / "review.md").write_text(golden("review")["cases"]["empty"]["markdown"])
                elif mutation == "done":
                    path = review / "done.json"
                    done = json.loads(path.read_text())
                    done["standards_findings"] = 99
                    write_json(path, done)
                elif mutation == "identity":
                    evidence["reviewer_identity"] = evidence["author_identity"]
                    write_json(review / "review-evidence.json", evidence)
                elif mutation == "legacy":
                    (review / "review.md").write_text("Both axes reviewed; no issues.\n")
                else:
                    (review / "capture/diff.patch").write_text("tampered\n")
                output, diagnostics = PREFLIGHT.review_records(review.parent, "task-1", root / "workers.txt")
                self.assertEqual(output, [])
                self.assertTrue(diagnostics)


if __name__ == "__main__":
    unittest.main()
