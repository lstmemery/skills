"""Exercise the desktop snapshot without reaching any live path or Herdr socket."""

import json
from pathlib import Path
import subprocess
import tempfile
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


if __name__ == "__main__":
    unittest.main()
