import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SKILL_DIR = Path(__file__).resolve().parents[1]
CLI = SKILL_DIR / "scripts" / "integration-preflight.py"


class IntegrationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.task_dir = self.run_dir / "task-1"
        self.task_dir.mkdir()
        self.git("init", "-b", "main", str(self.repo))
        self.git("-C", str(self.repo), "config", "user.name", "Synthetic Worker")
        self.git("-C", str(self.repo), "config", "user.email", "worker@example.invalid")
        (self.repo / "README.md").write_text("base\n")
        self.git("-C", str(self.repo), "add", "README.md")
        self.git("-C", str(self.repo), "commit", "-m", "base")
        self.base = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.git("-C", str(self.repo), "switch", "-c", "orch/task-1")
        (self.repo / "README.md").write_text("candidate\n")
        self.git("-C", str(self.repo), "commit", "-am", "candidate")
        self.head = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.git("-C", str(self.repo), "switch", "main")
        self.write_result()
        (self.run_dir / "workers.txt").write_text(
            "task-1 coder-1 w1:p1 codex test-model max working\n"
            "task-1 reviewer-1 w2:p1 codex test-model max (reviewer) head="
            + self.head
            + "\n"
        )

    def git(self, *args):
        environment = os.environ.copy()
        environment.update(
            GIT_AUTHOR_NAME="Synthetic Worker",
            GIT_AUTHOR_EMAIL="worker@example.invalid",
            GIT_COMMITTER_NAME="Synthetic Worker",
            GIT_COMMITTER_EMAIL="worker@example.invalid",
        )
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return result.stdout.strip()

    def write_result(self, *, base=None, head=None):
        result = {
            "task_id": "task-1",
            "outcome": "ready",
            "candidate": {
                "repo": "synthetic/repo",
                "branch": "orch/task-1",
                "base": base or self.base,
                "head": head or self.head,
            },
        }
        (self.task_dir / "result.json").write_text(json.dumps(result) + "\n")

    def write_review(
        self,
        name="review",
        *,
        base=None,
        head=None,
        reviewer="reviewer-1",
        standards_findings=None,
        spec_findings=None,
        coverage="complete",
        gaps=None,
    ):
        standards_findings = standards_findings or []
        spec_findings = spec_findings or []
        review_dir = self.task_dir / name
        capture_dir = review_dir / "capture"
        capture_dir.mkdir(parents=True)
        capture_id = hashlib.sha256(f"{name}:{base or self.base}:{head or self.head}".encode()).hexdigest()
        manifest = {
            "capture_id": capture_id,
            "base": base or self.base,
            "head": head or self.head,
            "coverage": coverage,
            "gaps": gaps or [],
        }
        (capture_dir / "manifest.json").write_text(json.dumps(manifest) + "\n")
        (capture_dir / "COMPLETE").write_text("synthetic complete capture\n")
        done = {
            "task_id": "task-1",
            "capture_id": capture_id,
            "standards_findings": len(standards_findings),
            "spec_findings": len(spec_findings),
        }
        (review_dir / "done.json").write_text(json.dumps(done) + "\n")
        (review_dir / "review.md").write_text(
            "# Independent review\n\n## Standards\n\n"
            + ("No findings.\n\n" if not standards_findings else "Findings recorded.\n\n")
            + "## Spec\n\n"
            + ("No findings.\n" if not spec_findings else "Findings recorded.\n")
        )
        evidence = {
            "schema_version": 1,
            "task_id": "task-1",
            "author_identity": "coder-1",
            "reviewer_identity": reviewer,
            "capture_id": capture_id,
            "axes": {
                "standards": {"status": "complete", "findings": standards_findings},
                "spec": {"status": "complete", "findings": spec_findings},
            },
        }
        (review_dir / "review-evidence.json").write_text(json.dumps(evidence) + "\n")
        dispositions = []
        for finding in [*standards_findings, *spec_findings]:
            dispositions.append(
                f"- `{name}/{finding['id']}`: {finding['disposition']} — recorded synthetic disposition"
            )
        response_path = self.task_dir / "review-response.md"
        old = response_path.read_text() if response_path.exists() else "# Review response\n\n"
        response_path.write_text(old + "\n".join(dispositions) + ("\n" if dispositions else ""))

    def install_review_fixture(self):
        fixture = SKILL_DIR / "tests" / "fixtures" / "integration-preflight" / "run"
        shutil.copytree(fixture, self.run_dir, dirs_exist_ok=True)
        for path in self.run_dir.rglob("*"):
            if path.is_file():
                contents = path.read_text(encoding="utf-8")
                path.write_text(
                    contents.replace("@BASE@", self.base).replace("@HEAD@", self.head),
                    encoding="utf-8",
                )

    def test_complete_independent_review_passes_and_is_recorded(self):
        self.install_review_fixture()

        result = self.command(candidate=self.head)

        self.assertEqual(result.returncode, 0, result.stderr)
        output_files = list((self.run_dir / "integration-preflight").glob("*.json"))
        self.assertEqual(len(output_files), 1)
        report = json.loads(output_files[0].read_text())
        self.assertEqual(report["outcome"], "ready")
        self.assertEqual(report["candidates"][0]["head"], self.head)
        self.assertEqual(self.git("-C", str(self.repo), "rev-parse", "main"), self.base)

    def command(self, *extra, candidate="orch/task-1"):
        return subprocess.run(
            [
                "python3",
                str(CLI),
                "--repo",
                str(self.repo),
                "--target",
                "main",
                "--run-dir",
                str(self.run_dir),
                "--candidate",
                candidate,
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_candidate_without_review_evidence_is_refused(self):
        result = self.command()

        self.assertEqual(result.returncode, 1)
        output = json.loads(result.stdout)
        self.assertEqual(output["outcome"], "blocked")
        self.assertIn(
            "missing independent review evidence",
            output["issues"][0]["issues"][0],
        )
        evidence_files = list((self.run_dir / "integration-preflight").glob("*.json"))
        self.assertEqual(len(evidence_files), 1)
        report = json.loads(evidence_files[0].read_text())
        self.assertEqual(report["outcome"], "blocked")
        self.assertEqual(report["candidates"][0]["head"], self.head)

    def test_capture_of_a_different_base_or_head_is_stale(self):
        self.write_review(base="f" * 40)

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("no complete review capture chain", result.stdout)

    def test_capture_that_ends_before_the_candidate_is_stale(self):
        self.write_review(head=self.base)

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("no complete review capture chain", result.stdout)

    def test_pinned_branch_must_still_point_to_the_reviewed_candidate(self):
        self.git("-C", str(self.repo), "switch", "orch/task-1")
        (self.repo / "README.md").write_text("branch moved\n")
        self.git("-C", str(self.repo), "commit", "-am", "branch moved")
        self.git("-C", str(self.repo), "switch", "main")

        result = self.command(candidate=f"orch/task-1={self.head}")

        self.assertEqual(result.returncode, 1)
        self.assertIn("no longer points", result.stdout)

    def test_author_cannot_review_their_own_candidate(self):
        self.write_review(reviewer="coder-1")

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("reviewer is the candidate author", result.stdout)

    def test_author_identity_must_match_first_task_worker(self):
        self.write_review(reviewer="coder-1")
        evidence_path = self.task_dir / "review" / "review-evidence.json"
        evidence = json.loads(evidence_path.read_text())
        evidence["author_identity"] = "reviewer-1"
        evidence["reviewer_identity"] = "coder-1"
        evidence_path.write_text(json.dumps(evidence) + "\n")

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("author identity does not match", result.stdout)

    def test_both_review_axes_must_be_complete(self):
        self.write_review()
        evidence_path = self.task_dir / "review" / "review-evidence.json"
        evidence = json.loads(evidence_path.read_text())
        evidence["axes"]["spec"]["status"] = "blocked"
        evidence_path.write_text(json.dumps(evidence) + "\n")

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("spec review is incomplete", result.stdout)

    def test_every_finding_needs_a_recorded_disposition(self):
        self.write_review(
            standards_findings=[
                {"id": "S1", "disposition": "fixed", "reason": "Corrected the issue."}
            ]
        )
        response_path = self.task_dir / "review-response.md"
        response_path.write_text("# Review response\n")

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("missing recorded disposition for review/S1", result.stdout)

    def test_complete_full_review_and_contiguous_delta_review_cover_final_head(self):
        first_review_head = self.head
        self.git("-C", str(self.repo), "switch", "orch/task-1")
        (self.repo / "README.md").write_text("final candidate\n")
        self.git("-C", str(self.repo), "commit", "-am", "final candidate")
        self.head = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.git("-C", str(self.repo), "switch", "main")
        self.write_result()
        (self.run_dir / "workers.txt").write_text(
            "task-1 coder-1 w1:p1 codex test-model max working\n"
            "task-1 reviewer-1 w2:p1 codex test-model max (reviewer) head="
            + self.head
            + "\n"
        )
        self.write_review(head=first_review_head)
        self.write_review(
            "review-r2",
            base=first_review_head,
            standards_findings=[
                {"id": "S2", "disposition": "fixed", "reason": "Corrected the delta issue."}
            ],
        )

        result = self.command()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence_files = list((self.run_dir / "integration-preflight").glob("*.json"))
        self.assertEqual(len(evidence_files), 1)
        report = json.loads(evidence_files[0].read_text())
        self.assertEqual(report["candidates"][0]["head"], self.head)
        self.assertEqual(
            [review["review_id"] for review in report["candidates"][0]["reviews"]],
            ["review", "review-r2"],
        )


if __name__ == "__main__":
    unittest.main()
