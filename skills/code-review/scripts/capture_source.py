"""Git policy and safe collection of fixed source observations."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess
from typing import Protocol

from capture_common import (
    Failure, MAX_FILE, MAX_FILES, MAX_TOTAL, VERSION,
    bounded_file, digest, encoded, invalid, is_text,
)


@dataclass(frozen=True)
class CaptureScope:
    mode: str
    base: str | None
    authorities: tuple[Path, ...]


class CaptureSource(Protocol):
    def observe(self, scope: CaptureScope) -> tuple:
        """Return manifest, blobs, diff, and history for one source observation."""
        ...


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

    def observe(self, scope: CaptureScope):
        repo = self.repo
        mode, base, authorities = scope.mode, scope.base, scope.authorities
        self.commands = []
        git = self
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

    def preview(self, scope: CaptureScope):
        head = self.resolve('HEAD')
        base = self.resolve(scope.base) if scope.base else head
        untracked = [os.fsdecode(name) for name in self.call('ls-files', '--others', '--exclude-standard', '-z').split(b'\x00') if name] if scope.mode == 'wip' else []
        paths = self.call('diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z',
                         *([base + '...' + head] if scope.mode == 'branch' else [base, head] if scope.mode == 'since' else [head]), '--')
        return {'outcome': 'preview', 'mode': scope.mode, 'head': head, 'base': base,
                'changed': [os.fsdecode(p) for p in paths.split(b'\x00') if p], 'untracked': untracked,
                'authorities': [str(p.absolute()) for p in scope.authorities]}


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
