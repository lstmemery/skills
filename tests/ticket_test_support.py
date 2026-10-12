"""Fixtures for exercising planning and execution without importing the CLI."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'skills/setup-matt-pocock-skills/scripts'))


def ticket(number, status='ready-for-agent', blockers='None', kind=None):
    typed = f'Type: {kind}\n' if kind else ''
    state = f'**Status:** {status}\n' if status else ''
    return f'# {number}: Example\n\n{typed}{state}**Blocked by:** {blockers}\n\nKeep this prose.\n'


def claimed(number=1, kind=None, blockers='None'):
    state = 'claimed' if kind else 'ready-for-agent'
    return (ticket(number, kind=kind, status=state, blockers=blockers) +
            'Claimed by: alice\nClaim ID: old-claim\n').encode()


def scenarios():
    yield 'publish', {}, {'operation_id': 'publish', 'approval': 'Accepted graph', 'tickets': [
        {'filename': '01-example.md', 'body': ticket(1, blockers='3')},
        {'filename': '02-example.md', 'body': ticket(2, blockers='1')},
        {'filename': '03-example.md', 'body': ticket(3)}]}
    yield 'claim', {'issues/01-example.md': ticket(1).encode()}, {
        'operation_id': 'claim', 'ticket': 1, 'owner': 'alice'}
    for action in ('release', 'reassign', 'resolve', 'complete'):
        before = {'issues/01-example.md': claimed(kind='research' if action == 'resolve' else None)}
        request = {'operation_id': action, 'ticket': 1, 'owner': 'alice', 'claim_id': 'old-claim'}
        if action in ('release', 'reassign'):
            request['reason'] = 'Explicit handoff'
        if action == 'reassign':
            request['new_owner'] = 'bob'
        if action in ('resolve', 'complete'):
            request['evidence'] = {'ticket': 1, 'accepted': True, 'summary': 'Accepted', 'checks': ['Checked']}
        if action == 'resolve':
            before['map.md'] = b'## Decisions so far\n\n## Not yet specified\n\nKeep fog.\n'
            request.update(answer='Choose option A.', gist='Select option A')
        yield action, before, request
