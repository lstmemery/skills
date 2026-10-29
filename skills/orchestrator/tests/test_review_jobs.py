"""Independent reviews scheduled through the managed-job interface."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from support import isolate_admission_state


PACKAGE = Path(__file__).resolve().parents[1]
DRIVER = PACKAGE / "tests/driver.py"


class ReviewJobsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        isolate_admission_state(self, self.root / "admission")
        self.repo = self.root / "repo"
        self.git("init", "-b", "main", str(self.repo))
        (self.repo / "file.txt").write_text("base\n")
        self.git("-C", str(self.repo), "add", ".")
        self.git("-C", str(self.repo), "commit", "-m", "base")
        self.base = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.git("-C", str(self.repo), "switch", "-c", "candidate")
        (self.repo / "file.txt").write_text("candidate\n")
        self.git("-C", str(self.repo), "commit", "-am", "candidate")
        self.head = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.task = self.root / "task.md"
        self.task.write_text("Implement the fixture change and return the committed candidate.\n")
        self.candidate = {"repo": str(self.repo), "branch": "candidate",
                          "base": self.base, "head": self.head}
        self.author = {
            "job_id": "author", "name": "Author", "task_kind": "ordinary",
            "task_file": str(self.task), "cwd": str(self.repo),
            "writes_repository": True, "editable_paths": ["file.txt"],
            "output_expectation": "A committed candidate.",
            "worker_result": {"task_id": "coding-task", "assignment_revision": 1},
            "repository_worktree": {
                "schema_version": 1, "path": str(self.repo), "lease_id": "fixture",
                "lease_holder": "fixture", "repo_root": str(self.repo),
                "git_common_dir": str(self.repo / ".git"), "base_commit": self.base},
        }
        self.manifest = self.root / "manifest.json"
        self.fixture = self.root / "fixture.json"
        self.fixture.write_text(json.dumps({"request_id": "review-fixture", "worker_results": {
            "author": {"candidate": self.candidate}}}))
        self.run = self.root / "run"

    def git(self, *args):
        environment = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                       "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                       "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        return subprocess.run(["git", *args], env=environment, capture_output=True,
                              text=True, check=True).stdout.strip()

    def call(self, operation="run", jobs=None, concurrency=1):
        if jobs is not None:
            self.manifest.write_text(json.dumps({"schema_version": 1,
                "request_id": "review-fixture", "concurrency": concurrency, "jobs": jobs}))
        command = [sys.executable, str(DRIVER), operation, "--run-dir", str(self.run),
                   "--host-contract", str(self.fixture), "--wait-seconds", "0.8"]
        if operation in ("run", "add"):
            command.extend(["--manifest", str(self.manifest), "--policy", str(PACKAGE / "launch-policy.json")])
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_one_manifest_entry_launches_review_after_author_settles(self):
        code, result = self.call(jobs=[self.author, {"job_id": "reviewer", "review_of": "author"}])

        self.assertEqual(code, 0, result)
        state = json.loads((self.run / "state.json").read_text())
        author, reviewer = state["jobs"]
        self.assertTrue(author["settled"])
        self.assertTrue(reviewer["settled"])
        self.assertNotEqual(author["spec"]["worker_result"], reviewer["spec"]["worker_result"])
        prompt = (self.root / "prompt-reviewer.txt").read_text()
        for value in (self.base, self.head, author["agent_name"], "coding-task", "done.json"):
            self.assertIn(value, prompt)
        self.assertIn("Load and follow skill code-review", prompt)
        self.assertIn("supplied as review evidence", prompt)
        self.assertIn("verdict", prompt)
        self.assertIn("validate-review", prompt)
        self.assertFalse(reviewer["spec"]["writes_repository"])
        events = json.loads((self.root / "events.json").read_text())
        author_done = next(i for i, e in enumerate(events)
                           if e["job_id"] == "author" and e["action"] == "observe")
        reviewer_start = next(i for i, e in enumerate(events)
                              if e["job_id"] == "reviewer" and e["action"] == "split")
        self.assertLess(author_done, reviewer_start)

    def clean_review_output(self, name="review", base=None):
        review = self.root / "fixture-output" / name
        review.mkdir(parents=True)
        capture = subprocess.run([
            sys.executable, str(PACKAGE.parent / "code-review/scripts/capture.py"),
            "capture", "--repo", str(self.repo), "--mode", "since", "--base", base or self.base,
            "--out", str(review / "capture")], capture_output=True, text=True, timeout=20)
        self.assertEqual(capture.returncode, 0, capture.stdout + capture.stderr)
        capture_id = json.loads(capture.stdout)["capture_id"]
        (review / "review.md").write_text(
            "## Standards\n\nNo findings.\nSummary: findings=0; worst=none.\n\n"
            "## Spec\n\nNo findings.\nSummary: findings=0; worst=none.\n")
        (review / "review-evidence.json").write_text(json.dumps({
            "schema_version": 2, "task_id": "coding-task", "capture_id": capture_id,
            "axes": {"standards": {"status": "complete", "findings": []},
                     "spec": {"status": "complete", "findings": []}}}))
        counts = ({"standards_findings": 0, "spec_findings": 0} if name == "review"
                  else {"new_findings": 0, "unfixed": 0})
        (review / "done.json").write_text(json.dumps({
            "task_id": "coding-task", "capture_id": capture_id, "verdict": "approve", **counts}))
        return review

    def prepare_preflight(self):
        return subprocess.run([
            sys.executable, str(PACKAGE / "scripts/worker-records.py"), "prepare-preflight",
            "--managed-run", str(self.run), "--job-id", "author", "--task-id", "coding-task",
            "--repo", str(self.repo), "--target", "main", "--candidate", f"candidate={self.head}",
            "--output", str(self.root / "batch")], capture_output=True, text=True, timeout=20)

    def test_scheduled_review_reaches_ready_without_hand_edited_files(self):
        review = self.clean_review_output()
        fixture = json.loads(self.fixture.read_text())
        fixture["review_outputs"] = {"reviewer": str(review)}
        self.fixture.write_text(json.dumps(fixture))
        code, result = self.call(jobs=[self.author, {"job_id": "reviewer", "review_of": "author"}])
        self.assertEqual(code, 0, result)

        prepared = self.prepare_preflight()

        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        gate = subprocess.run([
            sys.executable, str(PACKAGE / "scripts/integration-preflight.py"),
            "--repo", str(self.repo), "--target", "main", "--run-dir", str(self.root / "batch"),
            "--candidate", f"candidate={self.head}"], capture_output=True, text=True, timeout=20)
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        self.assertEqual(json.loads(gate.stdout)["outcome"], "ready")

    def test_one_entry_adds_review_to_an_already_finished_author(self):
        code, result = self.call(jobs=[self.author])
        self.assertEqual(code, 0, result)

        code, result = self.call("add", jobs=[{"job_id": "reviewer", "review_of": "author"}])

        self.assertEqual(code, 0, result)
        self.assertTrue(result["jobs"][1]["settled"])
        self.assertIn(self.head, (self.root / "prompt-reviewer.txt").read_text())

    def test_review_waits_without_a_pane_while_author_is_working(self):
        fixture = json.loads(self.fixture.read_text())
        fixture["jobs"] = {"author": "working"}
        self.fixture.write_text(json.dumps(fixture))
        _, result = self.call(jobs=[{"job_id": "reviewer", "review_of": "author"}, self.author],
                              concurrency=2)
        self.assertIsNone(result["jobs"][0]["pane_id"])
        self.assertFalse((self.root / "prompt-reviewer.txt").exists())

    def test_delta_pins_prior_head_instead_of_candidate_base(self):
        prior = self.head
        (self.repo / "file.txt").write_text("fixed candidate\n")
        self.git("-C", str(self.repo), "commit", "-am", "fix")
        self.head = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        fixture = json.loads(self.fixture.read_text())
        fixture["worker_results"]["author"]["candidate"]["head"] = self.head
        self.fixture.write_text(json.dumps(fixture))
        code, result = self.call(jobs=[self.author, {"job_id": "delta", "review_of": "author",
                                "review_kind": "delta", "prior_head": prior}])
        self.assertEqual(code, 0, result)
        prompt = (self.root / "prompt-delta.txt").read_text()
        self.assertIn(f"Capture base: {prior}; capture head: {self.head}", prompt)
        self.assertIn("--kind delta", prompt)
        self.assertIn("/review-r1/", prompt)

    def test_invalid_review_declarations_are_refused_before_launch(self):
        invalid = [
            {"review_of": "unknown"}, {"review_of": "reviewer"},
            {"review_of": "author", "review_kind": "other"},
            {"review_of": "author", "review_kind": "delta"},
            {"review_of": "author", "prior_head": self.base},
            {"review_of": "author", "writes_repository": True},
            {"review_kind": "delta", "prior_head": self.base},
            {"review_of": "author", "worker_result": self.author["worker_result"]},
        ]
        for declaration in invalid:
            with self.subTest(declaration=declaration):
                code, result = self.call(jobs=[self.author, {"job_id": "reviewer", **declaration}])
                self.assertEqual(code, 2, result)
                self.assertFalse((self.root / "events.json").exists())

    def test_review_author_missing_required_fields_returns_structured_invalid_input(self):
        for key in ("cwd", "name", "task_kind", "task_file", "output_expectation",
                    "writes_repository", "worker_result"):
            for review_first in (False, True):
                with self.subTest(missing=key, review_first=review_first):
                    author = dict(self.author)
                    del author[key]
                    reviewer = {"job_id": "reviewer", "review_of": "author"}
                    jobs = [reviewer, author] if review_first else [author, reviewer]

                    code, result = self.call(jobs=jobs)

                    self.assertEqual(code, 2, result)
                    self.assertEqual(result["error"], "invalid_input")
                    self.assertFalse((self.root / "events.json").exists())

    def test_moved_candidate_blocks_review_without_launching(self):
        fixture = json.loads(self.fixture.read_text())
        fixture["worker_results"]["author"]["candidate"]["head"] = self.base
        self.fixture.write_text(json.dumps(fixture))
        _, result = self.call(jobs=[self.author, {"job_id": "reviewer", "review_of": "author"}])
        self.assertIsNone(result["jobs"][1]["pane_id"])
        self.assertIn("branch moved", result["jobs"][1]["issue"])

    def test_scheduled_review_requires_verdict_in_done_even_with_report_verdict(self):
        review = self.clean_review_output()
        done = json.loads((review / "done.json").read_text())
        del done["verdict"]
        (review / "done.json").write_text(json.dumps(done))
        fixture = json.loads(self.fixture.read_text())
        fixture["review_outputs"] = {"reviewer": str(review)}
        self.fixture.write_text(json.dumps(fixture))
        code, result = self.call(jobs=[self.author, {"job_id": "reviewer", "review_of": "author"}])
        self.assertEqual(code, 0, result)
        state = json.loads((self.run / "state.json").read_text())
        (Path(state["jobs"][1]["host_output"]) / "report.md").write_text("Verdict: approve.\n")

        prepared = self.prepare_preflight()

        self.assertEqual(prepared.returncode, 2, prepared.stdout + prepared.stderr)
        self.assertIn("verdict in done.json", prepared.stderr)
        self.assertFalse((self.root / "batch").exists())

    def test_managed_gate_also_requires_the_scheduled_review_verdict(self):
        review = self.clean_review_output()
        done = json.loads((review / "done.json").read_text())
        del done["verdict"]
        (review / "done.json").write_text(json.dumps(done))
        fixture = json.loads(self.fixture.read_text())
        fixture["review_outputs"] = {"reviewer": str(review)}
        self.fixture.write_text(json.dumps(fixture))
        code, result = self.call(jobs=[self.author, {"job_id": "reviewer", "review_of": "author"}])
        self.assertEqual(code, 0, result)
        evidence = self.root / "evidence"
        evidence.mkdir()

        gate = subprocess.run([
            sys.executable, str(PACKAGE / "scripts/integration-preflight.py"),
            "--repo", str(self.repo), "--target", "main", "--run-dir", str(evidence),
            "--managed-run", str(self.run), "--candidate", f"candidate={self.head}"],
            capture_output=True, text=True, timeout=20)

        self.assertEqual(gate.returncode, 2, gate.stdout + gate.stderr)
        self.assertIn("verdict in done.json", gate.stdout + gate.stderr)

    def test_non_object_done_is_refused_cleanly_by_both_preflight_paths(self):
        review = self.clean_review_output()
        (review / "done.json").write_text("[]\n")
        fixture = json.loads(self.fixture.read_text())
        fixture["review_outputs"] = {"reviewer": str(review)}
        self.fixture.write_text(json.dumps(fixture))
        code, result = self.call(jobs=[self.author, {"job_id": "reviewer", "review_of": "author"}])
        self.assertEqual(code, 0, result)
        evidence = self.root / "evidence"
        evidence.mkdir()

        commands = {
            "prepare": self.prepare_preflight,
            "managed": lambda: subprocess.run([
                sys.executable, str(PACKAGE / "scripts/integration-preflight.py"),
                "--repo", str(self.repo), "--target", "main", "--run-dir", str(evidence),
                "--managed-run", str(self.run), "--candidate", f"candidate={self.head}"],
                capture_output=True, text=True, timeout=20),
        }
        for name, command in commands.items():
            with self.subTest(path=name):
                refused = command()
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertNotIn("Traceback", refused.stdout + refused.stderr)
                self.assertIn("done.json", refused.stdout + refused.stderr)
