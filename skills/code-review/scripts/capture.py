#!/usr/bin/env python3
"""Capture immutable review inputs and check whether their source has drifted."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

VERSION = 1
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 20000


class Failure(ValueError):
    def __init__(self, outcome, message, **details):
        super().__init__(message)
        self.result = {'outcome': outcome, 'message': message, **details}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()


def invalid(message, **details):
    raise Failure('invalid_input', message, **details)


def bounded_file(path, limit=MAX_FILE):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Failure('unsupported', 'Not a regular file', path=str(path))
        if info.st_size > limit:
            raise Failure('unsupported', 'File exceeds capture bound', path=str(path), limit=limit)
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise Failure('conflict', 'File grew beyond capture bound', path=str(path))
        after = os.fstat(stream.fileno())
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
            raise Failure('conflict', 'File changed while reading', path=str(path))
        return data


def is_text(data):
    if b'\x00' in data:
        return False
    try:
        data.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


class Git:
    def __init__(self, repo):
        self.repo = repo
        self.commands = []
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                                GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')

    def call(self, *args, allowed=(0,)):
        argv = ['git', '--no-pager', '-c', 'core.fsmonitor=false', '-C', str(self.repo), *args]
        self.commands.append(argv)
        result = subprocess.run(argv, env=self.environment, capture_output=True, timeout=30)
        if result.returncode not in allowed:
            raise Failure('git_error', 'Git command failed', argv=argv, exit_code=result.returncode,
                          stderr=os.fsdecode(result.stderr[:4000]))
        if len(result.stdout) > MAX_TOTAL:
            raise Failure('unsupported', 'Git output exceeds capture bound', argv=argv, limit=MAX_TOTAL)
        return result.stdout

    def ignored(self, path):
        result = self.call('check-ignore', '--no-index', '--', path, allowed=(0, 1))
        return bool(result)

    def resolve(self, ref):
        if not ref or '\x00' in ref:
            invalid('A nonempty ref is required')
        return self.call('rev-parse', '--verify', '--end-of-options', ref + '^{commit}').strip().decode('ascii')


class Collector:
    def __init__(self):
        self.blobs = {}
        self.total = 0

    def blob(self, data):
        sha = digest(data)
        if sha not in self.blobs:
            self.total += len(data)
            if self.total > MAX_TOTAL:
                raise Failure('unsupported', 'Capture exceeds total byte bound', limit=MAX_TOTAL)
            self.blobs[sha] = data
        return sha

    def entry(self, mode, data=None, **fields):
        result = {'mode': mode, **fields}
        if data is not None:
            result.update(sha256=self.blob(data), size=len(data), text=is_text(data))
        return result


def tree(git, oid, collector):
    entries = {}
    rows = git.call('ls-tree', '-rz', '--full-tree', oid).split(b'\x00')
    if len(rows) > MAX_FILES + 1:
        raise Failure('unsupported', 'Tree exceeds file count bound')
    for row in rows:
        if not row:
            continue
        metadata, filename = row.split(b'\t', 1)
        mode, kind, object_id = metadata.decode('ascii').split()
        name = os.fsdecode(filename)
        if kind == 'commit':
            entries[name] = collector.entry(mode, object_id=object_id, gap='submodule contents not captured')
            continue
        size = int(git.call('cat-file', '-s', object_id))
        if size > MAX_FILE:
            entries[name] = collector.entry(mode, object_id=object_id, size=size, gap='file exceeds capture bound')
            continue
        data = git.call('cat-file', 'blob', object_id)
        entry = collector.entry(mode, data, object_id=object_id)
        if mode == '120000':
            entry['gap'] = 'symlink target recorded; target not followed'
        elif not entry['text']:
            entry['gap'] = 'binary content captured; requires a suitable reviewer'
        entries[name] = entry
    return entries


def path_inside(repo, name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        invalid('Invalid repository path', path=name)
    current = repo
    for part in path.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise Failure('unsupported', 'Ancestor is a symlink', path=name)
    return repo / path


def work_entry(repo, name, collector, previous=None):
    try:
        path = path_inside(repo, name)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return collector.entry('120000', os.fsencode(os.readlink(path)), gap='symlink target recorded; target not followed')
        if stat.S_ISDIR(info.st_mode):
            return collector.entry('160000' if previous and previous['mode'] == '160000' else 'directory',
                                   gap='directory/submodule contents not captured')
        if not stat.S_ISREG(info.st_mode):
            return collector.entry('special', gap='nonregular content not captured')
        mode = '100755' if info.st_mode & stat.S_IXUSR else '100644'
        data = bounded_file(path)
        entry = collector.entry(mode, data)
        if not entry['text']:
            entry['gap'] = 'binary content captured; requires a suitable reviewer'
        return entry
    except FileNotFoundError:
        return None
    except PermissionError:
        return collector.entry('unreadable', gap='permission denied')
    except Failure as error:
        if error.result['outcome'] != 'unsupported':
            raise
        return collector.entry('unsupported', gap=error.result['message'])


def untracked_paths(repo, git, tracked):
    """Find untracked filesystem entries Git's regular-file listing omits."""
    found = set(os.fsdecode(name) for name in git.call(
        'ls-files', '--others', '--exclude-standard', '-z').split(b'\x00') if name)
    stack = [repo]
    visited = 0
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise Failure('unsupported', 'Cannot inspect repository entry', path=str(directory), detail=str(error)) from error
        for entry in entries:
            if entry.name == '.git' and directory == repo:
                continue
            visited += 1
            if visited > MAX_FILES * 4:
                raise Failure('unsupported', 'Repository filesystem exceeds entry bound')
            relative = os.fsdecode(Path(entry.path).relative_to(repo))
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
                continue
            if relative in tracked or git.ignored(relative):
                continue
            found.add(relative)
    return sorted(found)


def content_identity(entry):
    if entry is None:
        return None
    return {key: entry[key] for key in ('mode', 'sha256', 'size', 'gap', 'object_id') if key in entry and
            (key != 'object_id' or 'sha256' not in entry)}


def authority_records(paths, collector):
    result = []
    for path in paths:
        absolute = path.absolute()
        try:
            if absolute.is_symlink() or any(parent.is_symlink() for parent in absolute.parents):
                raise Failure('unsupported', 'Authority symlinks are not followed')
            data = bounded_file(absolute)
            entry = collector.entry('authority', data, path=str(absolute))
            if not entry['text']:
                entry['gap'] = 'authority is not UTF-8 text'
        except (OSError, Failure) as error:
            message = error.result['message'] if isinstance(error, Failure) else str(error)
            entry = {'path': str(absolute), 'gap': message}
        result.append(entry)
    return result


def observe(repo, mode, base, authorities):
    git = Git(repo)
    top = Path(os.fsdecode(git.call('rev-parse', '--show-toplevel')).strip()).resolve()
    if top != repo:
        invalid('--repo must name the repository root', root=str(top))
    head = git.resolve('HEAD')
    base_oid = git.resolve(base) if mode in ('branch', 'since') else head
    effective = base_oid
    if mode == 'branch':
        bases = git.call('merge-base', '--all', base_oid, head).splitlines()
        if len(bases) != 1:
            raise Failure('unsupported', 'Branch review requires one merge base')
        effective = bases[0].decode('ascii')
    collector = Collector()
    before = tree(git, effective, collector)
    after = tree(git, head, collector)
    untracked = []
    index_digest = None
    status = os.fsdecode(git.call('status', '--porcelain=v1', '-z', '--untracked-files=all'))
    if mode == 'wip':
        index = git.call('ls-files', '--stage', '-z')
        index_digest = digest(index)
        names = set(after)
        for row in index.split(b'\x00'):
            if not row:
                continue
            metadata, name = row.split(b'\t', 1)
            if metadata.split()[2] != b'0':
                raise Failure('unsupported', 'Resolve unmerged index entries before WIP capture')
            names.add(os.fsdecode(name))
        untracked = untracked_paths(repo, git, set(after))
        names.update(untracked)
        if len(names) > MAX_FILES:
            raise Failure('unsupported', 'WIP exceeds file count bound')
        working = {}
        for name in sorted(names):
            entry = work_entry(repo, name, collector, after.get(name))
            if entry is not None:
                working[name] = entry
        after = working
    changed = [name for name in sorted(set(before) | set(after))
               if content_identity(before.get(name)) != content_identity(after.get(name))]
    # Index-only changes can be meaningful even when the net working tree equals HEAD.
    diff_args = ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--full-index', '--no-color']
    if mode == 'wip':
        diff = git.call(*diff_args, head, '--')
    else:
        diff = git.call(*diff_args, effective, head, '--')
    if len(diff) > MAX_TOTAL:
        raise Failure('unsupported', 'Diff exceeds capture bound')
    history = git.call('log', '--format=%H %s', f'{base_oid}..{head}', '--') if mode != 'wip' else b''
    gaps = []
    for name in sorted(set(changed) | set(untracked)):
        for side, entries in [('before', before), ('after', after)]:
            entry = entries.get(name)
            if entry and 'gap' in entry:
                gaps.append({'path': name, 'side': side, 'reason': entry['gap']})
    authority = authority_records(authorities, collector)
    gaps.extend({'path': row['path'], 'side': 'authority', 'reason': row['gap']} for row in authority if 'gap' in row)
    manifest = {'version': VERSION, 'repo': str(repo), 'mode': mode, 'requested_base': base,
                'head': head, 'base': base_oid, 'effective_base': effective,
                'before': before, 'after': after, 'untracked': sorted(untracked),
                'changed': changed, 'index_digest': index_digest,
                'authorities': authority, 'gaps': gaps,
                'coverage': 'incomplete' if gaps else 'complete',
                'empty': not changed and not untracked and not diff,
                'diff_sha256': digest(diff), 'history_sha256': digest(history)}
    fingerprint = digest(encoded(manifest))
    manifest.update(capture_id=fingerprint, commands=git.commands,
                    worktree_notice='; '.join(filter(None, status.split('\0'))) if mode != 'wip' else None)
    return manifest, collector.blobs, diff, history


def verified_observation(repo, mode, base, authorities):
    first = observe(repo, mode, base, authorities)
    second = observe(repo, mode, base, authorities)
    if first[0]['capture_id'] != second[0]['capture_id']:
        raise Failure('conflict', 'Inputs changed during capture; no mixed capture was written')
    return second


def write_capture(out, manifest, blobs, diff, history):
    out = out.absolute()
    if out.exists() or out.is_symlink():
        raise Failure('conflict', 'Capture destination already exists', path=str(out))
    if not out.parent.is_dir() or any(parent.is_symlink() for parent in [out.parent, *out.parent.parents]):
        invalid('Capture parent must be an existing directory without symlinks')
    stage = Path(tempfile.mkdtemp(prefix='.review-', dir=out.parent))
    try:
        (stage / 'blobs').mkdir()
        for sha, data in blobs.items():
            (stage / 'blobs' / sha).write_bytes(data)
        (stage / 'diff.patch').write_bytes(diff)
        (stage / 'commits.txt').write_bytes(history)
        (stage / 'manifest.json').write_bytes(encoded(manifest) + b'\n')
        # Reserve destination without replacing another caller's capture.
        out.mkdir()
        for name in ['blobs', 'diff.patch', 'commits.txt', 'manifest.json']:
            os.replace(stage / name, out / name)
        (out / 'COMPLETE').write_text(manifest['capture_id'] + '\n')
    finally:
        shutil.rmtree(stage)


def validate_capture(directory):
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


def run(args):
    if args.command in ('verify', 'check', 'read'):
        if not args.capture:
            invalid('This operation requires --capture')
        manifest = validate_capture(args.capture)
        if args.command == 'verify':
            return {'outcome': 'verified', 'capture_id': manifest['capture_id'], 'coverage': manifest['coverage'], 'gaps': manifest['gaps']}
        if args.command == 'read':
            if args.path is None or args.path not in manifest[args.side]:
                invalid('Path is not present on selected side of capture')
            entry = manifest[args.side][args.path]
            if 'sha256' not in entry or not entry.get('text'):
                raise Failure('unsupported', 'Use an appropriate reader for this capture entry', entry=entry)
            return {'outcome': 'content', 'capture_id': manifest['capture_id'], 'side': args.side,
                    'path': args.path, 'text': bounded_file(args.capture / 'blobs' / entry['sha256']).decode('utf-8')}
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
        git = Git(repo)
        head = git.resolve('HEAD')
        base = git.resolve(args.base) if args.base else head
        untracked = [os.fsdecode(name) for name in git.call('ls-files', '--others', '--exclude-standard', '-z').split(b'\x00') if name] if args.mode == 'wip' else []
        paths = git.call('diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z',
                         *( [base + '...' + head] if args.mode == 'branch' else [base, head] if args.mode == 'since' else [head]), '--')
        return {'outcome': 'preview', 'mode': args.mode, 'head': head, 'base': base,
                'changed': [os.fsdecode(p) for p in paths.split(b'\x00') if p], 'untracked': untracked,
                'authorities': [str(p.absolute()) for p in args.authority], 'out': str(out), 'effects': 'none'}
    manifest, blobs, diff, history = verified_observation(repo, args.mode, args.base, args.authority)
    write_capture(out, manifest, blobs, diff, history)
    return {'outcome': 'captured', 'capture': str(out), 'capture_id': manifest['capture_id'],
            'coverage': manifest['coverage'], 'gaps': manifest['gaps'], 'empty': manifest['empty'],
            'worktree_notice': manifest['worktree_notice'], 'semantic_review': 'required'}


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
        return {'conflict': 3, 'corrupt': 4, 'unsupported': 5}.get(error.result['outcome'], 2)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'outcome': 'unavailable', 'message': str(error)}))
        return 5


if __name__ == '__main__':
    sys.exit(main())
