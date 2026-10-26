"""Runs in the container, verifies confinement, then executes the offline worker."""
import hashlib
import json
import os
import re
import runpy
import stat
import sys
from pathlib import Path


class CheckFailed(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_identity(path):
    info = os.stat(path)
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_rdev]


def mount_field(value):
    # mountinfo escapes whitespace and backslashes with octal sequences.
    if re.search(r'\\(?!040|011|012|134)', value):
        raise CheckFailed('mount-record')
    return re.sub(r'\\(040|011|012|134)', lambda match: chr(int(match[1], 8)), value)


def read_mounts():
    mounts = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        separator = fields.index('-')
        if separator < 6 or len(fields) != separator + 4:
            raise CheckFailed('mount-record')
        path = mount_field(fields[4])
        root = mount_field(fields[3])
        if not path.startswith('/') or not root.startswith('/'):
            raise CheckFailed('mount-record')
        if re.fullmatch(r'[0-9]+:[0-9]+', fields[2]) is None:
            raise CheckFailed('mount-record')
        mounts.append({'path': path, 'device': fields[2], 'root': root,
                       'options': fields[5].split(','), 'filesystem': fields[separator + 1],
                       'source': mount_field(fields[separator + 2])})
    if not mounts:
        raise CheckFailed('mount-record')
    return mounts


def verify_mounts(expected):
    records = read_mounts()
    mounts = {mount['path']: mount for mount in records}
    if len(mounts) != len(records):
        raise CheckFailed('mount-record')
    if set(mounts) != set(expected['mounts']):
        raise CheckFailed('mount-layout')
    if any('ro' not in mounts[path]['options'] for path in expected['readonly_mounts']):
        raise CheckFailed('readonly-mounts')
    policies = [set(expected[key]) for key in ('fresh_mounts', 'bind_mounts', 'data_mounts')]
    if set.union(*policies) != set(mounts) or sum(map(len, policies)) != len(mounts):
        raise CheckFailed('mount-policy')
    fresh_devices = set()
    for path, (filesystem, source, root) in expected['fresh_mounts'].items():
        mount = mounts[path]
        if (mount['filesystem'], mount['source'], mount['root']) != (filesystem, source, root):
            raise CheckFailed('mount-filesystem')
        if mount['device'] in expected['host_devices'] or mount['device'] in fresh_devices:
            raise CheckFailed('mount-provenance')
        fresh_devices.add(mount['device'])
    for path, provenance in expected['bind_mounts'].items():
        mount = mounts[path]
        if any(mount[key] != provenance[key] for key in ('filesystem', 'source', 'device', 'root')):
            raise CheckFailed('mount-provenance')
        if file_identity(path) != provenance['identity']:
            raise CheckFailed('mount-identity')
    for path in expected['data_mounts']:
        mount = mounts[path]
        if (mount['filesystem'], mount['source'], mount['device']) != ('tmpfs', 'tmpfs', mounts['/']['device']):
            raise CheckFailed('mount-provenance')
        if re.fullmatch(r'/bindfile[A-Za-z0-9]+//deleted', mount['root']) is None:
            raise CheckFailed('mount-provenance')
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise CheckFailed('mount-identity')
    return True


def verify(expected):
    if os.getuid() != 65534 or os.getgid() != 65534:
        raise CheckFailed('uid')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if status['NoNewPrivs'].strip() != '1':
        raise CheckFailed('no-new-privileges')
    if any(int(status[key], 16) for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')):
        raise CheckFailed('capabilities')
    if set(os.environ) != {'PATH', 'HOME', 'LANG', 'LC_ALL', 'PWD'}:
        raise CheckFailed('environment-keys')
    if os.environ['HOME'] != '/home/worker' or os.environ['PATH'] != '/usr/bin' or os.environ['PWD'] != '/out':
        raise CheckFailed('environment-values')
    for namespace, host_namespace in expected['host_namespaces'].items():
        if os.readlink('/proc/self/ns/' + namespace) == host_namespace:
            raise CheckFailed('namespaces')
    if set(Path('/').iterdir()) != {Path(p) for p in expected['root_entries']}:
        raise CheckFailed('root-layout')
    for path in ('/root', '/run', '/sys', '/etc', '/home/deadhand', '/home/worker/.ssh'):
        if Path(path).exists():
            raise CheckFailed('host-paths')
    # A fresh PID namespace keeps host processes, root paths and descriptors out.
    pids = [p.name for p in Path('/proc').iterdir() if p.name.isdecimal()]
    if len(pids) > 3:
        raise CheckFailed('host-processes')
    fds = [p for p in Path('/proc/self/fd').iterdir() if p.exists()]
    if any(int(p.name) > 2 for p in fds):
        raise CheckFailed('descriptors')
    verify_mounts(expected)
    for name, path in {'snapshot': '/input/snapshot.json', 'worker': '/worker.py',
                       'profile': '/profile.json', 'bootstrap': '/bootstrap.py'}.items():
        if digest(path) != expected['attestation'][name + '_sha256']:
            raise CheckFailed('input-digests')
    return True


def main():
    try:
        expected = json.loads(Path('/expectation.json').read_bytes())
        if not verify(expected):
            raise ValueError('refused')
    except CheckFailed as error:
        sys.stdout.write(json.dumps({'refused': 'confinement-unverified', 'check': str(error)}) + '\n')
        return 70
    except (OSError, ValueError, KeyError):
        # Never emit the exception, environment, paths or source bytes.
        sys.stdout.write('{"refused":"confinement-unverified"}\n')
        return 70
    sys.stdout.write(json.dumps({'attestation': expected['attestation']}, sort_keys=True) + '\n')
    sys.stdout.flush()
    # The worker cannot execute until every check above passes in this namespace.
    sys.argv = ['/worker.py']
    runpy.run_path('/worker.py', run_name='__main__')
    return 0


if __name__ == '__main__':
    sys.exit(main())
