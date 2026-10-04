import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
CLI = SKILL_DIR / "scripts" / "integration-preflight.py"
CAPTURE_CLI = SKILL_DIR.parent / "code-review" / "scripts" / "capture.py"
PREFLIGHT_SPEC = importlib.util.spec_from_file_location("integration_preflight", CLI)
PREFLIGHT = importlib.util.module_from_spec(PREFLIGHT_SPEC)
PREFLIGHT_SPEC.loader.exec_module(PREFLIGHT)


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
        self.write_workers()

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
        schema_version=1,
        base=None,
        head=None,
        reviewer="reviewer-1",
        standards_findings=None,
        spec_findings=None,
        unfixed=0,
    ):
        standards_findings = standards_findings or []
        spec_findings = spec_findings or []
        review_dir = self.task_dir / name
        capture_dir = review_dir / "capture"
        review_dir.mkdir(parents=True)
        capture = self.create_capture(capture_dir, base or self.base, head or self.head)
        capture_id = capture["capture_id"]
        done = {"task_id": "task-1", "capture_id": capture_id}
        if name == "review":
            done["standards_findings"] = len(standards_findings)
            done["spec_findings"] = len(spec_findings)
        else:
            done["new_findings"] = len(standards_findings) + len(spec_findings)
            if unfixed is not None:
                done["unfixed"] = unfixed
        (review_dir / "done.json").write_text(json.dumps(done) + "\n")
        standards_markdown = self.markdown_findings(standards_findings, "Standards")
        spec_markdown = self.markdown_findings(spec_findings, "Spec")
        (review_dir / "review.md").write_text(
            "# Independent review\n\n## Standards\n\n"
            + standards_markdown
            + "\n\n## Spec\n\n"
            + spec_markdown
            + "\n"
        )
        evidence = {
            "schema_version": schema_version,
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

    @staticmethod
    def markdown_findings(findings, axis):
        if not findings:
            entries = ["No findings."]
            worst = "none"
        else:
            entries = [
                f"- [{finding['id']}] Synthetic {axis} finding."
                for finding in findings
            ]
            worst = f"Synthetic {axis} finding"
        entries.append(f"Summary: findings={len(findings)}; worst={worst}.")
        return "\n".join(entries)

    def create_capture(self, capture_dir, base, head):
        self.git("-C", str(self.repo), "switch", "--detach", head)
        try:
            result = subprocess.run(
                [
                    "python3",
                    str(CAPTURE_CLI),
                    "capture",
                    "--repo",
                    str(self.repo),
                    "--mode",
                    "since",
                    "--base",
                    base,
                    "--out",
                    str(capture_dir),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.fail(f"capture fixture generation failed: {result.stdout}{result.stderr}")
            return json.loads(result.stdout)
        finally:
            self.git("-C", str(self.repo), "switch", "main")

    def write_workers(self, additional_rows=()):
        rows = [
            "task-1 coder-1 w1:p1 codex test-model max working",
            "task-1 reviewer-1 w2:p1 codex test-model max (reviewer) head=" + self.head,
            *additional_rows,
        ]
        (self.run_dir / "workers.txt").write_text("\n".join(rows) + "\n")

    def add_roster_only_candidate(self):
        self.write_workers(["task-2 coder-2 w3:p1 codex test-model max working"])

    def add_result_only_candidate(self, *, include_task_id=True):
        task_dir = self.run_dir / "task-2"
        task_dir.mkdir()
        result = {"candidate": {"head": "2" * 40}}
        if include_task_id:
            result["task_id"] = "task-2"
        (task_dir / "result.json").write_text(
            json.dumps(result) + "\n"
        )

    def add_delta_review(self, *, unfixed=0):
        first_review_head = self.head
        self.git("-C", str(self.repo), "switch", "orch/task-1")
        (self.repo / "README.md").write_text("final candidate\n")
        self.git("-C", str(self.repo), "commit", "-am", "final candidate")
        self.head = self.git("-C", str(self.repo), "rev-parse", "HEAD")
        self.git("-C", str(self.repo), "switch", "main")
        self.write_result()
        self.write_workers()
        self.write_review(head=first_review_head)
        self.write_review(
            "review-r2",
            base=first_review_head,
            standards_findings=[
                {"id": "S2", "disposition": "fixed", "reason": "Corrected the delta issue."}
            ],
            unfixed=unfixed,
        )
        return self.task_dir / "review-r2" / "done.json"

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
        review_dir = self.task_dir / "review"
        capture_dir = review_dir / "capture"
        shutil.rmtree(capture_dir)
        capture = self.create_capture(capture_dir, self.base, self.head)
        for record_name in ("done.json", "review-evidence.json"):
            record_path = review_dir / record_name
            record = json.loads(record_path.read_text())
            record["capture_id"] = capture["capture_id"]
            record_path.write_text(json.dumps(record) + "\n")

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

    def test_finding_evidence_metadata_passes_without_changing_markdown_grammar(self):
        self.write_review(
            schema_version=2,
            standards_findings=[
                {
                    "id": "S1",
                    "disposition": "fixed",
                    "reason": "Added the missing guard.",
                    "confidence": "high",
                    "reproducer": (
                        "Run the focused test; expected result: the missing guard is "
                        "exercised."
                    ),
                }
            ],
            spec_findings=[
                {
                    "id": "P1",
                    "disposition": "rejected",
                    "reason": "The behavior is outside the accepted requirements.",
                    "confidence": "medium",
                    "evidence": "The submitted specification has no requirement for this behavior.",
                    "unresolved_assumption": "No linked follow-up requirement exists.",
                }
            ],
        )

        result = self.command()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "ready")

    def test_legacy_schema_v1_finding_without_evidence_remains_valid(self):
        self.write_review(
            standards_findings=[
                {"id": "S1", "disposition": "fixed", "reason": "Added the guard."}
            ],
        )

        result = self.command()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "ready")

    def test_partial_finding_evidence_metadata_is_rejected(self):
        self.write_review(
            standards_findings=[
                {
                    "id": "S1",
                    "disposition": "fixed",
                    "reason": "Added the missing guard.",
                    "confidence": "high",
                }
            ]
        )

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "needs a reproducer or evidence and unresolved assumption",
            result.stdout,
        )

    def test_schema_v2_finding_requires_evidence_metadata(self):
        self.write_review(
            schema_version=2,
            standards_findings=[
                {"id": "S1", "disposition": "fixed", "reason": "Added the guard."}
            ],
        )

        result = self.command()

        self.assertEqual(result.returncode, 1)
        output = json.loads(result.stdout)
        self.assertEqual(output["outcome"], "blocked")
        self.assertIn(
            "schema version 2 finding requires evidence metadata",
            output["issues"][0]["issues"][0],
        )

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

    def test_capture_without_payload_is_refused(self):
        self.write_review()
        capture_dir = self.task_dir / "review" / "capture"
        for entry in capture_dir.iterdir():
            if entry.name in {"manifest.json", "COMPLETE"}:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("capture payload verification failed", result.stdout)

    def test_modified_capture_payload_is_refused(self):
        self.write_review()
        diff_path = self.task_dir / "review" / "capture" / "diff.patch"
        diff_path.write_bytes(diff_path.read_bytes() + b"tampered\n")

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("capture payload verification failed", result.stdout)

    def test_mismatched_capture_completion_marker_is_refused(self):
        self.write_review()
        complete_path = self.task_dir / "review" / "capture" / "COMPLETE"
        complete_path.write_text("different capture ID\n")

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("capture payload verification failed", result.stdout)

    def test_capture_verifier_requires_all_result_keys(self):
        self.write_review()
        review_dir = self.task_dir / "review"
        capture_dir = review_dir / "capture"
        manifest = json.loads((capture_dir / "manifest.json").read_text())
        verification = {
            "outcome": "verified",
            "capture_id": manifest["capture_id"],
            "coverage": "complete",
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(verification), "")

        with patch.object(PREFLIGHT.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                PREFLIGHT.PreflightError,
                "capture verifier is missing required keys: gaps",
            ):
                PREFLIGHT.verify_capture_payload(review_dir, capture_dir, manifest)

    def test_capture_verifier_rejects_result_mismatches(self):
        self.write_review()
        review_dir = self.task_dir / "review"
        capture_dir = review_dir / "capture"
        manifest = json.loads((capture_dir / "manifest.json").read_text())
        valid = {
            "outcome": "verified",
            "capture_id": manifest["capture_id"],
            "coverage": "complete",
            "gaps": [],
        }
        mismatches = [
            ("outcome", {**valid, "outcome": "failed"}),
            ("capture ID", {**valid, "capture_id": "different-capture"}),
            ("coverage", {**valid, "coverage": "partial"}),
            ("gaps", {**valid, "gaps": ["missing source"]}),
        ]

        for name, verification in mismatches:
            with self.subTest(name=name):
                completed = subprocess.CompletedProcess([], 0, json.dumps(verification), "")
                with patch.object(PREFLIGHT.subprocess, "run", return_value=completed):
                    with self.assertRaisesRegex(
                        PREFLIGHT.PreflightError,
                        "capture verifier did not verify complete matching coverage",
                    ):
                        PREFLIGHT.verify_capture_payload(review_dir, capture_dir, manifest)

    def test_markdown_finding_missing_from_sidecar_is_refused(self):
        self.write_review()
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n\n"
            "- [S1] MAJOR · CONFIRMED — Missing validation.\n"
            "Summary: findings=1; worst=Missing validation.\n\n"
            "## Spec\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Markdown finding IDs disagree with review-evidence.json", result.stdout)

    def test_code_review_skill_format_passes(self):
        finding = {"id": "S1", "disposition": "fixed", "reason": "Corrected the issue."}
        self.write_review(standards_findings=[finding])
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n"
            "- [S1] **MAJOR · CONFIRMED** — Missing validation for captured payloads.\n"
            "Summary: findings=1; worst=Missing payload verification.\n\n"
            "## Spec\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "ready")

    def test_markdown_prose_finding_is_refused(self):
        self.write_review()
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n\n"
            "The reviewer found a MAJOR validation issue.\n"
            "Summary: findings=0; worst=none.\n\n"
            "## Spec\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unrecognized nonblank line in the Standards section", result.stdout)

    def test_fresh_prose_only_review_is_refused(self):
        self.write_review()
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "Scope: this is a fresh review with no findings.\n\n"
            "## Standards\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n\n"
            "## Spec\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unrecognized content outside its axis sections", result.stdout)

    def test_legacy_prose_exception_is_exactly_pinned_to_three_prior_records(self):
        allowlist = PREFLIGHT.LEGACY_PRECHANGE_REVIEWS
        self.assertEqual(set(allowlist), {"1131", "1132", "1133"})
        self.assertEqual(
            PREFLIGHT.LEGACY_PRECEDENT["sha256"],
            "9706b575d12793163f61b5120fcd728b74ee8a2b73961fa1b341b1cc272ea8a7",
        )
        for task_id, expected in allowlist.items():
            with self.subTest(task_id=task_id):
                self.assertEqual(
                    set(expected),
                    set(PREFLIGHT.LEGACY_REVIEW_RECORD_FIELDS),
                )
                self.assertTrue(PREFLIGHT.legacy_review_record_matches(task_id, expected))
                forged = dict(expected)
                forged["review_markdown_sha256"] = "0" * 64
                self.assertFalse(PREFLIGHT.legacy_review_record_matches(task_id, forged))
                self.assertFalse(PREFLIGHT.legacy_review_record_matches("fresh-task", expected))

    def test_legacy_prose_requires_the_previously_ready_preflight_report(self):
        task_id = "1132"
        observed = PREFLIGHT.LEGACY_PRECHANGE_REVIEWS[task_id]
        self.assertIsNone(
            PREFLIGHT.legacy_review_precedent(task_id, observed, self.run_dir)
        )
        report_dir = self.run_dir / "integration-preflight"
        report_dir.mkdir()
        (report_dir / PREFLIGHT.LEGACY_PRECEDENT["report"]).write_text("{}\n")
        self.assertIsNone(
            PREFLIGHT.legacy_review_precedent(task_id, observed, self.run_dir)
        )

    def test_legacy_precedent_refuses_nonmatching_synthetic_reports(self):
        task_id = "task-1"
        observed = {
            field: f"synthetic-{field}"
            for field in PREFLIGHT.LEGACY_REVIEW_RECORD_FIELDS
        }
        observed["finding_ids"] = {axis: [] for axis in PREFLIGHT.AXES}

        def precedent_review():
            review = {
                field: observed[field]
                for field in PREFLIGHT.LEGACY_REVIEW_SHARED_FIELDS
            }
            review.update({"review_path": observed["review_id"], "finding_count": 0})
            return review

        def ready_candidate(reviews):
            return {"task_id": task_id, "status": "ready", "reviews": reviews}

        def precedent(report_payload, sha256=None):
            report_dir = self.run_dir / "integration-preflight"
            report_dir.mkdir(exist_ok=True)
            report_path = report_dir / "synthetic-precedent.json"
            report_path.write_text(json.dumps(report_payload) + "\n")
            return {
                "report": report_path.name,
                "sha256": sha256 or PREFLIGHT.sha256_file(report_path),
            }

        def refused(precedent):
            with (
                patch.object(PREFLIGHT, "LEGACY_PRECHANGE_REVIEWS", {task_id: observed}),
                patch.object(PREFLIGHT, "LEGACY_PRECEDENT", precedent),
            ):
                return PREFLIGHT.legacy_review_precedent(task_id, observed, self.run_dir)

        cases = {
            "non-ready outcome": {
                "outcome": "blocked",
                "candidates": [ready_candidate([precedent_review()])],
            },
            "zero matching ready candidates": {"outcome": "ready", "candidates": []},
            "duplicate matching ready candidates": {
                "outcome": "ready",
                "candidates": [
                    ready_candidate([precedent_review()]),
                    ready_candidate([precedent_review()]),
                ],
            },
            "zero matching precedent reviews": {
                "outcome": "ready",
                "candidates": [ready_candidate([])],
            },
            "multiple matching precedent reviews": {
                "outcome": "ready",
                "candidates": [
                    ready_candidate([precedent_review(), precedent_review()])
                ],
            },
        }
        for name, report_payload in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(refused(precedent(report_payload)))

        matching_report = {
            "outcome": "ready",
            "candidates": [ready_candidate([precedent_review()])],
        }
        control = precedent(matching_report)
        self.assertEqual(refused(control), control["sha256"])
        report_path = self.run_dir / "integration-preflight" / control["report"]
        tampered = {"report": control["report"], "sha256": control["sha256"]}
        report_path.write_text(json.dumps(matching_report, indent=2) + "\n")
        self.assertNotEqual(PREFLIGHT.sha256_file(report_path), tampered["sha256"])
        self.assertIsNone(refused(tampered))

    def test_allowlisted_prechange_review_yields_ready_record_with_synthetic_pin(self):
        self.write_review()
        review_dir = self.task_dir / "review"
        review_path = review_dir / "review.md"
        review_path.write_text(
            "Independent synthetic review. Both Standards and Spec axes are complete; "
            "neither found an issue.\n"
        )
        evidence_path = review_dir / "review-evidence.json"
        done_path = review_dir / "done.json"
        evidence = json.loads(evidence_path.read_text())
        capture = PREFLIGHT.find_capture(review_dir, evidence["capture_id"])
        observed = {
            "review_id": review_dir.name,
            "base": capture["base"],
            "head": capture["head"],
            "capture_id": evidence["capture_id"],
            "author_identity": evidence["author_identity"],
            "reviewer_identity": evidence["reviewer_identity"],
            "review_markdown_sha256": PREFLIGHT.sha256_file(review_path),
            "review_evidence_sha256": PREFLIGHT.sha256_file(evidence_path),
            "done_sha256": PREFLIGHT.sha256_file(done_path),
            "capture_manifest_sha256": capture["manifest_sha256"],
            "capture_complete_sha256": capture["complete_sha256"],
            "finding_ids": {axis: [] for axis in PREFLIGHT.AXES},
        }
        precedent_review = {
            field: observed[field]
            for field in PREFLIGHT.LEGACY_REVIEW_SHARED_FIELDS
        }
        precedent_review.update({"review_path": review_dir.name, "finding_count": 0})
        precedent_report = {
            "outcome": "ready",
            "candidates": [
                {
                    "task_id": "task-1",
                    "status": "ready",
                    "reviews": [precedent_review],
                }
            ],
        }
        report_dir = self.run_dir / "integration-preflight"
        report_dir.mkdir()
        report_path = report_dir / "synthetic-precedent.json"
        report_path.write_text(json.dumps(precedent_report) + "\n")
        precedent = {
            "report": report_path.name,
            "sha256": PREFLIGHT.sha256_file(report_path),
        }

        with (
            patch.object(PREFLIGHT, "LEGACY_PRECHANGE_REVIEWS", {"task-1": observed}),
            patch.object(PREFLIGHT, "LEGACY_PRECEDENT", precedent),
        ):
            records, diagnostics = PREFLIGHT.review_records(
                self.task_dir,
                "task-1",
                self.run_dir / "workers.txt",
            )

        self.assertEqual(diagnostics, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["markdown_compatibility"], "allowlisted-pre-change")
        self.assertEqual(records[0]["legacy_precedent_sha256"], precedent["sha256"])
        self.assertEqual(records[0]["finding_count"], 0)

    def test_markdown_heading_finding_is_refused(self):
        self.write_review()
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n\n"
            "### S1 — MAJOR: Missing validation.\n"
            "Summary: findings=0; worst=none.\n\n"
            "## Spec\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unrecognized nonblank line in the Standards section", result.stdout)

    def test_parser_refuses_specific_malformed_review_branches(self):
        review_dir = self.task_dir / "review"
        review_dir.mkdir()
        review_path = review_dir / "review.md"
        valid_empty_standards = "## Standards\nNo findings.\nSummary: findings=0; worst=none.\n"
        valid_empty_spec = "## Spec\nNo findings.\nSummary: findings=0; worst=none.\n"
        cases = {
            "repeated axis section": (
                valid_empty_standards + "\n## Standards\n",
                "repeats the Standards section",
            ),
            "duplicate finding ID across axes": (
                "## Standards\n- [S1] Finding.\nSummary: findings=1; worst=Finding.\n\n"
                "## Spec\n- [S1] Duplicate finding.\n",
                "repeats finding ID S1",
            ),
            "finding after No findings": (
                "## Standards\nNo findings.\n- [S1] Finding.\n",
                "has a finding after No findings.",
            ),
            "summary not last": (
                valid_empty_standards + "Unexpected trailing text.\n",
                "summary must be the last nonblank line in the Standards section",
            ),
            "missing summary": (
                "## Standards\nNo findings.\n\n" + valid_empty_spec,
                "lacks a Standards summary line",
            ),
            "empty axis without No findings": (
                "## Standards\nSummary: findings=0; worst=none.\n",
                "empty Standards summary requires No findings. and worst=none",
            ),
            "worst none with finding": (
                "## Standards\n- [S1] Finding.\nSummary: findings=1; worst=none.\n",
                "nonempty Standards summary requires a worst-issue description",
            ),
            "worst None variant with finding": (
                "## Standards\n- [S1] Finding.\nSummary: findings=1; worst=None.\n",
                "nonempty Standards summary requires a worst-issue description",
            ),
            "second title": (
                "# Independent review\n# Extra title\n" + valid_empty_standards,
                "unrecognized content outside its axis sections",
            ),
            "content before title": (
                "Introductory text.\n# Independent review\n" + valid_empty_standards,
                "unrecognized content outside its axis sections",
            ),
        }

        for name, (markdown, diagnostic) in cases.items():
            with self.subTest(name=name):
                review_path.write_text(markdown)
                with self.assertRaisesRegex(PREFLIGHT.PreflightError, diagnostic):
                    PREFLIGHT.parse_review_markdown(review_dir)

    def test_other_heading_cannot_hide_a_finding_outside_axis_sections(self):
        self.write_review()
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n\n"
            "## Additional findings\n"
            "- [S1] MAJOR · CONFIRMED — Missing validation.\n\n"
            "## Spec\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unsupported level-two heading", result.stdout)

    def test_markdown_summary_count_must_match_finding_entries(self):
        self.write_review()
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n\n"
            "No findings.\n"
            "Summary: findings=1; worst=Missing validation.\n\n"
            "## Spec\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Standards summary count does not match Markdown findings", result.stdout)

    def test_unlabeled_markdown_finding_is_refused(self):
        self.write_review()
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n\n"
            "1. MAJOR · CONFIRMED — Missing validation.\n\n"
            "Summary: findings=0; worst=none.\n\n"
            "## Spec\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("finding entry must use the new '- [ID] <finding>' format", result.stdout)

    def test_sidecar_finding_missing_from_markdown_is_refused(self):
        finding = {"id": "S1", "disposition": "fixed", "reason": "Corrected the issue."}
        self.write_review(standards_findings=[finding])
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n\n"
            "## Spec\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Markdown finding IDs disagree with review-evidence.json", result.stdout)

    def test_finding_id_moved_to_the_wrong_axis_is_refused(self):
        finding = {"id": "S1", "disposition": "fixed", "reason": "Corrected the issue."}
        self.write_review(standards_findings=[finding])
        review_path = self.task_dir / "review" / "review.md"
        review_path.write_text(
            "# Independent review\n\n"
            "## Standards\n\n"
            "No findings.\n"
            "Summary: findings=0; worst=none.\n\n"
            "## Spec\n\n"
            "- [S1] MAJOR · CONFIRMED — Missing validation.\n"
            "Summary: findings=1; worst=Missing validation.\n"
        )

        result = self.command()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Markdown finding IDs disagree with review-evidence.json", result.stdout)

    def test_capture_of_a_different_base_or_head_is_stale(self):
        self.write_review(base=self.head)

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("no complete review capture chain", result.stdout)

    def test_capture_that_ends_before_the_candidate_is_stale(self):
        self.write_review(head=self.base)

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("no complete review capture chain", result.stdout)

    def test_revision_review_requires_unfixed_field(self):
        done_path = self.add_delta_review(unfixed=None)
        done = json.loads(done_path.read_text())
        self.assertNotIn("unfixed", done)

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("'unfixed'", result.stdout)

    def test_revision_review_requires_zero_unfixed_findings(self):
        self.add_delta_review(unfixed=1)

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("unfixed", result.stdout)

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
        self.add_delta_review()

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

    def test_omitted_rostered_coding_candidate_blocks_batch(self):
        self.write_review()
        self.add_roster_only_candidate()

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("unaccounted coding candidates: task-2", result.stdout)

    def test_omitted_result_candidate_blocks_batch(self):
        self.write_review()
        self.add_result_only_candidate()

        result = self.command()

        self.assertEqual(result.returncode, 1)
        self.assertIn("unaccounted coding candidates: task-2", result.stdout)

    def test_candidate_result_without_task_id_is_rejected(self):
        self.add_result_only_candidate(include_task_id=False)

        result = self.command()

        self.assertEqual(result.returncode, 2)
        self.assertIn("candidate result has no valid task_id", result.stderr)

    def test_deferred_candidate_is_recorded_and_allows_partial_batch(self):
        self.install_review_fixture()
        self.add_roster_only_candidate()

        result = self.command("--defer", "task-2:waiting for review")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report_path = json.loads(result.stdout)["evidence"]
        report = json.loads(Path(report_path).read_text())
        self.assertEqual(report["outcome"], "ready")
        self.assertEqual(
            report["deferred"],
            [{"task_id": "task-2", "reason": "waiting for review"}],
        )


if __name__ == "__main__":
    unittest.main()
