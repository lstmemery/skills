"""Runs in the container, verifies confinement, then executes the offline worker."""
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path


class CheckFailed(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
    mounts = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        mounts[fields[4]] = fields[5].split(',')
    if set(mounts) != set(expected['mounts']):
        raise CheckFailed('mount-layout')
    if any('ro' not in mounts[path] for path in expected['readonly_mounts']):
        raise CheckFailed('readonly-mounts')
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
