import json
from pathlib import Path
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from finish_watch import ResultWatcher, finish_task
from herdr_jobs.records import JobError


class FakeAdmission:
    def __init__(self):
        self.released = []

    def release(self, lease_id):
        self.released.append(lease_id)
        return {"released": True, "lease_id": lease_id}


class FinishWatchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name)
        self.task_id = "task-example"
        self.task_dir = self.run_dir / "tasks" / self.task_id
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "location.json").write_text(json.dumps({
            "lease_id": "lease/from-location",
            "workspace": "wExample",
        }))
        (self.run_dir / "locations.json").write_text(json.dumps({
            self.task_id: {"status": "running"},
        }))
        (self.run_dir / "STATE.md").write_text("## Run\n")
        self.result = {
            "task_id": self.task_id,
            "assignment_revision": 7,
            "outcome": "blocked",
            "summary": "Waiting on a decision.",
            "artifacts": [],
            "checks": [{
                "name": "schema",
                "status": "passed",
                "evidence": "validated fixture",
            }],
            "unresolved": ["A decision is required."],
            "next_action": "Ask the coordinator for a decision.",
        }
        (self.task_dir / "result.json").write_text(json.dumps(self.result))
        self.admission = FakeAdmission()
        self.closed_workspaces = []

    def close_workspace(self, workspace):
        self.closed_workspaces.append(workspace)

    def test_valid_blocked_result_records_blocked_and_uses_recorded_resources(self):
        finished = finish_task(
            self.run_dir,
            self.task_id,
            7,
            "Waiting on the named decision.",
            self.admission.release,
            self.close_workspace,
        )

        disposition = json.loads((self.task_dir / "disposition.json").read_text())
        locations = json.loads((self.run_dir / "locations.json").read_text())
        self.assertEqual(disposition["disposition"], "blocked")
        self.assertEqual(disposition["assignment_revision"], 7)
        self.assertEqual(locations[self.task_id]["status"], "blocked")
        self.assertEqual(self.admission.released, ["lease/from-location"])
        self.assertEqual(self.closed_workspaces, ["wExample"])
        self.assertEqual(finished["status"], "blocked")

    def test_ready_result_maps_to_completed(self):
        self.result.update(outcome="ready", artifacts=[{"path": "report.md", "kind": "report"}], unresolved=[])
        (self.task_dir / "result.json").write_text(json.dumps(self.result))

        finished = finish_task(
            self.run_dir,
            self.task_id,
            7,
            None,
            self.admission.release,
            self.close_workspace,
        )

        disposition = json.loads((self.task_dir / "disposition.json").read_text())
        locations = json.loads((self.run_dir / "locations.json").read_text())
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(disposition["disposition"], "completed")
        self.assertEqual(disposition["evidence"], "result.json (outcome: ready)")
        self.assertEqual(locations[self.task_id]["status"], "completed")

    def test_failed_result_maps_to_failed(self):
        self.result.update(outcome="failed", unresolved=["The requested operation failed."])
        (self.task_dir / "result.json").write_text(json.dumps(self.result))

        finished = finish_task(
            self.run_dir,
            self.task_id,
            7,
            None,
            self.admission.release,
            self.close_workspace,
        )

        disposition = json.loads((self.task_dir / "disposition.json").read_text())
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(disposition["disposition"], "failed")

    def test_invalid_result_stops_before_disposition_or_resource_side_effects(self):
        self.result["checks"][0]["evidence"] = "   "
        (self.task_dir / "result.json").write_text(json.dumps(self.result))

        with self.assertRaisesRegex(JobError, "non-empty string"):
            finish_task(
                self.run_dir,
                self.task_id,
                7,
                "Waiting on the named decision.",
                self.admission.release,
                self.close_workspace,
            )

        self.assertFalse((self.task_dir / "disposition.json").exists())
        self.assertEqual(self.admission.released, [])
        self.assertEqual(self.closed_workspaces, [])

    def test_invalid_result_can_be_finished_with_explicit_coordinator_disposition(self):
        self.result["checks"][0]["evidence"] = ""
        (self.task_dir / "result.json").write_text(json.dumps(self.result))

        finished = finish_task(
            self.run_dir,
            self.task_id,
            7,
            "The result is invalid and needs a corrected report.",
            self.admission.release,
            self.close_workspace,
            invalid_status="blocked",
            invalid_evidence="Coordinator inspected the file and confirmed the check evidence is absent.",
        )

        disposition = json.loads((self.task_dir / "disposition.json").read_text())
        self.assertEqual(finished["status"], "blocked")
        self.assertIn("checks[0].evidence must be a non-empty string", disposition["evidence"])
        self.assertIn("Coordinator inspected the file", disposition["evidence"])
        self.assertEqual(self.admission.released, ["lease/from-location"])

    def test_result_in_batch_directory_does_not_substitute_for_task_result(self):
        batch_result = self.run_dir / "batch-7" / "tasks" / self.task_id / "result.json"
        batch_result.parent.mkdir(parents=True)
        batch_result.write_text(json.dumps(self.result))
        (self.task_dir / "result.json").unlink()

        with self.assertRaisesRegex(JobError, "missing result.json"):
            finish_task(
                self.run_dir,
                self.task_id,
                7,
                "Waiting on the named decision.",
                self.admission.release,
                self.close_workspace,
            )

        self.assertFalse((self.task_dir / "disposition.json").exists())
        self.assertEqual(self.admission.released, [])
        self.assertEqual(self.closed_workspaces, [])

    def test_watcher_ignores_result_copied_from_an_existing_batch_result(self):
        task_id = "copy-me"
        batch_result = self.run_dir / "batch" / task_id / "result.json"
        batch_result.parent.mkdir(parents=True)
        batch_result.write_text(json.dumps({"task_id": task_id, "summary": "old result"}))
        watcher = ResultWatcher(self.run_dir, [task_id], self.run_dir / "watch-state.json")

        canonical = self.run_dir / "tasks" / task_id / "result.json"
        canonical.parent.mkdir(parents=True)
        canonical.write_bytes(batch_result.read_bytes())

        self.assertIsNone(watcher.poll())

    def test_watcher_reports_a_new_canonical_result_once(self):
        task_id = "new-result"
        watcher = ResultWatcher(self.run_dir, [task_id], self.run_dir / "watch-state.json")
        result = self.run_dir / "tasks" / task_id / "result.json"
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({"task_id": task_id, "summary": "new result"}))

        self.assertEqual(watcher.poll(), task_id)
        self.assertIsNone(watcher.poll())


if __name__ == "__main__":
    unittest.main()
