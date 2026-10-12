"""Shared capture format, failures, hashes, and bounded regular-file reads."""
import hashlib
import json
import os
import stat

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
