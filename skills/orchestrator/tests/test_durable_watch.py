from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
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

    def test_new_result_uses_the_configured_ntfy_publisher_once(self):
        publisher = self.run_dir / "publisher"
        capture = self.run_dir / "published.txt"
        publisher.write_text("#!/bin/sh\ncat > \"$PUBLISHER_CAPTURE\"\n", encoding="utf-8")
        publisher.chmod(0o755)
        config_path = self.run_dir / durable_watch.CONFIG
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.update({"notify": True, "publisher": str(publisher)})
        durable_watch.write_config(config_path, config)
        (self.task_a / "result.json").write_text('{"outcome":"ready"}\n', encoding="utf-8")

        with mock.patch.dict(os.environ, {
            "NTFY_URL": "https://notify.example.invalid/orchestrator-test",
            "PUBLISHER_CAPTURE": str(capture),
        }):
            first = durable_watch.reconcile_once(self.run_dir)
            second = durable_watch.reconcile_once(self.run_dir)

        self.assertEqual(len(first["new_events"]), 1)
        self.assertEqual(second["new_events"], [])
        self.assertEqual(
            capture.read_text(encoding="utf-8"),
            "Orchestrator worker result\ntask-a wrote result.json in run\n3\n",
        )

    def test_task_paths_cannot_escape_the_run_directory(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        with self.assertRaises(durable_watch.WatchError):
            durable_watch.task_names(self.run_dir, [str(outside)])


if __name__ == "__main__":
    unittest.main()
