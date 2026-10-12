"""Pure Markdown ticket graph rules and durable, JSON-compatible plans.

Planning consumes bytes and labels only; it never reads or mutates a filesystem.
Plan version 1, its canonical JSON hash, and operation IDs are the wire contract.
"""
from __future__ import annotations

from copy import deepcopy
import base64
import difflib
import hashlib
import json
import re
from typing import TypedDict


VERSION = 1


MAX_FILE = 2 * 1024 * 1024


MAX_REQUEST = 16 * 1024 * 1024


DEFAULT_LABELS = {name: name for name in (
    'needs-triage', 'needs-info', 'ready-for-agent', 'ready-for-human', 'wontfix', 'done')}


TYPES = {'research', 'prototype', 'grilling', 'task'}


FIELD = re.compile(r'^(?:\*\*)?(Status|Type|Blocked by|Claimed by|Claim ID):(?:\*\*)?\s*(.*?)\s*$')


NAME = re.compile(r'(\d+)-[a-z0-9][a-z0-9-]*\.md\Z')


OP_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')


class Failure(ValueError):
    def __init__(self, outcome, message, **details):
        super().__init__(message)
        self.result = {'outcome': outcome, 'message': message, **details}


def fail(message, **details):
    raise Failure('invalid_input', message, **details)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('Duplicate JSON key', key=key)
        result[key] = value
    return result


def text_value(value, name, limit=1000000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        fail('Expected nonempty bounded text', field=name)
    return value


def one_line(value, name):
    value = text_value(value, name, 500)
    if '\n' in value or '\r' in value:
        fail('Expected one line', field=name)
    return value


def snap_hash(contents):
    return digest(encoded({name: digest(body) for name, body in contents.items()}))


def fields(body, path):
    try:
        content = body.decode('utf-8')
    except UnicodeDecodeError:
        fail('Ticket is not UTF-8', path=path)
    found = {}
    fence = None
    for index, line in enumerate(content.splitlines(keepends=True)):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif fence == marker[1][0]:
                fence = None
            continue
        if fence is not None:
            continue
        if line.startswith('## '):
            break
        match = FIELD.fullmatch(line.rstrip('\r\n'))
        if match:
            key, value = match.groups()
            if key in found:
                fail('Duplicate ticket field', path=path, field=key, line=index + 1)
            found[key] = (value, index)
    return content, found


def blockers(value, path):
    if value is None or re.fullmatch(r'None(?: \(can start immediately\))?\.?', value, re.I):
        return []
    if re.fullmatch(r'\d+(?:\s*,\s*\d+)*', value):
        ids = [int(item.strip()) for item in value.split(',')]
    elif all(re.fullmatch(r'\d+:\s*\S.*', item.strip()) for item in value.split(';')):
        ids = [int(item.strip().split(':', 1)[0]) for item in value.split(';')]
    else:
        fail('Unsupported Blocked by syntax', path=path, value=value)
    if len(set(ids)) != len(ids) or any(number < 1 for number in ids):
        fail('Duplicate or nonpositive blocker', path=path)
    return ids


def change_fields(body, path, updates):
    content, existing = fields(body, path)
    lines = content.splitlines(keepends=True)
    for key, (_, index) in sorted(existing.items(), key=lambda item: item[1][1], reverse=True):
        if key not in updates:
            continue
        value = updates[key]
        prefix = '**' if lines[index].startswith('**') else ''
        lines[index] = '' if value is None else f'{prefix}{key}:{prefix} {value}\n'
    additions = [f'{key}: {value}\n' for key, value in updates.items() if key not in existing and value is not None]
    if additions:
        insert = 1 if lines and lines[0].startswith('# ') else 0
        lines[insert:insert] = ['\n', *additions, '\n']
    return ''.join(lines).encode()


def check_evidence(value, number):
    if not isinstance(value, dict) or value.get('ticket') != number or value.get('accepted') is not True:
        fail('Evidence must identify this ticket and its accepted decision')
    text_value(value.get('summary'), 'evidence.summary')
    return value


REQUEST_FIELDS = {
    'publish': {'tickets', 'approval'}, 'claim': {'ticket', 'owner'},
    'release': {'ticket', 'claim_id', 'owner', 'reason'},
    'reassign': {'ticket', 'claim_id', 'owner', 'new_owner', 'reason'},
    'resolve': {'ticket', 'claim_id', 'owner', 'answer', 'gist', 'evidence'},
    'complete': {'ticket', 'claim_id', 'owner', 'evidence'},
}


def _validate_publish(request):
    text_value(request['approval'], 'approval')
    if not isinstance(request['tickets'], list) or not request['tickets']:
        fail('tickets must be a nonempty list')
    for draft in request['tickets']:
        if not isinstance(draft, dict) or set(draft) != {'filename', 'body'}:
            fail('Each draft needs filename and body')
        if not isinstance(draft['filename'], str) or not NAME.fullmatch(draft['filename']):
            fail('Invalid draft filename')
        text_value(draft['body'], 'body')


def _validate_claim(request):
    if type(request['ticket']) is not int or request['ticket'] < 1:
        fail('ticket must be a positive integer')
    one_line(request['owner'], 'owner')


def _validate_claimed(request):
    _validate_claim(request)
    one_line(request['claim_id'], 'claim_id')


def _validate_transfer(request):
    _validate_claimed(request)
    one_line(request['reason'], 'reason')
    one_line(request['new_owner'], 'new_owner')


def _validate_release(request):
    _validate_claimed(request)
    one_line(request['reason'], 'reason')


def _validate_resolve(request):
    _validate_claimed(request)
    check_evidence(request['evidence'], request['ticket'])
    text_value(request['answer'], 'answer')
    one_line(request['gist'], 'gist')


def _validate_complete(request):
    _validate_claimed(request)
    check_evidence(request['evidence'], request['ticket'])


REQUEST_VALIDATORS = {
    'publish': _validate_publish, 'claim': _validate_claim,
    'release': _validate_release, 'reassign': _validate_transfer,
    'resolve': _validate_resolve, 'complete': _validate_complete,
}


def parse_request(action, request):
    if action not in REQUEST_FIELDS:
        fail('Unknown mutation operation', action=action)
    required = {'operation_id'} | REQUEST_FIELDS[action]
    if not isinstance(request, dict) or set(request) != required:
        fail('Request fields do not match operation', required=sorted(required))
    if not isinstance(request['operation_id'], str) or not OP_ID.fullmatch(request['operation_id']):
        fail('Invalid operation_id')
    REQUEST_VALIDATORS[action](request)


def _ticket_context(request, tickets):
    number = request['ticket']
    if number not in tickets:
        fail('Ticket not found', ticket=number)
    return number, tickets[number], tickets[number]['path']


def _require_claim(ticket, request):
    if ticket['owner'] != request['owner'] or ticket['claim_id'] != request['claim_id']:
        raise Failure('conflict', 'Claim no longer belongs to this caller', ticket=ticket)


def _append_record(after, path, request, title, answer=''):
    evidence = json.dumps(request['evidence'], sort_keys=True, ensure_ascii=True)
    after[path] += f'\n## {title}\n\n{answer}Evidence: {evidence}\n'.encode()


def pack(data):
    return None if data is None else base64.b64encode(data).decode()


def unpack(value):
    if value is None:
        return None
    if not isinstance(value, str):
        fail('Invalid journal bytes')
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        fail('Invalid journal encoding')


def describe(plan):
    result = []
    for change in plan['changes']:
        before = unpack(change['before'])
        after = unpack(change['after'])
        diff = ''.join(difflib.unified_diff((before or b'').decode().splitlines(True),
                                          after.decode().splitlines(True),
                                          fromfile=change['path'], tofile=change['path']))
        result.append({'path': change['path'], 'before_sha256': digest(before) if before is not None else None,
                       'after_sha256': digest(after), 'diff': diff})
    return result


class Change(TypedDict):
    path: str
    before: str | None
    after: str


class Plan(TypedDict):
    version: int
    action: str
    request: dict
    labels: dict[str, str]
    input_hash: str
    guards: dict[str, str]
    changes: list[Change]
    claim_id: str | None


class TicketGraph:
    """Validated lifecycle and dependency graph of an in-memory snapshot."""

    def __init__(self, contents: dict[str, bytes], labels: dict[str, str]):
        tickets = {}
        for path, body in contents.items():
            if not path.startswith('issues/'):
                continue
            name = NAME.fullmatch(path[7:])
            if name is None:
                fail('Unexpected issue filename', path=path)
            number = int(name[1])
            if number < 1 or number in tickets:
                fail('Duplicate or nonpositive ticket ID', ticket=number)
            content, metadata = fields(body, path)
            values = {key: item[0] for key, item in metadata.items()}
            kind = values.get('Type', 'implementation')
            if 'Type' in values and kind not in TYPES:
                fail('Unknown ticket type', path=path)
            state = values.get('Status')
            if kind == 'implementation':
                if state not in labels.values():
                    fail('Unknown or missing implementation status', path=path, status=state)
            elif state not in (None, 'claimed', 'resolved'):
                fail('Invalid wayfinding status', path=path, status=state)
            owner, claim = values.get('Claimed by'), values.get('Claim ID')
            if (owner is None) != (claim is None) or (owner is not None and (not owner or not claim)):
                fail('Incomplete claim fields', path=path)
            successful = state == (labels['done'] if kind == 'implementation' else 'resolved')
            terminal = successful or (kind == 'implementation' and state == labels['wontfix'])
            if owner and (terminal or (kind != 'implementation' and state != 'claimed')):
                fail('Claim contradicts lifecycle', path=path)
            if kind == 'implementation' and owner and state != labels['ready-for-agent']:
                fail('Implementation claim requires ready-for-agent', path=path)
            tickets[number] = {'id': number, 'path': path, 'type': kind, 'status': state,
                               'owner': owner, 'claim_id': claim, 'successful': successful,
                               'terminal': terminal, 'blocked_by': blockers(values.get('Blocked by'), path),
                               'sha256': digest(body)}
        for ticket in tickets.values():
            for number in ticket['blocked_by']:
                if number == ticket['id'] or number not in tickets:
                    fail('Self-edge or missing blocker', ticket=ticket['id'], blocker=number)
        finished = set()
        visiting = []

        def visit(number):
            if number in visiting:
                fail('Dependency cycle', cycle=visiting[visiting.index(number):] + [number])
            if number in finished:
                return
            visiting.append(number)
            for blocker in tickets[number]['blocked_by']:
                visit(blocker)
            visiting.pop()
            finished.add(number)

        for number in tickets:
            visit(number)
        frontier = []
        for number, ticket in sorted(tickets.items()):
            reasons = []
            if ticket['terminal']:
                reasons.append('terminal')
            if ticket['owner']:
                reasons.append('claimed')
            if ticket['status'] == 'claimed' and not ticket['owner']:
                reasons.append('legacy_claim_without_owner')
            if ticket['type'] == 'implementation' and ticket['status'] != labels['ready-for-agent']:
                reasons.append('not_ready_for_agent')
            waiting = [item for item in ticket['blocked_by'] if not tickets[item]['successful']]
            if waiting:
                reasons.append('unsatisfied_dependencies')
            ticket['waiting_for'] = waiting
            ticket['ineligible_reasons'] = reasons
            if not reasons:
                frontier.append(number)
        self.tickets = tickets
        self.frontier = frontier

    def ordered_paths(self):
        """Blockers before dependents, with the decision map written last."""
        ordered = []
        seen = set()

        def add(number):
            if number in seen:
                return
            for blocker in self.tickets[number]['blocked_by']:
                add(blocker)
            seen.add(number)
            ordered.append(self.tickets[number]['path'])

        for number in sorted(self.tickets):
            add(number)
        ordered.append('map.md')
        return ordered


def graph(contents, labels):
    parsed = TicketGraph(contents, labels)
    return parsed.tickets, parsed.frontier


class OperationPlanner:
    """Plan one lifecycle operation without acquiring locks or writing files."""

    def plan(self, action: str, request: dict, snapshot: dict[str, bytes],
             labels: dict[str, str]) -> Plan:
        parse_request(action, request)
        request = deepcopy(request)
        labels = dict(labels)
        before = dict(snapshot)
        current = TicketGraph(before, labels)
        claim = digest(encoded([request['operation_id'], request]))[:32]
        handlers = {
            'publish': self._publish, 'claim': self._claim,
            'release': self._release, 'reassign': self._reassign,
            'resolve': self._resolve, 'complete': self._complete,
        }
        after = handlers[action](request, before, labels, current, claim)
        planned = TicketGraph(after, labels)
        changes = []
        for path in planned.ordered_paths():
            if before.get(path) != after.get(path):
                changes.append({'path': path, 'before': pack(before.get(path)), 'after': pack(after[path])})
        changed = {change['path'] for change in changes}
        guards = {path: digest(body) for path, body in before.items() if path not in changed}
        return {'version': VERSION, 'action': action, 'request': request, 'labels': labels,
                'input_hash': snap_hash(before), 'guards': guards, 'changes': changes,
                'claim_id': claim if action in ('claim', 'reassign') else None}

    def _publish(self, request, before, labels, current, claim):
        after = dict(before)
        for draft in request['tickets']:
            path = 'issues/' + draft['filename']
            if path in after:
                raise Failure('conflict', 'Publication destination exists', path=path)
            after[path] = draft['body'].encode()
        new_graph, _ = graph(after, labels)
        for number in set(new_graph) - set(current.tickets):
            ticket = new_graph[number]
            expected = labels['ready-for-agent'] if ticket['type'] == 'implementation' else None
            if ticket['status'] != expected or ticket['owner']:
                fail('Published tickets must be open and unclaimed', ticket=number)
        return after

    def _claim(self, request, before, labels, current, claim):
        number, ticket, path = _ticket_context(request, current.tickets)
        if number not in current.frontier:
            raise Failure('conflict', 'Ticket is not eligible', ticket=ticket)
        updates = {'Claimed by': request['owner'], 'Claim ID': claim}
        if ticket['type'] != 'implementation':
            updates['Status'] = 'claimed'
        after = dict(before)
        after[path] = change_fields(before[path], path, updates)
        return after

    def _release(self, request, before, labels, current, claim):
        _, ticket, path = _ticket_context(request, current.tickets)
        _require_claim(ticket, request)
        updates = {'Claimed by': None, 'Claim ID': None}
        if ticket['type'] != 'implementation':
            updates['Status'] = None
        after = dict(before)
        after[path] = change_fields(before[path], path, updates)
        return after

    def _reassign(self, request, before, labels, current, claim):
        _, ticket, path = _ticket_context(request, current.tickets)
        _require_claim(ticket, request)
        after = dict(before)
        after[path] = change_fields(before[path], path, {'Claimed by': request['new_owner'], 'Claim ID': claim})
        return after

    def _resolve(self, request, before, labels, current, claim):
        number, ticket, path = _ticket_context(request, current.tickets)
        _require_claim(ticket, request)
        if ticket['type'] == 'implementation':
            fail('resolve requires a wayfinding ticket')
        if ticket['waiting_for']:
            raise Failure('conflict', 'Dependencies are not satisfied', ticket=ticket)
        after = dict(before)
        after[path] = change_fields(before[path], path, {'Claimed by': None, 'Claim ID': None, 'Status': 'resolved'})
        _append_record(after, path, request, 'Answer', request['answer'] + '\n\n')
        if 'map.md' not in after:
            fail('Wayfinding resolution requires map.md')
        mapping = after['map.md'].decode('utf-8')
        matches = list(re.finditer(r'^## Decisions(?: so far|-so-far)\s*$', mapping, re.M))
        if len(matches) != 1:
            fail('map.md requires one Decisions so far heading')
        end = matches[0].end()
        gist = request['gist'].replace('[', '\\[').replace(']', '\\]')
        after['map.md'] = (mapping[:end] + f'\n\n- [{number}: {gist}]({path})\n' + mapping[end:]).encode()
        return after

    def _complete(self, request, before, labels, current, claim):
        _, ticket, path = _ticket_context(request, current.tickets)
        _require_claim(ticket, request)
        if ticket['type'] != 'implementation':
            fail('complete requires an implementation ticket')
        if ticket['waiting_for']:
            raise Failure('conflict', 'Dependencies are not satisfied', ticket=ticket)
        checks = request['evidence'].get('checks')
        if not isinstance(checks, list) or not checks:
            fail('Completion evidence requires checks')
        for check in checks:
            text_value(check, 'check')
        after = dict(before)
        after[path] = change_fields(before[path], path, {'Claimed by': None, 'Claim ID': None, 'Status': labels['done']})
        _append_record(after, path, request, 'Verification')
        return after


def make_plan(action, request, before, labels):
    return OperationPlanner().plan(action, request, before, labels)
