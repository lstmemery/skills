from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest import mock
import urllib.error


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

    def start_ntfy_server(self, response_id):
        received = []

        class NtfyHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                received.append((self.path, dict(self.headers), body))
                response = json.dumps({"id": response_id}).encode("utf-8")
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
        return server, received

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
        server, received = self.start_ntfy_server("test-message-id")
        config_path = self.run_dir / durable_watch.CONFIG
        config = json.loads(config_path.read_text(encoding="utf-8"))
        route = f"http://127.0.0.1:{server.server_port}/test/topic"
        token_file = self.run_dir / "ntfy-token"
        token_file.write_text("unit-test-token\n", encoding="utf-8")
        token_file.chmod(0o600)
        config.update({
            "notify": True,
            "ntfy_url_sha256": hashlib.sha256(route.encode()).hexdigest(),
            "ntfy_url_file": str(self.run_dir / durable_watch.NTFY_URL_FILE),
            "ntfy_token_file": str(token_file),
            "ntfy_token_path_checked": True,
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
        self.assertIn("ntfy publish succeeded (HTTP 200); event is recorded", output.getvalue())
        self.assertNotIn("test-message-id", output.getvalue())
        self.assertNotIn(route, output.getvalue())
        self.assertNotIn("unit-test-token", output.getvalue())

    def test_authenticated_ntfy_request_does_not_follow_cross_host_redirect(self):
        route = "https://ntfy.example.invalid/source-topic"
        redirect_target = "https://other.example.invalid/target-topic"
        token = "fake"
        token_file = self.run_dir / "redirect-token"
        token_file.write_text(token + "\n", encoding="utf-8")
        token_file.chmod(0o600)
        config = {
            "notify": True,
            "ntfy_url_sha256": hashlib.sha256(route.encode()).hexdigest(),
            "ntfy_url_file": str(self.run_dir / durable_watch.NTFY_URL_FILE),
            "ntfy_token_file": str(token_file),
            "ntfy_token_path_checked": True,
        }
        durable_watch.write_private_text(self.run_dir / durable_watch.NTFY_URL_FILE, route)
        delivered = []
        openers = []

        class FakeOpener:
            def __init__(self, handlers):
                self.redirect_handler = next(
                    handler for handler in handlers
                    if isinstance(handler, durable_watch.NoRedirectHandler)
                )

            def open(self, request, timeout):
                delivered.append(("ntfy.example.invalid", request.get_header("Authorization")))
                redirected = self.redirect_handler.redirect_request(
                    request, self, 302, "Found", {"Location": redirect_target}, redirect_target,
                )
                if redirected is not None:
                    delivered.append(("other.example.invalid", redirected.get_header("Authorization")))
                raise urllib.error.HTTPError(
                    request.full_url, 302, "Found", {}, io.BytesIO(),
                )

        def build_fake_opener(*handlers):
            opener = FakeOpener(handlers)
            openers.append(opener)
            return opener

        output = io.StringIO()
        with mock.patch.object(durable_watch.urllib.request, "build_opener", side_effect=build_fake_opener), \
                mock.patch.object(durable_watch.urllib.request, "urlopen", side_effect=AssertionError("unexpected urlopen")), \
                redirect_stderr(output):
            durable_watch.publish({"task_dir": "task-a", "run_dir": "run"}, config)

        self.assertEqual(len(openers), 1)
        self.assertEqual(delivered, [("ntfy.example.invalid", "Bearer fake")])
        self.assertIn(
            "ntfy publish failed (HTTP 302); event is recorded",
            output.getvalue(),
        )
        self.assertNotIn(redirect_target, output.getvalue())
        self.assertNotIn(token, output.getvalue())

    def test_ntfy_url_requires_https_except_for_loopback(self):
        with mock.patch.dict(os.environ, {"NTFY_URL": "http://ntfy.example.invalid/topic"}, clear=True):
            with self.assertRaisesRegex(durable_watch.WatchError, "https unless it targets loopback"):
                durable_watch.ntfy_url()

        for value in ("http://localhost/topic", "http://127.0.0.1/topic", "https://ntfy.example.invalid/topic"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"NTFY_URL": value}, clear=True):
                self.assertEqual(durable_watch.ntfy_url(), value)

    def test_arm_starts_watcher_with_an_allowlisted_environment(self):
        (self.run_dir / durable_watch.CONFIG).unlink()
        secret_url = "http://localhost/a"
        unknown_manager_value = "fake"
        runtime_dir = "/run/user/test"
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

        with mock.patch.dict(os.environ, {
                "NTFY_URL": secret_url,
                "FOO_UNLISTED_SETTING": unknown_manager_value,
                "XDG_RUNTIME_DIR": runtime_dir,
        }, clear=True), \
                mock.patch.object(durable_watch, "systemd_property", return_value="not-found"), \
                mock.patch.object(durable_watch.subprocess, "run", side_effect=capture_systemd_run), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(durable_watch.command_arm(args), 0)

        self.assertEqual(len(launched), 1)
        argv = launched[0]
        self.assertFalse(any(secret_url in value for value in argv))
        service_argv = argv[argv.index("--timer-property=AccuracySec=1s") + 1:]
        system_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        allowlisted_environment = [
            f"PATH={system_path}",
            f"HOME={Path.home()}",
            "LANG=C.UTF-8",
            f"XDG_RUNTIME_DIR={runtime_dir}",
        ]
        self.assertEqual(service_argv[:2], ["/usr/bin/env", "-i"])
        self.assertEqual(service_argv[2:6], allowlisted_environment)
        self.assertEqual(service_argv[6:], [
            sys.executable, "-B", str(PACKAGE / "scripts" / "durable_watch.py"),
            "reconcile", str(self.run_dir),
        ])
        self.assertEqual(launch_environments[0], {
            "PATH": system_path,
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
            "XDG_RUNTIME_DIR": runtime_dir,
        })
        self.assertFalse(any(secret_url in value for value in launch_environments[0].values()))

        # Exercise the exact env -i prefix with a manager-only unknown key.
        probe_argv = service_argv[:6] + [
            sys.executable,
            "-c",
            "import os; print('\\n'.join(sorted(os.environ)))",
        ]
        probe = subprocess.run(
            probe_argv,
            env={"FOO_UNLISTED_SETTING": unknown_manager_value},
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(probe.stdout.splitlines(), ["HOME", "LANG", "PATH", "XDG_RUNTIME_DIR"])
        self.assertNotIn("FOO_UNLISTED_SETTING", probe.stdout)

        secret_path = self.run_dir / durable_watch.NTFY_URL_FILE
        self.assertEqual(stat.S_IMODE(secret_path.stat().st_mode), 0o600)
        self.assertEqual(secret_path.read_text(encoding="utf-8").strip(), secret_url)
        config_text = (self.run_dir / durable_watch.CONFIG).read_text(encoding="utf-8")
        self.assertNotIn(secret_url, config_text)

    def test_publish_refuses_token_files_without_private_permissions(self):
        """S3: a group/world-readable or foreign-owned token file must fail closed."""
        route = "https://notify.example.invalid/private-topic-test-1159"
        token = "unit-test-token-value"

        def configure(token_file):
            config_path = self.run_dir / durable_watch.CONFIG
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({
                "notify": True,
                "ntfy_url_sha256": hashlib.sha256(route.encode()).hexdigest(),
                "ntfy_url_file": str(self.run_dir / durable_watch.NTFY_URL_FILE),
                "ntfy_token_file": str(token_file),
                "ntfy_token_path_checked": True,
                "notification_title": "Orchestrator worker result",
            })
            durable_watch.write_private_text(self.run_dir / durable_watch.NTFY_URL_FILE, route)
            durable_watch.write_config(config_path, config)
            token_file.write_text(token + "\n", encoding="utf-8")
            (self.task_a / "result.json").write_text('{"outcome":"ready"}\n', encoding="utf-8")

        def reconcile_and_capture():
            output = io.StringIO()
            with redirect_stderr(output):
                outcome = durable_watch.reconcile_once(self.run_dir)
            return outcome, output.getvalue()

        def assert_event_stays_redacted(outcome, errors):
            self.assertEqual(len(outcome["new_events"]), 1)
            self.assertNotIn(token, errors)
            self.assertNotIn(route, errors)
            for line in (self.run_dir / durable_watch.EVENTS).read_text(encoding="utf-8").splitlines():
                self.assertNotIn(token, line)
                self.assertNotIn(route, line)

        # A group/world-readable token file is refused with no secret printed.
        loose = self.run_dir / "loose-token"
        configure(loose)
        loose.chmod(0o644)
        outcome, errors = reconcile_and_capture()
        self.assertIn("ntfy token must be a mode-600 file owned by the current user", errors)
        self.assertNotIn(str(loose), errors)
        assert_event_stays_redacted(outcome, errors)

        # A token file owned by another user is refused before it is read.
        self.setUp()
        foreign = self.run_dir / "foreign-token"
        configure(foreign)
        foreign.chmod(0o600)
        with mock.patch.object(durable_watch.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaises(durable_watch.WatchError) as raised:
                durable_watch.read_private_text(foreign, "ntfy token")
        self.assertIn("owned by the current user", str(raised.exception))
        self.assertNotIn(token, str(raised.exception))

        # A missing token file stays redacted and still records the event.
        self.setUp()
        missing = self.run_dir / "absent-token"
        configure(missing)
        missing.unlink()
        outcome, errors = reconcile_and_capture()
        self.assertIn("ntfy token is unavailable", errors)
        assert_event_stays_redacted(outcome, errors)

    def test_arm_and_read_refuse_a_direct_token_symlink(self):
        (self.run_dir / durable_watch.CONFIG).unlink()
        token = "fake-token-1159"
        target = self.run_dir / "real-token"
        target.write_text(token + "\n", encoding="utf-8")
        target.chmod(0o600)
        override = self.run_dir / "token-override"
        override.symlink_to(target)
        route = "http://127.0.0.1:12345/topic-fake-1159"
        args = type("Args", (), {
            "run_dir": str(self.run_dir),
            "tasks": ["task-a"],
            "interval_seconds": 30,
            "notification_title": "Orchestrator worker result",
        })()

        with mock.patch.dict(os.environ, {"NTFY_URL": route, "NTFY_TOKEN_FILE": str(override)}, clear=True), \
                mock.patch.object(durable_watch, "systemd_property", return_value="not-found"):
            with self.assertRaises(durable_watch.WatchError) as raised:
                durable_watch.command_arm(args)
        self.assertNotIn(token, str(raised.exception))
        self.assertNotIn(route, str(raised.exception))
        self.assertFalse((self.run_dir / durable_watch.CONFIG).exists())
        with self.assertRaises(durable_watch.WatchError) as raised:
            durable_watch.read_private_text(override, "ntfy token")
        self.assertNotIn(token, str(raised.exception))

    def test_arm_and_read_refuse_a_parent_directory_token_symlink(self):
        (self.run_dir / durable_watch.CONFIG).unlink()
        token = "fake-token-1159"
        target_dir = self.run_dir / "real-token-dir"
        target_dir.mkdir()
        target = target_dir / "token"
        target.write_text(token + "\n", encoding="utf-8")
        target.chmod(0o600)
        parent_link = self.run_dir / "token-dir-override"
        parent_link.symlink_to(target_dir, target_is_directory=True)
        override = parent_link / "token"
        route = "http://127.0.0.1:12345/topic-fake-1159"
        args = type("Args", (), {
            "run_dir": str(self.run_dir),
            "tasks": ["task-a"],
            "interval_seconds": 30,
            "notification_title": "Orchestrator worker result",
        })()

        with mock.patch.dict(os.environ, {"NTFY_URL": route, "NTFY_TOKEN_FILE": str(override)}, clear=True), \
                mock.patch.object(durable_watch, "systemd_property", return_value="not-found"):
            with self.assertRaises(durable_watch.WatchError) as raised:
                durable_watch.command_arm(args)
        self.assertNotIn(token, str(raised.exception))
        self.assertNotIn(route, str(raised.exception))
        self.assertFalse((self.run_dir / durable_watch.CONFIG).exists())
        with self.assertRaises(durable_watch.WatchError) as raised:
            durable_watch.read_private_text(override, "ntfy token")
        self.assertNotIn(token, str(raised.exception))

    def test_reconcile_rechecks_token_path_after_arm(self):
        (self.run_dir / durable_watch.CONFIG).unlink()
        route = "http://127.0.0.1:12345/topic-fake-1159"
        token = "fake-token-1159"
        token_file = self.run_dir / "configured-token"
        token_file.write_text(token + "\n", encoding="utf-8")
        token_file.chmod(0o600)
        target = self.run_dir / "later-target-token"
        target.write_text(token + "\n", encoding="utf-8")
        target.chmod(0o600)
        args = type("Args", (), {
            "run_dir": str(self.run_dir),
            "tasks": ["task-a"],
            "interval_seconds": 30,
            "notification_title": "Orchestrator worker result",
        })()

        arm_output = io.StringIO()
        with mock.patch.dict(os.environ, {"NTFY_URL": route, "NTFY_TOKEN_FILE": str(token_file)}, clear=True), \
                mock.patch.object(durable_watch, "systemd_property", return_value="not-found"), \
                mock.patch.object(durable_watch.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                redirect_stdout(arm_output):
            self.assertEqual(durable_watch.command_arm(args), 0)
        token_file.unlink()
        token_file.symlink_to(target)
        (self.task_a / "result.json").write_text('{"outcome":"ready"}\n', encoding="utf-8")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
                durable_watch.urllib.request, "build_opener", side_effect=AssertionError("request must not be sent")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(durable_watch.main(["reconcile", str(self.run_dir)]), 0)

        event_text = (self.run_dir / durable_watch.EVENTS).read_text(encoding="utf-8")
        for sink in (stdout.getvalue(), stderr.getvalue(), event_text):
            self.assertNotIn(route, sink)
            self.assertNotIn(token, sink)
        self.assertIn("ntfy token must not use a symbolic link", stderr.getvalue())

    def test_unmarked_existing_config_cannot_publish_with_a_canonicalized_path(self):
        route = "https://notify.example.invalid/topic-fake-1159"
        token = "fake-token-1159"
        token_file = self.run_dir / "canonical-token"
        token_file.write_text(token + "\n", encoding="utf-8")
        token_file.chmod(0o600)
        config = {
            "notify": True,
            "ntfy_url_sha256": hashlib.sha256(route.encode()).hexdigest(),
            "ntfy_url_file": str(self.run_dir / durable_watch.NTFY_URL_FILE),
            "ntfy_token_file": str(token_file),
        }
        durable_watch.write_private_text(self.run_dir / durable_watch.NTFY_URL_FILE, route)
        output = io.StringIO()
        with mock.patch.object(
                durable_watch.urllib.request, "build_opener", side_effect=AssertionError("request must not be sent")), \
                redirect_stderr(output):
            durable_watch.publish({"task_dir": "task-a", "run_dir": "run"}, config)
        self.assertIn("ntfy token path must be re-armed", output.getvalue())
        self.assertNotIn(route, output.getvalue())
        self.assertNotIn(token, output.getvalue())

    def test_secrets_never_reach_any_runtime_sink(self):
        """SP3: arm and reconcile keep the route and token out of every runtime sink."""
        (self.run_dir / durable_watch.CONFIG).unlink()
        token = "unit-test-sink-token"
        server, received = self.start_ntfy_server(token)
        route = f"http://127.0.0.1:{server.server_port}/test/private-topic-1159"
        token_file = self.run_dir / "sink-token"
        token_file.write_text(token + "\n", encoding="utf-8")
        token_file.chmod(0o600)
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

        arm_output = io.StringIO()
        with mock.patch.dict(os.environ, {"NTFY_URL": route, "NTFY_TOKEN_FILE": str(token_file)}, clear=True), \
                mock.patch.object(durable_watch, "systemd_property", return_value="not-found"), \
                mock.patch.object(durable_watch.subprocess, "run", side_effect=capture_systemd_run), \
                redirect_stdout(arm_output):
            self.assertEqual(durable_watch.command_arm(args), 0)
        config = json.loads((self.run_dir / durable_watch.CONFIG).read_text(encoding="utf-8"))
        self.assertEqual(config["ntfy_token_file"], str(token_file))

        (self.task_a / "result.json").write_text('{"outcome":"ready"}\n', encoding="utf-8")
        reconcile_output = io.StringIO()
        reconcile_errors = io.StringIO()
        with redirect_stdout(reconcile_output), redirect_stderr(reconcile_errors):
            durable_watch.reconcile_once(self.run_dir)
        status_output = io.StringIO()
        with redirect_stdout(status_output):
            self.assertEqual(durable_watch.command_status(args), 0)

        # systemd-run argv and --setenv values carry nothing secret; because the
        # transient unit is built solely from that argv and environment, the
        # generated unit text cannot contain the route or token either.
        self.assertEqual(len(launched), 1)
        argv = launched[0]
        self.assertFalse(any(secret in value for value in argv for secret in (route, token)))
        setenv_values = [value[len("--setenv="):] for value in argv if value.startswith("--setenv=")]
        self.assertEqual(setenv_values, [])
        self.assertFalse(any(secret in value for value in setenv_values for secret in (route, token)))
        environment = launch_environments[0]
        self.assertFalse(any(key.startswith(("NTFY_", "ORCH_WATCH_")) for key in environment))
        self.assertFalse(any(secret in value for value in environment.values() for secret in (route, token)))

        # Stored config, events/flag file lines, journald-bound log lines the
        # script writes, reconcile stdout, and status output stay redacted.
        sinks = {
            "config": (self.run_dir / durable_watch.CONFIG).read_text(encoding="utf-8"),
            "events": "".join((self.run_dir / durable_watch.EVENTS).read_text(encoding="utf-8").splitlines(keepends=True)),
            "journal": reconcile_errors.getvalue(),
            "reconcile_stdout": reconcile_output.getvalue(),
            "arm_stdout": arm_output.getvalue(),
            "status": status_output.getvalue(),
        }
        for flag in (self.run_dir / "flags").iterdir():
            sinks[f"flag:{flag.name}"] = flag.read_text(encoding="utf-8")
        for sink, text in sinks.items():
            self.assertNotIn(route, text, sink)
            self.assertNotIn(token, text, sink)
        self.assertNotIn(token, sinks["journal"])
        self.assertIn("ntfy publish succeeded (HTTP 200); event is recorded", sinks["journal"])
        self.assertEqual(sinks["reconcile_stdout"], "")
        self.assertEqual(received[0][1]["Authorization"], f"Bearer {token}")
        self.assertEqual(received[0][2], f"task-a wrote result.json in {self.run_dir.name}".encode("utf-8"))

    def test_network_exception_does_not_leak_route_or_token(self):
        route = "https://notify.example.invalid/topic-fake-1159"
        token = "fake-token-1159"
        token_file = self.run_dir / "network-token"
        token_file.write_text(token + "\n", encoding="utf-8")
        token_file.chmod(0o600)
        config = json.loads((self.run_dir / durable_watch.CONFIG).read_text(encoding="utf-8"))
        config.update({
            "notify": True,
            "ntfy_url_sha256": hashlib.sha256(route.encode()).hexdigest(),
            "ntfy_url_file": str(self.run_dir / durable_watch.NTFY_URL_FILE),
            "ntfy_token_file": str(token_file),
            "ntfy_token_path_checked": True,
        })
        durable_watch.write_private_text(self.run_dir / durable_watch.NTFY_URL_FILE, route)
        durable_watch.write_config(self.run_dir / durable_watch.CONFIG, config)
        (self.task_a / "result.json").write_text('{"outcome":"ready"}\n', encoding="utf-8")

        class FakeOpener:
            def open(self, request, timeout):
                raise urllib.error.URLError(f"connection failed for {route} using Bearer {token}")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(durable_watch.urllib.request, "build_opener", return_value=FakeOpener()), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(durable_watch.main(["reconcile", str(self.run_dir)]), 0)

        event_text = (self.run_dir / durable_watch.EVENTS).read_text(encoding="utf-8")
        for sink in (stdout.getvalue(), stderr.getvalue(), event_text):
            self.assertNotIn(route, sink)
            self.assertNotIn(token, sink)
        self.assertIn("ntfy publish failed; event is recorded", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

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
