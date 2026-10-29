"""Offline contract checks for the fixed direct Pi launch boundary."""

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from herdr_jobs import local_lane
from herdr_jobs.admission import AdmissionStore
from herdr_jobs.records import JobError, load_json, policy_record
from herdr_jobs.routing_decision import decision_for


class DirectRouteDecisionTest(unittest.TestCase):
    def test_missing_coordinator_decision_has_no_launch_or_lease_effects(self):
        policy = policy_record(load_json(local_lane.POLICY_PATH))
        calls = []

        def host(argv, *, env=None):
            calls.append(argv)
            return 0, b'{"result":{}}', b''

        with tempfile.TemporaryDirectory() as directory:
            store = AdmissionStore(Path(directory) / 'admission')
            with patch.dict(os.environ, {'ORCHESTRATOR_ADMISSION_DIR': str(Path(directory) / 'admission')}):
                with self.assertRaises(JobError) as caught:
                    local_lane.start_direct_local_lane(
                        policy['local_lane'], task_class='summary',
                        local_lane_scope={'network_access': False, 'credential_access': False,
                                          'credentials_in_environment': False, 'destructive_actions': False},
                        pane='fixture:1', name='summary', prompt='Summarize supplied text.',
                        telemetry_path=Path(directory) / 'telemetry.json',
                        source_env={'HOME': '/fixture', 'PATH': '/bin'}, run_command=host,
                    )
            self.assertEqual(caught.exception.kind, 'decision_needed')
            self.assertEqual(calls, [])
            self.assertFalse((Path(directory) / 'admission').exists())
            self.assertFalse((Path(directory) / 'telemetry.json').exists())

    def test_changed_coordinator_decision_is_rejected_before_effects(self):
        policy = policy_record(load_json(local_lane.POLICY_PATH))
        calls = []

        def host(argv, *, env=None):
            calls.append(argv)
            return 0, b'{"result":{}}', b''

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'ORCHESTRATOR_ADMISSION_DIR': directory + '/admission'}):
                with self.assertRaises(JobError) as caught:
                    local_lane.start_direct_local_lane(
                        policy['local_lane'], task_class='summary',
                        local_lane_scope={'network_access': False, 'credential_access': False,
                                          'credentials_in_environment': False, 'destructive_actions': False},
                        pane='fixture:1', name='summary', prompt='Summarize supplied text.',
                        telemetry_path=Path(directory) / 'telemetry.json',
                        source_env={'HOME': '/fixture', 'PATH': '/bin'}, run_command=host,
                        route_decision={'resolved': {'model': 'unapproved-model'}},
                    )
            self.assertEqual(caught.exception.kind, 'decision_needed')
            self.assertEqual(calls, [])
            self.assertFalse((Path(directory) / 'telemetry.json').exists())

    def test_incomplete_exact_discovery_has_zero_launch_effects(self):
        policy = policy_record(load_json(local_lane.POLICY_PATH))
        config = policy['local_lane']
        scope = {'network_access': False, 'credential_access': False,
                 'credentials_in_environment': False, 'destructive_actions': False}
        prompt = 'Summarize supplied text.'
        spec = local_lane.direct_route_spec(config, 'summary', scope, 'summary', prompt)
        calls = []

        def host(argv, *, env=None):
            calls.append(argv)
            return 0, b'provider model context max-out thinking images\nother unrelated 32k 4k no no\n', b''

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'ORCHESTRATOR_ADMISSION_DIR': directory + '/admission'}):
                with self.assertRaisesRegex(JobError, 'exact model unavailable'):
                    local_lane.start_direct_local_lane(
                        config, task_class='summary', local_lane_scope=scope,
                        pane='fixture:1', name='summary', prompt=prompt,
                        telemetry_path=Path(directory) / 'telemetry.json',
                        source_env={'HOME': '/fixture', 'PATH': '/bin'}, run_command=host,
                        route_decision=decision_for(spec, policy),
                    )
            self.assertEqual(calls, [['pi', '--offline', '--list-models']])
            self.assertFalse((Path(directory) / 'admission').exists())
            self.assertFalse((Path(directory) / 'telemetry.json').exists())

    def test_cli_previews_a_decision_then_consumes_it_on_start(self):
        scope = {'network_access': False, 'credential_access': False,
                 'credentials_in_environment': False, 'destructive_actions': False}
        with tempfile.TemporaryDirectory() as directory:
            prompt = Path(directory) / 'prompt.md'
            prompt.write_text('Summarize supplied text.')
            arguments = ['--task-class', 'summary', '--local-lane-scope-json', json.dumps(scope),
                         '--name', 'summary', '--prompt-file', str(prompt)]
            stdout = StringIO()
            try:
                with redirect_stdout(stdout):
                    code = local_lane.main(['preview', *arguments])
            except SystemExit as error:
                self.fail(f'local route preview must be supported: {error}')
            self.assertEqual(code, 0)
            preview = json.loads(stdout.getvalue())
            self.assertEqual(preview['capabilities'], 'unverified')
            self.assertEqual(preview['route_decision']['resolved']['runtime'], 'pi')
            decision_file = Path(directory) / 'decision.json'
            decision_file.write_text(json.dumps(preview['route_decision']))
            calls = []

            def host(argv, *, env=None):
                calls.append(argv)
                return 0, b'provider model context max-out thinking images\n', b''

            with patch('herdr_jobs.local_lane._run_host_command', host), \
                    patch('herdr_jobs.local_lane._run_readonly', side_effect=lambda argv: (0, 'active' if argv[0] == 'systemctl' else '3072', '')), \
                    redirect_stdout(StringIO()) as output:
                code = local_lane.main(['start', *arguments, '--pane', 'fixture:1',
                                        '--route-decision', str(decision_file),
                                        '--telemetry-file', directory + '/telemetry.json'])
            self.assertEqual(code, 4)
            self.assertIn('exact model unavailable', json.loads(output.getvalue())['message'])
            self.assertEqual(calls, [['pi', '--offline', '--list-models']])


if __name__ == '__main__':
    unittest.main()
