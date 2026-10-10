import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

retry = types.ModuleType("herdr_jobs.retry")
retry.codex_retry_cli_args = lambda value: []
retry.validate_codex_retry_provider = lambda value: None
retry.validate_pi_project_settings = lambda cwd, value: None
retry.prepare_pi_run_profile = lambda *args, **kwargs: None
worktrees = types.ModuleType("herdr_jobs.worktrees")
worktrees.verify_repository_worktree = lambda *args: None
sys.modules.setdefault("herdr_jobs.retry", retry)
sys.modules.setdefault("herdr_jobs.worktrees", worktrees)

from herdr_jobs.model_ladder import load_ladder, select
from herdr_jobs.records import JobError, prepare
from herdr_jobs.transport import Deadline, NativeTransport, _codex_config, _native_model
from herdr_jobs.records import digest


class LadderRegressionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.csv = self.root / "model-ladder.csv"
        self.csv.write_bytes((PACKAGE / "model-ladder.csv").read_bytes())
        self.policy = self.root / "policy.json"
        self.policy.write_bytes((PACKAGE / "launch-policy.json").read_bytes())
        (self.root / "task.md").write_text("task")
        self.base = {"job_id": "j", "name": "job", "task_kind": "ordinary",
                     "task_file": "task.md", "cwd": ".", "output_expectation": "answer",
                     "writes_repository": False}

    def prepare(self, **values):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "request_id": "r",
                                        "jobs": [{**self.base, **values}]}))
        return prepare(manifest, self.policy)["jobs"][0]

    def evidence(self, reason):
        return {"source": "provider", "observed_at": "2026-10-10T16:00:00Z",
                "error": {"kind": reason, "message": "provider response", "code": "fixture"}}

    def escalation(self, level):
        return {"previous_level": level - 1,
                "failure": {"kind": "failure", "source": "fixture",
                             "observed_at": "2026-10-10T15:00:00Z", "summary": "failed"},
                "expected_improvement": "finish"}

    def test_partial_pin_is_completed_from_one_tuple(self):
        job = self.prepare(override={"instruction": "pin effort", "effort": "max"})
        self.assertEqual((job["model"], job["provider"], job["route"]["runtime"],
                          job["override"]["effort"]), ("gpt-6-luna", "codex", "codex", "max"))

    def test_pi_catalog_identity_is_stripped_only_for_selected_provider(self):
        self.assertEqual(_native_model("pi", "opencode-go/mimo-v2.6-pro", "opencode-go"),
                         "mimo-v2.6-pro")
        self.assertEqual(_native_model("pi", "vendor/custom/model", "other"),
                         "vendor/custom/model")

    def test_unstructured_fallback_and_unrecorded_escalation_fail_closed(self):
        levels = load_ladder(self.csv)
        with self.assertRaises(JobError):
            select(levels, 1, {"reason": "rate_limit", "evidence": "generic timeout"})
        with self.assertRaises(JobError):
            self.prepare(model_level=8)
        self.prepare(model_level=2, escalation=self.escalation(2))

    def test_multi_level_escalation_requires_user_authorization(self):
        evidence = self.escalation(2)
        evidence["previous_level"] = 1
        with self.assertRaises(JobError):
            self.prepare(model_level=4, escalation=evidence)
        evidence["authorization"] = "user"
        self.prepare(model_level=4, escalation=evidence)


class TransportRegressionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.adapter = object.__new__(NativeTransport)
        self.adapter.herdr = "herdr"
        self.adapter.orchestrator = "orchestrator"
        self.adapter.deadline = Deadline(2)
        self.adapter.binding = {"herdr_help_sha256": digest(b""), "herdr_agent_help_sha256": digest(b""),
                                "herdr_pane_help_sha256": digest(b""), "orchestrator_help_sha256": digest(b""),
                                "supported_kinds": ["codex", "pi", "claude"],
                                "server_version": "v", "paths": {"session_id": ["session"],
                                "server_version": ["version"], "agent_state": ["state"],
                                "agent_pane_id": ["pane"], "agent_kind": ["kind"],
                                "agent_name": ["name"]}, "jail_export": None}
        self.calls = []
        self.adapter.raw = self.raw

    def raw(self, argv):
        self.calls.append(argv)
        if argv == ["herdr", "--help"]:
            return b""
        if argv == ["herdr", "agent", "--help"]:
            return b""
        if argv == ["herdr", "pane", "--help"]:
            return b""
        if argv[:2] == ["orchestrator", "help"]:
            return b""
        if argv[:2] == ["herdr", "status"]:
            return json.dumps({"session": "s", "version": "v"}).encode()
        if argv[:2] == ["orchestrator", "doctor"]:
            return json.dumps({"runtimeSummary": {"availableIds": ["codex", "pi", "claude-code"]}}).encode()
        if argv[:2] == ["orchestrator", "models"]:
            return json.dumps({"models": []}).encode()
        if argv[:3] == ["pi", "--offline", "--list-models"]:
            return b"provider model context max-out thinking images\nopencode-go mimo-v2.6-pro 1M 128K yes yes\n"
        return b""

    def job(self, runtime="codex", model=None, effort=None, provider=None):
        spec = {"job_id": "j", "cwd": str(self.root), "kind": runtime,
                "model": model, "provider": provider, "writes_repository": False,
                "route": {"mode": "agent", "runtime": runtime}}
        if effort is not None:
            spec["override"] = {"instruction": "x", "effort": effort}
        return spec

    def test_malformed_codex_provider_shapes_fail_before_effects(self):
        home = self.root / "codex"
        home.mkdir()
        (home / "config.toml").write_text("model_providers = \"bad\"")
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            with self.assertRaisesRegex(JobError, "model_providers must be an object"):
                _codex_config()
        (home / "config.toml").write_text("[model_providers.vendor]\nbase_url = 7\n")
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            with self.assertRaisesRegex(JobError, "base_url must be a string"):
                _codex_config()

    def test_pi_explicit_effort_is_probed(self):
        job = self.job("pi", "mimo-v2.6-pro", "max", "opencode-go")
        efforts = []
        self.adapter.pi_model_efforts = lambda provider, model: efforts.append((provider, model)) or ["max"]
        with patch("herdr_jobs.transport.shutil.which", return_value="/bin/tool"):
            self.adapter.preflight({"jobs": [job]})
        self.assertEqual(efforts, [("opencode-go", "mimo-v2.6-pro")])

    def test_pi_preflight_identity_becomes_unqualified_effect_argument(self):
        spec = self.job("pi", "mimo-v2.6-pro", "max", "opencode-go")
        captured = []

        def fake_command(argv, deadline, cwd=None):
            captured.append(argv)
            return 0, b'{"result":{}}', b""

        run = {"spec": spec, "pane_id": "pane:1", "agent_name": "agent",
               "resolved_model": "opencode-go/mimo-v2.6-pro"}
        with patch("herdr_jobs.transport.command", fake_command):
            self.adapter.effect("start", run, "prompt")
        self.assertIn(["--model", "mimo-v2.6-pro"],
                      [captured[0][i:i + 2] for i in range(len(captured[0]) - 1)])

    def test_claude_display_alias_and_missing_effort_metadata_are_rejected(self):
        job = self.job("claude-code", "claude-opus-5-5", "high", "claude")
        self.adapter.raw = lambda argv: json.dumps({"models": [{"id": "opus", "displayName": "claude-opus-5-5"}]}).encode() if argv[:2] == ["orchestrator", "models"] else self.raw(argv)
        with self.assertRaises(JobError):
            self.adapter.preflight({"jobs": [job]})
        self.adapter.raw = lambda argv: json.dumps({"models": [{"id": "claude-opus-5-5"}]}).encode() if argv[:2] == ["orchestrator", "models"] else self.raw(argv)
        with self.assertRaises(JobError):
            self.adapter.preflight({"jobs": [job]})

    def test_pending_resume_rediscoveries_pinned_model(self):
        job = self.job("codex", "gpt-6-luna", "low", "codex")
        existing = {"jobs": [{"spec": job, "phase": "pending", "resolved_model": "gpt-6-luna"}]}
        with self.assertRaisesRegex(JobError, "exact model unavailable"):
            self.adapter.preflight({"jobs": [job]}, existing=existing)
        self.assertIn(["orchestrator", "models", "codex", "--json", "--compact"], self.calls)

    def test_retry_provider_must_match_selected_native_provider(self):
        job = self.job("codex", "gpt-6-luna", "low", "codex")
        with self.assertRaisesRegex(JobError, "does not match the selected native provider"):
            self.adapter.preflight({"jobs": [job], "retry_override": {
                "codex": {"provider_id": "vendor", "request_max_retries": 2}}})


    def test_saved_alias_caps_use_strictest_pool_cap(self):
        worker_records = types.ModuleType("herdr_jobs.worker_records")
        worker_records.DispositionInput = object
        worker_records.WorkerIdentity = object
        worker_records.record_disposition = lambda *args, **kwargs: None
        worker_records.result_schema_markdown = lambda: ""
        worker_records.validate_result = lambda *args, **kwargs: None
        sys.modules.setdefault("herdr_jobs.worker_records", worker_records)
        from herdr_jobs.engine import Engine
        engine = Engine(self.root, None, Deadline(1))
        engine.state = {"request": {"policy": {"provider_admission": {
            "default_provider_cap": 4, "provider_caps": {"codex": 8, "openai-codex": 2}}}}}
        self.assertEqual(engine.provider_cap("codex"), 2)

    def test_jail_requires_attested_default(self):
        export = self.root / "export"
        export.mkdir()
        self.adapter.binding["jail_export"] = {"verified": True, "host_root": str(export),
                                                 "worker_root": str(self.root / "worker-root")}
        job = self.job("codex")
        job["route"] = {"mode": "jail", "runtime": "codex"}
        with patch("herdr_jobs.transport.shutil.which", return_value="/bin/tool"):
            with self.assertRaisesRegex(JobError, "jail default model/effort attestation"):
                self.adapter.preflight({"jobs": [job]})


if __name__ == "__main__":
    unittest.main()
