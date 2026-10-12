"""Golden markers are read and written by the real HN and ML entry points."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility.builders import AGENTS_ROOT, DailyArchive, golden, write_files, write_json


class DailyCompatibilityTests(unittest.TestCase):
    def test_route_cli_preserves_argv_labels_and_metadata(self):
        fixture = golden("daily")
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "route.json"
            write_json(config, fixture["config"])
            for workflow in ("hn", "ml"):
                script = AGENTS_ROOT / f"skills-local/{workflow}-daily-brief/{workflow}_daily_brief.py"
                for route in ("primary", "fallback"):
                    for output, expected in fixture["routes"][route].items():
                        with self.subTest(workflow=workflow, route=route, output=output):
                            result = subprocess.run(
                                [sys.executable, str(script), "route", "--config", str(config),
                                 "--route", route, "--output", output],
                                capture_output=True, text=True, check=False,
                            )
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stdout, expected)

    def test_both_runners_write_golden_success_archives(self):
        for workflow in ("hn", "ml"):
            for route, primary in (("primary", "success"), ("fallback", "auth")):
                with self.subTest(workflow=workflow, route=route), tempfile.TemporaryDirectory() as temporary:
                    archive = DailyArchive(Path(temporary), workflow)
                    result = archive.run(primary=primary)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    for name, expected in archive.fixture["archives"][route].items():
                        self.assertEqual((archive.archive / name).read_text(), expected, name)
                        self.assertEqual((archive.archive / name).stat().st_mode & 0o777, 0o600)
                    self.assertFalse((archive.archive / "task-launched").exists())

    def test_existing_task_marker_resumes_without_launch(self):
        for workflow in ("hn", "ml"):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                archive = DailyArchive(Path(temporary), workflow)
                archive.install("pending")
                result = archive.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call[:2] for call in archive.calls()], [["orchestrator", "read"]])
                self.assertEqual(archive.calls()[0][2], archive.fixture["task_id"])

    def test_interrupted_fallback_writes_golden_pending_archive_and_resumes(self):
        for workflow in ("hn", "ml"):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                archive = DailyArchive(Path(temporary), workflow)
                result = archive.run(primary="auth", task="interrupted")
                self.assertNotEqual(result.returncode, 0)
                for name, expected in archive.fixture["archives"]["pending"].items():
                    self.assertEqual((archive.archive / name).read_text(), expected)
                    self.assertEqual((archive.archive / name).stat().st_mode & 0o777, 0o600)
                self.assertFalse((archive.root / "delivered.txt").exists())
                resumed = archive.run()
                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual([call[:2] for call in archive.calls()], [
                    ["codex", "exec"], ["orchestrator", "launch"],
                    ["orchestrator", "read"], ["orchestrator", "read"],
                ])

    def test_ml_receipt_fast_path_uses_the_shared_message_id_marker(self):
        delivery = golden("delivery")
        with tempfile.TemporaryDirectory() as temporary:
            archive = DailyArchive(Path(temporary), "ml")
            archive.prepare_output()
            write_files(archive.archive, {"emailed": delivery["delivered_marker"]})
            result = archive.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(archive.calls(), [])

    def test_invalid_success_marker_does_not_authorize_archived_output(self):
        for workflow in ("hn", "ml"):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                archive = DailyArchive(Path(temporary), workflow)
                archive.prepare_output()
                archive.install("primary")
                write_files(archive.archive, {"task-succeeded": "task_id=compat-task-1\nstatus=running\n"})
                result = archive.run(primary="other")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((archive.root / "delivered.txt").exists())

    def test_success_marker_recovers_without_model(self):
        for workflow in ("hn", "ml"):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                archive = DailyArchive(Path(temporary), workflow)
                archive.install("primary")
                archive.prepare_output()
                result = archive.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(archive.calls(), [])

    def test_malformed_task_markers_refuse_a_second_launch(self):
        for workflow in ("hn", "ml"):
            for name, marker in golden("daily")["invalid_task_markers"].items():
                with self.subTest(workflow=workflow, case=name), tempfile.TemporaryDirectory() as temporary:
                    archive = DailyArchive(Path(temporary), workflow)
                    archive.install("pending")
                    write_files(archive.archive, {"task-launched": marker})
                    result = archive.run()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(archive.calls(), [])
                    self.assertEqual((archive.archive / "task-launched").read_text(), marker)

    def test_unmarked_legacy_task_keeps_workflow_specific_behavior(self):
        for workflow, expected in (("hn", 0), ("ml", 1)):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                archive = DailyArchive(Path(temporary), workflow)
                write_files(archive.archive, {"task-launched": archive.fixture["archives"]["pending"]["task-launched"]})
                result = archive.run()
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(len(archive.calls()), 1 if workflow == "hn" else 0)

    def test_terminal_failure_and_non_auth_failure_do_not_deliver(self):
        for workflow in ("hn", "ml"):
            for primary, task in (("auth", "failed"), ("other", "succeeded")):
                with self.subTest(workflow=workflow, primary=primary), tempfile.TemporaryDirectory() as temporary:
                    archive = DailyArchive(Path(temporary), workflow)
                    result = archive.run(primary=primary, task=task)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((archive.root / "delivered.txt").exists())
                    self.assertFalse((archive.archive / "task-launched").exists())


if __name__ == "__main__":
    unittest.main()
