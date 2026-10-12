from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from threading import RLock
import unittest

from ticket_test_support import scenarios, ticket
from ticket_execution import JournalStore, PlanExecutor, validate_journal
from ticket_planning import DEFAULT_LABELS, Failure, OperationPlanner, encoded, unique_object


class MemoryJournalStore:
    """Working storage adapter for the same executor, without filesystem I/O."""

    def __init__(self, contents):
        self.root = Path('feature')
        self.files = dict(contents)
        self.records = {}
        self._lock = RLock()
        self._lock_modes = []

    @contextmanager
    def locked(self, exclusive=False):
        with self._lock:
            self._lock_modes.append(exclusive)
            try:
                yield
            finally:
                self._lock_modes.pop()

    def _require_lock(self, writing=False):
        if not self._lock_modes:
            raise RuntimeError('Storage accessed without a lock')
        if writing and not self._lock_modes[-1]:
            raise RuntimeError('Write attempted without an exclusive lock')

    def snapshot(self):
        self._require_lock()
        return dict(self.files)

    def journals(self):
        self._require_lock()
        return [(self.journal_path(name[:-5]), validate_journal(
            json.loads(data, object_pairs_hook=unique_object), self.journal_path(name[:-5])))
            for name, data in sorted(self.records.items())]

    def journal_path(self, operation):
        return self.root / '.ticket-operations' / (operation + '.json')

    def save(self, record, journal_path):
        self._require_lock(writing=True)
        self.records[journal_path.name] = encoded(record)

    def write(self, relative, body):
        self._require_lock(writing=True)
        self.files[relative] = body


class CrashAfterWrite:
    """Interrupt after a durable store write, including journal transitions."""

    def __init__(self, store, stop_after):
        self.store = store
        self.remaining = stop_after

    def __getattr__(self, name):
        return getattr(self.store, name)

    def _interrupt(self):
        self.remaining -= 1
        if self.remaining == 0:
            raise OSError('Injected crash after durable write')

    def save(self, record, journal_path):
        self.store.save(record, journal_path)
        self._interrupt()

    def write(self, relative, body):
        self.store.write(relative, body)
        self._interrupt()


class EditAfterChange:
    def __init__(self, store, trigger, relative, body):
        self.store = store
        self.trigger = trigger
        self.relative = relative
        self.body = body

    def __getattr__(self, name):
        return getattr(self.store, name)

    def write(self, relative, body):
        self.store.write(relative, body)
        if relative == self.trigger:
            self.store.write(self.relative, self.body)


class ExecutorContract:
    def contents(self, store):
        with store.locked():
            return store.snapshot()

    def records(self, store):
        with store.locked():
            return store.journals()

    def put(self, store, relative, body):
        with store.locked(exclusive=True):
            store.write(relative, body)

    def assert_failure(self, outcome, call):
        with self.assertRaises(Failure) as caught:
            call()
        self.assertEqual(caught.exception.result['outcome'], outcome)
        return caught.exception.result

    def assert_completed(self, action, store, result):
        state = PlanExecutor(store).inspect(DEFAULT_LABELS)
        contents = self.contents(store)
        if action == 'publish':
            self.assertEqual(state['frontier'], [3])
            self.assertEqual(set(contents), {'issues/01-example.md', 'issues/02-example.md', 'issues/03-example.md'})
        else:
            item = state['tickets'][0]
            if action in ('claim', 'reassign'):
                self.assertEqual(item['owner'], 'alice' if action == 'claim' else 'bob')
                self.assertEqual(item['claim_id'], result['claim_id'])
            if action == 'release':
                self.assertEqual(state['frontier'], [1])
                self.assertIsNone(item['owner'])
            if action in ('resolve', 'complete'):
                self.assertTrue(item['successful'])
                self.assertIsNone(item['owner'])
                heading = b'## Answer' if action == 'resolve' else b'## Verification'
                self.assertEqual(contents['issues/01-example.md'].count(heading), 1)
            if action == 'resolve':
                self.assertEqual(contents['map.md'].count(b'[1: Select option A]'), 1)
                self.assertIn(b'Keep fog.', contents['map.md'])

    def test_preview_does_not_create_a_journal_or_change_files(self):
        before = {'issues/01-example.md': ticket(1).encode()}
        store = self.make_store(before)
        executor = PlanExecutor(store)
        request = {'operation_id': 'claim-one', 'ticket': 1, 'owner': 'alice'}
        result = executor.preview('claim', request, DEFAULT_LABELS)
        self.assertEqual(result['outcome'], 'preview')
        self.assertEqual(result['plan_hash'],
                         'c452cb46df1485cd937cef79700f4f7a967a4ed5f50f9d89244f7ef4aa12c1e8')
        self.assertEqual(self.contents(store), before)
        self.assertEqual(self.records(store), [])

    def test_new_apply_requires_matching_preview_hash(self):
        before = {'issues/01-example.md': ticket(1).encode()}
        store = self.make_store(before)
        executor = PlanExecutor(store)
        request = {'operation_id': 'claim', 'ticket': 1, 'owner': 'alice'}
        self.assert_failure('invalid_input', lambda: executor.apply('claim', request, DEFAULT_LABELS))
        self.assert_failure('conflict', lambda: executor.apply('claim', request, DEFAULT_LABELS, 'stale-hash'))
        self.assertEqual(self.records(store), [])
        preview = executor.preview('claim', request, DEFAULT_LABELS)
        self.put(store, 'issues/01-example.md', before['issues/01-example.md'] + b'Independent edit.\n')
        self.assert_failure('conflict', lambda: executor.apply('claim', request, DEFAULT_LABELS, preview['plan_hash']))
        self.assertNotIn(b'Claimed by:', self.contents(store)['issues/01-example.md'])
        self.assertEqual(self.records(store), [])

    def test_crash_after_every_durable_write_resumes_each_operation_once(self):
        for action, before, request in scenarios():
            change_count = len(OperationPlanner().plan(action, request, before, DEFAULT_LABELS)['changes'])
            for checkpoint in range(1, change_count + 3):
                with self.subTest(action=action, checkpoint=checkpoint):
                    store = self.make_store(before)
                    executor = PlanExecutor(store)
                    preview = executor.preview(action, request, DEFAULT_LABELS)
                    interrupted = PlanExecutor(CrashAfterWrite(store, checkpoint))
                    with self.assertRaises((Failure, OSError)):
                        interrupted.apply(action, request, DEFAULT_LABELS, preview['plan_hash'])
                    records = self.records(store)
                    self.assertEqual(len(records), 1)
                    self.assertEqual(records[0][1]['plan_hash'], preview['plan_hash'])
                    if checkpoint < change_count + 2:
                        self.assertEqual(records[0][1]['state'], 'pending')
                        self.assert_failure('incomplete', lambda: executor.inspect(DEFAULT_LABELS))
                        recovery_preview = executor.preview_resume(request['operation_id'])
                        self.assertEqual(recovery_preview['outcome'], 'preview')
                    else:
                        self.assertEqual(records[0][1]['state'], 'applied')
                    recovered = PlanExecutor(store).resume(request['operation_id'])
                    self.assertEqual(recovered['outcome'], 'already_applied' if checkpoint == change_count + 2 else 'applied')
                    self.assertEqual(recovered['plan_hash'], preview['plan_hash'])
                    self.assert_completed(action, store, recovered)
                    completed_contents = self.contents(store)
                    repeated = PlanExecutor(store).apply(action, request, DEFAULT_LABELS)
                    self.assertEqual(repeated['outcome'], 'already_applied')
                    self.assertTrue(repeated['historical'])
                    self.assertEqual(self.contents(store), completed_contents)
                    self.assertEqual(self.records(store)[0][1]['state'], 'applied')

    def pending_claim(self, extra=None):
        before = {'issues/01-example.md': ticket(1).encode()}
        if extra:
            before.update(extra)
        request = {'operation_id': 'claim', 'ticket': 1, 'owner': 'alice'}
        store = self.make_store(before)
        preview = PlanExecutor(store).preview('claim', request, DEFAULT_LABELS)
        with self.assertRaises(OSError):
            PlanExecutor(CrashAfterWrite(store, 1)).apply('claim', request, DEFAULT_LABELS, preview['plan_hash'])
        return store, before, request

    def test_resume_rejects_changed_or_missing_guards_and_changed_images(self):
        unchanged = {'issues/02-example.md': ticket(2).encode()}
        for mode in ('guard-edit', 'guard-missing', 'before-edit', 'before-missing', 'gained-file'):
            with self.subTest(mode=mode):
                store, before, request = self.pending_claim(unchanged)
                if mode in ('guard-edit', 'before-edit'):
                    relative = 'issues/02-example.md' if mode == 'guard-edit' else 'issues/01-example.md'
                    self.put(store, relative, before[relative] + b'Independent edit.\n')
                elif mode in ('guard-missing', 'before-missing'):
                    relative = 'issues/02-example.md' if mode == 'guard-missing' else 'issues/01-example.md'
                    self.remove(store, relative)
                else:
                    self.put(store, 'issues/03-example.md', ticket(3).encode())
                conflicted = self.contents(store)
                executor = PlanExecutor(store)
                self.assert_failure('conflict', lambda: executor.resume(request['operation_id']))
                self.assert_failure('conflict', lambda: executor.preview_resume(request['operation_id']))
                self.assertEqual(self.contents(store), conflicted)
                self.assertEqual(self.records(store)[0][1]['state'], 'pending')
                self.assert_failure('incomplete', lambda: executor.inspect(DEFAULT_LABELS))
                if mode == 'gained-file':
                    self.remove(store, 'issues/03-example.md')
                else:
                    self.put(store, relative, before[relative])
                self.assertEqual(executor.resume(request['operation_id'])['outcome'], 'applied')

    def test_pending_operation_blocks_readers_other_mutations_and_other_resumes(self):
        before = {'issues/01-example.md': ticket(1).encode(), 'issues/02-example.md': ticket(2).encode()}
        store = self.make_store(before)
        executor = PlanExecutor(store)
        first = {'operation_id': 'first', 'ticket': 2, 'owner': 'bob'}
        preview = executor.preview('claim', first, DEFAULT_LABELS)
        executor.apply('claim', first, DEFAULT_LABELS, preview['plan_hash'])
        pending = {'operation_id': 'pending', 'ticket': 1, 'owner': 'alice'}
        preview = executor.preview('claim', pending, DEFAULT_LABELS)
        with self.assertRaises(OSError):
            PlanExecutor(CrashAfterWrite(store, 1)).apply('claim', pending, DEFAULT_LABELS, preview['plan_hash'])
        self.assert_failure('incomplete', lambda: executor.inspect(DEFAULT_LABELS))
        self.assert_failure('incomplete', lambda: executor.preview('claim', first, DEFAULT_LABELS))
        self.assert_failure('incomplete', lambda: executor.apply('claim', first, DEFAULT_LABELS))
        self.assert_failure('incomplete', lambda: executor.resume('first'))
        self.assert_failure('incomplete', lambda: executor.preview_resume('first'))
        self.assertEqual(executor.preview('claim', pending, DEFAULT_LABELS)['plan_hash'], preview['plan_hash'])
        self.assertEqual(executor.resume('pending')['outcome'], 'applied')
        self.assertEqual(executor.resume('first')['outcome'], 'already_applied')

    def test_operation_identity_cannot_be_reused_with_other_request_action_or_labels(self):
        before = {'issues/01-example.md': ticket(1).encode()}
        store = self.make_store(before)
        executor = PlanExecutor(store)
        request = {'operation_id': 'claim', 'ticket': 1, 'owner': 'alice'}
        preview = executor.preview('claim', request, DEFAULT_LABELS)
        executor.apply('claim', request, DEFAULT_LABELS, preview['plan_hash'])
        completed = self.contents(store)
        variants = [
            ('claim', {**request, 'owner': 'bob'}, DEFAULT_LABELS),
            ('claim', request, dict(DEFAULT_LABELS, done='finished')),
            ('publish', {'operation_id': 'claim', 'approval': 'Accepted', 'tickets': [
                {'filename': '02-example.md', 'body': ticket(2)}]}, DEFAULT_LABELS),
        ]
        for action, changed, labels in variants:
            with self.subTest(action=action, request=changed, labels=labels):
                self.assert_failure('conflict', lambda: executor.preview(action, changed, labels))
                self.assert_failure('conflict', lambda: executor.apply(action, changed, labels))
                self.assertEqual(self.contents(store), completed)
        self.assert_failure('conflict', lambda: executor.apply('claim', request, DEFAULT_LABELS, 'wrong-hash'))

    def test_applied_operations_are_historical_after_later_operations_and_edits(self):
        store = self.make_store({'issues/01-example.md': ticket(1).encode()})
        executor = PlanExecutor(store)
        request = {'operation_id': 'claim', 'ticket': 1, 'owner': 'alice'}
        preview = executor.preview('claim', request, DEFAULT_LABELS)
        claimed_result = executor.apply('claim', request, DEFAULT_LABELS, preview['plan_hash'])
        release = {'operation_id': 'release', 'ticket': 1, 'owner': 'alice',
                   'claim_id': claimed_result['claim_id'], 'reason': 'Explicit stop'}
        preview = executor.preview('release', release, DEFAULT_LABELS)
        executor.apply('release', release, DEFAULT_LABELS, preview['plan_hash'])
        self.put(store, 'issues/02-example.md', ticket(2).encode())
        self.put(store, 'issues/01-example.md', ticket(1).encode() + b'Later prose edit.\n')
        changed = self.contents(store)
        for repeated in (executor.resume('claim'), executor.preview_resume('claim'),
                         executor.preview('claim', request, DEFAULT_LABELS),
                         executor.apply('claim', request, DEFAULT_LABELS)):
            self.assertEqual(repeated['outcome'], 'already_applied')
            self.assertTrue(repeated['historical'])
            self.assertEqual(repeated['claim_id'], claimed_result['claim_id'])
            self.assertEqual(self.contents(store), changed)

    def test_invalid_resume_identity_or_missing_operation_has_no_effects(self):
        store = self.make_store({'issues/01-example.md': ticket(1).encode()})
        executor = PlanExecutor(store)
        for identity in (None, '', '../escape', 'missing'):
            with self.subTest(identity=identity):
                self.assert_failure('invalid_input', lambda: executor.resume(identity))
        self.assertEqual(self.records(store), [])

    def test_guards_are_checked_between_changes_and_before_journal_commit(self):
        action, original, request = next(s for s in scenarios() if s[0] == 'resolve')
        original['issues/02-example.md'] = ticket(2).encode()
        for trigger in ('issues/01-example.md', 'map.md'):
            for edit in ('guard', 'image', 'gained'):
                with self.subTest(trigger=trigger, edit=edit):
                    store = self.make_store(original)
                    executor = PlanExecutor(store)
                    preview = executor.preview(action, request, DEFAULT_LABELS)
                    relative = {'guard': 'issues/02-example.md', 'image': trigger, 'gained': 'issues/03-example.md'}[edit]
                    replacement = original.get(relative, ticket(3).encode()) + b'Independent edit.\n'
                    interrupted = PlanExecutor(EditAfterChange(store, trigger, relative, replacement))
                    result = self.assert_failure('incomplete', lambda: interrupted.apply(action, request, DEFAULT_LABELS,
                                                                                       preview['plan_hash']))
                    self.assertEqual(result['cause']['outcome'], 'conflict')
                    self.assertEqual(self.contents(store)[relative], replacement)
                    if trigger == 'issues/01-example.md':
                        self.assertEqual(self.contents(store)['map.md'], original['map.md'])
                    self.assertEqual(self.records(store)[0][1]['state'], 'pending')
                    self.assert_failure('incomplete', lambda: executor.inspect(DEFAULT_LABELS))
                    self.assert_failure('conflict', lambda: executor.resume('resolve'))
                    if edit == 'gained':
                        self.remove(store, relative)
                    else:
                        self.put(store, relative, original[relative])
                    recovered = executor.resume('resolve')
                    self.assertEqual(recovered['outcome'], 'applied')
                    self.assert_completed('resolve', store, recovered)

    def test_corrupted_journals_block_readers_and_resume(self):
        store, before, request = self.pending_claim()
        record = self.records(store)[0][1]
        corrupt = {**record, 'plan_hash': '0' * 64}
        with store.locked(exclusive=True):
            store.save(corrupt, store.journal_path('claim'))
        executor = PlanExecutor(store)
        self.assert_failure('invalid_input', lambda: executor.inspect(DEFAULT_LABELS))
        self.assert_failure('invalid_input', lambda: executor.resume(request['operation_id']))
        self.assertEqual(self.contents(store), before)


class MemoryExecutorTests(ExecutorContract, unittest.TestCase):
    def make_store(self, contents):
        return MemoryJournalStore(contents)

    def remove(self, store, relative):
        store.files.pop(relative)


class FilesystemExecutorTests(ExecutorContract, unittest.TestCase):
    def make_store(self, contents):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'feature'
        (root / 'issues').mkdir(parents=True)
        for relative, body in contents.items():
            (root / relative).write_bytes(body)
        return JournalStore(root)

    def remove(self, store, relative):
        (store.root / relative).unlink()


if __name__ == '__main__':
    unittest.main()
