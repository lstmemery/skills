import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import herdr_jobs.local_lane as local_lane
from herdr_jobs.local_lane import check_preconditions, main
from herdr_jobs.admission import AdmissionStore
from herdr_jobs.records import JobError, policy_record, load_json
from herdr_jobs.routing_decision import decision_for


class LocalLaneTest(unittest.TestCase):
    def start_direct(self, config, **kwargs):
        policy = kwargs.get("policy") or policy_record(load_json(local_lane.POLICY_PATH))
        spec = local_lane.direct_route_spec(config, kwargs["task_class"], kwargs["local_lane_scope"],
                                            kwargs["name"], kwargs["prompt"])
        kwargs["route_decision"] = decision_for(spec, policy)
        host = kwargs["run_command"]

        def discovered_host(argv, *, env=None):
            if argv == ["pi", "--offline", "--list-models"]:
                table = ("provider model context max-out thinking images\n"
                         + config["provider"] + " " + config["model"] + " 32k 4k no no\n")
                return 0, table.encode(), b""
            return host(argv, env=env)

        kwargs["run_command"] = discovered_host
        return local_lane.start_direct_local_lane(config, **kwargs)

    def fake_worktree_host(self, argv):
        if not hasattr(self, "_fake_worktree_tmp"):
            self._fake_worktree_tmp = tempfile.TemporaryDirectory()
            fixture_root = Path(self._fake_worktree_tmp.name)
            primary = fixture_root / "primary"
            linked = fixture_root / "linked"
            git_env = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
            git_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
            subprocess.run(["git", "init", str(primary)], cwd=fixture_root, env=git_env,
                           check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "--allow-empty",
                            "-m", "fixture"], cwd=primary, env=git_env,
                           check=True, capture_output=True)
            subprocess.run(["git", "worktree", "add", "-b", "fixture-linked",
                            str(linked)], cwd=primary, env=git_env,
                           check=True, capture_output=True)
            self.addCleanup(self._fake_worktree_tmp.cleanup)
            self._fake_worktree_path = linked
            self._fake_worktree_git_env = git_env
        repo_root = self._fake_worktree_path
        pane_cwd = str(repo_root)
        if argv[:3] == ["herdr", "pane", "get"]:
            return 0, json.dumps({"result": {"pane": {"cwd": pane_cwd}}}).encode(), b""
        if argv[:2] == ["git", "-C"]:
            result = subprocess.run(
                argv, check=True, capture_output=True, cwd=repo_root,
                env=self._fake_worktree_git_env,
            )
            return 0, result.stdout, b""
        return None

    def run_fixture(self, *, service=(0, "active", ""), gpu=(0, "3072\n", "")):
        calls = []

        def run(argv):
            calls.append(argv)
            return service if argv[0] == "systemctl" else gpu

        config = {"service_unit": "llama-server.service",
                  "minimum_free_gpu_memory_mib": 2048}
        return check_preconditions(config, run), calls

    def test_requires_active_model_server_and_reserved_gpu_headroom(self):
        result, calls = self.run_fixture()
        self.assertEqual(result["free_gpu_memory_mib"], 3072)
        self.assertEqual(result["service_state"], "active")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-2:], ["is-active", "llama-server.service"])
        self.assertIn("memory.free", calls[1][1])

    def test_blocks_when_server_is_not_active(self):
        with self.assertRaisesRegex(JobError, "requires active user service"):
            self.run_fixture(service=(3, "inactive", ""))

    def test_blocks_when_gpu_headroom_is_insufficient_or_ambiguous(self):
        for gpu in ((0, "2047\n", ""), (0, "2048\n4096\n", ""), (1, "", "missing")):
            with self.subTest(gpu=gpu), self.assertRaises(JobError):
                self.run_fixture(gpu=gpu)

    def test_direct_start_refuses_unallowlisted_task_class_before_host_calls(self):
        stdout = StringIO()
        stderr = StringIO()
        request = json.dumps({
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": False,
            "destructive_actions": False,
        })

        with tempfile.TemporaryDirectory() as directory, \
                redirect_stdout(stdout), redirect_stderr(stderr), \
                patch("herdr_jobs.local_lane.check_preconditions",
                      side_effect=AssertionError("scope gate must run first")) as preflight:
            code = main([
                "start", "--task-class", "review_verdict",
                "--local-lane-scope-json", request,
                "--pane", "pane:1", "--name", "bounded-task",
                "--prompt-file", "/unread/for-rejected-scope.md",
                "--telemetry-file", str(Path(directory) / "local-lane-rejected" / "telemetry.json"),
            ])

        self.assertEqual(code, 2)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["error"], "invalid_input")
        self.assertIn("task_class", result["message"])
        self.assertEqual(stderr.getvalue(), "")
        preflight.assert_not_called()

    def test_direct_start_cli_emits_the_unresolved_effect_record(self):
        scope = json.dumps({
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": False,
            "destructive_actions": False,
        })
        effect = {
            "action": "start",
            "status": "unknown",
            "pane_id": "moved:1",
            "agent_name": "bounded-task",
            "lease_id": "direct-local-fixture",
            "observation": {"state": "unknown", "identity_verified": False,
                            "prompt_verified": False},
        }
        unresolved = local_lane.UnknownDirectLocalLaneEffect("reconciliation required", effect)
        stdout = StringIO()
        stderr = StringIO()

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as input_dir:
            prompt_file = Path(input_dir) / "prompt.md"
            prompt_file.write_text("Summarize the fixture.", encoding="utf-8")
            decision_file = Path(input_dir) / "decision.json"
            policy = policy_record(load_json(local_lane.POLICY_PATH))
            spec = local_lane.direct_route_spec(policy["local_lane"], "summary", json.loads(scope),
                                                "bounded-task", prompt_file.read_text())
            decision_file.write_text(json.dumps(decision_for(spec, policy)))
            with redirect_stdout(stdout), redirect_stderr(stderr), \
                    patch("herdr_jobs.local_lane.check_preconditions", return_value={}), \
                    patch("herdr_jobs.local_lane.start_direct_local_lane", side_effect=unresolved):
                code = main([
                    "start", "--task-class", "summary", "--local-lane-scope-json", scope,
                    "--pane", "moved:1", "--name", "bounded-task", "--prompt-file", str(prompt_file),
                    "--telemetry-file", str(Path(input_dir) / "telemetry.json"),
                    "--route-decision", str(decision_file),
                ])

        self.assertEqual(code, 4)
        self.assertEqual(stderr.getvalue(), "")
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["error"], "unresolved_effect")
        self.assertEqual(result["pending_effect"], effect)

    def test_direct_start_refuses_when_shared_admission_slot_is_held(self):
        policy = policy_record(load_json(local_lane.POLICY_PATH))
        config = policy["local_lane"]
        scope = {
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": False,
            "destructive_actions": False,
        }
        calls = []

        def fake_host(argv, *, env=None):
            calls.append(argv)
            worktree_result = self.fake_worktree_host(argv)
            if worktree_result is not None:
                return worktree_result
            return 0, b'{"result":{}}', b""

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as admission_dir:
            store = AdmissionStore(admission_dir)
            held = store.acquire("llama-local", config["model"], "managed-held", 1, "managed-run")
            self.assertTrue(held["admitted"])
            with patch.dict(os.environ, {"ORCHESTRATOR_ADMISSION_DIR": admission_dir}):
                with self.assertRaisesRegex(JobError, "admission"):
                    self.start_direct(
                        config, task_class="summary", local_lane_scope=scope,
                        pane="moved:1", name="bounded-task", prompt="Summarize the fixture.",
                        telemetry_path=Path(admission_dir) / "telemetry.json",
                        source_env={"HOME": "/home/fixture", "PATH": "/bin"},
                        run_command=fake_host,
                    )

            self.assertEqual(calls, [])
            self.assertEqual(store.status("llama-local")["active_count"], 1)

    def test_direct_start_sanitizes_pane_before_starting_and_prompting_pi(self):
        start_direct = self.start_direct
        self.assertIsNotNone(start_direct, "the direct route must use the shared local-lane launcher")
        policy = policy_record(load_json(local_lane.POLICY_PATH))
        config = policy["local_lane"]
        scope = {
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": False,
            "destructive_actions": False,
        }
        source_env = {
            "HOME": "/home/fixture",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "PI_CODING_AGENT_DIR": "/home/fixture/.pi/agent",
            "HERDR_ENV": "1",
            "HERDR_PANE_ID": "coordinator:1",
            "HERDR_BIN_PATH": "/home/fixture/.local/bin/herdr",
            "HERDR_SOCKET_PATH": "/run/user/1000/herdr.sock",
            "API_TOKEN": "fixture-secret-token",
            "AWS_SECRET_ACCESS_KEY": "fixture-secret-key",
        }
        calls = []
        agent_state = {"value": "idle"}
        active_counts = []

        def fake_host(argv, *, env=None):
            calls.append((argv, env))
            active_counts.append(AdmissionStore(admission_dir).status("llama-local")["active_count"])
            worktree_result = self.fake_worktree_host(argv)
            if worktree_result is not None:
                return worktree_result
            if argv[:3] == ["herdr", "agent", "get"]:
                agent = {"agent_status": agent_state["value"], "pane_id": "moved:1",
                         "agent": "pi", "name": "bounded-task"}
                return 0, json.dumps({"result": {"agent": agent}}).encode(), b""
            if argv[:3] == ["herdr", "agent", "prompt"]:
                agent_state["value"] = "working"
            if argv[:3] == ["herdr", "pane", "read"]:
                agent_state["value"] = "done"
                return 0, b"Working\n", b""
            return 0, b'{"result":{}}', b""

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as admission_dir:
            telemetry_path = Path(admission_dir) / "telemetry.json"
            with patch.dict(os.environ, {"ORCHESTRATOR_ADMISSION_DIR": admission_dir}):
                result = start_direct(
                    config, task_class="summary", local_lane_scope=scope,
                    pane="moved:1", name="bounded-task", prompt="Summarize the supplied fixture.",
                    source_env=source_env, herdr="herdr", run_command=fake_host,
                    telemetry_path=telemetry_path,
                )
                self.assertEqual(AdmissionStore(admission_dir).status("llama-local")["active_count"], 0)
            telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))

        self.assertTrue(active_counts)
        self.assertEqual(set(active_counts), {1})
        self.assertEqual(result["outcome"], "ready")
        self.assertEqual(telemetry["launch"]["backend"], "native")
        self.assertEqual(telemetry["launch"]["decision"]["resolved"], {
            "runtime": "pi", "provider": "llama-local", "model": config["model"], "effort": "medium"})
        self.assertEqual(telemetry, result["telemetry"])
        self.assertEqual(result["telemetry_file"], str(telemetry_path))
        self.assertEqual(telemetry["schema_version"], 1)
        self.assertEqual(telemetry["route"], {
            "lane": "local_lane", "level": None, "choice": "explicit_opt_in",
            "model": config["model"], "effort": config["effort"],
            "harness": "pi", "provider": config["provider"],
        })
        self.assertTrue(telemetry["started_at"])
        self.assertTrue(telemetry["ended_at"])
        self.assertEqual(telemetry["continuation_count"], 0)
        self.assertEqual(telemetry["continuation_reasons"], [])
        self.assertEqual(telemetry["quota_signals"], [])
        self.assertEqual(telemetry["coordinator_grade"], "pending")
        self.assertEqual(telemetry["independent_validation"]["status"], "pending")
        self.assertEqual([call[0][1:3] for call in calls if call[0][0] == "herdr"], [
            ["pane", "get"],
            ["pane", "run"], ["pane", "wait-output"], ["agent", "start"], ["agent", "get"],
            ["agent", "prompt"], ["agent", "get"], ["pane", "read"], ["agent", "get"],
        ])
        reset_command = next(call[0][4] for call in calls
                             if call[0][:3] == ["herdr", "pane", "run"])
        self.assertIn("env -i", reset_command)
        self.assertIn("HERDR_PANE_ID=moved:1", reset_command)
        expected_env_keys = {
            "HOME", "PATH", "TERM", "LANG", "HERDR_ENV", "HERDR_PANE_ID",
            "HERDR_BIN_PATH", "HERDR_SOCKET_PATH", "PI_CODING_AGENT_DIR",
        }
        for argv, env in calls:
            if env is None:
                continue
            self.assertEqual(set(env), expected_env_keys)
            self.assertEqual(env["HOME"], "/home/fixture")
            self.assertEqual(env["PI_CODING_AGENT_DIR"], "/home/fixture/.pi/agent")
            self.assertNotIn("API_TOKEN", env)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        start_call = next(call[0] for call in calls if call[0][:3] == ["herdr", "agent", "start"])
        prompt_call = next(call[0] for call in calls if call[0][:3] == ["herdr", "agent", "prompt"])
        self.assertEqual(start_call[-5:], ["--provider", "llama-local", "--model", config["model"],
                                           "--approve"])
        self.assertEqual(prompt_call[3], "bounded-task")
        self.assertNotIn("--wait", prompt_call)
        self.assertNotIn("--timeout", prompt_call)
        worker_prompt = prompt_call[4]
        self.assertIn("task_class: summary", worker_prompt)
        self.assertIn('"credentials_in_environment": false', worker_prompt)
        self.assertIn("Do not access network, credentials, secrets, auth files, or environment", worker_prompt)
        self.assertIn("evidence_gate: investigatory claims cite their source and label unknowns", worker_prompt)
        self.assertIn("check both the host and jail namespaces", worker_prompt)
        self.assertIn("Summarize the supplied fixture.", worker_prompt)

    def test_direct_start_refuses_primary_checkout_before_pane_reset(self):
        policy = policy_record(load_json(local_lane.POLICY_PATH))
        config = policy["local_lane"]
        scope = {"network_access": False, "credential_access": False,
                 "credentials_in_environment": False, "destructive_actions": False}
        calls = []
        primary = tempfile.TemporaryDirectory()
        self.addCleanup(primary.cleanup)
        root = Path(primary.name).resolve()
        (root / ".git").mkdir()

        def fake_host(argv, *, env=None):
            calls.append(argv)
            if argv[:3] == ["herdr", "pane", "get"]:
                return 0, json.dumps({"result": {"pane": {"cwd": str(root)}}}).encode(), b""
            if argv[0] == "git":
                if "--show-toplevel" in argv:
                    return 0, str(root).encode(), b""
                if "--git-common-dir" in argv:
                    return 0, str(root / ".git").encode(), b""
                return 0, b"worktree " + str(root).encode() + b"\0", b""
            return 0, b'{"result":{}}', b""

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as admission_dir:
            with patch.dict(os.environ, {"ORCHESTRATOR_ADMISSION_DIR": admission_dir}), \
                    self.assertRaisesRegex(JobError, "linked worktree"):
                self.start_direct(
                    config, task_class="summary", local_lane_scope=scope,
                    pane="moved:1", name="bounded-task", prompt="Summarize the fixture.",
                    telemetry_path=Path(admission_dir) / "telemetry.json",
                    source_env={"HOME": "/home/fixture", "PATH": "/bin"},
                    run_command=fake_host,
                )
        self.assertEqual(calls[0][:3], ["herdr", "pane", "get"])
        self.assertFalse(any(argv[:3] == ["herdr", "pane", "run"] for argv in calls))

    def test_direct_start_retains_shared_admission_after_ambiguous_host_start(self):
        policy = policy_record(load_json(local_lane.POLICY_PATH))
        config = policy["local_lane"]
        scope = {
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": False,
            "destructive_actions": False,
        }

        def fake_host(argv, *, env=None):
            worktree_result = self.fake_worktree_host(argv)
            if worktree_result is not None:
                return worktree_result
            if argv[:3] == ["herdr", "agent", "start"]:
                return 1, b"", b"start failed"
            if argv[:3] == ["herdr", "agent", "get"]:
                agent = {"agent_status": "working", "pane_id": "moved:1",
                         "agent": "pi", "name": "bounded-task"}
                return 0, json.dumps({"result": {"agent": agent}}).encode(), b""
            return 0, b'{"result":{}}', b""

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as admission_dir:
            with patch.dict(os.environ, {"ORCHESTRATOR_ADMISSION_DIR": admission_dir}):
                with self.assertRaises(local_lane.UnknownDirectLocalLaneEffect) as caught:
                    self.start_direct(
                        config, task_class="summary", local_lane_scope=scope,
                        pane="moved:1", name="bounded-task", prompt="Summarize the fixture.",
                        telemetry_path=Path(admission_dir) / "telemetry.json",
                        source_env={"HOME": "/home/fixture", "PATH": "/bin"},
                        run_command=fake_host,
                    )

            status = AdmissionStore(admission_dir).status("llama-local")
            self.assertEqual(status["active_count"], 1)
            held = status["leases"][0]
            second = AdmissionStore(admission_dir).acquire(
                "llama-local", config["model"], "direct-after-unknown", 1, "second-direct-run")
            self.assertFalse(second["admitted"])
            self.assertEqual(second["reason"], "capacity")

        self.assertEqual(caught.exception.effect["action"], "start")
        self.assertEqual(caught.exception.effect["status"], "unknown")
        self.assertEqual(caught.exception.effect["pane_id"], "moved:1")
        self.assertEqual(caught.exception.effect["agent_name"], "bounded-task")
        self.assertTrue(caught.exception.effect["recorded"])
        self.assertEqual(caught.exception.effect["observation"]["identity_verified"], True)
        self.assertEqual(caught.exception.effect["observation"]["state"], "working")
        self.assertIn("reconciliation", str(caught.exception).casefold())
        self.assertEqual(held["lease_id"], caught.exception.effect["lease_id"])
        self.assertEqual(held["pending_effect"]["action"], "start")
        self.assertEqual(held["pending_effect"]["status"], "unknown")
        self.assertEqual(held["pending_effect"]["observation"]["state"], "working")

    def test_direct_worker_wait_interrupt_leaves_a_reconcilable_pending_effect(self):
        config = policy_record(load_json(local_lane.POLICY_PATH))["local_lane"]
        scope = {
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": False,
            "destructive_actions": False,
        }
        agent_state = {"value": "idle"}
        agent_gets = {"count": 0}
        observed_effects = []

        def fake_host(argv, *, env=None):
            worktree_result = self.fake_worktree_host(argv)
            if worktree_result is not None:
                return worktree_result
            if argv[:3] == ["herdr", "agent", "get"]:
                leases = AdmissionStore(admission_dir).status("llama-local")["leases"]
                observed_effects.append(leases[0].get("pending_effect"))
                agent_gets["count"] += 1
                if agent_gets["count"] == 3:
                    raise KeyboardInterrupt
                agent = {"agent_status": agent_state["value"], "pane_id": "moved:1",
                         "agent": "pi", "name": "bounded-task"}
                return 0, json.dumps({"result": {"agent": agent}}).encode(), b""
            if argv[:3] == ["herdr", "agent", "prompt"]:
                agent_state["value"] = "working"
            if argv[:3] == ["herdr", "pane", "read"]:
                return 0, b"Working\n", b""
            return 0, b'{"result":{}}', b""

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as admission_dir:
            telemetry_path = Path(admission_dir) / "telemetry.json"
            with patch.dict(os.environ, {"ORCHESTRATOR_ADMISSION_DIR": admission_dir}):
                with self.assertRaises(KeyboardInterrupt):
                    self.start_direct(
                        config, task_class="summary", local_lane_scope=scope,
                        pane="moved:1", name="bounded-task", prompt="Summarize the fixture.",
                        telemetry_path=telemetry_path,
                        source_env={"HOME": "/home/fixture", "PATH": "/bin"},
                        run_command=fake_host,
                    )

            leases = AdmissionStore(admission_dir).status("llama-local")["leases"]
            telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))

        self.assertEqual(len(leases), 1)
        self.assertTrue(observed_effects)
        self.assertTrue(all(effect is not None for effect in observed_effects))
        effect = leases[0]["pending_effect"]
        self.assertEqual(effect["action"], "start")
        self.assertEqual(effect["status"], "unknown")
        self.assertEqual(effect["pane_id"], "moved:1")
        self.assertEqual(effect["agent_name"], "bounded-task")
        self.assertEqual(effect["observation"]["state"], "unknown")
        self.assertIsInstance(effect["recorded_at"], (int, float))
        self.assertTrue(telemetry["started_at"])
        self.assertIsNone(telemetry["ended_at"])

    def test_direct_start_releases_shared_admission_when_failure_proves_start_not_attempted(self):
        config = policy_record(load_json(local_lane.POLICY_PATH))["local_lane"]
        scope = {
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": False,
            "destructive_actions": False,
        }

        def fake_host(argv, *, env=None):
            worktree_result = self.fake_worktree_host(argv)
            if worktree_result is not None:
                return worktree_result
            if argv[:3] == ["herdr", "pane", "run"]:
                return 1, b"", b"pane run failed before agent start"
            return 0, b'{"result":{}}', b""

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as admission_dir:
            with patch.dict(os.environ, {"ORCHESTRATOR_ADMISSION_DIR": admission_dir}):
                with self.assertRaisesRegex(JobError, "reset the local-lane pane environment"):
                    self.start_direct(
                        config, task_class="summary", local_lane_scope=scope,
                        pane="moved:1", name="bounded-task", prompt="Summarize the fixture.",
                        telemetry_path=Path(admission_dir) / "telemetry.json",
                        source_env={"HOME": "/home/fixture", "PATH": "/bin"},
                        run_command=fake_host,
                    )

            self.assertEqual(AdmissionStore(admission_dir).status("llama-local")["active_count"], 0)

    def test_direct_start_rejects_missing_or_unsafe_scope_before_host_calls(self):
        config = policy_record(load_json(local_lane.POLICY_PATH))["local_lane"]
        calls = []

        def fake_host(argv, *, env=None):
            calls.append(argv)
            return 0, b'{"result":{}}', b""

        unsafe_scope = {
            "network_access": False,
            "credential_access": False,
            "credentials_in_environment": True,
            "destructive_actions": False,
        }
        for scope in (None, unsafe_scope):
            with self.subTest(scope=scope), self.assertRaises(JobError):
                self.start_direct(
                    config, task_class="summary", local_lane_scope=scope,
                    pane="moved:1", name="bounded-task", prompt="Summarize the fixture.",
                    telemetry_path=Path(__file__).parent / "unused-telemetry.json",
                    source_env={"HOME": "/home/fixture", "PATH": "/bin"},
                    run_command=fake_host,
                )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
