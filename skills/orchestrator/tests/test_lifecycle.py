import tempfile
from pathlib import Path
import traceback
import unittest

from herdr_jobs.lifecycle import EffectUnknown, JobLifecycle
from herdr_jobs.admission_service import AdmissionService
from herdr_jobs.adapters import (DeterministicObservationAdapter, InMemoryEffectAdapter,
                                 NativeHerdrAdapter)
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

        self.assertEqual([call[0] for call in admission_store.calls], ["acquire", "release"])
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
