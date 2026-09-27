import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / 'skills/code-review/scripts/capture.py'
SPEC = importlib.util.spec_from_file_location('capture', SCRIPT)
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'file.txt').write_text('first\n')
        (self.repo / 'context.txt').write_text('unchanged context\n')
        self.commit('initial')
        self.initial = self.git('rev-parse', 'HEAD').strip()
        self.number = 0

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-m', message)

    def invoke(self, command, *args, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), command, *map(str, args)],
                                capture_output=True, text=True, env=os.environ | (env or {}))
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def capture(self, mode='wip', base=None, extra=()):
        self.number += 1
        out = self.root / f'capture-{self.number}'
        args = ['--repo', self.repo, '--mode', mode, '--out', out]
        if base:
            args += ['--base', base]
        code, result = self.invoke('capture', *args, *extra)
        self.assertEqual(code, 0, result)
        return out, result

    def test_wip_staged_unstaged_untracked_and_saved_context(self):
        (self.repo / 'file.txt').write_text('staged\n')
        self.git('add', 'file.txt')
        (self.repo / 'file.txt').write_text('unstaged final\n')
        (self.repo / 'new file\nwith newline.txt').write_text('new content\n')
        (self.repo / '.gitignore').write_text('ignored.txt\n')
        (self.repo / 'ignored.txt').write_text('excluded\n')
        out, result = self.capture()
        self.assertEqual(result['coverage'], 'complete')
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertIn('new file\nwith newline.txt', manifest['untracked'])
        self.assertNotIn('ignored.txt', manifest['after'])
        code, read = self.invoke('read', '--capture', out, '--path', 'file.txt')
        self.assertEqual(code, 0)
        self.assertEqual(read['text'], 'unstaged final\n')
        (self.repo / 'context.txt').write_text('changed after capture\n')
        self.assertEqual(self.invoke('read', '--capture', out, '--path', 'context.txt')[1]['text'], 'unchanged context\n')
        code, current = self.invoke('check', '--capture', out)
        self.assertEqual(code, 3, current)
        self.assertTrue(current['fresh_review_required'])

    def test_branch_and_exact_base_are_different(self):
        self.git('checkout', '-b', 'feature')
        (self.repo / 'feature.txt').write_text('feature\n')
        self.commit('feature work')
        self.git('checkout', 'main')
        (self.repo / 'landing.txt').write_text('landing change\n')
        self.commit('landing work')
        self.git('checkout', 'feature')
        branch, _ = self.capture('branch', 'main')
        exact, _ = self.capture('since', 'main')
        bm = json.loads((branch / 'manifest.json').read_text())
        em = json.loads((exact / 'manifest.json').read_text())
        self.assertEqual(bm['effective_base'], self.initial)
        self.assertEqual(bm['changed'], ['feature.txt'])
        self.assertEqual(em['changed'], ['feature.txt', 'landing.txt'])
        self.assertIn('feature work', (branch / 'commits.txt').read_text())

    def test_ref_movement_and_dirty_committed_checkout(self):
        out, _ = self.capture('since', self.initial)
        (self.repo / 'file.txt').write_text('dirty\n')
        code, result = self.invoke('check', '--capture', out)
        self.assertEqual(code, 3)
        self.assertTrue(result['checkout_has_uncommitted_changes'])
        self.assertFalse(result['selected_scope_changed'])
        self.commit('new head')
        self.assertTrue(self.invoke('check', '--capture', out)[1]['selected_scope_changed'])

    def test_empty_and_untracked_only(self):
        _, empty = self.capture()
        self.assertTrue(empty['empty'])
        (self.repo / 'new.txt').write_text('only untracked\n')
        _, nonempty = self.capture()
        self.assertFalse(nonempty['empty'])

    def test_binary_symlink_and_fifo_are_visible_gaps(self):
        (self.repo / 'binary').write_bytes(b'\x00\xff')
        outside = self.root / 'outside'
        outside.write_text('not followed\n')
        (self.repo / 'link').symlink_to(outside)
        os.mkfifo(self.repo / 'pipe')
        out, result = self.capture()
        self.assertEqual(result['coverage'], 'incomplete')
        paths = {gap['path'] for gap in result['gaps']}
        self.assertIn('binary', paths)
        self.assertIn('link', paths)
        self.assertIn('pipe', paths)
        manifest = json.loads((out / 'manifest.json').read_text())
        data = [path.read_bytes() for path in (out / 'blobs').iterdir()]
        self.assertNotIn(b'not followed\n', data)
        self.assertIn('pipe', manifest['untracked'])
        self.assertEqual(self.invoke('verify', '--capture', out)[0], 0)

    def test_rename_and_delete_preserve_both_sides(self):
        self.git('mv', 'file.txt', 'renamed.txt')
        (self.repo / 'context.txt').unlink()
        out, _ = self.capture()
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertEqual(manifest['changed'], ['context.txt', 'file.txt', 'renamed.txt'])
        self.assertEqual(self.invoke('read', '--capture', out, '--side', 'before', '--path', 'file.txt')[1]['text'], 'first\n')
        self.assertNotEqual(self.invoke('read', '--capture', out, '--path', 'file.txt')[0], 0)

    def test_preview_does_not_write_or_read_bodies(self):
        out = self.root / 'preview'
        (self.repo / 'huge').write_bytes(b'x' * (CAPTURE.MAX_FILE + 1))
        code, result = self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', out, '--preview')
        self.assertEqual(code, 0, result)
        self.assertIn('huge', result['untracked'])
        self.assertFalse(out.exists())

    def test_no_overwrite_and_output_outside_repo(self):
        out, _ = self.capture()
        self.assertEqual(self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', out)[0], 3)
        self.assertEqual(self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', self.repo / 'capture')[0], 2)

    def test_missing_ref_and_unborn_repository_are_explicit(self):
        code, result = self.invoke('capture', '--repo', self.repo, '--mode', 'since', '--base', 'does-not-exist', '--out', self.root / 'bad')
        self.assertEqual(code, 2, result)
        self.assertFalse((self.root / 'bad').exists())
        unborn = self.root / 'unborn'
        unborn.mkdir()
        subprocess.run(['git', 'init', str(unborn)], capture_output=True, check=True)
        code, result = self.invoke('capture', '--repo', unborn, '--mode', 'wip', '--out', self.root / 'bad2')
        self.assertEqual(code, 2, result)
        self.assertIn('Git command failed', result['message'])

    def test_authority_snapshots_and_drift(self):
        spec = self.root / 'spec.md'
        spec.write_text('Require original behavior.\n')
        out, result = self.capture(extra=['--authority', spec])
        self.assertEqual(result['coverage'], 'complete')
        spec.write_text('Different requirement.\n')
        self.assertEqual(self.invoke('check', '--capture', out)[0], 3)
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertEqual((out / 'blobs' / manifest['authorities'][0]['sha256']).read_text(), 'Require original behavior.\n')

    def test_missing_authority_keeps_capture_partial(self):
        _, result = self.capture(extra=['--authority', self.root / 'missing.md'])
        self.assertEqual(result['coverage'], 'incomplete')
        self.assertEqual(result['gaps'][0]['side'], 'authority')

    def test_corrupt_blob_and_manifest(self):
        out, _ = self.capture()
        blob = next((out / 'blobs').iterdir())
        blob.write_bytes(b'corrupt')
        self.assertEqual(self.invoke('verify', '--capture', out)[0], 4)
        (out / 'manifest.json').write_text('{}')
        self.assertEqual(self.invoke('verify', '--capture', out)[0], 2)

    def test_external_diff_not_executed(self):
        marker = self.root / 'should-not-exist'
        external = self.root / 'external.sh'
        external.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
        external.chmod(0o755)
        self.git('config', 'diff.external', str(external))
        (self.repo / 'file.txt').write_text('changed\n')
        self.capture()
        self.assertFalse(marker.exists())

    def test_source_change_during_capture_rejected(self):
        original = CAPTURE.observe
        count = 0

        def changing(*args):
            nonlocal count
            result = original(*args)
            count += 1
            if count == 1:
                (self.repo / 'file.txt').write_text('changed during capture\n')
            return result

        with patch.object(CAPTURE, 'observe', changing):
            with self.assertRaises(CAPTURE.Failure) as raised:
                CAPTURE.verified_observation(self.repo, 'wip', None, [])
        self.assertEqual(raised.exception.result['outcome'], 'conflict')

    def test_index_change_is_detected_even_with_same_worktree(self):
        (self.repo / 'file.txt').write_text('working\n')
        out, _ = self.capture()
        self.git('add', 'file.txt')
        self.assertEqual(self.invoke('check', '--capture', out)[0], 3)

    def test_large_file_is_a_gap(self):
        (self.repo / 'large').write_bytes(b'x' * (CAPTURE.MAX_FILE + 1))
        _, result = self.capture()
        self.assertEqual(result['coverage'], 'incomplete')
        self.assertEqual(result['gaps'][0]['path'], 'large')

    def refused_capture(self, out='sealed', env=None):
        code, result = self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', self.root / out, env=env)
        self.assertEqual(code, 6, result)
        self.assertEqual(result['outcome'], 'secret_detected')
        self.assertFalse((self.root / out).exists())
        return result

    def test_capture_refuses_secret_in_payload(self):
        token = 'ghp_' + secrets.token_hex(18)
        (self.repo / 'leak.txt').write_text(f'token = "{token}"\n')
        result = self.refused_capture()
        matching = [row for row in result['findings'] if row['rule'] == 'github-token']
        self.assertTrue(matching)
        self.assertTrue(matching[0]['file'].endswith('leak.txt'))
        self.assertEqual(matching[0]['line'], 1)
        self.assertEqual(matching[0]['length'], len(token))
        self.assertNotIn(token, json.dumps(result))

    def test_hex_digests_uuids_and_shas_are_not_flagged(self):
        value = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        (self.repo / 'digests.txt').write_text(
            f'checksum = {value}\nrequest_id = {uuid.uuid4()}\nhead = {value[:40]}\n'
            f"token_fingerprint = '{value}'\nsecret = \"{value[:32]}\"\n")
        out, result = self.capture()
        self.assertEqual(result['coverage'], 'complete')
        self.assertIn(result['secret_scan']['gitleaks'], ('used', 'unavailable', 'failed'))

    def test_private_key_block_is_refused(self):
        body = base64.b64encode(secrets.token_bytes(180)).decode()
        (self.repo / 'id.key').write_text('\n'.join(['-----BEGIN PRIVATE KEY' + '-----', body, '-----END PRIVATE KEY' + '-----', '']))
        result = self.refused_capture()
        self.assertIn('private-key-block', [row['rule'] for row in result['findings']])

    def test_high_entropy_assignment_refused_but_placeholders_pass(self):
        (self.repo / 'config.py').write_text(f'API_KEY = "{secrets.token_urlsafe(24)}"\n')
        result = self.refused_capture()
        self.assertIn('high-entropy-assignment', [row['rule'] for row in result['findings']])
        (self.repo / 'config.py').write_text('API_KEY = "${ENV_API_KEY}"\npassword = "changeme"\napi_key = \n')
        self.capture()

    def test_commit_message_secret_refused(self):
        token = 'ghp_' + secrets.token_hex(18)
        self.git('commit', '--allow-empty', '-m', f'rotate {token}')
        code, result = self.invoke('capture', '--repo', self.repo, '--mode', 'since', '--base', self.initial,
                                   '--out', self.root / 'sealed')
        self.assertEqual(code, 6, result)
        self.assertEqual(result['outcome'], 'secret_detected')
        self.assertTrue(any(row['file'] == 'commits.txt' for row in result['findings']))

    def test_fingerprint_denylist_blocks_listed_value(self):
        token = secrets.token_hex(24)
        assignment = secrets.token_hex(16)
        short = secrets.token_hex(6)
        prefixes = [hashlib.sha256(value.encode()).hexdigest()[:16] for value in (token, assignment, short)]
        denylist = self.root / 'fingerprints.txt'
        denylist.write_text('# known-compromised values\n' + '\n'.join(prefixes).upper() + '\n')
        (self.repo / 'plain.txt').write_text(f'note {token} end\n')
        (self.repo / 'env.txt').write_text(f'API_KEY={assignment}\nTOKEN={short}\n')
        result = self.refused_capture(env={'CODE_REVIEW_SECRET_FINGERPRINTS': str(denylist)})
        listed = [row for row in result['findings'] if row['rule'] == 'fingerprint-denylist']
        self.assertTrue(listed)
        for value in (token, assignment, short):
            self.assertNotIn(value, json.dumps(result))
        out = self.root / 'unlisted'
        code, _ = self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', out)
        self.assertEqual(code, 0)
        self.assertTrue((out / 'COMPLETE').exists())

    def test_invalid_denylist_entry_is_explicit(self):
        denylist = self.root / 'fingerprints.txt'
        denylist.write_text('zzz-not-hex\n')
        code, result = self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', self.root / 'sealed',
                                   env={'CODE_REVIEW_SECRET_FINGERPRINTS': str(denylist)})
        self.assertEqual(code, 2, result)
        self.assertEqual(result['outcome'], 'invalid_input')
        self.assertIn('denylist', result['message'])

    def test_secret_rules_apply_without_gitleaks(self):
        data = f'token = "ghp_{secrets.token_hex(18)}"\n'.encode()
        blobs = {CAPTURE.digest(data): data}
        payloads = CAPTURE.payload_inventory({'before': {}, 'after': {}, 'authorities': []}, blobs, b'', b'')
        with patch.object(CAPTURE.shutil, 'which', return_value=None):
            with self.assertRaises(CAPTURE.Failure) as raised:
                CAPTURE.secrets_gate(payloads)
        result = raised.exception.result
        self.assertEqual(result['outcome'], 'secret_detected')
        self.assertEqual(result['secret_scan']['gitleaks'], 'unavailable')
        self.assertIn('github-token', [row['rule'] for row in result['findings']])


if __name__ == '__main__':
    unittest.main()
