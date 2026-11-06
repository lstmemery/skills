from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))

from contextlib import redirect_stdout
import io

from herdr_jobs.claude_trust import config_path, prune_trust, trust_cwd
from herdr_jobs.cli import main
from herdr_jobs.records import JobError


class ClaudeTrustTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cwd = self.root / 'worker "quoted" café'
        self.cwd.mkdir()
        self.config = self.root / "claude-config.json"

    def test_only_exact_canonical_cwd_is_trusted_with_durable_private_backup(self):
        alias = self.root / "alias"
        alias.symlink_to(self.cwd)
        original = (b'{\r\n "mcpServers": {"fixture": {"command": "fake-server"}}, '
                    b'"projects": {"/unrelated": {"hasTrustDialogAccepted": false}}, "number": 1.2300e+9\r\n}\r\n')
        self.config.write_bytes(original)
        result = trust_cwd(alias, self.config)
        self.assertTrue(result["changed"])
        self.assertEqual(result["cwd"], str(self.cwd))
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        self.assertEqual(stat.S_IMODE(Path(result["backup"]).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        document = json.loads(self.config.read_bytes())
        self.assertEqual(document["projects"], {
            "/unrelated": {"hasTrustDialogAccepted": False},
            str(self.cwd): {"hasTrustDialogAccepted": True},
        })
        self.assertIn(b'"number": 1.2300e+9\r\n', self.config.read_bytes())
        self.assertNotIn(str(self.root), document["projects"])
        self.assertNotIn(str(alias), document["projects"])

    def test_existing_project_changes_only_boolean_and_second_call_writes_nothing(self):
        original = ('{"projects": {' + json.dumps(str(self.cwd)) +
                    ': {"mcpServers": {}, "hasTrustDialogAccepted" : false, "other": 7}}}\n').encode()
        self.config.write_bytes(original)
        trust_cwd(self.cwd, self.config)
        self.assertEqual(self.config.read_bytes(), original.replace(b"false", b"true"))
        before = self.config.stat()
        with patch("herdr_jobs.claude_trust.atomic_bytes") as replace:
            result = trust_cwd(self.cwd, self.config)
        replace.assert_not_called()
        self.assertFalse(result["changed"])
        self.assertEqual(self.config.stat().st_ino, before.st_ino)
        self.assertEqual(self.config.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(len(list(self.root.glob("*.orchestrator-backup-*"))), 1)

    def test_inserts_missing_members_at_each_object_depth(self):
        for document in ({}, {"other": {"projects": {}}}, {"projects": {}},
                         {"projects": {str(self.cwd): {}}},
                         {"projects": {str(self.cwd): {"other": ["}", {"a": 1}]}}}):
            with self.subTest(document=document):
                self.config.write_text(json.dumps(document))
                trust_cwd(self.cwd, self.config)
                document.setdefault("projects", {}).setdefault(str(self.cwd), {})["hasTrustDialogAccepted"] = True
                self.assertEqual(json.loads(self.config.read_bytes()), document)

    def test_missing_config_is_created(self):
        result = trust_cwd(self.cwd, self.config)
        self.assertEqual(Path(result["backup"]).read_bytes(), b"")
        self.assertEqual(json.loads(self.config.read_bytes()), {
            "projects": {str(self.cwd): {"hasTrustDialogAccepted": True}}})

    def test_leading_whitespace_and_utf8_bom_are_preserved(self):
        for original in (b" \r\n\t{}\n", b'\xef\xbb\xbf \n{"other":7}\n'):
            with self.subTest(original=original):
                self.config.write_bytes(original)
                trust_cwd(self.cwd, self.config)
                self.assertTrue(json.loads(self.config.read_bytes())["projects"][str(self.cwd)]["hasTrustDialogAccepted"])
                self.assertTrue(self.config.read_bytes().startswith(original.split(b"{")[0]))

    def test_malformed_and_ambiguous_configs_are_untouched_without_backup_or_value_leak(self):
        for original in (b"", b"{fake-private-value", b"[]", b'{"projects":null}',
                         b'{"projects":{},"projects":{}}', b'{"other":NaN}',
                         json.dumps({"projects": {str(self.cwd): False}}).encode(), b'"\\ud800"', b"\xff"):
            with self.subTest(original=original):
                self.config.write_bytes(original)
                with self.assertRaises(JobError) as caught:
                    trust_cwd(self.cwd, self.config)
                self.assertNotIn("fake-private-value", str(caught.exception))
                self.assertEqual(self.config.read_bytes(), original)
                self.assertEqual(list(self.root.glob("*.orchestrator-backup-*")), [])

    def test_symlink_and_nonregular_config_are_refused(self):
        target = self.root / "target"
        target.write_bytes(b"{}")
        self.config.symlink_to(target)
        with self.assertRaisesRegex(JobError, "symlink"):
            trust_cwd(self.cwd, self.config)
        self.assertEqual(target.read_bytes(), b"{}")
        self.config.unlink()
        os.mkfifo(self.config)
        with self.assertRaisesRegex(JobError, "regular file"):
            trust_cwd(self.cwd, self.config)

    def test_failed_replace_retains_original_and_private_backup(self):
        self.config.write_bytes(b'{"other":7}')
        with patch("herdr_jobs.records.os.replace", side_effect=OSError("fixture replace failure")):
            with self.assertRaises(OSError):
                trust_cwd(self.cwd, self.config)
        self.assertEqual(self.config.read_bytes(), b'{"other":7}')
        backups = list(self.root.glob("*.orchestrator-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'{"other":7}')
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

    def test_detected_external_edit_is_never_overwritten(self):
        with patch("herdr_jobs.claude_trust._read", side_effect=[b"{}", b'{"external":true}']), \
                patch("herdr_jobs.claude_trust.atomic_bytes") as replace:
            with self.assertRaisesRegex(JobError, "changed outside"):
                trust_cwd(self.cwd, self.config)
        replace.assert_not_called()

    def test_concurrent_processes_preserve_all_exact_project_entries(self):
        paths = [self.root / f"worker-{index}" for index in range(8)]
        for path in paths:
            path.mkdir()
        with ProcessPoolExecutor(max_workers=4) as pool:
            list(pool.map(trust_cwd, paths, [self.config] * len(paths)))
        self.assertEqual(json.loads(self.config.read_bytes()), {
            "projects": {str(path): {"hasTrustDialogAccepted": True} for path in paths}})

    def test_native_config_paths_and_unsupported_backend_fail_closed(self):
        with patch.dict(os.environ, {"HOME": str(self.root)}, clear=True):
            self.assertEqual(config_path(), self.root / ".claude.json")
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "alternate")}):
                self.assertEqual(config_path(), self.root / "alternate/.claude.json")
                with patch.dict(os.environ, {"CLAUDE_CODE_CUSTOM_OAUTH_URL": "https://fake.invalid"}):
                    with self.assertRaisesRegex(JobError, "custom OAuth"):
                        config_path()
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "relative"}):
                with self.assertRaisesRegex(JobError, "absolute"):
                    config_path()
            (self.root / ".claude").mkdir()
            (self.root / ".claude/.config.json").write_bytes(b"{}")
            with self.assertRaisesRegex(JobError, "legacy"):
                config_path()

class ClaudePruneTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        patched = patch("herdr_jobs.codex_trust.ORCHESTRATOR_ROOT", self.root)
        patched.start()
        self.addCleanup(patched.stop)
        self.config = self.root / ".claude.json"
        (self.root / "runs" / "2026-10-01" / "live").mkdir(parents=True)
        (self.root / "runs" / "2026-11-05" / "new").mkdir(parents=True)
        (self.root / "worktrees" / "kept").mkdir(parents=True)

    def document(self, names, indent=2):
        projects = {str(self.root / name): {"hasTrustDialogAccepted": True, "keep": [1, 2]} for name in names}
        projects["/var/empty/outside-missing"] = {"hasTrustDialogAccepted": True}
        return json.dumps({"mcpServers": {"x": {"command": "y"}}, "projects": projects, "tail": 0},
                          indent=indent).replace('"tail": 0', '"tail": 1.50e+3')

    def test_dry_run_reports_missing_entries_and_changes_nothing(self):
        original = self.document(["runs/2026-10-01/gone", "runs/2026-10-01/live", "worktrees/kept"]).encode()
        self.config.write_bytes(original)
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["prune-claude-trust", "--config", str(self.config)])
        report = json.loads(output.getvalue())
        self.assertEqual((code, report["dry_run"], report["count"]), (0, True, 1))
        self.assertEqual(report["paths"], [str(self.root / "runs/2026-10-01/gone")])
        self.assertEqual(self.config.read_bytes(), original)

    def test_apply_removes_missing_entries_with_backup_and_keeps_other_bytes(self):
        for indent in (2, None):
            with self.subTest(indent=indent):
                names = ["runs/2026-10-01/gone", "runs/2026-10-01/live", "runs/2026-10-01/gone2"]
                original = self.document(names, indent).encode()
                self.config.write_bytes(original)
                result = prune_trust(self.config, apply=True)
                self.assertEqual(result["count"], 2)
                self.assertEqual(Path(result["backup"]).read_bytes(), original)
                after = json.loads(self.config.read_bytes())
                self.assertEqual(sorted(after["projects"]), sorted([
                    str(self.root / "runs/2026-10-01/live"), "/var/empty/outside-missing"]))
                self.assertEqual(after["tail"], 1500.0)
                self.assertIn(b"1.50e+3", self.config.read_bytes())

    def test_apply_handles_removing_every_trailing_and_every_project_entry(self):
        only = {"projects": {str(self.root / "runs/2026-10-01/gone"): {"hasTrustDialogAccepted": True}}, "a": 1}
        self.config.write_text(json.dumps(only))
        prune_trust(self.config, apply=True)
        self.assertEqual(json.loads(self.config.read_text()), {"projects": {}, "a": 1})
        last = {"projects": {str(self.root / "worktrees/kept"): {"x": 1},
                             str(self.root / "runs/2026-10-01/gone"): {"y": 2}}}
        self.config.write_text(json.dumps(last, indent=1))
        prune_trust(self.config, apply=True)
        self.assertEqual(list(json.loads(self.config.read_text())["projects"]), [str(self.root / "worktrees/kept")])

    def test_runs_before_prunes_only_old_run_dates_never_worktrees(self):
        self.config.write_text(self.document(["runs/2026-10-01/live", "runs/2026-11-05/new", "worktrees/kept"]))
        report = prune_trust(self.config, runs_before="2026-11-01")
        self.assertEqual(report["paths"], [str(self.root / "runs/2026-10-01/live")])
        result = prune_trust(self.config, apply=True, runs_before="2026-11-01")
        self.assertEqual(result["count"], 1)
        self.assertIn(str(self.root / "runs/2026-11-05/new"), json.loads(self.config.read_text())["projects"])

    def test_runs_before_rejects_malformed_dates(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["prune-claude-trust", "--config", str(self.config), "--runs-before", "yesterday"])
        self.assertNotEqual(code, 0)
