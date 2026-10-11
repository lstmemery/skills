"""Durable runner for the effect-producing lifecycle reducer."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
import time
from typing import Any, Callable

from .lifecycle import Effect, EffectPort, EffectReceipt, EffectUnknown, JobLifecycle
from .store import StateStore

try:  # The transport exception is kept separate to preserve its public API.
    from .transport import EffectUnknown as TransportEffectUnknown, RateLimited
except ImportError:  # pragma: no cover - permits isolated reducer imports.
    TransportEffectUnknown = EffectUnknown

    class RateLimited(Exception):
        def __init__(self, signal):
            self.signal = signal


@dataclass
class RunResult(Mapping[str, Any]):
    """Projected run result plus the durable state used to produce it."""

    state: dict[str, Any]
    result: dict[str, Any]
    unresolved_effect: bool = False

    def __getitem__(self, key: str) -> Any:
        return self.result[key]

    def __iter__(self):
        return iter(self.result)

    def __len__(self) -> int:
        return len(self.result)

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self.result)


class CompatibilityMismatch(AssertionError):
    """Legacy and reducer projections or checkpoints differ."""


class UtcClock:
    """Small injectable wall clock used for durable effect timestamps."""

    def __call__(self) -> float:
        return time.time()


def _json_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":"), ensure_ascii=True) == json.dumps(
        right, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def compare_compatibility(legacy_result: Any, reducer_result: Any,
                          legacy_state: Any = None, reducer_state: Any = None) -> None:
    """Compare JSON result and persisted state with stable key ordering."""
    if not _json_equal(legacy_result, reducer_result):
        raise CompatibilityMismatch("reducer result JSON differs from the legacy Engine result")
    if legacy_state is not None or reducer_state is not None:
        if not _json_equal(legacy_state, reducer_state):
            raise CompatibilityMismatch("reducer persisted state differs from the legacy Engine state")


def _new_job(spec: Mapping[str, Any], index: int) -> dict[str, Any]:
    value = deepcopy(dict(spec))
    value.setdefault("job_id", value.get("spec", {}).get("job_id", f"job-{index}"))
    value.setdefault("spec", deepcopy(dict(spec)))
    value.setdefault("attempt_id", "0" * 32)
    value.setdefault("phase", "pending")
    value.setdefault("pane_id", None)
    value.setdefault("previous_pane_ids", [])
    value.setdefault("pending_effect", None)
    value.setdefault("history", [])
    value.setdefault("observed", None)
    value.setdefault("activity_seen", False)
    value.setdefault("settled", False)
    value.setdefault("issue", None)
    value.setdefault("workspace_closed", False)
    return value


def _initial_state(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    jobs = request.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("request jobs must be an array")
    return {"schema_version": 1, "request": deepcopy(dict(request)),
            "jobs": [_new_job(job, index) for index, job in enumerate(jobs)]}


def _jobs(state: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = state.get("jobs")
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise ValueError("lifecycle state jobs must be an array of objects")
    return jobs


def _project(state: dict[str, Any], unresolved: bool) -> dict[str, Any]:
    jobs = _jobs(state)
    projected = []
    for job in jobs:
        spec = job.get("spec") if isinstance(job.get("spec"), Mapping) else job
        projected.append({
            "job_id": spec.get("job_id", job.get("job_id")),
            "phase": job.get("phase", "pending"),
            "attempt_id": job.get("attempt_id"),
            "pane_id": job.get("pane_id"),
            "pending_effect": deepcopy(job.get("pending_effect")),
            "observed": deepcopy(job.get("observed")),
            "settled": job.get("settled", False),
            "issue": job.get("issue"),
            "workspace_closed": job.get("workspace_closed", False),
        })
    complete = bool(jobs) and all(job.get("settled") is True for job in jobs)
    partial = any(job.get("issue") for job in jobs)
    batch_state = "unresolved_effect" if unresolved else "partial" if partial else "collected" if complete else "active"
    request = state.get("request") if isinstance(state.get("request"), Mapping) else {}
    return {"schema_version": 1, "request_id": request.get("request_id"),
            "batch_state": batch_state, "jobs": projected,
            "unresolved_effect": unresolved}


def _job_snapshot(state: dict[str, Any], index: int) -> dict[str, Any]:
    return deepcopy(_jobs(state)[index])


def _set_job(state: dict[str, Any], index: int, value: Mapping[str, Any]) -> None:
    _jobs(state)[index] = deepcopy(dict(value))


def _normalize_receipt(effect: Effect, value: Any) -> EffectReceipt | EffectUnknown:
    if isinstance(value, EffectReceipt):
        return value
    if isinstance(value, EffectUnknown):
        return value
    if isinstance(value, TransportEffectUnknown):
        return EffectUnknown(str(value), effect)
    if isinstance(value, Mapping):
        return EffectReceipt(effect.action, effect.job_id, effect.attempt_id, dict(value))
    raise TypeError("effect port returned neither a receipt nor an unknown outcome")


def _execute(port: Any, effect: Effect) -> EffectReceipt | EffectUnknown:
    try:
        value = port.execute(effect)
    except (EffectUnknown, TransportEffectUnknown) as error:
        return EffectUnknown(str(error), effect)
    except RateLimited as error:
        return EffectReceipt(effect.action, effect.job_id, effect.attempt_id,
                             {"state": "done", "identity_verified": True,
                              "rate_limit": dict(getattr(error, "signal", {}) or {})})
    return _normalize_receipt(effect, value)


def run(request: Mapping[str, Any] | None = None, store: Any = None, effect_port: Any = None,
        clock: Callable[[], float] | None = None, max_steps: int = 10000) -> RunResult:
    """Run reducer transitions until settled or an effect is unresolved.

    The checkpoint before execution records the exact effect and attempt.  A
    successful receipt is checkpointed after reconciliation.  An unknown
    result keeps the pending effect and returns immediately; subsequent calls
    may observe/reconcile it but will never replay it automatically.
    """
    if not hasattr(store, "load") or not hasattr(store, "checkpoint"):
        raise TypeError("store must provide load() and checkpoint(state)")
    if not hasattr(effect_port, "execute"):
        raise TypeError("effect_port must provide execute(effect)")
    state = store.load()
    if state is None:
        if request is None:
            raise ValueError("request is required for a new run")
        state = _initial_state(request)
        store.checkpoint(state)
    elif request is not None:
        stored_request = state.get("request")
        if not _json_equal(stored_request, request):
            raise ValueError("request differs from the pinned lifecycle request")

    lifecycle = JobLifecycle()
    observed: dict[int, Mapping[str, Any]] = {}
    unresolved = False
    steps = 0
    while steps < max_steps:
        steps += 1
        progress = False
        all_settled = True
        for index, job in enumerate(_jobs(state)):
            if job.get("settled") is True:
                continue
            all_settled = False

            # A pending effect is an ambiguous external operation.  Give an
            # adapter an opportunity to reconcile it, but never execute it.
            if job.get("pending_effect") is not None:
                observation = observed.get(index)
                if observation is None and hasattr(effect_port, "observe"):
                    try:
                        observation = effect_port.observe(_job_snapshot(state, index))
                    except Exception as error:  # observation is unverified
                        observation = {"effect_unknown": True, "reason": str(error)}
                transition = lifecycle.step(job, observation)
                _set_job(state, index, transition.next_state)
                store.checkpoint(state)
                if transition.unknown:
                    unresolved = True
                    continue
                observed.pop(index, None)
                progress = True
                continue

            transition = lifecycle.step(job, observed.pop(index, None))
            _set_job(state, index, transition.next_state)
            if not transition.effects:
                store.checkpoint(state)
                progress = progress or transition.reason not in ("job settled", "no effect required")
                if state["jobs"][index].get("settled") is not True and hasattr(effect_port, "observe"):
                    try:
                        observed[index] = effect_port.observe(_job_snapshot(state, index))
                    except Exception as error:
                        observed[index] = {"effect_unknown": True, "reason": str(error)}
                    progress = True
                continue

            # One transition may describe an ordered list.  Execute only the
            # first; the next loop checkpoints and emits the following effect.
            effect = transition.effects[0]
            current_job = state["jobs"][index]
            pending = dict(current_job.get("pending_effect") or {})
            pending.setdefault("action", effect.action)
            pending.setdefault("attempt_id", effect.attempt_id)
            pending.setdefault("started_at", clock() if clock is not None else time.time())
            current_job["pending_effect"] = pending
            payload = dict(effect.payload)
            payload.setdefault("job", _job_snapshot(state, index))
            effect = Effect(effect.action, effect.job_id, effect.attempt_id, payload)
            store.checkpoint(state)
            receipt = _execute(effect_port, effect)
            if isinstance(receipt, EffectUnknown):
                current_job["issue"] = receipt.reason
                unresolved = True
                store.checkpoint(state)
                continue
            receipt_observation = receipt.as_observation()
            # Structural effects identify the new pane/workspace in their
            # receipt; mark that identity verified before reducing.
            if effect.action in {"split", "move"}:
                receipt_observation.setdefault("identity_verified", True)
            transition = lifecycle.step(current_job, receipt_observation)
            _set_job(state, index, transition.next_state)
            store.checkpoint(state)
            progress = True
            if transition.unknown:
                unresolved = True
        if unresolved:
            break
        if all(job.get("settled") is True for job in _jobs(state)):
            break
        if not progress:
            # Observe once more on the next call.  This is a durable active
            # checkpoint, not a failed effect.
            break
    else:
        unresolved = True
    return RunResult(state=deepcopy(state), result=_project(state, unresolved),
                     unresolved_effect=unresolved)


class CompatibilityRunner:
    """Run a reducer and compare its JSON/state with a legacy callable."""

    def __init__(self, legacy_runner: Callable[..., Any] | None = None):
        self.legacy_runner = legacy_runner

    @staticmethod
    def compare(legacy_result: Any, reducer_result: Any,
                legacy_state: Any = None, reducer_state: Any = None) -> None:
        compare_compatibility(legacy_result, reducer_result, legacy_state, reducer_state)

    def run(self, request: Mapping[str, Any] | None = None, *, store: Any,
            effect_port: Any, clock: Callable[[], float] | None = None,
            max_steps: int = 10000) -> RunResult:
        result = run(request, store=store, effect_port=effect_port, clock=clock, max_steps=max_steps)
        if self.legacy_runner is not None:
            legacy = self.legacy_runner(request)
            legacy_result = legacy.result if isinstance(legacy, RunResult) else legacy
            self.compare(legacy_result, result.result, legacy.state if isinstance(legacy, RunResult) else None,
                         result.state)
        return result


__all__ = ["CompatibilityMismatch", "CompatibilityRunner", "RunResult", "UtcClock",
           "compare_compatibility", "run"]
