import ast
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

from herdr_jobs.records import JobError, digest, is_commit_id, prepare, repository_worktree_record
from herdr_jobs.transport import Deadline, NativeTransport
from herdr_jobs.worktrees import verify_repository_worktree


class WorktreeAllocationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.git_calls = self.root / "git-calls"
        self.real_git = shutil.which("git")
        self.write_executable(
            self.bin / "treehouse",
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "if os.environ.get('TREEHOUSE_CALLS'):\n"
            "    with open(os.environ['TREEHOUSE_CALLS'], 'a') as stream: stream.write(repr(sys.argv[1:]) + '\\n')\n"
            "if os.environ.get('HOST_CALLS'):\n"
            "    with open(os.environ['HOST_CALLS'], 'a') as stream: stream.write(repr(['treehouse', *sys.argv[1:]]) + '\\n')\n"
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
            "if os.environ.get('HOST_CALLS'):\n"
            "    with open(os.environ['HOST_CALLS'], 'a') as stream: stream.write(repr(['git', *sys.argv[1:]]) + '\\n')\n"
            "if os.environ.get('GIT_FAIL_WORKTREE_ADD') and sys.argv[1:3] == ['-C', os.environ.get('FAIL_REPO')] and 'worktree' in sys.argv and 'add' in sys.argv:\n"
            "    sys.stderr.write('fixture refused worktree creation\\n')\n"
            "    raise SystemExit(int(os.environ['GIT_FAIL_WORKTREE_ADD']))\n"
            "raise SystemExit(subprocess.call([os.environ['REAL_GIT'], *sys.argv[1:]]))\n",
        )

    def write_executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def allocate(self, repo, expected_base=None, fail_worktree_add=None):
        env = dict(os.environ)
        env.update({"PATH": f"{self.bin}:{os.defpath}", "HOME": str(self.home),
                    "GIT_CALLS": str(self.git_calls), "TREEHOUSE_CALLS": str(self.root / "treehouse-calls"),
                    "HOST_CALLS": str(self.root / "host-calls"),
                    "REAL_GIT": self.real_git})
        if fail_worktree_add is not None:
            env.update({"GIT_FAIL_WORKTREE_ADD": str(fail_worktree_add), "FAIL_REPO": str(repo)})
        command = [sys.executable, str(SCRIPT), "acquire", "--repo", str(repo),
                   "--lease-holder", "task-1119"]
        if expected_base is not None:
            command.extend(["--expected-base", expected_base])
        return subprocess.run(
            command,
            cwd=self.root, env=env, capture_output=True, text=True, timeout=10,
        )

    def calls(self):
        if not self.git_calls.exists():
            return []
        return [ast.literal_eval(line) for line in self.git_calls.read_text().splitlines()]

    def git(self, *args, cwd=None):
        return subprocess.run([self.real_git, *args], cwd=cwd, check=True,
                              capture_output=True, text=True).stdout.strip()

    def run_git(self, argv, cwd):
        result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=10, check=False)
        return result.returncode, result.stdout, result.stderr

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
        pane_help = b"fixture pane help"
        orchestrator_help = b"fixture orchestrator help"
        adapter.binding = {
            "herdr_help_sha256": digest(help_bytes),
            "herdr_agent_help_sha256": digest(agent_help),
            "herdr_pane_help_sha256": digest(pane_help),
            "orchestrator_help_sha256": digest(orchestrator_help),
            "supported_kinds": ["codex"], "server_version": "fixture-v1",
            "paths": {"session_id": ["server", "socket"],
                      "server_version": ["server", "version"],
                      "agent_state": ["state"], "agent_pane_id": ["pane"],
                      "agent_kind": ["kind"], "agent_name": ["name"]},
            "jail_export": None,
        }
        adapter.calls = []

        def raw(argv):
            adapter.calls.append(argv)
            if argv == ["herdr", "--help"]:
                return help_bytes
            if argv == ["herdr", "agent", "--help"]:
                return agent_help
            if argv == ["herdr", "pane", "--help"]:
                return pane_help
            if argv == ["orchestrator", "help", "--json", "--compact"]:
                return orchestrator_help
            if argv == ["herdr", "status", "--json"]:
                return b'{"server":{"socket":"fixture-socket","version":"fixture-v1"}}'
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

        self.assertEqual(result["session_id"], "fixture-socket")

    def test_legacy_lease_with_git_prefix_keeps_treehouse_verification(self):
        repo, worktree, base = self.git_fixture()
        record = self.repository_worktree_record(
            worktree, repo, base, lease_id="git-worktree-legacy")

        with patch.dict(os.environ, {"HOME": str(self.home)}), \
                patch("herdr_jobs.worktrees._git_identity",
                      return_value=(repo, repo / ".git", base)), \
                patch("herdr_jobs.worktrees._verify_treehouse_lease") as verify_treehouse, \
                patch("herdr_jobs.worktrees._verify_git_lease_marker") as verify_git_marker:
            verified = verify_repository_worktree(record, str(worktree), self.run_git)

        self.assertEqual(verified, record)
        verify_treehouse.assert_called_once_with(record, worktree, self.run_git, "treehouse")
        verify_git_marker.assert_not_called()

    def test_managed_writer_accepts_allocator_lease_before_launch(self):
        repo, _, base = self.git_fixture()
        acquired = self.allocate(repo, expected_base=base)
        self.assertEqual(acquired.returncode, 0, acquired.stderr)
        record = json.loads(acquired.stdout)
        job = {"job_id": "job0", "cwd": record["path"], "kind": "codex", "model": None,
               "writes_repository": True,
               "route": {"mode": "agent", "runtime": "codex"},
               "repository_worktree": record}
        adapter = self.writer_adapter()
        env = {"PATH": f"{self.bin}:{os.defpath}", "HOME": str(self.home),
               "GIT_CALLS": str(self.git_calls), "REAL_GIT": self.real_git}

        with patch.dict(os.environ, env):
            result = adapter.preflight({"jobs": [job]})

        self.assertEqual(result["session_id"], "fixture-socket")
        self.assertFalse(any(call[:3] == ["herdr", "pane", "split"] for call in adapter.calls))
        self.assertFalse((self.root / "treehouse-calls").exists())

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

    def test_empty_repository_stops_before_worktree_creation(self):
        repo = self.root / "empty-repo"
        repo.mkdir()
        self.git("init", "-q", str(repo))

        result = self.allocate(repo)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("no readable HEAD commit", result.stderr)
        self.assertFalse(any("worktree" in call and "add" in call for call in self.calls()))

    def test_failed_git_allocation_emits_no_lease_record_or_verification_calls(self):
        repo, _, base = self.git_fixture()
        result = self.allocate(repo, expected_base=base, fail_worktree_add=1)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("fixture refused worktree creation", result.stderr)
        calls = self.calls()
        self.assertTrue(any("worktree" in call and "add" in call for call in calls))
        self.assertFalse(any("rev-parse" in call and "--absolute-git-dir" in call for call in calls))

    def test_plain_path_is_not_accepted_as_a_lease_record(self):
        calls = []
        with self.assertRaisesRegex(JobError, "repository_worktree: expected an object"):
            verify_repository_worktree(str(self.root), str(self.root),
                                       lambda argv, cwd: calls.append((argv, cwd)))
        self.assertEqual(calls, [])

    def test_lease_record_missing_identity_is_rejected_before_git(self):
        repo, worktree, base = self.git_fixture()
        record = self.repository_worktree_record(worktree, repo, base)
        del record["lease_id"]
        calls = []
        with self.assertRaisesRegex(JobError, r"missing \['lease_id'\]"):
            verify_repository_worktree(record, str(worktree),
                                       lambda argv, cwd: calls.append((argv, cwd)))
        self.assertEqual(calls, [])

    def test_git_lease_never_selects_main_checkout_or_home(self):
        repo, _, base = self.git_fixture()
        result = self.allocate(repo, expected_base=base)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        leased_path = Path(record["path"]).resolve()
        self.assertNotEqual(leased_path, repo.resolve())
        self.assertNotEqual(leased_path, self.home.resolve())

        for unsafe_path in (repo, self.home):
            with self.subTest(path=unsafe_path):
                forged = dict(record, path=str(unsafe_path.resolve()))
                calls = []
                with patch.dict(os.environ, {"HOME": str(self.home)}):
                    with self.assertRaisesRegex(JobError, "not home or the expected checkout"):
                        verify_repository_worktree(forged, str(unsafe_path),
                                                   lambda argv, cwd: calls.append((argv, cwd)))
                self.assertEqual(calls, [])

    def test_valid_lease_emits_repo_base_and_linked_worktree_identity(self):
        repo, worktree, base = self.git_fixture()
        result = self.allocate(repo, expected_base=base)

        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(set(record), {"schema_version", "path", "lease_id", "lease_holder",
                                       "repo_root", "git_common_dir", "base_commit"})
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["path"], str(Path(record["path"]).resolve()))
        self.assertRegex(record["lease_id"], r"^git-worktree-[0-9a-f]{32}$")
        self.assertEqual(record["lease_holder"], "task-1119")
        self.assertEqual(record["repo_root"], str(repo.resolve()))
        self.assertEqual(record["git_common_dir"], str((repo / ".git").resolve()))
        self.assertEqual(record["base_commit"], base)
        self.assertEqual(verify_repository_worktree(repository_worktree_record(record),
                                                    record["path"], self.run_git), record)
        self.assertFalse((self.root / "treehouse-calls").exists())


if __name__ == "__main__":
    unittest.main()
