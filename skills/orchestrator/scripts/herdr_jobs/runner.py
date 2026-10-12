"""Durable runner for the effect-producing lifecycle reducer."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Any, Callable
import uuid

from .admission import normalize_model, normalize_provider
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


def _new_job(spec: Mapping[str, Any], index: int, request: Mapping[str, Any],
             attempt_id_factory: Callable[[], str] | None = None) -> dict[str, Any]:
    value = deepcopy(dict(spec))
    value.setdefault("job_id", value.get("spec", {}).get("job_id", f"job-{index}"))
    value.setdefault("spec", deepcopy(dict(spec)))
    if "attempt_id" not in value:
        value["attempt_id"] = attempt_id_factory() if attempt_id_factory else uuid.uuid4().hex
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
    value.setdefault("rate_limit_recoveries", _prior_rate_limit_recoveries(value))
    value.setdefault("rate_limit_exhausted", False)
    policy = request.get("policy")
    if isinstance(policy, Mapping) and "provider_admission" in policy:
        source = value.get("spec") if isinstance(value.get("spec"), Mapping) else value
        override = source.get("override") if isinstance(source.get("override"), Mapping) else {}
        route = source.get("route") if isinstance(source.get("route"), Mapping) else {}
        resolved_model = value.get("resolved_model") or source.get("model") or override.get("model")
        provider = (value.get("provider") or source.get("provider") or override.get("provider")
                    or (resolved_model.split("/", 1)[0] if isinstance(resolved_model, str) and "/" in resolved_model
                        else route.get("runtime", "default")))
        value.setdefault("provider", normalize_provider(provider))
        value.setdefault("admission_model", normalize_model(resolved_model or "default"))
        value.setdefault("admission_lease_id", "managed-" + hashlib.sha256(
            json.dumps({"request_id": request.get("request_id", ""),
                        "job_id": value["job_id"], "attempt_id": value["attempt_id"]},
                       sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:40])
        value.setdefault("admission_acquired", False)
        value.setdefault("admission_wait", None)
        value.setdefault("rate_limit_seen", False)
        value.setdefault("rate_limit_until", None)
    return value


def _prior_rate_limit_recoveries(job: Mapping[str, Any]) -> int:
    history = job.get("history")
    if isinstance(history, list) and any(
            isinstance(entry, Mapping) and entry.get("action") == "rate_limit_retry"
            for entry in history):
        return 1
    attempts = job.get("attempt_history")
    if isinstance(attempts, list) and any(isinstance(attempt, str) and attempt for attempt in attempts):
        return 1
    return 0


def _initial_state(request: Mapping[str, Any], attempt_id_factory: Callable[[], str] | None = None) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    jobs = request.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("request jobs must be an array")
    return {"schema_version": 1, "request": deepcopy(dict(request)),
            "jobs": [_new_job(job, index, request, attempt_id_factory) for index, job in enumerate(jobs)]}


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


def _observe(effect_port: Any, job: Mapping[str, Any]) -> Mapping[str, Any] | None:
    observe = getattr(effect_port, "observe", None)
    if not callable(observe):
        return None
    runtime_job = getattr(effect_port, "runtime_job", None)
    return observe(runtime_job(job) if callable(runtime_job) else job)


def _startup_issue(observation: Any) -> str | None:
    if observation is None:
        return None
    if not isinstance(observation, Mapping) or observation.get("identity_verified") is not True:
        if isinstance(observation, Mapping) and observation.get("launch_issue"):
            return str(observation["launch_issue"])
        return "worker startup identity is unverified; the task prompt was not sent"
    if observation.get("state") != "idle":
        if observation.get("launch_issue"):
            return str(observation["launch_issue"])
        return "worker was not idle after startup; the task prompt was not sent"
    return None


def _execute(port: Any, effect: Effect, clock: Callable[[], float], default_backoff: float) -> EffectReceipt | EffectUnknown:
    try:
        value = port.execute(effect)
    except (EffectUnknown, TransportEffectUnknown) as error:
        return EffectUnknown(str(error), effect)
    except RateLimited as error:
        signal = dict(getattr(error, "signal", {}) or {})
        delay = signal.get("retry_after_seconds")
        if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
            delay = default_backoff
            signal["retry_after_seconds"] = delay
        signal.setdefault("retry_at", clock() + delay)
        return EffectReceipt(effect.action, effect.job_id, effect.attempt_id,
                             {"state": "done", "identity_verified": True,
                              "rate_limit": signal})
    return _normalize_receipt(effect, value)


def _normalize_rate_limit(observation: Mapping[str, Any] | None, clock: Callable[[], float],
                          default_backoff: float) -> Mapping[str, Any] | None:
    if not isinstance(observation, Mapping) or not isinstance(observation.get("rate_limit"), Mapping):
        return observation
    result = deepcopy(dict(observation))
    signal = dict(result["rate_limit"])
    if signal.get("retry_at") is None:
        delay = signal.get("retry_after_seconds")
        if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
            delay = default_backoff
            signal["retry_after_seconds"] = delay
        signal["retry_at"] = clock() + delay
    result["rate_limit"] = signal
    return result


def run(request: Mapping[str, Any] | None = None, store: Any = None, effect_port: Any = None,
        clock: Callable[[], float] | None = None, max_steps: int = 10000,
        attempt_id_factory: Callable[[], str] | None = None,
        _stop_at_phase: str | None = None) -> RunResult:
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
        state = _initial_state(request, attempt_id_factory)
        store.checkpoint(state)
    elif request is not None:
        stored_request = state.get("request")
        if not _json_equal(stored_request, request):
            raise ValueError("request differs from the pinned lifecycle request")

    for job in _jobs(state):
        job.setdefault("rate_limit_recoveries", _prior_rate_limit_recoveries(job))
        if type(job["rate_limit_recoveries"]) is not int or job["rate_limit_recoveries"] not in (0, 1):
            raise ValueError("rate_limit_recoveries must be 0 or 1")
        job.setdefault("rate_limit_exhausted", False)

    prepare_run = getattr(effect_port, "prepare_run", None)
    if callable(prepare_run):
        prepare_run(state.get("request", {}), state, store)

    lifecycle = JobLifecycle()
    current_time = clock or time.time
    next_attempt_id = attempt_id_factory or (lambda: uuid.uuid4().hex)
    admission_policy = state.get("request", {}).get("policy", {}).get("provider_admission", {})
    default_backoff = admission_policy.get("default_backoff_seconds", 60) if isinstance(admission_policy, Mapping) else 60
    observed: dict[int, Mapping[str, Any]] = {}
    admission_waiting: set[int] = set()
    launch_waiting: set[int] = set()
    unresolved = False
    steps = 0
    while steps < max_steps:
        if (_stop_at_phase is not None
                and all(job.get("settled") is True or job.get("phase") == _stop_at_phase
                        for job in _jobs(state))):
            break
        steps += 1
        progress = False
        all_settled = True
        for index, job in enumerate(_jobs(state)):
            if job.get("settled") is True or job.get("rate_limit_exhausted") is True:
                continue
            all_settled = False
            if index in launch_waiting:
                continue
            if index in admission_waiting:
                continue

            if job.get("phase") == "retry_ready" and job.get("rate_limit_seen") is True:
                if job.get("rate_limit_recoveries", 0) >= 1:
                    job["rate_limit_exhausted"] = True
                    job["issue"] = "provider usage limit persisted after the single bounded recovery; coordinator review required"
                    store.checkpoint(state)
                    continue
                retry_at = job.get("rate_limit_until")
                if (type(retry_at) not in (int, float) or not math.isfinite(retry_at)):
                    continue
                if current_time() < retry_at and job.get("admission_acquired") is not True:
                    continue
                if current_time() >= retry_at and job.get("admission_acquired") is not True:
                    previous_attempt = job.get("attempt_id")
                    fresh_attempt = next_attempt_id()
                    prior_attempts = list(job.get("attempt_history", []))
                    legacy_history = job.get("history", [])
                    legacy_attempts = {
                        entry.get("attempt_id") for entry in legacy_history
                        if isinstance(entry, Mapping) and isinstance(entry.get("attempt_id"), str)
                    } if isinstance(legacy_history, list) else set()
                    used_attempts = set(prior_attempts) | legacy_attempts | {previous_attempt}
                    if not isinstance(fresh_attempt, str) or not fresh_attempt or fresh_attempt in used_attempts:
                        raise ValueError("rate-limited retry requires a fresh attempt ID")
                    prior_attempts.append(previous_attempt)
                    job["attempt_history"] = prior_attempts
                    job["rate_limit_recoveries"] += 1
                    job.setdefault("history", []).append({
                        "action": "rate_limit_retry", "attempt_id": previous_attempt,
                        "retry_at": retry_at, "observed_at": current_time(),
                    })
                    job["attempt_id"] = fresh_attempt
                    job["phase"] = "pending"
                    job["pane_id"] = None
                    job["workspace_id"] = None
                    job["pending_effect"] = None
                    job["observed"] = None
                    job["activity_seen"] = False
                    job["settled"] = False
                    job["rate_limit_seen"] = False
                    job["rate_limit_until"] = None
                    job["admission_wait"] = None
                    job["issue"] = None
                    if job.get("admission_lease_id"):
                        request_id = state.get("request", {}).get("request_id", "")
                        lease_identity = json.dumps({"request_id": request_id,
                                                     "job_id": job.get("job_id"),
                                                     "attempt_id": fresh_attempt},
                                                    sort_keys=True, separators=(",", ":"))
                        job["admission_lease_id"] = "managed-" + hashlib.sha256(
                            lease_identity.encode()).hexdigest()[:40]
                    store.checkpoint(state)

            # A pending effect is an ambiguous external operation.  Give an
            # adapter an opportunity to reconcile it, but never execute it.
            if job.get("pending_effect") is not None:
                pending_action = job["pending_effect"].get("action")
                observation = observed.get(index)
                if observation is None:
                    observation = _observe(effect_port, _job_snapshot(state, index))
                observation = _normalize_rate_limit(observation, current_time, default_backoff)
                transition = lifecycle.step(job, observation)
                _set_job(state, index, transition.next_state)
                current_job = state["jobs"][index]
                if (pending_action == "start" and current_job.get("phase") == "ready"
                        and isinstance(observation, Mapping)):
                    current_job["observed"] = {**dict(observation),
                                               "observed_at": observation.get("observed_at", current_time())}
                if (current_job.get("phase") == "ready"
                        and _startup_issue(current_job.get("observed")) is not None):
                    current_job["issue"] = current_job.get("issue") or _startup_issue(current_job.get("observed"))
                    launch_waiting.add(index)
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
                if transition.reason == "provider admission is waiting":
                    admission_waiting.add(index)
                    continue
                current_job = state["jobs"][index]
                if (current_job.get("phase") == "ready"
                        and _startup_issue(current_job.get("observed")) is not None):
                    current_job["issue"] = (current_job.get("issue")
                                             or _startup_issue(current_job.get("observed")))
                    launch_waiting.add(index)
                    store.checkpoint(state)
                    continue
                if state["jobs"][index].get("settled") is not True and callable(getattr(effect_port, "observe", None)):
                    observation = _observe(effect_port, _job_snapshot(state, index))
                    observation = _normalize_rate_limit(observation, current_time, default_backoff)
                    if observation is not None:
                        observation = dict(observation)
                        observation.setdefault("observed_at", current_time())
                        if current_job.get("phase") == "ready":
                            transition = lifecycle.step(current_job, observation)
                            _set_job(state, index, transition.next_state)
                            current_job = state["jobs"][index]
                            issue = _startup_issue(observation)
                            if issue is not None:
                                current_job["issue"] = (observation.get("launch_issue") or issue)
                                launch_waiting.add(index)
                                store.checkpoint(state)
                                continue
                        observed[index] = observation
                        progress = True
                continue

            # One transition may describe an ordered list.  Execute only the
            # first; the next loop checkpoints and emits the following effect.
            effect = transition.effects[0]
            current_job = state["jobs"][index]
            pending = dict(current_job.get("pending_effect") or {})
            pending.setdefault("action", effect.action)
            pending.setdefault("started_at", clock() if clock is not None else time.time())
            current_job["pending_effect"] = pending
            payload = dict(effect.payload)
            payload.setdefault("job", _job_snapshot(state, index))
            if effect.action in {"admission_acquire", "admission_release"}:
                payload.setdefault("request_id", state.get("request", {}).get("request_id", ""))
                payload.setdefault("policy", state.get("request", {}).get("policy", {}))
            effect = Effect(effect.action, effect.job_id, effect.attempt_id, payload)
            prepare_effect = getattr(effect_port, "prepare_effect", None)
            if callable(prepare_effect):
                effect = prepare_effect(effect, _job_snapshot(state, index))
            store.checkpoint(state)
            receipt = _execute(effect_port, effect, current_time, default_backoff)
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
            current_job = state["jobs"][index]
            if effect.action == "start" and receipt_observation.get("launch_blocked") is True:
                observation = receipt_observation.get("observation")
                if isinstance(observation, Mapping):
                    current_job["observed"] = {**dict(observation),
                                               "observed_at": observation.get("observed_at", current_time())}
                current_job["issue"] = (receipt_observation.get("issue")
                                         or _startup_issue(observation))
                launch_waiting.add(index)
            store.checkpoint(state)
            progress = True
            if transition.reason == "provider admission is waiting":
                admission_waiting.add(index)
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
    unresolved = any(job.get("pending_effect") is not None for job in _jobs(state))
    return RunResult(state=deepcopy(state), result=_project(state, unresolved),
                     unresolved_effect=unresolved)


class CompatibilityRunner:
    """Run a reducer and compare its JSON/state with a legacy callable."""

    def __init__(self, legacy_runner: Callable[..., Any] | None = None,
                 projector: Callable[[Any, Any], tuple[Any, Any]] | None = None):
        if not callable(legacy_runner):
            raise TypeError("legacy_runner is required and must be callable")
        self.legacy_runner = legacy_runner
        self.projector = projector

    @staticmethod
    def compare(legacy_result: Any, reducer_result: Any,
                legacy_state: Any = None, reducer_state: Any = None) -> None:
        compare_compatibility(legacy_result, reducer_result, legacy_state, reducer_state)

    def run(self, request: Mapping[str, Any] | None = None, *, store: Any,
            effect_port: Any, clock: Callable[[], float] | None = None,
            max_steps: int = 10000, stop_at_phase: str | None = None,
            attempt_id_factory: Callable[[], str] | None = None) -> RunResult:
        result = run(request, store=store, effect_port=effect_port, clock=clock, max_steps=max_steps,
                     attempt_id_factory=attempt_id_factory, _stop_at_phase=stop_at_phase)
        if result.unresolved_effect:
            return result
        legacy = self.legacy_runner(request)
        legacy_result = legacy.result if isinstance(legacy, RunResult) else legacy
        legacy_state = legacy.state if isinstance(legacy, RunResult) else None
        reducer_result, reducer_state = result.result, result.state
        if self.projector is not None:
            legacy_result, legacy_state = self.projector(legacy_result, legacy_state)
            reducer_result, reducer_state = self.projector(reducer_result, reducer_state)
        self.compare(legacy_result, reducer_result, legacy_state, reducer_state)
        return result


def launch_compatibility_view(result: Mapping[str, Any], state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize Engine and reducer outputs to the migrated launch-phase contract."""
    jobs = result.get("jobs", [])
    result_jobs = []
    for job in jobs:
        result_jobs.append({key: deepcopy(job.get(key)) for key in (
            "job_id", "phase", "attempt_id", "pane_id", "pending_effect", "observed",
            "settled", "issue", "workspace_closed")})
    request = state.get("request") if isinstance(state.get("request"), Mapping) else {}
    result_view = {
        "schema_version": result.get("schema_version", 1),
        "request_id": result.get("request_id", request.get("request_id")),
        "batch_state": result.get("batch_state"),
        "jobs": result_jobs,
        "unresolved_effect": result.get("unresolved_effect",
                                         any(job.get("pending_effect") is not None for job in jobs)),
    }
    state_jobs = []
    for job in state.get("jobs", []):
        spec = job.get("spec") if isinstance(job.get("spec"), Mapping) else job
        state_jobs.append({
            "job_id": job.get("job_id", spec.get("job_id")),
            **{key: deepcopy(job.get(key)) for key in (
                "phase", "attempt_id", "pane_id", "previous_pane_ids", "workspace_id",
                "pending_effect", "observed", "settled", "issue", "admission_acquired")},
        })
    return result_view, {"request_id": request.get("request_id"), "jobs": state_jobs}


__all__ = ["CompatibilityMismatch", "CompatibilityRunner", "RunResult", "UtcClock",
           "compare_compatibility", "launch_compatibility_view", "run"]
