import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
HERDR_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "herdr"
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.records import (JobError, digest, effort_arguments, jail_context_path, load_json, prepare,
                                protected_paths_record, save, validate_required_context)
from herdr_jobs.transport import (Deadline, EffectUnknown, NativeTransport, RateLimited,
                                  orchestrator_help_contract_bytes)


def compact_orchestrator_help(cwd, extra=None):
    value = {"schemaVersion": 1, "fullHelp": {"args": ["help", "--json", "--workspace", cwd]},
             "commands": [{"name": "help"}]}
    if extra is not None:
        value["commands"].append(extra)
    return json.dumps(value, separators=(",", ":")).encode()


class FakeHerdr:
    """Small offline stand-in for the external Herdr CLI boundary."""

    def __init__(self, state="working", output="Working", start_code=0, prompt_error=None):
        self.calls = []
        self.environments = []
        self.state = state
        self.output = output
        self.start_code = start_code
        self.prompt_error = prompt_error
        self.retry_environment = {}
        self.retry_output = None
        self.start_environment = None

    def command(self, argv, deadline, cwd=None, env=None):
        self.calls.append(argv)
        self.environments.append(env)
        if argv[:3] == ["herdr", "agent", "start"]:
            self.start_environment = dict(self.retry_environment)
            return self.start_code, b'{"result":{}}', b"agent_not_ready: timed out" if self.start_code else b""
        if argv[:3] == ["herdr", "agent", "prompt"] and self.prompt_error:
            return 1, b"{}", self.prompt_error.encode()
        if argv[:3] == ["herdr", "agent", "get"]:
            if self.state is None:
                return 1, b'{"error":{"code":"agent_not_found","message":"not detected yet"}}', b""
            agent = {"agent_status": self.state, "pane_id": "moved:1",
                     "agent": "codex", "name": "c9-fixture"}
            return 0, json.dumps({"result": {"type": "agent_info", "agent": agent}}).encode(), b""
        if argv[:3] == ["herdr", "pane", "read"]:
            output = self.retry_output if self.retry_output is not None else self.output
            return 0, output.encode(), b""
        if argv[:3] == ["herdr", "pane", "run"]:
            shell_command = argv[4]
            if shell_command.startswith("exec /usr/bin/env -i"):
                return 0, b"", b""
            if shell_command.startswith("export "):
                for assignment in shlex.split(shell_command[len("export "):]):
                    key, value = assignment.split("=", 1)
                    self.retry_environment[key] = value
            else:
                format_string = shlex.split(shell_command)[1]
                marker = format_string.split("%s", 1)[0].lstrip("\\n")
                if "CLAUDE_CODE_AUTO_COMPACT_WINDOW" in shell_command:
                    self.retry_output = marker + self.retry_environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
                else:
                    self.retry_output = (marker + self.retry_environment["PI_CODING_AGENT_DIR"] + "__"
                                         + self.retry_environment["PI_CODING_AGENT_SESSION_DIR"])
            return 0, b"", b""
        return 0, b'{"result":{}}', b""


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.codex_home = self.root / "codex-home"
        environment = patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
        environment.start()
        self.addCleanup(environment.stop)
        self.jail_exports = ((self.root, PurePosixPath("/work")),)
        self.jail_exports_patch = patch("herdr_jobs.records.JAIL_CONTEXT_EXPORTS", self.jail_exports)
        self.jail_exports_patch.start()
        self.addCleanup(self.jail_exports_patch.stop)
        self.adapter = object.__new__(NativeTransport)
        self.adapter.herdr = "herdr"
        self.adapter.orchestrator = "orchestrator"
        self.adapter.deadline = Deadline(2)
        self.adapter.binding = {"herdr_help_sha256": digest(b"herdr help"),
                                "herdr_agent_help_sha256": digest(b"herdr agent help"),
                                "herdr_pane_help_sha256": digest(b"herdr pane help"),
                                "orchestrator_help_sha256": digest(b"orchestrator help"),
                                "supported_kinds": ["codex", "omp"], "server_version": "0.9.3",
                                "paths": {"session_id": ["server", "socket"],
                                          "server_version": ["server", "version"],
                                          "agent_state": ["result", "agent", "agent_status"],
                                          "agent_pane_id": ["result", "agent", "pane_id"],
                                          "agent_kind": ["result", "agent", "agent"],
                                          "agent_name": ["result", "agent", "name"]},
                                "jail_export": None,
                                "jail_default": {"verified": True, "harness": "codex",
                                                 "model": "gpt-6-astra", "effort": "max"}}
        self.calls = []
        self.available = ["codex", "omp"]
        self.agent = dict(load_json(HERDR_FIXTURES / "agent-get.json")["result"]["agent"])
        self.agent.update(agent_status="done", pane_id="moved:1", agent="codex", name="c9-fixture")
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
        if argv in (["herdr", "agent", "--help"], ["herdr", "agent"]):
            return b"herdr agent help"
        if argv in (["herdr", "pane", "--help"], ["herdr", "pane"]):
            return b"herdr pane help"
        if argv[:2] == ["orchestrator", "help"]:
            return b"orchestrator help"
        if argv[:2] == ["herdr", "status"]:
            return json.dumps(load_json(HERDR_FIXTURES / "status.json")).encode()
        if argv[:2] == ["orchestrator", "doctor"]:
            return json.dumps({"runtimeSummary": {"availableIds": self.available}}).encode()
        if argv[:2] == ["orchestrator", "models"]:
            return json.dumps(self.catalog).encode()
        if argv[:3] == ["herdr", "agent", "get"]:
            return json.dumps({"result": {"type": "agent_info", "agent": self.agent}}).encode()
        if argv[:3] == ["herdr", "pane", "read"]:
            return self.pane_output.encode()
        if argv[:3] == ["herdr", "pane", "split"]:
            return b'{"result":{"pane":{"pane_id":"old:1"}}}'
        if argv[:3] == ["herdr", "pane", "move"]:
            return b'{"result":{"move_result":{"pane":{"pane_id":"moved:1"},"created_workspace":{"workspace_id":"workspace:1"}}}}'
        return b'{"result":{}}'

    def prepared_jail_spec(self, task_class):
        (self.root / "task.md").write_text("A task for the jailed worker.")
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        job = {"job_id": "j1", "name": "fixture", "task_kind": "deep_research",
               "task_file": "task.md", "cwd": ".", "output_expectation": "answer",
               "writes_repository": False,
               "required_context": [
                   {"path": "acceptance_test.md", "kind": "test", "purpose": "Acceptance test."},
                   {"path": "fixture-input.json", "kind": "fixture", "purpose": "Input fixture."},
               ]}
        if task_class is not None:
            job["task_class"] = task_class
        manifest_path = self.root / "manifest.json"
        save(manifest_path, {"schema_version": 1, "request_id": "fixture", "jobs": [job]})
        request = prepare(manifest_path, PACKAGE / "launch-policy.json")
        return request["jobs"][0]

    def test_jail_requires_visible_test_and_fixture_context(self):
        (self.root / "task.md").write_text("A task for the jailed worker.")
        manifest_path = self.root / "manifest.json"
        save(manifest_path, {"schema_version": 1, "request_id": "fixture", "jobs": [{
            "job_id": "j1", "name": "fixture", "task_kind": "deep_research",
            "task_file": "task.md", "cwd": ".", "output_expectation": "answer",
            "writes_repository": False,
        }]})

        with self.assertRaisesRegex(JobError, "required_context"):
            prepare(manifest_path, PACKAGE / "launch-policy.json")

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

    def test_jail_preflight_resolves_installed_codex_wrapper(self):
        spec = self.prepared_jail_spec(None)
        self.adapter.binding["jail_export"] = {"verified": True, "host_root": str(self.root),
                                                "worker_root": "/work"}
        lookups = []

        def installed(command):
            lookups.append(command)
            return "/home/deadhand/.local/bin/codex-jail-agent" if command == "codex-jail-agent" else None

        with patch("herdr_jobs.transport.shutil.which", side_effect=installed):
            self.adapter.preflight({"jobs": [spec]})

        self.assertEqual(lookups, ["codex-jail-agent"])

    def test_changed_command_contract_stops_preflight(self):
        self.adapter.binding["herdr_help_sha256"] = "stale"
        with self.assertRaisesRegex(JobError, "CLI help contract changed"):
            self.adapter.preflight({"jobs": [self.spec]})

    def test_changed_pane_read_contract_stops_preflight_before_launch(self):
        self.adapter.binding["herdr_pane_help_sha256"] = "stale"
        with self.assertRaisesRegex(JobError, "CLI help contract changed"):
            self.adapter.preflight({"jobs": [self.spec]})
        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in self.calls))

    def test_orchestrator_help_cwd_does_not_change_preflight_digest(self):
        payloads = [compact_orchestrator_help(path) for path in
                    ("/home/deadhand", "/home/deadhand/agents", "/repo/skills/orchestrator/scripts")]
        self.adapter.binding["orchestrator_help_sha256"] = digest(orchestrator_help_contract_bytes(payloads[0]))
        for payload in payloads:
            with self.subTest(payload=payload):
                self.adapter.raw = lambda argv, payload=payload: payload if argv[:2] == ["orchestrator", "help"] else self.raw(argv)
                self.adapter.preflight({"jobs": [self.spec]})

    def test_real_orchestrator_help_change_still_stops_preflight(self):
        baseline = compact_orchestrator_help("/home/deadhand")
        changed = compact_orchestrator_help("/different/cwd", {"name": "new-command"})
        self.adapter.binding["orchestrator_help_sha256"] = digest(baseline)
        self.adapter.raw = lambda argv: changed if argv[:2] == ["orchestrator", "help"] else self.raw(argv)
        with self.assertRaisesRegex(JobError, "CLI help contract changed"):
            self.adapter.preflight({"jobs": [self.spec]})

    def test_preflight_uses_installed_help_flags_json_status_and_endpoint_identity(self):
        status = load_json(HERDR_FIXTURES / "status.json")
        self.adapter.binding["paths"].update(
            session_id=["server", "socket"], server_version=["server", "version"])
        self.adapter.binding["server_version"] = status["server"]["version"]

        def fixture_raw(argv):
            self.calls.append(argv)
            if argv == ["herdr", "--help"]:
                return b"herdr help"
            if argv in (["herdr", "agent", "--help"], ["herdr", "agent"]):
                return b"herdr agent help"
            if argv in (["herdr", "pane", "--help"], ["herdr", "pane"]):
                return b"herdr pane help"
            if argv[:2] == ["orchestrator", "help"]:
                return b"orchestrator help"
            if argv[:2] == ["herdr", "status"]:
                return json.dumps(status).encode()
            if argv[:2] == ["orchestrator", "doctor"]:
                return json.dumps({"runtimeSummary": {"availableIds": self.available}}).encode()
            return b"{}"

        self.adapter.raw = fixture_raw

        result = self.adapter.preflight({"jobs": [self.spec]})

        self.assertEqual(result["session_id"], "<redacted>")
        self.assertIn(["herdr", "agent", "--help"], self.calls)
        self.assertIn(["herdr", "pane", "--help"], self.calls)
        self.assertIn(["herdr", "status", "--json"], self.calls)

    def test_local_lane_preflight_checks_resources_and_exact_pi_model(self):
        self.adapter.binding["supported_kinds"].append("pi")
        self.available.append("pi")
        spec = {"job_id": "local", "cwd": str(self.root), "kind": "pi",
                "task_kind": "ordinary", "writes_repository": False, "local_lane": True,
                "route": {"mode": "agent", "runtime": "pi"},
                "provider": "llama-local", "model": "qwen3.8-27b-uncensored",
                "override": {"effort": "medium"},
                "model_route": {"lane": "local_lane", "effort": "medium"}}
        config = {"service_unit": "llama-server.service", "minimum_free_gpu_memory_mib": 2048}
        request = {"jobs": [spec], "policy": {"local_lane": config}}
        original_raw = self.adapter.raw
        self.adapter.raw = lambda argv: (
            b"provider model context max-out thinking images\n"
            b"llama-local qwen3.8-27b-uncensored 90.1K 16.4K yes no\n"
            if argv == ["pi", "--offline", "--list-models"] else original_raw(argv)
        )
        preconditions = []

        with patch("herdr_jobs.transport.check_local_lane_preconditions",
                   side_effect=lambda value, run: preconditions.append((value, run))), \
                patch.object(self.adapter, "pi_model_efforts", side_effect=AssertionError("effort adapter not used")):
            result = self.adapter.preflight(request)

        self.assertEqual(preconditions[0][0], config)
        self.assertEqual(result["models"], {"local": "llama-local/qwen3.8-27b-uncensored"})

    def test_observation_reads_raw_pane_text_and_installed_agent_info_envelope(self):
        response = load_json(HERDR_FIXTURES / "agent-get.json")
        response["result"]["agent"].update(
            agent="codex", agent_status="working", pane_id=self.job["pane_id"],
            name=self.job["agent_name"])
        paths = self.adapter.binding["paths"]
        paths.update(agent_state=["result", "agent", "agent_status"],
                     agent_pane_id=["result", "agent", "pane_id"],
                     agent_kind=["result", "agent", "agent"],
                     agent_name=["result", "agent", "name"])
        self.job["phase"] = "submitted"

        def fixture_raw(argv):
            self.calls.append(argv)
            if argv[:3] == ["herdr", "pane", "read"]:
                return b"Working"
            if argv[:3] == ["herdr", "agent", "get"]:
                return json.dumps(response).encode()
            return b"{}"

        self.adapter.raw = fixture_raw

        observed = self.adapter.observe(self.job)

        self.assertEqual(observed["state"], "working")
        self.assertTrue(observed["identity_verified"])
        self.assertTrue(observed["prompt_verified"])

    def test_status_does_not_rediscover_finished_workers_runtime_or_model(self):
        self.available = []
        self.spec["model"] = "formerly valid alias"
        self.job.update(phase="submitted", resolved_model="pinned-id")
        result = self.adapter.preflight({"jobs": [self.spec]}, existing={"jobs": [self.job]}, status_only=True)
        self.assertEqual(result["models"], {"j1": "pinned-id"})
        self.assertFalse(any(call[:2] in (["orchestrator", "doctor"], ["orchestrator", "models"]) for call in self.calls))

    def test_pi_observation_exposes_retry_after_for_provider_rate_limit(self):
        self.spec.update(kind="pi", route={"mode": "agent", "runtime": "pi"})
        self.agent.update(agent_status="done", agent="pi")
        self.job.update(provider="codex", admission_model="gpt-6-luna")
        self.pane_output = 'Error: 429: {"code":"1302","message":"Rate limit reached"}\nRetry-After: 12'

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "done")
        self.assertEqual(observation["rate_limit"],
                         {"retry_after_seconds": 12.0, "source": "retry-after"})

    def test_codex_usage_limit_pane_exposes_the_local_reset_to_the_lifecycle(self):
        self.pane_output = (HERDR_FIXTURES / "codex-usage-limit.txt").read_text()
        with patch.dict(os.environ, {"TZ": "America/Vancouver"}), patch(
                "herdr_jobs.admission.time.time", return_value=1792894669.0):
            observation = self.adapter.observe(self.job)

        self.assertTrue(observation["identity_verified"])
        self.assertEqual(observation["rate_limit"],
                         {"retry_after_seconds": 3131.0, "source": "reset"})

    def test_transcript_mentions_of_rate_limit_are_not_provider_errors(self):
        self.spec.update(kind="pi", route={"mode": "agent", "runtime": "pi"})
        self.agent.update(agent_status="done", agent="pi")
        self.pane_output = "The report discusses HTTP 429 and rate limit backoff."

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "done")
        self.assertNotIn("rate_limit", observation)

    def test_prompt_command_rate_limit_is_a_known_recoverable_result(self):
        fake = FakeHerdr(prompt_error="HTTP/1.1 429 Too Many Requests\nRetry-After: 20")
        self.agent.update(agent_status="idle", pane_id="moved:1", agent="codex", name="c9-fixture")
        self.job["pending_effect"] = {"action": "prompt"}
        self.adapter.raw = lambda argv: (
            json.dumps({"result": {"type": "agent_info", "agent": self.agent}}).encode()
            if argv[:3] == ["herdr", "agent", "get"] else self.pane_output.encode()
        )
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
        states = iter(["idle", "working"])
        self.agent.update(agent_status="idle", pane_id="moved:1", agent="codex", name="c9-fixture")
        self.job["pending_effect"] = {"action": "prompt"}
        self.pane_output = "✳ Working…"

        def observation_raw(argv):
            if argv[:3] == ["herdr", "agent", "get"]:
                self.agent["agent_status"] = next(states)
                return json.dumps({"result": {"type": "agent_info", "agent": self.agent}}).encode()
            if argv[:3] == ["herdr", "pane", "read"]:
                return self.pane_output.encode()
            return b"{}"

        self.adapter.raw = observation_raw
        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("prompt", self.job, prompt)
        self.assertEqual(fake.calls[-1], ["herdr", "agent", "prompt", "c9-fixture", prompt])

    def test_selectable_pane_text_transport_uses_submit_and_verify(self):
        fake = FakeHerdr()
        states = iter(["idle", "working"])
        self.agent.update(agent_status="idle", pane_id="moved:1", agent="codex", name="c9-fixture")
        self.spec["prompt_transport"] = "pane_text"
        self.job["pending_effect"] = {"action": "prompt"}
        self.pane_output = "✳ Working…"

        def observation_raw(argv):
            if argv[:3] == ["herdr", "agent", "get"]:
                self.agent["agent_status"] = next(states)
                return json.dumps({"result": {"type": "agent_info", "agent": self.agent}}).encode()
            if argv[:3] == ["herdr", "pane", "read"]:
                return self.pane_output.encode()
            return b"{}"

        self.adapter.raw = observation_raw
        prompt = "literal $(text)"
        with patch("herdr_jobs.transport.command", fake.command):
            result = self.adapter.effect("prompt", self.job, prompt)

        self.assertEqual(result, {"prompt_submitted": True})
        self.assertEqual(fake.calls, [
            ["herdr", "pane", "send-text", "moved:1", prompt],
            ["herdr", "pane", "send-keys", "moved:1", "enter"],
        ])

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
                                 "-m", "native-id", prompt])

    def test_local_lane_start_uses_pi_with_configured_mcp_and_extensions(self):
        fake = FakeHerdr()
        self.spec.update(kind="pi", provider="llama-local", route={"mode": "agent", "runtime": "pi"},
                         local_lane=True, override={"effort": "medium"})
        self.job["resolved_model"] = "qwen3.8-27b-uncensored"

        source_env = {
            "HOME": str(self.root), "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TERM": "xterm-256color", "LANG": "C.UTF-8",
            "HERDR_ENV": "1", "HERDR_PANE_ID": "coordinator:1",
            "HERDR_BIN_PATH": "/usr/local/bin/herdr", "HERDR_SOCKET_PATH": "/run/user/1000/herdr.sock",
            "PI_CODING_AGENT_DIR": str(self.root / ".pi" / "agent"),
            "API_TOKEN": "fake-secret-token",
        }
        with patch.dict(os.environ, source_env, clear=True), \
                patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start, ["herdr", "agent", "start", "c9-fixture", "--kind", "pi",
                                 "--pane", "moved:1", "--", "--provider", "llama-local", "--model",
                                 "qwen3.8-27b-uncensored", "--approve"])
        self.assertNotIn("--no-mcp", start)
        self.assertNotIn("--no-extensions", start)
        start_env = fake.environments[fake.calls.index(start)]
        self.assertEqual(set(start_env), {
            "HOME", "PATH", "TERM", "LANG", "HERDR_ENV", "HERDR_PANE_ID",
            "HERDR_BIN_PATH", "HERDR_SOCKET_PATH", "PI_CODING_AGENT_DIR",
        })
        self.assertEqual(start_env["HERDR_PANE_ID"], "moved:1")
        self.assertEqual(start_env["PI_CODING_AGENT_DIR"], str(self.root / ".pi" / "agent"))
        self.assertNotIn("API_TOKEN", start_env)

    def test_managed_local_lane_move_resets_pane_with_env_i_allowlist(self):
        self.spec.update(kind="pi", provider="llama-local", local_lane=True,
                         route={"mode": "agent", "runtime": "pi"})
        source_env = {
            "HOME": str(self.root), "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TERM": "xterm-256color", "LANG": "C.UTF-8",
            "HERDR_ENV": "1", "HERDR_BIN_PATH": "/usr/local/bin/herdr",
            "HERDR_SOCKET_PATH": "/run/user/1000/herdr.sock",
            "PI_CODING_AGENT_DIR": str(self.root / ".pi" / "agent"),
            "OPENAI_API_KEY": "fake-secret-key", "AWS_SECRET_ACCESS_KEY": "fake-secret-key",
        }
        fake = FakeHerdr()

        with patch.dict(os.environ, source_env, clear=True), \
                patch("herdr_jobs.transport.command", fake.command):
            result = self.adapter.effect("move", self.job, "")

        self.assertEqual(result["pane_id"], "moved:1")
        self.assertEqual([call[1:3] for call in fake.calls], [["pane", "run"], ["pane", "wait-output"]])
        self.assertIn("env -i", fake.calls[0][4])
        for env in fake.environments:
            self.assertEqual(set(env), {
                "HOME", "PATH", "TERM", "LANG", "HERDR_ENV", "HERDR_PANE_ID",
                "HERDR_BIN_PATH", "HERDR_SOCKET_PATH", "PI_CODING_AGENT_DIR",
            })
            self.assertEqual(env["HERDR_PANE_ID"], "moved:1")
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)

    def test_local_lane_refuses_secret_material_in_the_prompt_before_start(self):
        fake = FakeHerdr()
        self.spec.update(kind="pi", provider="llama-local", route={"mode": "agent", "runtime": "pi"},
                         local_lane=True, override={"effort": "medium"})
        self.job["resolved_model"] = "qwen3.8-27b-uncensored"

        prompts = (
            "Use API_TOKEN=secret-value for authentication",
            "-----BEGIN " + "PRIVATE KEY-----\nfixture-key\n-----END PRIVATE KEY-----",
        )
        with patch("herdr_jobs.transport.command", fake.command):
            for prompt in prompts:
                with self.subTest(prompt=prompt[:20]), self.assertRaisesRegex(JobError, "secret-like material"):
                    self.adapter.effect("start", self.job, prompt)

        self.assertEqual(fake.calls, [])

    def test_claude_effort_maps_without_an_explicit_model(self):
        fake = FakeHerdr()
        self.adapter.binding["supported_kinds"].append("claude")
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"},
                         override={"effort": "xhigh"})

        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start[-3:], ["--", "--effort", "xhigh"])

    def test_claude_kind_requires_verified_binding_and_builds_native_arguments(self):
        fake = FakeHerdr()
        self.spec.update(kind="claude", model="claude-sonnet-5-5",
                         route={"mode": "agent", "runtime": "claude-code"},
                         override={"effort": "high"})
        self.adapter.binding["supported_kinds"].append("claude")
        self.job["resolved_model"] = "claude-sonnet-5-5"

        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")

        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start[-5:], ["--", "--model", "claude-sonnet-5-5", "--effort", "high"])

    def test_claude_start_refuses_an_unbound_kind_before_any_host_effect(self):
        fake = FakeHerdr()
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        with patch("herdr_jobs.transport.command", fake.command):
            with self.assertRaisesRegex(JobError, "Herdr kind is not verified as installed: claude"):
                self.adapter.effect("start", self.job, "Assigned work")
        self.assertEqual(fake.calls, [])

    def test_haiku_start_inherits_the_required_compaction_window_in_its_pane(self):
        fake = FakeHerdr()
        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)
        self.adapter.binding["supported_kinds"].append("claude")
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"},
                         override={"effort": "medium"})
        self.job["resolved_model"] = "claude-haiku-4-5-20251001"
        with patch("herdr_jobs.transport.command", fake.command):
            self.adapter.effect("start", self.job, "Assigned work")
        self.assertEqual(fake.start_environment, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000"})
        start = next(call for call in fake.calls if call[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start, ["herdr", "agent", "start", "c9-fixture", "--kind", "claude",
                                 "--pane", "moved:1", "--", "--model", "claude-haiku-4-5-20251001",
                                 "--effort", "medium"])

    def test_haiku_environment_setup_rejection_never_starts_the_runtime(self):
        self.adapter.binding["supported_kinds"].append("claude")
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        self.job["resolved_model"] = "haiku"
        calls = []

        def reject_setup(argv, deadline, cwd=None, env=None):
            calls.append(argv)
            return 1, b"", b"setup rejected"

        with patch("herdr_jobs.transport.command", reject_setup):
            with self.assertRaisesRegex(EffectUnknown, "Haiku environment setup was rejected"):
                self.adapter.effect("start", self.job, "Assigned work")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:4], ["pane", "run", "moved:1"])

    def test_haiku_does_not_start_without_its_attempt_bound_environment_readback(self):
        fake = FakeHerdr()
        self.adapter.binding["supported_kinds"].append("claude")
        self.adapter.deadline = Deadline(0.01)
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        self.job["resolved_model"] = "claude-haiku-4-5"
        self.pane_output = "__ORCH_CLAUDE_ENV_previous-attempt__100000"
        with patch("herdr_jobs.transport.command", fake.command):
            with self.assertRaisesRegex(EffectUnknown, "Haiku environment did not persist"):
                self.adapter.effect("start", self.job, "Assigned work")
        self.assertIsNone(fake.start_environment)

    def test_claude_kind_is_refused_when_host_binding_does_not_list_it(self):
        fake = FakeHerdr()
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        self.available.append("claude-code")
        self.adapter.binding["supported_kinds"] = ["codex"]
        with patch("herdr_jobs.transport.command", fake.command):
            with self.assertRaisesRegex(JobError, "Herdr kind is not verified as installed: claude"):
                self.adapter.preflight({"jobs": [self.spec], "policy": {"local_lane": {}}})

        self.assertFalse(any(call[:3] == ["herdr", "agent", "start"] for call in fake.calls))

    def test_claude_kind_is_accepted_by_preflight_when_binding_lists_it(self):
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        self.adapter.binding["supported_kinds"].append("claude")
        self.available.append("claude-code")
        self.adapter.read = lambda argv: (load_json(HERDR_FIXTURES / "status.json") if argv[1] == "status"
                                          else {"runtimeSummary": {"availableIds": self.available}}
                                          if argv[1] == "doctor" else self.catalog)
        self.adapter.read_terminal = lambda argv: ""
        help_payload = (b'{"schemaVersion":1,"fullHelp":{"args":["help","--json","--workspace","fixture"]},'
                        b'"commands":[{"name":"help"}]}')
        self.adapter.raw = lambda argv: (b"herdr help" if argv == ["herdr", "--help"] else
                                         b"herdr agent help" if argv[1:3] == ["agent", "--help"] else
                                         b"herdr pane help" if argv[1:3] == ["pane", "--help"] else
                                         help_payload)
        self.adapter.binding["orchestrator_help_sha256"] = digest(orchestrator_help_contract_bytes(
            b'{"schemaVersion":1,"fullHelp":{"args":["help","--json","--workspace","fixture"]},"commands":[{"name":"help"}]}'))
        self.adapter.binding["herdr_help_sha256"] = digest(b"herdr help")
        self.adapter.binding["herdr_agent_help_sha256"] = digest(b"herdr agent help")
        self.adapter.binding["herdr_pane_help_sha256"] = digest(b"herdr pane help")

        result = self.adapter.preflight({"jobs": [self.spec], "policy": {"local_lane": {}}})
        self.assertEqual(result["binding"]["supported_kinds"], ["codex", "omp", "claude"])

    def test_claude_effort_is_capped_at_xhigh(self):
        self.assertEqual(effort_arguments("claude-code", "xhigh"), ["--effort", "xhigh"])
        with self.assertRaisesRegex(JobError, "effort 'max' is not supported"):
            effort_arguments("claude-code", "max")

    def test_claude_preflight_refuses_a_saved_max_route_even_if_catalog_accepts_it(self):
        self.adapter.binding["supported_kinds"].append("claude")
        self.available.append("claude-code")
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"},
                         model="claude-sonnet-5-5", override={"effort": "max"})
        self.catalog = {"models": [{"id": "claude-sonnet-5-5", "efforts": ["medium", "max"]}]}
        with self.assertRaisesRegex(JobError, "effort 'max' is not supported"):
            self.adapter.preflight({"jobs": [self.spec], "policy": {}})

    def test_claude_trust_question_blocks_submission_even_when_identity_is_idle(self):
        self.adapter.binding["supported_kinds"].append("claude")
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        self.agent.update(agent="claude", agent_status="idle")
        self.pane_output = "Do you trust the files in this folder?\n1. Yes, proceed\n2. No, exit"
        fake = FakeHerdr()
        with patch("herdr_jobs.transport.command", fake.command):
            with self.assertRaisesRegex(EffectUnknown, "trust or authentication dialog"):
                self.adapter.effect("prompt", self.job, "Assigned work")
        self.assertEqual(fake.calls, [])

    def test_claude_prompt_requires_idle_identity_and_observes_one_active_turn(self):
        self.adapter.binding["supported_kinds"].append("claude")
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        self.agent.update(agent="claude", agent_status="idle")
        self.pane_output = "Ready"
        self.job.update(phase="ready", pending_effect={"action": "prompt"})
        calls = []

        def accept_prompt(argv, deadline, cwd=None, env=None):
            calls.append(argv)
            self.agent["agent_status"] = "working"
            self.pane_output = "Working"
            return 0, b'{"result":{}}', b""

        with patch("herdr_jobs.transport.command", accept_prompt):
            result = self.adapter.effect("prompt", self.job, "Assigned work")
        self.assertEqual(result, {"prompt_submitted": True})
        self.assertEqual(calls, [["herdr", "agent", "prompt", "c9-fixture", "Assigned work"]])

    def test_claude_mismatched_identity_or_authentication_dialog_never_receives_prompt(self):
        self.adapter.binding["supported_kinds"].append("claude")
        self.spec.update(kind="claude", route={"mode": "agent", "runtime": "claude-code"})
        for agent_changes, output in (
            ({"agent": "codex"}, "Ready"),
            ({"agent": "claude", "pane_id": "another:pane"}, "Ready"),
            ({"agent": "claude", "name": "another-worker"}, "Ready"),
            ({"agent": "claude"}, "Sign in to continue"),
        ):
            with self.subTest(agent_changes=agent_changes, output=output):
                self.agent.update(agent="claude", pane_id="moved:1", name="c9-fixture", agent_status="idle")
                self.agent.update(agent_changes)
                self.pane_output = output
                fake = FakeHerdr()
                with patch("herdr_jobs.transport.command", fake.command):
                    with self.assertRaises(EffectUnknown):
                        self.adapter.effect("prompt", self.job, "Assigned work")
                self.assertEqual(fake.calls, [])

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
        self.agent.update(agent_status="working", agent="pi")
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
            response = load_json(HERDR_FIXTURES / "workspace-list.json")
            response["result"]["workspaces"][0]["workspace_id"] = "workspace:1"
            return json.dumps(response).encode()

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

    def test_failed_codex_start_retains_redacted_cli_output_when_identity_probe_fails(self):
        self.job["pending_effect"] = {"action": "start"}
        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)

        def failing_command(argv, deadline, cwd=None):
            if argv[:3] == ["herdr", "agent", "start"]:
                return (7, b'{"error":{"message":"registration rejected",'
                        b'"api_key":"native-start-response-secret",'
                        b'"client_secret":"native credential phrase"}}',
                        b"agent_not_ready: token=native-start-stderr-secret")
            if argv[:3] == ["herdr", "pane", "read"]:
                return 0, b"Codex session is starting", b""
            if argv[:3] == ["herdr", "agent", "get"]:
                return (1, b'{"error":{"message":"agent not found",'
                        b'"token":"native-get-response-secret"}}',
                        b"agent lookup failed: Authorization: Bearer native-get-stderr-secret")
            self.fail(f"unexpected command: {argv[:3]}")

        with patch("herdr_jobs.transport.command", failing_command):
            with self.assertRaises(EffectUnknown) as caught:
                self.adapter.effect("start", self.job, "Assigned work")

        diagnostic = caught.exception.start_diagnostic
        self.assertEqual(diagnostic["command"], "herdr agent start")
        self.assertEqual(diagnostic["exit_code"], 7)
        self.assertIn("registration rejected", diagnostic["stdout"])
        self.assertIn("agent_not_ready", diagnostic["stderr"])
        self.assertNotIn("native-start-response-secret", json.dumps(diagnostic))
        self.assertNotIn("native credential phrase", json.dumps(diagnostic))
        self.assertNotIn("native-start-stderr-secret", json.dumps(diagnostic))
        self.assertIn("herdr agent get exited 1", str(caught.exception.__cause__))
        self.assertNotIn("native-get-response-secret", str(caught.exception.__cause__))
        self.assertNotIn("native-get-stderr-secret", str(caught.exception.__cause__))
        self.assertEqual(self.job["start_diagnostic"], diagnostic)

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

    def test_codex_launch_persists_exact_cwd_trust_before_start(self):
        self.codex_home.mkdir()
        config = self.codex_home / "config.toml"
        original = b'# keep formatting\nmodel = "fixture"\n[mcp_servers.fixture]\ncommand = "fixture"\n'
        config.write_bytes(original)
        cwd = self.root / 'project with "quotes" and ☃'
        cwd.mkdir()
        self.spec["cwd"] = str(cwd)
        fake = FakeHerdr()

        def command(argv, deadline):
            if argv[:3] == ["herdr", "agent", "start"]:
                saved = config.read_bytes()
                self.assertTrue(saved.startswith(original))
                self.assertEqual(tomllib.loads(saved.decode())["projects"][str(cwd)]["trust_level"], "trusted")
            return fake.command(argv, deadline)

        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)
        with patch("herdr_jobs.transport.command", command):
            result = self.adapter.effect("start", self.job, "Assigned work")
        self.assertTrue(result["prompt_submitted"])

    def test_trust_and_resume_dialogs_fail_launch_without_answering_the_dialog(self):
        cases = [
            ("Do you trust the contents of this folder?", "trust dialog"),
            ("Trust this folder?\n1. Yes, trust and continue", "trust dialog"),
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
                if expected == "trust dialog":
                    self.assertIn("cwd is untrusted", result["issue"])
                    self.assertIn("config.toml", result["issue"])
                self.assertFalse(any(call[:2] == ["herdr", "agent"] and call[2] == "prompt" for call in fake.calls))
                self.assertFalse(any(call[:2] == ["herdr", "agent"] and call[2] == "send-keys" for call in fake.calls))
                self.assertFalse(any(call[:3] == ["herdr", "agent", "get"] for call in fake.calls))

    def test_codex_timeout_is_delivered_when_timed_activity_precedes_the_tui_footer(self):
        snapshot = ("• Ran a command\n"
                    "• Working (48s • esc to int…\n\n"
                    "› Ask Codex to do anything\n\n"
                    "  GPT-6.1-Sol high · ~/work…\n  ? for shortcuts  ⚠ 2 · f2\n")
        fake = FakeHerdr(state="working", output=snapshot, start_code=1)
        self.adapter.raw = NativeTransport.raw.__get__(self.adapter, NativeTransport)
        with patch("herdr_jobs.transport.command", fake.command):
            result = self.adapter.effect("start", self.job, "Assigned work")

        self.assertTrue(result["prompt_submitted"])
        self.assertEqual(result["start_diagnostic"]["exit_code"], 1)
        self.assertEqual(sum(call[:3] == ["herdr", "agent", "start"] for call in fake.calls), 1)
        self.assertFalse(any(call[:3] == ["herdr", "agent", "prompt"] for call in fake.calls))

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
        self.agent["agent_status"] = "working"
        self.pane_output = self.spec["task"]

        observation = self.adapter.observe(self.job)

        self.assertEqual(observation["state"], "working")
        self.assertFalse(observation["prompt_verified"])

    def test_unrelated_working_prose_does_not_prove_an_active_turn(self):
        self.job["phase"] = "submitted"
        self.agent["agent_status"] = "working"
        self.pane_output = "The report says the workflow is working as intended."

        observation = self.adapter.observe(self.job)

        self.assertFalse(observation["prompt_verified"])

    def test_active_turn_status_line_after_unrelated_prose_proves_prompt_landing(self):
        self.job["phase"] = "submitted"
        self.agent["agent_status"] = "working"
        self.pane_output = "The report says the workflow is working as intended.\n✳ Working…"

        observation = self.adapter.observe(self.job)

        self.assertTrue(observation["prompt_verified"])

    def test_active_status_after_an_echoed_working_prompt_proves_prompt_landing(self):
        self.spec["task"] = "Print this word on its own line:\nWorking"
        self.job["phase"] = "submitted"
        self.agent["agent_status"] = "working"
        self.pane_output = self.spec["task"] + "\n✳ Working…"

        observation = self.adapter.observe(self.job)

        self.assertTrue(observation["prompt_verified"])

    def test_echoed_authentication_phrase_does_not_block_a_working_worker(self):
        self.spec["task"] = "Explain the phrase authentication required in the log."
        self.job["phase"] = "submitted"
        self.agent["agent_status"] = "working"
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
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        self.spec["required_context"] = [
            {"path": str(self.root / "acceptance_test.md"), "kind": "test", "purpose": "Acceptance test."},
            {"path": str(self.root / "fixture-input.json"), "kind": "fixture", "purpose": "Input fixture."},
        ]
        self.spec["protected_paths"] = protected_paths_record(["keys/"])
        self.spec["editable_paths"] = []
        prompt = "Task with $(touch never) and 'quotes'"
        self.adapter.effect("jail", self.job, prompt)
        argv = self.calls[-1]
        self.assertEqual(argv[:4], ["herdr", "pane", "run", "moved:1"])
        runner_argv = shlex.split(argv[4])
        self.assertEqual(runner_argv[2], "--request")
        request = load_json(runner_argv[3])
        self.assertEqual(request["argv"], ["codex-jail-agent", "--harness", "codex", "exec",
                                           "--skip-git-repo-check", "-c",
                                           "model_providers.train-openai.stream_idle_timeout_ms=300000", prompt])
        self.assertEqual(request["protected_paths"], protected_paths_record(["keys/"]))
        self.assertEqual(request["editable_paths"], [])
        self.assertNotIn(prompt, argv[4])

    def test_short_routine_jail_class_uses_300000_ms_stream_timeout(self):
        self.job["spec"] = self.prepared_jail_spec("short_routine")
        prompt = "short task"

        self.adapter.effect("jail", self.job, prompt)

        runner_argv = shlex.split(self.calls[-1][4])
        request = load_json(runner_argv[3])
        self.assertEqual(request["argv"], ["codex-jail-agent", "--harness", "codex", "exec",
                                           "--skip-git-repo-check", "-c",
                                           "model_providers.train-openai.stream_idle_timeout_ms=300000",
                                           prompt])

    def test_long_form_research_jail_class_uses_600000_ms_stream_timeout(self):
        self.job["spec"] = self.prepared_jail_spec("long_form_research")
        prompt = "long research task"

        self.adapter.effect("jail", self.job, prompt)

        runner_argv = shlex.split(self.calls[-1][4])
        request = load_json(runner_argv[3])
        self.assertEqual(request["argv"], ["codex-jail-agent", "--harness", "codex", "exec",
                                           "--skip-git-repo-check", "-c",
                                           "model_providers.train-openai.stream_idle_timeout_ms=600000",
                                           prompt])

    def test_unrecognized_jail_class_uses_300000_ms_stream_timeout(self):
        self.job["spec"] = self.prepared_jail_spec("future_class")
        prompt = "future task"

        self.adapter.effect("jail", self.job, prompt)

        runner_argv = shlex.split(self.calls[-1][4])
        request = load_json(runner_argv[3])
        self.assertEqual(request["argv"], ["codex-jail-agent", "--harness", "codex", "exec",
                                           "--skip-git-repo-check", "-c",
                                           "model_providers.train-openai.stream_idle_timeout_ms=300000",
                                           prompt])

    def test_omitted_jail_class_uses_300000_ms_stream_timeout(self):
        self.job["spec"] = self.prepared_jail_spec(None)
        prompt = "unclassified task"

        self.adapter.effect("jail", self.job, prompt)

        runner_argv = shlex.split(self.calls[-1][4])
        request = load_json(runner_argv[3])
        self.assertEqual(request["argv"], ["codex-jail-agent", "--harness", "codex", "exec",
                                           "--skip-git-repo-check", "-c",
                                           "model_providers.train-openai.stream_idle_timeout_ms=300000",
                                           prompt])

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
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        request = {"schema_version": 1, "job_id": "j1", "attempt_id": "attempt1",
                   "argv": ["jail-agent", "--harness", "codex", "exec", "--skip-git-repo-check", "literal task"],
                   "exit_path": self.job["exit_record"],
                   "required_context": [
                       {"path": str(self.root / "acceptance_test.md"), "kind": "test", "purpose": "Acceptance test."},
                       {"path": str(self.root / "fixture-input.json"), "kind": "fixture", "purpose": "Input fixture."},
                   ],
                   "protected_paths": protected_paths_record(["keys/"]), "editable_paths": []}
        calls = []

        def launch(argv, check):
            calls.append((argv, check))
            return SimpleNamespace(returncode=23)

        self.assertEqual(runner.execute(request, run_process=launch,
                                        jail_exports=self.jail_exports), 23)
        self.assertEqual(calls, [(request["argv"], False)])
        self.assertEqual(load_json(self.job["exit_record"])["exit_code"], 23)

    def test_runner_accepts_codex_wrapper_with_stream_timeout_argument(self):
        module_spec = importlib.util.spec_from_file_location("jail_runner", PACKAGE / "scripts/jail-runner.py")
        runner = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(runner)
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        argv = ["codex-jail-agent", "--harness", "codex", "exec", "--skip-git-repo-check",
                "-c", "model_providers.train-openai.stream_idle_timeout_ms=300000", "literal task"]
        request = {"schema_version": 1, "job_id": "j1", "attempt_id": "attempt1", "argv": argv,
                   "exit_path": self.job["exit_record"],
                   "required_context": [
                       {"path": str(self.root / "acceptance_test.md"), "kind": "test", "purpose": "Acceptance test."},
                       {"path": str(self.root / "fixture-input.json"), "kind": "fixture", "purpose": "Input fixture."},
                   ],
                   "protected_paths": protected_paths_record(["keys/"]), "editable_paths": []}
        calls = []

        self.assertEqual(runner.execute(request,
                                        run_process=lambda command, check: calls.append((command, check))
                                        or SimpleNamespace(returncode=0),
                                        jail_exports=self.jail_exports), 0)
        self.assertEqual(calls, [(argv, False)])

    def test_runner_accepts_legacy_omp_train_codex_command_during_alias_period(self):
        module_spec = importlib.util.spec_from_file_location("jail_runner", PACKAGE / "scripts/jail-runner.py")
        runner = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(runner)
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        request = {"schema_version": 1, "job_id": "j1", "attempt_id": "attempt1",
                   "argv": ["omp-train", "--harness", "codex", "exec", "--skip-git-repo-check", "literal task"],
                   "exit_path": self.job["exit_record"],
                   "required_context": [
                       {"path": str(self.root / "acceptance_test.md"), "kind": "test", "purpose": "Acceptance test."},
                       {"path": str(self.root / "fixture-input.json"), "kind": "fixture", "purpose": "Input fixture."},
                   ],
                   "protected_paths": protected_paths_record(["keys/"]), "editable_paths": []}
        calls = []

        def launch(argv, check):
            calls.append((argv, check))
            return SimpleNamespace(returncode=0)

        self.assertEqual(runner.execute(request, run_process=launch,
                                        jail_exports=self.jail_exports), 0)
        self.assertEqual(calls, [(request["argv"], False)])

    def test_runner_rejects_host_readable_context_outside_jail_exports_before_launch(self):
        module_spec = importlib.util.spec_from_file_location("jail_runner", PACKAGE / "scripts/jail-runner.py")
        runner = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(runner)
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        request = {"schema_version": 1, "job_id": "j1", "attempt_id": "attempt1",
                   "argv": ["jail-agent", "--harness", "codex", "exec", "--skip-git-repo-check", "literal task"],
                   "exit_path": self.job["exit_record"],
                   "required_context": [
                       {"path": str(self.root / "acceptance_test.md"), "kind": "test", "purpose": "Acceptance test."},
                       {"path": str(self.root / "fixture-input.json"), "kind": "fixture", "purpose": "Input fixture."},
                   ],
                   "protected_paths": protected_paths_record(["keys/"]), "editable_paths": []}
        calls = []

        outside_exports = ((self.root / "exported", PurePosixPath("/ctx")),)
        with self.assertRaisesRegex(JobError, "outside.*jail-visible export"):
            runner.execute(request, run_process=lambda argv, check: calls.append((argv, check))
                           or SimpleNamespace(returncode=0), jail_exports=outside_exports)

        self.assertEqual(calls, [])

    def test_runner_rejects_editable_parent_of_a_protected_path_before_launch(self):
        module_spec = importlib.util.spec_from_file_location("jail_runner", PACKAGE / "scripts/jail-runner.py")
        runner = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(runner)
        (self.root / "acceptance_test.md").write_text("Jail acceptance test.")
        (self.root / "fixture-input.json").write_text('{"fixture": true}\n')
        request = {"schema_version": 1, "job_id": "j1", "attempt_id": "attempt1",
                   "argv": ["jail-agent", "--harness", "codex", "exec", "--skip-git-repo-check", "literal task"],
                   "exit_path": self.job["exit_record"],
                   "required_context": [
                       {"path": str(self.root / "acceptance_test.md"), "kind": "test", "purpose": "Acceptance test."},
                       {"path": str(self.root / "fixture-input.json"), "kind": "fixture", "purpose": "Input fixture."},
                   ],
                   "protected_paths": protected_paths_record(), "editable_paths": ["tools/"]}
        calls = []

        with self.assertRaisesRegex(JobError, "protected"):
            runner.execute(request, run_process=lambda argv, check: calls.append((argv, check)),
                           jail_exports=self.jail_exports)

        self.assertEqual(calls, [])

    def test_required_context_maps_paths_inside_an_export(self):
        context_root = self.root / "bundle"
        context_root.mkdir()
        required = context_root / "tests" / "acceptance_test.py"
        required.parent.mkdir()
        required.write_text("assert True\n")
        exports = ((context_root, PurePosixPath("/ctx")),
                   (self.root / "work", PurePosixPath("/work")))

        validated = validate_required_context(
            [{"path": str(required), "kind": "test"}], jail_visible=True, jail_exports=exports)

        self.assertEqual(validated[0]["path"], str(required.resolve()))
        self.assertEqual(jail_context_path(validated[0]["path"], exports), "/ctx/tests/acceptance_test.py")

    def test_required_context_rejects_a_host_readable_file_outside_exports(self):
        outside = self.root / "outside" / "required_test.py"
        outside.parent.mkdir()
        outside.write_text("assert True\n")

        with self.assertRaisesRegex(JobError, "outside.*jail-visible export"):
            validate_required_context([{"path": str(outside), "kind": "test"}],
                                      jail_visible=True,
                                      jail_exports=((self.root / "bundle", PurePosixPath("/ctx")),))

    def test_required_context_rejects_a_symlink_escape(self):
        context_root = self.root / "bundle"
        context_root.mkdir()
        outside = self.root / "private_test.py"
        outside.write_text("assert True\n")
        escaped = context_root / "tests" / "escaped_test.py"
        escaped.parent.mkdir()
        escaped.symlink_to(outside)

        with self.assertRaisesRegex(JobError, "outside.*jail-visible export"):
            validate_required_context([{"path": str(escaped), "kind": "test"}],
                                      jail_visible=True,
                                      jail_exports=((context_root, PurePosixPath("/ctx")),))

    def test_required_context_rejects_a_missing_file_inside_an_export(self):
        context_root = self.root / "bundle"
        context_root.mkdir()
        missing = context_root / "fixtures" / "input.json"

        with self.assertRaisesRegex(JobError, "required_context path is unavailable"):
            validate_required_context([{"path": str(missing), "kind": "fixture"}],
                                      jail_visible=True,
                                      jail_exports=((context_root, PurePosixPath("/ctx")),))


if __name__ == "__main__":
    unittest.main()


class CodexPromptArgumentTests(unittest.TestCase):
    def test_single_line_prompt_passes_through(self):
        from herdr_jobs.transport import codex_prompt_argument
        with tempfile.TemporaryDirectory() as root:
            job = {"host_output": str(Path(root) / "out"), "agent_name": "c9-x"}
            self.assertEqual(codex_prompt_argument(job, "do it"), "do it")
            self.assertFalse((Path(root) / "c9-x.prompt.md").exists())

    def test_multiline_prompt_moves_to_file(self):
        from herdr_jobs.transport import codex_prompt_argument
        with tempfile.TemporaryDirectory() as root:
            job = {"host_output": str(Path(root) / "out"), "agent_name": "c9-x"}
            argument = codex_prompt_argument(job, "line one\nline two\r\n")
            path = Path(root) / "c9-x.prompt.md"
            self.assertEqual(path.read_bytes(), b"line one\nline two\r\n")
            self.assertNotIn("\n", argument)
            self.assertNotIn("\r", argument)
            self.assertIn(str(path), argument)
