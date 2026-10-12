#!/usr/bin/env python3
"""Local Markdown ticket operations with cooperative locking and recovery."""
import argparse
import json
from pathlib import Path
import sys

# Also support callers that load this CLI by filename under isolated Python.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ticket_planning import (
    DEFAULT_LABELS, FIELD, MAX_FILE, MAX_REQUEST, NAME, OP_ID, TYPES, VERSION,
    Failure, OperationPlanner, Plan, TicketGraph, blockers, change_fields,
    check_evidence, describe, digest, encoded, fail, fields, graph, make_plan,
    one_line, pack, parse_request, snap_hash, text_value, unique_object, unpack,
)
from ticket_execution import (
    JournalStore, PlanExecutor, atomic_write, check_current, journals,
    load_journal, read_json, read_regular, safe_path, snapshot,
)


def execute(root, record, journal_path, apply):
    # Keep the established write-injection seam for in-process CLI consumers.
    store = JournalStore(root, writer=atomic_write)
    return PlanExecutor(store).execute_record(record, journal_path, apply)


def run(args):
    store = JournalStore(args.feature_root, writer=atomic_write)
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
    executor = PlanExecutor(store)
    if args.command in ('validate', 'frontier'):
        return executor.inspect(labels)
    if args.command == 'resume':
        resume = executor.resume if args.apply else executor.preview_resume
        return resume(args.operation_id)
    if not args.request:
        fail('Mutation requires --request JSON_FILE')
    request = read_json(args.request)
    operation = executor.apply if args.apply else executor.preview
    return operation(args.command, request, labels, args.expect_plan)


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
