"""Engine checkpoint compatibility at its load/result boundary.

The engine's real dependencies are required. Missing snapshot modules produce
an explicit skip, never substitutes for admission or worker validation.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility.builders import ROOT, golden, write_json

sys.path.insert(0, str(ROOT / "skills/skills/orchestrator/scripts"))
from herdr_jobs import records

try:
    from herdr_jobs.engine import Engine
except ModuleNotFoundError as error:
    if error.name not in {"herdr_jobs.admission", "herdr_jobs.retry", "herdr_jobs.transport",
                          "herdr_jobs.model_ladder", "herdr_jobs.worker_records"}:
        raise
    Engine = None
    ENGINE_UNAVAILABLE = "source snapshot omits " + error.name
else:
    ENGINE_UNAVAILABLE = ""


class NoExternalEffects:
    def status(self, provider):
        return {"active_count": 0}


@unittest.skipIf(Engine is None, ENGINE_UNAVAILABLE)
class StateCompatibilityTests(unittest.TestCase):
    def test_current_and_legacy_v1_checkpoints_resume(self):
        fixture = golden("state-v1")
        for name in ("current", "legacy"):
            with self.subTest(version=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original = fixture[name]
                write_json(root / "state.json", original)
                engine = Engine(root, None, 0, admission_store=NoExternalEffects())
                engine.load()
                result = engine.result()
                self.assertEqual(result.pop("run_dir"), str(root))
                self.assertEqual(result, fixture["result"])
                engine.checkpoint()
                stored = json.loads((root / "state.json").read_text())
                expected = copy.deepcopy(fixture["current"])
                expected["updated_at"] = stored["updated_at"]
                if name == "legacy":
                    del expected["retry_override"]
                    identity = {"run_dir": str(root), "request_id": "compat-request",
                                "job_id": "job-1", "attempt_id": "1" * 32}
                    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
                    expected["jobs"][0]["admission_lease_id"] = "managed-" + digest[:40]
                self.assertEqual(stored, expected)
                engine.load()
                self.assertEqual(engine.result()["jobs"], fixture["result"]["jobs"])

    def test_unsupported_versions_and_corrupt_identity_are_refused(self):
        for mutation in (0, 2, True, "1", "request-digest", "job-spec"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state = golden("state-v1")["current"]
                if mutation == "request-digest":
                    state["request"]["request_id"] = "different"
                elif mutation == "job-spec":
                    state["jobs"][0]["spec"]["job_id"] = "different"
                else:
                    state["schema_version"] = mutation
                write_json(root / "state.json", state)
                original = (root / "state.json").read_bytes()
                engine = Engine(root, None, 0, admission_store=NoExternalEffects())
                with self.assertRaises(records.JobError):
                    engine.load()
                self.assertEqual((root / "state.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
