import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/worker-records.py"


class ReviewDispositionsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.task_dir = Path(self.temporary.name) / "task-a"
        self.task_dir.mkdir()
        self.review = self.task_dir / "review"
        self.review.mkdir()
        self.delta = self.task_dir / "review-r2"
        self.delta.mkdir()
        self.initial_evidence = {
            "review": self.write_evidence(self.review, [
                {"id": "S1", "disposition": "unresolved", "reason": "Awaiting response."}
            ]),
            "review-r2": self.write_evidence(self.delta, [
                {"id": "P1", "disposition": "unresolved", "reason": "Awaiting response."}
            ]),
        }

    def write_evidence(self, review_dir, findings):
        evidence = {
            "schema_version": 1,
            "task_id": "task-a",
            "author_identity": "coder-a",
            "reviewer_identity": "reviewer-a",
            "capture_id": f"capture-{review_dir.name}",
            "axes": {
                "standards": {"status": "complete", "findings": findings},
                "spec": {"status": "complete", "findings": []},
            },
        }
        path = review_dir / "review-evidence.json"
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        return evidence

    def write_response(self, text):
        (self.task_dir / "review-response.md").write_text(text, encoding="utf-8")

    def call(self):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "sync-dispositions", str(self.task_dir)],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def read_evidence(self, review_dir):
        return json.loads((review_dir / "review-evidence.json").read_text(encoding="utf-8"))

    def test_sync_copies_terminal_dispositions_and_reasons_with_original_backups(self):
        self.write_response(
            "- `review/S1`: fixed — Corrected and verified.\n"
            "- `review-r2/P1`: rejected — Outside this ticket's scope.\n"
        )
        originals = {
            name: (directory / "review-evidence.json").read_bytes()
            for name, directory in (("review", self.review), ("review-r2", self.delta))
        }

        process = self.call()

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("SYNCED: 2 review evidence file(s)", process.stdout)
        self.assertEqual(
            self.read_evidence(self.review)["axes"]["standards"]["findings"][0],
            {"id": "S1", "disposition": "fixed", "reason": "Corrected and verified."},
        )
        self.assertEqual(
            self.read_evidence(self.delta)["axes"]["standards"]["findings"][0],
            {"id": "P1", "disposition": "rejected", "reason": "Outside this ticket's scope."},
        )
        for name, directory in (("review", self.review), ("review-r2", self.delta)):
            backup = directory / "review-evidence.json.pre-disposition-sync"
            self.assertEqual(backup.read_bytes(), originals[name])

    def test_missing_finding_disposition_refuses_without_partial_updates(self):
        self.write_response("- `review/S1`: fixed — Corrected and verified.\n")
        originals = {
            directory: (directory / "review-evidence.json").read_bytes()
            for directory in (self.review, self.delta)
        }

        process = self.call()

        self.assertEqual(process.returncode, 2)
        self.assertIn("missing disposition", process.stderr)
        for directory, original in originals.items():
            self.assertEqual((directory / "review-evidence.json").read_bytes(), original)
            self.assertFalse((directory / "review-evidence.json.pre-disposition-sync").exists())

    def test_unknown_finding_disposition_refuses_without_partial_updates(self):
        self.write_response(
            "- `review/S1`: fixed — Corrected and verified.\n"
            "- `review-r2/P9`: rejected — No such finding.\n"
        )
        originals = {
            directory: (directory / "review-evidence.json").read_bytes()
            for directory in (self.review, self.delta)
        }

        process = self.call()

        self.assertEqual(process.returncode, 2)
        self.assertIn("unknown finding", process.stderr)
        for directory, original in originals.items():
            self.assertEqual((directory / "review-evidence.json").read_bytes(), original)
            self.assertFalse((directory / "review-evidence.json.pre-disposition-sync").exists())

    def test_repeated_sync_is_idempotent_and_preserves_first_backup(self):
        self.write_response(
            "- `review/S1`: fixed — Corrected and verified.\n"
            "- `review-r2/P1`: rejected — Outside this ticket's scope.\n"
        )
        first = self.call()
        self.assertEqual(first.returncode, 0, first.stderr)
        evidence_after_first = {
            directory: (directory / "review-evidence.json").read_bytes()
            for directory in (self.review, self.delta)
        }
        backups_after_first = {
            directory: (directory / "review-evidence.json.pre-disposition-sync").read_bytes()
            for directory in (self.review, self.delta)
        }

        second = self.call()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already match", second.stdout)
        for directory in (self.review, self.delta):
            self.assertEqual((directory / "review-evidence.json").read_bytes(), evidence_after_first[directory])
            self.assertEqual(
                (directory / "review-evidence.json.pre-disposition-sync").read_bytes(),
                backups_after_first[directory],
            )


if __name__ == "__main__":
    unittest.main()
