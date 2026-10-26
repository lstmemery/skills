"""Fail-closed rootless container. No credential mounts, network or host fallback."""
import fcntl
import functools
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .bootstrap import file_identity, read_mounts
from .errors import Refusal
from .process import capture
from .schema import MAX_BYTES, SCHEMA_BYTES, canonical, read_json, read_snapshot

HERE = Path(__file__).resolve().parent
PYTHON = Path('/usr/bin/python3').resolve()
BWRAP = Path('/usr/bin/bwrap')


def _trusted(path):
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise Refusal('runtime-unavailable') from None
    if not str(resolved).startswith('/usr/'):
        raise Refusal('runtime-refused')
    for item in [resolved, *resolved.parents]:
        info = item.stat()
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise Refusal('runtime-refused')
    return resolved


@functools.lru_cache(maxsize=1)
def _runtime():
    python = _trusted(PYTHON)
    _trusted(BWRAP)
    _trusted(Path('/usr/bin/ldd'))
    version = capture([str(python), '-I', '-S', '-c',
                       'import sysconfig; print(sysconfig.get_path("stdlib"))']).stdout.decode().strip()
    stdlib = _trusted(Path(version))
    extensions = sorted((stdlib / 'lib-dynload').glob('*.so'))
    dependencies = capture(['/usr/bin/ldd', str(python), *map(str, extensions)],
                           timeout=15, limit=1048576).stdout.decode('ascii')
    files = {python}
    for match in re.findall(r'(/[A-Za-z0-9_./+-]+)', dependencies):
        path = Path(match)
        if path.is_file() and not path.is_relative_to(stdlib):
            # Keep loader and SONAME paths as well as their resolved targets.
            _trusted(path)
            files.add(path)
    return stdlib, tuple(sorted(files))


def _sealed(content):
    # bwrap copies these sealed memory files to read-only mounts before exec.
    descriptor = os.memfd_create('audit-input', os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
    try:
        with os.fdopen(os.dup(descriptor), 'wb') as stream:
            stream.write(content)
        os.lseek(descriptor, 0, os.SEEK_SET)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _bind_provenance(path, host_mounts, *, descriptor=None):
    resolved = Path(path).resolve(strict=True)
    candidates = [mount for mount in host_mounts if resolved.is_relative_to(mount['path'])]
    if not candidates:
        raise Refusal('host-mounts-unverified')
    depth = max(len(Path(mount['path']).parts) for mount in candidates)
    sources = {(mount['filesystem'], mount['source'], mount['device'],
                str(Path(mount['root']) / resolved.relative_to(mount['path'])))
               for mount in candidates if len(Path(mount['path']).parts) == depth}
    if len(sources) != 1:
        raise Refusal('host-mounts-unverified')
    filesystem, source, device, root = sources.pop()
    return {'filesystem': filesystem, 'source': source, 'device': device, 'root': root,
            'identity': file_identity(resolved if descriptor is None else descriptor)}


def launch_profile(snapshot, worker, output, *, timeout=30):
    snapshot = canonical(read_snapshot(snapshot))
    if type(worker) is not bytes or len(worker) > MAX_BYTES:
        raise Refusal('worker-size-refused')
    _trusted(BWRAP)
    stdlib, files = _runtime()
    bootstrap = (HERE / 'bootstrap.py').read_bytes()
    profile = (HERE / 'profile.json').read_bytes()
    hashes = {'snapshot': snapshot, 'worker': worker, 'bootstrap': bootstrap,
              'profile': profile, 'schema': SCHEMA_BYTES,
              'collector': (HERE / 'collector.py').read_bytes()}
    # Bind the complete trusted collector/validation/launch implementation too.
    hashes['implementation'] = b''.join(p.name.encode() + b'\0' + p.read_bytes()
                                        for p in sorted(HERE.glob('*.py')))
    attestation = {'profile': 'isolated-audit-v1', 'verified': True,
                   **{name + '_sha256': hashlib.sha256(raw).hexdigest() for name, raw in hashes.items()}}
    args = [str(BWRAP), '--unshare-all', '--unshare-user', '--uid', '65534', '--gid', '65534',
            '--cap-drop', 'ALL', '--disable-userns', '--assert-userns-disabled',
            '--die-with-parent', '--new-session', '--clearenv',
            '--setenv', 'PATH', '/usr/bin', '--setenv', 'HOME', '/home/worker',
            '--setenv', 'LANG', 'C', '--setenv', 'LC_ALL', 'C',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--dir', '/home/worker', '--dir', '/input']
    mounts = {'/', '/proc', '/dev', '/dev/pts', '/dev/null', '/dev/zero', '/dev/full',
              '/dev/random', '/dev/urandom', '/dev/tty', '/tmp', '/out'}
    readonly = {'/'}
    fresh = {'/': ['tmpfs', 'tmpfs', '/newroot'], '/tmp': ['tmpfs', 'tmpfs', '/'],
             '/dev': ['tmpfs', 'tmpfs', '/'], '/dev/pts': ['devpts', 'devpts', '/'],
             '/proc': ['proc', 'proc', '/']}
    args += ['--ro-bind', str(stdlib), str(stdlib)]
    mounts.add(str(stdlib))
    readonly.add(str(stdlib))
    # Exclude machine-wide third-party packages and startup customizations.
    packages = stdlib / 'site-packages'
    if packages.exists():
        args += ['--tmpfs', str(packages), '--remount-ro', str(packages)]
        mounts.add(str(packages))
        readonly.add(str(packages))
        fresh[str(packages)] = ['tmpfs', 'tmpfs', '/']
    for path in files:
        args += ['--ro-bind', str(path), str(path)]
        mounts.add(str(path))
        readonly.add(str(path))
    args += ['--symlink', str(PYTHON), '/usr/bin/python3']
    blobs = {'/input/snapshot.json': snapshot, '/worker.py': worker,
             '/bootstrap.py': bootstrap, '/profile.json': profile}
    mounts.update(blobs)
    readonly.update(blobs)
    mounts.add('/expectation.json')
    readonly.add('/expectation.json')
    roots = {'/usr', '/proc', '/dev', '/tmp', '/home', '/input', '/out',
             '/worker.py', '/bootstrap.py', '/profile.json', '/expectation.json'}
    roots.update('/' + p.parts[1] for p in files)
    try:
        host_mounts = read_mounts()
        devices = [Path('/dev') / name for name in ('null', 'zero', 'full', 'random', 'urandom', 'tty')]
        bind_mounts = {str(path): _bind_provenance(path, host_mounts)
                       for path in (stdlib, *files, *devices)}
    except (OSError, ValueError):
        raise Refusal('host-mounts-unverified') from None
    expectation = {'attestation': attestation,
                   'host_namespaces': {name: os.readlink('/proc/self/ns/' + name)
                                       for name in ('user', 'mnt', 'pid', 'net', 'ipc', 'uts')},
                   'mounts': sorted(mounts), 'readonly_mounts': sorted(readonly),
                   'fresh_mounts': fresh,
                   'bind_mounts': bind_mounts,
                   'data_mounts': sorted([*blobs, '/expectation.json']),
                   'host_devices': sorted({mount['device'] for mount in host_mounts}),
                   'root_entries': sorted(roots)}
    if any(re.fullmatch(name + r':\[[0-9]+\]', value) is None
           for name, value in expectation['host_namespaces'].items()):
        raise Refusal('host-confinement-unverified')
    descriptors = []
    try:
        # Never mount an existing directory; pin the fresh directory by descriptor.
        output = Path(output).absolute()
        if output.parent.resolve() != output.parent:
            raise Refusal('output-path-refused')
        output.mkdir(mode=0o700)
        output_fd = os.open(output, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
        descriptors.append(output_fd)
        bind_mounts['/out'] = _bind_provenance(output, host_mounts, descriptor=output_fd)
        attestation['confinement_sha256'] = hashlib.sha256(json.dumps(
            {key: value for key, value in expectation.items() if key != 'attestation'},
            sort_keys=True).encode()).hexdigest()
        blobs['/expectation.json'] = json.dumps(expectation, sort_keys=True).encode()
        for destination, content in blobs.items():
            descriptor = _sealed(content)
            descriptors.append(descriptor)
            args += ['--ro-bind-data', str(descriptor), destination]
        args += ['--bind-fd', str(output_fd), '/out', '--remount-ro', '/', '--chdir', '/out',
                 '--', str(PYTHON), '-I', '-S', '/bootstrap.py']
        try:
            result = capture(args, pass_fds=tuple(descriptors), timeout=timeout, limit=1048576)
        except Refusal:
            raise Refusal('launch-unverified-or-worker-failed') from None
        first, separator, stdout = result.stdout.partition(b'\n')
        if not separator or read_json(first) != {'attestation': attestation}:
            raise Refusal('confinement-unverified')
        try:
            return {'attestation': attestation, 'stdout': stdout.decode('utf-8'),
                    'stderr': result.stderr.decode('utf-8'), 'exit_code': 0}
        except UnicodeError:
            raise Refusal('worker-encoding-refused') from None
    except OSError:
        raise Refusal('launch-unavailable') from None
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
