"""Same-pane quota recovery through the public CLI, with offline pane fixtures."""

from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.cli import main
from herdr_jobs.engine import Engine
from herdr_jobs.records import digest, encoded, load_json, prepare, run_lock
from herdr_jobs.transport import Deadline, NativeTransport
from support import isolate_admission_state


class PaneFixture:
    """Herdr boundary that rejects an Enter sent before Codex's input settles."""

    def __init__(self):
        self.output = "■ You've hit your usage limit.\nReset-at: 2026-10-24T20:10:00Z\n›"
        self.state = "idle"
        self.pane = "owned:1"
        self.name = None
        self.kind = "codex"
        self.elapsed = 0
        self.typed_at = None
        self.events = []
        self.before_text = None
        self.after_text = None
        self.failure = None
        self.start_working = True
        self.agent_reads = 0
        self.on_agent_get = None

    def sleep(self, seconds):
        self.elapsed += seconds

    def command(self, argv, deadline, cwd=None, env=None):
        if argv[1:3] == ["workspace", "list"]:
            return 0, b'{"ok":true,"result":{"type":"workspace_list","workspaces":[]}}', b""
        if argv[1:3] == ["pane", "read"]:
            return 0, self.output.encode(), b""
        if argv[1:3] == ["agent", "get"]:
            self.agent_reads += 1
            if self.on_agent_get:
                self.on_agent_get(self.agent_reads)
            return 0, json.dumps({"pane": self.pane, "kind": self.kind,
                                  "name": self.name, "state": self.state}).encode(), b""
        if argv[1:3] == ["pane", "send-text"]:
            if self.before_text:
                self.before_text()
            self.events.append(("text", argv[3], argv[4]))
            self.typed_at = self.elapsed
            if self.after_text:
                self.after_text()
            if self.failure == "text":
                return 1, b"", b"fixture send-text failure"
            return 0, b"", b""
        if argv[1:3] == ["pane", "send-keys"]:
            self.events.append(("key", argv[3], argv[4]))
            if self.failure == "key":
                return 1, b"", b"fixture Enter failure"
            if self.elapsed - self.typed_at >= 2 and self.start_working:
                self.output = "• Working (esc to interrupt)"
                self.state = "working"
            return 0, b"", b""
        raise AssertionError(f"Unexpected external effect: {argv}")


class ContinuationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        isolate_admission_state(self, self.root / "admission")
        task = self.root / "task.md"
        task.write_text("Finish the assigned fixture report.")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "request_id": "quota-fixture", "jobs": [{
            "job_id": "j1", "name": "Fixture", "task_kind": "ordinary", "task_file": str(task),
            "cwd": str(self.root), "output_expectation": "A report.", "writes_repository": False,
            "override": {"runtime": "codex", "instruction": "Use Codex for this fixture."}}]}))
        request = prepare(manifest, PACKAGE / "launch-policy.json")
        self.capabilities = {"session_id": "fixture-session", "models": {}, "binding": {"fixture": True}}
        self.run = self.root / "run"
        with run_lock(self.run):
            engine = Engine(self.run, None, Deadline(30))
            engine.initialize(request, self.capabilities, None)
            job = engine.state["jobs"][0]
            job.update(phase="submitted", pane_id="owned:1", workspace_id="workspace:1")
            engine.acquire_admission(job)
            engine.checkpoint()
        self.original = load_json(self.run / "state.json")["jobs"][0]
        self.pane = PaneFixture()
        self.pane.name = job["agent_name"]

    def transport(self, binding, deadline):
        adapter = object.__new__(NativeTransport)
        adapter.herdr = "herdr"
        adapter.deadline = deadline
        adapter.binding = {"paths": {"agent_pane_id": ["pane"], "agent_kind": ["kind"],
                                     "agent_name": ["name"], "agent_state": ["state"]}}
        adapter.preflight = lambda *args, **kwargs: self.capabilities
        return adapter

    def call(self, operation="continue", wait="5", extra=()):
        argv = [operation, "--run-dir", str(self.run), "--wait-seconds", wait]
        if operation == "continue":
            argv += ["--job", "j1"]
        argv += list(extra)
        output = io.StringIO()
        with (redirect_stdout(output), patch("herdr_jobs.transport.command", self.pane.command),
              patch("time.sleep", self.pane.sleep),
              patch("time.monotonic", side_effect=lambda: self.pane.elapsed)):
            code = main(argv, transport_factory=self.transport)
        return code, json.loads(output.getvalue())

    def test_supervised_status_preserves_quota_attempt_for_journaled_continuation(self):
        code, result = self.call("status", extra=["--defer-quota-recovery"])
        self.assertEqual(code, 10, result)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["attempt_id"], self.original["attempt_id"])
        self.assertEqual(job["rate_limit_recoveries"], 0)
        self.assertFalse(job["settled"])
        self.assertEqual(self.pane.events, [])
        self.assertEqual(self.call()[0], 0)

    def test_supervised_resume_does_not_launch_a_prepared_fresh_quota_retry(self):
        self.assertEqual(self.call("status")[0], 0)
        queued = load_json(self.run / "state.json")["jobs"][0]
        self.assertNotEqual(queued["attempt_id"], self.original["attempt_id"])
        code, result = self.call("resume", extra=["--defer-quota-recovery"])
        self.assertEqual(code, 10, result)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["phase"], "pending")
        self.assertIsNone(job["pane_id"])
        self.assertIsNone(job["pending_effect"])
        self.assertEqual(self.pane.events, [])

    def test_supervised_continuation_requires_current_effect_reconciliation(self):
        record = {
            "schema_version": 1, "request_id": "quota-fixture", "job_id": "j1",
            "attempt_id": self.original["attempt_id"], "pane_id": "owned:1",
            "assignment_sha256": digest(encoded(self.original["spec"])),
            "quota_sha256": digest(self.pane.output.encode()),
            "mutation_state": "mutation-checkpointed", "observed_at": "2026-10-27T00:00:00Z",
            "artifact_evidence": "Original output inspected; no receipt; partial report retained.",
            "effect_evidence": "Fixture checkpoint confirms all completed effects; remaining steps safe.",
        }
        path = self.run / "reconciliation.json"
        extra = ["--reconciliation", str(path), "--defer-quota-recovery"]
        code, result = self.call(extra=extra)
        self.assertEqual(code, 5, result)
        for field, value in (("attempt_id", "stale"), ("pane_id", "other:1"),
                             ("assignment_sha256", "stale"), ("quota_sha256", "stale"), ("mutation_state", "mutation-state-unknown"),
                             ("artifact_evidence", ""), ("effect_evidence", "")):
            with self.subTest(field=field):
                path.write_text(json.dumps({**record, field: value}))
                self.assertEqual(self.call(extra=extra)[0], 5)
                self.assertEqual(self.pane.events, [])
        path.write_text(json.dumps(record))
        code, result = self.call(extra=extra)
        self.assertEqual(code, 0, result)
        job = load_json(self.run / "state.json")["jobs"][0]
        intent = next(item for item in job["history"] if item["action"] == "continue_intent")
        self.assertEqual(intent["reconciliation"], record)
        self.assertEqual(job["rate_limit_recoveries"], 1)
        self.assertEqual(result["jobs"][0]["continuation_count"], 1)

    def test_supervised_followup_keeps_the_recovery_fence(self):
        code, result = self.call("status", extra=["--defer-quota-recovery"])
        self.assertEqual(code, 10, result)
        self.assertIn("--defer-quota-recovery", result["next_action"]["argv"])

    def test_error_followups_preserve_reconciliation_before_replay(self):
        code, result = self.call(extra=[
            "--defer-quota-recovery", "--reconciliation", str(self.run / "missing.json")])
        self.assertEqual(code, 5, result)
        self.assertEqual(result["error"], "decision_needed")
        for expected_operation in ("status", "resume"):
            argv = result["next_action"]["argv"]
            self.assertEqual(argv[2], expected_operation)
            output = io.StringIO()
            with (redirect_stdout(output), patch("herdr_jobs.transport.command", self.pane.command),
                  patch("time.sleep", self.pane.sleep),
                  patch("time.monotonic", side_effect=lambda: self.pane.elapsed)):
                code = main(argv[2:], transport_factory=self.transport)
            result = json.loads(output.getvalue())
            self.assertIn(code, (0, 10), result)
            job = load_json(self.run / "state.json")["jobs"][0]
            self.assertEqual(job["attempt_id"], self.original["attempt_id"])
            self.assertEqual(job["rate_limit_recoveries"], 0)
            self.assertIsNone(job["pending_effect"])
            self.assertFalse(any(item["action"] == "rate_limit_retry" for item in job["history"]))
            self.assertEqual(self.pane.events, [])
        self.assertIn("--defer-quota-recovery", result["next_action"]["argv"])

    def test_supervised_quota_observation_preserves_unrelated_ambiguous_effect(self):
        self.edit_job(pending_effect={"action": "move", "started_at": "fixture"})
        code, result = self.call("status", extra=["--defer-quota-recovery"])
        self.assertEqual(code, 6, result)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["pending_effect"]["action"], "move")
        self.assertEqual(self.call()[0], 5)
        self.assertEqual(self.pane.events, [])

    def test_expired_quota_continues_owned_attempt_and_journals_working(self):
        code, result = self.call()
        self.assertEqual(code, 0, result)
        self.assertEqual([event[0] for event in self.pane.events], ["text", "key"])
        self.assertEqual(self.pane.events[1], ("key", "owned:1", "enter"))
        self.assertIn("Continue", self.pane.events[0][2])
        self.assertGreaterEqual(self.pane.elapsed, 2)
        job = load_json(self.run / "state.json")["jobs"][0]
        for key in ("attempt_id", "pane_id", "spec", "host_output", "worker_output"):
            self.assertEqual(job[key], self.original[key])
        self.assertIsNone(job["pending_effect"])
        self.assertTrue(job["observed"]["prompt_verified"])
        self.assertEqual(job["rate_limit_recoveries"], 1)
        self.assertTrue(any(item["action"] == "continue" for item in job["history"]))
        intent = next(item for item in job["history"] if item["action"] == "continue_intent")
        self.assertEqual(intent["evidence"]["reset_at"],
                         datetime.fromisoformat("2026-10-24T20:10:00+00:00").timestamp())
        telemetry = load_json(Path(job["host_output"]) / "telemetry.json")
        self.assertEqual(telemetry["continuation_count"], 1)
        self.assertEqual(telemetry["coordinator_grade"], "pending")

    def test_future_wrapped_reset_is_durable_and_resumes_after_that_reset(self):
        self.pane.output = (PACKAGE / "tests/fixtures/herdr/codex-usage-limit.txt").read_text().replace(
            "at 8:10 PM.", "at 8:10\n  PM.")
        before = datetime.fromisoformat("2026-10-24T20:00:00+00:00").timestamp()
        after = datetime.fromisoformat("2026-10-24T20:11:00+00:00").timestamp()
        with patch.dict("os.environ", {"TZ": "UTC"}), patch("time.time", return_value=before):
            code, result = self.call()
        self.assertEqual(code, 5, result)
        self.assertEqual(self.pane.events, [])
        job = load_json(self.run / "state.json")["jobs"][0]
        evidence = [item for item in job["history"] if item["action"] == "continue_quota_observed"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["evidence"]["reset_at"], before + 600)
        with patch.dict("os.environ", {"TZ": "UTC"}), patch("time.time", return_value=after):
            code, result = self.call()
        self.assertEqual(code, 0, result)
        self.assertEqual([event[0] for event in self.pane.events], ["text", "key"])

    def test_unknown_submission_survives_status_and_never_replays(self):
        self.pane.start_working = False

        def check_durable_intent():
            job = load_json(self.run / "state.json")["jobs"][0]
            self.assertEqual(job["pending_effect"]["action"], "continue")
            self.assertEqual(job["rate_limit_recoveries"], 1)
            self.assertEqual(load_json(Path(job["host_output"]) / "telemetry.json")["continuation_count"], 1)

        self.pane.before_text = check_durable_intent
        code, result = self.call()
        self.assertEqual(code, 6, result)
        events = list(self.pane.events)
        self.assertEqual(len(events), 2)
        code, result = self.call("status")
        self.assertEqual(code, 6, result)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["pending_effect"]["action"], "continue")
        self.assertEqual(self.call()[0], 5)
        self.assertEqual(self.pane.events, events)

    def test_resume_reconciles_late_working_without_resending(self):
        self.pane.start_working = False
        self.assertEqual(self.call()[0], 6)
        events = list(self.pane.events)
        self.pane.state = "working"
        self.pane.output += "\n• Working (esc to interrupt)"
        code, result = self.call("resume")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.pane.events, events)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertIsNone(job["pending_effect"])
        self.assertIsNone(job["issue"])
        self.assertTrue(job["activity_seen"])
        self.assertEqual(job["rate_limit_recoveries"], 1)
        self.assertEqual(job["attempt_id"], self.original["attempt_id"])
        self.assertEqual(self.call()[0], 5)

    def test_receipt_settles_unverified_continuation_without_working_observation(self):
        self.pane.start_working = False
        self.assertEqual(self.call()[0], 6)
        events = list(self.pane.events)
        output = Path(self.original["host_output"])
        (output / "result.md").write_text("Completed.")
        (output / "receipt.json").write_text(json.dumps({
            "schema_version": 1, "request_id": "quota-fixture", "job_id": "j1",
            "attempt_id": self.original["attempt_id"], "outcome": "complete",
            "artifacts": ["result.md"], "unresolved": []}))
        self.pane.output = "Completed."
        for operation in ("status", "resume", "finish"):
            with self.subTest(operation=operation):
                code, result = self.call(operation)
                self.assertEqual(code, 0, result)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertTrue(job["settled"])
        self.assertIsNone(job["pending_effect"])
        self.assertIsNone(job["issue"])
        self.assertEqual(job["collection"]["outcome"], "complete")
        self.assertEqual(job["attempt_id"], self.original["attempt_id"])
        self.assertEqual(job["rate_limit_recoveries"], 1)
        telemetry = load_json(output / "telemetry.json")
        self.assertEqual(telemetry["continuation_count"], 1)
        self.assertEqual(self.pane.events, events)
        self.assertTrue(any(entry["action"] == "continue"
                            and entry.get("reconciled_by") == "matching receipt"
                            for entry in job["history"]))

    def test_wrong_attempt_receipt_leaves_continuation_unresolved(self):
        self.pane.start_working = False
        self.assertEqual(self.call()[0], 6)
        events = list(self.pane.events)
        output = Path(self.original["host_output"])
        (output / "result.md").write_text("Completed.")
        (output / "receipt.json").write_text(json.dumps({
            "schema_version": 1, "request_id": "quota-fixture", "job_id": "j1",
            "attempt_id": "another-attempt", "outcome": "complete",
            "artifacts": ["result.md"], "unresolved": []}))
        self.pane.output = "Completed."
        self.assertEqual(self.call("status")[0], 6)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["pending_effect"]["action"], "continue")
        self.assertFalse(job["settled"])
        self.assertIsNotNone(job["collection_error"])
        self.assertEqual(self.pane.events, events)

    def test_receipt_reconciles_continuation_but_active_worker_stays_unsettled(self):
        self.pane.start_working = False
        self.assertEqual(self.call()[0], 6)
        events = list(self.pane.events)
        output = Path(self.original["host_output"])
        (output / "result.md").write_text("Completed.")
        (output / "receipt.json").write_text(json.dumps({
            "schema_version": 1, "request_id": "quota-fixture", "job_id": "j1",
            "attempt_id": self.original["attempt_id"], "outcome": "complete",
            "artifacts": ["result.md"], "unresolved": []}))
        self.pane.state = "working"
        self.pane.output = "Completed."
        self.call("status")
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertIsNone(job["pending_effect"])
        self.assertFalse(job["settled"])
        self.assertEqual(self.call("finish")[0], 10)
        self.assertEqual(self.pane.events, events)

    def test_owned_quota_reconciles_an_ambiguous_initial_codex_start(self):
        with run_lock(self.run):
            engine = Engine(self.run, None, Deadline(5))
            engine.load()
            job = engine.state["jobs"][0]
            job["phase"] = "moved"
            job["pending_effect"] = {"action": "start", "started_at": "2026-10-24T20:00:00+00:00"}
            job["issue_code"] = "effect_delivery_unproven"
            engine.checkpoint()
        code, result = self.call()
        self.assertEqual(code, 0, result)
        self.assertIsNone(result["jobs"][0]["issue_code"])
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["phase"], "submitted")
        self.assertIsNone(job["pending_effect"])
        self.assertEqual(job["attempt_id"], self.original["attempt_id"])
        self.assertTrue(any(entry["action"] == "start" and "reconciled_by" in entry
                            for entry in job["history"]))

    def test_newly_written_receipt_blocks_continuation(self):
        output = Path(self.original["host_output"])
        (output / "receipt.json").write_text('{"outcome":"complete"}')
        code, result = self.call()
        self.assertEqual(code, 5, result)
        self.assertEqual(self.pane.events, [])

    def edit_job(self, **updates):
        with run_lock(self.run):
            engine = Engine(self.run, None, Deadline(5))
            engine.load()
            engine.state["jobs"][0].update(updates)
            engine.checkpoint()

    def test_unproven_quota_or_reset_never_sends_input(self):
        cases = (
            "The report discusses quota exceeded and reset at midnight.",
            "■ You've hit your usage limit. Try again later.",
            "■ You've hit your usage limit. Reset-at: someday",
            "■ You've hit your usage limit.\nRetry-after: 0",
            "HTTP/2 429\nReset-at: 2026-10-24T20:10:00Z",
        )
        for output in cases:
            with self.subTest(output=output):
                self.pane.output = output
                code, result = self.call()
                self.assertEqual(code, 5, result)
                self.assertEqual(self.pane.events, [])

    def test_stale_pane_kind_or_name_never_receives_input(self):
        for field, stale in (("pane", "somebody-else:1"), ("kind", "omp"), ("name", "other-attempt")):
            with self.subTest(field=field):
                original = getattr(self.pane, field)
                setattr(self.pane, field, stale)
                code, result = self.call()
                self.assertEqual(code, 4, result)
                self.assertEqual(self.pane.events, [])
                setattr(self.pane, field, original)

    def test_active_or_blocked_worker_never_receives_input(self):
        for state in ("working", "blocked", "unknown"):
            with self.subTest(state=state):
                self.pane.state = state
                code, result = self.call()
                self.assertEqual(code, 5, result)
                self.assertEqual(self.pane.events, [])

    def test_trust_dialog_never_receives_input(self):
        self.pane.output += "\nDo you trust this directory?"
        self.assertEqual(self.call()[0], 5)
        self.assertEqual(self.pane.events, [])

    def test_single_recovery_budget_is_shared_with_automatic_retry(self):
        self.edit_job(rate_limit_recoveries=1)
        self.assertEqual(self.call()[0], 5)
        self.assertEqual(self.pane.events, [])

    def test_verified_continuation_cannot_be_submitted_twice(self):
        self.assertEqual(self.call()[0], 0)
        events = list(self.pane.events)
        self.pane.state = "idle"
        self.pane.output = "■ You've hit your usage limit.\nReset-at: 2026-10-24T20:10:00Z"
        self.assertEqual(self.call()[0], 5)
        self.assertEqual(self.pane.events, events)

    def test_other_pending_effect_is_not_discarded(self):
        self.edit_job(pending_effect={"action": "workspace_close", "started_at": "2026-10-24T20:00:00Z"})
        self.assertEqual(self.call()[0], 5)
        self.assertEqual(self.pane.events, [])
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["pending_effect"]["action"], "workspace_close")

    def test_agent_working_without_visible_working_does_not_confirm_delivery(self):
        def agent_claims_working():
            self.pane.state = "working"

        self.pane.before_text = agent_claims_working
        self.pane.start_working = False
        self.assertEqual(self.call()[0], 6)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["pending_effect"]["action"], "continue")

    def test_interrupt_after_text_preserves_intent_and_prevents_another_enter(self):
        def interrupt():
            raise KeyboardInterrupt()

        self.pane.after_text = interrupt
        self.assertEqual(self.call()[0], 130)
        self.pane.after_text = None
        self.assertEqual(self.call()[0], 5)
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertEqual(job["pending_effect"]["action"], "continue")
        self.assertEqual(job["rate_limit_recoveries"], 1)
        self.assertEqual([event[0] for event in self.pane.events], ["text"])

    def test_short_call_budget_never_types_partial_input(self):
        for wait in ("0", "1", "2", "nan"):
            with self.subTest(wait=wait):
                self.assertEqual(self.call(wait=wait)[0], 2)
                self.assertEqual(self.pane.events, [])

    def test_durable_telemetry_fences_recovery_if_state_checkpoint_was_interrupted(self):
        state_before = (self.run / "state.json").read_bytes()
        self.assertEqual(self.call()[0], 0)
        # Telemetry is committed before state.json; recreate a crash between them.
        (self.run / "state.json").write_bytes(state_before)
        engine = Engine(self.run, None, Deadline(5))
        engine.load()
        self.assertEqual(engine.state["jobs"][0]["rate_limit_recoveries"], 1)

    def test_quota_evidence_redacts_credentials_before_checkpointing(self):
        credential = "-".join(("fixture", "credential", "value"))
        private_material = "-".join(("fixture", "private", "material"))
        key_label = " ".join(("PRIVATE", "KEY"))
        self.pane.output += (
            f"\napi_key={credential}\n"
            f"-----BEGIN {key_label}-----\n{private_material}\n"
            f"-----END {key_label}-----"
        )
        code, result = self.call()
        self.assertEqual(code, 0, result)
        state_text = (self.run / "state.json").read_text()
        self.assertNotIn(credential, state_text)
        self.assertNotIn(private_material, state_text)

    def test_identity_change_after_evidence_is_rechecked_before_input(self):
        def replace_agent(read_count):
            if read_count >= 2:
                self.pane.name = "replacement-agent"

        self.pane.on_agent_get = replace_agent
        self.assertEqual(self.call()[0], 6)
        self.assertEqual(self.pane.events, [])

    def test_failed_text_is_unresolved_and_does_not_send_enter(self):
        self.pane.failure = "text"
        self.assertEqual(self.call()[0], 6)
        self.assertEqual([event[0] for event in self.pane.events], ["text"])
        self.assertEqual(self.call()[0], 5)
        self.assertEqual([event[0] for event in self.pane.events], ["text"])

    def test_failed_enter_is_unresolved_and_does_not_repeat_submission(self):
        self.pane.failure = "key"
        self.assertEqual(self.call()[0], 6)
        self.assertEqual([event[0] for event in self.pane.events], ["text", "key"])
        self.assertEqual(self.call()[0], 5)
        self.assertEqual([event[0] for event in self.pane.events], ["text", "key"])

    def test_continued_attempt_collects_its_original_receipt_normally(self):
        self.assertEqual(self.call()[0], 0)
        output = Path(self.original["host_output"])
        (output / "result.md").write_text("Fixture report completed after continuation.")
        (output / "receipt.json").write_text(json.dumps({
            "schema_version": 1, "request_id": "quota-fixture", "job_id": "j1",
            "attempt_id": self.original["attempt_id"], "outcome": "complete",
            "artifacts": ["result.md"], "unresolved": []}))
        self.pane.state = "idle"
        self.pane.output = "Fixture task completed."
        code, result = self.call("status")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["batch_state"], "collected")
        job = load_json(self.run / "state.json")["jobs"][0]
        self.assertTrue(job["settled"])
        self.assertEqual(job["collection"]["outcome"], "complete")
        self.assertFalse(job["admission_acquired"])
        self.assertEqual(job["attempt_id"], self.original["attempt_id"])
