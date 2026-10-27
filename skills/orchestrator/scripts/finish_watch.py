"""Coordinator helpers for safely finishing workers and watching results."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from herdr_jobs import records, worker_records
from herdr_jobs.cleanup import close_workspace


OUTCOME_DISPOSITIONS = {
    "ready": "completed",
    "blocked": "blocked",
    "failed": "failed",
}
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
WATCH_STATE_SCHEMA = 1
IGNORED_RESULT_PARTS = {".git", "node_modules", "__pycache__"}


def _task_id(value):
    if not isinstance(value, str) or TASK_ID_PATTERN.fullmatch(value) is None:
        raise records.JobError("invalid_input", "task_id must be a simple path-safe identifier")
    return value


def _regular_json(path, label):
    if path.is_symlink():
        raise records.JobError("invalid_input", f"{label} must not be a symlink: {path}")
    try:
        return records.load_json(path)
    except FileNotFoundError:
        raise records.JobError("invalid_input", f"missing {label}: {path}") from None


def close_herdr_workspace(workspace):
    """Use the managed ownership guard before coordinator workspace cleanup."""
    try:
        close_workspace(workspace)
    except FileNotFoundError as error:
        raise RuntimeError("herdr executable was not found") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("herdr workspace close timed out") from error


def _expected_disposition(task_id, revision, status, summary, evidence):
    return {
        "task_id": task_id,
        "assignment_revision": revision,
        "disposition": status,
        "summary": summary,
        "evidence": evidence,
    }


def _record_disposition(task_dir, record):
    target = task_dir / "disposition.json"
    if target.is_symlink():
        raise records.JobError("invalid_input", f"disposition.json must not be a symlink: {target}")
    if target.exists():
        existing = _regular_json(target, "disposition.json")
        if existing != record:
            raise records.JobError(
                "conflict",
                f"existing disposition differs from the validated result: {target}",
            )
        return "already-recorded"
    worker_records.record_disposition(
        task_dir,
        worker_records.DispositionInput(
            worker_records.WorkerIdentity(record["task_id"], record["assignment_revision"]),
            record["disposition"],
            record["summary"],
            record["evidence"],
        ),
    )
    return "recorded"


def _append_state(state_path, task_id, status, summary):
    safe_summary = " ".join(summary.splitlines()).strip()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"- {timestamp}: {task_id} {status} — {safe_summary}\n"
    if state_path.is_symlink():
        raise records.JobError("invalid_input", f"STATE.md must not be a symlink: {state_path}")
    existing = state_path.read_text(encoding="utf-8")
    if any(
        entry.endswith(f": {task_id} {status} — {safe_summary}")
        for entry in existing.splitlines()
    ):
        return "already-recorded"
    descriptor = os.open(state_path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return "appended"


def finish_task(run_dir, task_id, revision, summary, release_lease, close_workspace,
                invalid_status=None, invalid_evidence=None):
    """Validate and disposition one canonical result, then release its resources.

    Invalid results require an explicit terminal status and coordinator evidence;
    the validator's concrete error is retained in the disposition evidence.
    """
    task_id = _task_id(task_id)
    if type(revision) is not int or revision < 1:
        raise records.JobError("invalid_input", "revision must be a positive integer")
    run_dir = Path(run_dir)
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise records.JobError("invalid_input", f"run directory does not exist or is a symlink: {run_dir}")
    run_dir = run_dir.resolve()
    run_root = run_dir
    task_dir = run_dir / "tasks" / task_id
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise records.JobError("invalid_input", f"canonical task directory is missing: {task_dir}")
    if not task_dir.resolve().is_relative_to(run_root):
        raise records.JobError("invalid_input", f"task directory resolves outside the run: {task_dir}")

    result_path = task_dir / "result.json"
    identity = worker_records.WorkerIdentity(task_id, revision)
    try:
        worker_records.validate_result(result_path, identity)
        result = _regular_json(result_path, "result.json")
    except records.JobError as validation_error:
        if invalid_status is None or invalid_evidence is None or summary is None:
            raise records.JobError(
                validation_error.kind,
                f"invalid result requires --invalid-status, --invalid-evidence, and --summary: {validation_error}",
            ) from validation_error
        if not isinstance(invalid_status, str) or invalid_status not in worker_records.TERMINAL_DISPOSITIONS:
            raise records.JobError("invalid_input", "invalid_status must be a terminal disposition")
        summary = records.text(summary, "summary")
        invalid_evidence = records.text(invalid_evidence, "invalid_evidence")
        evidence = f"result.json validation failed: {validation_error}; coordinator evidence: {invalid_evidence}"
        status = invalid_status
    else:
        if invalid_status is not None or invalid_evidence is not None:
            raise records.JobError("invalid_input", "manual invalid-result fields cannot be used with a valid result")
        status = OUTCOME_DISPOSITIONS[result["outcome"]]
        summary = result["summary"] if summary is None else records.text(summary, "summary")
        evidence = f"result.json (outcome: {result['outcome']})"
    disposition = _expected_disposition(task_id, revision, status, summary, evidence)

    location_path = task_dir / "location.json"
    if location_path.is_symlink():
        raise records.JobError("invalid_input", f"location.json must not be a symlink: {location_path}")
    location = _regular_json(location_path, "location.json") if location_path.exists() else {}
    lease_id = location.get("lease_id", "")
    workspace = location.get("workspace", "")
    if lease_id:
        records.text(lease_id, "location.lease_id", 512)
    if workspace:
        records.text(workspace, "location.workspace", 512)

    locations_path = run_dir / "locations.json"
    locations = _regular_json(locations_path, "locations.json")
    if not isinstance(locations, dict) or not isinstance(locations.get(task_id), dict):
        raise records.JobError("invalid_input", f"locations.json has no task record for {task_id!r}")
    if locations_path.is_symlink():
        raise records.JobError("invalid_input", f"locations.json must not be a symlink: {locations_path}")
    state_path = run_dir / "STATE.md"
    if state_path.is_symlink() or not state_path.is_file():
        raise records.JobError("invalid_input", f"STATE.md is missing or a symlink: {state_path}")

    disposition_action = _record_disposition(task_dir, disposition)
    lease_result = release_lease(lease_id) if lease_id else None
    already_finished = locations[task_id].get("status") == status
    if workspace and not already_finished:
        close_workspace(workspace)

    locations[task_id]["status"] = status
    records.atomic_bytes(locations_path, records.encoded(locations) + b"\n")
    state_action = _append_state(state_path, task_id, status, summary)
    return {
        "task_id": task_id,
        "assignment_revision": revision,
        "status": status,
        "disposition": disposition_action,
        "lease_released": bool(lease_result and lease_result.get("released")),
        "workspace_closed": bool(workspace and not already_finished),
        "state": state_action,
    }


class ResultWatcher:
    """Poll canonical task results while deduplicating content copied in from elsewhere."""

    def __init__(self, run_dir, task_ids, state_file=None):
        self.run_dir = Path(run_dir)
        if self.run_dir.is_symlink() or not self.run_dir.is_dir():
            raise records.JobError("invalid_input", f"run directory does not exist or is a symlink: {run_dir}")
        self.run_dir = self.run_dir.resolve()
        self.run_root = self.run_dir
        self.task_ids = tuple(_task_id(task_id) for task_id in task_ids)
        if not self.task_ids or len(set(self.task_ids)) != len(self.task_ids):
            raise records.JobError("invalid_input", "task list must contain unique task IDs")
        self.canonical_paths = {
            task_id: self.run_dir / "tasks" / task_id / "result.json"
            for task_id in self.task_ids
        }
        self.state_file = Path(state_file) if state_file is not None else self.run_dir / ".watch-results-state.json"
        self._check_state_path()
        if self.state_file.exists():
            state = _regular_json(self.state_file, "watch state")
            self._load_state(state)
        else:
            self.seen_digests = self._scan_all_digests()
            self.reported_task_ids = set()
            self._save_state()

    def poll(self):
        noncanonical = self._scan_noncanonical_digests()
        self.seen_digests.update(noncanonical)
        candidates = []
        for task_id, path in self.canonical_paths.items():
            digest = self._fingerprint(path)
            if digest is None:
                continue
            if task_id in self.reported_task_ids:
                self.seen_digests.add(digest)
            elif digest not in self.seen_digests:
                candidates.append((task_id, digest))

        if not candidates:
            self._save_state()
            return None

        # Recheck the run for coordinator copies immediately before reporting a hit.
        self.seen_digests.update(self._scan_noncanonical_digests())
        for task_id, digest in candidates:
            if digest in self.seen_digests:
                continue
            self.seen_digests.add(digest)
            self.reported_task_ids.add(task_id)
            self._save_state()
            return task_id
        self._save_state()
        return None

    def _check_state_path(self):
        if self.state_file.is_symlink():
            raise records.JobError("invalid_input", f"watch state must not be a symlink: {self.state_file}")
        resolved_parent = self.state_file.parent.resolve()
        if not resolved_parent.is_relative_to(self.run_root):
            raise records.JobError("invalid_input", "watch state must stay inside the run directory")

    def _load_state(self, state):
        records.fields(state, ["schema_version", "seen_digests", "reported_task_ids"], label="watch state")
        records.version(state["schema_version"])
        digests = state["seen_digests"]
        if not isinstance(digests, list) or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in digests
        ):
            raise records.JobError("invalid_input", "watch state seen_digests must be SHA-256 values")
        reported = state["reported_task_ids"]
        if not isinstance(reported, list):
            raise records.JobError("invalid_input", "watch state reported_task_ids must be an array")
        self.seen_digests = set(digests)
        self.reported_task_ids = {_task_id(value) for value in reported}

    def _save_state(self):
        self._check_state_path()
        state = {
            "schema_version": WATCH_STATE_SCHEMA,
            "seen_digests": sorted(self.seen_digests),
            "reported_task_ids": sorted(self.reported_task_ids),
        }
        records.atomic_bytes(self.state_file, records.encoded(state) + b"\n")

    def _fingerprint(self, path):
        if path.is_symlink() or not path.exists():
            return None
        resolved_parent = path.parent.resolve()
        if not resolved_parent.is_relative_to(self.run_root):
            raise records.JobError("invalid_input", f"result path resolves outside the run: {path}")
        try:
            content = records.read_regular(path, 8388608)
        except FileNotFoundError:
            return None
        except records.JobError as error:
            if error.kind == "conflict":
                return None
            raise
        return hashlib.sha256(content).hexdigest()

    def _result_files(self):
        for path in self.run_dir.rglob("result.json"):
            try:
                relative = path.relative_to(self.run_dir)
            except ValueError:
                continue
            if any(part in IGNORED_RESULT_PARTS for part in relative.parts[:-1]):
                continue
            if path.is_symlink():
                continue
            yield path

    def _scan_noncanonical_digests(self):
        canonical = {path.absolute() for path in self.canonical_paths.values()}
        digests = set()
        for path in self._result_files():
            if path.absolute() in canonical:
                continue
            digest = self._fingerprint(path)
            if digest:
                digests.add(digest)
        return digests

    def _scan_all_digests(self):
        digests = self._scan_noncanonical_digests()
        for path in self.canonical_paths.values():
            digest = self._fingerprint(path)
            if digest:
                digests.add(digest)
        return digests


def watch_for_result(run_dir, task_ids, timeout_seconds, interval_seconds, state_file=None,
                     *, clock=time.monotonic, sleep=time.sleep):
    if timeout_seconds < 0 or interval_seconds <= 0:
        raise records.JobError("invalid_input", "timeout must be non-negative and interval must be positive")
    watcher = ResultWatcher(run_dir, task_ids, state_file)
    deadline = clock() + timeout_seconds
    while True:
        task_id = watcher.poll()
        if task_id is not None:
            return task_id
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        sleep(min(interval_seconds, remaining))


def read_task_list(run_dir, task_list):
    run_root = Path(run_dir).resolve()
    path = Path(task_list)
    if path.is_symlink():
        raise records.JobError("invalid_input", "task list must be a regular path inside the run directory")
    path = path.resolve()
    if not path.is_relative_to(run_root):
        raise records.JobError("invalid_input", "task list must be a regular path inside the run directory")
    try:
        content = records.read_regular(path, 1048576).decode("utf-8")
    except UnicodeDecodeError as error:
        raise records.JobError("invalid_input", f"task list is not valid UTF-8: {path}") from error
    task_ids = []
    for line in content.splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            task_ids.append(_task_id(value))
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise records.JobError("invalid_input", "task list must contain unique task IDs")
    return task_ids


def _positive_int(value):
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _nonnegative_float(value):
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not 0 <= parsed < float("inf"):
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


def _positive_float(value):
    parsed = _nonnegative_float(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _caller_path(value):
    """Interpret CLI paths relative to the caller's working directory."""
    return Path(os.path.abspath(os.fspath(value)))


def finish_main(argv=None):
    parser = argparse.ArgumentParser(description="Validate and finish one coordinator task result.")
    parser.add_argument("run_dir", type=_caller_path)
    parser.add_argument("task_id")
    parser.add_argument("--revision", required=True, type=_positive_int)
    parser.add_argument("--summary")
    parser.add_argument("--invalid-status", choices=sorted(worker_records.TERMINAL_DISPOSITIONS))
    parser.add_argument("--invalid-evidence")
    args = parser.parse_args(argv)
    try:
        from herdr_jobs.admission import AdmissionStore

        result = finish_task(
            args.run_dir,
            args.task_id,
            args.revision,
            args.summary,
            AdmissionStore().release,
            close_herdr_workspace,
            args.invalid_status,
            args.invalid_evidence,
        )
    except (records.JobError, OSError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def watch_main(argv=None):
    parser = argparse.ArgumentParser(description="Watch canonical task directories for one new result.")
    parser.add_argument("run_dir", type=_caller_path)
    parser.add_argument("task_list", type=_caller_path)
    parser.add_argument("--timeout-seconds", type=_nonnegative_float, default=3300)
    parser.add_argument("--interval-seconds", type=_positive_float, default=20)
    parser.add_argument("--state-file", type=_caller_path)
    args = parser.parse_args(argv)
    try:
        task_ids = read_task_list(args.run_dir, args.task_list)
        task_id = watch_for_result(
            args.run_dir,
            task_ids,
            args.timeout_seconds,
            args.interval_seconds,
            args.state_file,
        )
    except (records.JobError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if task_id is None:
        print("TIMEOUT")
        return 1
    print(f"DONE {task_id}")
    return 0
