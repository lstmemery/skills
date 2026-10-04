import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts/worker-records.py"
FIXTURES = PACKAGE / "tests/fixtures/worker-records"


class WorkerRecordsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def call(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, args)],
            capture_output=True,
            text=True,
            cwd=self.root,
            timeout=5,
        )

    def make_result(self, **overrides):
        record = {
            "task_id": "worker-a",
            "assignment_revision": 1,
            "outcome": "ready",
            "summary": "A report is ready.",
            "artifacts": [{"path": "report.md", "kind": "report"}],
            "checks": [{"name": "source check", "status": "passed", "evidence": "checks.log"}],
            "unresolved": [],
            "next_action": "Present the report.",
        }
        record.update(overrides)
        return record

    def test_schema_command_prints_the_result_contract_from_validator(self):
        process = self.call("schema", "--format", "md")

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('"path": "artifact-path"', process.stdout)
        self.assertIn('"kind": "report"', process.stdout)
        for value in ("`ready`", "`blocked`", "`failed`", "`passed`", "`not_run`"):
            self.assertIn(value, process.stdout)
        self.assertIn("candidate", process.stdout)
        self.assertIn("--repository-changes", process.stdout)

    def test_normalize_result_repairs_known_legacy_shapes_and_reports_changes(self):
        source = self.root / "result.json"
        output = self.root / "normalized.json"
        source.write_text(json.dumps(self.make_result(
            candidate=None,
            outcome="complete",
            checks=["unit tests"],
            artifacts=[{"path": "report.md"}],
        )))

        process = self.call(
            "normalize-result", source, "--output", output,
            "--task-id", "worker-a", "--revision", "1",
        )

        self.assertEqual(process.returncode, 0, process.stderr)
        original = json.loads(source.read_text())
        self.assertIsNone(original["candidate"])
        self.assertEqual(original["outcome"], "complete")
        normalized = json.loads(output.read_text())
        self.assertNotIn("candidate", normalized)
        self.assertEqual(normalized["outcome"], "ready")
        self.assertEqual(normalized["artifacts"], [{"path": "report.md", "kind": "artifact"}])
        self.assertEqual(normalized["checks"][0]["name"], "unit tests")
        self.assertEqual(normalized["checks"][0]["status"], "not_run")
        audit = json.loads(process.stdout)
        self.assertEqual(len(audit["changes"]), 4)
        self.assertIn("candidate", audit["changes"][0]["path"])
        self.assertEqual(audit["output"], str(output))

        validated = self.call(
            "validate-result", output,
            "--task-id", "worker-a", "--revision", "1",
        )
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_normalize_result_rejects_unrecognized_outcomes_without_writing_output(self):
        source = self.root / "result.json"
        output = self.root / "normalized.json"
        source.write_text(json.dumps(self.make_result(outcome="done")))

        process = self.call(
            "normalize-result", source, "--output", output,
            "--task-id", "worker-a", "--revision", "1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("outcome must be one of", process.stderr)
        self.assertFalse(output.exists())

    def test_normalize_result_rejects_malformed_outcome_without_traceback(self):
        source = self.root / "result.json"
        output = self.root / "normalized.json"
        source.write_text(json.dumps(self.make_result(outcome=[])))

        process = self.call(
            "normalize-result", source, "--output", output,
            "--task-id", "worker-a", "--revision", "1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("outcome", process.stderr)
        self.assertNotIn("Traceback", process.stderr)
        self.assertFalse(output.exists())

    def test_normalize_result_still_requires_candidate_for_repository_changes(self):
        source = self.root / "result.json"
        output = self.root / "normalized.json"
        source.write_text(json.dumps(self.make_result(candidate=None)))

        process = self.call(
            "normalize-result", source, "--output", output,
            "--task-id", "worker-a", "--revision", "1", "--repository-changes",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("candidate", process.stderr)
        self.assertFalse(output.exists())

    def test_normalize_result_preserves_already_valid_records(self):
        source = self.root / "result.json"
        output = self.root / "normalized.json"
        record = self.make_result(candidate={
            "repo": "example/project",
            "branch": "feature/result-contract",
            "base": "base-sha",
            "head": "head-sha",
        })
        source.write_text(json.dumps(record))

        process = self.call(
            "normalize-result", source, "--output", output,
            "--task-id", "worker-a", "--revision", "1",
        )

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(output.read_text()), record)
        self.assertEqual(json.loads(process.stdout)["changes"], [])

    def write_roster(self, run_dir, workers):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "workers.json").write_text(json.dumps({"workers": workers}))

    def roster_entry(self, task_id="worker-a", directory=None, revision=1):
        return {
            "task_id": task_id,
            "assignment_revision": revision,
            "directory": directory or f"tasks/{task_id}",
        }

    def register_completed_worker(self, run_dir, task_id="worker-a"):
        """Roster the worker under tasks/ and record a completed disposition."""
        worker_dir = run_dir / "tasks" / task_id
        worker_dir.mkdir(parents=True)
        (worker_dir / "disposition.json").write_text(json.dumps({
            "task_id": task_id,
            "assignment_revision": 1,
            "disposition": "completed",
            "summary": "Finished.",
            "evidence": "result.json",
        }))
        self.write_roster(run_dir, [self.roster_entry(task_id)])
        return worker_dir

    def git(self, *args, cwd):
        return subprocess.run(
            ["git", *map(str, args)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )

    def make_git_checkout(self, directory):
        """Create a real git checkout with one commit at the given path."""
        directory.mkdir(parents=True)
        self.git("init", "--quiet", cwd=directory)
        (directory / "README.md").write_text("Copied repository material.\n")
        self.git("add", "README.md", cwd=directory)
        self.git(
            "-c", "user.name=Closeout Tests",
            "-c", "user.email=closeout-tests@example.invalid",
            "commit", "--quiet", "-m", "Captured copy", cwd=directory,
        )

    def make_git_worktree(self, repository, directory):
        """Register a real linked git worktree of the repository at the path."""
        self.git("worktree", "add", "--quiet", str(directory), cwd=repository)

    def make_docs_symlink(self, subtree_root):
        """Mirror the live run shape: proxmox/docs/agents linked outside the run."""
        docs = subtree_root / "proxmox/docs"
        docs.mkdir(parents=True)
        outside = self.root / "outside-docs"
        outside.mkdir(exist_ok=True)
        (docs / "agents").symlink_to(outside, target_is_directory=True)
        return docs / "agents"

    def test_missing_result_is_rejected_for_brief_only_fixture(self):
        worker_dir = self.root / "tasks/brief-only-worker"
        worker_dir.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "brief-only/brief.md", worker_dir / "brief.md")

        process = self.call(
            "validate-result", worker_dir / "result.json",
            "--task-id", "brief-only-worker", "--revision", "1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("missing result.json", process.stderr)

    def test_non_schema_result_is_rejected_for_synthetic_fixture(self):
        result_path = FIXTURES / "invalid-result/result.json"

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-placeholder", "--revision", "1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("missing", process.stderr)

    def test_valid_result_and_expected_identity_pass(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps(self.make_result()))

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-a", "--revision", "1",
        )

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("VALID", process.stdout)

    def test_result_with_mismatched_identity_is_rejected(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps(self.make_result()))

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-b", "--revision", "1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("task_id mismatch", process.stderr)

    def test_result_identity_arguments_are_required_together(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps(self.make_result()))

        missing_both = self.call("validate-result", result_path)
        missing_revision = self.call("validate-result", result_path, "--task-id", "worker-a")
        missing_task = self.call("validate-result", result_path, "--revision", "1")

        for process in (missing_both, missing_revision, missing_task):
            self.assertEqual(process.returncode, 2)
            self.assertIn("required", process.stderr)

    def test_revision_argument_accepts_only_ascii_digits(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps(self.make_result()))

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-a", "--revision", "+1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("positive integer", process.stderr)

    def test_blocked_or_failed_result_requires_explanation(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps(self.make_result(outcome="blocked")))

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-a", "--revision", "1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("unresolved", process.stderr)

    def test_repository_change_requires_candidate_record(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps(self.make_result()))

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-a", "--revision", "1", "--repository-changes",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("candidate", process.stderr)

    def test_repository_change_accepts_valid_candidate_record(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps(self.make_result(candidate={
            "repo": "example/project",
            "branch": "feature/worker-records",
            "base": "base-sha",
            "head": "head-sha",
        })))

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-a", "--revision", "1", "--repository-changes",
        )

        self.assertEqual(process.returncode, 0, process.stderr)

    def test_invalid_enum_types_and_unknown_fields_are_rejected_without_traceback(self):
        result_path = self.root / "result.json"
        malformed_records = [
            self.make_result(outcome=[]),
            self.make_result(checks=[{"name": "source check", "status": [], "evidence": "checks.log"}]),
            self.make_result(unexpected=True),
        ]
        for record in malformed_records:
            with self.subTest(record=record):
                result_path.write_text(json.dumps(record))

                process = self.call(
                    "validate-result", result_path,
                    "--task-id", "worker-a", "--revision", "1",
                )

                self.assertEqual(process.returncode, 2)
                self.assertNotIn("Traceback", process.stderr)

    def test_duplicate_json_keys_are_rejected(self):
        result_path = self.root / "result.json"
        result_path.write_text('{"task_id":"worker-a","task_id":"worker-b"}')

        process = self.call(
            "validate-result", result_path,
            "--task-id", "worker-a", "--revision", "1",
        )

        self.assertEqual(process.returncode, 2)
        self.assertIn("duplicate JSON key", process.stderr)

    def test_closeout_flags_worker_without_terminal_disposition(self):
        run_dir = self.root / "run"
        worker_dir = run_dir / "tasks/brief-only-worker"
        worker_dir.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "brief-only/brief.md", worker_dir / "brief.md")
        self.write_roster(run_dir, [self.roster_entry("brief-only-worker")])

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("missing disposition.json", process.stderr)

    def test_unrostered_brief_only_worker_blocks_closeout(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)
        unregistered_dir = run_dir / "tasks/brief-only-worker"
        unregistered_dir.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "brief-only/brief.md", unregistered_dir / "brief.md")
        self.write_roster(run_dir, [self.roster_entry()])

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("unrostered worker-shaped directory", process.stderr)
        self.assertIn("tasks/brief-only-worker", process.stderr)

    def test_closeout_ignores_worker_shapes_only_inside_evidenced_copies(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)

        capture = run_dir / "tasks/reviewer/review/capture"
        capture.mkdir(parents=True)
        (capture / "manifest.json").write_text('{"capture_id": "synthetic"}\n')
        (capture / "COMPLETE").write_text("synthetic\n")
        captured_task = capture / ".scratch/old-task"
        captured_task.mkdir(parents=True)
        (captured_task / "result.json").write_text("{}")

        checkout = run_dir / "tasks/reviewer/review/synthetic-repo"
        self.make_git_checkout(checkout)
        copied_fixture = checkout / ".pi/delegate/old-task"
        copied_fixture.mkdir(parents=True)
        (copied_fixture / "brief.md").write_text("Task: copied historical worker.\n")
        self.make_docs_symlink(checkout)

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("CLOSEOUT READY: 1 worker(s)", process.stdout)

    def test_closeout_reports_unrostered_worker_with_repository_style_name(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)
        for name in ("reviewer-repo", "capture", "test-copy-7"):
            worker_dir = run_dir / "tasks" / name
            worker_dir.mkdir(parents=True)
            (worker_dir / "brief.md").write_text(
                f"Task: real unrostered worker named {name}.\n"
            )

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("unrostered worker-shaped directory", process.stderr)
        for name in ("reviewer-repo", "capture", "test-copy-7"):
            self.assertIn(f"tasks/{name}", process.stderr)

    def test_closeout_reports_unrostered_worker_with_git_checkout_at_its_root(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)
        worker_dir = run_dir / "tasks/cloner-worker"
        self.make_git_checkout(worker_dir)
        (worker_dir / "brief.md").write_text(
            "Task: real unrostered worker that cloned into its own root.\n"
        )
        (worker_dir / "result.json").write_text("{}")

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("unrostered worker-shaped directory", process.stderr)
        self.assertIn("tasks/cloner-worker", process.stderr)

    def test_closeout_reports_unrostered_worker_with_gitfile_at_its_root(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)
        source = self.root / "captured-repository"
        self.make_git_checkout(source)
        worker_dir = run_dir / "tasks/worktree-worker"
        self.make_git_worktree(source, worker_dir)
        (worker_dir / "brief.md").write_text(
            "Task: real unrostered worker registered as a linked worktree.\n"
        )
        (worker_dir / "result.json").write_text("{}")

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("unrostered worker-shaped directory", process.stderr)
        self.assertIn("tasks/worktree-worker", process.stderr)

    def test_closeout_reports_unrostered_worker_with_capture_markers_at_its_root(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)
        worker_dir = run_dir / "tasks/capture-worker"
        worker_dir.mkdir(parents=True)
        (worker_dir / "manifest.json").write_text('{"capture_id": "synthetic"}\n')
        (worker_dir / "COMPLETE").write_text("synthetic\n")
        (worker_dir / "brief.md").write_text(
            "Task: real unrostered worker with capture markers at its root.\n"
        )
        (worker_dir / "result.json").write_text("{}")

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("unrostered worker-shaped directory", process.stderr)
        self.assertIn("tasks/capture-worker", process.stderr)

    def test_closeout_reports_evidence_carrying_worker_below_the_tasks_root(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)
        worker_dir = run_dir / "tasks/reviewer/review/embedded-worker"
        self.make_git_checkout(worker_dir)
        (worker_dir / "brief.md").write_text(
            "Task: unrostered worker nested deeper than the tasks root.\n"
        )
        (worker_dir / "result.json").write_text("{}")

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("unrostered worker-shaped directory", process.stderr)
        self.assertIn("tasks/reviewer/review/embedded-worker", process.stderr)

    def test_rostered_worker_with_copy_evidence_at_its_root_is_validated_not_reported(self):
        run_dir = self.root / "run"
        worker_dir = run_dir / "tasks/cloner-worker"
        self.make_git_checkout(worker_dir)
        (worker_dir / "brief.md").write_text(
            "Task: rostered worker that cloned into its own root.\n"
        )
        self.write_roster(run_dir, [self.roster_entry("cloner-worker")])

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("missing disposition.json", process.stderr)
        self.assertNotIn("unrostered worker-shaped directory", process.stderr)

    def test_closeout_ignores_real_worktree_in_realistic_run_layout(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir, task_id="regate-1144-1146")
        shutil.copyfile(
            FIXTURES / "brief-only/brief.md",
            run_dir / "tasks/regate-1144-1146/brief.md",
        )

        source = self.root / "captured-repository"
        self.make_git_checkout(source)
        worktree = run_dir / "tasks/regate-1144-1146/review-worktree-1146"
        self.make_git_worktree(source, worktree)
        copied_fixture = (
            worktree / "skills/orchestrator/tests/fixtures/worker-records/brief-only"
        )
        copied_fixture.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "brief-only/brief.md", copied_fixture / "brief.md")
        self.make_docs_symlink(worktree)

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("CLOSEOUT READY: 1 worker(s)", process.stdout)
        self.assertNotIn("review-worktree-1146", process.stderr)

    def test_closeout_still_validates_rostered_worker_inside_repository_copy(self):
        run_dir = self.root / "run"
        checkout = run_dir / "tasks/reviewer/review/captured-checkout"
        self.make_git_checkout(checkout)
        worker_directory = "tasks/reviewer/review/captured-checkout/reviews/worker-a"
        worker_dir = run_dir / worker_directory
        worker_dir.mkdir(parents=True)
        (worker_dir / "brief.md").write_text(
            "Task: rostered worker inside a captured checkout.\n"
        )
        self.write_roster(run_dir, [self.roster_entry(directory=worker_directory)])

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("missing disposition.json", process.stderr)
        self.assertNotIn("unrostered worker-shaped directory", process.stderr)

    def test_closeout_still_reports_symlinked_directories(self):
        run_dir = self.root / "run"
        self.register_completed_worker(run_dir)
        outside = self.root / "outside"
        outside.mkdir()
        (run_dir / "notes").mkdir()
        (run_dir / "notes/external").symlink_to(outside, target_is_directory=True)

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("cannot inspect symlinked directory: notes/external", process.stderr)

    def test_no_roster_and_no_worker_directories_is_a_valid_empty_closeout(self):
        run_dir = self.root / "run"
        run_dir.mkdir()

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("CLOSEOUT READY: 0 worker(s)", process.stdout)

    def test_duplicate_roster_task_id_is_rejected(self):
        run_dir = self.root / "run"
        self.write_roster(run_dir, [
            self.roster_entry("worker-a", "tasks/worker-a"),
            self.roster_entry("worker-a", "tasks/worker-b"),
        ])

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("duplicate task_id", process.stderr)

    def test_duplicate_roster_directory_is_rejected(self):
        run_dir = self.root / "run"
        self.write_roster(run_dir, [
            self.roster_entry("worker-a", "tasks/shared"),
            self.roster_entry("worker-b", "tasks/shared"),
        ])

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("duplicate directory", process.stderr)

    def test_absolute_and_parent_roster_paths_are_rejected(self):
        run_dir = self.root / "run"
        run_dir.mkdir()
        outside = self.root / "outside"
        for directory in ("../outside", str(outside)):
            with self.subTest(directory=directory):
                (run_dir / "workers.json").write_text(json.dumps({
                    "workers": [self.roster_entry(directory=directory)],
                }))

                process = self.call("check-closeout", run_dir)

                self.assertEqual(process.returncode, 2)
                self.assertIn("must stay inside", process.stderr)

    def test_symlinked_roster_directory_cannot_escape_run(self):
        run_dir = self.root / "run"
        worker_parent = run_dir / "tasks"
        worker_parent.mkdir(parents=True)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "brief.md").write_text("Task: synthetic placeholder.\n")
        (worker_parent / "link").symlink_to(outside, target_is_directory=True)
        self.write_roster(run_dir, [self.roster_entry(directory="tasks/link")])

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("resolves outside", process.stderr)

    def test_coordinator_can_record_cancelled_worker_then_close_run(self):
        run_dir = self.root / "run"
        worker_dir = run_dir / "tasks/brief-only-worker"
        worker_dir.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "brief-only/brief.md", worker_dir / "brief.md")
        self.write_roster(run_dir, [self.roster_entry("brief-only-worker")])

        recorded = self.call(
            "record-disposition",
            worker_dir,
            "--task-id", "brief-only-worker",
            "--revision", "1",
            "--status", "cancelled",
            "--summary", "Stopped before completion per coordinator signal.",
            "--evidence", "Brief remains; no result file was returned.",
        )
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        disposition = json.loads((worker_dir / "disposition.json").read_text())
        self.assertEqual(disposition["disposition"], "cancelled")

        closed = self.call("check-closeout", run_dir)

        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("CLOSEOUT READY", closed.stdout)

    def test_disposition_overwrite_requires_replace(self):
        worker_dir = self.root / "worker"
        worker_dir.mkdir()
        args = (
            "record-disposition", worker_dir,
            "--task-id", "worker-a", "--revision", "1",
            "--status", "cancelled", "--summary", "Initial record.",
            "--evidence", "Observed stop.",
        )
        self.assertEqual(self.call(*args).returncode, 0)
        original = (worker_dir / "disposition.json").read_text()

        rejected = self.call(
            "record-disposition", worker_dir,
            "--task-id", "worker-a", "--revision", "1",
            "--status", "completed", "--summary", "Updated record.",
            "--evidence", "result.json",
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual((worker_dir / "disposition.json").read_text(), original)

        replaced = self.call(
            "record-disposition", worker_dir,
            "--task-id", "worker-a", "--revision", "1",
            "--status", "completed", "--summary", "Updated record.",
            "--evidence", "result.json", "--replace",
        )
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertNotEqual((worker_dir / "disposition.json").read_text(), original)

    def test_closeout_rejects_disposition_for_wrong_assignment_revision(self):
        run_dir = self.root / "run"
        worker_dir = run_dir / "tasks/worker-a"
        worker_dir.mkdir(parents=True)
        self.write_roster(run_dir, [self.roster_entry(revision=2)])
        (worker_dir / "disposition.json").write_text(json.dumps({
            "task_id": "worker-a",
            "assignment_revision": 1,
            "disposition": "completed",
            "summary": "Finished.",
            "evidence": "result.json",
        }))

        process = self.call("check-closeout", run_dir)

        self.assertEqual(process.returncode, 2)
        self.assertIn("assignment_revision", process.stderr)


if __name__ == "__main__":
    unittest.main()
