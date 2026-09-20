"""Validated records, bounded file access, and durable checkpoints."""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone


class JobError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def invalid(message):
    raise JobError("invalid_input", message)


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            invalid(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(data):
    try:
        return json.loads(data, object_pairs_hook=unique_object,
                          parse_constant=lambda value: invalid(f"non-finite JSON: {value}"))
    except (ValueError, UnicodeError) as error:
        invalid(f"invalid JSON: {error}")


def read_regular(path, limit=1048576):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            invalid(f"expected a regular file of at most {limit} bytes: {path}")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise JobError("conflict", f"file changed while reading: {path}")
    return data


def load_json(path, limit=1048576):
    return parse_json(read_regular(path, limit))


def fields(value, required, optional=(), label="record"):
    if not isinstance(value, dict):
        invalid(f"{label}: expected an object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        invalid(f"{label}: missing {sorted(missing)}, unknown {sorted(extra)}")
    return value


def text(value, label, maximum=100000):
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > maximum:
        invalid(f"{label}: expected nonempty text, at most {maximum} characters, without NUL")
    return value


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        invalid(f"{label}: expected 1–64 letters, digits, underscores or hyphens")
    return value


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        invalid(f"{label}: expected integer {low}–{high}")
    return value


def version(value):
    integer(value, "schema_version", 1, 1)


def absolute(value, base):
    return str((base / text(value, "path", 4096)).absolute())


def policy_record(value):
    fields(value, ["schema_version", "default_concurrency", "max_concurrency", "topology", "routes", "runtime_kinds"])
    version(value["schema_version"])
    if value["topology"] != "new-workspace":
        invalid("version 1 supports new-workspace topology only")
    maximum = integer(value["max_concurrency"], "max_concurrency", 1, 64)
    integer(value["default_concurrency"], "default_concurrency", 1, maximum)
    fields(value["routes"], ["ordinary", "deep_research", "shopping"], label="routes")
    for name, route in value["routes"].items():
        fields(route, ["mode", "runtime"], label=f"route {name}")
        if route["mode"] not in ("agent", "jail"):
            invalid(f"route {name}: mode must be agent or jail")
        if route["runtime"] is not None:
            identifier(route["runtime"], "route runtime")
        if route["mode"] == "jail" and route["runtime"] != "codex":
            invalid("version 1 supports the documented Codex jail route only")
    if not isinstance(value["runtime_kinds"], dict) or not value["runtime_kinds"]:
        invalid("runtime_kinds must be a nonempty object")
    for runtime, kind in value["runtime_kinds"].items():
        identifier(runtime, "runtime")
        identifier(kind, "Herdr kind")
    return value


def prepare(manifest_path, policy_path):
    manifest_path = Path(manifest_path).absolute()
    policy = policy_record(load_json(policy_path))
    manifest = fields(load_json(manifest_path), ["schema_version", "request_id", "jobs"], ["concurrency"])
    version(manifest["schema_version"])
    identifier(manifest["request_id"], "request_id")
    concurrency = integer(manifest.get("concurrency", policy["default_concurrency"]),
                          "concurrency", 1, policy["max_concurrency"])
    if not isinstance(manifest["jobs"], list) or not 1 <= len(manifest["jobs"]) <= 128:
        invalid("jobs must contain 1–128 independent jobs")
    jobs = []
    seen = set()
    for job in manifest["jobs"]:
        fields(job, ["job_id", "name", "task_kind", "task_file", "cwd", "output_expectation"],
               ["override"], "job")
        job_id = identifier(job["job_id"], "job_id")
        if job_id in seen:
            invalid(f"duplicate job_id: {job_id}")
        seen.add(job_id)
        text(job["name"], "name", 200)
        text(job["output_expectation"], "output_expectation", 10000)
        if not isinstance(job["task_kind"], str) or job["task_kind"] not in policy["routes"]:
            invalid("task_kind must be ordinary, deep_research, or shopping")
        route = dict(policy["routes"][job["task_kind"]])
        model = None
        override = job.get("override")
        if override is not None:
            fields(override, ["instruction"], ["runtime", "model"], "override")
            text(override["instruction"], "override instruction", 10000)
            if "runtime" in override:
                route = {"mode": "agent", "runtime": identifier(override["runtime"], "override runtime")}
            if "model" in override:
                model = text(override["model"], "model", 200)
        runtime = route["runtime"]
        if runtime is None:
            raise JobError("decision_needed", f"{job_id}: choose an ordinary-job runtime in policy or a current-request override")
        if runtime not in policy["runtime_kinds"]:
            raise JobError("unavailable_capability", f"unsupported runtime mapping: {runtime}; no substitution")
        task_path = absolute(job["task_file"], manifest_path.parent)
        task = read_regular(task_path).decode("utf-8")
        text(task, "task")
        jobs.append({**job, "task_file": task_path, "cwd": absolute(job["cwd"], manifest_path.parent),
                     "task": task, "route": route, "model": model,
                     "kind": policy["runtime_kinds"][runtime]})
    prepared = {"schema_version": 1, "request_id": manifest["request_id"], "concurrency": concurrency,
                "jobs": jobs, "policy": policy, "policy_digest": digest(encoded(policy))}
    if len(encoded(prepared)) > 8388608:
        invalid("expanded request exceeds 8 MiB")
    return prepared


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value):
    atomic_bytes(path, encoded(value) + b"\n")


@contextlib.contextmanager
def run_lock(root):
    root = Path(root)
    if root.is_symlink():
        invalid("run directory must not be a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise JobError("conflict", "another helper call owns this run") from error
        yield
    finally:
        os.close(descriptor)


def bounded_file(root, relative, limit=16777216):
    relative = text(relative, "artifact path", 4096)
    pieces = relative.split("/")
    if any(part in ("", ".", "..") for part in pieces) or "\\" in relative:
        invalid(f"artifact must have a bounded relative path: {relative}")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in pieces[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(pieces[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                invalid("artifact is not a bounded regular file")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if len(data) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise JobError("conflict", "artifact changed during collection")
        return data
    finally:
        os.close(descriptor)
