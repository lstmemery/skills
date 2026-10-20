import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPTS = PACKAGE / "scripts"
ALLOCATOR = SCRIPTS / "lease-worktree.py"
MANAGED_JOBS = SCRIPTS / "herdr-jobs.py"
sys.path.insert(0, str(SCRIPTS))

from herdr_jobs.records import JobError, repository_worktree_record
from herdr_jobs.worktrees import verify_repository_worktree


class GitWorktreeLeaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", str(self.repo))
        self.git("-C", str(self.repo), "config", "user.name", "Offline Test")
        self.git("-C", str(self.repo), "config", "user.email", "offline@example.invalid")
        (self.repo / "fixture.txt").write_text("base\n")
        self.git("-C", str(self.repo), "add", "fixture.txt")
        self.git("-C", str(self.repo), "commit", "-qm", "fixture base")
        self.base = self.git("-C", str(self.repo), "rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run([shutil.which("git"), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def acquire(self, expected_base=None):
        command = [sys.executable, str(ALLOCATOR), "acquire", "--repo", str(self.repo),
                   "--lease-holder", "task-1277"]
        if expected_base is not None:
            command.extend(["--expected-base", expected_base])
        environment = dict(os.environ)
        environment["HOME"] = str(self.home)
        environment["PATH"] = f"{Path(shutil.which('git')).parent}:/usr/bin:/bin"
        return subprocess.run(command, cwd=self.root, env=environment,
                              capture_output=True, text=True, timeout=15)

    def test_acquire_creates_detached_worktree_and_emits_herdr_record(self):
        result = self.acquire(self.base)

        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(set(record), {"schema_version", "path", "lease_id", "lease_holder",
                                       "repo_root", "git_common_dir", "base_commit"})
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["lease_holder"], "task-1277")
        self.assertEqual(record["base_commit"], self.base)
        worktree = Path(record["path"])
        self.assertTrue(worktree.is_dir())
        self.assertEqual(worktree.parent, self.home / ".local/share/orchestrator/worktrees")
        self.assertEqual(self.git("-C", str(worktree), "rev-parse", "HEAD"), self.base)

        validated = repository_worktree_record(record)
        self.assertEqual(verify_repository_worktree(validated, str(worktree), self.run_git), record)

    def run_git(self, argv, cwd):
        result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=10, check=False)
        return result.returncode, result.stdout, result.stderr

    def test_record_passes_managed_jobs_offline_preview(self):
        acquired = self.acquire(self.base)
        self.assertEqual(acquired.returncode, 0, acquired.stderr)
        record = json.loads(acquired.stdout)
        task_file = self.root / "task.md"
        task_file.write_text("Write a small repository change.\n")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "request_id": "lease-preview",
            "jobs": [{
                "job_id": "writer-1",
                "name": "Writer",
                "task_kind": "ordinary",
                "task_file": str(task_file),
                "cwd": record["path"],
                "output_expectation": "A repository change.",
                "override": {"runtime": "codex", "instruction": "Make the change."},
                "writes_repository": True,
                "repository_worktree": record,
            }],
        }))
        preview = subprocess.run([
            sys.executable, str(MANAGED_JOBS), "run", "--manifest", str(manifest),
            "--policy", str(PACKAGE / "launch-policy.json"),
            "--run-dir", str(self.root / "run"), "--preview",
        ], cwd=self.root, capture_output=True, text=True, timeout=15)

        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["batch_state"], "preview")

    def test_forged_or_unavailable_local_lease_fails_closed(self):
        acquired = self.acquire(self.base)
        self.assertEqual(acquired.returncode, 0, acquired.stderr)
        record = json.loads(acquired.stdout)
        forged = dict(record, lease_holder="another-task")

        with self.assertRaisesRegex(JobError, "local Git worktree lease record"):
            verify_repository_worktree(forged, record["path"], self.run_git)

        git_dir = Path(self.git("-C", record["path"], "rev-parse", "--absolute-git-dir"))
        (git_dir / "orchestrator-lease.json").unlink()
        with self.assertRaisesRegex(JobError, "lease marker is missing"):
            verify_repository_worktree(record, record["path"], self.run_git)

    def test_invalid_expected_base_reports_an_actionable_error_without_record(self):
        result = self.acquire("f" * 40)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("expected base commit is unavailable", result.stderr)
        self.assertFalse((self.home / ".local/share/orchestrator/worktrees").exists())

    def test_invalid_repository_reports_a_clear_error(self):
        empty = self.root / "not-a-repository"
        empty.mkdir()
        result = subprocess.run([
            sys.executable, str(ALLOCATOR), "acquire", "--repo", str(empty),
            "--lease-holder", "task-1277",
        ], cwd=self.root, env={**os.environ, "HOME": str(self.home)},
           capture_output=True, text=True, timeout=15)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("--repo must name the root of the expected Git checkout", result.stderr)


if __name__ == "__main__":
    unittest.main()
