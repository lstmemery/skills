import unittest

from ticket_test_support import claimed, scenarios, ticket
from ticket_planning import (
    DEFAULT_LABELS, Failure, OperationPlanner, TicketGraph, digest, encoded, unpack,
)


class PlannerTests(unittest.TestCase):
    def test_claim_is_pure_and_preserves_durable_identity(self):
        before = {'issues/01-example.md': ticket(1).encode()}
        request = {'operation_id': 'claim-one', 'ticket': 1, 'owner': 'alice'}
        plan = OperationPlanner().plan('claim', request, before, DEFAULT_LABELS)
        self.assertEqual(plan['claim_id'], '040847bb342668f2250f14a6498deb4e')
        self.assertEqual(digest(encoded(plan)),
                         'c452cb46df1485cd937cef79700f4f7a967a4ed5f50f9d89244f7ef4aa12c1e8')
        self.assertEqual(before['issues/01-example.md'], ticket(1).encode())
        self.assertEqual(plan['changes'][0]['path'], 'issues/01-example.md')
        after = unpack(plan['changes'][0]['after']).decode()
        self.assertIn('Claimed by: alice', after)
        self.assertIn('Keep this prose.', after)

    def test_publish_orders_new_blockers_before_dependents(self):
        request = {'operation_id': 'publish', 'approval': 'Accepted graph', 'tickets': [
            {'filename': '01-example.md', 'body': ticket(1, blockers='3')},
            {'filename': '02-example.md', 'body': ticket(2, blockers='1')},
            {'filename': '03-example.md', 'body': ticket(3)}]}
        plan = OperationPlanner().plan('publish', request, {}, DEFAULT_LABELS)
        self.assertEqual([c['path'] for c in plan['changes']],
                         ['issues/03-example.md', 'issues/01-example.md', 'issues/02-example.md'])
        self.assertTrue(all(c['before'] is None for c in plan['changes']))
        planned = {c['path']: unpack(c['after']) for c in plan['changes']}
        self.assertEqual(TicketGraph(planned, DEFAULT_LABELS).frontier, [3])

    def test_publication_rejects_existing_destination_and_closed_drafts(self):
        request = {'operation_id': 'publish', 'approval': 'Accepted graph', 'tickets': [
            {'filename': '01-example.md', 'body': ticket(1)}]}
        with self.assertRaises(Failure) as caught:
            OperationPlanner().plan('publish', request, {'issues/01-example.md': ticket(1).encode()}, DEFAULT_LABELS)
        self.assertEqual(caught.exception.result['outcome'], 'conflict')
        for draft in (ticket(1, status='done'), ticket(1, kind='research', status='resolved')):
            with self.subTest(draft=draft):
                request['tickets'][0]['body'] = draft
                with self.assertRaises(Failure) as caught:
                    OperationPlanner().plan('publish', request, {}, DEFAULT_LABELS)
                self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_release_reopens_owned_implementation_and_wayfinding_tickets(self):
        for kind in (None, 'research', 'prototype', 'grilling', 'task'):
            with self.subTest(kind=kind):
                before = {'issues/01-example.md': claimed(kind=kind)}
                request = {'operation_id': 'release', 'ticket': 1, 'owner': 'alice',
                           'claim_id': 'old-claim', 'reason': 'Explicit stop'}
                plan = OperationPlanner().plan('release', request, before, DEFAULT_LABELS)
                after = unpack(plan['changes'][0]['after'])
                parsed = TicketGraph({'issues/01-example.md': after}, DEFAULT_LABELS)
                self.assertEqual(parsed.frontier, [1])
                self.assertIsNone(parsed.tickets[1]['owner'])
                self.assertEqual(parsed.tickets[1]['status'], 'ready-for-agent' if kind is None else None)
                self.assertIn(b'Keep this prose.', after)

    def test_reassign_rotates_claim_and_rejects_stale_owner(self):
        before = {'issues/01-example.md': claimed()}
        request = {'operation_id': 'reassign', 'ticket': 1, 'owner': 'alice',
                   'claim_id': 'old-claim', 'new_owner': 'bob', 'reason': 'Explicit handoff'}
        planner = OperationPlanner()
        plan = planner.plan('reassign', request, before, DEFAULT_LABELS)
        after = {'issues/01-example.md': unpack(plan['changes'][0]['after'])}
        parsed = TicketGraph(after, DEFAULT_LABELS)
        self.assertEqual(parsed.tickets[1]['owner'], 'bob')
        self.assertEqual(parsed.tickets[1]['claim_id'], plan['claim_id'])
        self.assertNotEqual(plan['claim_id'], 'old-claim')
        self.assertEqual(plan, planner.plan('reassign', request, before, DEFAULT_LABELS))
        stale = {'operation_id': 'release', 'ticket': 1, 'owner': 'alice',
                 'claim_id': 'old-claim', 'reason': 'Stop'}
        with self.assertRaises(Failure) as caught:
            planner.plan('release', stale, after, DEFAULT_LABELS)
        self.assertEqual(caught.exception.result['outcome'], 'conflict')

    def test_resolve_plans_accepted_answer_and_map_decision(self):
        mapping = b'## Destination\n\nAnswer\n\n## Decisions so far\n\n## Not yet specified\n\nKeep fog.\n'
        before = {'issues/01-example.md': claimed(kind='research'), 'map.md': mapping}
        request = {'operation_id': 'resolve', 'ticket': 1, 'owner': 'alice', 'claim_id': 'old-claim',
                   'answer': 'Choose option A.', 'gist': 'Use [A]',
                   'evidence': {'ticket': 1, 'accepted': True, 'summary': 'Maintainer accepted'}}
        plan = OperationPlanner().plan('resolve', request, before, DEFAULT_LABELS)
        self.assertEqual([c['path'] for c in plan['changes']], ['issues/01-example.md', 'map.md'])
        after = {c['path']: unpack(c['after']) for c in plan['changes']}
        self.assertEqual(TicketGraph(after, DEFAULT_LABELS).tickets[1]['status'], 'resolved')
        self.assertNotIn(b'Claimed by:', after['issues/01-example.md'])
        self.assertEqual(after['issues/01-example.md'].count(b'## Answer'), 1)
        self.assertIn(b'Choose option A.', after['issues/01-example.md'])
        self.assertIn(br'- [1: Use \[A\]](issues/01-example.md)', after['map.md'])
        self.assertIn(b'Keep fog.', after['map.md'])
        self.assertEqual(before['map.md'], mapping)
        for invalid_map in (None, b'## Destination\n', b'## Decisions so far\n## Decisions-so-far\n'):
            with self.subTest(mapping=invalid_map):
                invalid = dict(before)
                if invalid_map is None:
                    del invalid['map.md']
                else:
                    invalid['map.md'] = invalid_map
                with self.assertRaises(Failure) as caught:
                    OperationPlanner().plan('resolve', request, invalid, DEFAULT_LABELS)
                self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_complete_requires_checked_evidence_and_plans_done_status(self):
        before = {'issues/01-example.md': claimed(blockers='2'),
                  'issues/02-example.md': ticket(2, status='done').encode()}
        request = {'operation_id': 'complete', 'ticket': 1, 'owner': 'alice', 'claim_id': 'old-claim',
                   'evidence': {'ticket': 1, 'accepted': True, 'summary': 'Accepted work', 'checks': ['Scenario passed']}}
        plan = OperationPlanner().plan('complete', request, before, DEFAULT_LABELS)
        after = unpack(plan['changes'][0]['after'])
        parsed = TicketGraph({**before, 'issues/01-example.md': after}, DEFAULT_LABELS)
        self.assertTrue(parsed.tickets[1]['successful'])
        self.assertEqual(parsed.tickets[1]['status'], 'done')
        self.assertIsNone(parsed.tickets[1]['owner'])
        self.assertEqual(after.count(b'## Verification'), 1)
        self.assertIn(b'Scenario passed', after)
        self.assertIn('issues/02-example.md', plan['guards'])
        for checks in (None, [], ['']):
            with self.subTest(checks=checks):
                invalid = {**request, 'evidence': {**request['evidence'], 'checks': checks}}
                with self.assertRaises(Failure) as caught:
                    OperationPlanner().plan('complete', invalid, before, DEFAULT_LABELS)
                self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_finish_operations_require_matching_lifecycle_and_satisfied_dependencies(self):
        for action, kind in (('resolve', 'research'), ('complete', None)):
            request = {'operation_id': action, 'ticket': 1, 'owner': 'alice', 'claim_id': 'old-claim',
                       'evidence': {'ticket': 1, 'accepted': True, 'summary': 'Accepted', 'checks': ['Checked']}}
            if action == 'resolve':
                request.update(answer='An answer.', gist='A decision')
            for status in ('ready-for-agent', 'wontfix'):
                with self.subTest(action=action, status=status):
                    before = {'issues/01-example.md': claimed(kind=kind, blockers='2'),
                              'issues/02-example.md': ticket(2, status=status).encode(),
                              'map.md': b'## Decisions so far\n'}
                    with self.assertRaises(Failure) as caught:
                        OperationPlanner().plan(action, request, before, DEFAULT_LABELS)
                    self.assertEqual(caught.exception.result['outcome'], 'conflict')
                    self.assertEqual(caught.exception.result['ticket']['waiting_for'], [2])
            wrong_kind = None if kind else 'research'
            before = {'issues/01-example.md': claimed(kind=wrong_kind), 'map.md': b'## Decisions so far\n'}
            with self.assertRaises(Failure) as caught:
                OperationPlanner().plan(action, request, before, DEFAULT_LABELS)
            self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_plan_retains_its_identity_when_caller_inputs_are_mutated(self):
        request = {'operation_id': 'publish', 'approval': 'Accepted', 'tickets': [
            {'filename': '01-example.md', 'body': ticket(1)}]}
        labels = dict(DEFAULT_LABELS)
        plan = OperationPlanner().plan('publish', request, {}, labels)
        original_hash = digest(encoded(plan))
        request['tickets'][0]['body'] = ticket(1, status='done')
        labels['done'] = 'finished'
        self.assertEqual(digest(encoded(plan)), original_hash)

    def test_claim_wayfinding_and_custom_implementation_labels(self):
        labels = dict(DEFAULT_LABELS, **{'ready-for-agent': 'queued', 'done': 'finished'})
        before = {'issues/01-example.md': ticket(1, kind='prototype', status=None).encode(),
                  'issues/02-example.md': ticket(2, status='queued').encode()}
        for number, state in ((1, 'claimed'), (2, 'queued')):
            with self.subTest(ticket=number):
                request = {'operation_id': f'claim-{number}', 'ticket': number, 'owner': 'alice'}
                plan = OperationPlanner().plan('claim', request, before, labels)
                after = dict(before)
                after.update({c['path']: unpack(c['after']) for c in plan['changes']})
                parsed = TicketGraph(after, labels)
                self.assertEqual(parsed.tickets[number]['status'], state)
                self.assertEqual(parsed.tickets[number]['owner'], 'alice')
                self.assertNotIn(number, parsed.frontier)

    def test_request_validation_is_part_of_in_process_planning(self):
        before = {'issues/01-example.md': ticket(1).encode()}
        for action, request in (
            ('unknown', {}),
            ('claim', {'operation_id': 'one', 'ticket': '1', 'owner': 'alice'}),
            ('claim', {'operation_id': '../one', 'ticket': 1, 'owner': 'alice'}),
            ('claim', {'operation_id': 'one', 'ticket': 1, 'owner': 'alice', 'extra': True}),
        ):
            with self.subTest(action=action, request=request):
                with self.assertRaises(Failure) as caught:
                    OperationPlanner().plan(action, request, before, DEFAULT_LABELS)
                self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_version_one_plan_hashes_match_the_bundled_snapshot_for_every_operation(self):
        # Literals captured from the original bundled implementation.
        expected = {
            'publish': 'df5d56c7089c5dd4ba5c626470693b6d75d0dac8640e2c14c692763b167fb366',
            'claim': '2e683905dbc16af5e5918a051d864e14767c4aa179fae425f44bf626d444ed55',
            'release': 'a8461766cfb774597c7ec0773c3d6f8b7aa1e5b1954c1168bfbc9353cacdda27',
            'reassign': 'a9571a33a1eae0ab900712c72e26673b5a21374ca0d8c33083398fe10f0dcdfd',
            'resolve': 'f8222fda7bafc887799267206fbf22bd24fa904157f3432db1f72a6258484b9d',
            'complete': '42ea91557e9da761491483b1bc77c78785d246361251c15dd30308aee4177c6e',
        }
        for action, before, request in scenarios():
            with self.subTest(action=action):
                plan = OperationPlanner().plan(action, request, before, DEFAULT_LABELS)
                self.assertEqual(digest(encoded(plan)), expected[action])


class GraphTests(unittest.TestCase):
    def test_cycle_reports_a_dependency_witness(self):
        before = {f'issues/0{n}-example.md': ticket(n, blockers=str(blocker)).encode()
                  for n, blocker in ((1, 2), (2, 3), (3, 1))}
        with self.assertRaises(Failure) as caught:
            TicketGraph(before, DEFAULT_LABELS)
        self.assertEqual(caught.exception.result['cycle'], [1, 2, 3, 1])
        self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_frontier_requires_successful_dependencies_and_open_unclaimed_lifecycle(self):
        before = {
            'issues/01-example.md': ticket(1, status='done').encode(),
            'issues/02-example.md': ticket(2, status='wontfix').encode(),
            'issues/03-example.md': ticket(3, blockers='1, 6').encode(),
            'issues/04-example.md': ticket(4, blockers='2').encode(),
            'issues/05-example.md': ticket(5, kind='research', status=None).encode(),
            'issues/06-example.md': ticket(6, kind='task', status='resolved').encode(),
            'issues/07-example.md': ticket(7, kind='task', status='claimed').encode(),
            'issues/08-example.md': claimed(8),
            'issues/09-example.md': ticket(9, status='ready-for-human').encode(),
        }
        parsed = TicketGraph(before, DEFAULT_LABELS)
        self.assertEqual(parsed.frontier, [3, 5])
        self.assertEqual(parsed.tickets[3]['waiting_for'], [])
        self.assertEqual(parsed.tickets[4]['waiting_for'], [2])
        self.assertIn('unsatisfied_dependencies', parsed.tickets[4]['ineligible_reasons'])
        self.assertIn('terminal', parsed.tickets[2]['ineligible_reasons'])
        self.assertIn('legacy_claim_without_owner', parsed.tickets[7]['ineligible_reasons'])
        self.assertIn('claimed', parsed.tickets[8]['ineligible_reasons'])
        self.assertIn('not_ready_for_agent', parsed.tickets[9]['ineligible_reasons'])
        with self.assertRaises(Failure) as caught:
            OperationPlanner().plan('claim', {'operation_id': 'blocked', 'ticket': 4, 'owner': 'alice'},
                                    before, DEFAULT_LABELS)
        self.assertEqual(caught.exception.result['outcome'], 'conflict')

    def test_diamond_graph_and_empty_frontier(self):
        before = {f'issues/0{n}-example.md': ticket(n, blockers=edges).encode()
                  for n, edges in ((1, '3, 2'), (2, '4'), (3, '4'), (4, 'None'))}
        parsed = TicketGraph(before, DEFAULT_LABELS)
        self.assertEqual(parsed.frontier, [4])
        self.assertEqual(parsed.ordered_paths(), ['issues/04-example.md', 'issues/03-example.md',
                                                'issues/02-example.md', 'issues/01-example.md', 'map.md'])
        self.assertEqual(TicketGraph({}, DEFAULT_LABELS).frontier, [])
        self.assertEqual(TicketGraph({'issues/01-example.md': ticket(1, status='done').encode()},
                                     DEFAULT_LABELS).frontier, [])

    def test_missing_self_duplicate_and_nonpositive_blockers_are_rejected(self):
        for blockers in ('9', '1', '2, 2', '0'):
            with self.subTest(blockers=blockers):
                before = {'issues/01-example.md': ticket(1, blockers=blockers).encode(),
                          'issues/02-example.md': ticket(2).encode()}
                with self.assertRaises(Failure) as caught:
                    TicketGraph(before, DEFAULT_LABELS)
                self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_duplicate_ticket_ids_and_claim_contradictions_are_rejected(self):
        before = {'issues/01-example.md': ticket(1).encode(),
                  'issues/001-duplicate.md': ticket(1).encode()}
        with self.assertRaises(Failure) as caught:
            TicketGraph(before, DEFAULT_LABELS)
        self.assertEqual(caught.exception.result['ticket'], 1)
        for body in (ticket(1) + 'Claimed by: alice\n',
                     ticket(1, status='done') + 'Claimed by: alice\nClaim ID: old\n',
                     ticket(1, kind='task', status=None) + 'Claimed by: alice\nClaim ID: old\n'):
            with self.subTest(body=body):
                with self.assertRaises(Failure) as caught:
                    TicketGraph({'issues/01-example.md': body.encode()}, DEFAULT_LABELS)
                self.assertEqual(caught.exception.result['outcome'], 'invalid_input')

    def test_invalid_snapshot_issue_paths_fail_as_input_errors(self):
        for path in ('issues/not-a-ticket.md', 'issues/nested/01-example.md'):
            with self.subTest(path=path):
                with self.assertRaises(Failure) as caught:
                    TicketGraph({path: ticket(1).encode()}, DEFAULT_LABELS)
                self.assertEqual(caught.exception.result['outcome'], 'invalid_input')


if __name__ == '__main__':
    unittest.main()
