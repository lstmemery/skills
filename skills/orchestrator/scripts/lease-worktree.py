#!/usr/bin/env python3
"""Acquire a durable Treehouse lease and emit a verified writer record."""

import argparse
import sys

from herdr_jobs.records import JobError, encoded
from herdr_jobs.worktrees import acquire


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise JobError("invalid_input", message)


def main(argv=None):
    parser = Parser(description="Acquire and verify an isolated Treehouse worktree for a repository writer.")
    commands = parser.add_subparsers(dest="operation", required=True)
    command = commands.add_parser("acquire", help="lease and verify a linked worktree")
    command.add_argument("--repo", required=True, help="expected repository checkout")
    command.add_argument("--lease-holder", required=True, help="durable task/run identity")
    command.add_argument("--expected-base", help="required base commit when the task has a pinned base")
    try:
        args = parser.parse_args(argv)
        record = acquire(args.repo, args.lease_holder, args.expected_base)
        print(encoded(record).decode("utf-8"))
        return 0
    except JobError as error:
        print(f"lease-worktree: {error}", file=sys.stderr)
        return {"invalid_input": 2, "unavailable_capability": 4}.get(error.kind, 2)


if __name__ == "__main__":
    raise SystemExit(main())
