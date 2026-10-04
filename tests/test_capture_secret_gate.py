"""Exercise capture.py's secrets gate against fake scanner executables.

Every secret below is a synthetic fixture assembled from fragments. The fake
`gitleaks` follows the observed CLI/report contract (`dir <path>` with JSON on
stdout, exit 7 on findings, redacted `Match`/`Secret`), so the code under test
runs its real subprocess path instead of a mocked one.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / 'skills/code-review/scripts/capture.py'
SPEC = importlib.util.spec_from_file_location('capture', SCRIPT)
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)

# Matched by the built-in token rules only.
BUILTIN_SECRET = 'ghp_' + 'Q7xK2mPzL9vR4tYw' * 2
# Matched only by the fake scanner; the built-in rules ignore this shape.
SCANNER_SECRET = 'FAKESECRET_' + 'ZK39QD71MX20'
SCANNER_PATTERN = r'FAKESECRET_[A-Z0-9]{12}'

FAKE_SCANNER = '''\
#!{python}
import hashlib, json, os, re, signal, sys, time
from pathlib import Path

MODE = {mode!r}
LOG = Path({log!r})
RAW = {raw!r}
EXIT = {exit_code}
PATTERN = re.compile({pattern!r})
EXPECTED = ['--report-format', 'json', '--report-path', '-', '--redact', '--exit-code', '7',
            '--no-banner', '--no-color', '-l', 'error', '--max-target-megabytes', '512']


def contract_violation(text):
    sys.stderr.write('fake gitleaks contract violation: ' + text + '\\n')
    sys.exit(64)


args = sys.argv[1:]
if len(args) < 2 or args[0] != 'dir' or not Path(args[1]).is_dir():
    contract_violation('want: dir <existing directory>')
staging = Path(args[1])
rest = args[2:]
if rest[:len(EXPECTED)] != EXPECTED or rest[len(EXPECTED):] != ['--gitleaks-ignore-path', str(staging)]:
    contract_violation('unexpected flags: ' + ' '.join(rest))

staged = {{str(path.relative_to(staging)): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in sorted(staging.rglob('*')) if path.is_file()}}
with LOG.open('a') as stream:
    stream.write(json.dumps({{'argv': args, 'staged': staged,
                              'gitleaks_env': sorted(k for k in os.environ if k.startswith('GITLEAKS_'))}}) + '\\n')

if MODE == 'timeout':
    time.sleep(60)
if MODE == 'killed':
    os.kill(os.getpid(), signal.SIGKILL)
if MODE == 'raw':
    sys.stderr.write('scanner diagnostic leaking ' + {leak!r} + '\\n')
    sys.stdout.buffer.write(RAW.replace(b'{{STAGING}}', str(staging).encode()))
    sys.exit(EXIT)

# MODE == 'scan': behave like the real tool for a synthetic rule.
rows = []
for path in sorted(staging.rglob('*')):
    if not path.is_file():
        continue
    raw = path.read_bytes()
    for number, line in enumerate(raw.split(b'\\n'), 1):
        for match in PATTERN.finditer(line.decode('latin-1')):
            # Observed gitleaks columns: 1-based, end inclusive, and shifted
            # by one on every line after the first.
            shift = 0 if number == 1 else 1
            rows.append({{'RuleID': 'fake-synthetic-rule', 'Description': 'synthetic fixture rule',
                         'StartLine': number, 'EndLine': number,
                         'StartColumn': match.start() + 1 + shift, 'EndColumn': match.end() + shift,
                         'Match': 'REDACTED', 'Secret': 'REDACTED', 'File': str(path),
                         'SymlinkFile': '', 'Commit': '', 'Entropy': 3.5, 'Author': '', 'Email': '',
                         'Date': '', 'Message': '', 'Tags': []}})
sys.stdout.write(json.dumps(rows))
sys.exit(7 if rows else 0)
'''


def row(**overrides):
    """A finding row as gitleaks reports it, aimed at staged `diff.patch`."""
    base = {'RuleID': 'fake-rule', 'StartLine': 1, 'EndLine': 1, 'StartColumn': 1, 'EndColumn': 8,
            'Match': 'REDACTED', 'Secret': 'REDACTED', 'File': '{STAGING}/diff.patch'}
    base.update(overrides)
    return base


class SecretGateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'scanner.log'
        self.number = 0
        git = shutil.which('git')
        self.assertTrue(git, 'git is required')
        (self.bin / 'git').symlink_to(git)
        self.env = {'PATH': str(self.bin)}
        self.addCleanup(self.tmp.cleanup)

    # -- fixtures ---------------------------------------------------------
    def install_scanner(self, mode='scan', raw=b'', exit_code=0, pattern=SCANNER_PATTERN):
        script = FAKE_SCANNER.format(python=sys.executable, mode=mode, log=str(self.log), raw=raw,
                                     exit_code=exit_code, pattern=pattern, leak=SCANNER_SECRET)
        path = self.bin / 'gitleaks'
        path.write_text(script)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def scanner_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def payload_args(self, before=None, after=None, authority=None, diff=b'', history=b'', extra_manifest=None):
        """Build write_capture inputs with chosen bytes in each payload type."""
        blobs, manifest = {}, {'before': {}, 'after': {}, 'authorities': [], 'capture_id': 'fixture'}
        for side, name, data in (('before', 'before.txt', before), ('after', 'after.txt', after)):
            if data is not None:
                sha = CAPTURE.digest(data)
                blobs[sha] = data
                manifest[side][name] = {'sha256': sha}
        if authority is not None:
            sha = CAPTURE.digest(authority)
            blobs[sha] = authority
            manifest['authorities'].append({'path': '/spec/authority.md', 'sha256': sha})
        if extra_manifest:
            manifest.update(extra_manifest)
        return manifest, blobs, diff, history

    def gate(self, scanner_env=None, **parts):
        """Run write_capture in-process under the isolated PATH; return (outcome, destination)."""
        self.number += 1
        out = self.root / f'capture-{self.number}'
        manifest, blobs, diff, history = self.payload_args(**parts)
        environment = {**os.environ, **self.env, **(scanner_env or {})}
        environment.pop(CAPTURE.SECRETS_SCANNER_ENV, None) if not (scanner_env or {}) else None
        with patch.dict(os.environ, environment, clear=True):
            try:
                return CAPTURE.write_capture(out, manifest, blobs, diff, history), out
            except CAPTURE.Failure as error:
                return error, out

    def assert_refused(self, outcome, out, *secrets, message=None):
        self.assertIsInstance(outcome, CAPTURE.Failure, 'capture was not refused')
        result = outcome.result
        self.assertEqual(result['outcome'], 'secret_detected')
        if message:
            self.assertEqual(result['message'], message)
        self.assertFalse(out.exists(), 'destination must not exist after a refusal')
        self.assertFalse((out / 'COMPLETE').exists())
        self.assertEqual([p.name for p in out.parent.iterdir() if p.name.startswith('.review-')], [],
                         'staging directory leaked')
        for secret in secrets:
            self.assertNotIn(secret, json.dumps(result), 'matched secret was emitted')
        return result


class PayloadTypeTests(SecretGateCase):
    """A match in any payload type refuses the capture, for either detector."""

    PAYLOADS = (
        ('before-blob', lambda s: {'before': f'x = 1\ntoken = "{s}"\n'.encode()}, 'before:before.txt'),
        ('after-blob', lambda s: {'after': f'note {s}\n'.encode()}, 'after:after.txt'),
        ('authority', lambda s: {'authority': f'spec says {s}\n'.encode()}, 'authority:/spec/authority.md'),
        ('diff', lambda s: {'diff': f'+++ b/f\n+ {s}\n'.encode()}, 'diff.patch'),
        ('commits', lambda s: {'history': f'{"a" * 40} rotate {s}\n'.encode()}, 'commits.txt'),
        ('manifest', lambda s: {'extra_manifest': {'untracked': [f'{s}.txt']}}, 'manifest.json'),
    )

    def test_builtin_rules_refuse_each_payload_type(self):
        for name, build, label in self.PAYLOADS:
            with self.subTest(payload=name):
                outcome, out = self.gate(scanner_env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'},
                                         **build(BUILTIN_SECRET))
                result = self.assert_refused(outcome, out, BUILTIN_SECRET)
                self.assertEqual(result['secret_scan'], {'gitleaks': 'stdlib-only'})
                hit = [r for r in result['findings'] if r['rule'] == 'github-token']
                self.assertEqual([r['file'] for r in hit], [label])
                self.assertEqual(hit[0]['length'], len(BUILTIN_SECRET))

    def test_scanner_findings_refuse_each_payload_type(self):
        self.install_scanner('scan')
        for name, build, label in self.PAYLOADS:
            with self.subTest(payload=name):
                outcome, out = self.gate(**build(SCANNER_SECRET))
                result = self.assert_refused(outcome, out, SCANNER_SECRET,
                                             message='Capture refused: a payload matches a secret rule; '
                                                     'remove the value or replace it with a fake and capture again')
                self.assertEqual(result['secret_scan'], {'gitleaks': 'used'})
                self.assertEqual([r['file'] for r in result['findings']], [label])
                self.assertEqual([r['rule'] for r in result['findings']], ['gitleaks:fake-synthetic-rule'])
                self.assertEqual(set(result['findings'][0]), {'file', 'line', 'rule'},
                                 'scanner findings must not carry value-derived fields')

    def test_findings_report_the_matching_line(self):
        self.install_scanner('scan')
        outcome, out = self.gate(after=f'one\ntwo\nthree {SCANNER_SECRET}\n'.encode())
        result = self.assert_refused(outcome, out, SCANNER_SECRET)
        self.assertEqual([(r['file'], r['line']) for r in result['findings']], [('after:after.txt', 3)])

    def test_both_detectors_report_together_without_values(self):
        self.install_scanner('scan')
        outcome, out = self.gate(after=f'a {BUILTIN_SECRET}\nb {SCANNER_SECRET}\n'.encode())
        result = self.assert_refused(outcome, out, BUILTIN_SECRET, SCANNER_SECRET)
        self.assertEqual(sorted(r['rule'] for r in result['findings']),
                         ['github-token', 'gitleaks:fake-synthetic-rule'])
        self.assertEqual(result['total_findings'], 2)

    def test_end_to_end_refusal_through_the_cli_for_each_source(self):
        self.install_scanner('scan')

        def head_file(repo, secret):
            (repo / 'leak.txt').write_text(f'value {secret}\n')
            return ['--mode', 'wip'], 'after:leak.txt'

        def removed_before_head(repo, secret):
            (repo / 'leak.txt').write_text(f'value {secret}\n')
            base = self.commit(repo, 'add')
            (repo / 'leak.txt').unlink()
            self.commit(repo, 'remove')
            return ['--mode', 'since', '--base', base], 'before:leak.txt'

        def commit_message(repo, secret):
            base = self.git(repo, 'rev-parse', 'HEAD')
            self.git(repo, 'commit', '-q', '--allow-empty', '-m', f'rotate {secret}')
            return ['--mode', 'since', '--base', base], 'commits.txt'

        def authority(repo, secret):
            spec = self.root / f'spec-{repo.name}.md'
            spec.write_text(f'authority {secret}\n')
            return ['--mode', 'wip', '--authority', spec], f'authority:{spec}'

        def path_name(repo, secret):
            (repo / f'{secret}.txt').write_text('harmless\n')
            return ['--mode', 'wip'], 'manifest.json'

        for detector, secret in (('builtin', BUILTIN_SECRET), ('scanner', SCANNER_SECRET)):
            for build in (head_file, removed_before_head, commit_message, authority, path_name):
                with self.subTest(detector=detector, source=build.__name__):
                    repo = self.new_repo(f'{detector}-{build.__name__}')
                    (repo / 'base.txt').write_text('base\n')
                    self.commit(repo, 'base')
                    args, label = build(repo, secret)
                    out = self.root / f'out-{detector}-{build.__name__}'
                    code, result, stdout, stderr = self.cli(repo, out, *args, env=self.env)
                    self.assertEqual(code, 6, result)
                    self.assertEqual(result['outcome'], 'secret_detected')
                    self.assertIn(label, [row['file'] for row in result['findings']])
                    self.assertFalse(out.exists())
                    self.assertNotIn(secret, stdout + stderr)

    # -- helpers for the CLI path -----------------------------------------
    def new_repo(self, name):
        repo = self.root / f'repo-{name}'
        repo.mkdir()
        self.git(repo, 'init', '-q', '-b', 'main')
        self.git(repo, 'config', 'user.name', 'Fixture')
        self.git(repo, 'config', 'user.email', 'fixture@example.invalid')
        return repo

    def git(self, repo, *args):
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, repo, message):
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', message)
        return self.git(repo, 'rev-parse', 'HEAD')

    def cli(self, repo, out, *args, env=None):
        environment = {key: value for key, value in os.environ.items() if not key.startswith(('CODE_REVIEW_', 'GITLEAKS_'))}
        environment.update(env or {})
        result = subprocess.run([sys.executable, str(SCRIPT), 'capture', '--repo', str(repo), '--out', str(out), *map(str, args)],
                                capture_output=True, text=True, env=environment)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout), result.stdout, result.stderr


class ScannerFailureTests(SecretGateCase):
    """Missing, hung, failing, or lying scanners never yield a capture or a leaked match."""

    SCANNER_FAILED = 'Capture refused: secrets scanner failed'

    def raw(self, rows, exit_code=7):
        return json.dumps(rows).encode(), exit_code

    def assert_scanner_failed(self, outcome, out, with_payload_secret=True):
        result = self.assert_refused(outcome, out, SCANNER_SECRET, BUILTIN_SECRET, message=self.SCANNER_FAILED)
        self.assertEqual(result['secret_scan'], {'gitleaks': 'failed'})
        self.assertNotIn('findings', result, 'a failed scan must not report partial findings')
        return result

    def test_nonzero_and_signal_exits_refuse_even_with_an_empty_report(self):
        for code in (1, 2, 3, 8, 64, 126, 127, 255):
            with self.subTest(exit_code=code):
                self.install_scanner('raw', raw=b'[]', exit_code=code)
                outcome, out = self.gate(after=b'ordinary\n')
                self.assert_scanner_failed(outcome, out)
        with self.subTest(exit='SIGKILL'):
            self.install_scanner('killed')
            outcome, out = self.gate(after=b'ordinary\n')
            self.assert_scanner_failed(outcome, out)

    def test_scanner_stderr_is_never_forwarded(self):
        self.install_scanner('raw', raw=b'[]', exit_code=2)
        outcome, out = self.gate(after=b'ordinary\n')
        self.assert_scanner_failed(outcome, out)  # the fake leaks a secret on stderr

    def test_timeout_refuses_and_kills_the_scanner(self):
        self.install_scanner('timeout')
        with patch.object(CAPTURE, 'GITLEAKS_TIMEOUT', 1):
            outcome, out = self.gate(after=b'ordinary\n')
        self.assert_scanner_failed(outcome, out)
        self.assertEqual(len(self.scanner_calls()), 1)

    def test_launch_failure_refuses(self):
        broken = self.bin / 'gitleaks'
        broken.write_text('#!/nonexistent/interpreter\n')
        broken.chmod(0o755)
        outcome, out = self.gate(after=b'ordinary\n')
        self.assert_scanner_failed(outcome, out)

    def test_malformed_reports_refuse(self):
        reports = {
            'not-json': (b'not-json', 0),
            'binary': (b'\xff\xfe\x00', 0),
            'empty-stdout': (b'', 0),
            'null': (b'null', 0),
            'object': (b'{}', 0),
            'string': (b'"[]"', 7),
            'rows-not-objects': (b'[1, "two", null]', 7),
            'row-is-list': (b'[[]]', 7),
            'finding-exit-without-findings': (b'[]', 7),
            'truncated': (json.dumps([row()]).encode()[:-5], 7),
            'trailing-garbage': (json.dumps([row()]).encode() + b' tail', 7),
        }
        for name, (raw, code) in reports.items():
            with self.subTest(report=name):
                self.install_scanner('raw', raw=raw, exit_code=code)
                outcome, out = self.gate(diff=b'one line\n')
                self.assert_scanner_failed(outcome, out)

    def test_oversized_report_refuses(self):
        self.install_scanner('raw', raw=b'[' + b'0,' * 400 + b'0]', exit_code=0)
        with patch.object(CAPTURE, 'MAX_TOTAL', 500):
            outcome, out = self.gate(diff=b'one line\n')
        self.assert_scanner_failed(outcome, out)

    def test_scanner_failure_hides_builtin_findings_and_never_writes(self):
        self.install_scanner('raw', raw=b'garbage', exit_code=0)
        outcome, out = self.gate(after=f'x {BUILTIN_SECRET}\n'.encode())
        self.assert_scanner_failed(outcome, out)

    def test_cli_exit_code_and_output_for_a_failed_scanner(self):
        self.install_scanner('raw', raw=b'garbage', exit_code=0)
        repo = self.root / 'repo'
        repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init', '-q', '-b', 'main'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=F', '-c', 'user.email=f@example.invalid',
                        'commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        (repo / 'f.txt').write_text(f'token {BUILTIN_SECRET}\n')
        out = self.root / 'cli-out'
        result = subprocess.run([sys.executable, str(SCRIPT), 'capture', '--repo', str(repo), '--mode', 'wip',
                                 '--out', str(out)], capture_output=True, text=True,
                                env={**os.environ, **self.env})
        self.assertEqual(result.returncode, 6, result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report['message'], self.SCANNER_FAILED)
        self.assertFalse(out.exists())
        self.assertNotIn(BUILTIN_SECRET, result.stdout + result.stderr)
        self.assertNotIn(SCANNER_SECRET, result.stdout + result.stderr)


class InvalidCoordinateTests(SecretGateCase):
    """Coordinates the scanner reports must resolve to real bytes in the staged payload."""

    DIFF = b'first line\nsecond line\n'  # staged verbatim as diff.patch

    def refuse_with(self, finding_row):
        self.install_scanner('raw', raw=json.dumps([finding_row]).encode(), exit_code=7)
        return self.gate(diff=self.DIFF)

    def test_valid_coordinates_report_a_finding_without_the_value(self):
        for name, overrides in {
            'first-bytes': {},
            'whole-first-line': {'EndColumn': len('first line')},
            'multi-line': {'EndLine': 2, 'EndColumn': 6},
            'end-column-includes-newline-slot': {'EndColumn': len('first line') + 1},
            'second-line': {'StartLine': 2, 'EndLine': 2, 'StartColumn': 1, 'EndColumn': 6},
        }.items():
            with self.subTest(coordinates=name):
                outcome, out = self.refuse_with(row(**overrides))
                result = self.assert_refused(outcome, out)
                self.assertEqual(result['secret_scan'], {'gitleaks': 'used'})
                self.assertEqual([r['file'] for r in result['findings']], ['diff.patch'])
                self.assertEqual(result['findings'][0]['rule'], 'gitleaks:fake-rule')

    def test_invalid_coordinates_fail_the_scan(self):
        cases = {
            'start-line-past-eof': {'StartLine': 3, 'EndLine': 3},
            'end-line-past-eof': {'EndLine': 3},
            'start-column-past-line': {'StartColumn': 11, 'EndColumn': 11},
            'end-column-past-line': {'EndColumn': 100},
            'end-before-start-same-line': {'StartColumn': 5, 'EndColumn': 4},
            'end-line-before-start-line': {'StartLine': 2, 'EndLine': 1},
            'zero-line': {'StartLine': 0},
            'zero-column': {'StartColumn': 0},
            'negative-line': {'StartLine': -1},
            'negative-column': {'EndColumn': -3},
            'float-line': {'StartLine': 1.0},
            'float-column': {'StartColumn': 1.5},
            'boolean-line': {'StartLine': True},
            'string-line': {'StartLine': '1'},
            'null-column': {'EndColumn': None},
            'beyond-file-bound': {'EndColumn': CAPTURE.MAX_FILE + 1},
            'unstaged-file': {'File': '{STAGING}/missing.patch'},
            'directory-not-file': {'File': '{STAGING}'},
            'empty-file-name': {'File': ''},
            'non-string-file': {'File': 7},
            'empty-rule': {'RuleID': ''},
            'non-string-rule': {'RuleID': 7},
        }
        for name, overrides in cases.items():
            with self.subTest(coordinates=name):
                outcome, out = self.refuse_with(row(**overrides))
                self.assert_failed(outcome, out)

    def test_missing_fields_fail_the_scan(self):
        for field in ('File', 'RuleID', 'StartLine', 'EndLine', 'StartColumn', 'EndColumn'):
            with self.subTest(missing=field):
                finding_row = row()
                del finding_row[field]
                outcome, out = self.refuse_with(finding_row)
                self.assert_failed(outcome, out)

    def test_one_bad_row_fails_the_scan_even_beside_a_valid_one(self):
        self.install_scanner('raw', raw=json.dumps([row(), row(EndColumn=100)]).encode(), exit_code=7)
        outcome, out = self.gate(diff=self.DIFF)
        self.assert_failed(outcome, out)

    def assert_failed(self, outcome, out):
        result = self.assert_refused(outcome, out, message='Capture refused: secrets scanner failed')
        self.assertEqual(result['secret_scan'], {'gitleaks': 'failed'})
        self.assertNotIn('findings', result)


class ScannerPolicyTests(SecretGateCase):
    """Clean captures, a missing binary, and the explicit stdlib-only opt-in."""

    def stage_repo(self):
        repo = self.root / 'repo'
        repo.mkdir()
        run = lambda *a: subprocess.run(['git', '-C', str(repo), *a], check=True, capture_output=True)
        run('init', '-q', '-b', 'main')
        run('config', 'user.name', 'Fixture')
        run('config', 'user.email', 'fixture@example.invalid')
        (repo / 'readme.txt').write_text('hello\n')
        run('add', '.')
        run('commit', '-q', '-m', 'initial')
        (repo / 'readme.txt').write_text('hello, world\n')
        return repo

    def cli(self, *args, env=None):
        environment = {key: value for key, value in os.environ.items() if not key.startswith(('CODE_REVIEW_', 'GITLEAKS_'))}
        environment.update(self.env)
        environment.update(env or {})
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                                env=environment)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_clean_capture_scans_exactly_the_bytes_it_writes(self):
        self.install_scanner('scan')
        repo, out = self.stage_repo(), self.root / 'clean'
        code, result = self.cli('capture', '--repo', repo, '--mode', 'wip', '--out', out,
                                env={'GITLEAKS_CONFIG': '/nonexistent/untrusted.toml'})
        self.assertEqual((code, result['outcome'], result['secret_scan']), (0, 'captured', {'gitleaks': 'used'}))
        self.assertEqual((out / 'COMPLETE').read_text().strip(), result['capture_id'])
        self.assertEqual(self.cli('verify', '--capture', out)[0], 0)
        (call,) = self.scanner_calls()
        written = {str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted(out.rglob('*')) if path.is_file() and path.name != 'COMPLETE'}
        self.assertEqual(call['staged'], written, 'scanner must see every written byte, and only those')
        self.assertIn('manifest.json', written)
        self.assertEqual(call['gitleaks_env'], [], 'GITLEAKS_* configuration must not reach the scanner')
        self.assertFalse(Path(call['argv'][1]).exists(), 'scan staging directory must be removed')

    def test_scanner_report_with_no_findings_is_not_a_failure_for_each_empty_shape(self):
        self.install_scanner('raw', raw=b'[]', exit_code=0)
        outcome, out = self.gate(after=b'ordinary\n')
        self.assertEqual(outcome, {'gitleaks': 'used'})
        self.assertTrue((out / 'COMPLETE').exists())

    def test_exit_zero_with_findings_still_refuses(self):
        self.install_scanner('raw', raw=json.dumps([row()]).encode(), exit_code=0)
        outcome, out = self.gate(diff=b'one line\n')
        self.assert_refused(outcome, out)

    def test_missing_scanner_is_documented_unavailable_and_builtin_rules_still_apply(self):
        # CAPTURE.md: "If gitleaks is missing, the built-in rules still apply and
        # the capture result says `unavailable` under `secret_scan`."
        repo, out = self.stage_repo(), self.root / 'no-scanner'
        code, result = self.cli('capture', '--repo', repo, '--mode', 'wip', '--out', out)
        self.assertEqual((code, result['secret_scan']), (0, {'gitleaks': 'unavailable'}))
        self.assertTrue((out / 'COMPLETE').exists())
        (repo / 'leak.txt').write_text(f'token {BUILTIN_SECRET}\n')
        refused = self.root / 'no-scanner-leak'
        code, result = self.cli('capture', '--repo', repo, '--mode', 'wip', '--out', refused)
        self.assertEqual((code, result['outcome'], result['secret_scan']),
                         (6, 'secret_detected', {'gitleaks': 'unavailable'}))
        self.assertFalse(refused.exists())
        self.assertNotIn(BUILTIN_SECRET, json.dumps(result))

    def test_non_executable_scanner_counts_as_missing(self):
        (self.bin / 'gitleaks').write_text('#!/bin/sh\nexit 0\n')
        (self.bin / 'gitleaks').chmod(0o644)
        outcome, out = self.gate(after=b'ordinary\n')
        self.assertEqual(outcome, {'gitleaks': 'unavailable'})

    def test_stdlib_only_opt_in_never_launches_the_scanner(self):
        self.install_scanner('scan')
        repo, out = self.stage_repo(), self.root / 'opt-in'
        code, result = self.cli('capture', '--repo', repo, '--mode', 'wip', '--out', out,
                                env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'})
        self.assertEqual((code, result['secret_scan']), (0, {'gitleaks': 'stdlib-only'}))
        self.assertEqual(self.scanner_calls(), [])
        self.assertTrue((out / 'COMPLETE').exists())

    def test_stdlib_only_trades_scanner_coverage_but_keeps_builtin_rules(self):
        self.install_scanner('scan')
        outcome, out = self.gate(scanner_env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'},
                                 after=f'scanner-only shape {SCANNER_SECRET}\n'.encode())
        self.assertEqual(outcome, {'gitleaks': 'stdlib-only'})  # documented reduced coverage
        outcome, out = self.gate(scanner_env={CAPTURE.SECRETS_SCANNER_ENV: 'stdlib-only'},
                                 after=f'builtin shape {BUILTIN_SECRET}\n'.encode())
        self.assert_refused(outcome, out, BUILTIN_SECRET)
        self.assertEqual(self.scanner_calls(), [])

    def test_stdlib_only_is_not_a_fallback_after_scanner_failure(self):
        self.install_scanner('raw', raw=b'garbage', exit_code=0)
        outcome, out = self.gate(after=b'ordinary\n')
        self.assert_refused(outcome, out, message='Capture refused: secrets scanner failed')
        self.assertEqual(len(self.scanner_calls()), 1)

    def test_unrecognised_scanner_modes_are_input_errors_and_write_nothing(self):
        self.install_scanner('scan')
        repo = self.stage_repo()
        for value in ('', 'off', 'gitleaks', 'STDLIB-ONLY', 'stdlib-only ', '0'):
            with self.subTest(value=value):
                out = self.root / f'invalid-{abs(hash(value))}'
                code, result = self.cli('capture', '--repo', repo, '--mode', 'wip', '--out', out,
                                        env={CAPTURE.SECRETS_SCANNER_ENV: value})
                self.assertEqual((code, result['outcome']), (2, 'invalid_input'))
                self.assertFalse(out.exists())
                self.assertEqual(self.scanner_calls(), [])


if __name__ == '__main__':
    unittest.main()
