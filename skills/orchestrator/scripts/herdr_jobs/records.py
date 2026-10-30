"""Validated records, bounded file access, and durable checkpoints."""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from datetime import datetime, timezone

from .edit_scope import brief_contract_errors


WORKER_MODES = frozenset(("literal", "inferential"))
CONTEXT_KINDS = frozenset(("test", "fixture", "source", "schema", "dependency", "document"))
LOCAL_LANE_TASK_CLASSES = frozenset((
    "triage", "summary", "contract_artifact", "bounded_code_edit", "inventory",
    "local_file_analysis",
))

# run-jail.sh mounts these host sources into the worker at the corresponding
# container roots. PII_JAIL_S0_TEST_ROOT mirrors jail-session.sh's test-only
# root override; production launches use its fixed /var/lib/pii-jail root.
_jail_root_value = os.environ.get("PII_JAIL_S0_TEST_ROOT", "/var/lib/pii-jail")
JAIL_CONTEXT_EXPORTS = (
    (Path(_jail_root_value) / "build/ctx", PurePosixPath("/ctx")),
    (Path(_jail_root_value) / "work", PurePosixPath("/work")),
) if _jail_root_value else ()

# These controls are protected even when a caller omits an explicit path list.
# The list is prompt data as well as a declaration checked at the launch boundary.
DEFAULT_PROTECTED_PATHS = (
    "~/.config/vikunja-mcp/delegation-authorized.toml",
    "tools/pii-jail/",
    "tools/pii-jail/token-allowlist.txt",
    "scripts/pii-jail/",
    "keys/",
)


RUNTIME_EFFORT_CAPABILITIES = {
    "codex": {"values": frozenset(("minimal", "low", "medium", "high", "xhigh", "max")),
              "flag": None},
    "pi": {"values": frozenset(("off", "minimal", "low", "medium", "high", "xhigh", "max")),
           "flag": "--thinking"},
    "claude-code": {"values": frozenset(("low", "medium", "high", "xhigh")),
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


def independent_validator_record(value):
    fields(value, ["argv"], label="independent_validator")
    argv = value["argv"]
    if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
        invalid("independent_validator.argv must contain 1–64 arguments")
    normalized = [text(argument, f"independent_validator.argv[{index}]", 4096)
                  for index, argument in enumerate(argv)]
    if "{output_dir}" not in normalized:
        invalid("independent_validator.argv must include the {output_dir} argument")
    return {"argv": normalized}


def worker_mode(value, label="worker_mode"):
    value = text(value, label, 32).strip().casefold()
    if value not in WORKER_MODES:
        invalid(f"{label} must be literal or inferential")
    return value


def path_list(value, label, maximum=64):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        invalid(f"{label} must be an array of at most {maximum} paths")
    result = []
    for index, item in enumerate(value):
        result.append(text(item, f"{label}[{index}]", 4096))
    if len(set(result)) != len(result):
        invalid(f"{label} must not contain duplicate paths")
    return result


def protected_paths_record(value=None, acceptance_tests=()):
    """Return the immutable defaults plus task-specific protected paths."""
    explicit = path_list(value, "protected_paths")
    tests = path_list(list(acceptance_tests), "acceptance_tests")
    result = list(DEFAULT_PROTECTED_PATHS)
    for item in (*explicit, *tests):
        if item not in result:
            result.append(item)
    return result


def _path_key(value, base=None):
    value = str(value).strip()
    if value.startswith("~/"):
        value = str(Path.home() / value[2:])
    path = Path(value)
    if base is not None and not path.is_absolute():
        path = Path(base) / path
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def paths_overlap(candidate, protected, base=None):
    """Whether a declared editable path contains or is inside a protected path."""
    candidate_path = _path_key(candidate, base)
    protected_path = _path_key(protected, base)
    try:
        candidate_path.relative_to(protected_path)
        return True
    except ValueError:
        try:
            protected_path.relative_to(candidate_path)
            return True
        except ValueError:
            return False


def validate_task_output_paths(task, job_id, task_path):
    """Reject task text that directs editable work or reports elsewhere."""
    declarations = (
        ("output directory", re.compile(
            r"(?im)^\s*(?P<bold>\*\*|)(?:output directory|output dir)\s*:(?P=bold)\s*(?P<value>.+?)\s*$")),
        ("editable directory", re.compile(
            r"(?im)^\s*(?:editable directory|editable paths?)\s*:\s*(?P<value>.+?)\s*$")),
    )
    for label, pattern in declarations:
        for match in pattern.finditer(task):
            value = match.group("value").strip().strip("`\"'")
            if value and value not in ("the output directory", "the assigned output directory"):
                raise JobError("invalid_input", f"{job_id}: task file {task_path} declares conflicting {label}: {value!r}")
    path_prefix = r"(?:[~/]|\.\.?/|[\w.-]+/)"
    filename = r"[\w.-]+\.[\w-]+"
    quoted_filename = r"[\w. -]+\.[\w-]+"
    prose_path = re.compile(
        r"(?im)\b(?:write|save|place|put|store|create)\s+(?:the\s+)?"
        r"(?P<artifact_quote>[`\"']?)(?:report|result|output)(?:\.\w+)?(?P=artifact_quote)"
        r"\s+(?:in|to|at|under)\s+(?:the\s+)?"
        rf"(?:(?P<quote>[`\"'])(?P<quoted>{path_prefix}[^\r\n]+?|{quoted_filename})(?P=quote)|"
        rf"(?P<bare>cwd|working directory|current directory|{path_prefix}[^\s,;]+|{filename}))"
    )
    for match in prose_path.finditer(task):
        if re.search(r"\bdo\s+not\s+$", task[:match.start()], re.IGNORECASE):
            continue
        value = match.group("quoted")
        if value is None:
            value = match.group("bare").rstrip(".")
        raise JobError("invalid_input", f"{job_id}: task file {task_path} declares conflicting output directory: {value!r}")


def validate_protected_paths(protected_paths, editable_paths=(), base=None):
    protected_paths = protected_paths_record(protected_paths)
    editable_paths = path_list(list(editable_paths), "editable_paths")
    for editable in editable_paths:
        if any(paths_overlap(editable, protected, base=base) for protected in protected_paths):
            invalid(f"editable path is protected: {editable}")
    return protected_paths


def host_path_record(value, label):
    fields(value, ["host", "path", "canonical_path"],
           ["lookup_host", "source"], label=label)
    if value["host"] not in ("desktop", "pve"):
        invalid(f"{label}.host must be desktop or pve")
    for key in ("path", "canonical_path"):
        path = PurePosixPath(text(value[key], f"{label}.{key}", 4096))
        if not path.is_absolute() or ".." in path.parts or "\\" in str(path):
            invalid(f"{label}.{key} must be an absolute path without traversal")
    if value.get("lookup_host", value["host"]) not in ("desktop", "pve"):
        invalid(f"{label}.lookup_host must be desktop or pve")
    if "source" in value:
        source = fields(value["source"], ["request_id", "job_id", "run_dir"], label=f"{label}.source")
        identifier(source["request_id"], f"{label}.source.request_id")
        identifier(source["job_id"], f"{label}.source.job_id")
        root = PurePosixPath(text(source["run_dir"], f"{label}.source.run_dir", 4096))
        if not root.is_absolute() or ".." in root.parts:
            invalid(f"{label}.source.run_dir must be absolute without traversal")
    return dict(value)


def brief_contract_record(job):
    for key in ("dependencies", "mutation_targets"):
        if key not in job:
            continue
        values = job[key]
        if not isinstance(values, list) or len(values) > 128:
            invalid(f"{key} must be an array of at most 128 entries")
        job[key] = [host_path_record(value, f"{key}[{index}]")
                    for index, value in enumerate(values)]
    if "remote_authority" in job:
        values = job["remote_authority"]
        if not isinstance(values, list) or len(values) > 64:
            invalid("remote_authority must be an array of at most 64 entries")
        for value in values:
            fields(value, ["host", "paths", "operations", "instruction"], label="remote_authority")
            if value["host"] != "pve":
                invalid("remote_authority.host must be pve")
            text(value["instruction"], "remote_authority.instruction", 10000)
            roots = path_list(value["paths"], "remote_authority.paths")
            if not roots or any(not PurePosixPath(root).is_absolute() or ".." in PurePosixPath(root).parts
                                for root in roots):
                invalid("remote_authority.paths must contain absolute paths without traversal")
            operations = value["operations"]
            if (not isinstance(operations, list) or not operations
                    or any(operation not in ("read", "write") for operation in operations)):
                invalid("remote_authority.operations must contain read or write")


def _context_kind(path, explicit=None):
    if explicit is not None:
        explicit = text(explicit, "required_context.kind", 32).strip().casefold()
        if explicit not in CONTEXT_KINDS:
            invalid("required_context.kind is not a supported context kind")
        return explicit
    lowered = str(path).casefold()
    if re.search(r"(^|[/_.-])fixtures?([/_.-]|$)", lowered):
        return "fixture"
    if re.search(r"(^|[/_.-])tests?([/_.-]|$)", lowered):
        return "test"
    return "source"


def _context_entries(value):
    """Normalize list and grouped context-manifest forms to entry objects."""
    if isinstance(value, dict):
        fields(value, ["tests", "fixtures"], ["files"], label="required_context")
        grouped = []
        for kind in ("test", "fixture"):
            paths = path_list(value[kind + "s"], f"required_context.{kind}s")
            grouped.extend({"path": path, "kind": kind, "purpose": f"Required {kind}."}
                            for path in paths)
        grouped.extend(value.get("files", []))
        value = grouped
    if not isinstance(value, list) or not value:
        invalid("required_context must contain at least one entry")
    if len(value) > 128:
        invalid("required_context may contain at most 128 entries")
    entries = []
    for index, item in enumerate(value):
        label = f"required_context[{index}]"
        if isinstance(item, str):
            item = {"path": item, "purpose": "Required worker context."}
        item = fields(item, ["path"], ["purpose", "kind"], label=label)
        entry = {"path": text(item["path"], f"{label}.path", 4096),
                 "purpose": text(item.get("purpose", "Required worker context."),
                                   f"{label}.purpose", 1000),
                 "kind": _context_kind(item["path"], item.get("kind"))}
        entries.append(entry)
    return entries


def jail_context_path(path, exports=None):
    """Map an exported host path to the path visible inside the jail."""
    host_path = _path_key(path)
    mapped_exports = JAIL_CONTEXT_EXPORTS if exports is None else exports
    for host_root, jail_root in mapped_exports:
        host_root = _path_key(host_root)
        try:
            relative = host_path.relative_to(host_root)
        except ValueError:
            continue
        return str(PurePosixPath(jail_root) / PurePosixPath(relative.as_posix()))
    invalid(f"required_context path is outside jail-visible export roots: {path}")


def validate_required_context(value, base=None, require_test_fixture=False, check_exists=True,
                              jail_visible=False, jail_exports=None):
    """Validate context paths and optionally require visibility through jail mounts."""
    entries = _context_entries(value)
    resolved = []
    seen = set()
    for index, entry in enumerate(entries):
        original = entry["path"]
        path = _path_key(original, base)
        key = str(path)
        if key in seen:
            invalid(f"required_context contains duplicate path: {original}")
        seen.add(key)
        if jail_visible:
            jail_context_path(path, jail_exports)
        if check_exists:
            if path.is_symlink() or not path.is_file():
                invalid(f"required_context path is unavailable: {original}")
            try:
                read_regular(path)
            except (OSError, JobError) as error:
                invalid(f"required_context path cannot be read: {original}: {error}")
        resolved.append({**entry, "path": str(path)})
    if require_test_fixture:
        kinds = {entry["kind"] for entry in resolved}
        if "test" not in kinds or "fixture" not in kinds:
            invalid("jailed work required_context must list at least one test and one fixture")
    return resolved


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
    # Defer to break the records.py -> model_ladder.py -> records.py import cycle.
    from .model_ladder import billing_pool

    fields(value, ["schema_version", "default_concurrency", "max_concurrency", "topology", "routes", "runtime_kinds"],
           ["provider_admission", "jail_stream_idle_timeout_ms", "model_ladder", "local_lane"])
    version(value["schema_version"])
    if "model_ladder" in value:
        ladder = fields(value["model_ladder"], ["csv", "default_level"], label="model_ladder")
        text(ladder["csv"], "model ladder CSV path", 4096)
        integer(ladder["default_level"], "default model level", 1, 8)
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
    local_lane = value.get("local_lane")
    if local_lane is not None:
        fields(local_lane, ["model", "provider", "effort", "service_unit",
                            "minimum_free_gpu_memory_mib", "require_repository_worktree",
                            "require_independent_validator", "mcp_configuration", "extensions",
                            "allowed_task_classes"],
               label="local_lane")
        if (local_lane["model"] != "qwen3.8-27b-uncensored"
                or local_lane["provider"] != "llama-local"
                or local_lane["effort"] != "medium"
                or local_lane["service_unit"] != "llama-server.service"):
            invalid("local_lane model, provider, effort, or service differs from the approved tuple")
        integer(local_lane["minimum_free_gpu_memory_mib"],
                "local_lane.minimum_free_gpu_memory_mib", 1, 65536)
        validate_effort("pi", local_lane["effort"])
        if local_lane["require_repository_worktree"] is not True:
            invalid("local_lane requires a throwaway repository worktree")
        if local_lane["require_independent_validator"] is not True:
            invalid("local_lane requires an independent validator")
        if (local_lane["mcp_configuration"] != "inherit_pi_agent_dir"
                or local_lane["extensions"] != "inherit_pi_agent_dir"):
            invalid("local_lane must inherit the user's configured Pi MCP and extension resources")
        task_classes = local_lane["allowed_task_classes"]
        if (not isinstance(task_classes, list) or not task_classes
                or any(not isinstance(item, str) or item not in LOCAL_LANE_TASK_CLASSES
                       for item in task_classes)
                or len(set(task_classes)) != len(task_classes)):
            invalid("local_lane allowed_task_classes must be a nonempty unique subset of the bounded task classes")
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
    original_keys = set()
    for provider, cap in admission["provider_caps"].items():
        text(provider, "provider cap key", 100)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", provider) is None:
            invalid("provider cap keys must be simple provider identifiers")
        if provider.casefold() in original_keys:
            invalid(f"duplicate provider cap key after case normalization: {provider}")
        original_keys.add(provider.casefold())
        normalized_provider = billing_pool(provider)
        if normalized_provider in normalized_caps:
            # Older policies gave aliases independent caps; use the stricter shared cap.
            normalized_caps[normalized_provider] = min(normalized_caps[normalized_provider],
                                                       integer(cap, f"provider cap for {provider}", 1, 64))
            continue
        normalized_caps[normalized_provider] = integer(cap, f"provider cap for {provider}", 1, 64)
    admission["provider_caps"] = normalized_caps
    if local_lane is not None and admission["provider_caps"].get("llama-local") != 1:
        invalid("local_lane requires one shared llama-local admission slot")
    value["provider_admission"] = admission
    return value


def ensure_task_admitted(task_kind, job_id):
    if task_kind == "shopping":
        raise JobError("decision_needed",
                       f"{job_id}: shopping is unsupported by managed Herdr Jobs because its jail route is Codex-only; "
                       "launch it from a host Herdr pane with `jail-agent --claude` as documented in "
                       "skills/orchestrator/PREFERENCES.md")


def ensure_local_lane_admitted(local_config, task_class, local_lane_scope, job_id):
    """Apply the local-lane class and explicit-scope gate to every launch route."""
    if task_class is None:
        invalid(f"{job_id}: local_lane requires an allowlisted task_class")
    task_class = identifier(task_class, "task_class")
    if task_class not in local_config["allowed_task_classes"]:
        invalid(f"{job_id}: local_lane task_class is not allowlisted")
    if local_lane_scope is None:
        invalid(f"{job_id}: local_lane requires an explicit local_lane_scope declaration")
    local_scope = fields(local_lane_scope,
                         ["network_access", "credential_access", "credentials_in_environment",
                          "destructive_actions"], label=f"{job_id}.local_lane_scope")
    if any(value is not False for value in local_scope.values()):
        invalid(f"{job_id}: local_lane_scope must exclude network, credentials, and destructive actions")
    return task_class, local_scope


def prepare(manifest_path, policy_path, existing_jobs=()):
    # Defer to break the records.py -> model_ladder.py -> records.py import cycle.
    from .model_ladder import (HARNESS_ALIASES, billing_pool, complete_tuple,
                               load_ladder, select, validate_escalation,
                               validate_provider_harness)
    from .review_jobs import expand_jobs

    # Also deferred for records.py -> routing_decision.py -> records.py.
    from .routing_decision import decision_for

    manifest_path = Path(manifest_path).absolute()
    policy = policy_record(load_json(policy_path))
    ladder_config = policy.get("model_ladder")
    ladder_levels = None
    if ladder_config is not None:
        ladder_path = Path(policy_path).absolute().parent / ladder_config["csv"]
        ladder_bytes = read_regular(ladder_path, 65536)
        ladder_levels = load_ladder(ladder_path, ladder_bytes)
        # Pin all eight levels, including choices not selected by the current manifest.
        ladder_config["sha256"] = digest(ladder_bytes)
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
    for job in expand_jobs(manifest["jobs"], existing_jobs):
        fields(job, ["job_id", "name", "task_kind", "task_file", "cwd", "output_expectation",
                     "writes_repository"],
                ["override", "repository_worktree", "worker_result", "task_class",
                "model_level", "model_fallback", "escalation", "worker_mode", "mode",
                "protected_paths", "editable_paths", "acceptance_tests", "required_context",
                "tracker_writes", "local_lane", "independent_validator", "local_lane_scope",
                "prompt_transport", "dependencies", "mutation_targets", "remote_authority",
                "review_of", "review_kind", "prior_head", "routing_assessment"], "job")
        job_id = identifier(job["job_id"], "job_id")
        validate_writer_intent(job, job_id)
        brief_contract_record(job)
        if job_id in seen:
            invalid(f"duplicate job_id: {job_id}")
        seen.add(job_id)
        text(job["name"], "name", 200)
        text(job["output_expectation"], "output_expectation", 10000)
        if "worker_mode" in job and "mode" in job and job["worker_mode"] != job["mode"]:
            invalid(f"{job_id}: worker_mode and mode disagree")
        selected_worker_mode = worker_mode(job.get("worker_mode", job.get("mode", "literal")))
        acceptance_tests = path_list(job.get("acceptance_tests"), f"{job_id}.acceptance_tests")
        local_lane_opt_in = job.get("local_lane", False)
        if type(local_lane_opt_in) is not bool:
            invalid(f"{job_id}.local_lane must be explicitly true or false")
        independent_validator = (independent_validator_record(job["independent_validator"])
                                if "independent_validator" in job else None)
        editable_paths = path_list(job.get("editable_paths"), f"{job_id}.editable_paths")
        protected_paths = validate_protected_paths(
            protected_paths_record(job.get("protected_paths"), acceptance_tests), editable_paths,
            base=manifest_path.parent,
        )
        if not isinstance(job["task_kind"], str) or job["task_kind"] not in policy["routes"]:
            invalid("task_kind must be ordinary, deep_research, or shopping")
        task_class = identifier(job["task_class"], "task_class") if "task_class" in job else None
        ensure_task_admitted(job["task_kind"], job_id)
        route = dict(policy["routes"][job["task_kind"]])
        prompt_transport = job.get("prompt_transport", "agent_prompt")
        if prompt_transport not in ("agent_prompt", "pane_text"):
            invalid(f"{job_id}: prompt_transport must be agent_prompt or pane_text")
        if prompt_transport != "agent_prompt" and route["mode"] != "agent":
            invalid(f"{job_id}: pane_text prompt transport requires an agent route")
        model = None
        override = job.get("override")
        if override is not None:
            fields(override, ["instruction"], ["runtime", "model", "provider", "effort"], "override")
            text(override["instruction"], "override instruction", 10000)
        explicit = override is not None and any(key in override for key in ("runtime", "model", "provider", "effort"))
        requested_override = dict(override or {})
        local_route_selection = None
        local_config = policy.get("local_lane")
        if local_lane_opt_in:
            if job["task_kind"] != "ordinary":
                invalid(f"{job_id}: local_lane is an ordinary host route; it cannot change a jail task")
            if local_config is None:
                raise JobError("unavailable_capability", f"{job_id}: local_lane is not configured")
            task_class, local_scope = ensure_local_lane_admitted(
                local_config, task_class, job.get("local_lane_scope"), job_id)
            if any(key in job for key in ("model_level", "model_fallback", "escalation")):
                invalid(f"{job_id}: local_lane is outside the integer model ladder")
            expected_tuple = {"runtime": "pi", "model": local_config["model"],
                              "provider": local_config["provider"], "effort": local_config["effort"]}
            actual_tuple = {key: override.get(key) if override is not None else None
                            for key in expected_tuple}
            actual_tuple["runtime"] = HARNESS_ALIASES.get(actual_tuple["runtime"], actual_tuple["runtime"])
            actual_tuple["provider"] = (billing_pool(actual_tuple["provider"])
                                         if actual_tuple["provider"] else None)
            if actual_tuple != expected_tuple:
                invalid(f"{job_id}: local_lane requires the complete approved Pi/Qwen tuple in override")
            if not job["writes_repository"]:
                invalid(f"{job_id}: local_lane requires a throwaway repository worktree")
            if not acceptance_tests:
                invalid(f"{job_id}: local_lane requires named acceptance tests")
            if independent_validator is None:
                invalid(f"{job_id}: local_lane requires an independent_validator argv")
            route = {"mode": "agent", "runtime": "pi"}
            local_route_selection = {
                "lane": "local_lane", "choice": "explicit_opt_in", "level": None,
                "model": local_config["model"], "effort": local_config["effort"],
                "runtime": "pi", "provider": local_config["provider"],
                "requested": {"model": local_config["model"], "effort": local_config["effort"],
                              "harness": "pi", "provider": local_config["provider"]},
                "fallback": None,
            }
        elif independent_validator is not None:
            invalid(f"{job_id}: independent_validator is reserved for the local_lane route")
        elif "local_lane_scope" in job:
            invalid(f"{job_id}: local_lane_scope is reserved for the local_lane route")
        elif (local_config is not None and explicit
              and override.get("provider") == local_config["provider"]):
            invalid(f"{job_id}: llama-local requires the explicit local_lane opt-in")
        if override is not None and "effort" in override:
            if route["mode"] != "agent":
                invalid(f"{job_id}: effort overrides are not supported for {route['mode']} routes")
            effort = text(override["effort"], "effort", 32)
            effort_runtime = route["runtime"]
            if "runtime" in override:
                effort_runtime = identifier(override["runtime"], "override runtime")
                effort_runtime = HARNESS_ALIASES.get(effort_runtime, effort_runtime)
            # P2 completion must not mask malformed or unsupported effort input.
            if effort_runtime is not None:
                validate_effort(effort_runtime, effort)
        selection = local_route_selection
        if "model_level" in job:
            integer(job["model_level"], "model_level", 1, 8)
        if "escalation" in job:
            if "model_level" not in job:
                invalid("escalation requires an explicit model_level")
            validate_escalation(job["escalation"], job["model_level"])
            if (job["writes_repository"]
                    and job["escalation"]["failure"]["category"] != "reasoning"):
                invalid(f"{job_id}: implementation escalation requires a concrete reasoning failure")
        if "model_fallback" in job and "model_level" not in job:
            invalid("model_fallback requires an explicit model_level")
        if ("model_level" in job or "model_fallback" in job) and route["mode"] != "agent":
            invalid("model ladder applies only to ordinary agents; preserve the jail route")
        if "model_fallback" in job and explicit:
            invalid("explicit model/harness/provider/effort requests are pinned; fallback requires a new authorized request")
        if "model_level" in job and ladder_config is None:
            invalid("model_level requires a model_ladder policy")
        if ("model_level" in job and job["model_level"] > 1
                and "escalation" not in job):
            pinned_fields = set((override or ())).intersection(
                {"runtime", "model", "provider", "effort"})
            if len(pinned_fields) < 4:
                invalid("levels above 1 require structured escalation evidence or a complete explicit tuple")
        if route["mode"] == "agent" and ladder_config is not None:
            use_ladder = not explicit and (route["runtime"] is None or "model_level" in job)
            if use_ladder:
                selection = select(ladder_levels, job.get("model_level", ladder_config["default_level"]),
                                   job.get("model_fallback"))
                selection["ladder_sha256"] = digest(ladder_bytes)
                route["runtime"] = selection["runtime"]
                model = selection["model"]
                override = {"instruction": (override or {}).get("instruction", "Configured model ladder selection"),
                            "effort": selection["effort"]}
            elif explicit:
                # Complete partial pins from one validated ladder tuple.  A
                # custom provider or an unlisted runtime remains an explicit
                # full-route capability check; partial versions fail closed.
                pin_fields = {key: override[key] for key in
                              ("runtime", "model", "provider", "effort") if key in override}
                # Every partial pin must become one complete, pool-accounted
                # tuple.  A named runtime with no compatible CSV tuple fails
                # closed instead of launching ambient native defaults.
                if len(pin_fields) < 4:
                    completed = complete_tuple(
                        ladder_levels,
                        job.get("model_level"),
                        pin_fields,
                        policy_runtime=route["runtime"],
                    )
                    route["runtime"] = completed["runtime"]
                    model = completed["model"]
                    provider = completed["provider"]
                    if not any(key in pin_fields for key in ("model", "effort")):
                        # Harness/provider pins restrict selection, not capacity.
                        # Keep them distinct from user-specified model/effort.
                        selection = {**completed, "ladder_sha256": digest(ladder_bytes)}
                    override = {**override, "runtime": completed["runtime"],
                                "model": completed["model"],
                                "provider": completed["provider"],
                                "effort": completed["effort"]}
        if override is not None:
            fields(override, ["instruction"], ["runtime", "model", "provider", "effort"], "override")
            text(override["instruction"], "override instruction", 10000)
            if "runtime" in override:
                requested_runtime = identifier(override["runtime"], "override runtime")
                named = HARNESS_ALIASES.get(requested_runtime, requested_runtime)
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
                provider = billing_pool(text(override["provider"], "provider", 100))
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", provider) is None:
                    invalid("provider must be a simple provider identifier")
            else:
                provider = None
        else:
            provider = None
        if selection is not None:
            provider = selection["provider"]
        if (local_config is not None and not local_lane_opt_in and provider == "llama-local"):
            invalid(f"{job_id}: llama-local requires the explicit local_lane opt-in")
        if route["mode"] == "jail" and provider is not None:
            invalid("provider overrides are unsupported on the jail route; preserve the research boundary")
        runtime = route["runtime"]
        validate_provider_harness(runtime, provider)
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
        if runtime == "claude-code" and provider not in (None, "claude"):
            raise JobError("unavailable_capability", "Claude provider override has no verified native adapter")
        task_path = absolute(job["task_file"], manifest_path.parent)
        task = read_regular(task_path).decode("utf-8")
        text(task, "task")
        if "review_of" not in job:
            validate_task_output_paths(task, job_id, task_path)
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
        required_context = None
        if "required_context" in job:
            required_context = validate_required_context(
                job["required_context"], base=manifest_path.parent,
                require_test_fixture=route["mode"] == "jail",
                jail_visible=route["mode"] == "jail",
            )
        elif route["mode"] == "jail":
            invalid(f"{job_id}: jailed work requires a required_context manifest with tests and fixtures")
        prepared_job = {**job, "task_file": task_path, "cwd": cwd,
                        "task": task, "route": route, "model": model,
                        "prompt_transport": prompt_transport,
                        "provider": provider, "kind": policy["runtime_kinds"][runtime],
                        "worker_mode": selected_worker_mode,
                        "acceptance_tests": acceptance_tests,
                        "editable_paths": editable_paths,
                        "protected_paths": protected_paths}
        if required_context is not None:
            prepared_job["required_context"] = required_context
        if independent_validator is not None:
            prepared_job["independent_validator"] = independent_validator
        if override is not None:
            # Keep the completed tuple (including an effort filled from the
            # ladder) in the durable request consumed by preflight/effects.
            prepared_job["override"] = override
        if selection is not None:
            prepared_job["model_route"] = selection
            prepared_job["override"] = override
        if route["mode"] == "jail":
            timeout_policy = policy["jail_stream_idle_timeout_ms"]
            prepared_job["jail_stream_idle_timeout_ms"] = timeout_policy["task_classes"].get(
                task_class, timeout_policy["default"])
        for error in brief_contract_errors(prepared_job):
            invalid(f"{job_id}: {error}")
        if selection is not None and explicit and not local_lane_opt_in:
            prepared_job["override"] = requested_override
        prepared_job["requested_override"] = requested_override
        prepared_job["route_decision"] = decision_for(prepared_job, policy)
        jobs.append(prepared_job)
    if retry_override:
        if ("pi" in retry_override
                and any(job.get("local_lane") is True for job in jobs)):
            invalid("local_lane cannot use a run-private Pi agent directory; preserve configured MCP and extensions")
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


def validate_receipt(raw, expected, hashes=None, *, label="receipt", identity_error=None):
    """Validate attempt identity and optionally bind receipt bytes to artifact hashes.

    Callers retain their own outcome policy and independently read artifact bytes.
    Collection validates before reading artifacts, then supplies hashes once read.
    """
    receipt = fields(parse_json(raw), ["schema_version", "request_id", "job_id", "attempt_id",
                                     "outcome", "artifacts", "unresolved"], label=label)
    version(receipt["schema_version"])
    if (receipt["request_id"], receipt["job_id"], receipt["attempt_id"]) != expected:
        if identity_error is not None:
            raise identity_error
        raise JobError("conflict", "receipt belongs to a different request/job/attempt")
    revision = None if hashes is None else digest(raw + encoded(hashes))
    return receipt, revision
