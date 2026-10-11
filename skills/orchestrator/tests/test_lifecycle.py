import tempfile
from pathlib import Path
import unittest

from herdr_jobs.lifecycle import EffectUnknown, JobLifecycle
from herdr_jobs.runner import run
from herdr_jobs.store import StateStore


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


class LifecycleTest(unittest.TestCase):
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
