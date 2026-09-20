import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/herdr-jobs.py"
DRIVER = PACKAGE / "tests/driver.py"
POLICY = PACKAGE / "launch-policy.json"


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        self.task = self.root / "task ' $(data).md"
        self.task.write_text("Inspect the assigned fixture. Literal `printf secret` and $(touch SHOULD_NOT_EXIST).\n")
        self.policy = self.root / "policy.json"
        self.policy.write_bytes(POLICY.read_bytes())
        self.manifest = self.root / "manifest.json"
        self.fixture = self.root / "fixture.json"
        self.write_jobs(1)
        self.configure()

    def write_jobs(self, count, kinds=None):
        jobs = []
        for index in range(count):
            kind = kinds[index] if kinds else "ordinary"
            job = {"job_id": f"job{index}", "name": f"Fixture {index}", "task_kind": kind,
                   "task_file": str(self.task), "cwd": str(self.root), "output_expectation": "A fixture report."}
            if kind == "ordinary":
                job["override"] = {"runtime": "codex", "instruction": "Use Codex for this fixture."}
            jobs.append(job)
        self.manifest.write_text(json.dumps({"schema_version": 1, "request_id": "test-request", "jobs": jobs}))

    def configure(self, **kwargs):
        self.fixture.write_text(json.dumps({"request_id": "test-request", **kwargs}))

    def argv(self, operation="run", real=False, **kwargs):
        args = [sys.executable, "-B", str(SCRIPT if real else DRIVER), operation,
                "--run-dir", str(self.run), "--host-contract", str(self.fixture), "--wait-seconds", "0.3"]
        if operation == "run":
            args += ["--manifest", str(self.manifest), "--policy", str(self.policy)]
        for key, value in kwargs.items():
            args += ["--" + key.replace("_", "-")]
            if value is not True:
                args.append(str(value))
        return args

    def call(self, operation="run", real=False, **kwargs):
        process = subprocess.run(self.argv(operation, real, **kwargs), capture_output=True, text=True,
                                 cwd=self.root, timeout=10)
        self.assertEqual(process.stderr, "", process.stderr)
        return process.returncode, json.loads(process.stdout)

    def events(self):
        return json.loads((self.root / "events.json").read_text())

    def state(self):
        return json.loads((self.run / "state.json").read_text())

    def test_preview_has_no_writes_or_transport_calls(self):
        code, result = self.call(real=True, preview=True)
        self.assertEqual((code, result["batch_state"], result["concurrency"]), (0, "preview", 4))
        self.assertFalse(self.run.exists())
        self.assertFalse((self.root / "events.json").exists())

    def test_zero_budget_is_local_only_and_returns_continuation(self):
        code, result = self.call(real=True, wait_seconds=0)
        self.assertEqual((code, result["batch_state"]), (0, "checkpointed"))
        self.assertFalse((self.root / "events.json").exists())
        self.assertFalse((self.run / "state.json").exists())
        self.assertIn("--manifest", result["next_action"]["argv"])
        self.call()
        before = self.events()
        code, result = self.call("status", real=True, wait_seconds=0)
        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        self.assertEqual(self.events(), before)

    def test_large_valid_batch_state_can_resume(self):
        self.write_jobs(6)
        self.task.write_text("x" * 90000)
        self.assertEqual(self.call(wait_seconds=2)[0], 0)
        self.assertGreater((self.run / "state.json").stat().st_size, 1048576)
        self.assertEqual(self.call("status", wait_seconds=2)[0], 0)

    def test_oversized_second_prompt_is_rejected_before_any_launch(self):
        self.write_jobs(2)
        large = self.root / "large.md"
        large.write_text("x" * 99900)
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][1]["task_file"] = str(large)
        self.manifest.write_text(json.dumps(manifest))
        self.assertEqual(self.call(preview=True)[0], 2)
        self.assertEqual(self.call()[0], 2)
        self.assertFalse((self.root / "events.json").exists())
        self.assertFalse((self.run / "state.json").exists())

    def test_live_cli_is_blocked_in_jail_before_host_access(self):
        if not Path("/ctx").exists():
            self.skipTest("jail-specific live guard")
        code, result = self.call(real=True)
        self.assertEqual((code, result["error"]), (4, "unavailable_capability"))
        self.assertFalse((self.root / "events.json").exists())
        self.assertFalse((self.run / "state.json").exists())

    def test_mixed_batch_starts_four_before_observation_then_queued_work(self):
        self.write_jobs(5, ["ordinary", "shopping", "deep_research", "ordinary", "ordinary"])
        code, result = self.call(wait_seconds=1)
        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        events = self.events()
        first_observe = next(i for i, event in enumerate(events) if event["action"] == "observe")
        starts = [event for event in events[:first_observe] if event["action"] in ("prompt", "jail")]
        self.assertEqual(len(starts), 4)
        self.assertEqual([job["route"]["mode"] for job in result["jobs"]], ["agent", "jail", "jail", "agent", "agent"])
        self.assertTrue(all(job["settled"] for job in result["jobs"]))
        self.assertEqual(result["acceptance"], "pending")
        for event in events:
            if event["action"] in ("start", "prompt", "jail", "observe"):
                self.assertTrue(event["pane_id"].startswith("moved:"))

    def test_repeat_and_resume_do_not_relaunch(self):
        self.call()
        self.call()
        self.call("resume")
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 1)
        self.assertEqual(sum(event["action"] == "split" for event in self.events()), 1)

    def test_task_content_change_conflicts(self):
        self.call()
        self.task.write_text("Changed request content")
        code, result = self.call()
        self.assertEqual((code, result["error"]), (3, "conflict"))
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 1)

    def test_changed_policy_conflicts(self):
        self.call()
        policy = json.loads(self.policy.read_text())
        policy["default_concurrency"] = 2
        self.policy.write_text(json.dumps(policy))
        self.assertEqual(self.call()[0], 3)

    def test_prompt_timeout_reconciles_receipt_without_resubmission(self):
        self.configure(jobs={"job0": "prompt_timeout"})
        code, result = self.call()
        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        self.call("resume")
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 1)
        self.assertTrue(any(event.get("reconciled_by") == "matching receipt" for event in self.state()["jobs"][0]["history"]))

    def test_working_does_not_prove_ambiguous_prompt_delivery(self):
        self.configure(jobs={"job0": "ambiguous"})
        self.assertEqual(self.call()[0], 6)
        self.assertEqual(self.call("resume")[0], 6)
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 1)

    def test_ambiguous_split_is_not_replayed_and_other_job_finishes(self):
        self.write_jobs(2)
        self.configure(jobs={"job0": "split_unknown"})
        code, result = self.call()
        self.assertEqual(code, 6)
        self.assertIsNotNone(result["jobs"][1]["collection"])
        self.call("resume")
        self.assertEqual(sum(event["action"] == "split" and event["job_id"] == "job0" for event in self.events()), 1)

    def test_missing_invalid_stale_or_escaping_receipts_never_collect(self):
        for mode in ("missing", "malformed", "stale", "missing_artifact", "escape", "symlink"):
            with self.subTest(mode=mode):
                self.run = self.root / mode
                self.configure(jobs={"job0": mode})
                _, result = self.call()
                self.assertFalse(result["collection_complete"])
                self.assertIsNone(result["jobs"][0]["collection"])
                self.assertIsNotNone(result["jobs"][0]["collection_error"])

    def test_failed_job_returns_partial_and_independent_jobs_continue(self):
        self.write_jobs(5)
        self.configure(jobs={"job0": "failed"})
        code, result = self.call(wait_seconds=1)
        self.assertEqual((code, result["batch_state"]), (10, "partial"))
        self.assertTrue(result["collection_complete"])
        self.assertEqual(result["jobs"][4]["collection"]["outcome"], "complete")

    def test_four_blocked_workers_hold_slots(self):
        self.write_jobs(5)
        self.configure(jobs={f"job{index}": "blocked" for index in range(4)})
        code, result = self.call()
        self.assertEqual(code, 10)
        self.assertEqual(result["active_jobs"], 4)
        self.assertEqual(result["jobs"][4]["phase"], "pending")

    def test_finite_jail_exits_release_slots_and_collect(self):
        self.write_jobs(5, ["shopping"] * 5)
        self.configure(jobs={f"job{index}": "exited_jail" for index in range(5)})
        code, result = self.call(wait_seconds=1)
        self.assertEqual((code, result["batch_state"], result["active_jobs"]), (0, "collected", 0))
        self.assertEqual(result["jobs"][4]["observed"]["state"], "exited")

    def test_malformed_nested_state_has_structured_error(self):
        self.call()
        state = self.state()
        state["jobs"][0]["pending_effect"] = "not an object"
        (self.run / "state.json").write_text(json.dumps(state))
        code, result = self.call("status")
        self.assertEqual((code, result["error"]), (2, "invalid_input"))

    def test_status_never_starts_or_prompts(self):
        self.write_jobs(5)
        self.configure(jobs={f"job{index}": "blocked" for index in range(4)})
        self.call()
        before = len(self.events())
        self.call("status")
        self.assertTrue(all(event["action"] == "observe" for event in self.events()[before:]))

    def test_session_identity_change_blocks_resume(self):
        self.call()
        self.configure(session_id="different-server")
        self.assertEqual(self.call("resume")[0], 3)

    def test_unavailable_preflight_does_not_launch(self):
        self.configure(preflight_error="jail export is unavailable")
        code, result = self.call()
        self.assertEqual((code, result["error"]), (4, "unavailable_capability"))
        self.assertFalse((self.root / "events.json").exists())

    def test_strict_schema_and_duplicate_keys(self):
        manifest = json.loads(self.manifest.read_text())
        cases = [dict(manifest, install=True), dict(manifest, concurrency=True), dict(manifest, jobs=[])]
        malformed_kind = copy.deepcopy(manifest)
        malformed_kind["jobs"][0]["task_kind"] = []
        cases.append(malformed_kind)
        for case in cases:
            self.manifest.write_text(json.dumps(case))
            self.assertEqual(self.call(preview=True)[0], 2)
        self.manifest.write_text('{"schema_version":1,"schema_version":1}')
        self.assertEqual(self.call(preview=True)[0], 2)

    def test_ordinary_default_requires_a_runtime_decision(self):
        manifest = json.loads(self.manifest.read_text())
        del manifest["jobs"][0]["override"]
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call(preview=True)
        self.assertEqual((code, result["error"]), (5, "decision_needed"))

    def test_shell_metacharacters_remain_task_data(self):
        self.call()
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())
        self.assertIn("$(touch SHOULD_NOT_EXIST)", (self.root / "prompt-job0.txt").read_text())

    def test_atomic_collection_snapshot_is_retained(self):
        _, result = self.call()
        artifact = Path(result["jobs"][0]["collection"]["artifacts"][0]["path"])
        original = artifact.read_bytes()
        source = Path(self.state()["jobs"][0]["host_output"]) / "result.md"
        source.write_text("new output")
        self.call("status")
        self.assertEqual(artifact.read_bytes(), original)

    def test_missing_or_corrupted_snapshot_is_restored_from_verified_source(self):
        _, result = self.call()
        artifact = Path(result["jobs"][0]["collection"]["artifacts"][0]["path"])
        receipt = Path(result["jobs"][0]["collection"]["receipt_path"])
        original = artifact.read_bytes()
        artifact.unlink()
        receipt.unlink()
        self.assertEqual(self.call("status")[1]["batch_state"], "collected")
        self.assertEqual(artifact.read_bytes(), original)
        self.assertTrue(receipt.is_file())
        artifact.write_text("corrupted")
        self.call("status")
        self.assertEqual(artifact.read_bytes(), original)

    def test_oversized_receipt_keeps_collection_incomplete(self):
        self.call()
        output = Path(self.state()["jobs"][0]["host_output"])
        receipt = json.loads((output / "receipt.json").read_text())
        receipt["unresolved"] = ["x" * 262144]
        (output / "receipt.json").write_text(json.dumps(receipt))
        _, result = self.call("status")
        self.assertFalse(result["collection_complete"])
        self.assertIsNotNone(result["jobs"][0]["collection_error"])

    def test_crash_after_each_effect_never_replays_uncertain_effect(self):
        for action in ("split", "move", "start", "prompt", "jail"):
            with self.subTest(action=action):
                self.run = self.root / f"crash-{action}"
                (self.root / "crashed").unlink(missing_ok=True)
                (self.root / "events.json").unlink(missing_ok=True)
                self.write_jobs(1, ["shopping"] if action == "jail" else None)
                self.configure(crash_after=action)
                first = subprocess.run(self.argv(), capture_output=True, timeout=10)
                self.assertEqual(first.returncode, 91)
                self.assertEqual(self.state()["jobs"][0]["pending_effect"]["action"], action)
                self.call("resume")
                self.assertEqual(sum(event["action"] == action for event in self.events()), 1)

    def test_concurrent_calls_cannot_both_launch(self):
        self.configure(jobs={"job0": "slow"})
        first = subprocess.Popen(self.argv(wait_seconds=2), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: first.kill() if first.poll() is None else None)
        for _ in range(100):
            if (self.root / "events.json").exists():
                break
            time.sleep(0.01)
        self.assertEqual(self.call()[0], 3)
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(stderr, b"")
        self.assertEqual(json.loads(stdout)["batch_state"], "collected")
        self.assertEqual(sum(event["action"] == "split" for event in self.events()), 1)


if __name__ == "__main__":
    unittest.main()
