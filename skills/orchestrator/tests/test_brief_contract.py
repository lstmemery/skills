"""Dispatch regressions for declared dependencies and intended effects."""

import json
import unittest

import test_jobs


class BriefContractTest(unittest.TestCase):
    setUp = test_jobs.JobsTest.setUp
    write_jobs = test_jobs.JobsTest.write_jobs
    configure = test_jobs.JobsTest.configure
    argv = test_jobs.JobsTest.argv
    call = test_jobs.JobsTest.call

    def assert_refused(self, job_changes, message):
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0].update(job_changes)
        self.manifest.write_text(json.dumps(manifest))
        for operation in ("run", "add"):
            for preview in (True, False):
                with self.subTest(operation=operation, preview=preview):
                    code, result = self.call(operation, **({"preview": True} if preview else {}))
                    self.assertNotEqual(code, 0)
                    self.assertEqual(result["error"], "invalid_input")
                    self.assertIn(message, result["message"])
                    self.assertFalse(self.run.exists())
                    self.assertFalse((self.root / "events.json").exists())

    def test_q3_remote_mutation_requires_explicit_remote_authority(self):
        self.assert_refused({"mutation_targets": [
            {"host": "pve", "path": "/etc/systemd/system/fixture.service",
             "canonical_path": "/etc/systemd/system/fixture.service"},
        ]}, "remote authority")

    def test_q14_desktop_context_cannot_be_looked_up_on_pve(self):
        self.assert_refused({"dependencies": [
            {"host": "desktop", "lookup_host": "pve", "path": str(self.context_test),
             "canonical_path": str(self.context_test)},
        ]}, "lookup host")

    def test_reviewer_dependency_stays_bound_to_author_run(self):
        author_run = self.root / "author-run"
        reviewer_run = self.root / "reviewer-run"
        for run in (author_run, reviewer_run):
            (run / "out-author").mkdir(parents=True)
            (run / "out-author/report.md").write_text("A plausible report.\n")
        wrong = reviewer_run / "out-author/report.md"
        self.assert_refused({"dependencies": [
            {"host": "desktop", "path": str(wrong), "canonical_path": str(wrong),
             "source": {"request_id": "author-run", "job_id": "author", "run_dir": str(author_run)}},
        ]}, "source identity")

    def test_g2_required_context_edit_must_be_in_editable_scope(self):
        context = self.root / "proxmox" / "CONTEXT.md"
        context.parent.mkdir()
        context.write_text("Backup schedule.\n")
        self.assert_refused({"editable_paths": [str(self.root / "out-g2")],
                             "mutation_targets": [
            {"host": "desktop", "path": str(context), "canonical_path": str(context)},
        ]}, "outside editable_paths")

    def test_valid_contract_is_reviewable_in_preview_and_worker_prompt(self):
        author = self.root / "author-run" / "out-author" / "report.md"
        author.parent.mkdir(parents=True)
        author.write_text("Author report.\n")
        target = self.root / "source" / "new.txt"
        contract = {
            "dependencies": [{"host": "desktop", "path": str(author), "canonical_path": str(author),
                              "source": {"request_id": "author-run", "job_id": "author",
                                         "run_dir": str(author.parent.parent)}}],
            "mutation_targets": [
                {"host": "desktop", "path": str(target), "canonical_path": str(target)},
                {"host": "pve", "path": "/etc/fixture.conf", "canonical_path": "/etc/fixture.conf"}],
            "remote_authority": [{"host": "pve", "paths": ["/etc/fixture.conf"], "operations": ["write"],
                                  "instruction": "User approved fixture configuration update."}],
        }
        manifest = json.loads(self.manifest.read_text())
        manifest["jobs"][0].update(contract, editable_paths=[str(target.parent)])
        self.manifest.write_text(json.dumps(manifest))
        for operation in ("run", "add"):
            code, result = self.call(operation, preview=True)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["jobs"][0].get("brief_contract"), contract)
        code, result = self.call()
        self.assertEqual(code, 0, result)
        prompt = (self.root / "prompt-job0.txt").read_text()
        self.assertIn("brief_contract: " + json.dumps(contract, sort_keys=True), prompt)

    def test_structured_symlink_target_requires_destination_scope(self):
        installed = self.root / "installed"
        installed.mkdir()
        link = installed / "context.md"
        link.symlink_to(self.context_test)
        self.assert_refused({"editable_paths": [str(installed)], "mutation_targets": [
            {"host": "desktop", "path": str(link), "canonical_path": str(self.context_test)},
        ]}, "outside editable_paths")

    def test_remote_grant_does_not_authorize_local_context_edits(self):
        self.assert_refused({"mutation_targets": [
            {"host": "desktop", "path": str(self.context_test), "canonical_path": str(self.context_test)},
        ], "remote_authority": [{"host": "pve", "paths": ["/"], "operations": ["write"],
                                  "instruction": "User approved remote fixture changes."}]}, "outside editable_paths")

    def test_remote_read_grant_cannot_authorize_a_write(self):
        self.assert_refused({"mutation_targets": [
            {"host": "pve", "path": "/etc/fixture", "canonical_path": "/etc/fixture"},
        ], "remote_authority": [{"host": "pve", "paths": ["/etc"], "operations": ["read"],
                                  "instruction": "User approved reading configuration."}]}, "remote authority")

    def test_remote_scope_must_cover_canonical_destination_by_components(self):
        self.assert_refused({"mutation_targets": [
            {"host": "pve", "path": "/etc/fixture/link", "canonical_path": "/etc/fixture-other/config"},
        ], "remote_authority": [{"host": "pve", "paths": ["/etc/fixture"], "operations": ["write"],
                                  "instruction": "User approved fixture edits."}]}, "remote authority")

    def test_missing_local_dependency_is_refused(self):
        missing = self.root / "missing.md"
        self.assert_refused({"dependencies": [
            {"host": "desktop", "path": str(missing), "canonical_path": str(missing)},
        ]}, "resolution unverified")

    def test_canonical_destination_must_match_current_local_resolution(self):
        self.assert_refused({"dependencies": [
            {"host": "desktop", "path": str(self.context_test), "canonical_path": str(self.context_fixture)},
        ]}, "canonical_path")

    def test_default_protection_applies_relative_to_worker_cwd(self):
        work = self.root / "work"
        work.mkdir()
        protected = work / "keys" / "test-only.txt"
        self.assert_refused({"cwd": str(work), "editable_paths": [str(protected)], "mutation_targets": [
            {"host": "desktop", "path": str(protected), "canonical_path": str(protected)},
        ]}, "protected")

    def test_structured_host_paths_are_not_resolved_in_a_jail(self):
        self.write_jobs(1, kinds=["deep_research"])
        self.assert_refused({"dependencies": [
            {"host": "desktop", "path": str(self.context_test), "canonical_path": str(self.context_test)},
        ]}, "jail is unverified")


if __name__ == "__main__":
    unittest.main()
