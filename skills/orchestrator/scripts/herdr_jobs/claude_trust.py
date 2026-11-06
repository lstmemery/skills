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


def _project_members(source):
    """Locate the top-level projects object: (object start, object end, [(name, start, end)])."""
    decoder = json.JSONDecoder()
    position = 1 if source.startswith("\ufeff") else 0

    def skip(index):
        while source[index] in " \t\r\n":
            index += 1
        return index

    def members(start):
        index = skip(start + 1)
        while source[index] != "}":
            key_start = index
            key, end = decoder.raw_decode(source, index)
            index = skip(source.index(":", end) + 1)
            _, value_end = decoder.raw_decode(source, index)
            yield key, key_start, index, value_end
            index = skip(value_end)
            if source[index] == ",":
                index = skip(index + 1)

    for key, _, value_start, value_end in members(skip(position)):
        if key == "projects" and source[value_start] == "{":
            return value_start, value_end, [(name, start, end) for name, start, _, end in members(value_start)]
    raise JobError("invalid_input", "Claude projects object could not be located")


def _without_projects(source, names):
    """Drop whole project members, keeping every other byte and the original separators."""
    start, end, members = _project_members(source)
    kept = [member for member in members if member[0] not in names]
    if not kept:
        return source[:start] + "{}" + source[end:]
    separator = source[members[0][2]:members[1][1]] if len(members) > 1 else ", "
    body = separator.join(source[first:last] for _, first, last in kept)
    return source[:members[0][1]] + body + source[members[-1][2]:]


def stale_paths(document, *, runs_before=None):
    """Orchestrator project paths whose directory is gone, or (opt-in) whose run date is old."""
    from .codex_trust import ORCHESTRATOR_ROOT, run_before
    projects = document.get("projects", {})
    if not isinstance(projects, dict):
        raise JobError("invalid_input", "Claude projects must be a JSON object")
    paths = []
    for name in projects:
        candidate = Path(name)
        if not candidate.is_absolute() or ".." in candidate.parts or not candidate.is_relative_to(ORCHESTRATOR_ROOT):
            continue
        if not candidate.resolve().is_relative_to(ORCHESTRATOR_ROOT):
            continue
        try:
            candidate.lstat()
            missing = False
        except FileNotFoundError:
            missing = True
        if missing or run_before(candidate, runs_before):
            paths.append(name)
    return sorted(paths)


def prune_trust(path=None, *, apply=False, runs_before=None):
    """Report (or with apply, remove) stale orchestrator project entries; other bytes are kept."""
    path = Path(path) if path is not None else config_path()
    if not apply:
        document = _parse(_read(path)) if path.exists() else {}
        paths = stale_paths(document, runs_before=runs_before)
        return {"dry_run": True, "count": len(paths), "paths": paths, "backup": None}
    with _locked(path):
        original = _read(path)
        document = _parse(original) if original else {}
        paths = stale_paths(document, runs_before=runs_before)
        backup = None
        if paths:
            source = original.decode("utf-8")
            replacement = _without_projects(source, set(paths)).encode("utf-8")
            expected = deepcopy(document)
            for name in paths:
                del expected["projects"][name]
            if _parse(replacement) != expected:
                raise JobError("invalid_input", "Claude prune could not preserve the JSON structure")
            descriptor, backup = tempfile.mkstemp(prefix=path.name + ".orchestrator-backup-", dir=path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            if _read(path) != original:
                raise JobError("conflict", "Claude config changed outside the managed trust lock; retry prune")
            atomic_bytes(path, replacement)
            backup = str(backup)
        return {"dry_run": False, "count": len(paths), "paths": paths, "backup": backup}
