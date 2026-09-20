#!/usr/bin/env python3
"""Local Markdown ticket operations with cooperative locking and recovery."""
import argparse
import base64
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

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


def read_json(path, limit=MAX_REQUEST):
    data = read_regular(path, limit)
    try:
        return json.loads(data, object_pairs_hook=unique_object)
    except (ValueError, UnicodeDecodeError) as error:
        if isinstance(error, Failure):
            raise
        fail('Invalid JSON', path=str(path), detail=str(error))


def read_regular(path, limit=MAX_FILE):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            fail('Expected bounded regular file', path=str(path), limit=limit)
        data = stream.read(limit + 1)
        if len(data) > limit:
            fail('File exceeds size limit', path=str(path))
        return data


def text_value(value, name, limit=1000000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        fail('Expected nonempty bounded text', field=name)
    return value


def one_line(value, name):
    value = text_value(value, name, 500)
    if '\n' in value or '\r' in value:
        fail('Expected one line', field=name)
    return value


def safe_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        fail('Invalid relative path', path=relative)
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            fail('Symlink in managed path', path=relative)
    return current


def snapshot(root):
    result = {}
    directory = safe_path(root, 'issues')
    if directory.exists():
        if not directory.is_dir():
            fail('issues must be a directory')
        for path in sorted(directory.iterdir()):
            if path.name.startswith('.'):
                continue
            if not NAME.fullmatch(path.name):
                fail('Unexpected issue filename', path=str(path))
            relative = f'issues/{path.name}'
            result[relative] = read_regular(safe_path(root, relative))
    map_path = safe_path(root, 'map.md')
    if map_path.exists():
        result['map.md'] = read_regular(map_path)
    if len(result) > 512 or sum(map(len, result.values())) > 32 * 1024 * 1024:
        fail('Feature exceeds supported snapshot bounds')
    return result


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


def graph(contents, labels):
    tickets = {}
    for path, body in contents.items():
        if not path.startswith('issues/'):
            continue
        number = int(NAME.fullmatch(Path(path).name)[1])
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
    return tickets, frontier


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


def _plan_publish(request, before, labels, tickets, frontier, claim):
    after = dict(before)
    for draft in request['tickets']:
        path = 'issues/' + draft['filename']
        if path in after:
            raise Failure('conflict', 'Publication destination exists', path=path)
        after[path] = draft['body'].encode()
    new_graph, _ = graph(after, labels)
    for number in set(new_graph) - set(tickets):
        ticket = new_graph[number]
        expected = labels['ready-for-agent'] if ticket['type'] == 'implementation' else None
        if ticket['status'] != expected or ticket['owner']:
            fail('Published tickets must be open and unclaimed', ticket=number)
    return after


def _plan_claim(request, before, labels, tickets, frontier, claim):
    number, ticket, path = _ticket_context(request, tickets)
    if number not in frontier:
        raise Failure('conflict', 'Ticket is not eligible', ticket=ticket)
    updates = {'Claimed by': request['owner'], 'Claim ID': claim}
    if ticket['type'] != 'implementation':
        updates['Status'] = 'claimed'
    after = dict(before)
    after[path] = change_fields(before[path], path, updates)
    return after


def _plan_release(request, before, labels, tickets, frontier, claim):
    _, ticket, path = _ticket_context(request, tickets)
    _require_claim(ticket, request)
    updates = {'Claimed by': None, 'Claim ID': None}
    if ticket['type'] != 'implementation':
        updates['Status'] = None
    after = dict(before)
    after[path] = change_fields(before[path], path, updates)
    return after


def _plan_reassign(request, before, labels, tickets, frontier, claim):
    _, ticket, path = _ticket_context(request, tickets)
    _require_claim(ticket, request)
    after = dict(before)
    after[path] = change_fields(before[path], path, {'Claimed by': request['new_owner'], 'Claim ID': claim})
    return after


def _plan_complete(request, before, labels, tickets, frontier, claim):
    _, ticket, path = _ticket_context(request, tickets)
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


def _plan_resolve(request, before, labels, tickets, frontier, claim):
    number, ticket, path = _ticket_context(request, tickets)
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


PLAN_HANDLERS = {
    'publish': _plan_publish, 'claim': _plan_claim,
    'release': _plan_release, 'reassign': _plan_reassign,
    'resolve': _plan_resolve, 'complete': _plan_complete,
}


def make_plan(action, request, before, labels):
    tickets, frontier = graph(before, labels)
    claim = digest(encoded([request['operation_id'], request]))[:32]
    after = PLAN_HANDLERS[action](request, before, labels, tickets, frontier, claim)
    graph(after, labels)
    changes = []
    # New blockers are written first; recovery still gates all readers until done.
    ordered = []
    seen = set()
    all_tickets, _ = graph(after, labels)

    def add(number):
        if number in seen:
            return
        for blocker in all_tickets[number]['blocked_by']:
            add(blocker)
        seen.add(number)
        ordered.append(all_tickets[number]['path'])

    for number in sorted(all_tickets):
        add(number)
    ordered.append('map.md')
    for path in ordered:
        if before.get(path) != after.get(path):
            changes.append({'path': path, 'before': pack(before.get(path)), 'after': pack(after[path])})
    guards = {path: digest(body) for path, body in before.items() if path not in {c['path'] for c in changes}}
    plan = {'version': VERSION, 'action': action, 'request': request, 'labels': labels,
            'input_hash': snap_hash(before), 'guards': guards, 'changes': changes,
            'claim_id': claim if action in ('claim', 'reassign') else None}
    return plan


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


def atomic_write(path, data):
    path.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.ticket-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_journal(path):
    record = read_json(path, 64 * 1024 * 1024)
    if not isinstance(record, dict) or set(record) != {'plan', 'plan_hash', 'state'}:
        fail('Malformed operation record', path=str(path))
    plan = record['plan']
    if record['state'] not in ('pending', 'applied') or not isinstance(plan, dict):
        fail('Invalid operation record state')
    if digest(encoded(plan)) != record['plan_hash']:
        fail('Operation record digest mismatch')
    keys = {'version', 'action', 'request', 'labels', 'input_hash', 'guards', 'changes', 'claim_id'}
    if set(plan) != keys or plan['version'] != VERSION or not isinstance(plan['changes'], list) or not isinstance(plan['guards'], dict):
        fail('Unsupported operation record')
    if plan['action'] not in ('publish', 'claim', 'release', 'reassign', 'resolve', 'complete'):
        fail('Unknown recorded action')
    parse_request(plan['action'], plan['request'])
    if path.name != plan['request']['operation_id'] + '.json':
        fail('Journal operation identity mismatch')
    seen = set()
    for change in plan['changes']:
        if not isinstance(change, dict) or set(change) != {'path', 'before', 'after'}:
            fail('Malformed recorded change')
        relative = change['path']
        if not isinstance(relative, str) or relative in seen or not (relative == 'map.md' or (relative.startswith('issues/') and NAME.fullmatch(relative[7:]))):
            fail('Invalid recorded change path')
        seen.add(relative)
        unpack(change['before'])
        if unpack(change['after']) is None:
            fail('Recorded after image is missing')
    return record


def journals(root):
    directory = safe_path(root, '.ticket-operations')
    if not directory.exists():
        return []
    if not directory.is_dir():
        fail('Operation store must be a directory')
    result = []
    for path in sorted(directory.glob('*.json')):
        result.append((path, load_journal(path)))
    return result


def check_current(root, plan):
    current = snapshot(root)
    changes = {change['path']: change for change in plan['changes']}
    if set(current) - (set(plan['guards']) | set(changes)):
        raise Failure('conflict', 'Feature gained files after operation began')
    for path, expected in plan['guards'].items():
        if path not in current or digest(current[path]) != expected:
            raise Failure('conflict', 'Unchanged input no longer matches', path=path)
    for path, change in changes.items():
        if current.get(path) not in (unpack(change['before']), unpack(change['after'])):
            raise Failure('conflict', 'File differs from recorded before/after states', path=path)
    return current


def execute(root, record, journal_path, apply):
    plan = record['plan']
    operation = plan['request']['operation_id']
    if record['state'] == 'applied':
        return {'outcome': 'already_applied', 'operation_id': operation,
                'historical': True, 'claim_id': plan['claim_id'], 'plan_hash': record['plan_hash']}
    check_current(root, plan)
    if not apply:
        return {'outcome': 'preview', 'operation_id': operation, 'plan_hash': record['plan_hash'],
                'claim_id': plan['claim_id'], 'changes': describe(plan), 'effects': 'none'}
    directory = safe_path(root, '.ticket-operations')
    directory.mkdir(exist_ok=True)
    atomic_write(journal_path, encoded(record))
    try:
        for change in plan['changes']:
            current = check_current(root, plan)
            after = unpack(change['after'])
            if current.get(change['path']) != after:
                atomic_write(safe_path(root, change['path']), after)
        check_current(root, plan)
        record['state'] = 'applied'
        atomic_write(journal_path, encoded(record))
    except (Failure, OSError) as error:
        details = error.result if isinstance(error, Failure) else {'message': str(error)}
        raise Failure('incomplete', 'Operation needs resume or reconciliation', operation_id=operation,
                      cause=details, journal=str(journal_path)) from error
    return {'outcome': 'applied', 'operation_id': operation, 'plan_hash': record['plan_hash'],
            'claim_id': plan['claim_id'], 'paths': [c['path'] for c in plan['changes']]}


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


def run(args):
    root = args.feature_root.resolve(strict=True)
    if not root.is_dir():
        fail('Feature root must be a directory')
    labels = dict(DEFAULT_LABELS)
    if args.labels:
        supplied = read_json(args.labels)
        if not isinstance(supplied, dict) or set(supplied) != set(labels):
            fail('Labels must map all canonical roles including done')
        for value in supplied.values():
            one_line(value, 'label')
        if len(set(supplied.values())) != len(supplied):
            fail('Labels must be distinct')
        labels = supplied
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX if args.apply else fcntl.LOCK_SH)
        saved = journals(root)
        pending = [(path, record) for path, record in saved if record['state'] == 'pending']
        if args.command in ('validate', 'frontier'):
            if pending:
                raise Failure('incomplete', 'Feature has unfinished operations', operations=[r['plan']['request']['operation_id'] for _, r in pending])
            before = snapshot(root)
            tickets, frontier = graph(before, labels)
            if snapshot(root) != before:
                raise Failure('conflict', 'Feature changed during scan')
            return {'outcome': 'valid', 'feature_root': str(root), 'input_hash': snap_hash(before),
                    'frontier': frontier, 'tickets': list(tickets.values()),
                    'all_terminal': bool(tickets) and all(t['terminal'] for t in tickets.values())}
        if args.command == 'resume':
            if not args.operation_id or not OP_ID.fullmatch(args.operation_id):
                fail('resume requires a valid --operation-id')
            selected = [(p, r) for p, r in saved if r['plan']['request']['operation_id'] == args.operation_id]
            if len(selected) != 1:
                fail('Operation not found')
            if any(r['plan']['request']['operation_id'] != args.operation_id for _, r in pending):
                raise Failure('incomplete', 'Another operation is pending')
            path, record = selected[0]
            return execute(root, record, path, args.apply)
        if not args.request:
            fail('Mutation requires --request JSON_FILE')
        request = read_json(args.request)
        parse_request(args.command, request)
        operation = request['operation_id']
        existing = [(p, r) for p, r in saved if r['plan']['request']['operation_id'] == operation]
        if any(r['plan']['request']['operation_id'] != operation for _, r in pending):
            raise Failure('incomplete', 'Another operation is pending', operations=[r['plan']['request']['operation_id'] for _, r in pending])
        if existing:
            path, record = existing[0]
            if record['plan']['request'] != request or record['plan']['action'] != args.command or record['plan']['labels'] != labels:
                raise Failure('conflict', 'Operation identity has different inputs', operation_id=operation)
        else:
            before = snapshot(root)
            plan = make_plan(args.command, request, before, labels)
            if snapshot(root) != before:
                raise Failure('conflict', 'Feature changed while planning')
            record = {'plan': plan, 'plan_hash': digest(encoded(plan)), 'state': 'pending'}
            path = safe_path(root, f'.ticket-operations/{operation}.json')
            if args.apply and not args.expect_plan:
                raise Failure('invalid_input', 'Apply requires a plan hash from a preview',
                              operation_id=operation, next_action='rerun with --expect-plan ' + digest(encoded(plan)))
        if args.expect_plan and args.expect_plan != record['plan_hash']:
            raise Failure('conflict', 'Preview plan changed', actual=record['plan_hash'])
        return execute(root, record, path, args.apply)
    finally:
        os.close(directory_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'frontier', 'publish', 'claim', 'release', 'reassign', 'resolve', 'complete', 'resume'])
    parser.add_argument('--feature-root', required=True, type=Path)
    parser.add_argument('--request', type=Path)
    parser.add_argument('--operation-id')
    parser.add_argument('--labels', type=Path)
    parser.add_argument('--expect-plan')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Failure as error:
        print(json.dumps(error.result, sort_keys=True))
        return {'invalid_input': 2, 'conflict': 3, 'incomplete': 4}.get(error.result['outcome'], 2)
    except (OSError, UnicodeError) as error:
        print(json.dumps({'outcome': 'unavailable', 'message': str(error)}))
        return 5


if __name__ == '__main__':
    sys.exit(main())
