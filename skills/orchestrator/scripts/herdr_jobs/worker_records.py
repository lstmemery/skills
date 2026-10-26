"""Validate worker results and record/check terminal worker dispositions."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys

from . import records
from . import preflight_batch
from . import review_dispositions
from . import jail_brief


MAX_REVISION = 2**63 - 1
RESULT_REQUIRED_FIELDS = (
    "task_id",
    "assignment_revision",
    "outcome",
    "summary",
    "artifacts",
    "checks",
    "unresolved",
    "next_action",
)
RESULT_OPTIONAL_FIELDS = ("candidate",)
CANDIDATE_FIELDS = ("repo", "branch", "base", "head")
ARTIFACT_FIELDS = ("path", "kind")
CHECK_FIELDS = ("name", "status", "evidence")
DISPOSITION_FIELDS = {
    "task_id",
    "assignment_revision",
    "disposition",
    "summary",
    "evidence",
}
TERMINAL_DISPOSITIONS = {"completed", "blocked", "failed", "cancelled"}
RESULT_OUTCOMES = {"ready", "partial", "blocked", "failed"}
CHECK_STATUSES = {"passed", "failed", "not_run"}
OUTCOME_NORMALIZATIONS = {
    "complete": "ready",
    "completed": "ready",
    "success": "ready",
    "ask": "blocked",
}
WORKER_MARKERS = {"brief.md", "result.json", "disposition.json"}
REPOSITORY_COPY_MARKER = ".git"
CAPTURE_MANIFEST_NAME = "manifest.json"
CAPTURE_COMPLETION_MARKER = "COMPLETE"
OVERWRITE_MESSAGE = "disposition already exists: {path}; pass --replace to update it"


@dataclass(frozen=True)
class WorkerIdentity:
    task_id: str
    assignment_revision: int


@dataclass(frozen=True)
class WorkerEntry:
    identity: WorkerIdentity
    directory: Path


@dataclass(frozen=True)
class DispositionInput:
    identity: WorkerIdentity
    disposition: str
    summary: str
    evidence: str


def _read_json(path, label):
    try:
        return records.load_json(path)
    except FileNotFoundError:
        records.invalid(f"missing {label} at {path}")
    except records.JobError as error:
        records.invalid(f"{label} at {path}: {error}")
    except OSError as error:
        records.invalid(f"cannot read {label} at {path}: {error.strerror or error}")


def _choice(value, choices, label):
    value = records.text(value, label)
    if value not in choices:
        records.invalid(f"{label} must be one of: {', '.join(sorted(choices))}")
    return value


def _check_identity(record, expected, label):
    if record["task_id"] != expected.task_id:
        records.invalid(
            f"{label} task_id mismatch: expected {expected.task_id!r}, got {record['task_id']!r}"
        )
    if record["assignment_revision"] != expected.assignment_revision:
        records.invalid(
            f"{label} assignment_revision mismatch: expected {expected.assignment_revision}, "
            f"got {record['assignment_revision']!r}"
        )


def check_brief_redaction(brief_path):
    """Warn when a brief names known leak-path hosts without a redaction section."""
    try:
        content = Path(brief_path).read_text(encoding="utf-8")
    except OSError as error:
        records.invalid(f"cannot read brief at {brief_path}: {error.strerror or error}")
    # Markdown fenced code is illustrative content, not a section of the brief.
    # CommonMark closes a fence only with its opening character and at least
    # its opening length. An unclosed fence extends through end of document.
    outside_fences = []
    fence_char = None
    fence_length = 0
    for line in content.splitlines():
        if fence_char is None:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if opening:
                marker, info = opening.groups()
                if marker[0] == "~" or "`" not in info:
                    fence_char = marker[0]
                    fence_length = len(marker)
                    continue
            outside_fences.append(line)
            continue

        closing = re.match(r"^ {0,3}([`~]+)[ \t]*$", line)
        if (closing and closing.group(1)[0] == fence_char
                and len(closing.group(1)) >= fence_length
                and len(set(closing.group(1))) == 1):
            fence_char = None
            fence_length = 0
    content = "\n".join(outside_fences)
    if not re.search(r"truenas|system\.general|\bwg0\b|secrets?", content, re.I):
        return False
    if re.search(r"^#{1,6}\s+Output redaction\s*$", content, re.I | re.M):
        return False
    return True


def _validate_candidate(candidate):
    candidate = records.fields(candidate, CANDIDATE_FIELDS, label="candidate")
    for key in CANDIDATE_FIELDS:
        records.text(candidate[key], f"candidate.{key}")


def result_schema_markdown():
    """Render the worker result contract from the validator's field and enum definitions."""
    example_values = {
        "task_id": "task-id",
        "assignment_revision": 1,
        "outcome": "ready",
        "summary": "What was produced and what it establishes.",
        "artifacts": [{"path": "artifact-path", "kind": "report"}],
        "checks": [{"name": "check", "status": "passed", "evidence": "evidence-path"}],
        "unresolved": [],
        "next_action": "Present the artifact to the user.",
    }
    example = {field: example_values[field] for field in RESULT_REQUIRED_FIELDS}
    candidate = {key: f"{key}-value" for key in CANDIDATE_FIELDS}
    required = ", ".join(f"`{field}`" for field in RESULT_REQUIRED_FIELDS)
    optional = ", ".join(f"`{field}`" for field in RESULT_OPTIONAL_FIELDS)
    outcomes = ", ".join(f"`{value}`" for value in sorted(RESULT_OUTCOMES))
    statuses = ", ".join(f"`{value}`" for value in sorted(CHECK_STATUSES))
    artifact_fields = ", ".join(f"`{field}`" for field in ARTIFACT_FIELDS)
    check_fields = ", ".join(f"`{field}`" for field in CHECK_FIELDS)
    candidate_fields = ", ".join(f"`{field}`" for field in CANDIDATE_FIELDS)
    validate_parser = next(
        action.choices["validate-result"]
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    validate_options = {
        action.dest: action.option_strings[0]
        for action in validate_parser._actions
        if action.option_strings
    }
    validate_command = (
        "python3 scripts/worker-records.py validate-result "
        f'"$WORKER_DIR/result.json" {validate_options["task_id"]} "$TASK_ID" '
        f'{validate_options["revision"]} "$REVISION"'
    )
    return "\n".join((
        "## Worker result record (generated by `worker-records.py schema --format md`)",
        "",
        "Copy this whole section verbatim into each worker brief or per-run CONTRACT. "
        "Do not rewrite the example or allowed values.",
        "",
        "```json",
        json.dumps(example, indent=2, ensure_ascii=False),
        "```",
        "",
        f"Required top-level fields: {required}.",
        f"Optional top-level fields: {optional}; omit `candidate` for non-repository tasks.",
        f"`outcome` must be one of {outcomes}; `assignment_revision` must be an "
        f"integer from 1 to {MAX_REVISION}.",
        "Brief outcomes map to this field as follows: `success` -> `ready`, `ask` or "
        "`blocked` -> `blocked`, `partial` -> `partial`, and `failed` -> `failed`.",
        "`task_id` and `assignment_revision` must match the identity supplied to `validate-result`.",
        "Text values must be non-empty strings with no NUL and at most 100,000 characters.",
        f"`artifacts` is an array of objects with exactly {artifact_fields}; it may be "
        "empty only when the outcome is not `ready`.",
        f"`checks` is an array of objects with exactly {check_fields}; `status` must "
        f"be one of {statuses}.",
        "`checks[].evidence` must be a non-empty string, such as a path or concise check result.",
        "`unresolved` is an array of non-empty strings; `partial`, `blocked`, and `failed` "
        "require at least one explanatory item.",
        "Validate each result with this command from the orchestrator skill directory:",
        "",
        "```sh",
        validate_command,
        "```",
        "",
        "For repository changes, validate with `--repository-changes` and include "
        f"`candidate` with exactly {candidate_fields}.",
        "When present, `candidate` must be an object with non-empty string values.",
        "Unknown fields and duplicate JSON keys are invalid.",
        "",
        "Repository-change candidate shape:",
        "",
        "```json",
        json.dumps({"candidate": candidate}, indent=2, ensure_ascii=False),
        "```",
    )) + "\n"


def validate_result(path, identity, repository_changes=False):
    _validate_result_record(_read_json(path, "result.json"), identity, repository_changes)


def _validate_result_record(record, identity, repository_changes=False):
    record = records.fields(
        record,
        RESULT_REQUIRED_FIELDS,
        RESULT_OPTIONAL_FIELDS,
        "result.json",
    )
    records.text(record["task_id"], "task_id")
    records.integer(record["assignment_revision"], "assignment_revision", 1, MAX_REVISION)
    _check_identity(record, identity, "result.json")
    _choice(record["outcome"], RESULT_OUTCOMES, "outcome")
    records.text(record["summary"], "summary")
    records.text(record["next_action"], "next_action")

    artifacts = record["artifacts"]
    if not isinstance(artifacts, list):
        records.invalid("artifacts must be an array")
    for index, item in enumerate(artifacts):
        item = records.fields(item, ARTIFACT_FIELDS, label=f"artifacts[{index}]")
        records.text(item["path"], f"artifacts[{index}].path")
        records.text(item["kind"], f"artifacts[{index}].kind")
    if record["outcome"] == "ready" and not artifacts:
        records.invalid("ready result requires at least one artifact")

    checks = record["checks"]
    if not isinstance(checks, list):
        records.invalid("checks must be an array")
    for index, item in enumerate(checks):
        item = records.fields(
            item,
            CHECK_FIELDS,
            label=f"checks[{index}]",
        )
        records.text(item["name"], f"checks[{index}].name")
        _choice(item["status"], CHECK_STATUSES, f"checks[{index}].status")
        evidence = item["evidence"]
        if not isinstance(evidence, str) or not evidence.strip():
            records.invalid(
                f"checks[{index}].evidence must be a non-empty string; "
                "use a path or concise check result"
            )
        records.text(evidence, f"checks[{index}].evidence")

    unresolved = record["unresolved"]
    if not isinstance(unresolved, list):
        records.invalid("unresolved must be an array")
    for index, item in enumerate(unresolved):
        records.text(item, f"unresolved[{index}]")
    if record["outcome"] in {"partial", "blocked", "failed"} and not unresolved:
        records.invalid("partial, blocked, and failed results require an explanatory unresolved item")

    if "candidate" in record:
        _validate_candidate(record["candidate"])
    if repository_changes and "candidate" not in record:
        records.invalid("candidate record is required for repository changes")


def normalize_result(source, output, identity, repository_changes=False):
    source = Path(source)
    output = Path(output)
    try:
        same_file = source.resolve() == output.resolve()
    except (OSError, RuntimeError):
        records.invalid("result input and output paths cannot be resolved safely")
    if same_file:
        records.invalid("normalized result output must differ from the input path")

    record = _read_json(source, "result.json")
    changes = []
    if isinstance(record, dict):
        if not repository_changes and record.get("candidate", object()) is None:
            del record["candidate"]
            changes.append({"path": "candidate", "action": "removed null optional candidate"})

        outcome = record.get("outcome")
        if isinstance(outcome, str) and outcome in OUTCOME_NORMALIZATIONS:
            record["outcome"] = OUTCOME_NORMALIZATIONS[outcome]
            changes.append({
                "path": "outcome",
                "action": f"mapped {outcome} to {record['outcome']}",
            })

        artifacts = record.get("artifacts")
        if isinstance(artifacts, list):
            for index, item in enumerate(artifacts):
                if isinstance(item, dict) and set(item) == {"path"}:
                    item["kind"] = "artifact"
                    changes.append({
                        "path": f"artifacts[{index}].kind",
                        "action": "filled missing kind as artifact",
                    })

        checks = record.get("checks")
        if isinstance(checks, list):
            for index, item in enumerate(checks):
                if isinstance(item, str):
                    checks[index] = {
                        "name": item,
                        "status": "not_run",
                        "evidence": "Bare-string check supplied no status or evidence.",
                    }
                    changes.append({
                        "path": f"checks[{index}]",
                        "action": "wrapped bare-string check as not_run",
                    })

    _validate_result_record(record, identity, repository_changes)
    try:
        records.atomic_bytes(output, records.encoded(record) + b"\n")
    except OSError as error:
        records.invalid(f"cannot write normalized result at {output}: {error.strerror or error}")
    return {"source": str(source), "output": str(output), "changes": changes}


def _validate_disposition(record, worker):
    record = records.fields(record, DISPOSITION_FIELDS, label="disposition.json")
    records.text(record["task_id"], "disposition.task_id")
    records.integer(
        record["assignment_revision"],
        "disposition.assignment_revision",
        1,
        MAX_REVISION,
    )
    _check_identity(record, worker.identity, "disposition.json")
    _choice(record["disposition"], TERMINAL_DISPOSITIONS, "disposition")
    records.text(record["summary"], "disposition.summary")
    records.text(record["evidence"], "disposition.evidence")


def _relative_directory(value, label):
    value = records.text(value, label)
    parts = value.split("/")
    if value.startswith("/") or "\\" in value or any(part in {"", ".", ".."} for part in parts):
        records.invalid(f"{label} must stay inside the run directory")
    return Path(*parts)


def _is_capture_or_repository_copy(directory):
    """Report whether evidence in the directory itself marks it as copied material.

    Discovery may only skip a subtree when the directory carries copy evidence:
    a git checkout or linked worktree marker (``.git`` file or directory), or a
    capture written by the local code-review convention (``manifest.json`` plus
    the ``COMPLETE`` marker). Directory names such as ``capture`` or ``*-repo``
    are never evidence, so a live worker with such a name is still discovered.
    """
    if (directory / REPOSITORY_COPY_MARKER).exists():
        return True
    return (
        (directory / CAPTURE_MANIFEST_NAME).is_file()
        and (directory / CAPTURE_COMPLETION_MARKER).is_file()
    )


def _direct_worker_markers(directory):
    """Report which worker markers sit directly inside an evidence-carrying directory.

    A genuine captured copy holds worker-shaped files only below its root, in
    nested historical task directories, so markers directly inside an
    evidence-carrying directory mean the directory itself is a live worker's
    task directory and must be discovered instead of silently pruned. Entries
    that are directories (including symlinks to directories) never count, so
    nested markers stay excluded. An unreadable directory is a closeout error,
    matching the walk error policy.
    """
    try:
        with os.scandir(directory) as entries:
            names = {entry.name for entry in entries if not entry.is_dir()}
    except OSError as error:
        records.invalid(
            f"cannot inspect copy-evidenced directory {directory}: "
            f"{error.strerror or error}"
        )
    return WORKER_MARKERS.intersection(names)


def _parse_roster(run_dir):
    roster_path = run_dir / "workers.json"
    if roster_path.is_symlink():
        records.invalid("workers.json must not be a symlink")
    if not roster_path.exists():
        return []
    roster = records.fields(_read_json(roster_path, "workers.json"), {"workers"}, label="workers.json")
    if not isinstance(roster["workers"], list):
        records.invalid("workers.json.workers must be an array")

    entries = []
    seen_task_ids = set()
    seen_directories = set()
    for index, item in enumerate(roster["workers"]):
        label = f"workers.json.workers[{index}]"
        item = records.fields(
            item,
            {"task_id", "assignment_revision", "directory"},
            label=label,
        )
        task_id = records.text(item["task_id"], f"{label}.task_id")
        revision = records.integer(
            item["assignment_revision"],
            f"{label}.assignment_revision",
            1,
            MAX_REVISION,
        )
        directory = _relative_directory(item["directory"], f"{label}.directory")
        if task_id in seen_task_ids:
            records.invalid(f"workers.json has duplicate task_id {task_id!r}")
        if directory in seen_directories:
            records.invalid(f"workers.json has duplicate directory {directory.as_posix()!r}")
        seen_task_ids.add(task_id)
        seen_directories.add(directory)
        entries.append(WorkerEntry(WorkerIdentity(task_id, revision), directory))
    return entries


def _discover_worker_directories(run_root):
    discovered = set()
    symlink_directories = []

    def fail_walk(error):
        raise error

    try:
        for current, directory_names, filenames in os.walk(
            run_root,
            topdown=True,
            followlinks=False,
            onerror=fail_walk,
        ):
            current_path = Path(current)
            traversable = []
            for name in directory_names:
                directory = current_path / name
                relative_directory = directory.relative_to(run_root)
                if directory.is_symlink():
                    symlink_directories.append(relative_directory)
                elif _is_capture_or_repository_copy(directory):
                    if _direct_worker_markers(directory):
                        discovered.add(relative_directory)
                    continue
                else:
                    traversable.append(name)
            directory_names[:] = traversable

            if current_path != run_root and WORKER_MARKERS.intersection(filenames):
                discovered.add(current_path.relative_to(run_root))
    except OSError as error:
        records.invalid(f"cannot inspect run directory {run_root}: {error.strerror or error}")
    return discovered, symlink_directories


def _validate_roster_directories(run_root, workers):
    issues = []
    safe_directories = set()
    for worker in workers:
        worker_dir = run_root / worker.directory
        try:
            resolved = worker_dir.resolve()
        except (OSError, RuntimeError):
            issues.append(
                f"worker {worker.identity.task_id!r}: directory cannot be resolved safely"
            )
            continue
        if not resolved.is_relative_to(run_root):
            issues.append(
                f"worker {worker.identity.task_id!r}: directory resolves outside the run directory"
            )
        else:
            safe_directories.add(worker.directory)
    return safe_directories, issues


def record_disposition(worker_dir, disposition, replace=False):
    if worker_dir.is_symlink() or not worker_dir.is_dir():
        records.invalid(f"worker directory does not exist or is a symlink: {worker_dir}")
    records.text(disposition.identity.task_id, "task_id")
    records.integer(
        disposition.identity.assignment_revision,
        "assignment_revision",
        1,
        MAX_REVISION,
    )
    _choice(disposition.disposition, TERMINAL_DISPOSITIONS, "disposition")
    records.text(disposition.summary, "summary")
    records.text(disposition.evidence, "evidence")

    record = {
        "task_id": disposition.identity.task_id,
        "assignment_revision": disposition.identity.assignment_revision,
        "disposition": disposition.disposition,
        "summary": disposition.summary,
        "evidence": disposition.evidence,
    }
    target = worker_dir / "disposition.json"
    if (target.exists() or target.is_symlink()) and not replace:
        records.invalid(OVERWRITE_MESSAGE.format(path=target))
    try:
        records.atomic_bytes(target, records.encoded(record) + b"\n")
    except OSError as error:
        records.invalid(f"cannot write disposition.json at {target}: {error.strerror or error}")


def check_closeout(run_dir):
    if run_dir.is_symlink() or not run_dir.is_dir():
        records.invalid(f"run directory does not exist or is a symlink: {run_dir}")
    run_root = run_dir.resolve()
    workers = _parse_roster(run_dir)
    discovered, symlink_directories = _discover_worker_directories(run_root)
    roster_directories = {worker.directory for worker in workers}
    safe_directories, issues = _validate_roster_directories(run_root, workers)

    for directory in sorted(discovered - roster_directories, key=Path.as_posix):
        issues.append(
            f"unrostered worker-shaped directory: {directory.as_posix()}"
        )
    for directory in sorted(symlink_directories, key=Path.as_posix):
        issues.append(f"cannot inspect symlinked directory: {directory.as_posix()}")

    for worker in workers:
        if worker.directory not in safe_directories:
            continue
        worker_dir = run_root / worker.directory
        if not worker_dir.is_dir():
            issues.append(
                f"worker {worker.identity.task_id!r}: worker directory is missing: "
                f"{worker.directory.as_posix()}"
            )
            continue
        disposition_path = worker_dir / "disposition.json"
        try:
            record = _read_json(disposition_path, "disposition.json")
            _validate_disposition(record, worker)
        except records.JobError as error:
            issues.append(
                f"worker {worker.identity.task_id!r} revision "
                f"{worker.identity.assignment_revision}: {error}"
            )

    if issues:
        details = "\n".join(f"- {issue}" for issue in issues)
        records.invalid(f"CLOSEOUT INCOMPLETE:\n{details}")
    return len(workers)


def positive_integer(value):
    if not value.isascii() or not value.isdigit():
        raise argparse.ArgumentTypeError("must be a positive integer")
    parsed = int(value)
    if parsed < 1 or parsed > MAX_REVISION:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare_parser = commands.add_parser(
        "prepare-preflight", help="stage legacy managed-job review outputs for integration preflight"
    )
    prepare_parser.add_argument("--managed-run", type=Path, required=True)
    prepare_parser.add_argument("--job-id", required=True)
    prepare_parser.add_argument("--task-id", required=True)
    prepare_parser.add_argument("--repo", type=Path, required=True)
    prepare_parser.add_argument("--target", default="main")
    prepare_parser.add_argument("--candidate", required=True)
    reviews = prepare_parser.add_mutually_exclusive_group(required=True)
    reviews.add_argument("--review-output", type=Path, action="append")
    reviews.add_argument("--review-job", action="append", help="review job ID, in round order")
    prepare_parser.add_argument("--author-alias-job", action="append", default=[],
                                help="explicit fix-author job ID accepted in source sidecars")
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--review-response", type=Path)

    schema_parser = commands.add_parser(
        "schema", help="print the current worker result contract"
    )
    schema_parser.add_argument("--format", choices=("md",), default="md")

    normalize_parser = commands.add_parser(
        "normalize-result", help="repair known benign result shapes and validate the copy"
    )
    normalize_parser.add_argument("result_json", type=Path)
    normalize_parser.add_argument("--output", type=Path, required=True)
    normalize_parser.add_argument("--task-id", required=True)
    normalize_parser.add_argument("--revision", type=positive_integer, required=True)
    normalize_parser.add_argument("--repository-changes", action="store_true")

    result_parser = commands.add_parser("validate-result", help="validate one worker result.json")
    result_parser.add_argument("result_json", type=Path)
    result_parser.add_argument("--task-id", required=True)
    result_parser.add_argument("--revision", type=positive_integer, required=True)
    result_parser.add_argument("--repository-changes", action="store_true")

    brief_parser = commands.add_parser(
        "check-brief", help="check brief redaction and optional jailed-task bundle preflight"
    )
    brief_parser.add_argument("brief", type=Path)
    brief_parser.add_argument(
        "--jail", action="store_true",
        help="validate the brief's jail pre-launch inventory against its staged bundle",
    )
    brief_parser.add_argument(
        "--bundle-root", type=Path,
        help="root of the staged jail bundle (required with --jail)",
    )

    disposition_parser = commands.add_parser("record-disposition", help="write a terminal coordinator disposition")
    disposition_parser.add_argument("worker_dir", type=Path)
    disposition_parser.add_argument("--task-id", required=True)
    disposition_parser.add_argument("--revision", type=positive_integer, required=True)
    disposition_parser.add_argument("--status", choices=sorted(TERMINAL_DISPOSITIONS), required=True)
    disposition_parser.add_argument("--summary", required=True)
    disposition_parser.add_argument("--evidence", required=True)
    disposition_parser.add_argument("--replace", action="store_true")

    closeout_parser = commands.add_parser("check-closeout", help="require a terminal disposition for every worker")
    closeout_parser.add_argument("run_dir", type=Path)

    sync_parser = commands.add_parser(
        "sync-dispositions",
        help="copy coordinator dispositions from review-response.md into review evidence",
    )
    sync_parser.add_argument("task_dir", type=Path)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-preflight":
            print(json.dumps(preflight_batch.prepare(args), sort_keys=True))
            return 0
        if args.command == "schema":
            print(result_schema_markdown(), end="")
            return 0
        if args.command == "normalize-result":
            identity = WorkerIdentity(args.task_id, args.revision)
            audit = normalize_result(
                args.result_json,
                args.output,
                identity,
                repository_changes=args.repository_changes,
            )
            print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "validate-result":
            identity = WorkerIdentity(args.task_id, args.revision)
            validate_result(
                args.result_json,
                identity,
                repository_changes=args.repository_changes,
            )
            print(f"VALID: {args.result_json}")
            return 0
        if args.command == "check-brief":
            if args.jail and args.bundle_root is None:
                parser.error("check-brief --jail requires --bundle-root")
            if args.bundle_root is not None and not args.jail:
                parser.error("check-brief --bundle-root requires --jail")
            redaction_warning = check_brief_redaction(args.brief)
            if args.jail:
                issues = jail_brief.validate_jail_brief(args.brief, args.bundle_root)
                if redaction_warning:
                    issues.append(
                        f"{args.brief} mentions TrueNAS, system.general, wg0, or secrets "
                        "but has no Output redaction section."
                    )
                if issues:
                    for issue in issues:
                        print(f"ERROR: {issue}", file=sys.stderr)
                    return 1
                print(f"BRIEF OK: {args.brief} (jail inventory and bundle inputs checked)")
                return 0
            if redaction_warning:
                print(
                    f"WARNING: {args.brief} mentions TrueNAS, system.general, wg0, "
                    "or secrets but has no Output redaction section.",
                    file=sys.stderr,
                )
                return 1
            print(f"BRIEF OK: {args.brief}")
            return 0
        if args.command == "record-disposition":
            disposition = DispositionInput(
                WorkerIdentity(args.task_id, args.revision),
                args.status,
                args.summary,
                args.evidence,
            )
            record_disposition(args.worker_dir, disposition, replace=args.replace)
            print(f"RECORDED: {args.worker_dir / 'disposition.json'}")
            return 0
        if args.command == "sync-dispositions":
            count = review_dispositions.sync_dispositions(args.task_dir)
            if count:
                print(f"SYNCED: {count} review evidence file(s)")
            else:
                print("SYNCED: dispositions already match review evidence")
            return 0
        count = check_closeout(args.run_dir)
        print(f"CLOSEOUT READY: {count} worker(s) have terminal dispositions.")
        return 0
    except records.JobError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
