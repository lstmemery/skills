"""Synchronize coordinator dispositions from review-response.md into review evidence."""

import os
from pathlib import Path
import stat

from . import records
from . import review_evidence
# Preserve existing constant exports while keeping the contract in its owner.
from .review_evidence import (
    AXES, EVIDENCE_NAME, FINDING_EVIDENCE_FIELDS, IDENTIFIER, REVIEW_EVIDENCE_SCHEMA_VERSIONS,
)


BACKUP_SUFFIX = ".pre-disposition-sync"


def _read_response(path):
    try:
        return records.read_regular(path).decode("utf-8")
    except FileNotFoundError as error:
        records.invalid(f"missing review-response.md at {path}")
    except (OSError, UnicodeError) as error:
        records.invalid(f"cannot read review-response.md at {path}: {error}")


def _parse_response(text):
    try:
        return review_evidence.parse_review_response(text, reject_malformed=True)
    except ValueError as error:
        records.invalid(str(error))


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
    try:
        documents = [
            review_evidence.read_disposition_evidence(directory)
            for directory in _review_directories(task_dir)
        ]
        changed = review_evidence.apply_dispositions(documents, response)
    except review_evidence.ReviewEvidenceError as error:
        records.invalid(str(error))

    for change in changed:
        _save_backup(change.path, change.original)
    for change in changed:
        try:
            records.atomic_bytes(change.path, change.replacement)
        except OSError as error:
            records.invalid(f"cannot write review evidence at {change.path}: {error.strerror or error}")

    if not changed:
        return 0
    return len(changed)
