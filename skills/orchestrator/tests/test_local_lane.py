import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import herdr_jobs.local_lane as local_lane
from herdr_jobs.local_lane import check_preconditions, main
from herdr_jobs.records import JobError, policy_record, load_json


class LocalLaneTest(unittest.TestCase):
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

        with redirect_stdout(stdout), redirect_stderr(stderr), \
                patch("herdr_jobs.local_lane.check_preconditions",
                      side_effect=AssertionError("scope gate must run first")) as preflight:
            code = main([
                "start", "--task-class", "review_verdict",
                "--local-lane-scope-json", request,
                "--pane", "pane:1", "--name", "bounded-task",
                "--prompt-file", "/unread/for-rejected-scope.md",
            ])

        self.assertEqual(code, 2)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["error"], "invalid_input")
        self.assertIn("task_class", result["message"])
        self.assertEqual(stderr.getvalue(), "")
        preflight.assert_not_called()

    def test_direct_start_sanitizes_pane_before_starting_and_prompting_pi(self):
        start_direct = getattr(local_lane, "start_direct_local_lane", None)
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

        def fake_host(argv, *, env=None):
            calls.append((argv, env))
            return 0, b'{"result":{}}', b""

        result = start_direct(
            config, task_class="summary", local_lane_scope=scope,
            pane="moved:1", name="bounded-task", prompt="Summarize the supplied fixture.",
            source_env=source_env, herdr="herdr", run_command=fake_host,
        )

        self.assertEqual(result["outcome"], "ready")
        self.assertEqual([call[0][1:3] for call in calls], [
            ["pane", "run"], ["pane", "wait-output"], ["agent", "start"], ["agent", "prompt"],
        ])
        reset_command = calls[0][0][4]
        self.assertIn("env -i", reset_command)
        self.assertIn("HERDR_PANE_ID=moved:1", reset_command)
        expected_env_keys = {
            "HOME", "PATH", "TERM", "LANG", "HERDR_ENV", "HERDR_PANE_ID",
            "HERDR_BIN_PATH", "HERDR_SOCKET_PATH", "PI_CODING_AGENT_DIR",
        }
        for argv, env in calls:
            self.assertEqual(set(env), expected_env_keys)
            self.assertEqual(env["HOME"], "/home/fixture")
            self.assertEqual(env["PI_CODING_AGENT_DIR"], "/home/fixture/.pi/agent")
            self.assertNotIn("API_TOKEN", env)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertEqual(calls[2][0][-5:], ["--provider", "llama-local", "--model", config["model"],
                                           "--approve"])
        worker_prompt = calls[3][0][4]
        self.assertIn("task_class: summary", worker_prompt)
        self.assertIn('"credentials_in_environment": false', worker_prompt)
        self.assertIn("Do not access network, credentials, secrets, auth files, or environment", worker_prompt)
        self.assertIn("Summarize the supplied fixture.", worker_prompt)

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
                local_lane.start_direct_local_lane(
                    config, task_class="summary", local_lane_scope=scope,
                    pane="moved:1", name="bounded-task", prompt="Summarize the fixture.",
                    source_env={"HOME": "/home/fixture", "PATH": "/bin"},
                    run_command=fake_host,
                )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
