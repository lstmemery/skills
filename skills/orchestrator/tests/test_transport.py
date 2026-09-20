import importlib.util
import json
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.records import JobError, digest, load_json, save
from herdr_jobs.transport import Deadline, EffectUnknown, NativeTransport


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.adapter = object.__new__(NativeTransport)
        self.adapter.herdr = "herdr"
        self.adapter.orchestrator = "orchestrator"
        self.adapter.deadline = Deadline(2)
        self.adapter.binding = {"herdr_help_sha256": digest(b"herdr help"),
                                "herdr_agent_help_sha256": digest(b"herdr agent help"),
                                "orchestrator_help_sha256": digest(b"orchestrator help"),
                                "supported_kinds": ["codex", "omp"], "server_version": "fixture-v1",
                                "paths": {"session_id": ["session"], "server_version": ["version"],
                                          "agent_state": ["state"], "agent_pane_id": ["pane"],
                                          "agent_kind": ["kind"], "agent_name": ["name"]},
                                "jail_export": None}
        self.calls = []
        self.available = ["codex", "omp"]
        self.agent = {"state": "done", "pane": "moved:1", "kind": "codex", "name": "c9-fixture"}
        self.catalog = {"models": [{"id": "native-id", "displayName": "Human label"}]}
        self.adapter.raw = self.raw
        self.spec = {"job_id": "j1", "cwd": str(self.root), "kind": "codex", "model": None,
                     "route": {"mode": "agent", "runtime": "codex"}}
        self.job = {"spec": self.spec, "pane_id": "moved:1", "agent_name": "c9-fixture",
                    "attempt_id": "attempt1", "resolved_model": None,
                    "exit_record": str(self.root / "lifecycle" / "exit.json")}

    def raw(self, argv):
        self.calls.append(argv)
        if argv == ["herdr", "--help"]:
            return b"herdr help"
        if argv == ["herdr", "agent"]:
            return b"herdr agent help"
        if argv[:2] == ["orchestrator", "help"]:
            return b"orchestrator help"
        if argv == ["herdr", "status"]:
            return b'{"session":"fixture-server-session","version":"fixture-v1"}'
        if argv[:2] == ["orchestrator", "doctor"]:
            return json.dumps({"runtimeSummary": {"availableIds": self.available}}).encode()
        if argv[:2] == ["orchestrator", "models"]:
            return json.dumps(self.catalog).encode()
        if argv[:3] == ["herdr", "agent", "get"]:
            return json.dumps(self.agent).encode()
        if argv[:3] == ["herdr", "pane", "split"]:
            return b'{"result":{"pane":{"pane_id":"old:1"}}}'
        if argv[:3] == ["herdr", "pane", "move"]:
            return b'{"result":{"move_result":{"pane":{"pane_id":"moved:1"}}}}'
        return b'{"result":{}}'

    def test_exact_runtime_is_not_substituted(self):
        self.spec["route"]["runtime"] = "omp"
        self.spec["kind"] = "omp"
        self.available = ["codex", "pi"]
        with self.assertRaisesRegex(JobError, "exact runtime unavailable: omp"):
            self.adapter.preflight({"jobs": [self.spec]})
        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in self.calls))

    def test_missing_herdr_kind_is_caught_before_launch(self):
        self.spec["kind"] = "missing-kind"
        with self.assertRaisesRegex(JobError, "Herdr kind"):
            self.adapter.preflight({"jobs": [self.spec]})

    def test_model_label_resolves_to_native_id_and_unavailable_model_stops(self):
        self.spec["model"] = "Human label"
        self.assertEqual(self.adapter.preflight({"jobs": [self.spec]})["models"], {"j1": "native-id"})
        self.spec["model"] = "not-installed"
        with self.assertRaisesRegex(JobError, "exact model unavailable"):
            self.adapter.preflight({"jobs": [self.spec]})

    def test_jail_export_is_required(self):
        self.spec["route"]["mode"] = "jail"
        with self.assertRaisesRegex(JobError, "export has not been bound"):
            self.adapter.preflight({"jobs": [self.spec]})

    def test_changed_command_contract_stops_preflight(self):
        self.adapter.binding["herdr_help_sha256"] = "stale"
        with self.assertRaisesRegex(JobError, "CLI help contract changed"):
            self.adapter.preflight({"jobs": [self.spec]})

    def test_status_does_not_rediscover_finished_workers_runtime_or_model(self):
        self.available = []
        self.spec["model"] = "formerly valid alias"
        self.job.update(phase="submitted", resolved_model="pinned-id")
        result = self.adapter.preflight({"jobs": [self.spec]}, existing={"jobs": [self.job]}, status_only=True)
        self.assertEqual(result["models"], {"j1": "pinned-id"})
        self.assertFalse(any(call[:2] in (["orchestrator", "doctor"], ["orchestrator", "models"]) for call in self.calls))

    def test_pane_ids_come_from_response_and_prompt_remains_one_argument(self):
        self.assertEqual(self.adapter.effect("split", self.job, ""), {"pane_id": "old:1"})
        self.assertEqual(self.adapter.effect("move", self.job, ""), {"pane_id": "moved:1"})
        prompt = "Literal 'quotes' and $(echo nope)\nNext line"
        self.adapter.effect("prompt", self.job, prompt)
        self.assertEqual(self.calls[-1], ["herdr", "agent", "prompt", "c9-fixture", prompt])

    def test_unverified_mutation_response_stays_ambiguous(self):
        self.adapter.raw = lambda argv: b'{}'
        with self.assertRaises(EffectUnknown):
            self.adapter.effect("prompt", self.job, "task")

    def test_wrong_agent_identity_is_not_accepted(self):
        self.agent["name"] = "another-agent"
        with self.assertRaisesRegex(JobError, "name differs"):
            self.adapter.observe(self.job)

    def test_jail_launch_uses_quoted_runner_and_keeps_task_in_request_file(self):
        self.spec["route"]["mode"] = "jail"
        prompt = "Task with $(touch never) and 'quotes'"
        self.adapter.effect("jail", self.job, prompt)
        argv = self.calls[-1]
        self.assertEqual(argv[:4], ["herdr", "pane", "run", "moved:1"])
        runner_argv = shlex.split(argv[4])
        self.assertEqual(runner_argv[2], "--request")
        request = load_json(runner_argv[3])
        self.assertEqual(request["argv"], ["omp-train", "--harness", "codex", "exec", "--skip-git-repo-check", prompt])
        self.assertNotIn(prompt, argv[4])

    def test_verified_exit_record_survives_absent_agent(self):
        self.spec["route"]["mode"] = "jail"
        save(self.job["exit_record"], {"schema_version": 1, "job_id": "j1", "attempt_id": "attempt1", "exit_code": 0, "exited_at": "fixture"})
        result = self.adapter.observe(self.job)
        self.assertEqual(result, {"state": "exited", "identity_verified": True, "exit_code": 0})
        self.assertEqual(self.calls, [])
        record = load_json(self.job["exit_record"])
        record["attempt_id"] = "wrong"
        save(self.job["exit_record"], record)
        with self.assertRaisesRegex(JobError, "does not match"):
            self.adapter.observe(self.job)

    def test_runner_records_real_return_value_without_shell_execution(self):
        module_spec = importlib.util.spec_from_file_location("jail_runner", PACKAGE / "scripts/jail-runner.py")
        runner = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(runner)
        request = {"schema_version": 1, "job_id": "j1", "attempt_id": "attempt1",
                   "argv": ["omp-train", "--harness", "codex", "exec", "--skip-git-repo-check", "literal task"],
                   "exit_path": self.job["exit_record"]}
        calls = []

        def launch(argv, check):
            calls.append((argv, check))
            return SimpleNamespace(returncode=23)

        self.assertEqual(runner.execute(request, run_process=launch), 23)
        self.assertEqual(calls, [(request["argv"], False)])
        self.assertEqual(load_json(self.job["exit_record"])["exit_code"], 23)


if __name__ == "__main__":
    unittest.main()
