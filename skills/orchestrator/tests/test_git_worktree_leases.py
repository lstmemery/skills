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
        self.git("-C", str(self.repo), "branch", "-m", "main")
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
        self.assertRegex(record["lease_id"], r"^git-worktree-[0-9a-f]{32}$")
        self.assertEqual(record["lease_holder"], "task-1277")
        self.assertEqual(record["base_commit"], self.base)
        worktree = Path(record["path"])
        self.assertTrue(worktree.is_dir())
        self.assertEqual(worktree.parent, self.home / ".local/share/orchestrator/worktrees")
        self.assertEqual(self.git("-C", str(worktree), "rev-parse", "HEAD"), self.base)

        validated = repository_worktree_record(record)
        self.assertEqual(verify_repository_worktree(validated, str(worktree), self.run_git), record)

    def test_list_and_status_report_local_lease_metadata(self):
        acquired = self.acquire(self.base)
        self.assertEqual(acquired.returncode, 0, acquired.stderr)
        record = json.loads(acquired.stdout)
        environment = {**os.environ, "HOME": str(self.home)}

        listed = subprocess.run([sys.executable, str(ALLOCATOR), "list"], cwd=self.root,
                                env=environment, capture_output=True, text=True, timeout=15)
        status = subprocess.run([sys.executable, str(ALLOCATOR), "status", "--lease-id",
                                 record["lease_id"]], cwd=self.root, env=environment,
                                capture_output=True, text=True, timeout=15)

        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(status.returncode, 0, status.stderr)
        listing = json.loads(listed.stdout)["leases"]
        lease = json.loads(status.stdout)["lease"]
        self.assertEqual(len(listing), 1)
        for value in (listing[0], lease):
            self.assertEqual(value["lease_id"], record["lease_id"])
            self.assertEqual(value["lease_holder"], "task-1277")
            self.assertEqual(value["path"], record["path"])
            self.assertTrue(value["created"])
            self.assertIsNone(value["branch"])
            self.assertTrue(value["merged_into_main"])

    def test_release_refuses_unmerged_or_dirty_worktrees_without_force(self):
        unmerged_result = self.acquire(self.base)
        dirty_result = self.acquire(self.base)
        self.assertEqual(unmerged_result.returncode, 0, unmerged_result.stderr)
        self.assertEqual(dirty_result.returncode, 0, dirty_result.stderr)
        unmerged = json.loads(unmerged_result.stdout)
        dirty = json.loads(dirty_result.stdout)
        self.git("-C", unmerged["path"], "switch", "-c", "candidate")
        (Path(unmerged["path"]) / "candidate.txt").write_text("unmerged\n")
        self.git("-C", unmerged["path"], "add", "candidate.txt")
        self.git("-C", unmerged["path"], "commit", "-qm", "candidate")
        (Path(dirty["path"]) / "fixture.txt").write_text("dirty\n")
        environment = {**os.environ, "HOME": str(self.home)}

        refused_unmerged = subprocess.run(
            [sys.executable, str(ALLOCATOR), "release", "--lease-id", unmerged["lease_id"],
             "--who", "test-runner", "--why", "candidate is under review"], cwd=self.root,
            env=environment, capture_output=True, text=True, timeout=15)
        refused_dirty = subprocess.run(
            [sys.executable, str(ALLOCATOR), "release", "--lease-id", dirty["lease_id"],
             "--who", "test-runner", "--why", "candidate is under review"], cwd=self.root,
            env=environment, capture_output=True, text=True, timeout=15)

        self.assertNotEqual(refused_unmerged.returncode, 0)
        self.assertIn("unmerged", refused_unmerged.stderr)
        self.assertTrue(Path(unmerged["path"]).is_dir())
        self.assertNotEqual(refused_dirty.returncode, 0)
        self.assertIn("dirty", refused_dirty.stderr)
        self.assertTrue(Path(dirty["path"]).is_dir())

    def test_forced_release_records_reason_and_is_idempotent(self):
        acquired = self.acquire(self.base)
        self.assertEqual(acquired.returncode, 0, acquired.stderr)
        record = json.loads(acquired.stdout)
        worktree = Path(record["path"])
        self.git("-C", str(worktree), "switch", "-c", "abandoned")
        (worktree / "candidate.txt").write_text("committed candidate\n")
        self.git("-C", str(worktree), "add", "candidate.txt")
        self.git("-C", str(worktree), "commit", "-qm", "candidate")
        (worktree / "uncommitted.txt").write_text("discarded by force\n")
        environment = {**os.environ, "HOME": str(self.home)}
        command = [sys.executable, str(ALLOCATOR), "release", "--lease-id", record["lease_id"],
                   "--who", "coordinator", "--why", "abandoned candidate", "--force"]

        released = subprocess.run(command, cwd=self.root, env=environment, capture_output=True,
                                  text=True, timeout=15)
        repeated = subprocess.run(
            [sys.executable, str(ALLOCATOR), "release", "--lease-id", record["lease_id"],
             "--who", "another-caller", "--why", "different reason", "--force"],
            cwd=self.root, env=environment, capture_output=True, text=True, timeout=15)
        status = subprocess.run(
            [sys.executable, str(ALLOCATOR), "status", "--lease-id", record["lease_id"]],
            cwd=self.root, env=environment, capture_output=True, text=True, timeout=15)

        self.assertEqual(released.returncode, 0, released.stderr)
        first = json.loads(released.stdout)
        self.assertTrue(first["released"])
        self.assertFalse(worktree.exists())
        self.assertEqual(first["release"]["who"], "coordinator")
        self.assertEqual(first["release"]["why"], "abandoned candidate")
        self.assertTrue(first["release"]["forced"])
        self.assertFalse(first["release"]["merged_into_main"])
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertTrue(json.loads(repeated.stdout)["already_released"])
        self.assertEqual(json.loads(repeated.stdout)["release"], first["release"])
        self.assertTrue(json.loads(status.stdout)["lease"]["released"])

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
