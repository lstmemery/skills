import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/lease-worktree.py"
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.records import JobError, digest, is_commit_id, prepare
from herdr_jobs.transport import Deadline, NativeTransport


class WorktreeAllocationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.git_calls = self.root / "git-calls"
        self.real_git = shutil.which("git")
        self.write_executable(
            self.bin / "treehouse",
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "if sys.argv[1:] == ['status', '--json']:\n"
            "    sys.stdout.write(os.environ.get('STATUS_OUTPUT', ''))\n"
            "    raise SystemExit(0)\n"
            "if sys.argv[1:2] == ['get']:\n"
            "    sys.stdout.write(os.environ.get('GET_OUTPUT', ''))\n"
            "    raise SystemExit(int(os.environ.get('GET_STATUS', '0')))\n"
            "raise SystemExit(2)\n",
        )
        self.write_executable(
            self.bin / "git",
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            "with open(os.environ['GIT_CALLS'], 'a') as stream: stream.write(repr(sys.argv[1:]) + '\\n')\n"
            "raise SystemExit(subprocess.call([os.environ['REAL_GIT'], *sys.argv[1:]]))\n",
        )

    def write_executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def allocate(self, output="", status=0, status_output="", repo=None, expected_base="a" * 40):
        env = dict(os.environ)
        env.update({"PATH": f"{self.bin}:{os.defpath}", "GET_OUTPUT": output,
                    "GET_STATUS": str(status), "STATUS_OUTPUT": status_output,
                    "GIT_CALLS": str(self.git_calls), "REAL_GIT": self.real_git})
        return subprocess.run(
            [sys.executable, str(SCRIPT), "acquire", "--repo", str(repo or self.root),
             "--lease-holder", "task-1119", "--expected-base", expected_base],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=10,
        )

    def git(self, *args, cwd=None):
        return subprocess.run([self.real_git, *args], cwd=cwd, check=True,
                              capture_output=True, text=True).stdout.strip()

    def git_fixture(self):
        repo = self.root / "repo"
        worktree = self.root / "leased worktree"
        repo.mkdir()
        self.git("init", "-q", str(repo))
        self.git("-C", str(repo), "config", "user.name", "Offline Test")
        self.git("-C", str(repo), "config", "user.email", "offline@example.invalid")
        (repo / "fixture.txt").write_text("base\n")
        self.git("-C", str(repo), "add", "fixture.txt")
        self.git("-C", str(repo), "commit", "-qm", "fixture base")
        base = self.git("-C", str(repo), "rev-parse", "HEAD")
        self.git("-C", str(repo), "worktree", "add", "-qb", "leased", str(worktree), base)
        return repo, worktree, base

    def repository_worktree_record(self, path, repo, base_commit,
                                   lease_id="lease-fixture", lease_holder="task-1119"):
        return {"schema_version": 1, "path": str(Path(path).resolve()),
                "lease_id": lease_id, "lease_holder": lease_holder,
                "repo_root": str(Path(repo).resolve()),
                "git_common_dir": str((Path(repo) / ".git").resolve()),
                "base_commit": base_commit}

    def treehouse_records(self, path, lease_id="lease-fixture", holder="task-1119"):
        path = str(path)
        allocation = {"path": path, "lease_id": lease_id, "lease_holder": holder,
                      "name": "slot-1", "leased_at": "2026-09-30T00:00:00Z"}
        status = [{"name": "free-slot", "path": str(self.root / "free-slot"),
                   "status": "available", "flavor": "git", "lease_id": "",
                   "lease_holder": "", "leased_at": None, "processes": []},
                  {"name": "slot-1", "path": path, "status": "leased", "flavor": "git",
                   "lease_id": lease_id, "lease_holder": holder,
                   "leased_at": "2026-09-30T00:00:00Z", "processes": []}]
        return json.dumps(allocation), json.dumps(status)

    def writer_adapter(self):
        adapter = object.__new__(NativeTransport)
        adapter.herdr = "herdr"
        adapter.orchestrator = "orchestrator"
        adapter.deadline = Deadline(2)
        help_bytes = b"fixture herdr help"
        agent_help = b"fixture agent help"
        orchestrator_help = b"fixture orchestrator help"
        adapter.binding = {
            "herdr_help_sha256": digest(help_bytes),
            "herdr_agent_help_sha256": digest(agent_help),
            "orchestrator_help_sha256": digest(orchestrator_help),
            "supported_kinds": ["codex"], "server_version": "fixture-v1",
            "paths": {"session_id": ["session"], "server_version": ["version"],
                      "agent_state": ["state"], "agent_pane_id": ["pane"],
                      "agent_kind": ["kind"], "agent_name": ["name"]},
            "jail_export": None,
        }
        adapter.calls = []

        def raw(argv):
            adapter.calls.append(argv)
            if argv == ["herdr", "--help"]:
                return help_bytes
            if argv == ["herdr", "agent"]:
                return agent_help
            if argv == ["orchestrator", "help", "--json", "--compact"]:
                return orchestrator_help
            if argv == ["herdr", "status"]:
                return b'{"session":"fixture-session","version":"fixture-v1"}'
            if argv[:2] == ["orchestrator", "doctor"]:
                return b'{"runtimeSummary":{"availableIds":["codex"]}}'
            return b'{"result":{}}'

        adapter.raw = raw
        return adapter

    def test_managed_writer_rejects_main_checkout_before_launch(self):
        repo, _, base = self.git_fixture()
        record = self.repository_worktree_record(repo, repo, base)
        job = {"job_id": "job0", "cwd": str(repo), "kind": "codex", "model": None,
               "writes_repository": True,
               "route": {"mode": "agent", "runtime": "codex"},
               "repository_worktree": record}
        adapter = self.writer_adapter()

        with self.assertRaisesRegex(JobError, "linked worktree"):
            adapter.preflight({"jobs": [job]})

        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in adapter.calls))

    def test_managed_writer_accepts_matching_linked_worktree_lease_and_base(self):
        repo, worktree, base = self.git_fixture()
        lease, status = self.treehouse_records(worktree)
        lease = json.loads(lease)
        record = self.repository_worktree_record(worktree, repo, base,
                                                 lease["lease_id"], lease["lease_holder"])
        job = {"job_id": "job0", "cwd": str(worktree), "kind": "codex", "model": None,
               "writes_repository": True,
               "route": {"mode": "agent", "runtime": "codex"},
               "repository_worktree": record}
        adapter = self.writer_adapter()
        env = {"PATH": f"{self.bin}:{os.defpath}", "STATUS_OUTPUT": status,
               "GIT_CALLS": str(self.git_calls), "REAL_GIT": self.real_git}

        with patch.dict(os.environ, env):
            result = adapter.preflight({"jobs": [job]})

        self.assertEqual(result["session_id"], "fixture-session")

    def test_managed_writer_rejects_stale_lease_before_launch(self):
        repo, worktree, base = self.git_fixture()
        lease, status = self.treehouse_records(worktree)
        lease = json.loads(lease)
        record = self.repository_worktree_record(worktree, repo, base,
                                                 lease["lease_id"], lease["lease_holder"])
        status_data = json.loads(status)
        status_data[1]["lease_id"] = "different-lease"
        job = {"job_id": "job0", "cwd": str(worktree), "kind": "codex", "model": None,
               "writes_repository": True,
               "route": {"mode": "agent", "runtime": "codex"},
               "repository_worktree": record}
        adapter = self.writer_adapter()

        with patch.dict(os.environ, {"PATH": f"{self.bin}:{os.defpath}",
                                     "STATUS_OUTPUT": json.dumps(status_data),
                                     "GIT_CALLS": str(self.git_calls), "REAL_GIT": self.real_git}):
            with self.assertRaisesRegex(JobError, "active lease"):
                adapter.preflight({"jobs": [job]})

        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in adapter.calls))

    def test_managed_writer_rejects_worktree_head_that_moved_from_recorded_base(self):
        repo, worktree, base = self.git_fixture()
        lease, status = self.treehouse_records(worktree)
        lease = json.loads(lease)
        record = self.repository_worktree_record(worktree, repo, "0" * 40,
                                                 lease["lease_id"], lease["lease_holder"])
        job = {"job_id": "job0", "cwd": str(worktree), "kind": "codex", "model": None,
               "writes_repository": True,
               "route": {"mode": "agent", "runtime": "codex"},
               "repository_worktree": record}
        adapter = self.writer_adapter()

        with patch.dict(os.environ, {"PATH": f"{self.bin}:{os.defpath}",
                                     "STATUS_OUTPUT": status, "GIT_CALLS": str(self.git_calls),
                                     "REAL_GIT": self.real_git}):
            with self.assertRaisesRegex(JobError, "recorded base commit"):
                adapter.preflight({"jobs": [job]})

        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in adapter.calls))

    def test_writer_without_record_is_rejected_before_git_or_launch(self):
        job = {"job_id": "job0", "cwd": str(self.root), "kind": "codex", "model": None,
               "writes_repository": True, "route": {"mode": "agent", "runtime": "codex"}}
        adapter = self.writer_adapter()

        with patch("herdr_jobs.transport.command") as run_command:
            with self.assertRaisesRegex(JobError, "writes_repository=true requires repository_worktree"):
                adapter.preflight({"jobs": [job]})

        run_command.assert_not_called()
        self.assertEqual(adapter.calls, [])

    def test_writer_with_mismatched_record_is_rejected_before_git_or_launch(self):
        repo, worktree, base = self.git_fixture()
        record = self.repository_worktree_record(worktree, repo, base)
        job = {"job_id": "job0", "cwd": str(repo), "kind": "codex", "model": None,
               "writes_repository": True, "route": {"mode": "agent", "runtime": "codex"},
               "repository_worktree": record}
        adapter = self.writer_adapter()

        with patch("herdr_jobs.transport.command") as run_command:
            with self.assertRaisesRegex(JobError, "writer cwd does not match"):
                adapter.preflight({"jobs": [job]})

        run_command.assert_not_called()
        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in adapter.calls))

    def test_writer_manifest_cannot_pair_cwd_with_a_different_recorded_path(self):
        task = self.root / "task.md"
        task.write_text("Write a report.\n")
        record = self.repository_worktree_record(self.root / "different-worktree", self.root,
                                                 "a" * 40)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "request_id": "writer-check",
            "jobs": [{"job_id": "job0", "name": "Writer", "task_kind": "ordinary",
                      "task_file": str(task), "cwd": str(self.root),
                      "output_expectation": "A report.",
                      "override": {"runtime": "codex", "instruction": "Write the report."},
                      "writes_repository": True,
                      "repository_worktree": record}]}))

        with self.assertRaisesRegex(JobError, "cwd must equal repository_worktree.path"):
            prepare(manifest, PACKAGE / "launch-policy.json")

    def test_writer_manifest_uses_canonical_cwd_for_a_symlink_alias(self):
        repo, worktree, base = self.git_fixture()
        alias = self.root / "worktree-alias"
        alias.symlink_to(worktree, target_is_directory=True)
        task = self.root / "task.md"
        task.write_text("Write a report.\n")
        record = self.repository_worktree_record(worktree, repo, base)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "request_id": "writer-canonical",
            "jobs": [{"job_id": "job0", "name": "Writer", "task_kind": "ordinary",
                      "task_file": str(task), "cwd": str(alias),
                      "output_expectation": "A report.",
                      "override": {"runtime": "codex", "instruction": "Write the report."},
                      "writes_repository": True,
                      "repository_worktree": record}]}))

        prepared = prepare(manifest, PACKAGE / "launch-policy.json")

        self.assertEqual(prepared["jobs"][0]["cwd"], str(worktree.resolve()))

    def test_repository_writer_manifest_requires_explicit_worktree_record(self):
        task = self.root / "task.md"
        task.write_text("Write a report.\n")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "request_id": "writer-missing-record",
            "jobs": [{"job_id": "job0", "name": "Writer", "task_kind": "ordinary",
                      "task_file": str(task), "cwd": str(self.root),
                      "output_expectation": "A report.", "writes_repository": True,
                      "override": {"runtime": "codex", "instruction": "Write the report."}}]}))

        with self.assertRaisesRegex(JobError, "writes_repository=true requires repository_worktree"):
            prepare(manifest, PACKAGE / "launch-policy.json")

    def test_shared_commit_id_validator_accepts_supported_full_ids_only(self):
        self.assertTrue(is_commit_id("a" * 40))
        self.assertTrue(is_commit_id("f" * 64))
        self.assertFalse(is_commit_id("a" * 39))
        self.assertFalse(is_commit_id("a" * 65))
        self.assertFalse(is_commit_id("A" * 40))
        self.assertFalse(is_commit_id(None))

    def test_empty_allocation_stops_before_git(self):
        result = self.allocate()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.git_calls.exists())

    def test_failed_allocation_stops_before_git(self):
        result = self.allocate(output=json.dumps({"path": str(self.root), "lease_id": "lease",
                                                 "lease_holder": "task-1119"}), status=1)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.git_calls.exists())

    def test_plain_path_is_not_accepted_as_a_lease_record(self):
        result = self.allocate(output=str(self.root))

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.git_calls.exists())

    def test_lease_record_missing_identity_is_rejected_before_git(self):
        result = self.allocate(output=json.dumps({"path": str(self.root),
                                                  "lease_holder": "task-1119"}))

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.git_calls.exists())

    def test_broken_slot_cannot_select_main_checkout_or_home(self):
        repo, _, base = self.git_fixture()
        for path in (repo, Path.home()):
            with self.subTest(path=path):
                self.git_calls.unlink(missing_ok=True)
                output, status = self.treehouse_records(path)
                result = self.allocate(output, status_output=status, repo=repo, expected_base=base)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse(self.git_calls.exists())

    def test_valid_lease_emits_repo_base_and_linked_worktree_identity(self):
        repo, worktree, base = self.git_fixture()
        output, status = self.treehouse_records(worktree)
        result = self.allocate(output, status_output=status, repo=repo, expected_base=base)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": 1,
            "path": str(worktree.resolve()),
            "lease_id": "lease-fixture",
            "lease_holder": "task-1119",
            "repo_root": str(repo.resolve()),
            "git_common_dir": str((repo / ".git").resolve()),
            "base_commit": base,
        })


if __name__ == "__main__":
    unittest.main()
