"""Synchronize coordinator dispositions from review-response.md into review evidence."""

import json
import os
from pathlib import Path
import re
import stat

from . import records


AXES = ("standards", "spec")
FINDING_EVIDENCE_FIELDS = frozenset(
    {"confidence", "reproducer", "evidence", "unresolved_assumption"}
)
FINDING_CONFIDENCE = frozenset({"high", "medium", "low"})
REVIEW_EVIDENCE_VERSIONS = frozenset({1, 2})
FINDING_LINE = re.compile(
    r"^\s*-\s*`(?P<review>[^`]+)/(?P<finding>[^`]+)`\s*:\s*"
    r"(?P<disposition>fixed|rejected)\s+—\s+(?P<reason>\S.*)$"
)
FINDING_REFERENCE_LINE = re.compile(r"^\s*-\s*`[^`]+/[^`]+`")
IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")
EVIDENCE_NAME = "review-evidence.json"
BACKUP_SUFFIX = ".pre-disposition-sync"


def _read_response(path):
    try:
        return records.read_regular(path).decode("utf-8")
    except FileNotFoundError as error:
        records.invalid(f"missing review-response.md at {path}")
    except (OSError, UnicodeError) as error:
        records.invalid(f"cannot read review-response.md at {path}: {error}")


def _parse_response(text):
    dispositions = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        if not FINDING_REFERENCE_LINE.match(line):
            continue
        match = FINDING_LINE.match(line)
        if match is None:
            records.invalid(f"malformed review disposition at review-response.md:{line_number}")
        key = (match.group("review"), match.group("finding"))
        if key in dispositions:
            records.invalid(f"duplicate disposition for {key[0]}/{key[1]}")
        dispositions[key] = (match.group("disposition"), match.group("reason").strip())
    return dispositions


def _review_directories(task_dir):
    try:
        entries = sorted(task_dir.iterdir(), key=lambda path: path.name)
    except OSError as error:
        records.invalid(f"cannot inspect task directory {task_dir}: {error.strerror or error}")
    directories = []
    for entry in entries:
        if entry.name != "review" and not entry.name.startswith("review-"):
            continue
        if entry.is_symlink():
            records.invalid(f"review directory must not be a symlink: {entry}")
        if entry.is_dir():
            directories.append(entry)
    if not directories:
        records.invalid(f"no review directories found in {task_dir}")
    return directories


def _validate_finding_evidence(finding, review_name, schema_version):
    has_metadata = bool(finding.keys() & FINDING_EVIDENCE_FIELDS)
    finding_id = finding["id"]
    if schema_version == 2 and not has_metadata:
        records.invalid(
            f"{review_name}/{finding_id} schema version 2 finding requires evidence metadata"
        )
    if not has_metadata:
        return

    confidence = finding.get("confidence")
    reproducer = finding.get("reproducer")
    evidence = finding.get("evidence")
    assumption = finding.get("unresolved_assumption")
    if type(confidence) is not str or confidence not in FINDING_CONFIDENCE:
        records.invalid(f"{review_name}/{finding_id} has invalid finding confidence")
    if reproducer is not None and (
        not isinstance(reproducer, str) or not reproducer.strip()
    ):
        records.invalid(f"{review_name}/{finding_id} has an invalid reproducer")
    if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
        records.invalid(f"{review_name}/{finding_id} has invalid finding evidence")
    if assumption is not None and (
        not isinstance(assumption, str) or not assumption.strip()
    ):
        records.invalid(f"{review_name}/{finding_id} has an invalid unresolved assumption")
    has_reproducer = isinstance(reproducer, str) and bool(reproducer.strip())
    has_unresolved_evidence = (
        isinstance(evidence, str)
        and bool(evidence.strip())
        and isinstance(assumption, str)
        and bool(assumption.strip())
    )
    if not (has_reproducer or has_unresolved_evidence):
        records.invalid(
            f"{review_name}/{finding_id} needs a reproducer or evidence and unresolved assumption"
        )


def _load_evidence(review_dir):
    evidence_path = review_dir / EVIDENCE_NAME
    try:
        raw = records.read_regular(evidence_path)
    except FileNotFoundError as error:
        records.invalid(f"missing {EVIDENCE_NAME} at {evidence_path}")
    except OSError as error:
        records.invalid(f"cannot read {EVIDENCE_NAME} at {evidence_path}: {error.strerror or error}")
    try:
        evidence = records.parse_json(raw)
    except records.JobError as error:
        records.invalid(f"invalid {EVIDENCE_NAME} at {evidence_path}: {error}")

    if (
        not isinstance(evidence, dict)
        or type(evidence.get("schema_version")) is not int
        or evidence["schema_version"] not in REVIEW_EVIDENCE_VERSIONS
    ):
        records.invalid(f"invalid {EVIDENCE_NAME} schema in {review_dir.name}")
    axes = evidence.get("axes")
    if not isinstance(axes, dict) or set(axes) != set(AXES):
        records.invalid(f"{review_dir.name} must record both Standards and Spec axes")

    finding_map = {}
    for axis in AXES:
        axis_record = axes[axis]
        if (
            not isinstance(axis_record, dict)
            or axis_record.get("status") != "complete"
            or not isinstance(axis_record.get("findings"), list)
        ):
            records.invalid(f"{review_dir.name} {axis} review is incomplete")
        for finding in axis_record["findings"]:
            if (
                not isinstance(finding, dict)
                or not {"id", "disposition"} <= finding.keys()
                or finding.keys()
                - {"id", "disposition", "reason", *FINDING_EVIDENCE_FIELDS}
            ):
                records.invalid(f"{review_dir.name} has a malformed finding")
            finding_id = finding["id"]
            if not isinstance(finding_id, str) or not IDENTIFIER.fullmatch(finding_id):
                records.invalid(f"{review_dir.name} has an invalid finding ID")
            _validate_finding_evidence(finding, review_dir.name, evidence["schema_version"])
            key = (review_dir.name, finding_id)
            if key in finding_map:
                records.invalid(f"{review_dir.name} repeats finding ID {finding_id}")
            finding_map[key] = finding
    return evidence_path, raw, evidence, finding_map


def _save_backup(path, data):
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.is_symlink():
        records.invalid(f"backup must not be a symlink: {backup}")
    if backup.exists():
        try:
            info = backup.stat()
        except OSError as error:
            records.invalid(f"cannot inspect backup {backup}: {error.strerror or error}")
        if not stat.S_ISREG(info.st_mode):
            records.invalid(f"backup must be a regular file: {backup}")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(backup, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if backup.is_symlink() or not backup.is_file():
            records.invalid(f"backup must be a regular file: {backup}")
        return
    except OSError as error:
        try:
            backup.unlink()
        except OSError:
            pass
        records.invalid(f"cannot write backup {backup}: {error.strerror or error}")


def sync_dispositions(task_dir):
    task_dir = Path(task_dir)
    if task_dir.is_symlink() or not task_dir.is_dir():
        records.invalid(f"task directory does not exist or is a symlink: {task_dir}")
    task_dir = task_dir.resolve()
    response = _parse_response(_read_response(task_dir / "review-response.md"))
    evidence_files = {}
    all_findings = {}
    updated = {}

    for review_dir in _review_directories(task_dir):
        evidence_path, raw, evidence, findings = _load_evidence(review_dir)
        evidence_files[review_dir.name] = (evidence_path, raw)
        all_findings.update(findings)
        updated[review_dir.name] = evidence

    unknown = sorted(set(response) - set(all_findings))
    if unknown:
        names = ", ".join(f"{review}/{finding}" for review, finding in unknown)
        records.invalid(f"review-response.md has unknown finding(s): {names}")
    missing = sorted(set(all_findings) - set(response))
    if missing:
        names = ", ".join(f"{review}/{finding}" for review, finding in missing)
        records.invalid(f"review-response.md is missing disposition(s) for: {names}")

    for key, (disposition, reason) in response.items():
        finding = all_findings[key]
        finding["disposition"] = disposition
        finding["reason"] = reason

    changed = []
    for review_name, evidence in updated.items():
        evidence_path, original = evidence_files[review_name]
        replacement = (
            json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
            + b"\n"
        )
        if replacement != original:
            changed.append((evidence_path, original, replacement))

    for path, original, _replacement in changed:
        _save_backup(path, original)
    for path, _original, replacement in changed:
        try:
            records.atomic_bytes(path, replacement)
        except OSError as error:
            records.invalid(f"cannot write review evidence at {path}: {error.strerror or error}")

    if not changed:
        return 0
    return len(changed)
