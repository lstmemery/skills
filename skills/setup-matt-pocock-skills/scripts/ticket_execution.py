"""Filesystem storage, locked plan execution, and journal recovery.

PlanExecutor depends only on the JournalStore surface: root, locked, snapshot,
journals, journal_path, save and write. An in-memory store can replace it locally.
All reads and operation selection happen under the same lock as execution.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from ticket_planning import (
    MAX_FILE, MAX_REQUEST, NAME, OP_ID, VERSION, Failure, OperationPlanner,
    TicketGraph, describe, digest, encoded, fail, parse_request, snap_hash,
    unique_object, unpack,
)


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


def validate_journal(record, path):
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


def load_journal(path):
    return validate_journal(read_json(path, 64 * 1024 * 1024), path)


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


def check_snapshot(current, plan):
    changes = {change['path']: change for change in plan['changes']}
    if set(current) - (set(plan['guards']) | set(changes)):
        raise Failure('conflict', 'Feature gained files after operation began')
    for path, expected in plan['guards'].items():
        if path not in current or digest(current[path]) != expected:
            raise Failure('conflict', 'Unchanged input no longer matches', path=path)
    for path, change in changes.items():
        images = (unpack(change['before']), unpack(change['after']))
        allowed = {digest(body) if body is not None else None for body in images}
        body = current.get(path)
        actual = digest(body) if body is not None else None
        if actual not in allowed:
            raise Failure('conflict', 'File differs from recorded before/after states', path=path)
    return current


def check_current(root, plan):
    return check_snapshot(snapshot(root), plan)


class JournalStore:
    """Local filesystem adapter, including locking and durable atomic writes."""

    def __init__(self, root, writer=None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            fail('Feature root must be a directory')
        self._writer = atomic_write if writer is None else writer

    @contextmanager
    def locked(self, exclusive=False):
        directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            os.close(directory_fd)

    def snapshot(self):
        return snapshot(self.root)

    def journals(self):
        return journals(self.root)

    def journal_path(self, operation):
        return safe_path(self.root, f'.ticket-operations/{operation}.json')

    def save(self, record, journal_path):
        directory = safe_path(self.root, '.ticket-operations')
        directory.mkdir(exist_ok=True)
        self._writer(journal_path, encoded(record))

    def write(self, relative, body):
        self._writer(safe_path(self.root, relative), body)


class PlanExecutor:
    """Guarded execution and recovery using durable operation IDs and hashes."""

    def __init__(self, store, planner=None):
        self.store = store
        self.planner = OperationPlanner() if planner is None else planner

    def inspect(self, labels):
        with self.store.locked():
            pending = self._pending(self.store.journals())
            if pending:
                raise Failure('incomplete', 'Feature has unfinished operations',
                              operations=[r['plan']['request']['operation_id'] for _, r in pending])
            before = self.store.snapshot()
            parsed = TicketGraph(before, labels)
            if self.store.snapshot() != before:
                raise Failure('conflict', 'Feature changed during scan')
            return {'outcome': 'valid', 'feature_root': str(self.store.root),
                    'input_hash': snap_hash(before), 'frontier': parsed.frontier,
                    'tickets': list(parsed.tickets.values()),
                    'all_terminal': bool(parsed.tickets) and all(t['terminal'] for t in parsed.tickets.values())}

    def preview(self, action, request, labels, expect_plan=None):
        return self._operation(action, request, labels, False, expect_plan)

    def apply(self, action, request, labels, expect_plan=None):
        return self._operation(action, request, labels, True, expect_plan)

    def preview_resume(self, operation):
        return self._resume(operation, False)

    def resume(self, operation):
        return self._resume(operation, True)

    @staticmethod
    def _pending(saved):
        return [(path, record) for path, record in saved if record['state'] == 'pending']

    def _operation(self, action, request, labels, applying, expect_plan):
        with self.store.locked(exclusive=applying):
            saved = self.store.journals()
            pending = self._pending(saved)
            parse_request(action, request)
            operation = request['operation_id']
            existing = [(p, r) for p, r in saved if r['plan']['request']['operation_id'] == operation]
            if any(r['plan']['request']['operation_id'] != operation for _, r in pending):
                raise Failure('incomplete', 'Another operation is pending',
                              operations=[r['plan']['request']['operation_id'] for _, r in pending])
            if existing:
                path, record = existing[0]
                if record['plan']['request'] != request or record['plan']['action'] != action or record['plan']['labels'] != labels:
                    raise Failure('conflict', 'Operation identity has different inputs', operation_id=operation)
            else:
                before = self.store.snapshot()
                plan = self.planner.plan(action, request, before, labels)
                if self.store.snapshot() != before:
                    raise Failure('conflict', 'Feature changed while planning')
                record = {'plan': plan, 'plan_hash': digest(encoded(plan)), 'state': 'pending'}
                path = self.store.journal_path(operation)
                if applying and not expect_plan:
                    raise Failure('invalid_input', 'Apply requires a plan hash from a preview',
                                  operation_id=operation,
                                  next_action='rerun with --expect-plan ' + record['plan_hash'])
            if expect_plan and expect_plan != record['plan_hash']:
                raise Failure('conflict', 'Preview plan changed', actual=record['plan_hash'])
            return self._execute(record, path, applying)

    def _resume(self, operation, applying):
        with self.store.locked(exclusive=applying):
            saved = self.store.journals()
            pending = self._pending(saved)
            if not operation or not OP_ID.fullmatch(operation):
                fail('resume requires a valid --operation-id')
            selected = [(p, r) for p, r in saved if r['plan']['request']['operation_id'] == operation]
            if len(selected) != 1:
                fail('Operation not found')
            if any(r['plan']['request']['operation_id'] != operation for _, r in pending):
                raise Failure('incomplete', 'Another operation is pending')
            path, record = selected[0]
            return self._execute(record, path, applying)

    def execute_record(self, record, journal_path, apply):
        """Compatibility entry for callers holding a version-1 journal record."""
        with self.store.locked(exclusive=apply):
            validate_journal(record, journal_path)
            operation = record['plan']['request']['operation_id']
            saved = self.store.journals()
            for path, saved_record in saved:
                identity = saved_record['plan']['request']['operation_id']
                if identity == operation:
                    if saved_record['plan_hash'] != record['plan_hash']:
                        raise Failure('conflict', 'Operation identity has different inputs', operation_id=operation)
                    record, journal_path = saved_record, path
                elif saved_record['state'] == 'pending':
                    raise Failure('incomplete', 'Another operation is pending')
            return self._execute(record, journal_path, apply)

    def _check_current(self, plan):
        return check_snapshot(self.store.snapshot(), plan)

    def _execute(self, record, journal_path, applying):
        plan = record['plan']
        operation = plan['request']['operation_id']
        if record['state'] == 'applied':
            return {'outcome': 'already_applied', 'operation_id': operation,
                    'historical': True, 'claim_id': plan['claim_id'], 'plan_hash': record['plan_hash']}
        self._check_current(plan)
        if not applying:
            return {'outcome': 'preview', 'operation_id': operation, 'plan_hash': record['plan_hash'],
                    'claim_id': plan['claim_id'], 'changes': describe(plan), 'effects': 'none'}
        self.store.save(record, journal_path)
        try:
            for change in plan['changes']:
                current = self._check_current(plan)
                after = unpack(change['after'])
                if current.get(change['path']) != after:
                    self.store.write(change['path'], after)
            self._check_current(plan)
            completed = {**record, 'state': 'applied'}
            self.store.save(completed, journal_path)
            record['state'] = 'applied'
        except (Failure, OSError) as error:
            details = error.result if isinstance(error, Failure) else {'message': str(error)}
            raise Failure('incomplete', 'Operation needs resume or reconciliation', operation_id=operation,
                          cause=details, journal=str(journal_path)) from error
        return {'outcome': 'applied', 'operation_id': operation, 'plan_hash': record['plan_hash'],
                'claim_id': plan['claim_id'], 'paths': [c['path'] for c in plan['changes']]}
