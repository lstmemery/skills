import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
SPEC = importlib.util.spec_from_file_location("native_jail_runner", PACKAGE / "scripts/native-jail-runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class NativeJailRunnerTests(unittest.TestCase):
    def test_runs_fixed_jail_command_without_shell_and_records_exit(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            exit_path = root / "exit.json"
            request = {"schema_version": 1, "job_id": "job1", "attempt_id": "attempt1",
                       "cwd": str(root), "exit_path": str(exit_path),
                       "argv": ["jail-agent", "--harness", "codex", "exec",
                                "--skip-git-repo-check", "change task"]}
            calls = []

            code = runner.execute(request, run_process=lambda argv, **kwargs: (
                calls.append((argv, kwargs)) or SimpleNamespace(returncode=23)))

            self.assertEqual(code, 23)
            self.assertEqual(calls, [(request["argv"], {"cwd": str(root), "check": False})])
            self.assertEqual(runner.load_json(exit_path)["exit_code"], 23)
            self.assertEqual(runner.load_json(exit_path)["attempt_id"], "attempt1")

    def test_rejects_non_jail_command_before_launch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            request = {"schema_version": 1, "job_id": "job1", "attempt_id": "attempt1",
                       "cwd": str(root), "exit_path": str(root / "exit.json"),
                       "argv": ["codex", "exec", "change task"]}
            calls = []

            with self.assertRaisesRegex(runner.JobError, "supported jail-agent"):
                runner.execute(request, run_process=lambda *args, **kwargs: calls.append(args))

            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
