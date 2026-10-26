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
    (root / 'helper-environment.json').write_text(json.dumps({
        key: os.environ[key] for key in ('HERDR_PANE_ID', 'HERDR_WORKSPACE_ID')
    }))
    op = a[0]
    if op == 'status':
        if c.get('status_error'):
            print('{"error":"unavailable"}')
            sys.exit(1)
        print('job | pane | state | disposition | lease')
        print('----+------+-------+-------------+------')
        for j, p in c['rows']:
            print(f'{j} | {p} | running | - | -')
        sys.exit(c.get('status_exit', 0))
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

    def test_partial_helper_results_preserve_supervision_without_new_launches(self):
        self.backlog()
        self.config['rows'] = [['a', '-'], ['running', 'ws:2']]
        (self.run / 'run/state.json').write_text(
            '{"request_id":"replay","request":{"concurrency":3}}')
        self.pane('complete.txt')
        for operation in ('resume', 'status'):
            for code in (6, 10):
                with self.subTest(operation=operation, code=code):
                    self.config.update(resume_exit=0, status_exit=0)
                    self.config[operation + '_exit'] = code
                    self.config['resume_output'] = json.dumps({
                        'batch_state': 'partial' if code == 10 else 'unresolved_effect',
                        'jobs': [{'job_id': 'running', 'pane_id': 'ws:2'}],
                    })
                    (self.run / 'out-a/.woke').unlink(missing_ok=True)
                    self.replay(RESET + 300)
                    self.assertEqual((self.run / 'quota-running.until').read_text().strip(), str(RESET))
                    self.assertTrue((self.run / 'quota-running.until.sent').exists())
                    self.assertTrue((self.run / 'out-a/.woke').exists())
                    self.assertFalse(self.calls('add'))
                    # Once the banner clears, spare capacity must still not admit backlog.
                    (self.root / 'pane.txt').write_text('Working (1s)')
                    self.replay(RESET + 301)
                    self.assertFalse(self.calls('add'))
                    self.pane('complete.txt')

    def test_partial_helper_outcomes_can_finish_completed_runs(self):
        (self.run / 'out-a/DONE').touch()
        for code in (6, 10):
            with self.subTest(code=code):
                (self.sup / 'active-runs.txt').write_text(str(self.run) + '\n')
                self.config.update(resume_exit=code, status_exit=code, finish_exit=code)
                self.config['resume_output'] = json.dumps({'batch_state': 'partial', 'jobs': []})
                self.replay()
                self.assertEqual((self.sup / 'active-runs.txt').read_text(), '')

    def test_finish_requires_explicit_job_fields_before_removal(self):
        (self.run / 'out-a/DONE').touch()
        for job in ({}, {'workspace_closed': True}, {'pane_id': None}):
            with self.subTest(job=job):
                (self.sup / 'active-runs.txt').write_text(str(self.run) + '\n')
                self.config['finish_output'] = json.dumps({'finish_complete': True, 'jobs': [job]})
                self.replay()
                self.assertIn(str(self.run), (self.sup / 'active-runs.txt').read_text())

    def test_echoed_continuation_does_not_schedule_another_reset(self):
        self.pane('complete.txt')
        self.replay(RESET + 300)
        self.assertTrue((self.run / 'quota-a.until.sent').exists())
        (self.root / 'pane.txt').write_text(
            'The usage limit has reset. Continue the task from where you stopped; '
            'do not repeat changes already made.')
        self.replay(RESET + 301)
        self.assertFalse((self.run / 'quota-a.until').exists())
        self.assertFalse((self.run / 'quota-a.until.sent').exists())
        self.replay(RESET + 7200)
        self.assertFalse((self.run / 'quota-a.until').exists())
        self.assertEqual(len([a for a in self.calls('pane') if a[1] == 'send-text']), 1)

    def test_recorded_coordinator_is_split_origin_and_workspaces_are_labeled_once(self):
        self.env.update(HERDR_PANE_ID='stale:1', HERDR_WORKSPACE_ID='stale')
        self.replay()
        self.replay()
        environment = json.loads((self.root / 'helper-environment.json').read_text())
        self.assertEqual(environment, {
            'HERDR_PANE_ID': 'coordinator:1', 'HERDR_WORKSPACE_ID': 'coordinator',
        })
        self.assertEqual(self.calls('workspace'), [
            ['workspace', 'rename', 'ws', 'codex · managed a'],
        ])

    def test_cancelled_job_skips_pane_supervision_and_done_wake_then_finishes(self):
        (self.run / 'out-a/CANCELLED').touch()
        (self.run / 'quota-a.until').write_text(str(RESET))
        (self.run / 'quota-a.until.sent').touch()
        self.pane('complete.txt')
        self.config['finish_output'] = json.dumps({
            'finish_complete': False,
            'jobs': [{'pane_id': 'ws:1', 'workspace_closed': False}],
        })
        self.replay()
        self.assertFalse(self.calls('pane'))
        self.assertFalse(self.calls('workspace'))
        self.assertFalse((self.run / 'quota-a.until').exists())
        self.assertFalse((self.run / 'quota-a.until.sent').exists())
        self.assertFalse((self.run / 'out-a/.woke').exists())
        self.assertFalse((self.sup / 'wake-queue.txt').exists())
        self.assertIn('active=0', (self.run / 'heartbeat.log').read_text())
        self.assertIn(str(self.run), (self.sup / 'active-runs.txt').read_text())
        del self.config['finish_output']
        self.replay()
        self.assertEqual(len(self.calls('finish')), 2)
        self.assertEqual((self.sup / 'active-runs.txt').read_text(), '')

    def test_panelless_job_needs_terminal_marker_then_no_workspace_closure(self):
        self.config['rows'] = [['a', '-']]
        self.config['finish_output'] = json.dumps({
            'finish_complete': True,
            'jobs': [{'pane_id': None, 'workspace_closed': False}],
        })
        self.replay()
        self.assertFalse(self.calls('finish'))
        self.assertIn(str(self.run), (self.sup / 'active-runs.txt').read_text())
        (self.run / 'out-a/DONE').touch()
        self.replay()
        self.assertFalse(self.calls('pane'))
        self.assertFalse(self.calls('workspace'))
        self.assertEqual((self.sup / 'active-runs.txt').read_text(), '')

    def test_finish_retries_without_repeating_job_done_wake(self):
        (self.run / 'out-a/DONE').touch()
        self.config['finish_output'] = json.dumps({
            'finish_complete': False,
            'jobs': [{'pane_id': 'ws:1', 'workspace_closed': False}],
        })
        self.replay()
        self.assertIn(str(self.run), (self.sup / 'active-runs.txt').read_text())
        self.assertTrue((self.run / 'out-a/.woke').exists())
        del self.config['finish_output']
        self.replay()
        self.assertEqual((self.sup / 'active-runs.txt').read_text(), '')
        self.assertEqual(len(self.calls('finish')), 2)
        self.assertEqual((self.sup / 'wake-queue.txt').read_text().count('a DONE'), 1)

    def test_continuation_uses_default_two_second_delay_before_enter(self):
        sleep = self.bin / 'sleep'
        sleep.write_text(STUB)
        sleep.chmod(0o755)
        del self.env['HEARTBEAT_SEND_DELAY']
        self.pane('complete.txt')
        self.replay(RESET + 300)
        calls = list(map(json.loads, (self.root / 'calls.jsonl').read_text().splitlines()))
        index = next(i for i, (_, args) in enumerate(calls) if args[:2] == ['pane', 'send-text'])
        self.assertEqual(calls[index][1][2], 'ws:1')
        self.assertEqual(calls[index + 1], ['sleep', ['2']])
        self.assertEqual(calls[index + 2], ['herdr', ['pane', 'send-keys', 'ws:1', 'Enter']])
