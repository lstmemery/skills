"""Scoped Codex project trust; serialize managed writers without rewriting TOML."""

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import tomllib

from .records import JobError, atomic_bytes


ORCHESTRATOR_ROOT = Path("/home/deadhand/.local/share/orchestrator")


def _tokens(source):
    """Locate TOML tokens; tomllib owns validation and decoded values."""
    position = 0
    while position < len(source):
        start = position
        char = source[position]
        if char in " \t\r":
            position += 1
            continue
        if char == "#":
            position = source.find("\n", position)
            if position == -1:
                return
            continue
        if char in "\"'":
            delimiter = char * (3 if source.startswith(char * 3, position) else 1)
            position += len(delimiter)
            while position < len(source):
                if char == '"' and source[position] == "\\":
                    position += 2
                elif source.startswith(delimiter, position):
                    position += len(delimiter)
                    if len(delimiter) == 3:
                        while position < len(source) and source[position] == char:
                            position += 1
                    break
                else:
                    position += 1
        elif char in "\n[]=.,{}":
            position += 1
        else:
            while position < len(source) and source[position] not in " \t\r\n[]=.,{}#\"'":
                position += 1
        yield source[start:position], start, position


def _key(tokens):
    parts = []
    for value, _, _ in tokens:
        if value == ".":
            continue
        if value.startswith(('"', "'")):
            parts.append(tomllib.loads("key = " + value)["key"])
        else:
            parts.append(value)
    return tuple(parts)


def _entries(source):
    """Return exact assignment/value spans and explicit table insertion points."""
    tokens = list(_tokens(source))
    entries, tables, removals = {}, {(): 0}, {}

    def inline_entries(key, start, end):
        if tokens[start][0] != "{":
            return
        index = start + 1
        while index < end - 1:
            if tokens[index][0] in (",", "\n"):
                index += 1
                continue
            key_start = index
            while tokens[index][0] != "=":
                index += 1
            child = key + _key(tokens[key_start:index])
            index += 1
            value_start = index
            depth = 0
            while index < end - 1:
                value = tokens[index][0]
                if value == "," and depth == 0:
                    break
                if value in ("[", "{"):
                    depth += 1
                elif value in ("]", "}"):
                    depth -= 1
                index += 1
            entries[child] = (tokens[value_start][1], tokens[index - 1][2])
            if index < end - 1 and tokens[index][0] == ",":
                removals[child] = (tokens[key_start][1], tokens[index][2])
            elif key_start > start + 1:
                previous = key_start - 1
                while tokens[previous][0] == "\n":
                    previous -= 1
                removals[child] = (tokens[previous][1], tokens[index - 1][2])
            else:
                removals[child] = (tokens[key_start][1], tokens[index - 1][2])
            inline_entries(child, value_start, index)
    table = ()
    index = 0
    while index < len(tokens):
        if tokens[index][0] == "\n":
            index += 1
            continue
        start = index
        if tokens[index][0] == "[":
            array = tokens[index + 1][0] == "["
            index += 2 if array else 1
            key_start = index
            while tokens[index][0] != "]":
                index += 1
            table = _key(tokens[key_start:index])
            index += 2 if array else 1
            end = tokens[index][2] if index < len(tokens) and tokens[index][0] == "\n" else tokens[index - 1][2]
            tables[table] = end
            removals[table] = (tokens[start][1], end)
            continue
        while tokens[index][0] != "=":
            index += 1
        key = table + _key(tokens[start:index])
        index += 1
        value_start = index
        depth = 0
        while index < len(tokens):
            value = tokens[index][0]
            if value == "\n" and depth == 0:
                break
            if value in ("[", "{"):
                depth += 1
            elif value in ("]", "}"):
                depth -= 1
            index += 1
        entries[key] = (tokens[value_start][1], tokens[index - 1][2])
        end = tokens[index][2] if index < len(tokens) else len(source)
        removals[key] = (tokens[start][1], end)
        inline_entries(key, value_start, index)
    return entries, tables, removals


def config_path():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"


def _parse(raw):
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise JobError("invalid_input", "Codex config.toml is malformed; repair it before launch") from error


def _same_toml_values(left, right):
    """Compare parsed TOML values, treating corresponding NaNs as equal."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_toml_values(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_toml_values(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and math.isnan(left):
        return math.isnan(right)
    return left == right


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
        raise JobError("invalid_input", "Codex config.toml must not be a symlink")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b""
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise JobError("invalid_input", "Codex config.toml must be a regular file")
        return stream.read()


def _replace(path, original, replacement):
    _parse(replacement)
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
        raise JobError("conflict", "Codex config.toml changed outside the managed trust lock; retry launch")
    atomic_bytes(path, replacement)
    return str(backup)


def trust_cwd(cwd, path=None):
    """Persist trust for the exact launch cwd; return no config contents."""
    path = Path(path) if path is not None else config_path()
    cwd = str(Path(cwd).resolve(strict=True))
    with _locked(path):
        original = _read(path)
        document = _parse(original)
        projects = document.get("projects", {})
        if not isinstance(projects, dict):
            raise JobError("invalid_input", "Codex projects must be a TOML table")
        project = projects.get(cwd, {})
        if not isinstance(project, dict):
            raise JobError("invalid_input", "Codex project must be a TOML table")
        if project.get("trust_level") == "trusted":
            return {"changed": False, "config": str(path), "cwd": cwd, "backup": None}
        source = original.decode("utf-8")
        entries, tables, _ = _entries(source)
        target = ("projects", cwd, "trust_level")
        if target in entries:
            start, end = entries[target]
            replacement = source[:start] + '"trusted"' + source[end:]
        else:
            inline_parents = [key for key, (start, _) in entries.items()
                              if target[:len(key)] == key and source[start] == "{"]
            if inline_parents:
                parent = max(inline_parents, key=len)
                start, end = entries[parent]
                position = end - 1
                comma = ", " if source[start + 1:position].strip() else ""
                key = ".".join(json.dumps(part, ensure_ascii=False) for part in target[len(parent):])
                replacement = source[:position] + comma + key + ' = "trusted"' + source[position:]
            elif project or target[:2] in tables or ("projects",) in tables:
                parent = max((key for key in tables if target[:len(key)] == key), key=len)
                position = tables[parent]
                key = ".".join(json.dumps(part, ensure_ascii=False) for part in target[len(parent):])
                newline = "\r\n" if "\r\n" in source else "\n"
                prefix = newline if position and source[position - 1] != "\n" else ""
                replacement = source[:position] + prefix + key + ' = "trusted"' + newline + source[position:]
            else:
                replacement = source + f'\n[projects.{json.dumps(cwd, ensure_ascii=False)}]\ntrust_level = "trusted"\n'
        expected = deepcopy(document)
        expected.setdefault("projects", {}).setdefault(cwd, {})["trust_level"] = "trusted"
        if not _same_toml_values(_parse(replacement.encode()), expected):
            raise JobError("invalid_input", "Codex trust edit could not preserve the TOML structure")
        backup = _replace(path, original, replacement.encode())
        return {"changed": True, "config": str(path), "cwd": cwd, "backup": backup}


def _stale_paths(document):
    projects = document.get("projects", {})
    if not isinstance(projects, dict):
        raise JobError("invalid_input", "Codex projects must be a TOML table")
    paths = []
    for name, settings in projects.items():
        if not isinstance(settings, dict) or "trust_level" not in settings:
            continue
        candidate = Path(name)
        if not candidate.is_absolute() or ".." in candidate.parts:
            continue
        if not candidate.is_relative_to(ORCHESTRATOR_ROOT):
            continue
        if not candidate.resolve().is_relative_to(ORCHESTRATOR_ROOT):
            continue
        try:
            candidate.lstat()
        except FileNotFoundError:
            paths.append(name)
    return sorted(paths)


def _pruned(source, paths):
    for name in paths:
        project_key = ("projects", name)
        entries, tables, removals = _entries(source)
        settings = _parse(source.encode())["projects"][name]
        keys = [project_key + ("trust_level",)]
        if set(settings) == {"trust_level"}:
            if project_key in entries:
                keys = [project_key]
            elif project_key in tables:
                keys.append(project_key)
        for start, end in sorted((removals[key] for key in keys), reverse=True):
            source = source[:start] + source[end:]
    return source.encode()


def prune_trust(path=None, *, apply=False):
    """Report stale managed project trust entries; mutation requires opt-in."""
    path = Path(path) if path is not None else config_path()
    if not apply:
        paths = _stale_paths(_parse(_read(path)))
        return {"dry_run": True, "count": len(paths), "paths": paths, "backup": None}
    with _locked(path):
        original = _read(path)
        paths = _stale_paths(_parse(original))
        backup = None
        if paths:
            backup = _replace(path, original, _pruned(original.decode(), paths))
        return {"dry_run": False, "count": len(paths), "paths": paths, "backup": backup}
