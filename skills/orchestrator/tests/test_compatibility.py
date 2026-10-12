import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from herdr_jobs.admission_service import AdmissionService
from herdr_jobs.adapters import NativeHerdrAdapter
from herdr_jobs.engine import Engine
from herdr_jobs.lifecycle import EffectUnknown
from herdr_jobs.records import JobError
from herdr_jobs.runner import CompatibilityRunner, RunResult, launch_compatibility_view
from herdr_jobs.store import StateStore
from test_lifecycle import FakeAdmissionStore, FakeNativeTransport


FIXTURES = Path(__file__).parent / "fixtures" / "lifecycle"
ATTEMPT_ID = "a" * 32
FIXED_TIME = "2030-01-01T00:00:00+00:00"


class Deadline:
    def check(self):
        return None


def request():
    return {
        "schema_version": 1,
        "request_id": "compatibility-request",
        "concurrency": 1,
        "jobs": [{
            "job_id": "compatibility-job",
            "task_kind": "ordinary",
            "route": {"mode": "agent", "runtime": "codex"},
            "task": "Run the compatibility fixture.",
            "output_expectation": "A fixture report.",
            "writes_repository": False,
        }],
        "policy": {"provider_admission": {
            "default_provider_cap": 4,
            "provider_caps": {"codex": 2},
            "default_backoff_seconds": 60,
        }},
        "policy_digest": "fixture-policy-digest",
    }


def normalized(value, root):
    if isinstance(value, dict):
        return {key: "$LEASE" if key == "admission_lease_id" else normalized(item, root)
                for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root), "$RUN_DIR")
    return value


class CompatibilityTest(unittest.TestCase):
    def test_admission_service_methods_smoke(self):
        calls = []

        class Store:
            def acquire(self, *args, **kwargs):
                calls.append(("acquire", args, kwargs))
                return {"acquired": True}

            def release(self, *args):
                calls.append(("release", args))
                return {"released": True}

            def note_rate_limit(self, *args):
                calls.append(("rate_limit", args))
                return {"noted": True}

        store = Store()
        service = AdmissionService(store)
        job = {"provider": "codex", "admission_lease_id": "lease-1"}
        signal = {"retry_after_seconds": 12, "source": "test"}

        self.assertIs(service.store, store)
        self.assertEqual(service.acquire(job, 3, "request-1"), {"acquired": True})
        self.assertEqual(service.release(job), {"released": True})
        self.assertEqual(service.rate_limit(job, signal), {"noted": True})
        self.assertEqual(AdmissionService.provider_cap({"provider_admission": {}}, "codex"),
                         Engine.provider_cap_from_config({}, "codex"))
        self.assertEqual(AdmissionService.pool("openai-codex"), "codex")
        self.assertEqual([call[0] for call in calls], ["acquire", "release", "rate_limit"])

    def test_admission_provider_cap_reuses_engine_validation(self):
        policy = {"provider_admission": {
            "default_provider_cap": 4,
            "provider_caps": {"codex": 0},
        }}
        engine = Engine("/tmp/provider-cap-validation", object(), Deadline())
        engine.state = {"request": {"policy": policy}}

        with self.assertRaisesRegex(JobError, "stored provider admission caps are malformed") as engine_error:
            engine.provider_cap("codex")
        with self.assertRaisesRegex(JobError, "stored provider admission caps are malformed") as service_error:
            AdmissionService.provider_cap(policy, "codex")

        self.assertEqual(str(engine_error.exception), str(service_error.exception))

    def test_unknown_reducer_effect_skips_legacy_runner_for_reconciliation(self):
        events = []

        class LostAcknowledgmentPort:
            def execute(self, effect):
                events.append(("reducer", effect.action))
                raise EffectUnknown("split acknowledgment lost", effect)

        def legacy_runner(_request):
            events.append(("legacy", "split"))
            raise AssertionError("legacy runner must not run while the reducer effect is unresolved")

        unresolved_request = {"request_id": "unknown-compatibility", "jobs": [{
            "job_id": "job-1",
            "route": {"mode": "agent", "runtime": "codex"},
        }]}
        with tempfile.TemporaryDirectory() as directory:
            result = CompatibilityRunner(legacy_runner).run(
                unresolved_request,
                store=StateStore(Path(directory) / "state.json"),
                effect_port=LostAcknowledgmentPort(),
                max_steps=4,
            )

        self.assertTrue(result.unresolved_effect)
        self.assertEqual(result["batch_state"], "unresolved_effect")
        self.assertEqual(result.state["jobs"][0]["pending_effect"]["action"], "split")
        self.assertEqual(events, [("reducer", "split")])

    def test_migrated_launch_matches_current_engine_result_and_state_fixtures(self):
        self.maxDiff = None
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            request_value = request()
            legacy_run_dir = root / "legacy"
            legacy_run_dir.mkdir()
            legacy_transport = FakeNativeTransport()
            legacy_engine = Engine(legacy_run_dir, legacy_transport, Deadline(), FakeAdmissionStore())

            def legacy_runner(pinned_request):
                with patch("herdr_jobs.engine.uuid.uuid4", return_value=SimpleNamespace(hex=ATTEMPT_ID)), \
                        patch("herdr_jobs.engine.now", return_value=FIXED_TIME):
                    legacy_engine.initialize(
                        pinned_request,
                        {"session_id": "compatibility-session", "models": {"compatibility-job": None},
                         "binding": {}},
                        None,
                    )
                    legacy_engine.launch(legacy_engine.state["jobs"][0])
                return RunResult(state=legacy_engine.state, result=legacy_engine.result())

            reducer_adapter = NativeHerdrAdapter(
                transport=FakeNativeTransport(),
                admission_service=AdmissionService(FakeAdmissionStore()),
            )
            compatibility = CompatibilityRunner(legacy_runner, projector=launch_compatibility_view)
            result = compatibility.run(
                request_value,
                store=StateStore(root / "reducer" / "state.json"),
                effect_port=reducer_adapter,
                clock=lambda: 100.0,
                stop_at_phase="submitted",
                attempt_id_factory=lambda: ATTEMPT_ID,
            )

            expected_result = json.loads((FIXTURES / "engine-launch-result.json").read_text())
            expected_state = json.loads((FIXTURES / "engine-launch-state.json").read_text())
            self.assertEqual(normalized(legacy_engine.result(), legacy_run_dir), expected_result)
            self.assertEqual(normalized(legacy_engine.state, legacy_run_dir), expected_state)
            self.assertEqual(result.state["jobs"][0]["phase"], "submitted")


if __name__ == "__main__":
    unittest.main()
