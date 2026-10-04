"""Guard the single-owner finding-evidence contract shared by sync and preflight."""

import importlib.util
from pathlib import Path
import sys
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
PREFLIGHT_CLI = PACKAGE / "scripts" / "integration-preflight.py"
sys.path.insert(0, str(PACKAGE / "scripts"))

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
            with self.subTest(case=name):
                expected = review_evidence.finding_evidence_error(
                    candidate, "review", 1
                )
                self.assertEqual(
                    review_evidence.finding_evidence_error(candidate, "review", 2),
                    expected,
                    "metadata-bearing findings must validate identically under v1 and v2",
                )
                if expected is None:
                    review_dispositions._validate_finding_evidence(
                        candidate, "review", 1
                    )
                else:
                    with self.assertRaises(records.JobError) as raised:
                        review_dispositions._validate_finding_evidence(
                            candidate, "review", 1
                        )
                    self.assertEqual(str(raised.exception), expected)

    def test_schema_2_requires_metadata_while_v1_does_not(self):
        bare = {"id": "S1", "disposition": "fixed"}
        self.assertIsNone(review_evidence.finding_evidence_error(bare, "review", 1))
        self.assertEqual(
            review_evidence.finding_evidence_error(bare, "review", 2),
            "review/S1 schema version 2 finding requires evidence metadata",
        )


if __name__ == "__main__":
    unittest.main()
