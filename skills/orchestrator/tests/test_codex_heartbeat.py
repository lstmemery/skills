"""Offline subprocess replays; no live supervisor, pane, or quota command is used."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/codex-heartbeat.sh'
FIXTURES = Path(__file__).with_name('fixtures') / 'heartbeat'
RESET = 1792958400  # 2026-10-25 20:00:00 UTC

STUB = '''#!/usr/bin/python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['REPLAY_ROOT'])
c = json.loads((root / 'config.json').read_text())
a = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps([name, a]) + '\\n')
if name == 'herdr':
    if a[:2] == ['pane', 'read']:
        print((root / 'pane.txt').read_text())
    if a[:2] == ['pane', 'send-keys']:
        sys.exit(c.get('send_exit', 0))
elif name == 'quota-axi':
    print(json.dumps(c['quota']))
    sys.exit(c.get('quota_exit', 0))
elif name == 'herdr-jobs.py':
    op = a[0]
    if op == 'status':
        if c.get('status_error'):
            print('{"error":"unavailable"}')
            sys.exit(1)
        print('job | pane | state | disposition | lease')
        print('----+------+-------+-------------+------')
        for j, p in c['rows']:
            print(f'{j} | {p} | running | - | -')
    else:
        print(c.get(op + '_output', json.dumps({'jobs': [{'job_id': 'a', 'pane_id': 'ws:1', 'workspace_closed': True}], 'finish_complete': True})))
        sys.exit(c.get(op + '_exit', 0))
'''


class HeartbeatReplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='heartbeat-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sup = self.root / 'sup'
        self.run = self.root / 'managed'
        self.bin = self.root / 'bin'
        for p in (self.sup, self.run / 'run', self.run / 'out-a', self.bin):
            p.mkdir(parents=True, exist_ok=True)
        (self.sup / 'active-runs.txt').write_text(str(self.run) + '\n')
        (self.sup / 'coordinator.json').write_text('{"pane_id":"coordinator:1"}')
        (self.run / 'run/state.json').write_text('{"request_id":"replay","request":{"concurrency":1}}')
        (self.run / 'manifest.json').write_text('{"concurrency":1}')
        (self.root / 'pane.txt').write_text('Working (1s)')
        for name in ('herdr', 'herdr-jobs.py', 'quota-axi', 'wake'):
            p = self.bin / name
            p.write_text(STUB)
            p.chmod(0o755)
        self.config = {'rows': [['a', 'ws:1']], 'quota': {'providers': [{'state': {'stale': False}, 'windows': [{'percentRemaining': 50}]}]}}
        self.env = dict(os.environ, SUP=str(self.sup), HERDR_JOBS=str(self.bin / 'herdr-jobs.py'),
                        HERDR='herdr', QUOTA_AXI='quota-axi', WAKE_COORDINATOR=str(self.bin / 'wake'),
                        HEARTBEAT_PATH=str(self.bin) + ':/usr/bin:/bin', PATH='/usr/bin:/bin',
                        REPLAY_ROOT=str(self.root), HEARTBEAT_NOW=str(RESET), HEARTBEAT_SEND_DELAY='0', TZ='UTC')

    def replay(self, now=RESET):
        (self.root / 'config.json').write_text(json.dumps(self.config))
        self.env['HEARTBEAT_NOW'] = str(now)
        result = subprocess.run(['/bin/bash', str(SCRIPT)], env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self, operation):
        path = self.root / 'calls.jsonl'
        return [a for name, a in map(json.loads, path.read_text().splitlines()) if a and a[0] == operation]

    def pane(self, name):
        (self.root / 'pane.txt').write_text((FIXTURES / name).read_text())

    def test_wrapped_banner_records_reset(self):
        self.pane('wrapped.txt')
        self.replay()
        self.assertEqual((self.run / 'quota-a.until').read_text().strip(), str(RESET))

    def test_truncated_banner_uses_conservative_wait(self):
        self.pane('truncated.txt')
        self.replay()
        self.assertEqual((self.run / 'quota-a.until').read_text().strip(), str(RESET + 3600))
        self.assertFalse(self.calls('pane') and any(a[1] == 'send-text' for a in self.calls('pane')))

    def test_continues_once_at_reset_plus_five_minutes(self):
        self.pane('complete.txt')
        self.replay(RESET - 1)
        self.replay(RESET + 299)
        self.assertFalse(any(a[1] == 'send-text' for a in self.calls('pane')))
        self.replay(RESET + 300)
        self.replay(RESET + 301)
        sent = [a[1] for a in self.calls('pane') if a[1].startswith('send-')]
        self.assertEqual(sent, ['send-text', 'send-keys'])

    def backlog(self):
        (self.run / 'backlog').mkdir(exist_ok=True)
        (self.run / 'ORDER').write_text('a\nb\nc\n')
        for job in ('b', 'c'):
            (self.run / f'backlog/task-{job}.md').write_text(f'Replay task {job}\n')
        (self.run / 'out-a/DONE').touch()

    def test_backlog_preserves_stored_concurrency_and_capacity(self):
        self.backlog()
        # The durable request, rather than a missing or edited original manifest, owns the cap.
        (self.run / 'manifest.json').write_text('{"concurrency":4}')
        self.replay()
        self.assertEqual(len(self.calls('add')), 1)
        self.assertEqual(json.loads((self.run / 'add-b.json').read_text())['concurrency'], 1)

    def test_add_failure_is_never_logged_as_added(self):
        self.backlog()
        self.config.update(add_output='{"jobs":[]}', add_exit=1)
        self.replay()
        log = (self.run / 'heartbeat.log').read_text()
        self.assertIn('add-failed b', log)
        self.assertNotIn('added b', log)

    def test_quota_at_threshold_or_stale_blocks_backlog(self):
        self.backlog()
        provider = self.config['quota']['providers'][0]
        for remaining, stale in ((10, False), (0, False), (80, True)):
            with self.subTest(remaining=remaining, stale=stale):
                provider['windows'][0]['percentRemaining'] = remaining
                provider['state']['stale'] = stale
                self.replay()
                self.assertFalse(self.calls('add'))
        self.assertEqual((self.sup / 'wake-queue.txt').read_text().count('QUOTA GATE:'), 1)

    def test_fractional_quota_above_threshold_allows_backlog(self):
        self.backlog()
        self.config['quota']['providers'][0]['windows'][0]['percentRemaining'] = 10.5
        self.replay()
        self.assertEqual(len(self.calls('add')), 1)

    def test_finish_partial_keeps_run_active(self):
        (self.run / 'out-a/DONE').touch()
        self.config['finish_output'] = json.dumps({'finish_complete': False, 'jobs': [{'job_id': 'a', 'workspace_closed': True}]})
        self.replay()
        self.assertIn(str(self.run), (self.sup / 'active-runs.txt').read_text())

    def test_finish_removes_only_completed_run(self):
        (self.run / 'out-a/DONE').touch()
        other = self.root / 'other.[run]'
        (self.sup / 'active-runs.txt').write_text(f'{self.run}\n{other}\n')
        self.replay()
        self.assertEqual((self.sup / 'active-runs.txt').read_text(), f'{other}\n')
        self.assertEqual(len(self.calls('finish')), 1)
        self.assertIn('all 1 jobs DONE', (self.sup / 'wake-queue.txt').read_text())

    def test_add_error_json_and_malformed_output_are_failures(self):
        self.backlog()
        for output in ('{"error":"conflict"}', '{broken', 'null'):
            with self.subTest(output=output):
                self.config['add_output'] = output
                self.replay()
                log = (self.run / 'heartbeat.log').read_text()
                self.assertIn('add-failed b', log)
                self.assertNotIn('added b', log)

    def test_failed_send_is_not_reported_as_continued(self):
        self.pane('complete.txt')
        self.config['send_exit'] = 1
        self.replay(RESET + 300)
        self.assertNotIn('continued a', (self.run / 'heartbeat.log').read_text())
        self.assertFalse((self.run / 'quota-a.until.sent').exists())

    def test_working_pane_clears_quota_wait(self):
        self.pane('complete.txt')
        self.replay()
        (self.root / 'pane.txt').write_text("You've hit your usage limit. Working (2s)")
        self.replay(RESET + 400)
        self.assertFalse((self.run / 'quota-a.until').exists())
        self.assertFalse(any(a[1] == 'send-text' for a in self.calls('pane')))

    def test_failed_resume_does_not_feed_backlog(self):
        self.backlog()
        self.config.update(resume_output='{"error":"unavailable"}', resume_exit=1)
        self.replay()
        self.assertFalse(self.calls('add'))

    def test_run_path_with_spaces_is_processed(self):
        target = self.root / 'run with spaces.[1]'
        self.run.rename(target)
        self.run = target
        (self.sup / 'active-runs.txt').write_text(str(target) + '\n')
        (target / 'out-a/DONE').touch()
        self.replay()
        self.assertEqual((self.sup / 'active-runs.txt').read_text(), '')

    def test_systemd_minimal_path_uses_overridable_commands(self):
        self.pane('complete.txt')
        # Unique names guarantee a failed lookup can never fall through to live tools.
        for name in ('herdr', 'quota-axi'):
            p = self.bin / ('replay-' + name)
            p.write_text(STUB.replace("name = pathlib.Path(sys.argv[0]).name", "name = pathlib.Path(sys.argv[0]).name.removeprefix('replay-')"))
            p.chmod(0o755)
        self.env.update(HERDR='replay-herdr', QUOTA_AXI='replay-quota-axi')
        self.replay()
        self.assertTrue((self.run / 'quota-a.until').exists())
        self.assertTrue(any(a[:2] == ['pane', 'read'] for a in self.calls('pane')))

    def test_quota_gate_also_prevents_resume_launching_pending_jobs(self):
        self.config['quota']['providers'][0]['windows'][0]['percentRemaining'] = 10
        self.replay()
        self.assertFalse(self.calls('resume'))
        self.assertTrue(self.calls('status'))

    def test_unknown_quota_never_launches(self):
        self.backlog()
        for quota in ({}, {'providers': []}, {'providers': [{'state': {'stale': False}, 'windows': []}]}):
            with self.subTest(quota=quota):
                self.config['quota'] = quota
                self.replay()
                self.assertFalse(self.calls('add'))
                self.assertFalse(self.calls('resume'))

    def test_low_second_quota_window_blocks_launches(self):
        self.backlog()
        self.config['quota']['providers'][0]['windows'].append({'percentRemaining': 5})
        self.replay()
        self.assertFalse(self.calls('add'))
        self.assertFalse(self.calls('resume'))

    def test_quota_command_failure_blocks_launches(self):
        self.backlog()
        self.config['quota_exit'] = 1
        self.replay()
        self.assertFalse(self.calls('add'))
        self.assertFalse(self.calls('resume'))

    def test_empty_run_does_not_read_an_unidentified_pane(self):
        self.config['rows'] = []
        self.replay()
        self.assertFalse(self.calls('pane'))
        self.assertFalse(self.calls('finish'))

    def test_completed_job_clears_quota_wait_before_feeding_backlog(self):
        self.backlog()
        (self.run / 'quota-a.until').write_text(str(RESET))
        (self.run / 'quota-a.until.sent').touch()
        self.replay()
        self.assertEqual(len(self.calls('add')), 1)
