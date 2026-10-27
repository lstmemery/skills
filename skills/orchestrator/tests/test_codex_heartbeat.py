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
        if '--summary' in a:
            print('job | pane | state | disposition | lease')
            print('----+------+-------+-------------+------')
            for j, p in c['rows']:
                print(f'{j} | {p} | running | - | -')
        else:
            print(c.get('status_output', json.dumps({'jobs': [
                {'job_id': j, 'pane_id': None if p == '-' else p} for j, p in c['rows']
            ]})))
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

    def quota_job(self, **changes):
        job = {
            'job_id': 'a', 'pane_id': 'ws:1', 'attempt_id': 'original-attempt',
            'route': {'mode': 'agent', 'runtime': 'codex'}, 'settled': False,
            'workspace_closed': False, 'collection': None, 'collection_error': None,
            'pending_effect': None, 'issue': 'quota recovery deferred',
            'rate_limit_seen': True, 'rate_limit_recoveries': 0, 'continuation_count': 0,
            'observed': {'identity_verified': True, 'state': 'idle',
                         'rate_limit': {'source': 'reset', 'retry_after_seconds': 0}},
        }
        job.update(changes)
        self.config['status_output'] = json.dumps({'batch_state': 'partial', 'jobs': [job]})
        self.config['status_exit'] = 10
        return job

    def test_quota_supervision_uses_reconciled_managed_continuation(self):
        job = self.quota_job()
        self.pane('wrapped.txt')
        working = {**job, 'pending_effect': None, 'rate_limit_recoveries': 1,
                   'continuation_count': 1,
                   'observed': {'identity_verified': True, 'state': 'working', 'prompt_verified': True}}
        self.config['continue_output'] = json.dumps({'batch_state': 'active', 'jobs': [working]})
        self.replay(RESET + 300)
        calls = self.calls('continue')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index('--job') + 1], 'a')
        self.assertEqual(calls[0][calls[0].index('--reconciliation') + 1], 'run/quota-recovery/a.json')
        self.assertTrue(all('--defer-quota-recovery' in call for call in self.calls('resume') + self.calls('status')))
        self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))
        self.assertIn('continued a ws:1 through managed quota recovery', (self.run / 'heartbeat.log').read_text())

    def test_success_reply_for_another_attempt_is_not_reported_as_continued(self):
        job = self.quota_job()
        job.update(attempt_id='other-attempt', rate_limit_recoveries=1, continuation_count=1,
                   observed={'identity_verified': True, 'state': 'working', 'prompt_verified': True})
        self.config['continue_output'] = json.dumps({'jobs': [job]})
        self.replay()
        self.assertNotIn('continued a', (self.run / 'heartbeat.log').read_text())
        self.assertIn('quota continuation held', (self.run / 'heartbeat.log').read_text())

    def test_absent_receipt_allows_the_managed_continuation_preflight(self):
        self.quota_job(collection_error="[Errno 2] No such file or directory: 'receipt.json'")
        self.config.update(continue_exit=5, continue_output='{"error":"decision_needed"}')
        self.replay()
        self.assertEqual(len(self.calls('continue')), 1)
        self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))

    def test_capacity_banner_cannot_replay_an_unresolved_submission(self):
        self.quota_job(rate_limit_seen=False, pending_effect={'action': 'prompt'},
                       observed={'identity_verified': True, 'state': 'idle'})
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.')
        self.replay()
        self.replay(RESET + 300)
        self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))
        self.assertFalse(self.calls('continue'))

    def test_wrapped_quota_banner_delegates_reset_handling(self):
        self.quota_job()
        self.pane('wrapped.txt')
        self.config.update(continue_exit=5, continue_output='{"error":"decision_needed","message":"reset pending"}')
        self.replay()
        self.assertEqual(len(self.calls('continue')), 1)
        self.assertFalse((self.run / 'quota-a.until').exists())
        self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))

    def test_truncated_banner_never_gets_an_invented_reset_or_direct_input(self):
        self.quota_job()
        self.pane('truncated.txt')
        self.config.update(continue_exit=5, continue_output='{"error":"decision_needed","message":"reset unparseable"}')
        self.replay()
        self.replay(RESET + 7200)
        self.assertEqual(len(self.calls('continue')), 2)
        self.assertFalse((self.run / 'quota-a.until').exists())
        self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))

    def test_quota_continuation_waits_on_helper_without_local_timer(self):
        self.quota_job()
        self.config.update(continue_exit=5, continue_output='{"error":"decision_needed","message":"reset pending"}')
        self.replay(RESET - 1)
        self.replay(RESET + 299)
        self.assertEqual(len(self.calls('continue')), 2)
        self.assertNotIn('continued a', (self.run / 'heartbeat.log').read_text())

    def test_capacity_stall_continues_once_after_five_minute_backoff(self):
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.')
        self.replay()
        self.replay(RESET + 299)
        self.assertFalse(any(a[1] == 'send-text' for a in self.calls('pane')))
        self.replay(RESET + 300)
        self.replay(RESET + 3600)
        sent = [a for a in self.calls('pane') if a[1].startswith('send-')]
        self.assertEqual([a[1] for a in sent], ['send-text', 'send-keys'])
        self.assertEqual(sent[0][2], 'ws:1')
        self.assertTrue((self.run / 'capacity-a.until.sent').exists())

    def test_wrapped_capacity_banner_is_detected(self):
        (self.root / 'pane.txt').write_text(
            'Selected model is at\ncapacity. Please try a different\nmodel.')
        self.replay()
        self.replay(RESET + 300)
        self.assertEqual(len([a for a in self.calls('pane') if a[1] == 'send-text']), 1)

    def test_capacity_continuation_echo_does_not_schedule_another_stall(self):
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.')
        self.replay()
        self.replay(RESET + 300)
        sent = [a for a in self.calls('pane') if a[1] == 'send-text']
        self.assertEqual(len(sent), 1)
        (self.root / 'pane.txt').write_text('› ' + sent[0][3])
        self.replay(RESET + 301)
        self.assertFalse((self.run / 'capacity-a.until').exists())
        self.assertFalse((self.run / 'capacity-a.until.sent').exists())
        self.replay(RESET + 7200)
        self.assertEqual(len([a for a in self.calls('pane') if a[1] == 'send-text']), 1)

    def test_capacity_stall_rearms_only_after_banner_clears(self):
        banner = 'Selected model is at capacity. Please try a different model.'
        (self.root / 'pane.txt').write_text(banner)
        self.replay()
        self.replay(RESET + 300)
        (self.root / 'pane.txt').write_text(banner + '\nWorking (2s)')
        self.replay(RESET + 301)
        self.assertFalse((self.run / 'capacity-a.until').exists())
        self.assertFalse((self.run / 'capacity-a.until.sent').exists())
        (self.root / 'pane.txt').write_text(banner)
        self.replay(RESET + 400)
        self.replay(RESET + 699)
        self.assertEqual(len([a for a in self.calls('pane') if a[1] == 'send-text']), 1)
        self.replay(RESET + 700)
        self.assertEqual(len([a for a in self.calls('pane') if a[1] == 'send-text']), 2)

    def test_busy_capacity_pane_never_schedules_continuation(self):
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.\nWorking (2s)')
        self.replay()
        self.replay(RESET + 3600)
        self.assertFalse((self.run / 'capacity-a.until').exists())
        self.assertFalse(any(a[1] == 'send-text' for a in self.calls('pane')))

    def test_done_capacity_job_clears_wait_without_reading_or_continuing(self):
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.')
        self.replay()
        (self.run / 'out-a/DONE').touch()
        self.replay(RESET + 300)
        self.assertFalse((self.run / 'capacity-a.until').exists())
        self.assertFalse((self.run / 'capacity-a.until.sent').exists())
        self.assertEqual(len([a for a in self.calls('pane') if a[1] == 'read']), 1)
        self.assertFalse(any(a[1] == 'send-text' for a in self.calls('pane')))

    def test_failed_capacity_send_retries_without_marking_success(self):
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.')
        self.replay()
        self.config['send_exit'] = 1
        self.replay(RESET + 300)
        self.assertFalse((self.run / 'capacity-a.until.sent').exists())
        self.assertNotIn('continued a', (self.run / 'heartbeat.log').read_text())
        self.config['send_exit'] = 0
        self.replay(RESET + 420)
        self.replay(RESET + 540)
        self.assertTrue((self.run / 'capacity-a.until.sent').exists())
        self.assertEqual(len([a for a in self.calls('pane') if a[1] == 'send-text']), 2)

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

    def test_worker_quota_wait_blocks_backlog_with_spare_capacity(self):
        self.backlog()
        (self.run / 'out-a/DONE').unlink()
        (self.run / 'run/state.json').write_text(
            '{"request_id":"replay","request":{"concurrency":3}}')
        self.quota_job()
        self.config.update(continue_exit=5, continue_output='{"error":"decision_needed"}')
        self.replay(RESET - 1)
        self.assertEqual(len(self.calls('continue')), 1)
        self.assertFalse(self.calls('add'))

    def test_existing_active_worker_counts_toward_stored_concurrency(self):
        self.backlog()
        (self.run / 'out-a/DONE').unlink()
        # The active worker fills the durable cap even if the manifest allows more.
        (self.run / 'manifest.json').write_text('{"concurrency":4}')
        self.replay()
        self.assertFalse(self.calls('add'))

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

    def test_ambiguous_managed_delivery_is_retained_without_replay(self):
        self.quota_job()
        response = '{"error":"unresolved_effect","message":"delivery unproven"}'
        self.config.update(continue_exit=6, continue_output=response)
        self.replay(RESET + 300)
        self.assertNotIn('continued a', (self.run / 'heartbeat.log').read_text())
        self.assertTrue((self.run / 'last-continue-a.json').exists())
        self.assertEqual((self.run / 'last-continue-a.json').read_text().strip(), response)
        self.quota_job(pending_effect={'action': 'continue'},
                       rate_limit_recoveries=1, continuation_count=1)
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.')
        self.replay(RESET + 600)
        self.replay(RESET + 900)
        self.assertEqual(len(self.calls('continue')), 1)
        self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))

    def test_working_observation_never_requests_quota_continuation(self):
        self.quota_job(rate_limit_seen=False,
                       observed={'identity_verified': True, 'state': 'working'})
        (self.root / 'pane.txt').write_text("You've hit your usage limit. Working (2s)")
        self.replay(RESET + 400)
        self.assertFalse(self.calls('continue'))
        self.assertFalse(any(a[1] == 'send-text' for a in self.calls('pane')))

    def test_observed_alive_unresolved_starts_allow_backlog_with_spare_capacity(self):
        self.backlog()
        (self.run / 'out-a/DONE').unlink()
        (self.run / 'run/state.json').write_text(
            '{"request_id":"replay","request":{"concurrency":2}}')
        alive = {
            'job_id': 'a', 'pane_id': 'ws:1', 'phase': 'moved', 'settled': False,
            'route': {'mode': 'agent', 'runtime': 'codex'},
            'pending_effect': {'action': 'start'},
            'issue': 'Updated human diagnostic: the start acknowledgement is missing.',
            'issue_code': 'effect_delivery_unproven',
            'observed': {'state': 'working', 'identity_verified': True, 'prompt_verified': False},
        }
        payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [alive]})
        self.config.update(resume_exit=6, status_exit=6,
                           resume_output=payload, status_output=payload)

        self.replay()

        self.assertEqual(len(self.calls('add')), 1)
        self.assertEqual(json.loads((self.run / 'add-b.json').read_text())['concurrency'], 2)
        self.assertNotIn('resume-failed', (self.run / 'heartbeat.log').read_text())

    def test_settled_sibling_collection_error_does_not_veto_observed_alive_start(self):
        self.backlog()
        (self.run / 'out-a/DONE').unlink()
        (self.run / 'run/state.json').write_text(
            '{"request_id":"replay","request":{"concurrency":3}}')
        alive = {
            'job_id': 'a', 'pane_id': 'ws:1', 'phase': 'moved', 'settled': False,
            'route': {'mode': 'agent', 'runtime': 'codex'},
            'pending_effect': {'action': 'start'},
            'issue': 'effect delivery remains unproven; it will not be replayed',
            'issue_code': 'effect_delivery_unproven',
            'observed': {'state': 'working', 'identity_verified': True, 'prompt_verified': False},
        }
        completed = {
            'job_id': 'completed', 'pane_id': 'ws:2', 'phase': 'submitted', 'settled': True,
            'route': {'mode': 'agent', 'runtime': 'codex'},
            'pending_effect': None, 'issue': None,
            'observed': {'state': 'done', 'identity_verified': True, 'prompt_verified': False},
            'collection_error': 'missing receipt',
        }
        (self.run / 'out-completed').mkdir()
        (self.run / 'out-completed/DONE').touch()
        self.config['rows'].append(['completed', 'ws:2'])
        payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [alive, completed]})
        self.config.update(resume_exit=6, status_exit=6,
                           resume_output=payload, status_output=payload)

        self.replay()

        self.assertEqual(len(self.calls('add')), 2)
        log = (self.run / 'heartbeat.log').read_text()
        self.assertIn('collection-error completed: missing receipt', log)
        self.assertNotIn('resume-failed', log)
        self.assertIn('active=3', log)

    def test_missing_or_unrecognized_issue_code_vetoes_unresolved_start(self):
        self.backlog()
        (self.run / 'out-a/DONE').unlink()
        (self.run / 'run/state.json').write_text(
            '{"request_id":"replay","request":{"concurrency":3}}')
        alive = {
            'job_id': 'a', 'pane_id': 'ws:1', 'settled': False,
            'route': {'mode': 'agent', 'runtime': 'codex'},
            'pending_effect': {'action': 'start'},
            'issue': 'effect delivery remains unproven; it will not be replayed',
            'issue_code': 'effect_delivery_unproven',
            'observed': {'state': 'working', 'identity_verified': True},
        }
        safe_payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [alive]})
        for reason in ({}, {'issue_code': None}, {'issue_code': 'startup_blocked'}):
            for operation in ('resume', 'status'):
                with self.subTest(reason=reason, operation=operation):
                    unresolved = dict(alive)
                    del unresolved['issue_code']
                    unresolved.update(reason)
                    payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [unresolved]})
                    self.config.update(resume_exit=6, status_exit=6,
                                       resume_output=safe_payload, status_output=safe_payload)
                    self.config[operation + '_output'] = payload
                    self.replay()
                    self.assertFalse(self.calls('add'))

    def test_mixed_unknown_effects_veto_backlog_even_with_an_observed_alive_start(self):
        self.backlog()
        (self.run / 'out-a/DONE').unlink()
        (self.run / 'run/state.json').write_text(
            '{"request_id":"replay","request":{"concurrency":4}}')
        alive = {
            'job_id': 'a', 'pane_id': 'ws:1', 'phase': 'moved', 'settled': False,
            'route': {'mode': 'agent', 'runtime': 'codex'},
            'pending_effect': {'action': 'start'}, 'issue': None,
            'issue_code': 'effect_delivery_unproven',
            'observed': {'state': 'working', 'identity_verified': True},
        }
        for observation in ({'state': 'unknown', 'identity_verified': False},
                            {'state': 'unknown', 'identity_verified': True},
                            {'state': 'working', 'identity_verified': False},
                            {'state': 'idle', 'identity_verified': True},
                            {'state': 'blocked', 'identity_verified': True}):
            for operation in ('resume', 'status'):
                with self.subTest(observation=observation, operation=operation):
                    unknown = dict(alive, job_id='unknown', pane_id='ws:2', observed=observation)
                    payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [alive, unknown]})
                    safe_payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [alive]})
                    self.config.update(resume_exit=6, status_exit=6,
                                       resume_output=safe_payload, status_output=safe_payload)
                    self.config[operation + '_output'] = payload
                    self.replay()
                    self.assertFalse(self.calls('add'))

                    # A sibling without a pending effect must still have a healthy observation.
                    if observation['state'] in ('unknown', 'blocked') or not observation['identity_verified']:
                        unknown.update(pending_effect=None, collection_error='missing receipt')
                        self.config[operation + '_output'] = json.dumps({
                            'batch_state': 'unresolved_effect', 'jobs': [alive, unknown],
                        })
                        self.replay()
                        self.assertFalse(self.calls('add'))

    def test_observed_alive_fallback_still_obeys_capacity_and_effect_type(self):
        self.backlog()
        (self.run / 'out-a/DONE').unlink()
        alive = {
            'job_id': 'a', 'pane_id': 'ws:1', 'phase': 'moved', 'settled': False,
            'route': {'mode': 'agent', 'runtime': 'codex'},
            'pending_effect': {'action': 'start'}, 'issue': None,
            'issue_code': 'effect_delivery_unproven',
            'observed': {'state': 'working', 'identity_verified': True},
        }
        payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [alive]})
        self.config.update(resume_exit=6, status_exit=6,
                           resume_output=payload, status_output=payload)
        self.replay()
        self.assertFalse(self.calls('add'))  # The verified live start fills cap=1.

        (self.run / 'run/state.json').write_text(
            '{"request_id":"replay","request":{"concurrency":3}}')
        for action in ('prompt', 'jail', 'split', 'move'):
            with self.subTest(action=action):
                alive['pending_effect'] = {'action': action}
                payload = json.dumps({'batch_state': 'unresolved_effect', 'jobs': [alive]})
                self.config.update(resume_output=payload, status_output=payload)
                self.replay()
                self.assertFalse(self.calls('add'))

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
        self.assertTrue(any(a[:2] == ['pane', 'read'] for a in self.calls('pane')))
        self.assertTrue(self.calls('status'))

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
        job = self.quota_job(job_id='running', pane_id='ws:2')
        self.config['status_output'] = json.dumps({'jobs': [
            {'job_id': 'a', 'pane_id': None}, job]})
        self.config.update(continue_exit=5, continue_output='{"error":"decision_needed"}')
        for operation in ('resume', 'status'):
            for code in (6, 10):
                with self.subTest(operation=operation, code=code):
                    self.config.update(resume_exit=0, status_exit=0)
                    self.config['status_output'] = json.dumps({'jobs': [
                        {'job_id': 'a', 'pane_id': None}, job]})
                    self.config[operation + '_exit'] = code
                    self.config['resume_output'] = json.dumps({
                        'batch_state': 'partial' if code == 10 else 'unresolved_effect',
                        'jobs': [{'job_id': 'running', 'pane_id': 'ws:2'}],
                    })
                    (self.run / 'out-a/.woke').unlink(missing_ok=True)
                    self.replay(RESET + 300)
                    self.assertTrue(self.calls('continue'))
                    self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))
                    self.assertTrue((self.run / 'out-a/.woke').exists())
                    self.assertFalse(self.calls('add'))
                    # Partial helper replies still gate backlog after quota clears.
                    self.config['status_output'] = json.dumps({'jobs': [
                        {'job_id': 'a', 'pane_id': None},
                        {**job, 'rate_limit_seen': False, 'observed': {
                            'identity_verified': True, 'state': 'working'}}]})
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
        (self.root / 'pane.txt').write_text(
            'The usage limit has reset. Continue the task from where you stopped; '
            'do not repeat changes already made.')
        self.replay(RESET + 301)
        self.replay(RESET + 7200)
        self.assertFalse(self.calls('continue'))
        self.assertFalse((self.run / 'quota-a.until').exists())
        self.assertFalse(any(a[1].startswith('send-') for a in self.calls('pane')))

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

    def test_capacity_continuation_uses_default_two_second_delay_before_enter(self):
        sleep = self.bin / 'sleep'
        sleep.write_text(STUB)
        sleep.chmod(0o755)
        del self.env['HEARTBEAT_SEND_DELAY']
        (self.root / 'pane.txt').write_text(
            'Selected model is at capacity. Please try a different model.')
        self.replay(RESET)
        self.replay(RESET + 300)
        calls = list(map(json.loads, (self.root / 'calls.jsonl').read_text().splitlines()))
        index = next(i for i, (_, args) in enumerate(calls) if args[:2] == ['pane', 'send-text'])
        self.assertEqual(calls[index][1][2], 'ws:1')
        self.assertEqual(calls[index + 1], ['sleep', ['2']])
        self.assertEqual(calls[index + 2], ['herdr', ['pane', 'send-keys', 'ws:1', 'Enter']])
