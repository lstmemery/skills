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
from herdr_jobs.transport import Deadline, EffectUnknown, NativeTransport, RateLimited


class FakeHerdr:
    """Small offline stand-in for the external Herdr CLI boundary."""

    def __init__(self, state="working", output="Working", start_code=0, prompt_error=None):
        self.calls = []
        self.state = state
        self.output = output
        self.start_code = start_code
        self.prompt_error = prompt_error
        self.retry_environment = {}
        self.retry_output = None

    def command(self, argv, deadline, cwd=None):
        self.calls.append(argv)
        if argv[:3] == ["herdr", "agent", "start"]:
            return self.start_code, b'{"result":{}}', b"agent_not_ready: timed out" if self.start_code else b""
        if argv[:3] == ["herdr", "agent", "prompt"] and self.prompt_error:
            return 1, b"{}", self.prompt_error.encode()
        if argv[:3] == ["herdr", "agent", "get"]:
            if self.state is None:
                return 1, b'{"error":{"code":"agent_not_found","message":"not detected yet"}}', b""
            agent = {"state": self.state, "pane": "moved:1", "kind": "codex", "name": "c9-fixture"}
            return 0, json.dumps(agent).encode(), b""
        if argv[:3] == ["herdr", "pane", "read"]:
            output = self.retry_output if self.retry_output is not None else self.output
            return 0, json.dumps({"result": {"output": output}}).encode(), b""
        if argv[:3] == ["herdr", "pane", "run"]:
            shell_command = argv[4]
            if shell_command.startswith("export "):
                for assignment in shlex.split(shell_command[len("export "):]):
                    key, value = assignment.split("=", 1)
                    self.retry_environment[key] = value
            else:
                format_string = shlex.split(shell_command)[1]
                marker = format_string.split("%s", 1)[0].lstrip("\\n")
                self.retry_output = (marker + self.retry_environment["PI_CODING_AGENT_DIR"] + "__"
                                     + self.retry_environment["PI_CODING_AGENT_SESSION_DIR"])
            return 0, b"", b""
        return 0, b'{"result":{}}', b""


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
                                "herdr_pane_help_sha256": digest(b"herdr pane help"),
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
        self.pane_output = "Working"
        self.adapter.raw = self.raw
        self.spec = {"job_id": "j1", "cwd": str(self.root), "kind": "codex", "model": None,
                     "writes_repository": False,
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
        if argv == ["herdr", "pane"]:
            return b"herdr pane help"
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
        if argv[:3] == ["herdr", "pane", "read"]:
            return json.dumps({"result": {"output": self.pane_output}}).encode()
        if argv[:3] == ["herdr", "pane", "split"]:
            return b'{"result":{"pane":{"pane_id":"old:1"}}}'
        if argv[:3] == ["herdr", "pane", "move"]:
            return b'{"result":{"move_result":{"pane":{"pane_id":"moved:1"},"workspace":{"workspace_id":"workspace:1"}}}}'
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

    def test_changed_pane_read_contract_stops_preflight_before_launch(self):
        self.adapter.binding["herdr_pane_help_sha256"] = "stale"
        with self.assertRaisesRegex(JobError, "CLI help contract changed"):
            self.adapter.preflight({"jobs": [self.spec]})
        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in self.calls))

    def test_status_does_not_rediscover_finished_workers_runtime_or_model(self):
        self.available = []
        self.spec["model"] = "formerly valid alias"
        self.job.update(phase="submitted", resolved_model="pinned-id")
        result = self.adapter.preflight({"jobs": [self.spec]}, existing={"jobs": [self.job]}, status_only=True)
        self.assertEqual(result["models"], {"j1": "pinned-id"})
        self.assertFalse(any(call[:2] in (["orchestrator", "doctor"], ["orchestrator", "models"]) for call in self.calls))

    def test_pi_observation_exposes_retry_after_for_provider_rate_limit(self):
        self.spec.update(kind="pi", route={"mode": "agent", "runtime": "pi"})
        self.agent.update(state="done", kind="pi")
        self.job.update(provider="zai", admission_model="glm-4.5")
        self.pane_output = 'Error: 429: {"code":"1302","message":"Rate limit reached"}\nRetry-After: 12'

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "done")
        self.assertEqual(observation["rate_limit"],
                         {"retry_after_seconds": 12.0, "source": "retry-after"})

    def test_transcript_mentions_of_rate_limit_are_not_provider_errors(self):
        self.spec.update(kind="pi", route={"mode": "agent", "runtime": "pi"})
        self.agent.update(state="done", kind="pi")
        self.pane_output = "The report discusses HTTP 429 and rate limit backoff."

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "done")
        self.assertNotIn("rate_limit", observation)

    def test_prompt_command_rate_limit_is_a_known_recoverable_result(self):
        fake = FakeHerdr(prompt_error="HTTP/1.1 429 Too Many Requests\nRetry-After: 20")
        with patch("herdr_jobs.transport.command", fake.command):
            with self.assertRaises(RateLimited) as caught:
                self.adapter.effect("prompt", self.job, "Assigned work")

        self.assertEqual(caught.exception.signal,
                         {"retry_after_seconds": 20.0, "source": "retry-after"})

    def test_pane_ids_come_from_response_and_prompt_remains_one_argument(self):
        self.assertEqual(self.adapter.effect("split", self.job, ""), {"pane_id": "old:1"})
        moved = self.adapter.effect("move", self.job, "")
        self.assertEqual(moved, {"pane_id": "moved:1", "workspace_id": "workspace:1"})
        prompt = "Literal 'quotes' and $(echo nope)\nNext line"
        fake = FakeHerdr()
        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("prompt", self.job, prompt)
        self.assertEqual(fake.calls[-1], ["herdr", "agent", "prompt", "c9-fixture", prompt])

    def test_codex_start_opens_a_fresh_session_with_prompt_as_native_argv(self):
        fake = FakeHerdr(start_code=1)
        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)
        prompt = "Review this exact work request."
        self.job["resolved_model"] = "native-id"
        with patch("herdr_jobs.transport.command", fake.command):
            result = self.adapter.effect("start", self.job, prompt)

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(result["prompt_submitted"], True)
        self.assertEqual(start, ["herdr", "agent", "start", "c9-fixture", "--kind", "codex",
                                 "--pane", "moved:1", "--timeout", "5000", "--", "-C", str(self.root),
                                 "-c", f'projects."{self.root}".trust_level="trusted"', "-m", "native-id", prompt])

    def test_pi_zai_start_passes_provider_model_and_effort_to_the_agent(self):
        fake = FakeHerdr()
        self.spec.update(kind="pi", provider="zai", route={"mode": "agent", "runtime": "pi"},
                         override={"effort": "high"})
        self.job["resolved_model"] = "glm-5.3-flash"

        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start, ["herdr", "agent", "start", "c9-fixture", "--kind", "pi",
                                 "--pane", "moved:1", "--", "--provider", "zai", "--model",
                                 "glm-5.3-flash", "--thinking", "high"])

    def test_claude_effort_maps_without_an_explicit_model(self):
        fake = FakeHerdr()
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"},
                         override={"effort": "xhigh"})

        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start[-3:], ["--", "--effort", "xhigh"])

    def test_omp_effort_maps_to_thinking_without_an_explicit_model(self):
        fake = FakeHerdr()
        self.spec.update(kind="omp", route={"mode": "agent", "runtime": "omp"},
                         override={"effort": "max"})

        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start[-3:], ["--", "--thinking", "max"])

    def test_codex_effort_is_scoped_to_the_prompt_bearing_start(self):
        fake = FakeHerdr(start_code=1)
        self.spec["override"] = {"effort": "max"}
        self.job["resolved_model"] = "native-id"
        self.adapter.observe = lambda job: {"state": "working", "identity_verified": True,
                                            "prompt_verified": True}

        with patch("herdr_jobs.transport.command", fake.command):
            result = self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(result["prompt_submitted"], True)
        self.assertEqual(start[-5:], ["-m", "native-id", "-c", "model_reasoning_effort=max", "Assigned work"])

    def test_non_codex_startup_dialog_is_reported_as_blocked(self):
        self.spec.update(kind="pi", route={"mode": "agent", "runtime": "pi"})
        self.agent.update(state="working", kind="pi")
        self.pane_output = "Trust and continue to access this project"

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "blocked")
        self.assertFalse(observation["identity_verified"])
        self.assertIn("dialog", observation["launch_issue"])

    def test_finish_closes_only_the_recorded_workspace_id(self):
        self.job["workspace_id"] = "workspace:1"
        calls = []

        def fake_command(argv, deadline, cwd=None):
            calls.append(argv)
            return 0, b"closed", b""

        with patch("herdr_jobs.transport.command", fake_command):
            result = self.adapter.close_workspace(self.job)

        self.assertEqual(result, {"closed": True, "workspace_id": "workspace:1"})
        self.assertEqual(calls, [["herdr", "workspace", "close", "workspace:1"]])

    def test_finish_reconciliation_checks_the_exact_workspace_id(self):
        self.job["workspace_id"] = "workspace:1"
        calls = []

        def workspace_list(argv):
            calls.append(argv)
            return json.dumps({"result": {"type": "workspace_list", "workspaces": [
                {"workspace_id": "workspace:other"}, {"workspace_id": "workspace:1"}
            ]}}).encode()

        self.adapter.raw = workspace_list

        self.assertTrue(self.adapter.workspace_is_open(self.job))
        self.assertEqual(calls, [["herdr", "workspace", "list"]])

        self.adapter.raw = lambda argv: json.dumps({"result": {"type": "workspace_list", "workspaces": [
            {"workspace_id": "workspace:other"}
        ]}}).encode()
        self.assertFalse(self.adapter.workspace_is_open(self.job))

    def test_workspace_list_rejects_an_unexpected_response_shape(self):
        self.job["workspace_id"] = "workspace:1"
        responses = (
            {"result": {"workspaces": [{"workspace_id": "workspace:1"}]}},
            {"result": {"type": "workspace_list_v2", "workspaces": [{"workspace_id": "workspace:1"}]}},
            {"result": {"type": "workspace_list", "workspace_list": [{"workspace_id": "workspace:1"}]}},
        )
        for response in responses:
            with self.subTest(response=response):
                self.adapter.raw = lambda _argv, response=response: json.dumps(response).encode()
                with self.assertRaisesRegex(JobError, "workspace list returned an unexpected response shape"):
                    self.adapter.workspace_is_open(self.job)

    def test_jail_exit_record_verifies_completion_without_prompt_marker(self):
        self.spec["route"]["mode"] = "jail"
        save(self.job["exit_record"], {"schema_version": 1, "job_id": self.spec["job_id"],
                                        "attempt_id": self.job["attempt_id"], "exit_code": 0,
                                        "exited_at": "2026-10-04T00:00:00+00:00"})

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation, {"state": "exited", "identity_verified": True, "exit_code": 0})
        self.assertEqual(self.calls, [])

    def test_codex_retry_override_is_scoped_to_the_fresh_launch_arguments(self):
        fake = FakeHerdr(start_code=1)
        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)
        self.job["retry_override"] = {
            "provider_id": "vendor",
            "request_max_retries": 8,
            "stream_max_retries": 10,
        }
        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start[-5:], ["-c", "model_providers.vendor.request_max_retries=8",
                                      "-c", "model_providers.vendor.stream_max_retries=10",
                                      "Assigned work"])

    def test_pi_retry_profile_environment_is_set_on_only_the_owned_pane(self):
        self.job["retry_profile"] = {
            "agent_dir": str(self.root / "run" / "pi-agent"),
            "session_dir": str(self.root / "run" / "pi-sessions"),
            "settings_sha256": "0" * 64,
        }
        fake = FakeHerdr()
        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)

        with patch("herdr_jobs.transport.command", fake.command):
            result = self.adapter.effect("retry_setup", self.job, "")

        self.assertEqual(result["retry_environment_verified"], True)
        self.assertEqual(fake.retry_environment, {
            "PI_CODING_AGENT_DIR": str(self.root / "run" / "pi-agent"),
            "PI_CODING_AGENT_SESSION_DIR": str(self.root / "run" / "pi-sessions"),
        })
        self.assertEqual(sum(call[:3] == ["herdr", "pane", "run"] for call in fake.calls), 2)

    def test_codex_retry_override_rejects_the_builtin_provider_before_launch(self):
        config_home = self.root / "codex-home"
        config_home.mkdir()
        (config_home / "config.toml").write_text('model_provider = "openai"\n')
        request = {"jobs": [self.spec], "retry_override": {
            "codex": {"provider_id": "openai", "request_max_retries": 8}}}

        with patch.dict("os.environ", {"CODEX_HOME": str(config_home)}):
            with self.assertRaisesRegex(JobError, "built-in provider"):
                self.adapter.preflight(request)

        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in self.calls))

    def test_codex_retry_override_accepts_only_the_already_selected_custom_provider(self):
        config_home = self.root / "codex-home"
        config_home.mkdir()
        (config_home / "config.toml").write_text(
            'model_provider = "vendor"\n[model_providers.vendor]\nname = "Fixture"\n'
        )
        request = {"jobs": [self.spec], "retry_override": {
            "codex": {"provider_id": "vendor", "request_max_retries": 8}}}

        with patch.dict("os.environ", {"CODEX_HOME": str(config_home)}):
            capabilities = self.adapter.preflight(request)

        self.assertEqual(capabilities["models"], {})

    def test_pi_project_retry_settings_block_a_run_override_before_launch(self):
        project = self.root / ".pi"
        project.mkdir()
        (project / "settings.json").write_text('{"retry": { /* project wins */ "maxRetries": 4, }}')
        self.spec["route"]["runtime"] = "pi"
        self.spec["kind"] = "pi"
        self.adapter.binding["supported_kinds"].append("pi")
        self.available.append("pi")
        request = {"jobs": [self.spec], "retry_override": {"pi": {"max_retries": 10}}}

        with self.assertRaisesRegex(JobError, "project pi settings override run retry values"):
            self.adapter.preflight(request)

        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in self.calls))

    def test_trust_and_resume_dialogs_fail_launch_without_answering_the_dialog(self):
        cases = [
            ("Do you trust the contents of this folder?", "trust dialog"),
            ("Resume session or use session directory", "resume/session-selection dialog"),
        ]
        for output, expected in cases:
            with self.subTest(output=output):
                fake = FakeHerdr(state=None, output=output, start_code=1)
                self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)
                with patch("herdr_jobs.transport.command", fake.command):
                    result = self.adapter.effect("start", self.job, "Assigned work")

                self.assertTrue(result["launch_blocked"])
                self.assertIn(expected, result["issue"])
                self.assertFalse(any(call[:2] == ["herdr", "agent"] and call[2] == "prompt" for call in fake.calls))
                self.assertFalse(any(call[:2] == ["herdr", "agent"] and call[2] == "send-keys" for call in fake.calls))
                self.assertFalse(any(call[:3] == ["herdr", "agent", "get"] for call in fake.calls))

    def test_working_state_without_visible_working_text_does_not_prove_prompt_delivery(self):
        fake = FakeHerdr(state="working", output="Codex session is starting")
        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)
        with patch("herdr_jobs.transport.command", fake.command):
            with self.assertRaises(EffectUnknown):
                self.adapter.effect("start", self.job, "Assigned work")

        self.assertEqual(sum(call[:3] == ["herdr", "agent", "start"] for call in fake.calls), 1)
        self.assertTrue(any(call[:3] == ["herdr", "pane", "read"] for call in fake.calls))
        self.assertFalse(any(call[:3] == ["herdr", "agent", "prompt"] for call in fake.calls))

    def test_echoed_prompt_working_text_does_not_prove_an_active_turn(self):
        self.spec["task"] = "Print this word on its own line:\nWorking"
        self.job["phase"] = "submitted"
        self.agent["state"] = "working"
        self.pane_output = self.spec["task"]

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "working")
        self.assertFalse(observation["prompt_verified"])

    def test_unrelated_working_prose_does_not_prove_an_active_turn(self):
        self.job["phase"] = "submitted"
        self.agent["state"] = "working"
        self.pane_output = "The report says the workflow is working as intended."

        observation = self.adapter.observe(self.job)

        self.assertFalse(observation["prompt_verified"])

    def test_active_turn_status_line_after_unrelated_prose_proves_prompt_landing(self):
        self.job["phase"] = "submitted"
        self.agent["state"] = "working"
        self.pane_output = "The report says the workflow is working as intended.\n✳ Working…"

        observation = self.adapter.observe(self.job)

        self.assertTrue(observation["prompt_verified"])

    def test_active_status_after_an_echoed_working_prompt_proves_prompt_landing(self):
        self.spec["task"] = "Print this word on its own line:\nWorking"
        self.job["phase"] = "submitted"
        self.agent["state"] = "working"
        self.pane_output = self.spec["task"] + "\n✳ Working…"

        observation = self.adapter.observe(self.job)

        self.assertTrue(observation["prompt_verified"])

    def test_echoed_authentication_phrase_does_not_block_a_working_worker(self):
        self.spec["task"] = "Explain the phrase authentication required in the log."
        self.job["phase"] = "submitted"
        self.agent["state"] = "working"
        self.pane_output = self.spec["task"]

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "working")
        self.assertTrue(observation["identity_verified"])

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
