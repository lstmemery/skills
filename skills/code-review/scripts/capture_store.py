"""Capture destination checks, stage/rename/COMPLETE writes, and verification."""
import json
import os
from pathlib import Path
import shutil
import tempfile

from capture_common import Failure, MAX_TOTAL, VERSION, bounded_file, digest, encoded, invalid
from capture_payloads import PayloadInventory


class CaptureStore:
    def __init__(self, out):
        out = out.absolute()
        if out.exists() or out.is_symlink():
            raise Failure('conflict', 'Capture destination already exists', path=str(out))
        if not out.parent.is_dir() or any(parent.is_symlink() for parent in [out.parent, *out.parent.parents]):
            invalid('Capture parent must be an existing directory without symlinks')
        self._out = out

    def write(self, inventory: PayloadInventory):
        out = self._out
        payloads = inventory.payloads
        stage = Path(tempfile.mkdtemp(prefix='.review-', dir=out.parent))
        try:
            for _, relative, data in payloads:
                target = stage / relative
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(data)
            # Reserve destination without replacing another caller's capture.
            out.mkdir()
            for _, relative, _ in payloads:
                target = out / relative
                target.parent.mkdir(exist_ok=True)
                os.replace(stage / relative, target)
            (out / 'COMPLETE').write_text(inventory.capture_id + '\n')
        finally:
            shutil.rmtree(stage)

    @staticmethod
    def validate(directory):
        if directory.is_symlink():
            invalid('Capture directory must not be a symlink')
        try:
            manifest = json.loads(bounded_file(directory / 'manifest.json', MAX_TOTAL))
        except (ValueError, UnicodeError) as error:
            invalid('Invalid capture manifest', detail=str(error))
        required = {'version', 'repo', 'mode', 'requested_base', 'head', 'base', 'effective_base', 'before', 'after',
                    'untracked', 'changed', 'index_digest', 'authorities', 'gaps', 'coverage', 'empty',
                    'diff_sha256', 'history_sha256', 'capture_id', 'commands', 'worktree_notice'}
        if not isinstance(manifest, dict) or set(manifest) != required or manifest['version'] != VERSION:
            invalid('Unsupported capture manifest')
        core = {key: value for key, value in manifest.items() if key not in ('capture_id', 'commands', 'worktree_notice')}
        if digest(encoded(core)) != manifest['capture_id']:
            raise Failure('corrupt', 'Capture manifest digest mismatch')
        if bounded_file(directory / 'COMPLETE', 100).decode().strip() != manifest['capture_id']:
            raise Failure('corrupt', 'Capture completion marker mismatch')
        if not all(isinstance(manifest[key], dict) for key in ('before', 'after')) or not isinstance(manifest['authorities'], list):
            invalid('Invalid source inventory')
        if (directory / 'blobs').is_symlink():
            invalid('Capture blob directory is a symlink')
        for entry in [*manifest['before'].values(), *manifest['after'].values(), *manifest['authorities']]:
            if not isinstance(entry, dict):
                invalid('Invalid source entry')
            if 'sha256' in entry:
                sha = entry['sha256']
                if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
                    invalid('Invalid source digest')
                if digest(bounded_file(directory / 'blobs' / sha)) != sha:
                    raise Failure('corrupt', 'Captured source digest mismatch', sha256=sha)
        for name, field in [('diff.patch', 'diff_sha256'), ('commits.txt', 'history_sha256')]:
            if digest(bounded_file(directory / name, MAX_TOTAL)) != manifest[field]:
                raise Failure('corrupt', 'Capture artifact digest mismatch', path=name)
        return manifest

    @staticmethod
    def read(directory, manifest, side, path):
        if path is None or path not in manifest[side]:
            invalid('Path is not present on selected side of capture')
        entry = manifest[side][path]
        if 'sha256' not in entry or not entry.get('text'):
            raise Failure('unsupported', 'Use an appropriate reader for this capture entry', entry=entry)
        return {'outcome': 'content', 'capture_id': manifest['capture_id'], 'side': side,
                'path': path, 'text': bounded_file(directory / 'blobs' / entry['sha256']).decode('utf-8')}
