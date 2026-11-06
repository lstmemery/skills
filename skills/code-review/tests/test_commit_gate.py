import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
HOOK = Path(__file__).resolve().parents[1] / 'hooks' / 'pre-commit'
sys.path.insert(0, str(SCRIPTS))

import commit_gate  # noqa: E402
from capture_secrets import FINGERPRINT_ENV, SECRETS_SCANNER_ENV  # noqa: E402
from capture_common import digest  # noqa: E402

# Assembled at runtime so this file never holds a scannable secret shape.
FAKE_AWS = 'AK' + 'IA' + 'Q7ZXN4WMD2PLR5TB'
FAKE_TOKEN = 'gh' + 'p_' + 'a8F3kQ9zLm2XvB7nR4tY6wC1dE5hJ0sUoPiG'
HEX_DIGEST = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4'


def clean_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('GIT_', 'GITLEAKS_', 'CODE_REVIEW_', 'COMMIT_GATE'))}
    env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
    return env


class GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, env=clean_env())

    def stage(self, name, text):
        (self.repo / name).write_text(text)
        self.git('add', name)

    def hook(self, **extra):
        env = clean_env() | extra
        return subprocess.run([str(HOOK)], cwd=self.repo, env=env, capture_output=True, text=True)


@unittest.skipUnless(shutil.which('gitleaks'), 'gitleaks not installed')
class CommitGateTest(GateCase):
    def test_blocks_fake_key_without_printing_it(self):
        self.stage('cfg.txt', f'aws_access_key_id = {FAKE_AWS}\ntoken: {FAKE_TOKEN}\n')
        result = self.hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn('secret_detected', result.stderr)
        for secret in (FAKE_AWS, FAKE_TOKEN):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_hex_digest_is_not_a_false_positive(self):
        self.stage('lock.txt', f'api_key = "{HEX_DIGEST}"\nsecret_token: {HEX_DIGEST}\ncommit {HEX_DIGEST}\n')
        result = self.hook()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_staged_content_is_scanned(self):
        self.stage('ok.txt', 'hello\n')
        (self.repo / 'ok.txt').write_text(f'key = {FAKE_AWS}\n')  # unstaged edit
        self.assertEqual(self.hook().returncode, 0)

    def test_staged_blob_not_worktree_is_scanned(self):
        self.stage('a.txt', f'key = {FAKE_AWS}\n')
        (self.repo / 'a.txt').write_text('clean\n')  # worktree cleaned, index still dirty
        self.assertEqual(self.hook().returncode, 1)

    def test_live_fingerprint_denylist_blocks(self):
        value = 'plain-looking-value-0042'
        deny = self.root / 'deny.txt'
        deny.write_text(digest(value.encode())[:16] + '\n')
        self.stage('n.txt', f'note = {value}\n')
        result = self.hook(**{FINGERPRINT_ENV: str(deny)})
        self.assertEqual(result.returncode, 1)
        self.assertIn('fingerprint-denylist', result.stderr)
        self.assertNotIn(value, result.stderr)

    def test_malformed_denylist_fails_closed(self):
        deny = self.root / 'bad.txt'
        deny.write_text('not-hex\n')
        self.stage('n.txt', 'x\n')
        self.assertEqual(self.hook(**{FINGERPRINT_ENV: str(deny)}).returncode, 2)

    def test_stdlib_only_optout_cannot_disable_gate(self):
        self.stage('a.txt', f'key = {FAKE_AWS}\n')
        self.assertEqual(self.hook(**{SECRETS_SCANNER_ENV: 'stdlib-only'}).returncode, 1)

    def test_nothing_staged_is_clean(self):
        self.assertEqual(self.hook().returncode, 0)


class FailClosedTest(GateCase):
    def test_missing_gitleaks_blocks(self):
        self.stage('a.txt', 'harmless\n')
        with patch('capture_secrets.shutil.which', return_value=None):
            code, result = commit_gate.run(self.repo)
        self.assertEqual(code, 1)
        self.assertIn('gitleaks did not run', result['message'])

    def test_scanner_failure_blocks(self):
        class Broken:
            def scan(self, *args, **kwargs):
                return [], 'failed'
        self.stage('a.txt', 'harmless\n')
        code, result = commit_gate.run(self.repo, scanner=Broken())
        self.assertEqual(code, 1)
        self.assertIn('scanner failed', result['message'])


if __name__ == '__main__':
    unittest.main()
