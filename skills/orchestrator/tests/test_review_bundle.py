"""Domain contracts using the shared review goldens and in-memory I/O ports."""

import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import sys
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
sys.path.insert(0, str(PACKAGE.parents[1] / "tests"))
from compatibility.builders import golden, review_evidence
from herdr_jobs import review_evidence as domain


class MemoryReviewFiles:
    def __init__(self, files):
        self.files = files
        self.reads = []

    def read(self, path):
        self.reads.append(path)
        try:
            return self.files[path]
        except KeyError:
            raise FileNotFoundError(path) from None

    read_regular = read


class MemoryCaptureVerifier:
    def __init__(self, capture, error=None):
        self.capture = capture
        self.error = error
        self.calls = []

    def verify(self, directory, capture_id):
        self.calls.append((directory, capture_id))
        if self.error is not None:
            raise self.error
        return self.capture


class ReviewBundleTests(unittest.TestCase):
    def bundle_inputs(self, case="v1-bare", name="review"):
        selected = golden("review")["cases"][case]
        self.directory = Path("task-1") / name
        self.evidence = review_evidence(
            schema_version=selected["schema_version"],
            standards=selected["standards"], spec=selected["spec"],
        )
        self.done = {"task_id": "task-1", "capture_id": "fixture-capture"}
        if name == "review":
            self.done.update(standards_findings=len(selected["standards"]),
                             spec_findings=len(selected["spec"]))
        else:
            self.done.update(new_findings=len(selected["standards"]) + len(selected["spec"]), unfixed=0)
        self.files = MemoryReviewFiles({self.directory / "review.md": selected["markdown"].encode()})
        self.save_records()
        self.response = domain.parse_review_response(selected["response"].replace("`review/", f"`{name}/"))
        self.verifier = MemoryCaptureVerifier(domain.VerifiedCapture(
            base="1" * 40, head="2" * 40, capture_id="fixture-capture",
            manifest_sha256="3" * 64, complete_sha256="4" * 64,
        ))

    def save_records(self):
        for filename, record in (("review-evidence.json", self.evidence), ("done.json", self.done)):
            self.files.files[self.directory / filename] = (
                json.dumps(record, indent=2, ensure_ascii=False) + "\n"
            ).encode()

    def read(self, **kwargs):
        return domain.ReviewBundle.read(
            self.directory, "task-1", ["coder-1", "reviewer-1"], response=self.response,
            capture_verifier=self.verifier, filesystem=self.files, **kwargs,
        )

    def test_shared_v1_v2_goldens_produce_immutable_complete_summaries(self):
        for case in golden("review")["cases"]:
            with self.subTest(case=case):
                self.bundle_inputs(case)
                bundle = self.read()
                summary = bundle.summary()
                capture = self.verifier.capture
                expected = {
                    "review_id": "review", "author_identity": "coder-1", "reviewer_identity": "reviewer-1",
                    "base": capture.base, "head": capture.head, "capture_id": capture.capture_id,
                    "review_path": "review", "finding_count": sum(
                        len(self.evidence["axes"][axis]["findings"]) for axis in domain.AXES
                    ), "markdown_compatibility": "strict",
                    "capture_manifest_sha256": capture.manifest_sha256,
                    "capture_complete_sha256": capture.complete_sha256,
                }
                for filename, field in (("review-evidence.json", "review_evidence_sha256"),
                                        ("done.json", "done_sha256"), ("review.md", "review_markdown_sha256")):
                    expected[field] = hashlib.sha256(self.files.files[self.directory / filename]).hexdigest()
                self.assertEqual(summary.to_record(), expected)
                self.assertEqual(self.verifier.calls, [(self.directory, "fixture-capture")])
                self.assertEqual(len(self.files.reads), 3)
                with self.assertRaises(FrozenInstanceError):
                    summary.finding_count = 99
                record = summary.to_record()
                record["finding_count"] = 99
                self.assertEqual(bundle.summary().to_record(), expected)

    def test_per_axis_reviewer_attribution_is_retained(self):
        self.bundle_inputs("empty")
        self.evidence["axis_reviewers"] = {"standards": "/root/standards", "spec": "/root/spec"}
        self.save_records()

        summary = self.read().summary()
        record = summary.to_record()

        self.assertEqual(record["axis_reviewers"],
                         {"standards": "/root/standards", "spec": "/root/spec"})
        record["axis_reviewers"]["spec"] = "changed"
        self.assertEqual(summary.to_record()["axis_reviewers"]["spec"], "/root/spec")

    def test_per_axis_reviewer_attribution_cannot_be_malformed_or_name_the_author(self):
        for reviewers in (None, {}, {"standards": "reviewer-1"},
                          {"standards": "reviewer-1", "spec": " "},
                          {"standards": "reviewer-1", "spec": 1},
                          {"standards": "coder-1", "spec": "reviewer-1"},
                          {"standards": "reviewer-1", "spec": "reviewer-1", "extra": "other"}):
            with self.subTest(reviewers=reviewers):
                self.bundle_inputs("empty")
                self.evidence["axis_reviewers"] = reviewers
                self.save_records()
                with self.assertRaisesRegex(domain.ReviewEvidenceError, "axis.review"):
                    self.read()
                with self.assertRaisesRegex(domain.ReviewEvidenceError, "axis.review"):
                    domain.read_disposition_evidence(self.directory, filesystem=self.files)

    def test_bundle_rejects_golden_invalid_evidence(self):
        for invalid in golden("review")["invalid"]:
            with self.subTest(case=invalid["name"]):
                self.bundle_inputs("v2-reproducer")
                self.evidence.update(invalid["top_level"])
                finding = self.evidence["axes"]["standards"]["findings"][0]
                finding.update(invalid["finding"])
                for field in invalid["remove"]:
                    del finding[field]
                self.save_records()
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read()
                with self.assertRaises(domain.ReviewEvidenceError):
                    domain.read_disposition_evidence(self.directory, filesystem=self.files)

    def test_both_stages_reject_malformed_axes_and_finding_schema(self):
        mutations = (
            lambda e: e.update(axes=[]),
            lambda e: e["axes"].pop("spec"),
            lambda e: e["axes"]["spec"].update(status="blocked"),
            lambda e: e["axes"]["spec"].update(findings={}),
            lambda e: e["axes"]["standards"]["findings"].append([]),
            lambda e: e["axes"]["standards"]["findings"][0].pop("disposition"),
            lambda e: e["axes"]["standards"]["findings"][0].update(id="bad/id"),
            lambda e: e["axes"]["standards"]["findings"][0].update(unexpected=True),
            lambda e: e["axes"]["spec"]["findings"].append(e["axes"]["standards"]["findings"][0]),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                self.bundle_inputs()
                mutate(self.evidence)
                self.save_records()
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read()
                with self.assertRaises(domain.ReviewEvidenceError):
                    domain.read_disposition_evidence(self.directory, filesystem=self.files)

    def test_integration_envelope_and_identity_are_domain_rules(self):
        changes = [
            {"task_id": "other"}, {"author_identity": "reviewer-1", "reviewer_identity": "coder-1"},
            {"reviewer_identity": "coder-1"}, {"reviewer_identity": "unrostered"},
            {"reviewer_identity": ""}, {"capture_id": ""}, {"unexpected": True},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.bundle_inputs()
                self.evidence.update(change)
                self.save_records()
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read()
        self.bundle_inputs()
        del self.evidence["author_identity"]
        self.save_records()
        with self.assertRaisesRegex(domain.ReviewEvidenceError, "unexpected fields"):
            self.read()

    def test_done_counts_and_identity_cannot_use_legacy_exception(self):
        for name, invalid in (
            ("review", {"standards_findings": True}), ("review", {"spec_findings": 1}),
            ("review", {"task_id": "other"}), ("review", {"capture_id": "other"}),
            ("review-r2", {"new_findings": False}), ("review-r2", {"unfixed": True}),
            ("review-r2", {"unfixed": 1}),
        ):
            with self.subTest(name=name, invalid=invalid):
                self.bundle_inputs(name=name)
                self.done.update(invalid)
                self.save_records()
                self.files.files[self.directory / "review.md"] = b"Old prose.\n"
                calls = []
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read(legacy_precedent=lambda observed: calls.append(observed) or "pin")
                self.assertEqual(calls, [])
        self.bundle_inputs(name="review-r2")
        del self.done["unfixed"]
        self.save_records()
        with self.assertRaisesRegex(domain.ReviewEvidenceError, "'unfixed'"):
            self.read()

    def test_terminal_and_recorded_dispositions_are_required_at_integration(self):
        for disposition, reason, response in (
            ("unresolved", "Awaiting response.", None),
            ([], "Invalid disposition.", None),
            ("rejected", " ", ("rejected", "Recorded rejection.")),
            ("fixed", 7, ("fixed", "Recorded fix.")),
            ("fixed", "Fixed.", None),
            ("rejected", "Rejected.", ("fixed", "Wrong disposition.")),
        ):
            with self.subTest(disposition=disposition, reason=reason, response=response):
                self.bundle_inputs()
                self.evidence["axes"]["standards"]["findings"][0].update(disposition=disposition, reason=reason)
                self.response = {} if response is None else {("review", "S1"): response}
                self.save_records()
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read()

    def test_capture_port_failure_or_mismatched_id_cannot_use_legacy_policy(self):
        for failure in (True, False):
            with self.subTest(failure=failure):
                self.bundle_inputs()
                if failure:
                    self.verifier.error = domain.ReviewEvidenceError("capture coverage is incomplete")
                else:
                    self.verifier.capture = replace(self.verifier.capture, capture_id="wrong")
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read(legacy_precedent=lambda observed: self.fail("legacy policy bypassed capture"))

    def test_legacy_policy_is_explicit_and_only_handles_strict_parse_failure(self):
        self.bundle_inputs("empty")
        self.files.files[self.directory / "review.md"] = b"Historical prose.\n"
        with self.assertRaises(domain.ReviewEvidenceError):
            self.read()
        observed = []
        bundle = self.read(legacy_precedent=lambda record: observed.append(record) or "synthetic-pin")
        summary = bundle.summary().to_record()
        self.assertEqual(summary["markdown_compatibility"], "allowlisted-pre-change")
        self.assertEqual(summary["legacy_precedent_sha256"], "synthetic-pin")
        self.assertEqual(observed[0], {
            key: summary[key] for key in (
                "review_id", "base", "head", "capture_id", "author_identity", "reviewer_identity",
                "review_markdown_sha256", "review_evidence_sha256", "done_sha256",
                "capture_manifest_sha256", "capture_complete_sha256",
            )
        } | {"finding_ids": {"standards": [], "spec": []}})
        with self.assertRaises(domain.ReviewEvidenceError):
            self.read(legacy_precedent=lambda record: None)
        self.bundle_inputs()
        self.files.files[self.directory / "review.md"] = golden("review")["cases"]["empty"]["markdown"].encode()
        with self.assertRaisesRegex(domain.ReviewEvidenceError, "Markdown finding IDs disagree"):
            self.read(legacy_precedent=lambda observed: self.fail("legacy policy bypassed ID mismatch"))

    def test_sync_accepts_staged_sidecar_without_a_complete_bundle(self):
        self.bundle_inputs()
        self.evidence = {key: self.evidence[key] for key in ("schema_version", "axes")}
        self.evidence["extension"] = "preserved"
        self.evidence["axes"]["standards"]["extension"] = "preserved"
        self.evidence["axes"]["standards"]["findings"][0].update(disposition="pending")
        self.save_records()
        self.files.files = {self.directory / domain.EVIDENCE_NAME: self.files.files[self.directory / domain.EVIDENCE_NAME]}
        document = domain.read_disposition_evidence(self.directory, filesystem=self.files)
        changes = domain.apply_dispositions([document], self.response)
        self.assertEqual(len(changes), 1)
        expected = copy.deepcopy(self.evidence)
        expected["axes"]["standards"]["findings"][0].update(disposition="fixed", reason="Added the guard.")
        self.assertEqual(json.loads(changes[0].replacement), expected)
        self.assertEqual(json.loads(document.original), self.evidence)
        with self.assertRaises(domain.ReviewEvidenceError):
            self.read()

    def test_apply_dispositions_preserves_input_and_validates_whole_response(self):
        self.bundle_inputs("v2-reproducer")
        finding = self.evidence["axes"]["standards"]["findings"][0]
        finding.update(disposition="unresolved", reason="Awaiting response.")
        self.save_records()
        before = dict(self.files.files)
        document = domain.read_disposition_evidence(self.directory, filesystem=self.files)
        for response, error in (({}, "missing disposition"), ({("review", "unknown"): ("fixed", "Fixed.")}, "unknown finding")):
            with self.subTest(response=response):
                with self.assertRaisesRegex(domain.ReviewEvidenceError, error):
                    domain.apply_dispositions([document], response)
                self.assertEqual(self.files.files, before)
        changes = domain.apply_dispositions([document], self.response)
        self.assertEqual(changes[0].original, document.original)
        self.assertEqual(self.files.files, before)
        expected = copy.deepcopy(self.evidence)
        expected["axes"]["standards"]["findings"][0].update(disposition="fixed", reason="Added the guard.")
        self.assertEqual(json.loads(changes[0].replacement), expected)
        self.files.files[document.path] = changes[0].replacement
        synced = domain.read_disposition_evidence(self.directory, filesystem=self.files)
        self.assertEqual(domain.apply_dispositions([synced], self.response), ())

    def test_raw_json_and_required_sidecars_fail_at_the_domain_boundary(self):
        for name, raw in (
            ("review-evidence.json", b"[]"), ("review-evidence.json", b"{"),
            ("review-evidence.json", bytes([255])), ("done.json", b"[]"),
            ("done.json", b"{"), ("review.md", bytes([255])),
        ):
            with self.subTest(name=name, raw=raw):
                self.bundle_inputs()
                self.files.files[self.directory / name] = raw
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read()
        for name in ("review-evidence.json", "done.json", "review.md"):
            with self.subTest(missing=name):
                self.bundle_inputs()
                del self.files.files[self.directory / name]
                with self.assertRaises(domain.ReviewEvidenceError):
                    self.read()

    def test_selected_review_author_consistency_is_a_domain_invariant(self):
        domain.validate_chain_authors([{"author_identity": "author"}, {"author_identity": "author"}])
        with self.assertRaisesRegex(domain.ReviewEvidenceError, "disagree about the candidate author"):
            domain.validate_chain_authors([{"author_identity": "author"}, {"author_identity": "other"}])


if __name__ == "__main__":
    unittest.main()
