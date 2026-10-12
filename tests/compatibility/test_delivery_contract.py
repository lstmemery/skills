"""Mailer and archive recovery consume the same receipt/Message-ID goldens."""
from contextlib import redirect_stderr
from datetime import date
import io
import json
from pathlib import Path
import smtplib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility.builders import golden, load_module, write_files, write_json


HELPERS = load_module("compat_mail_helpers", "skills-local/research-delivery/tests/test_send_report_email.py")
MAILER = HELPERS.mailer
RECOVERY = load_module("compat_recovery", "skills-local/research-delivery/reverify_pending.py")
DELIVERY = load_module("compat_delivery", "skills-local/research-delivery/deliver.py")

from delivery_state import Receipt


class FixtureMailer:
    def __init__(self, receipt, code, calls):
        self.receipt, self.code, self.calls = receipt, code, calls

    def verify(self, report, message_id, *, timeout):
        self.calls.append(message_id)
        return Receipt(self.receipt["state"], self.receipt["message_id"], self.code)

    def send(self, *args, **kwargs):
        raise AssertionError("recovery cannot send")


class DeliveryCompatibilityTests(unittest.TestCase):
    def test_receipt_states_and_exit_codes_remain_distinct(self):
        fixture = golden("delivery")
        for name, case in fixture["receipts"].items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                pdf = root / "report.pdf"
                pdf.write_bytes(b"fixture PDF")
                smtp = HELPERS.FakeSMTP(
                    auth_error=smtplib.SMTPAuthenticationError(535, b"redacted")
                    if name == "failed" else None,
                )
                imap = HELPERS.FakeIMAP(
                    smtp, received=name == "delivered",
                    existing_message_id=fixture["message_id"] if name == "existing" else None,
                )
                result = MAILER.deliver(
                    pdf, "Fixture report", message_id=fixture["message_id"],
                    verify_only=name in {"existing", "pending-verification"},
                    credentials_provider=lambda: (MAILER.SENDER, "unused"),
                    smtp_factory=lambda *args, **kwargs: smtp,
                    imap_factory=lambda *args, **kwargs: imap,
                    receipt_timeout=0, lock_directory=root / "locks",
                )
                self.assertEqual(result.as_dict(), case["result"])
                self.assertEqual(result.exit_code, case["exit_code"])
                self.assertEqual(smtp.submitted, case["smtp_submitted"])

    def test_report_workflow_consumes_each_golden_receipt(self):
        fixture = golden("delivery")
        for name in ("delivered", "submitted", "failed"):
            with self.subTest(state=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                report = root / "report.md"
                report.write_text("# Fixture report\n")
                receipt = fixture["receipts"][name]

                def runner(argv, **kwargs):
                    if argv[0] == "pandoc":
                        Path(argv[argv.index("-o") + 1]).write_bytes(b"fixture PDF")
                        return subprocess.CompletedProcess(argv, 0, "", "")
                    if argv[0] == "uv":
                        return subprocess.CompletedProcess(argv, 0, json.dumps(fixture["paste"]), "")
                    return subprocess.CompletedProcess(argv, receipt["exit_code"], json.dumps(receipt["result"]), "")

                with redirect_stderr(io.StringIO()):
                    result = DELIVERY.deliver_report(report, root / "destination", runner=runner, message_id_factory=lambda: fixture["message_id"])
                observed = result.as_dict()
                self.assertTrue(Path(observed["pdf"]).is_file())
                observed["pdf"] = "report.pdf"
                self.assertEqual(observed, fixture["report_results"][name])

    def test_legacy_and_current_markers_recover_only_exact_receipts(self):
        fixture = golden("delivery")
        for name, case in fixture["archives"].items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / fixture["day"]
                write_files(archive, {"brief.md": "# Fixture brief\n", **case["files"]})
                calls = []

                with redirect_stderr(io.StringIO()):
                    summary = RECOVERY.reverify_pending(
                        root, mailer=FixtureMailer(fixture["receipts"]["delivered"]["result"], 0, calls),
                        today=date.fromisoformat(fixture["day"]), logger=lambda line: None,
                    )
                self.assertEqual([summary.checked, summary.delivered, summary.pending, summary.invalid], case["counts"])
                self.assertEqual(len(calls), case["counts"][0])
                self.assertEqual(calls, [fixture["message_id"]] * case["counts"][0])
                for filename, contents in case["files"].items():
                    self.assertEqual((archive / filename).read_text(), contents)
                self.assertEqual(list(archive.glob("emailed.unverified.*")), [])
                if summary.delivered:
                    record = RECOVERY.DeliveryRecord.read(archive / "brief.md")
                    self.assertEqual((record.state, record.message_id), ("delivered", fixture["message_id"]))
                    self.assertEqual((archive / "brief.delivery.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual((archive / "emailed").exists(), "emailed" in case["files"])

    def test_versioned_pending_record_recovers_once_without_legacy_writes(self):
        fixture = golden("delivery-v1")
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "2026-10-02"
            write_files(archive, {"brief.md": fixture["markdown"], "fixture.pdf": fixture["pdf"]})
            write_json(archive / "brief.delivery.json", fixture["record"])
            calls = []
            receipt = dict(state="delivered", message_id=fixture["record"]["message_id"])
            mailer = FixtureMailer(receipt, 0, calls)
            summary = RECOVERY.reverify_pending(Path(temporary), mailer=mailer,
                                                today=date(2026, 10, 2), logger=lambda line: None)
            self.assertEqual((summary.checked, summary.delivered), (1, 1))
            record = fixture["record"] | {"state": "delivered"}
            self.assertEqual(json.loads((archive / "brief.delivery.json").read_text()), record)
            again = RECOVERY.reverify_pending(Path(temporary), mailer=mailer,
                                              today=date(2026, 10, 2), logger=lambda line: None)
            self.assertEqual(again.checked, 0)
            self.assertEqual(calls, [record["message_id"]])
            self.assertFalse((archive / "email-message-id").exists())
            self.assertFalse((archive / "emailed").exists())


    def test_unverified_or_mismatched_receipt_does_not_mark_delivery(self):
        fixture = golden("delivery")
        for name, code in (("submitted", 75), ("failed", 1), ("delivered", 75), ("wrong-id", 0)):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / fixture["day"]
                write_files(archive, {"brief.md": "# Fixture\n", "email-message-id": fixture["message_marker"]})
                receipt = fixture["receipts"]["delivered" if name == "wrong-id" else name]["result"]
                if name == "wrong-id":
                    receipt["message_id"] = "<different@example.invalid>"
                calls = []

                summary = RECOVERY.reverify_pending(
                    root, mailer=FixtureMailer(receipt, code, calls),
                    today=date.fromisoformat(fixture["day"]), logger=lambda line: None,
                )
                self.assertEqual((summary.checked, summary.pending, summary.delivered), (1, 1, 0))
                self.assertEqual(calls, [fixture["message_id"]])
                self.assertFalse((archive / "emailed").exists())


if __name__ == "__main__":
    unittest.main()
