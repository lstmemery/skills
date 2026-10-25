import os
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.edit_scope import edit_scope_warnings


class EditScopeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.allowed = self.root / "installed"
        self.repo = self.root / "repo"
        self.allowed.mkdir()
        self.repo.mkdir()
        self.target = self.repo / "fixture.service"
        self.target.write_text("fixture remains unchanged\n")
        self.link = self.allowed / "fixture.service"
        self.link.symlink_to(self.target)

    def warnings(self, task, editable=None, mode="agent"):
        return edit_scope_warnings({
            "cwd": str(self.root), "route": {"mode": mode}, "task": task,
            "editable_paths": editable if editable is not None else [str(self.allowed)],
        })

    def test_installed_unit_reports_excluded_repo_destination_without_editing_it(self):
        for task in (f"Edit `{self.link}`.", f"Edit {self.link}.",
                     "Edit `installed/fixture.service`."):
            with self.subTest(task=task):
                warnings = self.warnings(task)
                self.assertEqual(len(warnings), 1)
                self.assertEqual(warnings[0]["resolved_path"], str(self.target))
                self.assertEqual(warnings[0]["reason"], "symlink_outside_editable_paths")
        self.assertEqual(self.target.read_text(), "fixture remains unchanged\n")
        self.assertTrue(self.link.is_symlink())

    def test_explicit_destination_authorization_clears_warning(self):
        self.assertEqual(self.warnings(f"Edit `{self.link}`.",
                                      [str(self.allowed), str(self.target)]), [])

    def test_backticked_filename_in_worker_cwd_is_checked(self):
        (self.root / "unit.service").symlink_to(self.target)
        warnings = self.warnings("Edit `unit.service`.")
        self.assertEqual(warnings[0]["resolved_path"], str(self.target))

    def test_symlink_listed_as_editable_does_not_authorize_its_destination(self):
        warnings = self.warnings("Edit the declared unit.", [str(self.link)])
        self.assertEqual(warnings[0]["resolved_path"], str(self.target))

    def test_directory_symlinks_and_new_files_are_resolved(self):
        directory = self.allowed / "units"
        directory.symlink_to(self.repo, target_is_directory=True)
        warnings = self.warnings("Create `installed/units/new.service`.")
        self.assertEqual(warnings[0]["resolved_path"], str(self.repo / "new.service"))
        self.assertFalse((self.repo / "new.service").exists())

    def test_dotdot_after_symlink_uses_the_actual_parent(self):
        nested = self.repo / "nested"
        nested.mkdir()
        (self.allowed / "units").symlink_to(nested, target_is_directory=True)
        warnings = self.warnings("Edit `installed/units/../fixture.service`.")
        self.assertEqual(warnings[0]["resolved_path"], str(self.target))

    def test_component_containment_does_not_allow_a_sibling_with_the_same_prefix(self):
        sibling = self.root / "installed-other"
        sibling.mkdir()
        (self.allowed / "escape").symlink_to(sibling, target_is_directory=True)
        warnings = self.warnings("Edit `installed/escape/new.txt`.")
        self.assertEqual(warnings[0]["resolved_path"], str(sibling / "new.txt"))

    def test_in_scope_symlinks_and_regular_paths_do_not_warn(self):
        local = self.allowed / "local.txt"
        local.write_text("local\n")
        (self.allowed / "alias.txt").symlink_to(local)
        self.assertEqual(self.warnings("Edit `installed/alias.txt` and `installed/local.txt`."), [])

    def test_home_paths_and_quoted_spaces_are_supported_and_deduplicated(self):
        spaced = self.allowed / "my unit.service"
        spaced.symlink_to(self.target)
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            warnings = self.warnings("Edit `~/installed/my unit.service` twice: `~/installed/my unit.service`.",
                                     ["~/installed"])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["resolved_path"], str(self.target))

    def test_resolution_failure_is_reported_as_unverified(self):
        loop = self.allowed / "loop"
        loop.symlink_to(loop)
        warnings = self.warnings("Edit `installed/loop`.")
        self.assertEqual(warnings[0]["reason"], "resolution_unverified")

    def test_unexpandable_home_is_a_warning_instead_of_a_preview_crash(self):
        with patch("pathlib.Path.expanduser", side_effect=RuntimeError("unknown home")):
            warnings = self.warnings("Edit the declared path.", ["~/installed"])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["reason"], "resolution_unverified")

    def test_broken_link_still_reports_its_excluded_destination(self):
        (self.allowed / "broken").symlink_to(self.repo / "missing")
        warnings = self.warnings("Create `installed/broken`.")
        self.assertEqual(warnings[0]["resolved_path"], str(self.repo / "missing"))

    def test_jail_paths_are_not_resolved_against_the_host_namespace(self):
        with patch("pathlib.Path.resolve", side_effect=AssertionError("host lookup")):
            warnings = self.warnings(f"Edit `{self.link}`.", mode="jail")
        self.assertEqual(warnings[0]["reason"], "namespace_unverified")


if __name__ == "__main__":
    unittest.main()
