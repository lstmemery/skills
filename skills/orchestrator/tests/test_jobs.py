import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/herdr-jobs.py"
DRIVER = PACKAGE / "tests/driver.py"
POLICY = PACKAGE / "launch-policy.json"
sys.path.insert(0, str(PACKAGE / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from herdr_jobs.engine import Engine
from support import isolate_admission_state
from herdr_jobs.records import digest, encoded


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.admission_dir = self.root / "shared-admission"
        isolate_admission_state(self, self.admission_dir)
        self.run = self.root / "run"
        self.task = self.root / "task ' $(data).md"
        self.task.write_text("Inspect the assigned fixture. Literal `printf secret` and $(touch SHOULD_NOT_EXIST).\n")
        self.policy = self.root / "policy.json"
        self.policy.write_bytes(POLICY.read_bytes())
        self.manifest = self.root / "manifest.json"
        self.fixture = self.root / "fixture.json"
        self.write_jobs(1)
        self.configure()

    def write_jobs(self, count, kinds=None, runtime="codex", concurrency=None, provider=None, model=None):
        jobs = []
        for index in range(count):
            kind = kinds[index] if kinds else "ordinary"
            job = {"job_id": f"job{index}", "name": f"Fixture {index}", "task_kind": kind,
                   "task_file": str(self.task), "cwd": str(self.root), "output_expectation": "A fixture report.",
                   "writes_repository": False}
            if kind == "ordinary":
                override = {"runtime": runtime, "instruction": f"Use {runtime.capitalize()} for this fixture."}
                if provider is not None:
                    override["provider"] = provider
                if model is not None:
                    override["model"] = model
                job["override"] = override
            jobs.append(job)
        manifest = {"schema_version": 1, "request_id": "test-request", "jobs": jobs}
        if concurrency is not None:
            manifest["concurrency"] = concurrency
        self.manifest.write_text(json.dumps(manifest))

    def write_add_job(self, job_id="job-added", task_id="task-added", revision=1,
                      runtime="pi", provider="llama-local", model="qwen3.8-27b-uncensored", effort="high",
                      retry_override=None):
        add_manifest = self.root / "add-manifest.json"
        job = {"job_id": job_id, "name": "Incremental fixture", "task_kind": "ordinary",
               "task_file": str(self.task), "cwd": str(self.root),
               "output_expectation": "A fixture report.", "writes_repository": False,
               "worker_result": {"task_id": task_id, "assignment_revision": revision},
               "override": {"runtime": runtime, "provider": provider, "model": model,
                            "effort": effort, "instruction": f"Use {runtime} with {model}."}}
        manifest = {"schema_version": 1, "request_id": "test-request", "jobs": [job]}
        if retry_override is not None:
            manifest["retry_override"] = retry_override
        add_manifest.write_text(json.dumps(manifest))
        self.manifest = add_manifest

    def prepare_pi_retry_run(self, retry_override):
        global_agent = self.root / "pi-global"
        global_agent.mkdir()
        (global_agent / "settings.json").write_text('{"retry":{"enabled":true,"maxRetries":2}}')
        (global_agent / "auth.json").write_text("{}")

        policy = json.loads(self.policy.read_text())
        policy["routes"]["ordinary"]["runtime"] = "pi"
        policy["runtime_kinds"]["pi"] = "pi"
        self.policy.write_text(json.dumps(policy))
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0].pop("override")
        manifest["retry_override"] = retry_override
        self.manifest.write_text(json.dumps(manifest))
        return global_agent

    def configure(self, **kwargs):
        self.fixture.write_text(json.dumps({"request_id": "test-request", **kwargs}))

    def argv(self, operation="run", real=False, **kwargs):
        args = [sys.executable, "-B", str(SCRIPT if real else DRIVER), operation,
                "--run-dir", str(self.run), "--host-contract", str(self.fixture), "--wait-seconds", "0.3"]
        if operation in ("run", "add"):
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

    def restore_pending_checkpoint(self, task_kind):
        state = self.state()
        request = state["request"]
        spec = request["jobs"][0]
        spec["task_kind"] = task_kind
        if task_kind == "shopping":
            spec["route"] = copy.deepcopy(request["policy"]["routes"]["shopping"])
            spec["kind"] = request["policy"]["runtime_kinds"][spec["route"]["runtime"]]
        job = state["jobs"][0]
        job["spec"] = copy.deepcopy(spec)
        job.update(phase="pending", pane_id=None, pending_effect=None, settled=False,
                   observed=None, activity_seen=False, collection=None, collection_error=None,
                   issue=None, admission_acquired=False)
        if task_kind == "shopping":
            suffix = f"c9-{digest(request['request_id'].encode())[:12]}/{spec['job_id']}-{job['attempt_id']}"
            job["host_output"] = str(self.root / "export" / suffix)
            job["worker_output"] = str(Path("/worker-workspace") / suffix)
        state["request_digest"] = digest(encoded(request))
        (self.run / "state.json").write_bytes(encoded(state) + b"\n")
        (self.root / "events.json").write_text("[]")
        return (self.run / "state.json").read_bytes()

    def test_codex_pending_start_predicate_matches_only_managed_codex_starts(self):
        job = {"spec": {"route": {"mode": "agent", "runtime": "codex"}}}
        pending = {"action": "start", "started_at": "fixture"}
        self.assertTrue(Engine.is_codex_pending_start(job, pending))
        self.assertFalse(Engine.is_codex_pending_start(job, None))
        self.assertFalse(Engine.is_codex_pending_start(job, {"action": "prompt"}))
        for route in ({"mode": "jail", "runtime": "codex"},
                      {"mode": "agent", "runtime": "other"}):
            with self.subTest(route=route):
                non_codex_job = {"spec": {"route": route}}
                self.assertFalse(Engine.is_codex_pending_start(non_codex_job, pending))

    def test_preview_has_no_writes_or_transport_calls(self):
        code, result = self.call(real=True, preview=True)
        self.assertEqual((code, result["batch_state"], result["concurrency"]), (0, "preview", 4))
        self.assertFalse(self.run.exists())
        self.assertFalse((self.root / "events.json").exists())

    def test_shopping_admission_refuses_managed_route_with_host_pane_command(self):
        self.write_jobs(1, ["shopping"])
        code, result = self.call(preview=True)
        self.assertEqual((code, result.get("error")), (5, "decision_needed"), result)
        self.assertIn("omp-train --claude", result["message"])
        self.assertIn("PREFERENCES.md", result["message"])
        self.assertFalse(self.run.exists())
        self.assertFalse((self.root / "events.json").exists())

    def test_resume_refuses_legacy_pending_shopping_checkpoint_without_launch_effects(self):
        self.call()
        checkpoint = self.restore_pending_checkpoint("shopping")

        code, result = self.call("resume")

        self.assertEqual((code, result.get("error")), (5, "decision_needed"), result)
        self.assertIn("omp-train --claude", result["message"])
        self.assertIn("PREFERENCES.md", result["message"])
        self.assertEqual(self.events(), [])
        self.assertEqual((self.run / "state.json").read_bytes(), checkpoint)

    def test_resume_relaunches_pending_ordinary_checkpoint(self):
        self.call()
        self.restore_pending_checkpoint("ordinary")

        code, result = self.call("resume")

        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)

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

    def test_add_appends_one_worker_to_a_persistent_run_without_replaying_old_jobs(self):
        self.call()
        prior_state = self.state()
        prior_attempt = prior_state["jobs"][0]["attempt_id"]
        self.write_add_job()

        code, result = self.call("add", wait_seconds=1)

        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        state = self.state()
        self.assertEqual([job["spec"]["job_id"] for job in state["jobs"]], ["job0", "job-added"])
        self.assertEqual(state["jobs"][0]["attempt_id"], prior_attempt)
        self.assertEqual(sum(event["action"] == "start" and event["job_id"] == "job0"
                             for event in self.events()), 1)
        self.assertEqual(state["jobs"][1]["provider"], "llama-local")
        self.assertEqual(state["jobs"][1]["resolved_model"], "qwen3.8-27b-uncensored")

    def test_old_zai_run_record_still_loads(self):
        self.call()
        state = self.state()
        spec = state["request"]["jobs"][0]
        spec["route"] = {"mode": "agent", "runtime": "pi"}
        spec["kind"] = "pi"
        spec["provider"] = "zai"
        spec["model"] = "glm-5.3-flash"
        spec["override"] = {"runtime": "pi", "provider": "zai", "model": "glm-5.3-flash",
                             "instruction": "Legacy fixture from a retired provider."}
        state["jobs"][0]["spec"] = copy.deepcopy(spec)
        state["jobs"][0]["resolved_model"] = "glm-5.3-flash"
        state["jobs"][0]["provider"] = "zai"
        state["jobs"][0]["admission_model"] = "glm-5.3-flash"
        state["request_digest"] = digest(encoded(state["request"]))
        (self.run / "state.json").write_bytes(encoded(state) + b"\n")

        legacy = Engine(self.run, None, None)
        legacy.load()

        self.assertEqual(legacy.state["jobs"][0]["provider"], "zai")
        self.assertEqual(legacy.state["jobs"][0]["resolved_model"], "glm-5.3-flash")

    def test_add_applies_the_run_pinned_retry_override_to_matching_workers(self):
        retry_override = {"pi": {"max_retries": 10, "max_agent_delay_ms": 120000}}
        global_agent = self.prepare_pi_retry_run(retry_override)

        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(global_agent)}):
            self.call()
            self.write_add_job(retry_override=retry_override)
            code, result = self.call("add", wait_seconds=1)

        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        state = self.state()
        added_job = state["jobs"][1]
        self.assertEqual(state["retry_override"], retry_override)
        self.assertEqual(added_job["retry_override"], retry_override["pi"])
        self.assertIsNotNone(added_job["retry_profile"])
        settings = json.loads((Path(added_job["retry_profile"]["agent_dir"]) / "settings.json").read_text())
        self.assertEqual(settings["retry"], {"enabled": True, "maxRetries": 10,
                                               "maxAgentDelayMs": 120000})

    def test_add_rejects_a_retry_override_that_changes_the_run_pin(self):
        pinned = {"pi": {"max_retries": 10}}
        global_agent = self.prepare_pi_retry_run(pinned)

        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(global_agent)}):
            self.call()
            self.write_add_job(retry_override={"pi": {"max_retries": 9}})
            code, result = self.call("add")

        self.assertEqual((code, result["error"]), (3, "conflict"))
        self.assertIn("run's pinned retry_override", result["message"])
        self.assertEqual(len(self.state()["jobs"]), 1)
        self.assertFalse(any(event["action"] == "start" and event["job_id"] == "job-added"
                             for event in self.events()))

    def test_add_rejects_a_retry_override_when_the_run_has_no_pin(self):
        self.call()
        self.write_add_job(retry_override={"pi": {"max_retries": 10}})

        code, result = self.call("add")

        self.assertEqual((code, result["error"]), (3, "conflict"))
        self.assertIn("run's pinned retry_override", result["message"])
        self.assertEqual(len(self.state()["jobs"]), 1)
        self.assertFalse(any(event["action"] == "start" and event["job_id"] == "job-added"
                             for event in self.events()))

    def test_add_rejects_duplicate_job_or_worker_identity(self):
        self.call()
        self.write_add_job()
        self.call("add")
        code, result = self.call("add")

        self.assertEqual((code, result["error"]), (3, "conflict"))
        self.assertIn("job_id already exists", result["message"])
        self.assertEqual(sum(event["action"] == "start" and event["job_id"] == "job-added"
                             for event in self.events()), 1)

    def test_add_rejects_duplicate_worker_identity_even_with_a_new_job_id(self):
        self.call()
        self.write_add_job()
        self.call("add")
        self.write_add_job(job_id="job-another", task_id="task-added")
        code, result = self.call("add")

        self.assertEqual((code, result["error"]), (3, "conflict"))
        self.assertIn("worker task/revision already exists", result["message"])
        self.assertFalse(any(event["job_id"] == "job-another" for event in self.events()))

    def test_worker_finish_validates_result_records_disposition_releases_and_closes(self):
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["worker_result"] = {"task_id": "task-finish", "assignment_revision": 2}
        self.manifest.write_text(json.dumps(manifest))
        self.call()

        code, result = self.call("finish")

        self.assertEqual((code, result["finish_complete"]), (0, True))
        self.assertEqual(result["jobs"][0]["worker_disposition"], "completed")
        self.assertTrue(result["jobs"][0]["workspace_closed"])
        self.assertEqual(result["finish"]["jobs"]["job0"]["worker_result_outcome"], "ready")
        output = Path(self.state()["jobs"][0]["host_output"])
        disposition = json.loads((output / "disposition.json").read_text())
        self.assertEqual((disposition["task_id"], disposition["assignment_revision"],
                          disposition["disposition"]), ("task-finish", 2, "completed"))
        self.assertEqual(sum(event["action"] == "workspace_close" for event in self.events()), 1)
        self.assertEqual(result["next_action"]["kind"], "review")
        self.assertIn("Validated each worker result.json schema", result["next_action"]["message"])
        self.assertNotIn("accept the work", result["next_action"]["message"].lower())

    def prepare_worker_outcome(self, outcome):
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["worker_result"] = {"task_id": "task-finish", "assignment_revision": 2}
        self.manifest.write_text(json.dumps(manifest))
        self.configure(worker_results={"job0": {"outcome": outcome,
                                                  "summary": f"Fixture worker {outcome}.",
                                                  "unresolved": [] if outcome == "ready" else [f"Fixture {outcome} detail."]}})
        return self.call()

    def test_finish_preserves_blocked_worker_result_disposition_and_requests_review(self):
        self.prepare_worker_outcome("blocked")

        code, result = self.call("finish")

        self.assertEqual((code, result["batch_state"], result["finish_complete"]), (10, "partial", True))
        self.assertEqual(result["jobs"][0]["worker_disposition"], "blocked")
        self.assertEqual(result["next_action"]["kind"], "review")
        self.assertIn("blocked", result["next_action"]["message"])
        self.assertNotEqual(result["next_action"]["kind"], "accept")
        output = Path(self.state()["jobs"][0]["host_output"])
        disposition = json.loads((output / "disposition.json").read_text())
        self.assertEqual(disposition["disposition"], "blocked")

    def test_finish_preserves_failed_worker_result_disposition_and_requests_review(self):
        self.prepare_worker_outcome("failed")

        code, result = self.call("finish")

        self.assertEqual((code, result["batch_state"], result["finish_complete"]), (10, "partial", True))
        self.assertEqual(result["jobs"][0]["worker_disposition"], "failed")
        self.assertEqual(result["next_action"]["kind"], "review")
        self.assertIn("failed", result["next_action"]["message"])
        self.assertNotEqual(result["next_action"]["kind"], "accept")
        output = Path(self.state()["jobs"][0]["host_output"])
        disposition = json.loads((output / "disposition.json").read_text())
        self.assertEqual(disposition["disposition"], "failed")

    def test_finish_requires_named_reason_for_explicit_disposition_override(self):
        self.prepare_worker_outcome("blocked")
        reason = "Coordinator reviewed the result and approved completion despite the reported blocker."

        missing_reason_code, missing_reason = self.call(
            "finish", coordinator_disposition_override="job0=completed")

        self.assertEqual((missing_reason_code, missing_reason["error"]), (2, "invalid_input"))
        self.assertIn("require --coordinator-override-reason", missing_reason["message"])

        code, result = self.call("finish", coordinator_disposition_override="job0=completed",
                                 coordinator_override_reason=reason)

        self.assertEqual((code, result["batch_state"], result["finish_complete"]), (10, "partial", True))
        self.assertEqual(result["jobs"][0]["worker_disposition"], "completed")
        self.assertEqual(result["next_action"]["kind"], "review")
        self.assertIn("blocked", result["next_action"]["message"])
        self.assertIn("completed", result["next_action"]["message"])
        output = Path(self.state()["jobs"][0]["host_output"])
        disposition = json.loads((output / "disposition.json").read_text())
        self.assertEqual(disposition["disposition"], "completed")
        self.assertIn(reason, disposition["evidence"])
        self.assertIn("blocked -> disposition completed", result["next_action"]["message"])
        self.assertIn(reason, result["next_action"]["message"])

        repeat_code, repeat_result = self.call("finish")

        self.assertEqual((repeat_code, repeat_result["finish_complete"]), (10, True))
        self.assertEqual(repeat_result["jobs"][0]["worker_disposition"], "completed")
        repeated = json.loads((output / "disposition.json").read_text())
        self.assertEqual(repeated, disposition)

    def test_finish_reconciles_workspace_closed_before_checkpoint_after_crash(self):
        self.call()
        self.configure(crash_after="workspace_close")

        first = subprocess.run(self.argv("finish"), capture_output=True, timeout=10)

        self.assertEqual(first.returncode, 91)
        self.assertEqual(self.state()["jobs"][0]["pending_effect"]["action"], "workspace_close")
        self.configure()

        code, result = self.call("finish")

        self.assertEqual((code, result["finish_complete"]), (0, True))
        self.assertTrue(result["jobs"][0]["workspace_closed"])
        self.assertEqual(sum(event["action"] == "workspace_close" for event in self.events()), 1)
        self.assertEqual(self.call("finish")[1]["finish_complete"], True)
        self.assertEqual(sum(event["action"] == "workspace_close" for event in self.events()), 1)

    def test_finish_identifies_legacy_receipt_only_jobs_as_not_worker_validated(self):
        self.configure(legacy_receipt_fixture=str(PACKAGE / "tests/fixtures/managed-jobs/legacy-receipt.json"))
        code, result = self.call()
        self.assertEqual((code, result["collection_complete"]), (0, True))
        self.assertEqual(result["jobs"][0]["collection"]["outcome"], "complete")

        code, result = self.call("finish")

        self.assertEqual((code, result["finish_complete"]), (0, True))
        self.assertEqual(result["finish"]["jobs"]["job0"]["worker_result_validation"], "not_requested")
        message = result["next_action"]["message"]
        self.assertIn("job0", message)
        self.assertIn("not requested", message)
        self.assertNotIn("validated worker records", message)

    def test_finish_refuses_an_unsettled_pane_and_keeps_it_open(self):
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["worker_result"] = {"task_id": "task-wait", "assignment_revision": 1}
        self.manifest.write_text(json.dumps(manifest))
        self.configure(jobs={"job0": "working"})
        self.call()

        code, result = self.call("finish")

        self.assertEqual((code, result["finish_complete"]), (10, False))
        self.assertIn("not settled", result["finish"]["jobs"]["job0"]["issue"])
        self.assertFalse(any(event["action"] == "workspace_close" for event in self.events()))

    def test_visible_working_marker_is_required_when_no_receipt_landed(self):
        self.configure(jobs={"job0": "unverified_working"})

        code, result = self.call()

        self.assertEqual((code, result["batch_state"]), (10, "partial"))
        self.assertFalse(result["jobs"][0]["settled"])
        self.assertIn("visible Working marker", result["jobs"][0]["issue"])

    def test_non_codex_startup_dialog_is_detected_before_sending_the_task_prompt(self):
        self.write_jobs(1, runtime="pi", provider="llama-local", model="qwen3.8-27b-uncensored")
        self.configure(jobs={"job0": "blocked"})

        code, result = self.call()

        self.assertEqual((code, result["batch_state"]), (10, "partial"))
        self.assertIn("dialog", result["jobs"][0]["issue"])
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 0)

    def test_pi_provider_override_without_model_is_rejected_in_manifest_validation(self):
        self.write_jobs(1, runtime="pi", provider="llama-local")

        code, result = self.call(real=True, preview=True)

        self.assertEqual((code, result["error"]), (2, "invalid_input"))
        self.assertIn("provider override requires a model", result["message"])
        self.assertFalse(self.run.exists())

    def test_runtime_without_effort_support_is_rejected_before_launch(self):
        policy = json.loads(self.policy.read_text())
        policy["runtime_kinds"]["gemini"] = "gemini"
        self.policy.write_text(json.dumps(policy))
        self.write_jobs(1, runtime="gemini")
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["override"]["effort"] = "high"
        self.manifest.write_text(json.dumps(manifest))

        code, result = self.call(real=True, preview=True)

        self.assertEqual((code, result["error"]), (2, "invalid_input"))
        self.assertIn("runtime gemini does not support an effort override", result["message"])
        self.assertFalse(self.run.exists())

    def test_effort_values_outside_each_runtime_capability_are_rejected(self):
        policy = json.loads(self.policy.read_text())
        policy["runtime_kinds"]["claude-code"] = "claude"
        self.policy.write_text(json.dumps(policy))
        cases = (("codex", "auto"), ("pi", "auto"),
                 ("claude-code", "off"), ("omp", "none"))
        for runtime, effort in cases:
            with self.subTest(runtime=runtime, effort=effort):
                self.write_jobs(1, runtime=runtime)
                manifest = json.loads(self.manifest.read_text())
                manifest["jobs"][0]["override"]["effort"] = effort
                self.manifest.write_text(json.dumps(manifest))

                code, result = self.call(real=True, preview=True)

                self.assertEqual((code, result["error"]), (2, "invalid_input"))
                self.assertIn(f"not supported by runtime {runtime}", result["message"])
                self.assertFalse(self.run.exists())

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

    def test_mixed_batch_observes_each_launch_before_starting_the_next(self):
        self.write_jobs(5, ["ordinary", "deep_research", "deep_research", "ordinary", "ordinary"])
        code, result = self.call(wait_seconds=1)
        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        events = self.events()
        first_observe = next(i for i, event in enumerate(events) if event["action"] == "observe")
        launches_before_first_observe = [event for event in events[:first_observe]
                                         if event["action"] in ("start", "jail")]
        self.assertEqual(len(launches_before_first_observe), 1)
        self.assertEqual(sum(event["action"] in ("start", "jail") for event in events), 5)
        self.assertEqual([job["route"]["mode"] for job in result["jobs"]], ["agent", "jail", "jail", "agent", "agent"])
        self.assertTrue(all(job["settled"] for job in result["jobs"]))
        self.assertEqual(result["acceptance"], "pending")
        for event in events:
            if event["action"] in ("start", "prompt", "jail", "observe"):
                self.assertTrue(event["pane_id"].startswith("moved:"))

    def test_eight_codex_jobs_share_four_host_slots_with_direct_launches(self):
        policy = json.loads(self.policy.read_text())
        policy["provider_admission"]["provider_caps"]["codex"] = 4
        self.policy.write_text(json.dumps(policy))
        self.write_jobs(8, runtime="codex", concurrency=8, provider="codex", model="gpt-6-luna")
        self.configure(jobs={f"job{index}": "working" for index in range(4)})

        _, result = self.call(wait_seconds=0.5)
        direct_status = subprocess.run(
            [sys.executable, "-B", str(PACKAGE / "scripts/herdr-admission.py"),
             "status", "--provider", "codex"],
            capture_output=True, text=True, timeout=5,
        )
        direct_acquire = subprocess.run(
            [sys.executable, "-B", str(PACKAGE / "scripts/herdr-admission.py"),
             "acquire", "--provider", "codex", "--model", "gpt-6-luna", "--lease-id", "direct-extra",
             "--policy", str(self.policy)],
            capture_output=True, text=True, timeout=5,
        )

        self.assertEqual(direct_status.returncode, 0, direct_status.stderr)
        self.assertEqual(direct_acquire.returncode, 0, direct_acquire.stderr)
        self.assertEqual(sum(event["action"] == "split" for event in self.events()), 4)
        self.assertEqual(sum(job["phase"] == "pending" for job in result["jobs"]), 4)
        self.assertEqual(result["provider_active"]["codex"], 4)
        self.assertEqual(json.loads(direct_status.stdout)["active_count"], 4)
        self.assertFalse(json.loads(direct_acquire.stdout)["admitted"])

    def test_rate_limited_assignment_retries_after_backoff_without_losing_lease_or_artifacts(self):
        self.write_jobs(2, runtime="pi", provider="llama-local", model="qwen3.8-27b-uncensored")
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["cwd"] = str(self.root)
        manifest["jobs"][0]["writes_repository"] = True
        lease = {"schema_version": 1, "path": str(self.root), "lease_id": "treehouse-lease-1",
                 "lease_holder": "test-request", "repo_root": str(self.root),
                 "git_common_dir": str(self.root / ".git"), "base_commit": "a" * 40}
        manifest["jobs"][0]["repository_worktree"] = lease
        self.manifest.write_text(json.dumps(manifest))
        self.configure(jobs={"job0": "rate_limited_once"})

        code, result = self.call(wait_seconds=1.2)
        state = self.state()
        job = state["jobs"][0]
        retries = [item for item in job["history"] if item["action"] == "rate_limit_retry"]

        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        self.assertEqual(len(retries), 1)
        if not retries:
            return
        retry = retries[0]
        self.assertNotEqual(retry["attempt_id"], job["attempt_id"])
        self.assertTrue(retry["pane_id"].startswith("moved:"))
        self.assertTrue(Path(retry["host_output"], "rate-limit-evidence.md").is_file())
        self.assertEqual(job["spec"]["repository_worktree"], lease)
        self.assertTrue(job["settled"])
        self.assertTrue(state["jobs"][1]["settled"])
        second_job_splits = [event for event in self.events()
                             if event["job_id"] == "job1" and event["action"] == "split"]
        self.assertTrue(second_job_splits)
        self.assertGreaterEqual(second_job_splits[0]["at"], retry["retry_at"])

    def test_explicit_start_rate_limit_retries_even_without_a_registered_worker(self):
        self.write_jobs(1, runtime="pi", provider="llama-local", model="qwen3.8-27b-uncensored")
        self.configure(jobs={"job0": "rate_limited_start_once"})

        code, result = self.call(wait_seconds=1.2)
        job = self.state()["jobs"][0]
        retries = [item for item in job["history"] if item["action"] == "rate_limit_retry"]

        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        self.assertEqual(len(retries), 1)
        if len(retries) != 1:
            return
        retry = retries[0]
        self.assertTrue(retry["pane_id"].startswith("moved:"))
        self.assertTrue(Path(retry["host_output"], "rate-limit-evidence.md").is_file())
        self.assertNotEqual(retry["attempt_id"], job["attempt_id"])

    def test_managed_rate_limit_uses_bounded_default_when_metadata_is_absent(self):
        self.write_jobs(1, runtime="pi", provider="llama-local", model="qwen3.8-27b-uncensored")
        self.configure(jobs={"job0": "rate_limited_without_metadata_once"})

        before = time.time()
        code, result = self.call(wait_seconds=0.3)
        state = self.state()
        job = state["jobs"][0]
        limited = next(item for item in job["history"] if item["action"] == "provider_rate_limit")
        retry = next(item for item in job["history"] if item["action"] == "rate_limit_retry")

        self.assertEqual((code, result["batch_state"]), (0, "active"))
        self.assertEqual(limited["source"], "default")
        self.assertGreaterEqual(retry["retry_at"] - before, 59)
        self.assertLessEqual(retry["retry_at"] - before, 61)
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 1)

    def test_repeat_and_resume_do_not_relaunch(self):
        self.call()
        self.call()
        self.call("resume")
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 0)
        self.assertEqual(sum(event["action"] == "split" for event in self.events()), 1)

    def test_codex_receives_the_managed_prompt_during_fresh_start(self):
        self.call()
        events = self.events()
        self.assertEqual(sum(event["action"] == "start" for event in events), 1)
        self.assertEqual(sum(event["action"] == "prompt" for event in events), 0)
        self.assertTrue((self.root / "prompt-job0.txt").is_file())
        self.assertLess(next(i for i, event in enumerate(events) if event["action"] == "prompt_verified"),
                        next(i for i, event in enumerate(events) if event["action"] == "observe"))

    def test_pi_retry_override_is_private_to_the_run_and_recorded_in_state(self):
        global_agent = self.root / "pi-global"
        global_agent.mkdir()
        global_settings = global_agent / "settings.json"
        original_settings = ('{\n  // preserve unrelated pi configuration\n'
                             '  "defaultModel": "fixture-model",\n'
                             '  "retry": {"enabled": true, "maxRetries": 2, "keep": "existing",},\n}\n')
        global_settings.write_text(original_settings)
        (global_agent / "auth.json").write_text("{}")

        policy = json.loads(self.policy.read_text())
        policy["routes"]["ordinary"]["runtime"] = "pi"
        policy["runtime_kinds"]["pi"] = "pi"
        self.policy.write_text(json.dumps(policy))
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0].pop("override")
        manifest["retry_override"] = {"pi": {"max_retries": 10, "max_agent_delay_ms": 120000}}
        self.manifest.write_text(json.dumps(manifest))
        self.configure(jobs={"job0": "rate_limited_start_once"})

        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(global_agent)}):
            code, result = self.call(wait_seconds=1.2)

        self.assertEqual((code, result["batch_state"]), (0, "collected"))
        status_code, status = self.call("status")
        self.assertEqual((status_code, status["batch_state"]), (0, "collected"))
        state = self.state()
        self.assertEqual(state["retry_override"], manifest["retry_override"])
        job = state["jobs"][0]
        retry = next(item for item in job["history"] if item["action"] == "rate_limit_retry")
        self.assertNotEqual(retry["attempt_id"], job["attempt_id"])
        self.assertEqual(job["retry_override"], manifest["retry_override"]["pi"])
        profile = Path(job["retry_profile"]["agent_dir"])
        self.assertIn(job["attempt_id"], str(profile))
        self.assertNotEqual(str(profile), str(self.run / "runtime" / "pi" / "job0" / retry["attempt_id"] / "agent"))
        run_settings = json.loads((profile / "settings.json").read_text())
        self.assertEqual(run_settings, {
            "defaultModel": "fixture-model",
            "retry": {"enabled": True, "maxRetries": 10, "maxAgentDelayMs": 120000, "keep": "existing"},
        })
        self.assertEqual(global_settings.read_text(), original_settings)
        self.assertEqual((profile / "auth.json").resolve(), (global_agent / "auth.json").resolve())
        self.assertTrue(Path(job["retry_profile"]["session_dir"]).is_dir())
        self.assertEqual(digest((profile / "settings.json").read_bytes()),
                         job["retry_profile"]["settings_sha256"])
        actions = [event["action"] for event in self.events()]
        self.assertEqual(actions.count("retry_setup"), 2)
        self.assertEqual(actions.count("start"), 2)
        for attempt in {retry["attempt_id"], job["attempt_id"]}:
            attempt_actions = [event["action"] for event in self.events() if event["attempt_id"] == attempt]
            self.assertLess(attempt_actions.index("retry_setup"), attempt_actions.index("start"))

    def test_unmatched_retry_override_is_rejected_before_any_worker_launch(self):
        manifest = json.loads(self.manifest.read_text())
        manifest["retry_override"] = {"pi": {"max_retries": 10}}
        self.manifest.write_text(json.dumps(manifest))

        code, result = self.call()

        self.assertEqual((code, result["error"]), (2, "invalid_input"))
        self.assertIn("no pi workers", result["message"])
        self.assertFalse((self.run / "state.json").exists())
        self.assertFalse((self.root / "events.json").exists())

    def test_codex_dialog_is_a_launch_failure_and_is_never_retried(self):
        self.configure(jobs={"job0": "blocked"})
        code, result = self.call()
        self.assertEqual(code, 10)
        self.assertEqual(result["jobs"][0]["observed"]["state"], "blocked")
        self.assertIn("trust or resume dialog", result["jobs"][0]["issue"])
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 0)

        self.call("resume")
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 0)

    def test_task_content_change_conflicts(self):
        self.call()
        self.task.write_text("Changed request content")
        code, result = self.call()
        self.assertEqual((code, result["error"]), (3, "conflict"))
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)

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
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 0)
        self.assertTrue(any(event.get("reconciled_by") == "matching receipt" for event in self.state()["jobs"][0]["history"]))

    def test_working_does_not_prove_ambiguous_prompt_delivery(self):
        self.configure(jobs={"job0": "ambiguous"})
        self.assertEqual(self.call()[0], 6)
        self.assertEqual(sum(event["action"] == "observe" for event in self.events()), 1)
        self.assertEqual(self.call("resume")[0], 6)
        self.assertEqual(sum(event["action"] == "start" for event in self.events()), 1)
        self.assertEqual(sum(event["action"] == "prompt" for event in self.events()), 0)

    def test_agent_working_without_prompt_landing_is_not_verified_activity(self):
        self.write_jobs(1, runtime="pi")
        self.configure(jobs={"job0": "unverified_working"})

        _, result = self.call(wait_seconds=0.3)

        self.assertEqual(result["jobs"][0]["observed"]["state"], "working")
        self.assertFalse(self.state()["jobs"][0]["activity_seen"])
        self.assertEqual(sum(event["action"] == "observe" for event in self.events()), 2)

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
        self.write_jobs(5, ["deep_research"] * 5)
        self.configure(jobs={f"job{index}": "exited_jail" for index in range(5)})
        code, result = self.call(wait_seconds=1)
        self.assertEqual((code, result["batch_state"], result["active_jobs"]), (0, "collected", 0))
        self.assertEqual(result["jobs"][4]["observed"]["state"], "exited")
        self.assertTrue(result["jobs"][4]["settled"])
        self.assertNotIn("prompt_verified", result["jobs"][4]["observed"])

    def test_long_running_jail_waits_for_exit_evidence_until_budget(self):
        self.write_jobs(1, ["deep_research"])
        self.configure(jobs={"job0": "working"})
        started = time.monotonic()

        _, result = self.call(wait_seconds=0.3)

        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        self.assertEqual(result["jobs"][0]["observed"]["state"], "working")
        self.assertFalse(result["jobs"][0]["settled"])
        self.assertTrue(self.state()["jobs"][0]["activity_seen"])
        self.assertEqual(self.state()["jobs"][0]["spec"]["route"]["mode"], "jail")

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
        non_boolean_intent = copy.deepcopy(manifest)
        non_boolean_intent["jobs"][0]["writes_repository"] = 1
        cases.append(non_boolean_intent)
        malformed_kind = copy.deepcopy(manifest)
        malformed_kind["jobs"][0]["task_kind"] = []
        cases.append(malformed_kind)
        for case in cases:
            self.manifest.write_text(json.dumps(case))
            self.assertEqual(self.call(preview=True)[0], 2)
        self.manifest.write_text('{"schema_version":1,"schema_version":1}')
        self.assertEqual(self.call(preview=True)[0], 2)

    def test_writer_intent_is_required_and_missing_worktree_stops_before_launch(self):
        manifest = json.loads(self.manifest.read_text())
        del manifest["jobs"][0]["writes_repository"]
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call()
        self.assertEqual((code, result["error"]), (2, "invalid_input"))
        self.assertFalse((self.root / "events.json").exists())
        self.assertFalse((self.run / "state.json").exists())

        manifest["jobs"][0]["writes_repository"] = True
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call()
        self.assertEqual((code, result["error"]), (2, "invalid_input"))
        self.assertIn("writes_repository=true requires repository_worktree", result["message"])
        self.assertFalse((self.root / "events.json").exists())
        self.assertFalse((self.run / "state.json").exists())

    def test_ordinary_default_requires_a_runtime_decision(self):
        manifest = json.loads(self.manifest.read_text())
        del manifest["jobs"][0]["override"]
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call(preview=True)
        self.assertEqual((code, result["error"]), (5, "decision_needed"))

    def test_override_instruction_leads_the_worker_prompt(self):
        self.call()
        prompt = (self.root / "prompt-job0.txt").read_text()
        self.assertTrue(prompt.startswith("Current user instruction (highest precedence):\nUse Codex for this fixture."))

    def test_override_cannot_move_a_jail_job_out_of_its_route(self):
        self.write_jobs(1, ["deep_research"])
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0]["override"] = {"runtime": "claude-code", "instruction": "Use Claude Code."}
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call(preview=True)
        self.assertEqual((code, result["error"]), (5, "decision_needed"))
        manifest["jobs"][0]["override"]["runtime"] = "codex"
        self.manifest.write_text(json.dumps(manifest))
        code, result = self.call(preview=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["jobs"][0]["route"], {"mode": "jail", "runtime": "codex"})

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
        for action in ("split", "move", "start", "jail"):
            with self.subTest(action=action):
                self.run = self.root / f"crash-{action}"
                (self.root / "crashed").unlink(missing_ok=True)
                (self.root / "events.json").unlink(missing_ok=True)
                self.write_jobs(1, ["deep_research"] if action == "jail" else None)
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
