#!/usr/bin/env python3
"""Acquire a local Git worktree lease and emit a verified writer record."""

import argparse
import sys

from herdr_jobs.records import JobError, encoded
from herdr_jobs.worktrees import (acquire, list_local_leases, release_local_lease,
                                  status_local_lease)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise JobError("invalid_input", message)


def main(argv=None):
    parser = Parser(description="Acquire and verify an isolated Git worktree for a repository writer.")
    commands = parser.add_subparsers(dest="operation", required=True)
    command = commands.add_parser("acquire", help="lease and verify a linked worktree")
    command.add_argument("--repo", required=True, help="expected repository checkout")
    command.add_argument("--lease-holder", required=True, help="durable task/run identity")
    command.add_argument("--expected-base", help="required base commit when the task has a pinned base")
    commands.add_parser("list", help="list verified active local Git worktree leases")
    command = commands.add_parser("status", help="show status for one local Git worktree lease")
    command.add_argument("--lease-id", required=True)
    command = commands.add_parser("release", help="release a verified local Git worktree lease")
    command.add_argument("--lease-id", required=True)
    command.add_argument("--who", required=True, help="person or process responsible for release")
    command.add_argument("--why", required=True, help="reason for releasing the lease")
    command.add_argument("--force", action="store_true",
                         help="remove a dirty or unmerged worktree")
    try:
        args = parser.parse_args(argv)
        if args.operation == "acquire":
            record = acquire(args.repo, args.lease_holder, args.expected_base)
            result = record
        elif args.operation == "list":
            result = {"leases": list_local_leases()}
        elif args.operation == "status":
            result = {"lease": status_local_lease(args.lease_id)}
        else:
            result = release_local_lease(args.lease_id, args.who, args.why, force=args.force)
        print(encoded(result).decode("utf-8"))
        return 0
    except JobError as error:
        print(f"lease-worktree: {error}", file=sys.stderr)
        return {"invalid_input": 2, "unavailable_capability": 4}.get(error.kind, 2)


if __name__ == "__main__":
    raise SystemExit(main())
