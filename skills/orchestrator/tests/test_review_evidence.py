"""Guard the single-owner finding-evidence contract shared by sync and preflight."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
PREFLIGHT_CLI = PACKAGE / "scripts" / "integration-preflight.py"
sys.path.insert(0, str(PACKAGE / "scripts"))
sys.path.insert(0, str(PACKAGE.parents[1] / "tests"))
from compatibility.builders import review_bundle, write_json

from herdr_jobs import records
from herdr_jobs import review_dispositions
from herdr_jobs import review_evidence


PREFLIGHT_SPEC = importlib.util.spec_from_file_location("integration_preflight", PREFLIGHT_CLI)
PREFLIGHT = importlib.util.module_from_spec(PREFLIGHT_SPEC)
PREFLIGHT_SPEC.loader.exec_module(PREFLIGHT)


def finding(confidence="high", reproducer="Run the focused check.", **overrides):
    result = {
        "id": "S1",
        "disposition": "fixed",
        "confidence": confidence,
        "reproducer": reproducer,
    }
    result.update(overrides)
    return result


CASES = [
    ("valid-reproducer", finding()),
    ("valid-evidence-and-assumption", finding(reproducer=None, evidence="Checked.", unresolved_assumption="Load-bearing.")),
    ("invalid-confidence", finding(confidence="certain")),
    ("non-string-confidence", finding(confidence=2)),
    ("empty-reproducer", finding(reproducer="   ")),
    ("non-string-reproducer", finding(reproducer=7)),
    ("empty-evidence", finding(evidence=" ")),
    ("empty-assumption", finding(unresolved_assumption="")),
    ("non-string-assumption", finding(unresolved_assumption=[])),
    ("no-reproducer-and-incomplete-evidence", finding(reproducer=None, evidence="Checked.")),
]


class ReviewEvidenceContractTest(unittest.TestCase):
    def test_both_gates_import_the_owner_module(self):
        self.assertIs(PREFLIGHT.FINDING_EVIDENCE_FIELDS, review_evidence.FINDING_EVIDENCE_FIELDS)
        self.assertIs(
            PREFLIGHT.REVIEW_EVIDENCE_SCHEMA_VERSIONS,
            review_evidence.REVIEW_EVIDENCE_SCHEMA_VERSIONS,
        )
        self.assertIs(PREFLIGHT.finding_evidence_error, review_evidence.finding_evidence_error)
        self.assertIs(
            review_dispositions.FINDING_EVIDENCE_FIELDS,
            review_evidence.FINDING_EVIDENCE_FIELDS,
        )
        self.assertIs(
            review_dispositions.REVIEW_EVIDENCE_SCHEMA_VERSIONS,
            review_evidence.REVIEW_EVIDENCE_SCHEMA_VERSIONS,
        )
        self.assertIs(PREFLIGHT.parse_review_response, review_evidence.parse_review_response)
        self.assertIs(review_dispositions.review_evidence.parse_review_response, review_evidence.parse_review_response)

    def test_response_parser_preserves_each_callers_malformed_line_policy(self):
        response = (
            "- `review/S1`: fixed — Corrected.\n"
            "- `review/S2`: undecided — Still open.\n"
        )
        self.assertEqual(
            review_evidence.parse_review_response(response),
            {("review", "S1"): ("fixed", "Corrected.")},
        )
        with self.assertRaisesRegex(ValueError, "malformed review disposition at review-response.md:2"):
            review_evidence.parse_review_response(response, reject_malformed=True)
        with self.assertRaisesRegex(ValueError, "duplicate disposition for review/S1"):
            review_evidence.parse_review_response(
                "- `review/S1`: fixed — First.\n- `review/S1`: rejected — Second.\n"
            )

    def test_owner_contract_shape(self):
        self.assertEqual(
            review_evidence.FINDING_EVIDENCE_FIELDS,
            {"confidence", "reproducer", "evidence", "unresolved_assumption"},
        )
        self.assertEqual(
            review_evidence.FINDING_CONFIDENCE, {"high", "medium", "low"}
        )
        self.assertEqual(review_evidence.REVIEW_EVIDENCE_SCHEMA_VERSIONS, {1, 2})

    def test_sync_and_gate_agree_on_every_case(self):
        for name, candidate in CASES:
            for version in (1, 2):
                with self.subTest(case=name, version=version), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    review, evidence = review_bundle(root, "v2-reproducer")
                    evidence["schema_version"] = version
                    evidence["axes"]["standards"]["findings"] = [
                        {**candidate, "reason": "Added the guard."}
                    ]
                    write_json(review / "review-evidence.json", evidence)
                    if name.startswith("valid-"):
                        self.assertEqual(review_dispositions.sync_dispositions(review.parent), 0)
                        result, diagnostics = PREFLIGHT.review_records(
                            review.parent, "task-1", root / "workers.txt")
                        self.assertEqual(len(result), 1)
                        self.assertEqual(diagnostics, [])
                    else:
                        with self.assertRaises(records.JobError):
                            review_dispositions.sync_dispositions(review.parent)
                        result, diagnostics = PREFLIGHT.review_records(
                            review.parent, "task-1", root / "workers.txt")
                        self.assertEqual(result, [])
                        self.assertTrue(diagnostics)

    def test_schema_2_requires_metadata_while_v1_does_not(self):
        bare = {"id": "S1", "disposition": "fixed"}
        self.assertIsNone(review_evidence.finding_evidence_error(bare, "review", 1))
        self.assertEqual(
            review_evidence.finding_evidence_error(bare, "review", 2),
            "review/S1 schema version 2 finding requires evidence metadata",
        )


if __name__ == "__main__":
    unittest.main()
