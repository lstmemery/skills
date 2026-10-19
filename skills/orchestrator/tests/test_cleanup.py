import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.admission import AdmissionStore
from herdr_jobs.cleanup import cleanup_stale, close_workspace
from herdr_jobs.records import JobError


class CleanupStaleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs = self.root / "runs"
        self.run = self.runs / "fixture-run"
        self.run.mkdir(parents=True)
        self.state = {"request": {"request_id": "fixture-run"}, "jobs": [{
            "admission_lease_id": "lease-a", "admission_acquired": True,
            "settled": True, "workspace_id": "w1", "spec": {"job_id": "job-a"}}]}
        (self.run / "state.json").write_text(json.dumps(self.state))
        self.store = AdmissionStore(self.root / "admission")
        self.store.acquire("codex", "model", "lease-a", cap=3, run_id="fixture-run")
        self.args = argparse.Namespace(runs_root=str(self.runs), wait_seconds=5, apply=False)

    def mock_live_and_finish(self, completed_commands):
        def run(argv, **kwargs):
            if argv[1:3] == ["workspace", "list"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"result": {
                    "workspaces": [{"workspace_id": "w1"}]}}), "")
            completed_commands.append(argv)
            # Model finish's safety contract: lease release precedes any workspace close.
            self.store.release("lease-a")
            self.assertEqual(self.store.status()["active_count"], 0)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"finish_complete": True}), "")
        return run

    def test_lease_bound_run_uses_finish_path_before_workspace_close(self):
        commands = []
        with patch("herdr_jobs.cleanup.subprocess.run", side_effect=self.mock_live_and_finish(commands)):
            self.args.apply = True
            result = cleanup_stale(self.args, env={}, admission_dir=self.root / "admission")
        self.assertEqual(result["finished"][0]["result"]["finish_complete"], True)
        self.assertIn("finish", commands[0])
        self.assertEqual(self.store.status()["active_count"], 0)

    def test_orphan_lease_refuses_without_closing_and_names_run_directory(self):
        self.store.release("lease-a")
        orphan = self.store.acquire("codex", "model", "orphan", cap=3, run_id="fixture-run")
        self.store.record_unknown_effect(orphan["lease_id"], {
            "action": "start", "status": "unknown", "pane_id": "w1:p1",
            "agent_name": "fixture-agent", "observation": {"state": "unknown",
            "identity_verified": False, "prompt_verified": False}})
        self.state["jobs"][0].update(admission_lease_id="old-lease", admission_acquired=False,
                                      settled=True)
        (self.run / "state.json").write_text(json.dumps(self.state))
        calls = []
        def unavailable(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["workspace", "list"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"result": {"workspaces": [{"workspace_id": "w1"}]}}), "")
            return subprocess.CompletedProcess(argv, 1, "", "agent not found")
        with patch("herdr_jobs.cleanup.subprocess.run", side_effect=unavailable):
            self.args.apply = True
            result = cleanup_stale(self.args, env={}, admission_dir=self.root / "admission")
        refusal = result["released_orphans"][0]
        self.assertFalse(refusal["released"])
        self.assertEqual(refusal["run_dir"], str(self.run))
        self.assertIn(str(self.run), refusal["reason"])
        self.assertFalse(any("workspace" in command and "close" in command for command in calls))
        self.assertEqual(self.store.status()["active_count"], 1)

    def test_orphan_without_pending_effect_refuses_release(self):
        self.store.release("lease-a")
        self.store.acquire("codex", "model", "orphan", cap=3, run_id="fixture-run")
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["workspace", "list"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"result": {"workspaces": []}}), "")
            self.fail(f"unexpected command: {argv}")
        with patch("herdr_jobs.cleanup.subprocess.run", side_effect=run):
            self.args.apply = True
            result = cleanup_stale(self.args, env={}, admission_dir=self.root / "admission")
        refusal = result["released_orphans"][0]
        self.assertFalse(refusal["released"])
        self.assertIn(str(self.run), refusal["reason"])
        self.assertEqual(self.store.status()["active_count"], 1)
        self.assertEqual(calls, [["herdr", "workspace", "list"]])

    def test_non_lease_panes_are_untouched(self):
        self.store.release("lease-a")
        (self.run / "state.json").write_text(json.dumps({"request": {"request_id": "fixture-run"}, "jobs": []}))
        calls = []
        with patch("herdr_jobs.cleanup.subprocess.run", side_effect=self.mock_live_and_finish(calls)):
            result = cleanup_stale(self.args, env={}, admission_dir=self.root / "admission")
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["orphan_leases"], [])
        self.assertEqual(calls, [])

    def test_close_guard_refuses_lease_bound_workspace_and_names_run(self):
        with self.assertRaisesRegex(JobError, str(self.run)):
            close_workspace("w1", env={}, runs_root=self.runs,
                            admission_dir=self.root / "admission")

    def test_close_guard_refuses_coordinator_workspace_from_pane_environment(self):
        with self.assertRaisesRegex(JobError, "coordinator workspace"):
            close_workspace("w1", env={"HERDR_PANE_ID": "w1:p9"},
                            runs_root=self.runs, admission_dir=self.root / "admission")

    def test_close_guard_keeps_existing_behavior_for_non_lease_workspace(self):
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        result = close_workspace("ordinary", env={}, runs_root=self.runs,
                                 admission_dir=self.root / "admission", run_command=run)
        self.assertEqual(result, {"closed": True, "workspace_id": "ordinary"})
        self.assertEqual(commands, [["herdr", "workspace", "close", "ordinary"]])


if __name__ == "__main__":
    unittest.main()
