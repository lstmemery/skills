"""Durable state seam for reducer-driven job runs."""

from __future__ import annotations

from copy import deepcopy
import fcntl
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from .records import (JobError, atomic_bytes, digest, encoded, fields, integer, load_json, now,
                      parse_json, text, version)


class CoordinatorCheckpointStore:
    """Append compact takeover summaries without replacing earlier records."""

    MAX_RECORD_BYTES = 2 * 1024 * 1024
    MAX_ACTIVE_OWNERS = 128
    MAX_DECISIONS = 512

    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()

    def _latest_from_fd(self, descriptor: int) -> dict[str, Any] | None:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise JobError("conflict", "coordinator checkpoint log must be a regular file")
        if metadata.st_size == 0:
            return None
        start = max(0, metadata.st_size - self.MAX_RECORD_BYTES - 1)
        os.lseek(descriptor, start, os.SEEK_SET)
        data = os.read(descriptor, self.MAX_RECORD_BYTES + 1)
        if start:
            boundary = data.find(b"\n")
            if boundary < 0:
                raise JobError("conflict", "latest coordinator checkpoint exceeds the record limit")
            data = data[boundary + 1:]
        if not data.endswith(b"\n"):
            raise JobError("conflict", "coordinator checkpoint log ends with a partial record")
        lines = [line for line in data.split(b"\n") if line]
        if not lines or len(lines[-1]) + 1 > self.MAX_RECORD_BYTES:
            raise JobError("conflict", "latest coordinator checkpoint is invalid or too large")
        return self._validate(parse_json(lines[-1]))

    def _validate(self, record: Any) -> dict[str, Any]:
        fields(record, ["schema_version", "sequence", "recorded_at", "request_id", "coordinator_owner",
                        "active_owners", "unresolved_decisions", "next_safe_action", "summary_sha256",
                        "reason", "reconciles_sequence"], label="coordinator checkpoint")
        version(record["schema_version"])
        integer(record["sequence"], "coordinator checkpoint sequence", 1, 9223372036854775807)
        text(record["recorded_at"], "coordinator checkpoint timestamp", 100)
        text(record["request_id"], "coordinator checkpoint request ID", 256)
        text(record["coordinator_owner"], "coordinator checkpoint owner", 256)
        text(record["next_safe_action"], "coordinator next safe action", 4096)
        if not isinstance(record["summary_sha256"], str) or len(record["summary_sha256"]) != 64:
            raise JobError("conflict", "coordinator checkpoint summary digest is invalid")
        if any(character not in "0123456789abcdef" for character in record["summary_sha256"]):
            raise JobError("conflict", "coordinator checkpoint summary digest is invalid")
        if record["reason"] not in ("initial", "state_reconciled", "checkpoint", "takeover_reconciled"):
            raise JobError("conflict", "coordinator checkpoint reason is invalid")
        reconciles_sequence = record["reconciles_sequence"]
        if reconciles_sequence is not None:
            integer(reconciles_sequence, "reconciled coordinator checkpoint sequence", 1,
                    9223372036854775807)
            if reconciles_sequence >= record["sequence"]:
                raise JobError("conflict", "coordinator checkpoint cannot reconcile a future sequence")
        for name in ("active_owners", "unresolved_decisions"):
            values = record[name]
            limit = self.MAX_ACTIVE_OWNERS if name == "active_owners" else self.MAX_DECISIONS
            if not isinstance(values, list) or len(values) > limit:
                raise JobError("conflict", f"coordinator checkpoint {name} is invalid")
        for owner in record["active_owners"]:
            fields(owner, ["job_id", "attempt_id", "agent_name", "pane_id", "workspace_id", "phase"],
                   label="coordinator active owner")
            for name in ("job_id", "attempt_id", "agent_name", "phase"):
                text(owner[name], f"coordinator active owner {name}", 512)
            for name in ("pane_id", "workspace_id"):
                if owner[name] is not None:
                    text(owner[name], f"coordinator active owner {name}", 512)
        for decision in record["unresolved_decisions"]:
            fields(decision, ["job_id", "attempt_id", "kind", "detail"], label="coordinator decision")
            for name in ("job_id", "attempt_id", "kind"):
                text(decision[name], f"coordinator decision {name}", 512)
            text(decision["detail"], "coordinator decision detail", 512)
        summary = {name: record[name] for name in (
            "request_id", "coordinator_owner", "active_owners", "unresolved_decisions", "next_safe_action")}
        if digest(encoded(summary)) != record["summary_sha256"]:
            raise JobError("conflict", "coordinator checkpoint summary digest does not match its contents")
        return record

    def latest(self) -> dict[str, Any] | None:
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise JobError("conflict", "coordinator checkpoint log is unavailable") from error
        with os.fdopen(descriptor, "rb"):
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            return self._latest_from_fd(descriptor)

    def append(self, summary: Mapping[str, Any], reason: str, reconciles_sequence: int | None = None):
        values = dict(summary)
        fields(values, ["request_id", "coordinator_owner", "active_owners", "unresolved_decisions",
                        "next_safe_action"], label="coordinator checkpoint summary")
        text(reason, "coordinator checkpoint reason", 64)
        if reason not in ("initial", "state_reconciled", "checkpoint", "takeover_reconciled"):
            raise JobError("invalid_input", "invalid coordinator checkpoint reason")
        payload_digest = digest(encoded(values))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600)
        except OSError as error:
            raise JobError("conflict", "coordinator checkpoint log cannot be opened safely") from error
        with os.fdopen(descriptor, "r+b"):
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            previous = self._latest_from_fd(descriptor)
            if previous is not None and previous["request_id"] != values["request_id"]:
                raise JobError("conflict", "coordinator checkpoint log belongs to another request")
            sequence = 1 if previous is None else previous["sequence"] + 1
            record = self._validate({"schema_version": 1, "sequence": sequence, "recorded_at": now(),
                                     **values, "summary_sha256": payload_digest, "reason": reason,
                                     "reconciles_sequence": reconciles_sequence})
            payload = encoded(record) + b"\n"
            if len(payload) > self.MAX_RECORD_BYTES:
                raise JobError("conflict", "coordinator checkpoint exceeds 64 KiB")
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("coordinator checkpoint append made no progress")
                offset += written
            os.fsync(descriptor)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return record


class StateStore:
    """Atomic JSON store used by the reducer runner.

    The store intentionally does not interpret lifecycle fields.  Validation
    belongs to the reducer and the legacy Engine migration boundary; this
    class guarantees only that checkpoints are JSON objects and durable.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()

    def load(self) -> dict[str, Any] | None:
        try:
            value = load_json(self.path, 67108864)
        except FileNotFoundError:
            return None
        if not isinstance(value, dict):
            raise JobError("conflict", "stored lifecycle state must be an object")
        return deepcopy(value)

    def save(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return self.checkpoint(state)

    def checkpoint(self, state: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(state, Mapping):
            raise JobError("invalid_input", "lifecycle checkpoint must be an object")
        snapshot = deepcopy(dict(state))
        try:
            payload = encoded(snapshot)
        except (TypeError, ValueError, UnicodeError) as error:
            raise JobError("invalid_input", f"lifecycle checkpoint is not JSON: {error}") from error
        if len(payload) > 67108864:
            raise JobError("conflict", "lifecycle checkpoint exceeds 64 MiB")
        atomic_bytes(self.path, payload + b"\n")
        return snapshot


class JobRunStore(StateStore):
    """Named interface used by the lifecycle design document."""


__all__ = ["JobRunStore", "StateStore"]
