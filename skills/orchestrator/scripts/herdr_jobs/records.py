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


RUNTIME_EFFORT_CAPABILITIES = {
    "codex": {"values": frozenset(("minimal", "low", "medium", "high", "xhigh", "max")),
              "flag": None},
    "pi": {"values": frozenset(("off", "minimal", "low", "medium", "high", "xhigh", "max")),
           "flag": "--thinking"},
    "claude-code": {"values": frozenset(("low", "medium", "high", "xhigh", "max")),
                    "flag": "--effort"},
    "omp": {"values": frozenset(("off", "minimal", "low", "medium", "high", "xhigh", "max", "auto")),
            "flag": "--thinking"},
}


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


def validate_effort(runtime, effort):
    capability = RUNTIME_EFFORT_CAPABILITIES.get(runtime)
    if capability is None:
        invalid(f"runtime {runtime} does not support an effort override")
    if effort not in capability["values"]:
        invalid(f"effort {effort!r} is not supported by runtime {runtime}")


def effort_arguments(runtime, effort):
    if effort is None:
        return []
    validate_effort(runtime, effort)
    if runtime == "codex":
        return ["-c", f"model_reasoning_effort={effort}"]
    return [RUNTIME_EFFORT_CAPABILITIES[runtime]["flag"], effort]


def is_commit_id(value):
    return (isinstance(value, str)
            and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is not None)


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        invalid(f"{label}: expected 1–64 letters, digits, underscores or hyphens")
    return value


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        invalid(f"{label}: expected integer {low}–{high}")
    return value


def jail_stream_idle_timeout_record(value):
    fields(value, ["default", "task_classes"], label="jail_stream_idle_timeout_ms")
    default = integer(value["default"], "jail stream idle timeout default", 1, 2**31 - 1)
    classes = value["task_classes"]
    if not isinstance(classes, dict):
        invalid("jail stream idle timeout task_classes must be an object")
    normalized = {}
    for task_class, timeout in classes.items():
        identifier(task_class, "jail timeout task class")
        normalized[task_class] = integer(timeout, f"jail stream idle timeout for {task_class}", 1, 2**31 - 1)
    return {"default": default, "task_classes": normalized}


def version(value):
    integer(value, "schema_version", 1, 1)


def repository_worktree_record(value):
    fields(value, ["schema_version", "path", "lease_id", "lease_holder", "repo_root",
                   "git_common_dir", "base_commit"], label="repository_worktree")
    version(value["schema_version"])
    for key in ("path", "repo_root", "git_common_dir"):
        path = Path(text(value[key], f"repository_worktree.{key}", 4096))
        if not path.is_absolute():
            invalid(f"repository_worktree.{key} must be absolute")
    text(value["lease_id"], "repository_worktree.lease_id", 4096)
    text(value["lease_holder"], "repository_worktree.lease_holder", 4096)
    if not is_commit_id(value["base_commit"]):
        invalid("repository_worktree.base_commit must be a full Git commit ID")
    return value


def validate_writer_intent(job, label="job"):
    if not isinstance(job, dict):
        invalid(f"{label}: expected an object")
    if type(job.get("writes_repository")) is not bool:
        invalid(f"{label}: writes_repository must be explicitly set to true or false")
    has_worktree = "repository_worktree" in job
    if job["writes_repository"] and not has_worktree:
        invalid(f"{label}: writes_repository=true requires repository_worktree")
    if not job["writes_repository"] and has_worktree:
        invalid(f"{label}: repository_worktree requires writes_repository=true")
    return job["writes_repository"]


def worker_result_record(value):
    fields(value, ["task_id", "assignment_revision"], label="worker_result")
    text(value["task_id"], "worker_result.task_id", 200)
    integer(value["assignment_revision"], "worker_result.assignment_revision", 1, 2**63 - 1)
    return value


def retry_override_record(value):
    fields(value, [], ["pi", "codex"], "retry_override")
    if not value:
        invalid("retry_override must contain pi and/or codex settings")
    result = {}
    if "pi" in value:
        pi = fields(value["pi"], [], ["max_retries", "max_agent_delay_ms"], "retry_override.pi")
        if not pi:
            invalid("retry_override.pi must set at least one retry value")
        normalized = {}
        if "max_retries" in pi:
            normalized["max_retries"] = integer(pi["max_retries"], "pi max_retries", 0, 100)
        if "max_agent_delay_ms" in pi:
            normalized["max_agent_delay_ms"] = integer(pi["max_agent_delay_ms"],
                                                        "pi max_agent_delay_ms", 0, 600000)
        result["pi"] = normalized
    if "codex" in value:
        codex = fields(value["codex"], ["provider_id"],
                       ["request_max_retries", "stream_max_retries"], "retry_override.codex")
        if not any(key in codex for key in ("request_max_retries", "stream_max_retries")):
            invalid("retry_override.codex must set request_max_retries and/or stream_max_retries")
        normalized = {"provider_id": identifier(codex["provider_id"], "Codex provider_id")}
        for key in ("request_max_retries", "stream_max_retries"):
            if key in codex:
                normalized[key] = integer(codex[key], f"Codex {key}", 0, 100)
        result["codex"] = normalized
    return result


def absolute(value, base):
    return str((base / text(value, "path", 4096)).absolute())


def policy_record(value):
    fields(value, ["schema_version", "default_concurrency", "max_concurrency", "topology", "routes", "runtime_kinds"],
           ["provider_admission", "jail_stream_idle_timeout_ms"])
    version(value["schema_version"])
    if value["topology"] != "new-workspace":
        invalid("version 1 supports new-workspace topology only")
    value["jail_stream_idle_timeout_ms"] = jail_stream_idle_timeout_record(
        value.get("jail_stream_idle_timeout_ms", {
            "default": 300000,
            "task_classes": {"short_routine": 300000, "long_form_research": 600000},
        })
    )
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
    admission = value.get("provider_admission", {})
    fields(admission, [], ["default_provider_cap", "provider_caps", "default_backoff_seconds"],
           "provider_admission")
    admission = {"default_provider_cap": integer(admission.get("default_provider_cap", 4),
                                                "default_provider_cap", 1, 64),
                 "provider_caps": admission.get("provider_caps", {}),
                 "default_backoff_seconds": integer(admission.get("default_backoff_seconds", 60),
                                                    "default_backoff_seconds", 1, 3600)}
    if not isinstance(admission["provider_caps"], dict):
        invalid("provider_caps must be an object")
    normalized_caps = {}
    for provider, cap in admission["provider_caps"].items():
        text(provider, "provider cap key", 100)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", provider) is None:
            invalid("provider cap keys must be simple provider identifiers")
        normalized_provider = provider.casefold()
        if normalized_provider in normalized_caps:
            invalid(f"duplicate provider cap key after case normalization: {provider}")
        normalized_caps[normalized_provider] = integer(cap, f"provider cap for {provider}", 1, 64)
    admission["provider_caps"] = normalized_caps
    value["provider_admission"] = admission
    return value


def ensure_task_admitted(task_kind, job_id):
    if task_kind == "shopping":
        raise JobError("decision_needed",
                       f"{job_id}: shopping is unsupported by managed Herdr Jobs because its jail route is Codex-only; "
                       "launch it from a host Herdr pane with `omp-train --claude` as documented in "
                       "skills/orchestrator/PREFERENCES.md")


def prepare(manifest_path, policy_path):
    manifest_path = Path(manifest_path).absolute()
    policy = policy_record(load_json(policy_path))
    manifest = fields(load_json(manifest_path), ["schema_version", "request_id", "jobs"],
                      ["concurrency", "retry_override"])
    version(manifest["schema_version"])
    identifier(manifest["request_id"], "request_id")
    retry_override = retry_override_record(manifest["retry_override"]) if "retry_override" in manifest else None
    concurrency = integer(manifest.get("concurrency", policy["default_concurrency"]),
                          "concurrency", 1, policy["max_concurrency"])
    if not isinstance(manifest["jobs"], list) or not 1 <= len(manifest["jobs"]) <= 128:
        invalid("jobs must contain 1–128 independent jobs")
    jobs = []
    seen = set()
    worker_results = set()
    for job in manifest["jobs"]:
        fields(job, ["job_id", "name", "task_kind", "task_file", "cwd", "output_expectation",
                     "writes_repository"],
               ["override", "repository_worktree", "worker_result", "task_class"], "job")
        job_id = identifier(job["job_id"], "job_id")
        validate_writer_intent(job, job_id)
        if job_id in seen:
            invalid(f"duplicate job_id: {job_id}")
        seen.add(job_id)
        text(job["name"], "name", 200)
        text(job["output_expectation"], "output_expectation", 10000)
        if not isinstance(job["task_kind"], str) or job["task_kind"] not in policy["routes"]:
            invalid("task_kind must be ordinary, deep_research, or shopping")
        task_class = identifier(job["task_class"], "task_class") if "task_class" in job else None
        ensure_task_admitted(job["task_kind"], job_id)
        route = dict(policy["routes"][job["task_kind"]])
        model = None
        override = job.get("override")
        if override is not None:
            fields(override, ["instruction"], ["runtime", "model", "provider", "effort"], "override")
            text(override["instruction"], "override instruction", 10000)
            if "runtime" in override:
                named = identifier(override["runtime"], "override runtime")
                if route["mode"] == "jail" and named != route["runtime"]:
                    raise JobError("decision_needed",
                                   f"{job_id}: override runtime {named} would leave the {job['task_kind']} jail route; "
                                   "re-issue the job as task_kind ordinary to run it outside the jail")
                route = {"mode": route["mode"], "runtime": named}
            if "model" in override:
                model = text(override["model"], "model", 200)
            if "effort" in override:
                effort = text(override["effort"], "effort", 32)
            if "provider" in override:
                provider = text(override["provider"], "provider", 100)
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", provider) is None:
                    invalid("provider must be a simple provider identifier")
            else:
                provider = None
        else:
            provider = None
        runtime = route["runtime"]
        if runtime is None:
            raise JobError("decision_needed", f"{job_id}: choose an ordinary-job runtime in policy or a current-request override")
        if runtime not in policy["runtime_kinds"]:
            raise JobError("unavailable_capability", f"unsupported runtime mapping: {runtime}; no substitution")
        if override is not None and "effort" in override:
            if route["mode"] != "agent":
                invalid(f"{job_id}: effort overrides are not supported for {route['mode']} routes")
            validate_effort(runtime, effort)
        if runtime == "pi" and provider is not None and model is None:
            invalid(f"{job_id}: pi provider override requires a model")
        task_path = absolute(job["task_file"], manifest_path.parent)
        task = read_regular(task_path).decode("utf-8")
        text(task, "task")
        cwd = absolute(job["cwd"], manifest_path.parent)
        if job["writes_repository"]:
            repository_worktree_record(job["repository_worktree"])
            try:
                cwd = str(Path(cwd).resolve(strict=False))
                recorded_path = str(Path(job["repository_worktree"]["path"]).resolve(strict=False))
            except (OSError, RuntimeError):
                invalid(f"{job_id}: repository worktree path cannot be canonicalized")
            if cwd != recorded_path:
                invalid(f"{job_id}: cwd must equal repository_worktree.path")
        if "worker_result" in job:
            worker_result_record(job["worker_result"])
            identity = (job["worker_result"]["task_id"], job["worker_result"]["assignment_revision"])
            if identity in worker_results:
                invalid(f"duplicate worker_result identity: {identity[0]} revision {identity[1]}")
            worker_results.add(identity)
        prepared_job = {**job, "task_file": task_path, "cwd": cwd,
                        "task": task, "route": route, "model": model,
                        "provider": provider, "kind": policy["runtime_kinds"][runtime]}
        if route["mode"] == "jail":
            timeout_policy = policy["jail_stream_idle_timeout_ms"]
            prepared_job["jail_stream_idle_timeout_ms"] = timeout_policy["task_classes"].get(
                task_class, timeout_policy["default"])
        jobs.append(prepared_job)
    if retry_override:
        for runtime in retry_override:
            if not any(job["route"]["mode"] == "agent" and job["route"]["runtime"] == runtime
                       for job in jobs):
                invalid(f"retry_override for {runtime} has no {runtime} workers")
            if runtime == "codex" and any(job["route"]["mode"] == "jail"
                                           and job["route"]["runtime"] == "codex" for job in jobs):
                invalid("Codex retry_override is unsupported for jail workers")
    prepared = {"schema_version": 1, "request_id": manifest["request_id"], "concurrency": concurrency,
                "jobs": jobs, "policy": policy, "policy_digest": digest(encoded(policy))}
    if retry_override is not None:
        prepared["retry_override"] = retry_override
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
