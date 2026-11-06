#!/usr/bin/env python3
"""Pre-commit secrets gate: scan staged (index) content with the capture SecretGate.

Reuses capture_secrets.SecretGate unchanged, so the redacted gitleaks pass, the
built-in rules, the hex false-positive tuning, the reviewed-exception file, and
the live-fingerprint denylist (CODE_REVIEW_SECRET_FINGERPRINTS) behave exactly
as they do for review capture. Differences are policy only:

* the gate fails closed unless gitleaks actually ran (missing binary, failed
  scan, or the stdlib-only opt-out all block the commit);
* the stdlib-only opt-out is ignored here, since an environment variable must
  not be able to silently disable a commit gate;
* findings are printed redacted (file, line, rule, length, sha256 prefix).
"""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_common import Failure, MAX_FILE, MAX_FILES
from capture_payloads import PayloadInventory
from capture_secrets import SECRETS_SCANNER_ENV, GitleaksAdapter, SecretGate

EXIT_OK, EXIT_BLOCKED, EXIT_ERROR = 0, 1, 2
SKIPPED_MODES = {b'160000', b'120000'}  # submodule pointers, symlinks


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if result.returncode != 0:
        raise Failure('invalid_input', 'git failed: ' + ' '.join(args[:2]))
    return result.stdout


def staged_payloads(repo):
    """Index entries as capture-style (label, relative, bytes) payloads."""
    rows = [row for row in git(repo, 'ls-files', '-s', '-z').split(b'\0') if row]
    changed = set(git(repo, 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z').split(b'\0'))
    if len(rows) > MAX_FILES:
        raise Failure('unsupported', 'Too many tracked files to scan')
    payloads = []
    for row in rows:
        meta, _, path = row.partition(b'\t')
        mode, sha, stage = meta.split(b' ')
        if path not in changed and path != b'.code-review-secrets-allow':
            continue  # only staged changes are gated; allowlist always loads
        if mode in SKIPPED_MODES or stage != b'0':
            continue
        name = path.decode('utf-8', 'surrogateescape')
        size = int(git(repo, 'cat-file', '-s', sha.decode()).strip())
        if size > MAX_FILE:
            raise Failure('unsupported', 'Staged file exceeds scan bound', path=name)
        data = git(repo, 'cat-file', 'blob', sha.decode())
        suffix = Path(name).suffix[:16]
        payloads.append((f'after:{name}', f'{len(payloads):06d}{suffix}', data))
    return payloads


def run(repo, scanner=None):
    os.environ.pop(SECRETS_SCANNER_ENV, None)
    payloads = staged_payloads(repo)
    if not any(label != 'after:.code-review-secrets-allow' for label, _, _ in payloads):
        return EXIT_OK, {'outcome': 'clean', 'files': 0}
    gate = SecretGate(scanner or GitleaksAdapter())
    try:
        scan = gate.check(PayloadInventory(tuple(payloads)))
    except Failure as error:
        return EXIT_BLOCKED if error.result['outcome'] == 'secret_detected' else EXIT_ERROR, error.result
    if scan.get('gitleaks') != 'used':
        return EXIT_BLOCKED, {'outcome': 'secret_detected', 'message':
                              'Commit blocked: gitleaks did not run, so coverage is incomplete',
                              'secret_scan': scan}
    return EXIT_OK, {'outcome': 'clean', 'files': len(payloads), 'secret_scan': scan}


def main():
    repo = Path(os.environ.get('COMMIT_GATE_REPO') or Path.cwd())
    try:
        code, result = run(repo)
    except Failure as error:
        code, result = EXIT_ERROR, error.result
    if code != EXIT_OK:
        print('commit-secrets-gate: commit refused', file=sys.stderr)
        print(json.dumps(result, sort_keys=True, indent=2), file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
