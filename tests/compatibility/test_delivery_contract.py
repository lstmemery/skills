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
from compatibility.builders import golden, load_module, write_files


HELPERS = load_module("compat_mail_helpers", "skills-local/research-delivery/tests/test_send_report_email.py")
MAILER = HELPERS.mailer
RECOVERY = load_module("compat_recovery", "skills-local/research-delivery/reverify_pending.py")
DELIVERY = load_module("compat_delivery", "skills-local/research-delivery/deliver.py")


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
                    result = DELIVERY.deliver_report(report, root / "destination", runner=runner)
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

                def runner(argv, **kwargs):
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, 0, json.dumps(fixture["receipts"]["delivered"]["result"]), "")

                with patch.object(RECOVERY.time, "strftime", return_value=fixture["delivered_at"]):
                    summary = RECOVERY.reverify_pending(
                        root, mailer=Path("fixture-mailer.py"), runner=runner,
                        today=date.fromisoformat(fixture["day"]), logger=lambda line: None,
                    )
                self.assertEqual([summary.checked, summary.delivered, summary.pending, summary.invalid], case["counts"])
                self.assertEqual(len(calls), case["counts"][0])
                for argv in calls:
                    self.assertIn("--verify-only", argv)
                    self.assertNotIn("--check-existing", argv)
                    self.assertEqual(argv[argv.index("--message-id") + 1], fixture["message_id"])
                self.assertEqual((archive / "email-message-id").read_text(), case["files"]["email-message-id"])
                if summary.delivered:
                    self.assertEqual((archive / "emailed").read_text(), fixture["delivered_marker"])
                    self.assertEqual((archive / "emailed").stat().st_mode & 0o777, 0o600)
                    if "emailed" in case["files"]:
                        backups = list(archive.glob("emailed.unverified.*"))
                        self.assertEqual(len(backups), 1)
                        self.assertEqual(backups[0].read_text(), case["files"]["emailed"])
                else:
                    self.assertEqual((archive / "emailed").exists(), "emailed" in case["files"])

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

                def runner(argv, **kwargs):
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, code, json.dumps(receipt), "")

                summary = RECOVERY.reverify_pending(root, mailer=Path("fixture-mailer.py"), runner=runner,
                                                    today=date.fromisoformat(fixture["day"]), logger=lambda line: None)
                self.assertEqual((summary.checked, summary.pending, summary.delivered), (1, 1, 0))
                self.assertEqual(len(calls), 1)
                self.assertIn("--verify-only", calls[0])
                self.assertFalse((archive / "emailed").exists())


if __name__ == "__main__":
    unittest.main()
