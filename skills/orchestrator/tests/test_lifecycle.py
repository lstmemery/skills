import json
import tempfile
from pathlib import Path
import traceback
import unittest

from herdr_jobs.lifecycle import EffectUnknown, JobLifecycle
from herdr_jobs.admission import AdmissionStore
from herdr_jobs.admission_service import AdmissionService
from herdr_jobs.adapters import (DeterministicObservationAdapter, InMemoryEffectAdapter,
                                 NativeHerdrAdapter)
from herdr_jobs.engine import Engine
from herdr_jobs.runner import CompatibilityRunner, run
from herdr_jobs.store import StateStore
from herdr_jobs.transport import RateLimited


def job(route_mode="agent", runtime="codex"):
    return {
        "job_id": "job-1",
        "spec": {"job_id": "job-1", "route": {"mode": route_mode, "runtime": runtime}},
        "phase": "pending",
        "attempt_id": "a" * 32,
        "pane_id": None,
        "pending_effect": None,
        "history": [],
        "settled": False,
        "activity_seen": False,
    }


class ImmediatePort:
    def __init__(self):
        self.actions = []

    def execute(self, effect):
        self.actions.append(effect.action)
        if effect.action == "split":
            return {"pane_id": "pane-old"}
        if effect.action == "move":
            return {"pane_id": "pane-new", "workspace_id": "workspace-1"}
        if effect.action == "start":
            return {"prompt_submitted": True}
        return {}

    def observe(self, current):
        return {"state": "done", "identity_verified": True, "receipt_valid": True}


class UnknownPort:
    def __init__(self):
        self.actions = []

    def execute(self, effect):
        self.actions.append(effect.action)
        raise EffectUnknown("response lost")


class ReconcilePort(UnknownPort):
    def execute(self, effect):
        self.actions.append(effect.action)
        if len(self.actions) == 1:
            raise EffectUnknown("response lost")
        if effect.action == "move":
            return {"pane_id": "pane-new", "workspace_id": "workspace-1"}
        return {"prompt_submitted": True}

    def observe(self, current):
        if len(self.actions) >= 3:
            return {"state": "done", "identity_verified": True, "receipt_valid": True}
        return {"pane_id": "pane-created", "identity_verified": True}


class FakeAdmissionStore:
    def __init__(self):
        self.calls = []
        self.held = False
        self.admitted = True

    def acquire(self, provider, model, lease_id, cap, request_id, bypass_admission=False):
        self.calls.append(("acquire", lease_id, provider, model, cap, request_id))
        self.held = self.admitted
        return {"admitted": self.admitted, "lease_held": self.admitted, "provider": provider,
                "provider_active": 3 if not self.admitted else 1, "cap": cap,
                "reason": None if self.admitted else "capacity", "retry_at": None}

    def release(self, lease_id):
        self.calls.append(("release", lease_id))
        self.held = False
        return {"released": True}

    def status(self, provider):
        return {"active_count": int(self.held)}


class FakeNativeTransport:
    def __init__(self):
        self.actions = []

    def effect(self, action, job, prompt):
        self.actions.append(action)
        if action == "split":
            return {"pane_id": "pane-old"}
        if action == "move":
            return {"pane_id": "pane-new", "workspace_id": "workspace-1"}
        if action == "start":
            return {"prompt_submitted": True}
        return {}

    def observe(self, job):
        return {"state": "done", "identity_verified": True, "receipt_valid": True}

    def close_workspace(self, job):
        return {"closed": True, "workspace_id": job.get("workspace_id")}

    def workspace_is_open(self, job):
        return True


class RateLimitedOncePort:
    def __init__(self):
        self.effects = []
        self.start_attempts = []

    def execute(self, effect):
        self.effects.append(effect)
        if effect.action == "start":
            self.start_attempts.append(effect.attempt_id)
            if len(self.start_attempts) == 1:
                raise RateLimited({"retry_after_seconds": 60, "source": "retry-after"})
        if effect.action == "split":
            return {"pane_id": f"pane-{len(self.start_attempts)}"}
        if effect.action == "move":
            return {"pane_id": f"moved-{len(self.start_attempts)}", "workspace_id": "workspace-1"}
        return {"prompt_submitted": True}

    def observe(self, job):
        return {"state": "done", "identity_verified": True, "receipt_valid": True}


class ObservedRateLimitOncePort(RateLimitedOncePort):
    def __init__(self):
        super().__init__()
        self.observations = 0

    def execute(self, effect):
        self.effects.append(effect)
        if effect.action == "start":
            self.start_attempts.append(effect.attempt_id)
            return {"prompt_submitted": True}
        if effect.action == "split":
            return {"pane_id": f"pane-{len(self.start_attempts)}"}
        if effect.action == "move":
            return {"pane_id": f"moved-{len(self.start_attempts)}", "workspace_id": "workspace-1"}
        return {}

    def observe(self, job):
        self.observations += 1
        if self.observations == 1:
            return {"state": "done", "identity_verified": True,
                    "rate_limit": {"retry_after_seconds": 60, "source": "retry-after"}}
        return {"state": "done", "identity_verified": True, "receipt_valid": True}


class BrokenObservationPort(ImmediatePort):
    def observe(self, job):
        raise TypeError("adapter observation bug")


class LifecycleTest(unittest.TestCase):
    def test_native_runner_delivers_engine_prompt_and_runtime_job(self):
        class CapturingTransport(FakeNativeTransport):
            def __init__(self):
                super().__init__()
                self.start_payload = None
                self.preflight_existing = None

            def preflight(self, _request, existing=None):
                self.preflight_existing = existing
                return {"models": {"adapter-job": "codex/engine-model"},
                        "binding": {"jail_export": None}}

            def effect(self, action, current_job, prompt):
                if action == "start":
                    self.start_payload = (current_job, prompt)
                return super().effect(action, current_job, prompt)

        with tempfile.TemporaryDirectory() as directory:
            transport = CapturingTransport()
            request = {"request_id": "adapter-request", "jobs": [{
                "job_id": "adapter-job", "task_kind": "ordinary",
                "route": {"mode": "agent", "runtime": "codex"},
                "task": "This task text should reach the worker.",
                "cwd": directory, "kind": "codex",
            }]}
            run(request, store=StateStore(Path(directory) / "state.json"),
                effect_port=NativeHerdrAdapter(transport=transport), max_steps=20)

        current_job, prompt = transport.start_payload
        self.assertIn("This task text should reach the worker.", prompt)
        self.assertRegex(current_job["agent_name"], r"^c9-[0-9a-f]{24}$")
        self.assertEqual(current_job["resolved_model"], "codex/engine-model")
        self.assertIn("host_output", current_job)
        self.assertIn("exit_record", current_job)
        self.assertIsNone(transport.preflight_existing["jobs"][0]["resolved_model"])

    def test_pending_effect_checkpoint_remains_loadable_by_engine(self):
        fixture = Path(__file__).parent / "fixtures/lifecycle/engine-launch-state.json"
        state = json.loads(fixture.read_text(encoding="utf-8"))
        state["jobs"][0].update(phase="pending", pane_id=None, pending_effect=None,
                                settled=False, observed=None)
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            admission = FakeAdmissionStore()
            Engine(directory, object(), object(), admission_store=admission).load()
            run(store=StateStore(state_path), effect_port=UnknownPort())

            reopened = Engine(directory, object(), object(), admission_store=admission)
            reopened.load()

        self.assertEqual(reopened.state["jobs"][0]["pending_effect"]["action"], "split")
        self.assertEqual(set(reopened.state["jobs"][0]["pending_effect"]), {"action", "started_at"})

    def test_native_rate_limit_blocks_a_following_run_through_shared_admission(self):
        class RateLimitedTransport(FakeNativeTransport):
            def __init__(self):
                super().__init__()
                self.started = []

            def effect(self, action, current_job, prompt):
                if action == "start":
                    job_id = current_job["spec"]["job_id"]
                    self.started.append(job_id)
                    if len(self.started) == 1:
                        raise RateLimited({"retry_after_seconds": 60, "source": "retry-after"})
                return super().effect(action, current_job, prompt)

        with tempfile.TemporaryDirectory() as directory:
            now = [1000.0]
            admission = AdmissionStore(state_dir=Path(directory) / "admission", clock=lambda: now[0])
            service = AdmissionService(admission)
            transport = RateLimitedTransport()
            policy = {"provider_admission": {"default_provider_cap": 4,
                                               "provider_caps": {"codex": 4},
                                               "default_backoff_seconds": 60}}
            first_request = {"request_id": "first-run", "policy": policy, "jobs": [
                {"job_id": job_id, "task_kind": "ordinary",
                 "route": {"mode": "agent", "runtime": "codex"},
                 "task": "Do the assigned task.", "output_expectation": "Complete it.",
                 "cwd": directory, "kind": "codex"}
                for job_id in ("job-1", "job-2")
            ]}
            run(first_request, store=StateStore(Path(directory) / "first/state.json"),
                effect_port=NativeHerdrAdapter(transport=transport, admission_service=service),
                clock=lambda: now[0], max_steps=40)
            backoffs = admission.status("codex")["backoffs"]
            second_request = {"request_id": "second-run", "policy": policy, "jobs": [
                {"job_id": "job-3", "task_kind": "ordinary",
                 "route": {"mode": "agent", "runtime": "codex"},
                 "task": "Do the assigned task.", "output_expectation": "Complete it.",
                 "cwd": directory, "kind": "codex"}
            ]}
            second = run(second_request, store=StateStore(Path(directory) / "second/state.json"),
                         effect_port=NativeHerdrAdapter(transport=transport, admission_service=service),
                         clock=lambda: now[0], max_steps=20)

        self.assertEqual([item["until"] for item in backoffs], [1060.0])
        self.assertEqual(transport.started, ["job-1"])
        self.assertEqual(second.state["jobs"][0]["admission_wait"]["reason"], "backoff")

    def test_retry_id_cannot_reuse_an_id_in_legacy_history(self):
        old_attempt = "a" * 32
        current_attempt = "b" * 32
        request_job = job()
        request_job["attempt_id"] = current_attempt
        request_job["history"] = [{"action": "rate_limit_retry", "attempt_id": old_attempt}]
        with tempfile.TemporaryDirectory() as directory:
            now = [1000.0]
            store = StateStore(Path(directory) / "state.json")
            port = RateLimitedOncePort()
            run({"request_id": "legacy-attempt-history", "jobs": [request_job]},
                store=store, effect_port=port, clock=lambda: now[0])
            now[0] = 1060.0
            with self.assertRaisesRegex(ValueError, "fresh attempt ID"):
                run(store=store, effect_port=port, clock=lambda: now[0],
                    attempt_id_factory=lambda: old_attempt)

    def test_blocked_startup_returns_without_reobserving_or_claiming_unknown_effect(self):
        class BlockedPort:
            def __init__(self):
                self.observations = 0

            def execute(self, effect):
                if effect.action == "split":
                    return {"pane_id": "pane-1"}
                if effect.action == "move":
                    return {"pane_id": "pane-2", "workspace_id": "workspace-1"}
                if effect.action == "start":
                    return {"launch_blocked": True, "issue": "startup blocked",
                            "observation": {"state": "blocked", "identity_verified": True}}
                return {}

            def observe(self, _job):
                self.observations += 1
                return {"state": "blocked", "identity_verified": True}

        with tempfile.TemporaryDirectory() as directory:
            port = BlockedPort()
            result = run({"request_id": "blocked", "jobs": [job()]},
                         store=StateStore(Path(directory) / "state.json"),
                         effect_port=port, max_steps=12)

        self.assertEqual(port.observations, 0)
        self.assertFalse(result.unresolved_effect)
        self.assertIsNone(result.state["jobs"][0]["pending_effect"])
        self.assertEqual(result.state["jobs"][0]["issue"], "startup blocked")

    def test_unsafe_startup_observation_returns_once_without_pending_effect(self):
        class ObservationPort:
            def __init__(self, observation):
                self.observation = observation
                self.observations = 0

            def execute(self, _effect):
                raise AssertionError("an unsafe startup must not launch a prompt")

            def observe(self, _job):
                self.observations += 1
                return self.observation

        cases = (
            ({"state": "idle", "identity_verified": False},
             "worker startup identity is unverified; the task prompt was not sent"),
            ({"state": "working", "identity_verified": True},
             "worker was not idle after startup; the task prompt was not sent"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (observation, expected_issue) in enumerate(cases):
                request_job = job()
                request_job["phase"] = "ready"
                port = ObservationPort(observation)
                result = run({"request_id": f"unsafe-startup-{index}", "jobs": [request_job]},
                             store=StateStore(Path(directory) / f"state-{index}.json"),
                             effect_port=port, max_steps=12)
                self.assertEqual(port.observations, 1)
                self.assertFalse(result.unresolved_effect)
                self.assertIsNone(result.state["jobs"][0]["pending_effect"])
                self.assertEqual(result.state["jobs"][0]["issue"], expected_issue)

    def test_compatibility_runner_requires_a_current_engine_reference(self):
        with self.assertRaisesRegex(TypeError, "legacy_runner is required"):
            CompatibilityRunner()

    def test_phase_effect_order_is_deterministic(self):
        reducer = JobLifecycle()
        state = job()
        expected = ("split", "move", "start")
        seen = []
        receipts = {
            "split": {"pane_id": "pane-old", "identity_verified": True},
            "move": {"pane_id": "pane-new", "workspace_id": "workspace-1", "identity_verified": True},
            "start": {"prompt_submitted": True, "effect_receipt": True},
        }
        for action in expected:
            transition = reducer.step(state)
            self.assertEqual(len(transition.effects), 1)
            seen.append(transition.effects[0].action)
            state = dict(transition.next_state)
            state = dict(reducer.step(state, receipts[action]).next_state)
        self.assertEqual(tuple(seen), expected)
        self.assertEqual(state["phase"], "submitted")
        self.assertIsNone(state["pending_effect"])

    def test_unknown_effect_is_pending_and_never_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            port = UnknownPort()
            request = {"request_id": "request-1", "jobs": [job()]}
            first = run(request, store=store, effect_port=port)
            second = run(store=store, effect_port=port)

        self.assertTrue(first.unresolved_effect)
        self.assertTrue(second.unresolved_effect)
        self.assertEqual(port.actions, ["split"])
        self.assertEqual(second["jobs"][0]["pending_effect"]["action"], "split")

    def test_unknown_effect_can_be_reconciled_by_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            port = ReconcilePort()
            request = {"request_id": "request-1", "jobs": [job()]}
            first = run(request, store=store, effect_port=port)
            second = run(store=store, effect_port=port)

        self.assertTrue(first.unresolved_effect)
        self.assertFalse(second.unresolved_effect)
        self.assertEqual(port.actions, ["split", "move", "start"])
        self.assertEqual(second["jobs"][0]["pane_id"], "pane-new")
        self.assertIn("pane-created", second.state["jobs"][0]["previous_pane_ids"])

    def test_native_adapter_executes_and_releases_provider_lease_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            admission_store = FakeAdmissionStore()
            transport = FakeNativeTransport()
            adapter = NativeHerdrAdapter(
                transport=transport,
                admission_service=AdmissionService(admission_store),
            )
            request_job = job()
            request_job.update(provider="codex", admission_model="default",
                               admission_lease_id="lease-1")
            request = {"request_id": "request-1", "policy": {"provider_admission": {
                "default_provider_cap": 3, "provider_caps": {"codex": 2}}},
                "jobs": [request_job]}

            result = run(request, store=store, effect_port=adapter,
                         clock=lambda: 100.0, max_steps=30)

        self.assertEqual([call[0] for call in admission_store.calls], ["acquire", "acquire", "release"])
        self.assertEqual(admission_store.calls[0], ("acquire", "lease-1", "codex", "default", 2, "request-1"))
        self.assertEqual(transport.actions, ["split", "move", "start"])
        self.assertFalse(result.state["jobs"][0]["admission_acquired"])

    def test_admission_denial_is_checkpointed_without_launching_or_spinning(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            admission_store = FakeAdmissionStore()
            admission_store.admitted = False
            transport = FakeNativeTransport()
            adapter = NativeHerdrAdapter(transport=transport,
                                         admission_service=AdmissionService(admission_store))
            request_job = job()
            request_job.update(provider="codex", admission_model="default",
                               admission_lease_id="lease-1")
            request = {"request_id": "request-1", "policy": {"provider_admission": {
                "default_provider_cap": 3, "provider_caps": {"codex": 2}}},
                "jobs": [request_job]}

            result = run(request, store=store, effect_port=adapter, max_steps=30)

        self.assertEqual(admission_store.calls[0][0], "acquire")
        self.assertEqual(transport.actions, [])
        self.assertEqual(result.state["jobs"][0]["admission_wait"]["reason"], "capacity")
        self.assertEqual(result.state["jobs"][0]["phase"], "pending")
        self.assertFalse(result.unresolved_effect)

    def test_rate_limited_retry_waits_for_backoff_and_uses_a_fresh_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            port = RateLimitedOncePort()
            request = {"request_id": "request-1", "jobs": [job()]}
            now = [1000.0]
            attempts = iter(("b" * 32,))

            first = run(request, store=store, effect_port=port, clock=lambda: now[0],
                        attempt_id_factory=lambda: next(attempts))
            now[0] = 1059.0
            before_due = run(store=store, effect_port=port, clock=lambda: now[0],
                             attempt_id_factory=lambda: next(attempts))
            now[0] = 1060.0
            retried = run(store=store, effect_port=port, clock=lambda: now[0],
                          attempt_id_factory=lambda: next(attempts))

        self.assertEqual(first.state["jobs"][0]["rate_limit_until"], 1060.0)
        self.assertEqual(before_due.state["jobs"][0]["attempt_id"], "a" * 32)
        self.assertEqual(port.start_attempts, ["a" * 32, "b" * 32])
        self.assertEqual(retried.state["jobs"][0]["attempt_id"], "b" * 32)

    def test_rate_limited_retry_refuses_an_attempt_id_already_used(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            port = RateLimitedOncePort()
            now = [1000.0]
            run({"request_id": "request-1", "jobs": [job()]}, store=store, effect_port=port,
                clock=lambda: now[0])
            now[0] = 1060.0
            with self.assertRaisesRegex(ValueError, "fresh attempt ID"):
                run(store=store, effect_port=port, clock=lambda: now[0],
                    attempt_id_factory=lambda: "a" * 32)

    def test_observed_rate_limit_delay_becomes_an_absolute_retry_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            port = ObservedRateLimitOncePort()
            request = {"request_id": "request-1", "jobs": [job()]}
            now = [1000.0]
            attempts = iter(("b" * 32,))

            first = run(request, store=store, effect_port=port, clock=lambda: now[0],
                        attempt_id_factory=lambda: next(attempts))
            now[0] = 1059.0
            before_due = run(store=store, effect_port=port, clock=lambda: now[0],
                             attempt_id_factory=lambda: next(attempts))
            now[0] = 1060.0
            retried = run(store=store, effect_port=port, clock=lambda: now[0],
                          attempt_id_factory=lambda: next(attempts))

        self.assertEqual(first.state["jobs"][0]["rate_limit_until"], 1060.0)
        self.assertEqual(before_due.state["jobs"][0]["attempt_id"], "a" * 32)
        self.assertEqual(port.start_attempts, ["a" * 32, "b" * 32])
        self.assertEqual(retried.state["jobs"][0]["attempt_id"], "b" * 32)

    def test_observation_programming_errors_keep_their_type_and_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            try:
                run({"request_id": "request-1", "jobs": [job()]},
                    store=StateStore(Path(directory) / "state.json"),
                    effect_port=BrokenObservationPort(), max_steps=10)
            except TypeError as error:
                self.assertEqual(str(error), "adapter observation bug")
                frames = [frame.name for frame in traceback.extract_tb(error.__traceback__)]
            else:
                self.fail("observation programming error was converted into lifecycle state")

        self.assertIn("observe", frames)

    def test_unresolved_effect_observation_errors_keep_their_type_and_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            request = {"request_id": "request-1", "jobs": [job()]}
            first = run(request, store=store, effect_port=UnknownPort())
            self.assertTrue(first.unresolved_effect)
            try:
                run(store=store, effect_port=BrokenObservationPort())
            except TypeError as error:
                self.assertEqual(str(error), "adapter observation bug")
                frames = [frame.name for frame in traceback.extract_tb(error.__traceback__)]
            else:
                self.fail("unresolved-effect observation error was converted to lifecycle state")
            self.assertEqual(store.load()["jobs"][0]["pending_effect"]["action"], "split")

        self.assertIn("observe", frames)

    def test_observation_adapters_share_the_nested_job_id_fallback(self):
        observed = {"job-1": {"state": "idle", "identity_verified": True}}
        state = {"spec": {"job_id": "job-1"}}
        self.assertEqual(InMemoryEffectAdapter(observations=observed).observe(state), observed["job-1"])
        self.assertEqual(DeterministicObservationAdapter(observed).observe(state), observed["job-1"])

    def test_rate_limit_is_a_known_retry_state(self):
        reducer = JobLifecycle()
        state = job()
        state["phase"] = "ready"
        state["observed"] = {"state": "idle", "identity_verified": True}
        transition = reducer.step(state, {"rate_limit": {"retry_after_seconds": 4, "source": "retry-after"}})
        self.assertEqual(transition.next_state["phase"], "retry_ready")
        self.assertFalse(transition.unknown)
        self.assertEqual(transition.next_state["rate_limit_until"], 4)

    def test_jail_exit_settles_without_replaying_jail_effect(self):
        reducer = JobLifecycle()
        state = job("jail")
        state["phase"] = "pending"
        transition = reducer.step(state)
        self.assertEqual(transition.effects[0].action, "split")
        state = dict(transition.next_state)
        state = dict(reducer.step(state, {"pane_id": "pane", "identity_verified": True}).next_state)
        move = reducer.step(state)
        state = dict(reducer.step(move.next_state, {"pane_id": "workspace-pane", "workspace_id": "workspace",
                                                     "identity_verified": True}).next_state)
        self.assertEqual(state["phase"], "moved")
        launch = reducer.step(state)
        self.assertEqual(launch.effects[0].action, "jail")
        settled = reducer.step(launch.next_state, {"state": "exited", "identity_verified": True,
                                                   "effect_receipt": True})
        self.assertEqual(settled.next_state["phase"], "submitted")
        self.assertTrue(settled.next_state["settled"])


if __name__ == "__main__":
    unittest.main()
