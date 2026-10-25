"""Exercise the desktop snapshot without reaching any live path or Herdr socket."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "supervision"
LIVE_SUPERVISION = "/home/deadhand/.local/share/orchestrator/supervision"


class WakeCoordinatorTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.queue = self.root / "wake-queue.txt"
        self.queue.write_text("job a DONE\n", encoding="utf-8")
        (self.root / "coordinator.json").write_text(json.dumps({
            "pane_id": "w1:p1", "agent": "claude", "session": "successor-session",
        }), encoding="utf-8")
        self.agent = {
            "pane_id": "w1:p1", "agent": "claude", "agent_status": "idle",
            "agent_session": {"value": "successor-session"},
        }

    def run_wake(self, agents, prompt_code=0):
        source = (SCRIPTS / "wake-coordinator.py").read_text(encoding="utf-8")
        self.assertEqual(source.count(LIVE_SUPERVISION), 1)
        source = source.replace(LIVE_SUPERVISION, str(self.root))
        responses = [
            subprocess.CompletedProcess([], 0, json.dumps({"result": {"agents": agents}})),
            subprocess.CompletedProcess([], prompt_code, ""),
        ]
        with mock.patch("subprocess.run", side_effect=responses) as calls:
            try:
                exec(compile(source, str(SCRIPTS / "wake-coordinator.py"), "exec"), {})
            except SystemExit as exc:
                self.assertIn(exc.code, (None, 0))
        return calls.call_args_list

    def test_only_exact_idle_identity_receives_last_eight_events(self):
        self.queue.write_text("\n".join(f"event-{n}" for n in range(10)), encoding="utf-8")
        calls = self.run_wake([self.agent])
        self.assertEqual(len(calls), 2)
        command = calls[1].args[0]
        self.assertEqual(command[:4], ["herdr", "agent", "prompt", "w1:p1"])
        self.assertNotIn("event-1;", command[4])
        self.assertIn("event-2;", command[4])
        self.assertIn("event-9", command[4])
        self.assertIn("Not user input.", command[4])
        self.assertEqual(self.queue.read_text(encoding="utf-8"), "")
        self.assertIn("event-9", (self.root / "wake-sent.log").read_text(encoding="utf-8"))

    def test_busy_missing_or_replaced_identity_preserves_queue(self):
        variants = [[], [dict(self.agent, agent_status="working")],
                    [dict(self.agent, pane_id="w2:p1")],
                    [dict(self.agent, agent="codex")],
                    [dict(self.agent, agent_session={"value": "retired-session"})]]
        for agents in variants:
            with self.subTest(agents=agents):
                self.assertEqual(len(self.run_wake(agents)), 1)
                self.assertEqual(self.queue.read_text(encoding="utf-8"), "job a DONE\n")

    def test_prompt_failure_preserves_queue(self):
        self.assertEqual(len(self.run_wake([self.agent], prompt_code=1)), 2)
        self.assertEqual(self.queue.read_text(encoding="utf-8"), "job a DONE\n")
        self.assertFalse((self.root / "wake-sent.log").exists())

    def test_empty_queue_makes_no_host_calls(self):
        self.queue.write_text("", encoding="utf-8")
        self.assertEqual(self.run_wake([]), [])


HELPER_STUB = '''import json, os, pathlib, sys
root = pathlib.Path(os.environ["HB_FIXTURE_DIR"])
cfg = json.loads((root / "fixture.json").read_text())
operation = sys.argv[1]
with (root / "calls.jsonl").open("a") as output:
    output.write(json.dumps(["helper", *sys.argv[1:]]) + "\\n")
if operation == "status":
    print("job | pane | state")
    print("--- | --- | ---")
    for job, pane in cfg["jobs"].items():
        print(f"{job} | {pane} | working")
elif operation == "add":
    manifest = json.loads(pathlib.Path(sys.argv[sys.argv.index("--manifest") + 1]).read_text())
    job = manifest["jobs"][0]["job_id"]
    cfg["jobs"][job] = f"w-{job}:p1"
    (root / "fixture.json").write_text(json.dumps(cfg))
    print("{}")
elif operation == "finish":
    print(json.dumps({"jobs": [{"workspace_closed": cfg["closed"]} for _ in cfg["jobs"]]}))
else:
    print("{}")
'''

HERDR_STUB = '''#!/usr/bin/python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["HB_FIXTURE_DIR"])
with (root / "calls.jsonl").open("a") as output:
    output.write(json.dumps(["herdr", *sys.argv[1:]]) + "\\n")
if sys.argv[1:3] == ["pane", "read"]:
    print(json.loads((root / "fixture.json").read_text())["pane_text"])
'''

SLEEP_STUB = '''#!/usr/bin/python3
import json, os, pathlib, sys
with (pathlib.Path(os.environ["HB_FIXTURE_DIR"]) / "calls.jsonl").open("a") as output:
    output.write(json.dumps(["sleep", *sys.argv[1:]]) + "\\n")
'''


class HeartbeatTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sup = self.root / "supervision"
        self.sup.mkdir()
        self.run = self.root / "batch"
        (self.run / "run").mkdir(parents=True)
        (self.run / "run" / "state.json").write_text('{"request_id":"test-batch"}', encoding="utf-8")
        (self.sup / "active-runs.txt").write_text(str(self.run) + "\n", encoding="utf-8")
        (self.sup / "wake-coordinator.py").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.sup / "wake-coordinator.py").chmod(0o755)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, source in (("herdr", HERDR_STUB), ("sleep", SLEEP_STUB)):
            path = self.bin / name
            path.write_text(source, encoding="utf-8")
            path.chmod(0o755)
        helper = self.root / "helper.py"
        helper.write_text(HELPER_STUB, encoding="utf-8")
        source = (SCRIPTS / "codex-heartbeat.sh").read_text(encoding="utf-8")
        replacements = {
            LIVE_SUPERVISION: str(self.sup),
            "/home/deadhand/.claude/skills/orchestrator/scripts/herdr-jobs.py": str(helper),
            "/home/deadhand/.claude/skills/orchestrator/launch-policy.json": str(self.root / "policy.json"),
            "/home/deadhand/.config/orchestrator/host-contract.json": str(self.root / "host-contract.json"),
            "/home/deadhand/.config/herdr/herdr.sock": str(self.root / "unused.sock"),
            "/home/deadhand/.local/share/orchestrator/worktrees": str(self.root / "worktrees"),
            "export PATH=/home/deadhand/.local/bin:/home/deadhand/.local/npm-global/bin:/usr/local/bin:/usr/bin:/bin":
                f"export PATH={self.bin}:/usr/bin:/bin",
        }
        for old, new in replacements.items():
            self.assertIn(old, source)
            source = source.replace(old, new)
        self.assertNotIn("/home/deadhand", source.replace(str(self.root), "<fixture>"))
        self.script = self.root / "heartbeat.sh"
        self.script.write_text(source, encoding="utf-8")
        self.fixture = {"jobs": {"a": "w1:p1"}, "pane_text": "ready", "closed": False}

    def pass_once(self):
        (self.root / "fixture.json").write_text(json.dumps(self.fixture), encoding="utf-8")
        result = subprocess.run(["bash", str(self.script)], cwd=self.root,
                                env={**os.environ, "HB_FIXTURE_DIR": str(self.root)},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.fixture = json.loads((self.root / "fixture.json").read_text(encoding="utf-8"))

    def calls(self):
        return [json.loads(line) for line in
                (self.root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]

    def backlog(self):
        (self.run / "ORDER").write_text("b\nc\nd\ne\n", encoding="utf-8")
        (self.run / "backlog").mkdir()
        for job in "bcde":
            (self.run / "backlog" / f"task-{job}.md").write_text("Approved fake brief", encoding="utf-8")

    def test_resume_labels_and_feeds_order_only_to_cap_four(self):
        self.backlog()
        self.pass_once()
        self.assertEqual(list(self.fixture["jobs"]), ["a", "b", "c", "d"])
        calls = self.calls()
        self.assertEqual(calls[0][:2], ["helper", "resume"])
        self.assertIn(["herdr", "workspace", "rename", "w1", "codex · batch a"], calls)
        self.assertFalse((self.run / "add-e.json").exists())

    def test_quota_wait_blocks_backlog_then_text_delay_enter(self):
        self.backlog()
        self.fixture["pane_text"] = "You've hit your usage limit."
        until = self.run / "quota-a.until"
        until.write_text(str(int(time.time()) + 3600), encoding="utf-8")
        self.pass_once()
        self.assertEqual(list(self.fixture["jobs"]), ["a"])
        self.assertFalse(any(call[:3] == ["herdr", "pane", "send-text"] for call in self.calls()))
        until.write_text(str(int(time.time()) - 301), encoding="utf-8")
        self.pass_once()
        calls = self.calls()
        index = next(i for i, call in enumerate(calls) if call[:3] == ["herdr", "pane", "send-text"])
        self.assertEqual(calls[index][3], "w1:p1")
        self.assertEqual(calls[index + 1], ["sleep", "2"])
        self.assertEqual(calls[index + 2], ["herdr", "pane", "send-keys", "w1:p1", "Enter"])
        self.assertFalse(until.exists())

    def test_finish_retries_until_every_workspace_closed(self):
        (self.run / "out-a").mkdir()
        (self.run / "out-a" / "DONE").touch()
        self.pass_once()
        self.assertEqual((self.sup / "active-runs.txt").read_text(encoding="utf-8"), str(self.run) + "\n")
        self.assertTrue((self.run / "out-a" / ".woke").exists())
        self.fixture["closed"] = True
        self.pass_once()
        self.assertEqual((self.sup / "active-runs.txt").read_text(encoding="utf-8"), "")
        self.assertEqual(sum(call[:2] == ["helper", "finish"] for call in self.calls()), 2)
        self.assertEqual((self.sup / "wake-queue.txt").read_text(encoding="utf-8").count("a DONE"), 1)

    def test_snapshot_shell_syntax(self):
        result = subprocess.run(["bash", "-n", str(SCRIPTS / "codex-heartbeat.sh")],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
