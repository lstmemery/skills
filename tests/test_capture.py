import base64
from contextlib import redirect_stdout
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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

    def invoke(self, command, *args, env=None, timeout=None):
        result = subprocess.run([sys.executable, str(SCRIPT), command, *map(str, args)],
                                capture_output=True, text=True, env=os.environ | (env or {}), timeout=timeout)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def capture(self, mode='wip', base=None, extra=(), env=None):
        self.number += 1
        out = self.root / f'capture-{self.number}'
        args = ['--repo', self.repo, '--mode', mode, '--out', out]
        if base:
            args += ['--base', base]
        code, result = self.invoke('capture', *args, *extra, env=env)
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

    def test_branch_capture_includes_runtime_credential_assignment_in_context(self):
        sender = self.repo / 'skills-local/research-delivery/send_report_email.py'
        sender.parent.mkdir(parents=True)
        sender.write_text(
            'def credentials_provider():\n'
            '    return get_runtime_credentials()\n'
            'user, password = credentials_provider()\n')
        self.commit('add delivery helper context')
        base = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', '-b', 'review-target')
        (self.repo / 'file.txt').write_text('unrelated branch change\n')
        self.commit('change unrelated file')

        capture, result = self.capture('branch', base)

        manifest = json.loads((capture / 'manifest.json').read_text())
        self.assertEqual(result['coverage'], 'complete')
        self.assertEqual(manifest['changed'], ['file.txt'])
        self.assertIn('skills-local/research-delivery/send_report_email.py', manifest['after'])
        code, saved = self.invoke('read', '--capture', capture, '--path',
                                  'skills-local/research-delivery/send_report_email.py')
        self.assertEqual(code, 0, saved)
        self.assertIn('user, password = credentials_provider()', saved['text'])

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

    def test_repository_allowlist_suppresses_only_exact_builtin_fingerprint(self):
        value = secrets.token_urlsafe(24)
        (self.repo / 'config.py').write_text(f'API_KEY = "{value}"\n')
        fingerprint = hashlib.sha256(value.encode()).hexdigest()
        (self.repo / '.code-review-secrets-allow').write_text(
            f'# reviewed test fixture\n{fingerprint}\tsynthetic fixture, not a credential\n')
        self.git('add', '.code-review-secrets-allow')
        out, result = self.capture(env={'CODE_REVIEW_SECRETS_SCANNER': 'stdlib-only'})
        self.assertTrue((out / 'COMPLETE').exists())
        self.assertEqual(result['secret_scan']['gitleaks'], 'stdlib-only')

        changed = secrets.token_urlsafe(24)
        (self.repo / 'config.py').write_text(f'API_KEY = "{changed}"\n')
        result = self.refused_capture(out='changed-fingerprint', env={
            'CODE_REVIEW_SECRETS_SCANNER': 'stdlib-only'})
        self.assertIn('high-entropy-assignment', [row['rule'] for row in result['findings']])
        self.assertNotIn(changed, json.dumps(result))

    def test_repository_allowlist_matches_exact_gitleaks_span(self):
        with tempfile.TemporaryDirectory() as area:
            staging = Path(area)
            line = b'API_KEY = fixture'
            (staging / 'payload.txt').write_bytes(line + b'\n')
            row = {'File': str(staging / 'payload.txt'), 'RuleID': 'fixture-rule',
                   'StartLine': 1, 'EndLine': 1, 'StartColumn': 1, 'EndColumn': len(line)}
            result = subprocess.CompletedProcess(
                ['gitleaks'], 7, json.dumps([row]).encode(), b'')
            with (patch.object(CAPTURE.shutil, 'which', return_value='/fake/gitleaks'),
                  patch.object(CAPTURE.subprocess, 'run', return_value=result)):
                allowed = {hashlib.sha256(line).hexdigest()}
                findings, state = CAPTURE.gitleaks_scan(staging, {'payload.txt': 'after:payload.txt'}, allowed)
                self.assertEqual((findings, state), ([], 'used'))
                findings, state = CAPTURE.gitleaks_scan(
                    staging, {'payload.txt': 'after:payload.txt'}, {'0' * 64})
            self.assertEqual(state, 'used')
            self.assertEqual(findings[0]['rule'], 'gitleaks:fixture-rule')

    def test_repository_allowlist_matches_multiline_gitleaks_span(self):
        with tempfile.TemporaryDirectory() as area:
            staging = Path(area)
            first = b'API_KEY = fixture'
            second = b'continued'
            (staging / 'payload.txt').write_bytes(first + b'\n' + second + b'\n')
            span = first[len(b'API_KEY = '):] + b'\n' + second
            row = {'File': str(staging / 'payload.txt'), 'RuleID': 'fixture-rule',
                   'StartLine': 1, 'EndLine': 2, 'StartColumn': len(b'API_KEY = ') + 1,
                   'EndColumn': len(second)}
            result = subprocess.CompletedProcess(['gitleaks'], 7, json.dumps([row]).encode(), b'')
            with (patch.object(CAPTURE.shutil, 'which', return_value='/fake/gitleaks'),
                  patch.object(CAPTURE.subprocess, 'run', return_value=result)):
                findings, state = CAPTURE.gitleaks_scan(
                    staging, {'payload.txt': 'after:payload.txt'}, {hashlib.sha256(span).hexdigest()})
            self.assertEqual((findings, state), ([], 'used'))

    def test_gitleaks_out_of_line_coordinates_fail_closed(self):
        with tempfile.TemporaryDirectory() as area:
            staging = Path(area)
            (staging / 'payload.txt').write_bytes(b'short\n')
            row = {'File': str(staging / 'payload.txt'), 'RuleID': 'fixture-rule',
                   'StartLine': 1, 'EndLine': 1, 'StartColumn': 1, 'EndColumn': 100}
            result = subprocess.CompletedProcess(['gitleaks'], 7, json.dumps([row]).encode(), b'')
            with (patch.object(CAPTURE.shutil, 'which', return_value='/fake/gitleaks'),
                  patch.object(CAPTURE.subprocess, 'run', return_value=result)):
                findings, state = CAPTURE.gitleaks_scan(staging, {'payload.txt': 'after:payload.txt'})
            self.assertEqual((findings, state), ([], 'failed'))

    def test_invalid_repository_allowlist_entry_is_explicit(self):
        (self.repo / '.code-review-secrets-allow').write_text('not-a-fingerprint\tmissing value\n')
        code, result = self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', self.root / 'sealed',
                                   env={'CODE_REVIEW_SECRETS_SCANNER': 'stdlib-only'})
        self.assertEqual(code, 2, result)
        self.assertEqual(result['outcome'], 'invalid_input')
        self.assertIn('allowlist', result['message'])

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
        code, _ = self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', out,
                              env={'CODE_REVIEW_SECRETS_SCANNER': 'stdlib-only'})
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

    def test_scanner_operational_failures_refuse_capture_without_writing(self):
        data = b'ordinary content\n'
        blobs = {CAPTURE.digest(data): data}
        manifest = {'before': {}, 'after': {}, 'authorities': [], 'capture_id': 'fixture'}
        failures = (
            OSError('scanner launch failed'),
            subprocess.TimeoutExpired('gitleaks', CAPTURE.GITLEAKS_TIMEOUT),
            subprocess.CompletedProcess(['gitleaks'], 2, b'', b''),
            subprocess.CompletedProcess(['gitleaks'], 0, b'not-json', b''),
            subprocess.CompletedProcess(
                ['gitleaks'], 7,
                json.dumps([{'File': 'payload.txt', 'RuleID': 'generic-api-key',
                             'StartLine': 1, 'StartColumn': 1.5, 'EndColumn': 4.5}]).encode(), b''),
            subprocess.CompletedProcess(
                ['gitleaks'], 7,
                json.dumps([{'File': 'payload.txt', 'RuleID': 'generic-api-key',
                             'StartLine': 1, 'StartColumn': 1,
                             'EndColumn': CAPTURE.MAX_FILE + 1}]).encode(), b''),
        )
        for index, failure in enumerate(failures):
            with self.subTest(failure=type(failure).__name__):
                out = self.root / f'scanner-failure-{index}'
                process_patch = {'side_effect': failure} if isinstance(failure, BaseException) else {'return_value': failure}
                with (patch.object(CAPTURE.shutil, 'which', return_value='/fake/gitleaks'),
                      patch.object(CAPTURE.subprocess, 'run', **process_patch)):
                    with self.assertRaises(CAPTURE.Failure) as raised:
                        CAPTURE.write_capture(out, manifest, blobs, b'', b'')
                self.assertEqual(raised.exception.result['outcome'], 'secret_detected')
                self.assertEqual(raised.exception.result['message'], 'Capture refused: secrets scanner failed')
                self.assertEqual(raised.exception.result['secret_scan']['gitleaks'], 'failed')
                self.assertFalse(out.exists())

    def test_stdlib_only_opt_in_skips_gitleaks_and_writes_capture(self):
        data = b'ordinary content\n'
        blobs = {CAPTURE.digest(data): data}
        manifest = {'before': {}, 'after': {}, 'authorities': [], 'capture_id': 'fixture'}
        out = self.root / 'stdlib-only-capture'
        with (patch.dict(os.environ, {CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'}),
              patch.object(CAPTURE.shutil, 'which', side_effect=AssertionError('must skip discovery'))):
            result = CAPTURE.write_capture(out, manifest, blobs, b'', b'')
        self.assertEqual(result['gitleaks'], 'stdlib-only')
        self.assertTrue((out / 'COMPLETE').exists())

    def test_authority_leaf_and_ancestor_symlinks_are_gaps_without_target_bytes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        value = ('ghp_' + 'EXAMPLE' + 'Q7xK2mPzL9vR4tYw' * 2).encode()
        (outside / 'spec.md').write_bytes(value)
        leaf = self.root / 'leaf.md'
        leaf.symlink_to(outside / 'spec.md')
        ancestor = self.root / 'ancestor'
        ancestor.symlink_to(outside, target_is_directory=True)
        out, result = self.capture(extra=['--authority', leaf, '--authority', ancestor / 'spec.md'],
                                   env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        self.assertEqual(result['coverage'], 'incomplete')
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertEqual([row['gap'] for row in manifest['authorities']],
                         ['Authority symlinks are not followed'] * 2)
        self.assertTrue(all('sha256' not in row for row in manifest['authorities']))
        self.assertNotIn(value, [path.read_bytes() for path in (out / 'blobs').iterdir()])
        self.assertEqual(self.invoke('verify', '--capture', out)[0], 0)

    def test_wip_symlink_ancestor_is_a_gap_without_external_bytes(self):
        nested = self.repo / 'nested'
        nested.mkdir()
        (nested / 'file.txt').write_text('tracked original\n')
        self.commit('nested tracked source')
        outside = self.root / 'outside'
        outside.mkdir()
        value = ('ghp_' + 'EXAMPLE' + 'Q7xK2mPzL9vR4tYw' * 2).encode()
        (outside / 'file.txt').write_bytes(value)
        shutil.rmtree(nested)
        nested.symlink_to(outside, target_is_directory=True)
        out, result = self.capture(env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        self.assertEqual(result['coverage'], 'incomplete')
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertEqual(manifest['after']['nested/file.txt'], {'mode': 'unsupported', 'gap': 'Ancestor is a symlink'})
        self.assertNotIn(value, [path.read_bytes() for path in (out / 'blobs').iterdir()])
        self.assertEqual(self.invoke('read', '--capture', out, '--path', 'nested/file.txt')[0], 5)

    def test_destination_symlinks_and_symlinked_parents_do_not_write(self):
        target = self.root / 'target'
        target.mkdir()
        (target / 'sentinel').write_bytes(b'keep original bytes')
        link = self.root / 'destination-link'
        link.symlink_to(target, target_is_directory=True)
        dangling = self.root / 'dangling'
        dangling.symlink_to(self.root / 'missing')
        parent_link = self.root / 'parent-link'
        parent_link.symlink_to(target, target_is_directory=True)
        for out, code, outcome in ((link, 3, 'conflict'), (dangling, 3, 'conflict'),
                                   (parent_link / 'new', 2, 'invalid_input')):
            with self.subTest(destination=out.name):
                actual, result = self.invoke('capture', '--repo', self.repo, '--mode', 'wip', '--out', out,
                                            env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
                self.assertEqual((actual, result['outcome']), (code, outcome))
                self.assertEqual((target / 'sentinel').read_bytes(), b'keep original bytes')
                self.assertEqual({path.name for path in target.iterdir()}, {'sentinel'})
        self.assertTrue(link.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertEqual(list(self.root.glob('.review-*')), [])

    def test_all_capture_consumers_reject_tampered_payloads_and_completion(self):
        (self.repo / 'file.txt').write_text('committed after\n')
        self.commit('captured history')
        authority = self.root / 'spec.md'
        authority.write_text('captured authority\n')
        original, saved = self.capture('since', self.initial, extra=['--authority', authority],
                                       env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        manifest = json.loads((original / 'manifest.json').read_text())
        blobs = [manifest['before']['file.txt']['sha256'], manifest['after']['file.txt']['sha256'],
                 manifest['authorities'][0]['sha256']]
        cases = [('manifest.json', 'Capture manifest digest mismatch'),
                 ('COMPLETE', 'Capture completion marker mismatch'),
                 ('diff.patch', 'Capture artifact digest mismatch'),
                 ('commits.txt', 'Capture artifact digest mismatch')]
        cases += [(f'blobs/{sha}', 'Captured source digest mismatch') for sha in blobs]
        for index, (relative, message) in enumerate(cases):
            with self.subTest(artifact=relative):
                out = self.root / f'tampered-{index}'
                shutil.copytree(original, out)
                code, verified = self.invoke('verify', '--capture', out)
                self.assertEqual((code, verified['capture_id']), (0, saved['capture_id']))
                if relative == 'manifest.json':
                    altered = dict(manifest, empty=not manifest['empty'])
                    (out / relative).write_text(json.dumps(altered))
                else:
                    (out / relative).write_bytes(b'tampered bytes\n')
                for command in ('verify', 'read', 'check'):
                    args = ['--path', 'file.txt'] if command == 'read' else []
                    code, result = self.invoke(command, '--capture', out, *args)
                    self.assertEqual((code, result['outcome']), (4, 'corrupt'))
                    self.assertEqual(result['message'], message)

    def test_all_capture_consumers_reject_symlinked_storage_even_with_matching_bytes(self):
        original, _ = self.capture(env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        manifest = json.loads((original / 'manifest.json').read_text())
        sha = manifest['after']['file.txt']['sha256']
        cases = [('.', 2, 'invalid_input'), ('blobs', 2, 'invalid_input')]
        cases += [(name, 5, 'unavailable') for name in
                  ('manifest.json', 'COMPLETE', 'diff.patch', 'commits.txt', f'blobs/{sha}')]
        for index, (relative, expected_code, outcome) in enumerate(cases):
            with self.subTest(artifact=relative):
                out = self.root / f'linked-{index}'
                if relative == '.':
                    out.symlink_to(original, target_is_directory=True)
                else:
                    shutil.copytree(original, out)
                    target = out / relative
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    target.symlink_to(original / relative)
                for command in ('verify', 'read', 'check'):
                    args = ['--path', 'file.txt'] if command == 'read' else []
                    code, result = self.invoke(command, '--capture', out, *args)
                    self.assertEqual((code, result['outcome']), (expected_code, outcome))

    def test_incomplete_nonregular_and_malformed_captures_never_verify(self):
        original, _ = self.capture(env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        manifest = json.loads((original / 'manifest.json').read_text())
        sha = manifest['after']['file.txt']['sha256']
        for index, relative in enumerate(('manifest.json', 'COMPLETE', 'diff.patch', 'commits.txt', f'blobs/{sha}')):
            with self.subTest(missing=relative):
                out = self.root / f'incomplete-{index}'
                shutil.copytree(original, out)
                (out / relative).unlink()
                code, result = self.invoke('verify', '--capture', out)
                self.assertEqual((code, result['outcome']), (5, 'unavailable'))
        for index, relative in enumerate(('manifest.json', 'COMPLETE', 'diff.patch', 'commits.txt', f'blobs/{sha}')):
            with self.subTest(nonregular=relative):
                out = self.root / f'fifo-capture-{index}'
                shutil.copytree(original, out)
                (out / relative).unlink()
                os.mkfifo(out / relative)
                code, result = self.invoke('verify', '--capture', out, timeout=10)
                # Manifest parsing catches Failure's ValueError base; other reads retain unsupported.
                expected = (2, 'invalid_input') if relative == 'manifest.json' else (5, 'unsupported')
                self.assertEqual((code, result['outcome']), expected)
        for index, raw in enumerate((b'{', b'[]', b'null', b'{}', b'\xff')):
            with self.subTest(manifest=raw):
                out = self.root / f'malformed-{index}'
                shutil.copytree(original, out)
                (out / 'manifest.json').write_bytes(raw)
                code, result = self.invoke('verify', '--capture', out)
                self.assertEqual((code, result['outcome']), (2, 'invalid_input'))
        (original / 'COMPLETE').write_bytes(b'\xff')
        code, result = self.invoke('verify', '--capture', original)
        self.assertEqual((code, result['outcome']), (5, 'unavailable'))

    def test_saved_capture_verifies_and_reads_without_the_source_repository(self):
        out, saved = self.capture(env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        shutil.rmtree(self.repo)
        code, verified = self.invoke('verify', '--capture', out)
        self.assertEqual((code, verified['capture_id']), (0, saved['capture_id']))
        code, read = self.invoke('read', '--capture', out, '--path', 'file.txt')
        self.assertEqual((code, read['text']), (0, 'first\n'))

    def test_capture_identity_is_independent_of_destination_and_json_presentation(self):
        first, saved = self.capture(env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        second, repeated = self.capture(env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        self.assertEqual(saved['capture_id'], repeated['capture_id'])
        manifest = json.loads((second / 'manifest.json').read_text())
        (second / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        code, verified = self.invoke('verify', '--capture', second)
        self.assertEqual((code, verified['capture_id']), (0, saved['capture_id']))
        self.assertEqual((first / 'COMPLETE').read_bytes(), (second / 'COMPLETE').read_bytes())

    def test_cli_failure_outcomes_keep_their_exit_codes(self):
        (self.repo / 'binary').write_bytes(b'\x00\xff')
        out, _ = self.capture(env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        corrupt = self.root / 'corrupt'
        shutil.copytree(out, corrupt)
        (corrupt / 'COMPLETE').write_bytes(b'wrong capture id\n')
        (self.repo / 'leak.txt').write_text('ghp_' + 'EXAMPLE' + 'Q7xK2mPzL9vR4tYw' * 2 + '\n')
        cases = (
            ('missing-input', 'capture', [], 2, 'invalid_input'),
            ('missing-base', 'capture', ['--repo', self.repo, '--mode', 'since', '--out', self.root / 'no-base'], 2, 'invalid_input'),
            ('bad-ref', 'capture', ['--repo', self.repo, '--mode', 'since', '--base', 'missing-ref', '--out', self.root / 'bad-ref'], 2, 'git_error'),
            ('reservation', 'capture', ['--repo', self.repo, '--mode', 'wip', '--out', out], 3, 'conflict'),
            ('drift', 'check', ['--capture', out], 3, 'drift'),
            ('integrity', 'verify', ['--capture', corrupt], 4, 'corrupt'),
            ('binary-reader', 'read', ['--capture', out, '--path', 'binary'], 5, 'unsupported'),
            ('missing-capture', 'verify', ['--capture', self.root / 'missing'], 5, 'unavailable'),
            ('secret', 'capture', ['--repo', self.repo, '--mode', 'wip', '--out', self.root / 'secret'], 6, 'secret_detected'),
        )
        for name, command, args, expected_code, outcome in cases:
            with self.subTest(failure=name):
                code, result = self.invoke(command, *args, env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
                self.assertEqual((code, result['outcome']), (expected_code, outcome))
        self.assertFalse((self.root / 'secret').exists())

    def test_cli_git_timeout_is_an_unavailable_json_result(self):
        out = self.root / 'timed-out'
        argv = [str(SCRIPT), 'capture', '--repo', str(self.repo), '--mode', 'wip', '--out', str(out)]
        stdout = io.StringIO()
        with (patch.object(sys, 'argv', argv), redirect_stdout(stdout),
              patch.object(CAPTURE.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 30))):
            code = CAPTURE.main()
        self.assertEqual((code, json.loads(stdout.getvalue())['outcome']), (5, 'unavailable'))
        self.assertFalse(out.exists())


class BoundedReadContractTests(unittest.TestCase):
    """Deterministic OS-boundary probes for races that a CLI fixture cannot schedule."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'payload'

    def test_regular_reads_accept_the_inclusive_bound_and_reject_larger_files(self):
        for size in (0, 1, 63, 64):
            with self.subTest(size=size):
                data = b'x' * size
                self.path.write_bytes(data)
                self.assertEqual(CAPTURE.bounded_file(self.path, 64), data)
        self.path.write_bytes(b'x' * 65)
        with self.assertRaises(CAPTURE.Failure) as raised:
            CAPTURE.bounded_file(self.path, 64)
        self.assertEqual((raised.exception.result['outcome'], raised.exception.result['limit']), ('unsupported', 64))

    def test_leaf_symlinks_and_directories_are_not_read_as_regular_files(self):
        self.path.write_bytes(b'benign target bytes')
        link = self.root / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(OSError) as raised:
            CAPTURE.bounded_file(link, 64)
        self.assertEqual(raised.exception.errno, errno.ELOOP)
        with self.assertRaises(OSError) as raised:
            CAPTURE.bounded_file(self.root, 64)
        self.assertEqual(raised.exception.errno, errno.EISDIR)

    def test_read_detects_growth_beyond_the_bound_after_the_size_check(self):
        self.path.write_bytes(b'x' * 65)
        info = self.path.stat()
        fields = ('st_mode', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        before = SimpleNamespace(**{field: getattr(info, field) for field in fields})
        before.st_size = 64
        with patch.object(CAPTURE.os, 'fstat', return_value=before):
            with self.assertRaises(CAPTURE.Failure) as raised:
                CAPTURE.bounded_file(self.path, 64)
        self.assertEqual((raised.exception.result['outcome'], raised.exception.result['message']),
                         ('conflict', 'File grew beyond capture bound'))

    def test_read_detects_identity_size_and_timestamp_changes_without_growth(self):
        self.path.write_bytes(b'unchanged bytes\n')
        info = self.path.stat()
        fields = ('st_mode', 'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        before = {field: getattr(info, field) for field in fields}
        for field in fields[1:]:
            with self.subTest(changed=field):
                after = dict(before)
                after[field] += 1
                with patch.object(CAPTURE.os, 'fstat', side_effect=[SimpleNamespace(**before), SimpleNamespace(**after)]):
                    with self.assertRaises(CAPTURE.Failure) as raised:
                        CAPTURE.bounded_file(self.path, 64)
                self.assertEqual((raised.exception.result['outcome'], raised.exception.result['message']),
                                 ('conflict', 'File changed while reading'))

    def test_repository_paths_reject_absolute_empty_and_parent_traversal_names(self):
        for name in ('', '..', '../outside', 'nested/../payload', str(self.root / 'payload')):
            with self.subTest(path=name):
                with self.assertRaises(CAPTURE.Failure) as raised:
                    CAPTURE.work_entry(self.root, name, CAPTURE.Collector())
                self.assertEqual(raised.exception.result['outcome'], 'invalid_input')


if __name__ == '__main__':
    unittest.main()
