#!/usr/bin/env python3
"""Capture immutable review inputs, refuse secret-looking payloads, and check drift."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

# File-loader consumers and isolated Python execution also need these siblings.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_common import Failure, MAX_FILE, MAX_FILES, MAX_TOTAL, VERSION, bounded_file, digest, invalid
from capture_payloads import PayloadInventory
from capture_secrets import (
    FINGERPRINT_ENV, GITLEAKS_TIMEOUT, SECRETS_SCANNER_ENV,
    GitleaksAdapter, SecretGate,
)
from capture_source import CaptureScope, Collector, Git, work_entry
from capture_store import CaptureStore


def observe(repo, mode, base, authorities):
    return Git(repo).observe(CaptureScope(mode, base, tuple(authorities)))


def verified_observation(repo, mode, base, authorities):
    first = observe(repo, mode, base, authorities)
    second = observe(repo, mode, base, authorities)
    if first[0]['capture_id'] != second[0]['capture_id']:
        raise Failure('conflict', 'Inputs changed during capture; no mixed capture was written')
    return second


def payload_inventory(manifest, blobs, diff, history):
    return list(PayloadInventory.from_observation(manifest, blobs, diff, history).payloads)


def gitleaks_scan(staging, labels, allowed_fingerprints=frozenset()):
    return GitleaksAdapter(timeout=GITLEAKS_TIMEOUT, max_report=MAX_TOTAL).scan(
        staging, labels, allowed_fingerprints)


def secrets_gate(payloads):
    return SecretGate(GitleaksAdapter(timeout=GITLEAKS_TIMEOUT, max_report=MAX_TOTAL)).check(
        PayloadInventory(tuple(payloads)))


def write_capture(out, manifest, blobs, diff, history):
    store = CaptureStore(out)
    # Freeze one inventory. The gate and writer receive this same instance,
    # including manifest bytes; neither may enumerate or reread the source.
    inventory = PayloadInventory.from_observation(manifest, blobs, diff, history)
    secret_scan = SecretGate(GitleaksAdapter(timeout=GITLEAKS_TIMEOUT, max_report=MAX_TOTAL)).check(inventory)
    store.write(inventory)
    return secret_scan


validate_capture = CaptureStore.validate


def run(args):
    if args.command in ('verify', 'check', 'read'):
        if not args.capture:
            invalid('This operation requires --capture')
        manifest = validate_capture(args.capture)
        if args.command == 'verify':
            return {'outcome': 'verified', 'capture_id': manifest['capture_id'], 'coverage': manifest['coverage'], 'gaps': manifest['gaps']}
        if args.command == 'read':
            return CaptureStore.read(args.capture, manifest, args.side, args.path)
        observation = verified_observation(Path(manifest['repo']), manifest['mode'], manifest['requested_base'],
                                            [Path(row['path']) for row in manifest['authorities']])[0]
        scope_drift = observation['capture_id'] != manifest['capture_id']
        dirty_checkout = manifest['mode'] != 'wip' and bool(observation['worktree_notice'])
        drift = scope_drift or dirty_checkout
        return {'outcome': 'drift' if drift else 'current', 'capture_id': manifest['capture_id'],
                'current_id': observation['capture_id'], 'coverage': manifest['coverage'],
                'fresh_review_required': drift, 'selected_scope_changed': scope_drift,
                'checkout_has_uncommitted_changes': dirty_checkout, 'full_scope_review_complete': False}
    if not args.repo or not args.mode or not args.out:
        invalid('Capture requires --repo, --mode, and --out')
    if args.mode != 'wip' and not args.base:
        invalid('branch and since require --base')
    if args.mode == 'wip' and args.base:
        invalid('WIP does not accept --base')
    repo = args.repo.resolve(strict=True)
    out = args.out.absolute()
    if out == repo or repo in out.parents:
        invalid('Capture output must be outside the repository to avoid becoming WIP input')
    if args.preview:
        preview = Git(repo).preview(CaptureScope(args.mode, args.base, tuple(args.authority)))
        return {**preview, 'out': str(out), 'effects': 'none'}
    manifest, blobs, diff, history = verified_observation(repo, args.mode, args.base, args.authority)
    secret_scan = write_capture(out, manifest, blobs, diff, history)
    return {'outcome': 'captured', 'capture': str(out), 'capture_id': manifest['capture_id'],
            'coverage': manifest['coverage'], 'gaps': manifest['gaps'], 'empty': manifest['empty'],
            'worktree_notice': manifest['worktree_notice'], 'secret_scan': secret_scan, 'semantic_review': 'required'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['capture', 'verify', 'check', 'read'])
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--mode', choices=['branch', 'since', 'wip'])
    parser.add_argument('--base')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--authority', action='append', default=[], type=Path)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--side', choices=['before', 'after'], default='after')
    parser.add_argument('--path')
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps(result, sort_keys=True))
        return 3 if result['outcome'] == 'drift' else 0
    except Failure as error:
        print(json.dumps(error.result, sort_keys=True))
        return {'conflict': 3, 'corrupt': 4, 'unsupported': 5, 'secret_detected': 6}.get(error.result['outcome'], 2)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'outcome': 'unavailable', 'message': str(error)}))
        return 5


if __name__ == '__main__':
    sys.exit(main())
