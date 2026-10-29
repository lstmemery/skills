import concurrent.futures
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from herdr_jobs.codex_trust import prune_trust, trust_cwd
from herdr_jobs.records import JobError


class CodexTrustTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=PACKAGE / "tests")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config.toml"
        self.cwd = self.root / 'quoted "project" ☃'
        self.cwd.mkdir()

    def test_updates_existing_trust_without_rewriting_other_bytes(self):
        original = (f'# header\r\n[ projects . {json.dumps(str(self.cwd), ensure_ascii=False)} ] # keep\r\n'
                    "trust_level  = 'untrusted' # keep this too\r\nother = { value = 7 }\r\n"
                    '[mcp_servers.fixture]\r\ncommand="fixture"\r\n').encode()
        self.config.write_bytes(original)

        changed = trust_cwd(self.cwd, self.config)

        self.assertEqual(self.config.read_bytes(), original.replace(b"'untrusted'", b'"trusted"'))
        self.assertEqual(Path(changed["backup"]).read_bytes(), original)
        self.assertEqual(Path(changed["backup"]).stat().st_mode & 0o777, 0o600)

    def test_preserves_unrelated_nan_values_when_adding_or_updating_trust(self):
        for literal in ("nan", "+nan", "-nan"):
            for existing_project in (False, True):
                with self.subTest(literal=literal, existing_project=existing_project):
                    source = (f'# preserve formatting\r\nscore = {literal} # unchanged\r\n'
                              f'metrics = {{ scores = [{literal}, {{ value = {literal} }}] }}\r\n')
                    if existing_project:
                        source += (f'[projects.{json.dumps(str(self.cwd), ensure_ascii=False)}]\r\n'
                                   "trust_level = 'untrusted' # preserve comment\r\n")
                    original = source.encode()
                    self.config.write_bytes(original)

                    changed = trust_cwd(self.cwd, self.config)

                    saved = self.config.read_bytes()
                    if existing_project:
                        self.assertEqual(saved, original.replace(b"'untrusted'", b'"trusted"'))
                    else:
                        self.assertTrue(saved.startswith(original))
                    self.assertEqual(tomllib.loads(saved.decode())["projects"][str(self.cwd)]["trust_level"],
                                     "trusted")
                    self.assertEqual(Path(changed["backup"]).read_bytes(), original)

    def test_adds_missing_trust_to_existing_project_and_ignores_multiline_decoy(self):
        original = (f'note = """\n[projects.{json.dumps(str(self.cwd))}]\ntrust_level = "untrusted"\n"""\n'
                    f'[projects.{json.dumps(str(self.cwd))}]\nother = 7 # keep\n').encode()
        self.config.write_bytes(original)
        trust_cwd(self.cwd, self.config)
        saved = self.config.read_bytes()
        self.assertIn(original.split(b'"""\n')[0], saved)
        self.assertEqual(tomllib.loads(saved.decode())["projects"][str(self.cwd)],
                         {"other": 7, "trust_level": "trusted"})
        self.assertIn(b'other = 7 # keep\n', saved)

    def test_inline_and_dotted_projects_preserve_unrelated_values(self):
        key = json.dumps(str(self.cwd), ensure_ascii=False)
        cases = [
            f'projects = {{ {key} = {{ trust_level = "untrusted", other = 7 }} }}\n',
            f'projects = {{ {key} = {{ other = 7 }} }}\n',
            'projects = { other = { trust_level = "trusted" } }\n',
            f'projects.{key}.other = 7\n',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.config.write_text(source)
                trust_cwd(self.cwd, self.config)
                actual = tomllib.loads(self.config.read_text())
                expected = tomllib.loads(source)
                expected["projects"].setdefault(str(self.cwd), {})["trust_level"] = "trusted"
                self.assertEqual(actual, expected)
                self.assertIn('other', self.config.read_text())

    def test_trust_is_idempotent_without_another_backup_or_replace(self):
        trust_cwd(self.cwd, self.config)
        original = self.config.read_bytes()
        inode = self.config.stat().st_ino
        backups = list(self.root.glob("config.toml.orchestrator-backup-*"))
        result = trust_cwd(self.cwd, self.config)
        self.assertFalse(result["changed"])
        self.assertEqual(self.config.stat().st_ino, inode)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.root.glob("config.toml.orchestrator-backup-*")), backups)

    def test_concurrent_processes_do_not_lose_project_entries(self):
        original = b'# preserve\nmodel="fixture"\n[mcp_servers.fixture]\ncommand="fixture"\n'
        self.config.write_bytes(original)
        paths = [self.root / f"worker-{index}" for index in range(20)]
        for path in paths:
            path.mkdir()
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=4, mp_context=multiprocessing.get_context("fork")) as pool:
            results = list(pool.map(trust_cwd, paths, [self.config] * len(paths)))
        self.assertTrue(all(item["changed"] for item in results))
        self.assertTrue(self.config.read_bytes().startswith(original))
        self.assertEqual(tomllib.loads(self.config.read_text())["projects"],
                         {str(path): {"trust_level": "trusted"} for path in paths})

    def test_malformed_config_is_unchanged_without_backup(self):
        original = b'[invalid\n'
        self.config.write_bytes(original)
        with self.assertRaisesRegex(JobError, "malformed"):
            trust_cwd(self.cwd, self.config)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*backup*")), [])

    def test_failed_atomic_replace_preserves_original_and_durable_backup(self):
        original = b'model="fixture"\n'
        self.config.write_bytes(original)
        with patch("herdr_jobs.records.os.replace", side_effect=OSError("fixture replace failure")):
            with self.assertRaises(OSError):
                trust_cwd(self.cwd, self.config)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual([path.read_bytes() for path in self.root.glob("*backup*")], [original])

    def test_prune_defaults_to_dry_run_and_keeps_existing_and_outside_paths(self):
        missing = self.root / "gone"
        outside = "/var/empty/orchestrator-trust-fixture-missing"
        original = (f'[projects.{json.dumps(str(missing))}]\ntrust_level="trusted"\n'
                    f'[projects.{json.dumps(str(self.cwd))}]\ntrust_level="trusted"\n'
                    f'[projects.{json.dumps(outside)}]\ntrust_level="trusted"\n').encode()
        self.config.write_bytes(original)
        result = subprocess.run([sys.executable, str(PACKAGE / "scripts/herdr-jobs.py"),
                                 "prune-codex-trust", "--config", str(self.config)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["paths"], [str(missing)])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*backup*")), [])

    def test_prune_apply_removes_only_stale_trust_and_backs_up_original(self):
        missing = str(self.root / "gone")
        other = str(self.root / "gone-with-settings")
        original = (f'# preserve header\n[projects.{json.dumps(missing)}]\ntrust_level="trusted"\n'
                    f'[projects.{json.dumps(other)}]\ntrust_level="untrusted"\nother = 7 # keep\n'
                    f'[projects.{json.dumps(str(self.cwd))}]\ntrust_level="trusted"\n'
                    '[mcp_servers.fixture]\ncommand="fixture"\n').encode()
        self.config.write_bytes(original)
        report = prune_trust(self.config, apply=True)
        self.assertFalse(report["dry_run"])
        self.assertEqual(report["count"], 2)
        actual = tomllib.loads(self.config.read_text())
        self.assertNotIn(missing, actual["projects"])
        self.assertEqual(actual["projects"][other], {"other": 7})
        self.assertEqual(actual["projects"][str(self.cwd)], {"trust_level": "trusted"})
        self.assertIn(b'other = 7 # keep\n', self.config.read_bytes())
        self.assertIn(b'[mcp_servers.fixture]\ncommand="fixture"\n', self.config.read_bytes())
        self.assertEqual(Path(report["backup"]).read_bytes(), original)
        self.assertEqual(prune_trust(self.config)["count"], 0)

    def test_prune_inline_and_dotted_entries_preserves_live_and_nontrust_values(self):
        missing = json.dumps(str(self.root / "gone"))
        other = json.dumps(str(self.root / "gone-with-settings"))
        live = json.dumps(str(self.cwd))
        cases = [
            f'projects = {{ {missing} = {{trust_level="trusted"}}, {other} = {{trust_level="trusted", other=7}}, '
            f'{live} = {{trust_level="trusted"}} }}\n',
            f'projects.{missing}.trust_level="trusted"\nprojects.{other}.trust_level="trusted"\n'
            f'projects.{other}.other=7\nprojects.{live}.trust_level="trusted"\n',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.config.write_text(source)
                self.assertEqual(prune_trust(self.config, apply=True)["count"], 2)
                self.assertEqual(tomllib.loads(self.config.read_text())["projects"],
                                 {str(self.root / "gone-with-settings"): {"other": 7},
                                  str(self.cwd): {"trust_level": "trusted"}})

    def test_prune_keeps_dangling_symlinks_sibling_roots_and_parent_traversals(self):
        link = self.root / "dangling"
        link.symlink_to(self.root / "missing-target")
        paths = [str(link), "/home/deadhand/.local/share/orchestrator-other/missing",
                 "/home/deadhand/.local/share/orchestrator/../missing"]
        self.config.write_text("".join(f'[projects.{json.dumps(path)}]\ntrust_level="trusted"\n' for path in paths))
        self.assertEqual(prune_trust(self.config)["count"], 0)
