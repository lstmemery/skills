import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from herdr_jobs import checkout_preflight

LINKED_ONLY = '''\
import subprocess, unittest

def rev(*args):
    return subprocess.run(["git", "rev-parse", *args], capture_output=True, text=True,
                          check=True).stdout.strip()

class LinkedOnly(unittest.TestCase):
    def test_runs_in_a_linked_worktree(self):
        # Passes only where the git dir differs from the common dir.
        self.assertNotEqual(rev("--absolute-git-dir"),
                            rev("--path-format=absolute", "--git-common-dir"))
'''
ANYWHERE = '''\
import unittest

class Anywhere(unittest.TestCase):
    def test_a(self):
        self.assertTrue(True)

    def test_b(self):
        self.assertTrue(True)
'''


def git(*args, cwd=None):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=environment).stdout.strip()


class ManifestCoverageTest(unittest.TestCase):
    PATTERN = re.compile(r'"git"|git_|git-common|worktree add|\.git\b|rev-parse|"init"|worktree', re.I)

    def test_manifest_names_every_git_or_worktree_test_module(self):
        manifest = json.loads((SKILL_DIR / "tests/checkout-sensitive.json").read_text())
        listed = {entry["module"] for entry in manifest["tests"]}
        for entry in manifest["tests"]:
            self.assertTrue(entry["reason"])
            self.assertTrue((SKILL_DIR / "tests" / f"{entry['module']}.py").is_file(), entry)
        detected = {
            path.stem for path in (SKILL_DIR / "tests").glob("test_*.py")
            if self.PATTERN.search(path.read_text()) and path.stem != Path(__file__).stem
            and path.stem != "test_premise_preflight_docs"
        }
        self.assertEqual(sorted(detected - listed), [],
                         "git/worktree tests missing from tests/checkout-sensitive.json")

    def test_loader_rejects_a_malformed_manifest(self):
        with tempfile.TemporaryDirectory() as tree:
            path = Path(tree) / checkout_preflight.MANIFEST
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"version": 1, "cwd": "skills/orchestrator",
                                        "tests": [{"module": "../evil"}]}))
            with self.assertRaises(checkout_preflight.CheckoutError):
                checkout_preflight.load_manifest(tree)


class FreshPrimaryCheckoutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.tests = self.repo / "skills/orchestrator/tests"
        self.tests.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("config", "user.name", "T", cwd=self.repo)
        git("config", "user.email", "t@invalid", cwd=self.repo)
        (self.repo / "README.md").write_text("base\n")
        self.commit("base")
        self.base = git("rev-parse", "HEAD", cwd=self.repo)

    def commit(self, message):
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", message, cwd=self.repo)

    def declare(self, **modules):
        for name, body in modules.items():
            (self.tests / f"{name}.py").write_text(body)
        (self.tests / "checkout-sensitive.json").write_text(json.dumps({
            "version": 1, "cwd": "skills/orchestrator",
            "tests": [{"module": name, "reason": "synthetic"} for name in modules],
        }))
        self.commit("declare tests")
        return git("rev-parse", "HEAD", cwd=self.repo)

    def candidate(self, name="cand"):
        git("switch", "-q", "-c", name, self.base, cwd=self.repo)
        (self.repo / f"{name}.txt").write_text("x\n")
        self.commit(name)
        head = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "-q", "main", cwd=self.repo)
        return head

    def test_premise_linked_worktree_passes_what_a_primary_checkout_fails(self):
        target = self.declare(test_linked_only=LINKED_ONLY)
        linked = Path(self.temp.name) / "linked"
        git("worktree", "add", "-q", "--detach", str(linked), target, cwd=self.repo)
        in_linked = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_linked_only.py"],
            cwd=linked / "skills/orchestrator", capture_output=True, text=True)
        self.assertEqual(in_linked.returncode, 0, in_linked.stderr)

    def test_linked_only_pass_blocks_integration_and_names_the_context(self):
        target = self.declare(test_linked_only=LINKED_ONLY)
        head = self.candidate()

        report = checkout_preflight.run_checkout_preflight(self.repo, target, [head])

        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["context"]["primary"])
        self.assertTrue(report["context"]["dot_git_is_directory"])
        self.assertEqual(report["context"]["git_dir"], report["context"]["git_common_dir"])
        (test,) = report["tests"]
        self.assertEqual((test["module"], test["status"]), ("test_linked_only", "failed"))
        self.assertEqual(test["checkout_context"]["kind"], "fresh-primary-checkout")
        self.assertIn("test_linked_only", report["issues"][0])

    def test_checkout_independent_tests_pass_with_counts_and_context(self):
        target = self.declare(test_anywhere=ANYWHERE)
        head = self.candidate()

        report = checkout_preflight.run_checkout_preflight(self.repo, target, [head])

        self.assertEqual(report["status"], "ready")
        (test,) = report["tests"]
        self.assertEqual((test["status"], test["tests_run"]), ("passed", 2))
        self.assertTrue(test["checkout_context"]["primary"])
        self.assertEqual(report["merged_candidates"], [head])
        self.assertNotEqual(report["context"]["head"], target)  # candidate merged in

    def test_module_that_runs_no_tests_cannot_pass(self):
        target = self.declare(test_empty="import unittest\n")
        head = self.candidate()

        report = checkout_preflight.run_checkout_preflight(self.repo, target, [head])

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["tests"][0]["tests_run"], 0)

    def test_conflicting_candidate_blocks(self):
        target = self.declare(test_anywhere=ANYWHERE)
        git("switch", "-q", "-c", "clash", self.base, cwd=self.repo)
        (self.repo / "README.md").write_text("clash\n")
        self.commit("clash")
        head = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "-q", "main", cwd=self.repo)
        (self.repo / "README.md").write_text("main\n")
        self.commit("main change")
        target = git("rev-parse", "HEAD", cwd=self.repo)

        report = checkout_preflight.run_checkout_preflight(self.repo, target, [head])

        self.assertEqual(report["status"], "blocked")
        self.assertIn("does not merge cleanly", report["issues"][0])

    def test_fully_skipped_module_cannot_pass(self):
        skipped = ("import unittest\n\nclass S(unittest.TestCase):\n"
                   "    @unittest.skip('x')\n    def test_a(self):\n        pass\n")
        target = self.declare(test_skipped=skipped)
        head = self.candidate()

        report = checkout_preflight.run_checkout_preflight(self.repo, target, [head])

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["tests"][0]["tests_skipped"], 1)

    def test_candidate_cannot_delete_the_target_manifest(self):
        target = self.declare(test_linked_only=LINKED_ONLY)
        git("switch", "-q", "-c", "nomanifest", target, cwd=self.repo)
        (self.tests / "checkout-sensitive.json").unlink()
        self.commit("delete manifest")
        head = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "-q", "main", cwd=self.repo)

        report = checkout_preflight.run_checkout_preflight(self.repo, target, [head])

        self.assertEqual(report["status"], "blocked")
        self.assertIn("removes", report["issues"][0])

    def test_candidate_cannot_drop_declared_modules(self):
        target = self.declare(test_linked_only=LINKED_ONLY, test_anywhere=ANYWHERE)
        git("switch", "-q", "-c", "dropper", target, cwd=self.repo)
        (self.tests / "checkout-sensitive.json").write_text(json.dumps({
            "version": 1, "cwd": "skills/orchestrator",
            "tests": [{"module": "test_anywhere", "reason": "synthetic"}]}))
        self.commit("drop module")
        head = git("rev-parse", "HEAD", cwd=self.repo)
        git("switch", "-q", "main", cwd=self.repo)

        report = checkout_preflight.run_checkout_preflight(self.repo, target, [head])

        self.assertEqual(report["status"], "blocked")
        self.assertIn("test_linked_only", report["issues"][0])

    def test_tree_without_manifest_is_not_applicable(self):
        head = self.candidate()
        report = checkout_preflight.run_checkout_preflight(self.repo, self.base, [head])
        self.assertEqual(report["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
