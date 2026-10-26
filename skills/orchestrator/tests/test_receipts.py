import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from herdr_jobs import records


class ReceiptTest(unittest.TestCase):
    def test_receipt_revision_binds_exact_bytes_and_artifact_hashes(self):
        raw = b'{"schema_version":1,"request_id":"r","job_id":"j","attempt_id":"a","outcome":"complete","artifacts":["result.md"],"unresolved":[]}'
        hashes = [{"source": "result.md", "sha256": "0" * 64}]
        receipt, revision = records.validate_receipt(raw, ("r", "j", "a"), hashes)
        self.assertEqual(receipt["artifacts"], ["result.md"])
        self.assertEqual(revision, "f2b19d970b4fa7b3969343ed2cb127cbf1d8d1aff3ccd81b3eea29b44950501b")
        self.assertNotEqual(
            records.validate_receipt(raw + b"\n", ("r", "j", "a"), hashes)[1], revision)
        self.assertNotEqual(
            records.validate_receipt(raw, ("r", "j", "a"), [
                {"source": "result.md", "sha256": "1" * 64}])[1], revision)
        self.assertEqual(records.validate_receipt(raw, ("r", "j", "a")), (receipt, None))
        with self.assertRaisesRegex(records.JobError, "receipt belongs to a different request/job/attempt"):
            records.validate_receipt(raw, ("other", "j", "a"), hashes)

    def test_boundary_diagnostics_are_preserved(self):
        raw = b'{"schema_version":1,"request_id":"r","job_id":"j","attempt_id":"a","outcome":"complete","artifacts":["result.md"],"unresolved":[]}'
        with self.assertRaisesRegex(ValueError, "managed receipt identity or outcome mismatch"):
            records.validate_receipt(
                raw, ("other", "j", "a"), label="managed receipt",
                identity_error=ValueError("managed receipt identity or outcome mismatch"))
        with self.assertRaisesRegex(records.JobError, "managed receipt: missing"):
            records.validate_receipt(b"{}", ("r", "j", "a"), label="managed receipt")
        with self.assertRaisesRegex(records.JobError, "schema_version"):
            records.validate_receipt(raw.replace(b'"schema_version":1', b'"schema_version":2'),
                                     ("r", "j", "a"))
