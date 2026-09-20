import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / 'skills/setup-matt-pocock-skills/scripts/local-tickets.py'
SPEC = importlib.util.spec_from_file_location('tickets', SCRIPT)
TICKETS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TICKETS)


def ticket(number, status='ready-for-agent', blockers='None', kind=None):
    typed = f'Type: {kind}\n' if kind else ''
    state = f'**Status:** {status}\n' if status else ''
    return f'# {number}: Example\n\n{typed}{state}**Blocked by:** {blockers}\n\nKeep this prose.\n'


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'feature'
        (self.root / 'issues').mkdir(parents=True)
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, number, **kwargs):
        path = self.root / f'issues/{number:02d}-example.md'
        path.write_text(ticket(number, **kwargs))
        return path

    def invoke(self, action, request=None, apply=True, extra=()):
        if apply and request is not None and '--expect-plan' not in extra:
            code, preview = self.invoke(action, request, apply=False, extra=extra)
            self.assertEqual(code, 0, preview)
            extra = tuple(extra) + ('--expect-plan', preview['plan_hash'])
        argv = [sys.executable, str(SCRIPT), action, '--feature-root', str(self.root)]
        if request:
            self.counter += 1
            source = self.root.parent / f'request-{self.counter}.json'
            source.write_text(json.dumps(request))
            argv += ['--request', str(source)]
        if apply:
            argv.append('--apply')
        result = subprocess.run(argv + list(extra), capture_output=True, text=True)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def claim(self, number=1, operation='claim-one', owner='alice'):
        code, result = self.invoke('claim', {'operation_id': operation, 'ticket': number, 'owner': owner})
        self.assertEqual(code, 0, result)
        return result['claim_id']

    def test_frontier_lifecycles_and_cancelled_dependency(self):
        self.add(1, status='wontfix')
        self.add(2, blockers='01: Cancelled prerequisite; 03: Completed work.')
        self.add(3, status='done')
        self.add(4, kind='research', status=None)
        self.add(5, kind='task', status='claimed')
        self.add(6, status='ready-for-human')
        code, result = self.invoke('frontier', apply=False)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['frontier'], [4])
        self.assertEqual(result['tickets'][1]['waiting_for'], [1])
        self.assertIn('legacy_claim_without_owner', result['tickets'][4]['ineligible_reasons'])
        self.assertFalse((self.root / '.ticket-operations').exists())

    def test_invalid_graph_reports_witness(self):
        self.add(1, blockers='2')
        self.add(2, blockers='1')
        code, result = self.invoke('validate')
        self.assertEqual(code, 2)
        self.assertEqual(result['cycle'], [1, 2, 1])

    def test_missing_self_duplicate_fields_and_ids(self):
        first = self.add(1, blockers='9')
        self.assertEqual(self.invoke('validate')[0], 2)
        first.write_text(ticket(1, blockers='1'))
        self.assertEqual(self.invoke('validate')[0], 2)
        first.write_text(ticket(1) + 'Status: done\n')
        self.assertEqual(self.invoke('validate')[0], 2)
        first.write_text(ticket(1))
        (self.root / 'issues/001-duplicate.md').write_text(ticket(1))
        self.assertEqual(self.invoke('validate')[0], 2)

    def test_fenced_example_is_not_metadata(self):
        path = self.add(1)
        path.write_text(path.read_text() + '\n```\nStatus: example\n```\n## Comments\nStatus: commentary\n')
        self.assertEqual(self.invoke('validate')[0], 0)

    def test_preview_no_effect_and_changed_plan(self):
        path = self.add(1)
        request = {'operation_id': 'one', 'ticket': 1, 'owner': 'alice'}
        code, preview = self.invoke('claim', request, apply=False)
        self.assertEqual(code, 0, preview)
        self.assertFalse((self.root / '.ticket-operations').exists())
        path.write_text(path.read_text() + 'Another edit.\n')
        code, result = self.invoke('claim', request, extra=['--expect-plan', preview['plan_hash']])
        self.assertEqual(code, 3, result)
        self.assertNotIn('Claimed by:', path.read_text())

    def test_claim_reassignment_stale_owner_and_completion(self):
        path = self.add(1)
        old = self.claim()
        code, result = self.invoke('reassign', {'operation_id': 'transfer', 'ticket': 1,
             'owner': 'alice', 'claim_id': old, 'new_owner': 'bob', 'reason': 'Explicit reassignment'})
        self.assertEqual(code, 0, result)
        completion = {'operation_id': 'finish', 'ticket': 1, 'owner': 'alice', 'claim_id': old,
                      'evidence': {'ticket': 1, 'accepted': True, 'summary': 'Verified behavior', 'checks': ['Scenario passed']}}
        self.assertEqual(self.invoke('complete', completion, apply=False)[0], 3)
        completion.update(owner='bob', claim_id=result['claim_id'])
        self.assertEqual(self.invoke('complete', completion)[0], 0)
        content = path.read_text()
        self.assertIn('**Status:** done', content)
        self.assertNotIn('Claimed by:', content)
        self.assertIn('Keep this prose.', content)
        code, repeat = self.invoke('complete', completion)
        self.assertEqual(code, 0)
        self.assertEqual(repeat['outcome'], 'already_applied')
        self.assertEqual(path.read_text().count('## Verification'), 1)

    def test_explicit_release(self):
        path = self.add(1, kind='task', status=None)
        claim = self.claim()
        code, result = self.invoke('release', {'operation_id': 'release', 'ticket': 1, 'owner': 'alice',
                      'claim_id': claim, 'reason': 'Explicit stop'})
        self.assertEqual(code, 0, result)
        self.assertNotIn('Status:', path.read_text())
        self.assertEqual(self.invoke('frontier')[1]['frontier'], [1])

    def test_publication_dependencies_repeat_and_conflict(self):
        request = {'operation_id': 'publish', 'approval': 'Approved ticket graph', 'tickets': [
            {'filename': '02-second.md', 'body': ticket(2, blockers='1')},
            {'filename': '01-first.md', 'body': ticket(1)}]}
        code, result = self.invoke('publish', request)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['paths'], ['issues/01-first.md', 'issues/02-second.md'])
        self.assertEqual(self.invoke('publish', request)[1]['outcome'], 'already_applied')
        request['approval'] = 'Different request'
        self.assertEqual(self.invoke('publish', request, apply=False)[0], 3)
        self.assertEqual(len(list((self.root / 'issues').iterdir())), 2)

    def test_concurrent_claimants(self):
        self.add(1)
        processes = []
        plans = []
        for owner in ['alice', 'bob']:
            source = self.root.parent / f'{owner}.json'
            source.write_text(json.dumps({'operation_id': owner, 'ticket': 1, 'owner': owner}))
            code, preview = self.invoke('claim', json.loads(source.read_text()), apply=False)
            self.assertEqual(code, 0, preview)
            plans.append(preview['plan_hash'])
        for owner, plan in zip(['alice', 'bob'], plans):
            source = self.root.parent / f'{owner}.json'
            processes.append(subprocess.Popen([sys.executable, str(SCRIPT), 'claim', '--feature-root', str(self.root),
                                               '--request', str(source), '--apply', '--expect-plan', plan], stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        results = []
        for process in processes:
            out, err = process.communicate(timeout=10)
            results.append(process.returncode)
            self.assertTrue(out, err)
        self.assertEqual(sorted(results), [0, 3])

    def prepare_resolution(self):
        self.add(1, kind='grilling', status=None)
        (self.root / 'map.md').write_text('## Destination\n\nAn answer\n\n## Decisions so far\n\n## Not yet specified\n\nKeep fog.\n')
        claim = self.claim()
        request = {'operation_id': 'resolve', 'ticket': 1, 'owner': 'alice', 'claim_id': claim,
                   'answer': 'The maintainer selected option A.', 'gist': 'Select option A',
                   'evidence': {'ticket': 1, 'accepted': True, 'summary': 'Explicit maintainer answer'}}
        plan = TICKETS.make_plan('resolve', request, TICKETS.snapshot(self.root), TICKETS.DEFAULT_LABELS)
        record = {'plan': plan, 'plan_hash': TICKETS.digest(TICKETS.encoded(plan)), 'state': 'pending'}
        return request, record

    def test_interrupt_each_mutation_and_resume_public_interface(self):
        for stop_after in [1, 2, 3]:
            with self.subTest(stop_after=stop_after):
                if stop_after > 1:
                    self.tearDown()
                    self.setUp()
                request, record = self.prepare_resolution()
                real_write = TICKETS.atomic_write
                writes = 0

                def interrupted(path, data):
                    nonlocal writes
                    real_write(path, data)
                    writes += 1
                    if writes == stop_after:
                        raise OSError('Injected interruption after durable write')

                with patch.object(TICKETS, 'atomic_write', interrupted):
                    with self.assertRaises((TICKETS.Failure, OSError)):
                        TICKETS.execute(self.root, record, self.root / '.ticket-operations/resolve.json', True)
                self.assertEqual(self.invoke('frontier')[0], 4)
                code, result = self.invoke('resume', extra=['--operation-id', 'resolve'])
                self.assertEqual(code, 0, result)
                content = (self.root / 'issues/01-example.md').read_text()
                self.assertIn('Status: resolved', content)
                self.assertEqual(content.count('## Answer'), 1)
                self.assertEqual((self.root / 'map.md').read_text().count('[1: Select option A]'), 1)
                self.assertEqual(self.invoke('resolve', request)[1]['outcome'], 'already_applied')

    def test_resume_rejects_independent_edit_and_stale_claim(self):
        _, record = self.prepare_resolution()
        journal = self.root / '.ticket-operations/resolve.json'
        TICKETS.atomic_write(journal, TICKETS.encoded(record))
        issue = self.root / 'issues/01-example.md'
        original = issue.read_text()
        issue.write_text(original.replace('alice', 'bob'))
        code, result = self.invoke('resume', extra=['--operation-id', 'resolve'])
        self.assertEqual(code, 3, result)
        self.assertNotIn('## Answer', issue.read_text())
        issue.write_text(original)
        self.assertEqual(self.invoke('resume', extra=['--operation-id', 'resolve'])[0], 0)

    def test_custom_labels(self):
        self.add(1, status='queued')
        labels = dict(TICKETS.DEFAULT_LABELS, **{'ready-for-agent': 'queued'})
        file = self.root.parent / 'labels.json'
        file.write_text(json.dumps(labels))
        self.assertEqual(self.invoke('frontier', extra=['--labels', str(file)])[1]['frontier'], [1])

    def test_malformed_request_and_journal(self):
        self.add(1)
        self.assertEqual(self.invoke('claim', {'operation_id': 'bad', 'ticket': '1', 'owner': 'x'}, apply=False)[0], 2)
        self.claim()
        journal = self.root / '.ticket-operations/claim-one.json'
        journal.write_text('{"plan":"corrupt","state":"pending","plan_hash":"x"}')
        self.assertEqual(self.invoke('frontier')[0], 2)

    def test_symlink_scope(self):
        external = self.root.parent / 'outside.md'
        external.write_text(ticket(1))
        (self.root / 'issues/01-link.md').symlink_to(external)
        self.assertNotEqual(self.invoke('validate')[0], 0)
        self.assertEqual(external.read_text(), ticket(1))


if __name__ == '__main__':
    unittest.main()
