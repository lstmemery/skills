"""Exact-cwd Claude Code trust, verified against the 2.1.318 installed package."""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from .records import JobError, atomic_bytes, parse_json


def config_path():
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home())
    if not root.is_absolute():
        raise JobError("invalid_input", "CLAUDE_CONFIG_DIR must be absolute for managed trust")
    if os.environ.get("CLAUDE_CODE_CUSTOM_OAUTH_URL"):
        raise JobError("unavailable_capability", "custom OAuth Claude config requires a verified trust adapter")
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    # The native path selector can prefer this legacy file. Do not write a
    # different config while a legacy backend might be active.
    if (config_dir / ".config.json").exists():
        raise JobError("unavailable_capability", "legacy Claude .config.json requires a verified trust adapter")
    return root / ".claude.json"


@contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path.with_name(path.name + ".orchestrator.lock"),
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _read(path):
    if path.is_symlink():
        raise JobError("invalid_input", "Claude config must not be a symlink")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b""
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise JobError("invalid_input", "Claude config must be a regular file")
        return stream.read()


def _parse(raw):
    try:
        value = parse_json(raw)
    except JobError:
        # Native config can contain account/MCP data; never include parser
        # diagnostics, keys, or values in launcher errors.
        raise JobError("invalid_input", "Claude config is malformed; repair it before launch") from None
    if not isinstance(value, dict):
        raise JobError("invalid_input", "Claude config must be a JSON object")
    return value


def _edit(source, keys):
    """Edit only the target value or insert members, preserving other bytes."""
    decoder = json.JSONDecoder()
    position = 1 if source.startswith("\ufeff") else 0
    while source[position] in " \t\r\n":
        position += 1
    for depth, target in enumerate(keys):
        position += 1  # opening brace of the current object
        while True:
            while source[position] in " \t\r\n":
                position += 1
            if source[position] == "}":
                value = True
                for key in reversed(keys[depth + 1:]):
                    value = {key: value}
                member = json.dumps(target, ensure_ascii=False) + ": " + json.dumps(value, ensure_ascii=False)
                return source[:position] + member + source[position:]
            key, end = decoder.raw_decode(source, position)
            position = source.index(":", end) + 1
            while source[position] in " \t\r\n":
                position += 1
            _, end = decoder.raw_decode(source, position)
            if key == target:
                if depth == len(keys) - 1:
                    return source[:position] + "true" + source[end:]
                break
            position = end
            while source[position] in " \t\r\n":
                position += 1
            if source[position] == ",":
                position += 1
                continue
            value = True
            for key in reversed(keys[depth + 1:]):
                value = {key: value}
            member = ", " + json.dumps(target, ensure_ascii=False) + ": " + json.dumps(value, ensure_ascii=False)
            return source[:position] + member + source[position:]
    raise JobError("invalid_input", "Claude trust target could not be located")


def trust_cwd(cwd, path=None):
    """Persist only exact canonical cwd trust; never report config contents."""
    path = Path(path) if path is not None else config_path()
    cwd = str(Path(cwd).resolve(strict=True))
    if not Path(cwd).is_dir():
        raise JobError("invalid_input", "Claude worker cwd must be a directory")
    with _locked(path):
        original = _read(path)
        document = _parse(original) if original or path.exists() else {}
        projects = document.get("projects", {})
        if not isinstance(projects, dict):
            raise JobError("invalid_input", "Claude projects must be a JSON object")
        project = projects.get(cwd, {})
        if not isinstance(project, dict):
            raise JobError("invalid_input", "Claude project must be a JSON object")
        if project.get("hasTrustDialogAccepted") is True:
            return {"changed": False, "config": str(path), "cwd": cwd, "backup": None}
        source = original.decode("utf-8") if original else "{}"
        replacement = _edit(source, ("projects", cwd, "hasTrustDialogAccepted")).encode("utf-8")
        expected = deepcopy(document)
        expected.setdefault("projects", {}).setdefault(cwd, {})["hasTrustDialogAccepted"] = True
        if _parse(replacement) != expected:
            raise JobError("invalid_input", "Claude trust edit could not preserve the JSON structure")
        descriptor, backup = tempfile.mkstemp(prefix=path.name + ".orchestrator-backup-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if _read(path) != original:
            raise JobError("conflict", "Claude config changed outside the managed trust lock; reconcile before launch")
        atomic_bytes(path, replacement)
        return {"changed": True, "config": str(path), "cwd": cwd, "backup": str(backup)}
