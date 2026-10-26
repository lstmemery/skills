"""Bounded, non-streaming trusted subprocess I/O. No raw diagnostics escape."""
import os
import selectors
import signal
import subprocess
import time

from .errors import Refusal

CLEAN_ENV = {'PATH': '/usr/bin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': '/nonexistent'}


def capture(argv, *, input_bytes=b'', timeout=5, limit=65536, pass_fds=()):
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=CLEAN_ENV, close_fds=True,
                                pass_fds=pass_fds, start_new_session=True)
    except OSError:
        raise Refusal('command-unavailable') from None
    streams = {proc.stdout: bytearray(), proc.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    pending = memoryview(input_bytes)
    try:
        with selectors.DefaultSelector() as selector:
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            if pending:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE)
            else:
                proc.stdin.close()
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Refusal('command-timeout')
                for key, _ in selector.select(remaining):
                    stream = key.fileobj
                    if stream is proc.stdin:
                        try:
                            pending = pending[os.write(stream.fileno(), pending[:4096]):]
                        except BrokenPipeError:
                            pending = memoryview(b'')
                        if not pending:
                            selector.unregister(stream)
                            stream.close()
                        continue
                    chunk = os.read(stream.fileno(), 4096)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    streams[stream].extend(chunk)
                    if sum(map(len, streams.values())) > limit:
                        raise Refusal('command-size-refused')
        try:
            code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise Refusal('command-timeout') from None
        if code:
            raise Refusal('command-failed')
        return subprocess.CompletedProcess(argv, code, bytes(streams[proc.stdout]),
                                           bytes(streams[proc.stderr]))
    finally:
        # Also reap descendants that retained a pipe or backgrounded themselves.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
