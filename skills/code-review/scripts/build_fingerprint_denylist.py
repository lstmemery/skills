#!/usr/bin/env python3
"""Add live secret values to the fingerprint denylist used by the commit gate.

Reads one secret value per line from stdin (never from argv, so values stay out
of shell history and process listings) and appends the first 16 hex characters
of each value's sha256 to the denylist file. Only fingerprints are stored. The
file defaults to the commit gate's durable path and is created with mode 600.

    some-secret-lister | python3 build_fingerprint_denylist.py [--file PATH]
"""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_common import digest
from commit_gate import default_denylist_path

PREFIX_LENGTH = 16


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--file', type=Path, default=None)
    args = parser.parse_args(argv)
    target = args.file or default_denylist_path()
    values = [line.strip() for line in sys.stdin.read().splitlines() if len(line.strip()) >= 8]
    existing = set()
    if target.is_file():
        existing = {line.strip() for line in target.read_text().splitlines()}
    new = sorted({digest(value.encode('latin-1', 'replace'))[:PREFIX_LENGTH] for value in values} - existing)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'a') as handle:
        handle.write(''.join(f'{prefix}\n' for prefix in new))
    print(f'{len(new)} fingerprint(s) added to {target}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
