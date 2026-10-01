"""Validate worker results and record/check terminal worker dispositions."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import sys

from . import records


MAX_REVISION = 2**63 - 1
RESULT_FIELDS = {
    "task_id",
    "assignment_revision",
    "outcome",
    "summary",
    "artifacts",
    "checks",
    "unresolved",
    "next_action",
}
DISPOSITION_FIELDS = {
    "task_id",
    "assignment_revision",
    "disposition",
    "summary",
    "evidence",
}
TERMINAL_DISPOSITIONS = {"completed", "blocked", "failed", "cancelled"}
RESULT_OUTCOMES = {"ready", "blocked", "failed"}
CHECK_STATUSES = {"passed", "failed", "not_run"}
WORKER_MARKERS = {"brief.md", "result.json", "disposition.json"}
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
    except FileNotFoundError as error:
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


def _validate_candidate(candidate):
    candidate = records.fields(candidate, {"repo", "branch", "base", "head"}, label="candidate")
    for key in ("repo", "branch", "base", "head"):
        records.text(candidate[key], f"candidate.{key}")


def validate_result(path, identity, repository_changes=False):
    record = records.fields(
        _read_json(path, "result.json"),
        RESULT_FIELDS,
        {"candidate"},
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
        item = records.fields(item, {"path", "kind"}, label=f"artifacts[{index}]")
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
            {"name", "status", "evidence"},
            label=f"checks[{index}]",
        )
        records.text(item["name"], f"checks[{index}].name")
        _choice(item["status"], CHECK_STATUSES, f"checks[{index}].status")
        records.text(item["evidence"], f"checks[{index}].evidence")

    unresolved = record["unresolved"]
    if not isinstance(unresolved, list):
        records.invalid("unresolved must be an array")
    for index, item in enumerate(unresolved):
        records.text(item, f"unresolved[{index}]")
    if record["outcome"] in {"blocked", "failed"} and not unresolved:
        records.invalid("blocked and failed results require an explanatory unresolved item")

    if "candidate" in record:
        _validate_candidate(record["candidate"])
    if repository_changes and "candidate" not in record:
        records.invalid("candidate record is required for repository changes")


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
                if directory.is_symlink():
                    symlink_directories.append(directory.relative_to(run_root))
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

    result_parser = commands.add_parser("validate-result", help="validate one worker result.json")
    result_parser.add_argument("result_json", type=Path)
    result_parser.add_argument("--task-id", required=True)
    result_parser.add_argument("--revision", type=positive_integer, required=True)
    result_parser.add_argument("--repository-changes", action="store_true")

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
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-result":
            identity = WorkerIdentity(args.task_id, args.revision)
            validate_result(args.result_json, identity, repository_changes=args.repository_changes)
            print(f"VALID: {args.result_json}")
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
        count = check_closeout(args.run_dir)
        print(f"CLOSEOUT READY: {count} worker(s) have terminal dispositions.")
        return 0
    except records.JobError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
