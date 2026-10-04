from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import stat
import sys
import tempfile
from threading import Thread
import unittest
from unittest import mock


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
import durable_watch


class DurableWatchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name) / "run"
        self.run_dir.mkdir()
        self.task_a = self.run_dir / "task-a"
        self.task_b = self.run_dir / "task-b"
        self.task_a.mkdir()
        self.task_b.mkdir()
        durable_watch.write_config(self.run_dir / durable_watch.CONFIG, {
            "schema_version": durable_watch.VERSION,
            "tasks": ["task-a", "task-b"],
            "interval_seconds": 30,
            "notify": False,
            "publisher": "",
        })

    def events(self):
        path = self.run_dir / durable_watch.EVENTS
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_reconcile_records_each_new_result_once_and_ignores_unlisted_dirs(self):
        (self.task_a / "result.json").write_text('{"outcome":"ready"}\n', encoding="utf-8")
        (self.run_dir / "stray").mkdir()
        (self.run_dir / "stray" / "result.json").write_text("{}\n", encoding="utf-8")

        first = durable_watch.reconcile_once(self.run_dir)
        second = durable_watch.reconcile_once(self.run_dir)

        self.assertEqual(len(first["new_events"]), 1)
        self.assertEqual(first["new_events"][0]["task_dir"], "task-a")
        self.assertFalse(first["complete"])
        self.assertEqual(second["new_events"], [])
        self.assertEqual([event["task_dir"] for event in self.events()], ["task-a"])

    def test_changed_result_is_a_distinct_observation(self):
        result = self.task_a / "result.json"
        result.write_text('{"outcome":"ready"}\n', encoding="utf-8")
        durable_watch.reconcile_once(self.run_dir)
        result.write_text('{"outcome":"blocked"}\n', encoding="utf-8")

        outcome = durable_watch.reconcile_once(self.run_dir)

        self.assertEqual(len(outcome["new_events"]), 1)
        self.assertEqual(len(self.events()), 2)
        self.assertNotEqual(self.events()[0]["result_sha256"], self.events()[1]["result_sha256"])

    def test_completion_requires_a_disposition_for_every_listed_task(self):
        (self.task_a / "disposition.json").write_text('{"disposition":"completed"}\n', encoding="utf-8")
        self.assertFalse(durable_watch.reconcile_once(self.run_dir)["complete"])

        (self.task_b / "disposition.json").write_text('{"disposition":"completed"}\n', encoding="utf-8")
        outcome = durable_watch.reconcile_once(self.run_dir)

        self.assertTrue(outcome["complete"])
        self.assertEqual(outcome["settled"], ["task-a", "task-b"])

    def test_terminal_reconcile_requests_timer_self_disarm(self):
        for task in (self.task_a, self.task_b):
            (task / "disposition.json").write_text('{"disposition":"completed"}\n', encoding="utf-8")
        args = type("Args", (), {"run_dir": str(self.run_dir)})()

        with mock.patch.object(durable_watch, "stop_units") as stop, redirect_stdout(io.StringIO()):
            result = durable_watch.command_reconcile(args)

        self.assertEqual(result, 0)
        stop.assert_called_once_with(self.run_dir.resolve(), nonblocking=True)

    def test_new_result_uses_the_private_ntfy_route_once(self):
        received = []

        class NtfyHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                received.append((self.path, dict(self.headers), body))
                response = json.dumps({"id": "test-message-id"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), NtfyHandler)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, 2)
        self.addCleanup(server.shutdown)
        config_path = self.run_dir / durable_watch.CONFIG
        config = json.loads(config_path.read_text(encoding="utf-8"))
        route = f"http://127.0.0.1:{server.server_port}/test/topic"
        token_file = self.run_dir / "ntfy-token"
        token_file.write_text("unit-test-token\n", encoding="utf-8")
        config.update({
            "notify": True,
            "ntfy_url_sha256": hashlib.sha256(route.encode()).hexdigest(),
            "ntfy_url_file": str(self.run_dir / durable_watch.NTFY_URL_FILE),
            "ntfy_token_file": str(token_file),
            "notification_title": "Orchestrator worker result",
        })
        durable_watch.write_private_text(self.run_dir / durable_watch.NTFY_URL_FILE, route)
        durable_watch.write_config(config_path, config)
        (self.task_a / "result.json").write_text('{"outcome":"ready"}\n', encoding="utf-8")

        output = io.StringIO()
        with redirect_stderr(output):
            first = durable_watch.reconcile_once(self.run_dir)
            second = durable_watch.reconcile_once(self.run_dir)

        self.assertEqual(len(first["new_events"]), 1)
        self.assertEqual(second["new_events"], [])
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "/test/topic")
        self.assertEqual(received[0][1]["Authorization"], "Bearer unit-test-token")
        self.assertEqual(received[0][1]["Title"], "Orchestrator worker result")
        self.assertEqual(received[0][1]["Priority"], "3")
        self.assertEqual(received[0][2], b"task-a wrote result.json in run")
        self.assertIn("ntfy accepted (HTTP 200; id=test-message-id)", output.getvalue())
        self.assertNotIn(route, output.getvalue())
        self.assertNotIn("unit-test-token", output.getvalue())

    def test_arm_keeps_notification_route_out_of_systemd_arguments(self):
        (self.run_dir / durable_watch.CONFIG).unlink()
        secret_url = "https://notify.example.invalid/private-topic-test-1159"
        args = type("Args", (), {
            "run_dir": str(self.run_dir),
            "tasks": ["task-a"],
            "interval_seconds": 30,
            "notification_title": "Orchestrator worker result",
        })()
        launched = []
        launch_environments = []

        def capture_systemd_run(command, **kwargs):
            launched.append(command)
            launch_environments.append(kwargs["env"])
            return mock.Mock(returncode=0)

        with mock.patch.dict(os.environ, {"NTFY_URL": secret_url}, clear=True), \
                mock.patch.object(durable_watch, "systemd_property", return_value="not-found"), \
                mock.patch.object(durable_watch.subprocess, "run", side_effect=capture_systemd_run), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(durable_watch.command_arm(args), 0)

        self.assertEqual(len(launched), 1)
        argv = launched[0]
        self.assertFalse(any(secret_url in value for value in argv))
        self.assertFalse(any(value.startswith("--setenv=") for value in argv))
        self.assertNotIn("NTFY_URL", launch_environments[0])
        self.assertFalse(any(secret_url in value for value in launch_environments[0].values()))
        secret_path = self.run_dir / durable_watch.NTFY_URL_FILE
        self.assertEqual(stat.S_IMODE(secret_path.stat().st_mode), 0o600)
        self.assertEqual(secret_path.read_text(encoding="utf-8").strip(), secret_url)
        config_text = (self.run_dir / durable_watch.CONFIG).read_text(encoding="utf-8")
        self.assertNotIn(secret_url, config_text)

    def test_disarm_clears_configuration_before_changed_watch_is_armed(self):
        durable_watch.write_private_text(self.run_dir / durable_watch.NTFY_URL_FILE, "https://notify.example.invalid/topic")
        disarm_args = type("Args", (), {"run_dir": str(self.run_dir)})()
        arm_args = type("Args", (), {
            "run_dir": str(self.run_dir),
            "tasks": ["task-a"],
            "interval_seconds": 15,
            "notification_title": "Orchestrator worker result",
        })()

        with mock.patch.object(durable_watch, "stop_units"), redirect_stdout(io.StringIO()):
            self.assertEqual(durable_watch.command_disarm(disarm_args), 0)

        self.assertFalse((self.run_dir / durable_watch.CONFIG).exists())
        self.assertFalse((self.run_dir / durable_watch.NTFY_URL_FILE).exists())
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(durable_watch, "systemd_property", return_value="not-found"), \
                mock.patch.object(durable_watch.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(durable_watch.command_arm(arm_args), 0)

        self.assertEqual(durable_watch.read_config(self.run_dir)["tasks"], ["task-a"])

    def test_task_paths_cannot_escape_the_run_directory(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        with self.assertRaises(durable_watch.WatchError):
            durable_watch.task_names(self.run_dir, [str(outside)])


if __name__ == "__main__":
    unittest.main()
