"""Durable state seam for reducer-driven job runs."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .records import JobError, atomic_bytes, encoded, load_json


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
