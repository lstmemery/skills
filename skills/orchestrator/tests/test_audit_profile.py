"""Behavioral acceptance for the credential-isolated snapshot profile."""
from unittest.mock import patch
import datetime
import hashlib
import json
import os
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_profile import collector
from audit_profile import launch
from audit_profile.launch import launch_profile
from audit_profile.process import capture
from audit_profile.schema import canonical
from audit_profile.schema import read_snapshot
from audit_profile.schema import validate_snapshot


class CollectorTests(unittest.TestCase):
    def test_service_state_retains_only_a_closed_state(self):
        def systemctl(argv):
            self.assertEqual(argv, ['/usr/bin/systemctl', 'show', '--property=ActiveState',
                                    '--value', '--', 'example.service'])
            return subprocess.CompletedProcess(argv, 0, b'active\n', b'')
        self.assertEqual(collector.service_state('example', 'example.service', systemctl),
                         {'id': 'example', 'active_state': 'active'})

    def test_fake_secrets_in_every_service_stream_are_withheld(self):
        sentinels = [b'SYNTHETIC_bare_X7q9v0', b'{"cookie":"SYNTHETIC_a=b=c"}',
                     ((b'-' * 5 + b'BEGIN PRIVATE KEY' + b'-' * 5) + b'\nSYNTHETIC_pem\n' + (b'-' * 5 + b'END PRIVATE KEY' + b'-' * 5)),
                     b'PrivateKey=SYNTHETIC_wg', b'PresharedKey=SYNTHETIC_psk',
                     b'Authorization: Bearer SYNTHETIC_header',
                     b'https://user:SYNTHETIC_url@example.invalid',
                     b'\tdata: U1lOVEhFVElDX2VuY29kZWQ=', ((b'-' * 5 + b'BEGIN PRIVATE KEY' + b'-' * 5))]
        for secret in sentinels:
            for stream, returncode in [('stdout', 0), ('stderr', 1)]:
                with self.subTest(case=sentinels.index(secret), stream=stream):
                    def systemctl(argv):
                        return subprocess.CompletedProcess(argv, returncode,
                            secret if stream == 'stdout' else b'active\n',
                            secret if stream == 'stderr' else b'')
                    with self.assertRaises(collector.Refusal) as error:
                        collector.service_state('example', 'example.service', systemctl)
                    self.assertEqual(str(error.exception), 'service-state-refused')
        # Even successful diagnostics stay private.
        def noisy(argv):
            return subprocess.CompletedProcess(argv, 0, b'active\n', sentinels[0])
        self.assertEqual(collector.service_state('example', 'example.service', noisy),
                         {'id': 'example', 'active_state': 'active'})
        def binary(argv):
            return subprocess.CompletedProcess(argv, 0, b'\xff', b'')
        with self.assertRaises(collector.Refusal):
            collector.service_state('example', 'example.service', binary)


class SchemaTests(unittest.TestCase):
    def test_snapshot_rejects_extra_records_and_encoded_payloads(self):
        valid = {'schema_version': 1, 'services': [{'id': 'example', 'active_state': 'active'}],
                 'certificates': []}
        self.assertEqual(validate_snapshot(valid), valid)
        for field in ['private_key', 'journal', 'data', 'cookie']:
            poisoned = dict(valid, **{field: 'SYNTHETIC_encoded_or_bare'})
            with self.subTest(field=field), self.assertRaises(collector.Refusal):
                validate_snapshot(poisoned)
        for invalid in [dict(valid, schema_version=True), dict(valid, services='active'),
                        dict(valid, services=[{'id': 'example', 'active_state': 'active',
                                               'secret': 'SYNTHETIC_extra'}]),
                        dict(valid, certificates=[{'id': 'cert', 'sha256': 'a'*64,
                                                   'not_after': 'not-a-date'}])]:
            with self.assertRaises(collector.Refusal):
                validate_snapshot(invalid)


class CaptureTests(unittest.TestCase):
    def test_partial_streams_are_never_released_on_failure(self):
        commands = [
            ("import os; os.write(1,b'SYNTHETIC_'); os.write(2,b'split_secret'); raise SystemExit(2)",
             'command-failed', 5, 65536),
            ("import os,time; os.write(1,b'SYNTHETIC_timeout'); time.sleep(1)",
             'command-timeout', 0.1, 65536),
            ("import os; os.write(2,b'SYNTHETIC_size'*100)", 'command-size-refused', 5, 64),
        ]
        for script, reason, timeout, limit in commands:
            with self.subTest(reason=reason), self.assertRaises(collector.Refusal) as error:
                capture(['/usr/bin/python3', '-I', '-S', '-c', script], timeout=timeout, limit=limit)
            self.assertEqual(str(error.exception), reason)


class CertificateTests(unittest.TestCase):
    def test_certificate_projects_only_public_fingerprint_and_expiry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            cert = Path(tmp) / 'cert.pem'
            # The key is synthetic and ephemeral; it is never printed or exported.
            capture(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-noenc',
                     '-keyout', str(Path(tmp) / 'key.pem'), '-out', str(cert), '-days', '1',
                     '-subj', '/CN=SYNTHETIC_subject_secret'])
            der = capture(['/usr/bin/openssl', 'x509', '-in', str(cert), '-outform', 'DER']).stdout
            record = collector.certificate_identity('example-cert', der)
            self.assertEqual(set(record), {'id', 'sha256', 'not_after'})
            self.assertEqual(record['id'], 'example-cert')
            self.assertEqual(record['sha256'], hashlib.sha256(der).hexdigest())
            expiry = datetime.datetime.strptime(record['not_after'], '%Y-%m-%dT%H:%M:%SZ')
            self.assertLess(abs((expiry - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).total_seconds() - 86400), 30)
            self.assertNotIn('SYNTHETIC', str(record))
            with self.assertRaises(collector.Refusal):
                collector.certificate_identity('example-cert', b'SYNTHETIC_invalid_DER')


class ConfinementTests(unittest.TestCase):
    def test_real_container_blocks_host_secrets_and_keeps_useful_snapshot(self):
        snapshot = canonical({'schema_version': 1, 'services': [{'id': 'example', 'active_state': 'active'}],
                              'certificates': []})
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp).resolve()
            secret = root / 'host-secret'
            secret.write_text('SYNTHETIC_host_secret_do_not_release')
            planted = {
                'bare': b'SYNTHETIC_bare_password_X9',
                'cookie.json': b'{"cookie":"SYNTHETIC_cookie=a=b"}',
                'pem': ((b'-' * 5 + b'BEGIN PRIVATE KEY' + b'-' * 5) + b'\nSYNTHETIC_pem\n' + (b'-' * 5 + b'END PRIVATE KEY' + b'-' * 5)),
                'unterminated-pem': ((b'-' * 5 + b'BEGIN PRIVATE KEY' + b'-' * 5) + b'\nSYNTHETIC_unterminated'),
                'wg.conf': b'PrivateKey=SYNTHETIC_wg\nPresharedKey=SYNTHETIC_psk',
                'headers': b'Authorization: Bearer SYNTHETIC_auth\nCookie: SYNTHETIC_cookie',
                'url': b'https://user:SYNTHETIC_url@example.invalid',
                'encoded': b'\tdata: U1lOVEhFVElDX2VuY29kZWQ=',
                'split': b'SYNTHETIC_\nsplit_value',
            }
            private = root / 'private'
            private.mkdir()
            for name, value in planted.items():
                (private / name).write_bytes(value)
                self.assertEqual((private / name).read_bytes(), value)

            old = os.environ.get('AUDIT_FAKE_CREDENTIAL')
            os.environ['AUDIT_FAKE_CREDENTIAL'] = 'SYNTHETIC_env_do_not_release'
            try:
                worker = '''import json, os, socket, subprocess, sys
from pathlib import Path
snapshot = json.loads(Path('/input/snapshot.json').read_text())
assert snapshot['services'][0]['active_state'] == 'active'
assert 'AUDIT_FAKE_CREDENTIAL' not in os.environ
assert 'SSH_AUTH_SOCK' not in os.environ
assert os.getuid() == 65534
for target in [HOST_SECRET, PRIVATE_PATH, '/root/.ssh', '/home/deadhand/.ssh', '/run', '/etc', '/sys']:
    assert not Path(target).exists(), target
assert not Path('/usr/bin/ssh').exists()
assert not Path('/usr/bin/sudo').exists()
try:
    Path('/input/snapshot.json').write_text('changed')
    raise AssertionError('snapshot writable')
except OSError:
    pass
try:
    subprocess.run(['/usr/bin/ssh', 'example.invalid'], check=True)
    raise AssertionError('ssh accessible')
except FileNotFoundError:
    pass
# A nested process sees the same credential-free namespace.
subprocess.run([sys.executable, '-I', '-S', '-c',
    'from pathlib import Path; assert not Path(' + repr(HOST_SECRET) + ').exists()'], check=True)
try:
    socket.create_connection(('192.168.50.35', 8006), timeout=0.1)
    raise AssertionError('network accessible')
except OSError:
    pass
Path('/out/audit.json').write_text(json.dumps(snapshot))
print('audit-ok')
'''.replace('HOST_SECRET', repr(str(secret))).replace('PRIVATE_PATH', repr(str(private)))
                result = launch_profile(snapshot, worker.encode(), root / 'out')
                self.assertEqual(result['stdout'], 'audit-ok\n')
                self.assertEqual(result['stderr'], '')
                self.assertTrue(result['attestation']['verified'])
                sinks = [json.dumps(result).encode()]
                sinks += [p.read_bytes() for p in (root / 'out').rglob('*') if p.is_file()]
                for content in sinks:
                    self.assertNotIn(b'SYNTHETIC_', content)
                    for value in planted.values():
                        self.assertNotIn(value, content)

            finally:
                if old is None:
                    os.environ.pop('AUDIT_FAKE_CREDENTIAL')
                else:
                    os.environ['AUDIT_FAKE_CREDENTIAL'] = old

    def test_unverified_confinement_never_executes_worker(self):
        snapshot = b'{"schema_version":1,"services":[],"certificates":[]}'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            output = Path(tmp) / 'out'
            worker = b"from pathlib import Path; Path('/out/EXECUTED').touch()"
            with patch.object(launch.os, 'readlink', return_value='invalid-host-namespace'):
                with self.assertRaises(collector.Refusal):
                    launch.launch_profile(snapshot, worker, output)
            self.assertFalse((output / 'EXECUTED').exists())

    def test_bootstrap_refuses_a_container_sharing_host_network(self):
        def misconfigured_container(argv, **kwargs):
            if argv[0] == '/usr/bin/bwrap':
                argv = list(argv)
                position = argv.index('--unshare-all')
                argv[position:position+1] = ['--unshare-pid', '--unshare-ipc', '--unshare-uts']
            return capture(argv, **kwargs)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            output = Path(tmp) / 'out'
            with patch.object(launch, 'capture', misconfigured_container):
                with self.assertRaises(collector.Refusal):
                    launch.launch_profile(b'{"schema_version":1,"services":[],"certificates":[]}',
                        b"from pathlib import Path; Path('/out/EXECUTED').touch()", output)
            self.assertFalse((output / 'EXECUTED').exists())

    def test_missing_isolation_binary_has_no_host_fallback(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            output = Path(tmp) / 'out'
            with patch.object(launch, 'BWRAP', Path('/usr/bin/does-not-exist-audit-profile')):
                with self.assertRaises(collector.Refusal):
                    launch.launch_profile(b'{"schema_version":1,"services":[],"certificates":[]}',
                        b"from pathlib import Path; Path('/out/EXECUTED').touch()", output)
            self.assertFalse((output / 'EXECUTED').exists())

    def test_existing_output_directory_with_credentials_is_never_mounted(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            output = Path(tmp) / 'out'
            output.mkdir()
            (output / 'secret').write_text('SYNTHETIC_preexisting_secret')
            with self.assertRaises(collector.Refusal):
                launch_profile(b'{"schema_version":1,"services":[],"certificates":[]}',
                               b'print("must-not-execute")', output)

    def test_polling_split_chunks_and_nested_worker_errors_are_buffered(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            worker = b'''import os, subprocess, sys, time
process = subprocess.Popen([sys.executable, '-I', '-S', '-c',
    "import os,time; os.write(1,b'poll-'); time.sleep(.02); os.write(1,b'ok'); os.write(2,b'nested-ok')"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
while process.poll() is None:
    time.sleep(.005)
out, err = process.communicate()
os.write(1, out)
os.write(2, err)
'''
            result = launch_profile(b'{"schema_version":1,"services":[],"certificates":[]}',
                                    worker, Path(tmp) / 'out')
            self.assertEqual(result['stdout'], 'poll-ok')
            self.assertEqual(result['stderr'], 'nested-ok')

    def test_timeout_does_not_return_partial_worker_streams(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            worker = b"import os,time; os.write(1,b'partial-stream'); time.sleep(5)"
            with self.assertRaises(collector.Refusal) as error:
                launch_profile(b'{"schema_version":1,"services":[],"certificates":[]}',
                               worker, Path(tmp) / 'out', timeout=.3)
            self.assertEqual(str(error.exception), 'launch-unverified-or-worker-failed')


class InputBoundaryTests(unittest.TestCase):
    def test_json_input_has_size_depth_duplicate_key_and_encoding_limits(self):
        invalid = [b'{"schema_version":1,"schema_version":1,"services":[],"certificates":[]}',
                   b'{"schema_version":1,"services":[],"certificates":[]}SYNTHETIC_trailing',
                   b'\xff', b'[', b' '*65537, b'['*2000 + b']'*2000]
        for number, raw in enumerate(invalid):
            with self.subTest(case=number), self.assertRaises(collector.Refusal):
                read_snapshot(raw)

    def test_only_the_two_fixed_operations_are_supported(self):
        invalid = [{'operation': 'journal', 'args': ['SYNTHETIC_argument']},
                   {'services': [], 'certificates': [], 'config': 'SYNTHETIC_config'},
                   {'services': [{'id': 'example', 'unit': 'x.service;cat /secret'}], 'certificates': []},
                   {'services': [], 'certificates': [{'id': 'cert', 'host': 'http://example.invalid',
                                                     'port': 443, 'server_name': 'example.invalid'}]}]
        for number, plan in enumerate(invalid):
            with self.subTest(case=number), self.assertRaises(collector.Refusal):
                collector.collect(plan)

    def test_cli_errors_never_echo_arguments_or_bad_input(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            plan = Path(tmp) / 'plan.json'
            plan.write_bytes(b'{"operation":"SYNTHETIC_bad_input"}')
            scripts = Path(__file__).resolve().parents[1] / 'scripts'
            for args in [['SYNTHETIC_bad_argument'], ['collect', '--plan', str(plan.resolve())]]:
                result = subprocess.run(['/usr/bin/python3', '-B', '-m', 'audit_profile', *args],
                                        cwd=scripts, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(result.returncode, 70)
                self.assertEqual(result.stderr, b'')
                self.assertEqual(set(json.loads(result.stdout)), {'refused'})
                self.assertNotIn(b'SYNTHETIC', result.stdout)
