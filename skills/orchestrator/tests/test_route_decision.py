"""Offline launch-boundary regressions: no Herdr panes or agents are created."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.admission import AdmissionStore
from herdr_jobs.admission_service import AdmissionService
from herdr_jobs.adapters import NativeHerdrAdapter
from herdr_jobs.runner import run
from herdr_jobs.store import StateStore
from herdr_jobs.cli import main
from herdr_jobs.engine import Engine
from herdr_jobs.launch_transport import LaunchTransport
from herdr_jobs.records import JobError, load_json, prepare, save
from herdr_jobs.routing_decision import validate_decision
from herdr_jobs.transport import Deadline, NativeTransport
import test_transport


class RouteDecisionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "task.md").write_text("Interview the owner about one bounded decision.")
        self.manifest = self.root / "manifest.json"
        self.policy = PACKAGE / "launch-policy.json"
        self.job = {"job_id": "interview", "name": "Interview", "task_kind": "ordinary",
                    "task_file": str(self.root / "task.md"), "cwd": str(self.root),
                    "output_expectation": "One interview report", "writes_repository": False,
                    "task_class": "interview"}
        self.store = AdmissionStore(self.root / "admission")
        config = patch("herdr_jobs.transport._codex_config", return_value={})
        config.start()
        self.addCleanup(config.stop)

    def request(self, *, backend="managed", **values):
        save(self.manifest, {"schema_version": 1, "request_id": "interviews",
                             "jobs": [{**self.job, **values}]})
        request = prepare(self.manifest, self.policy)
        request["backend"] = backend
        return request

    def transport(self, models):
        fixture = test_transport.TransportTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.available = ["codex", "claude-code", "pi"]
        fixture.catalog = {"models": [{"id": model, "efforts": ["low", "medium", "high", "max"]}
                                      for model in models]}
        fixture.adapter.binding["supported_kinds"] = ["codex", "claude", "pi"]
        transport = object.__new__(LaunchTransport)
        transport.__dict__.update(fixture.adapter.__dict__)
        return transport, fixture

    def engine(self, request, transport, capabilities, name="run"):
        root = self.root / name
        root.mkdir()
        engine = Engine(root, transport, Deadline(2), self.store)
        engine.initialize(request, capabilities, None)
        return engine

    @staticmethod
    def claude_pin():
        return {"instruction": "Use claude-sonnet-5-5 at medium effort on Claude.",
                "runtime": "claude-code", "provider": "claude", "model": "claude-sonnet-5-5",
                "effort": "medium"}

    @staticmethod
    def escalation():
        return {"previous_level": 1,
                "failure": {"kind": "failure", "category": "reasoning", "source": "attempt-1/check.json",
                            "observed_at": "2026-10-20T12:00:00Z", "summary": "Contradictory requirements were missed"},
                "expected_improvement": "Resolve the contradictions before drafting questions"}

    def test_five_claude_interviews_do_not_authorize_blanket_opus_high(self):
        jobs = [{**self.job, "job_id": f"interview-{i}",
                 "override": {"instruction": "Use Claude for these five interviews", "runtime": "claude"}}
                for i in range(5)]
        save(self.manifest, {"schema_version": 1, "request_id": "five", "jobs": jobs})
        request = prepare(self.manifest, self.policy)
        for spec in request["jobs"]:
            self.assertEqual(spec["route_decision"]["resolved"], {
                "runtime": "claude-code", "provider": "claude", "model": "claude-sonnet-5-5", "effort": "low"})
            self.assertEqual(spec["route_decision"]["pins"], {"runtime": "claude"})
        self.assertFalse((self.root / "admission").exists())
        self.assertEqual(load_json(self.manifest)["jobs"], jobs)

    def test_harness_and_provider_pins_remain_separate_from_resolved_tuple(self):
        for pins in ({"runtime": "codex"}, {"provider": "codex"}, {"runtime": "pi"}):
            with self.subTest(pins=pins):
                if pins == {"runtime": "pi"}:
                    policy = load_json(self.policy)
                    policy["model_ladder"]["csv"] = str(PACKAGE / "tests/fixtures/opencode-go-model-ladder.csv")
                    self.policy = self.root / "pi-policy.json"
                    save(self.policy, policy)
                override = {"instruction": "Use this harness/provider", **pins}
                request = self.request(override=override)
                spec = request["jobs"][0]
                self.assertEqual(spec["override"], override)
                self.assertEqual(spec["route_decision"]["pins"], pins)
                self.assertEqual(spec["route_decision"]["level"], 1)
                self.assertEqual(spec["route_decision"]["resolved"]["model"], "gpt-6-luna")
                validate_decision(spec, request["policy"])

    def test_exact_pins_and_per_task_escalation_retain_their_evidence(self):
        request = self.request(override=self.claude_pin())
        decision = request["jobs"][0]["route_decision"]
        self.assertEqual(decision["pins"]["model"], "claude-sonnet-5-5")
        self.assertEqual(decision["resolved"]["effort"], "medium")
        request = self.request(model_level=2, escalation=self.escalation())
        decision = request["jobs"][0]["route_decision"]
        self.assertEqual(decision["escalation_steps"], 1)
        self.assertEqual(decision["difficulty_evidence"]["source"], "attempt-1/check.json")
        self.assertEqual(len(decision["csv_sha256"]), 64)
        for category in ("quota", "operational"):
            escalation = self.escalation()
            escalation["failure"]["category"] = category
            with self.assertRaisesRegex(JobError, "do not authorize"):
                self.request(model_level=2, escalation=escalation)
        assessment = {"task_class": "interview", "difficulty_evidence": {"source": "check", "summary": "failed"},
                      "sufficiency_reason": "Resolve contradictions", "escalation_steps": 3}
        with self.assertRaises(JobError):
            self.request(model_level=2, escalation=self.escalation(), routing_assessment=assessment)

    def test_unavailable_fallback_requires_structured_evidence(self):
        fallback = {"reason": "unavailable", "evidence": {"source": "exact-discovery.json",
                    "observed_at": "2026-10-20T12:00:00Z", "error": {
                        "kind": "unavailable", "message": "Exact model absent from provider catalog"}}}
        decision = self.request(model_level=1, model_fallback=fallback)["jobs"][0]["route_decision"]
        self.assertEqual(decision["fallback"], fallback)
        self.assertEqual(decision["level"], 1)
        with self.assertRaises(JobError):
            self.request(model_level=1, model_fallback={"reason": "unavailable", "evidence": "slow"})
        with self.assertRaises(JobError):
            self.request(override={"instruction": "Use an unsupported harness", "runtime": "omp"})

    def test_incomplete_batch_discovery_has_zero_launch_effects_on_both_backends(self):
        for backend in ("managed", "native"):
            with self.subTest(backend=backend):
                request = self.request(backend=backend)
                # Prepare separately to keep the decision bound to the actual assignment.
                self.job["job_id"] = "claude-interview"
                claude = self.request(backend=backend, override=self.claude_pin())["jobs"][0]
                self.job["job_id"] = "interview"
                request["jobs"].append(claude)
                transport, fixture = self.transport(["gpt-6-luna", "sonnet", "opus"])
                with patch.object(NativeTransport, "effect") as effect:
                    with self.assertRaisesRegex(JobError, "exact model unavailable"):
                        transport.preflight(request)
                    self.assertEqual(transport._verified, {})
                    effect.assert_not_called()
                self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in fixture.calls))
                self.assertFalse((self.root / "admission").exists())

    def test_failed_preflight_is_append_only_and_does_not_rewrite_manifest(self):
        for backend in ("managed", "native"):
            request = self.request(backend=backend, override=self.claude_pin())
            original = self.manifest.read_bytes()
            transport, _ = self.transport(["sonnet"])
            root = self.root / backend
            with redirect_stdout(io.StringIO()), patch.object(NativeTransport, "effect") as effect:
                code = main(["run", "--manifest", str(self.manifest), "--policy", str(self.policy),
                             "--run-dir", str(root)], transport_factory=lambda *args: transport, backend=backend)
            self.assertEqual(code, 4)
            effect.assert_not_called()
            holds = list((root / "preflight-holds").glob("*.json"))
            self.assertEqual(len(holds), 1)
            held = load_json(holds[0])
            self.assertEqual(held["state"], "held")
            self.assertEqual(held["request"]["jobs"][0]["route_decision"], request["jobs"][0]["route_decision"])
            self.assertEqual(self.manifest.read_bytes(), original)
            self.assertFalse((root / "state.json").exists())
            self.assertFalse((root / "workers").exists())

    def test_native_alias_cannot_bypass_discovery(self):
        pin = self.claude_pin()
        pin["model"] = "sonnet"
        request = self.request(backend="native", override=pin)
        transport, fixture = self.transport(["sonnet"])
        with self.assertRaisesRegex(JobError, "aliases"):
            transport.preflight(request)
        self.assertEqual(fixture.calls, [])

    def test_dispatch_requires_unchanged_decision_and_real_bound_lease(self):
        request = self.request()
        transport, _ = self.transport(["gpt-6-luna"])
        capabilities = transport.preflight(request)
        engine = self.engine(request, transport, capabilities)
        job = engine.state["jobs"][0]
        with patch.object(NativeTransport, "effect") as effect:
            with self.assertRaisesRegex(JobError, "shared admission lease"):
                transport.effect("split", job, "prompt")
            self.assertTrue(engine.acquire_admission(job))
            transport.authorize(job, self.store, request["request_id"], engine.root)
            original = deepcopy(job["spec"])
            job["spec"]["route_decision"]["sufficiency_reason"] = "changed after preflight"
            with self.assertRaisesRegex(JobError, "unchanged"):
                transport.effect("split", job, "prompt")
            job["spec"] = original
            self.store.release(job["admission_lease_id"])
            with self.assertRaisesRegex(JobError, "missing"):
                transport.effect("split", job, "prompt")
            effect.assert_not_called()

    def test_four_active_claude_leases_queue_fifth_then_launch_once(self):
        for backend in ("managed", "native"):
            with self.subTest(backend=backend):
                self.store = AdmissionStore(self.root / (backend + "-admission"))
                for index in range(4):
                    self.store.acquire("claude", "claude-sonnet-5-5", f"active-{index}", 4, "other")
                request = self.request(backend=backend, override=self.claude_pin())
                transport, _ = self.transport(["claude-sonnet-5-5"])
                engine = self.engine(request, transport, transport.preflight(request), backend)
                job = engine.state["jobs"][0]
                actions = []

                def effect(action, current, prompt):
                    actions.append(action)
                    if action == "split":
                        return {"pane_id": "fixture:1"}
                    if action == "move":
                        return {"pane_id": "fixture:2", "workspace_id": "workspace:1"}
                    return {"prompt_submitted": True}

                with patch.object(NativeTransport, "effect", side_effect=effect):
                    engine.launch(job)
                    self.assertEqual(actions, [])
                    self.assertEqual(job["phase"], "pending")
                    self.assertEqual(job["admission_wait"]["reason"], "capacity")
                    self.store.release("active-0")
                    engine.launch(job)
                    engine.launch(job)
                self.assertEqual(actions, ["split", "move", "start"])
                self.assertEqual(self.store.status("claude")["active_count"], 4)
                evidence = load_json(Path(job["host_output"]) / "launch-evidence.json")
                self.assertEqual(evidence["backend"], backend)
                self.assertEqual(evidence["lease_id"], job["admission_lease_id"])
                self.assertEqual(evidence["decision"]["requested"], self.claude_pin())
                self.assertEqual(len(engine.state["jobs"]), 1)

    def test_add_preflights_existing_queued_jobs_as_well_as_the_new_job(self):
        for index in range(4):
            self.store.acquire("claude", "claude-sonnet-5-5", f"active-{index}", 4, "other")
        self.request(backend="native", override=self.claude_pin())
        transport, _ = self.transport(["claude-sonnet-5-5"])
        root = self.root / "queued-add"
        arguments = ["--manifest", str(self.manifest), "--policy", str(self.policy),
                     "--run-dir", str(root), "--wait-seconds", "0.01"]
        with patch.dict(os.environ, {"ORCHESTRATOR_ADMISSION_DIR": str(self.store.state_dir)}), \
                redirect_stdout(io.StringIO()), patch.object(NativeTransport, "effect") as effect:
            self.assertEqual(main(["run", *arguments], transport_factory=lambda *args: transport,
                                  backend="native"), 0)
            self.job["job_id"] = "second"
            self.request(backend="native", override=self.claude_pin())
            self.assertEqual(main(["add", *arguments], transport_factory=lambda *args: transport,
                                  backend="native"), 0)
            effect.assert_not_called()
        self.assertEqual(set(transport._verified), {"interview", "second"})
        state = load_json(root / "state.json")
        self.assertEqual(len(state["jobs"]), 2)
        self.assertTrue(all(job["pane_id"] is None for job in state["jobs"]))

    def test_reducer_native_adapter_uses_the_same_discovery_and_admission_gate(self):
        request = self.request(backend="native")
        transport, _ = self.transport(["gpt-6-luna"])
        store = StateStore(self.root / "native-reducer" / "state.json")
        adapter = NativeHerdrAdapter(transport=transport, admission_service=AdmissionService(self.store))
        actions = []

        def effect(action, current, prompt):
            actions.append(action)
            if action in ("split", "move"):
                return {"pane_id": "fixture:1", "workspace_id": "workspace:1"}
            return {"prompt_submitted": True}

        with patch.object(NativeTransport, "effect", side_effect=effect), patch.object(
                transport, "observe", return_value={"state": "done", "identity_verified": True,
                                                     "receipt_valid": True}):
            run(request, store=store, effect_port=adapter, max_steps=20)
        self.assertEqual(actions, ["split", "move", "start"])
        self.assertEqual(self.store.status("codex")["active_count"], 0)
        records = list((self.root / "native-reducer" / "workers").glob("*/*/launch-evidence.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(load_json(records[0])["backend"], "native")
        telemetry = load_json(records[0].with_name("telemetry.json"))
        self.assertEqual(telemetry["launch"]["backend"], "native")
        self.assertIsNotNone(telemetry["started_at"])
        self.assertIsNotNone(telemetry["ended_at"])

    def test_parked_blocked_worker_retains_lease_and_resumes_without_duplicate_start(self):
        request = self.request(backend="native")
        transport, _ = self.transport(["gpt-6-luna"])
        engine = self.engine(request, transport, transport.preflight(request))
        job = engine.state["jobs"][0]
        actions = []

        def effect(action, current, prompt):
            actions.append(action)
            if action in ("split", "move"):
                return {"pane_id": "fixture:1", "workspace_id": "workspace:1"}
            return {"launch_blocked": True, "observation": {"state": "blocked", "identity_verified": True}}

        with patch.object(NativeTransport, "effect", side_effect=effect):
            engine.launch(job)
        lease = job["admission_lease_id"]
        resumed = Engine(engine.root, transport, Deadline(2), self.store)
        resumed.load()
        capabilities = transport.preflight(request, existing=resumed.state)
        resumed.bind(capabilities)
        observation = {"state": "blocked", "identity_verified": True, "prompt_verified": False}
        with patch.object(transport, "observe", return_value=observation), patch.object(NativeTransport, "effect") as effect:
            resumed.drive(status_only=True)
            effect.assert_not_called()
        self.assertEqual(actions.count("start"), 1)
        self.assertEqual(self.store.status("codex")["leases"][0]["lease_id"], lease)
        self.assertEqual(resumed.state["jobs"][0]["pane_id"], "fixture:1")


if __name__ == "__main__":
    unittest.main()
