"""Effect-port adapters for the reducer lifecycle."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Mapping

from .lifecycle import Effect, EffectPort, EffectReceipt, EffectUnknown, job_id_of
from .admission_service import AdmissionService
from .engine import Engine
from .transport import Deadline, EffectUnknown as TransportEffectUnknown, NativeTransport, RateLimited


class NativeHerdrAdapter(EffectPort):
    """Production effect port backed by the existing ``NativeTransport``.

    The adapter receives a complete job snapshot in ``Effect.payload``.  This
    keeps the reducer independent of Herdr while preserving the transport
    factory and its existing prompt/observation verification rules.
    """

    def __init__(self, binding_path: str | None = None, deadline: Deadline | None = None,
                 transport: Any | None = None, admission_service: AdmissionService | None = None):
        self.transport = transport or NativeTransport(binding_path, deadline or Deadline(30))
        self.admission_service = admission_service
        self.engine = None
        self.capabilities = None
        self._runtime_bases: dict[tuple[str, str], dict[str, Any]] = {}
        self.default_backoff_seconds = 60

    def prepare_run(self, request: Mapping[str, Any], state: Mapping[str, Any], store: Any) -> None:
        """Bind Engine prompt and job construction to one reducer run."""
        if callable(getattr(self.transport, "preflight", None)):
            existing = {"jobs": [{
                "spec": job["spec"],
                "phase": job.get("phase", "pending"),
                "resolved_model": job.get("resolved_model"),
                "pending_effect": job.get("pending_effect"),
            } for job in state.get("jobs", [])]}
            self.capabilities = self.transport.preflight(request, existing=existing)
        else:
            self.capabilities = {
                "models": {spec["job_id"]: spec.get("model") for spec in request["jobs"]},
                "binding": {"jail_export": None},
            }
        admission_store = self.admission_service.store if self.admission_service is not None else None
        store_path = getattr(store, "path", None)
        root = Path(store_path).parent if store_path is not None else Path.cwd()
        self.engine = Engine(root, self.transport,
                             getattr(self.transport, "deadline", None),
                             admission_store=admission_store)
        self.engine.state = {"request": deepcopy(dict(request))}
        self._runtime_bases = {}
        policy = request.get("policy")
        admission = policy.get("provider_admission") if isinstance(policy, Mapping) else None
        if isinstance(admission, Mapping):
            value = admission.get("default_backoff_seconds", 60)
            if type(value) is int and 1 <= value <= 3600:
                self.default_backoff_seconds = value

    def _record_rate_limit(self, job: Mapping[str, Any], signal: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(signal))
        if self.admission_service is None:
            return result
        if job.get("rate_limit_seen") is True:
            retry_at = job.get("rate_limit_until")
            if retry_at is not None:
                result["retry_at"] = retry_at
            return result
        delay = result.get("retry_after_seconds")
        if (type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0):
            result["retry_after_seconds"] = None
        rate_limit_job = dict(job)
        resolved_model = rate_limit_job.get("resolved_model")
        if isinstance(resolved_model, str) and resolved_model.strip():
            rate_limit_job["admission_model"] = resolved_model
        backoff = self.admission_service.rate_limit(
            rate_limit_job, result, default_seconds=self.default_backoff_seconds)
        result["retry_at"] = backoff["retry_at"]
        return result

    def runtime_job(self, state_job: Mapping[str, Any]) -> dict[str, Any]:
        if self.engine is None or self.capabilities is None:
            raise RuntimeError("native adapter is not bound to a reducer run")
        key = (state_job.get("job_id", ""), state_job.get("attempt_id", ""))
        base = self._runtime_bases.get(key)
        if base is None:
            base = self.engine.reducer_runtime_job(state_job, self.capabilities)
            self._runtime_bases[key] = deepcopy(base)
        runtime_job = deepcopy(base)
        for name in runtime_job:
            if name in state_job and name not in ("spec", "attempt_id"):
                runtime_job[name] = deepcopy(state_job[name])
        return runtime_job

    def prepare_effect(self, effect: Effect, state_job: Mapping[str, Any]) -> Effect:
        if effect.action in {"admission_acquire", "admission_release"}:
            return effect
        job = self.runtime_job(state_job)
        payload = deepcopy(dict(effect.payload))
        payload.update(job=job, prompt=self.engine.prompt(job))
        return Effect(effect.action, effect.job_id, effect.attempt_id, payload)

    def execute(self, effect: Effect) -> EffectReceipt | EffectUnknown:
        payload = dict(effect.payload)
        job = payload.get("job", payload)
        prompt = payload.get("prompt", "")
        try:
            if effect.action == "workspace_close":
                result = self.transport.close_workspace(job)
            elif effect.action == "collect":
                result = self.transport.observe(job)
            elif effect.action == "admission_acquire":
                if self.admission_service is None:
                    raise EffectUnknown("admission effects require an AdmissionService", effect)
                result = self.admission_service.acquire(
                    job,
                    self.admission_service.provider_cap(payload.get("policy", {}), job["provider"]),
                    payload.get("request_id", ""),
                    bypass=payload.get("bypass_admission") is True,
                )
            elif effect.action == "admission_release":
                if self.admission_service is None:
                    raise EffectUnknown("admission effects require an AdmissionService", effect)
                result = self.admission_service.release(job)
            elif (effect.action in {"start", "prompt", "jail"}
                  and self.admission_service is not None
                  and job.get("admission_acquired") is True):
                policy = self.engine.state["request"].get("policy", {})
                cap = self.admission_service.provider_cap(policy, job["provider"])
                admission = self.admission_service.acquire(
                    job, cap, self.engine.state["request"].get("request_id", ""))
                if not admission["admitted"]:
                    if admission.get("lease_held") is True:
                        self.admission_service.release(job)
                    wait = {key: admission.get(key) for key in
                            ("reason", "provider", "provider_active", "cap", "retry_at")}
                    return EffectReceipt(effect.action, effect.job_id, effect.attempt_id,
                                         {"admission_wait": wait, "lease_released": True})
                result = self.transport.effect(effect.action, job, prompt)
            else:
                result = self.transport.effect(effect.action, job, prompt)
        except RateLimited as error:
            if self.admission_service is None:
                raise
            signal = self._record_rate_limit(job, getattr(error, "signal", {}) or {})
            return EffectReceipt(effect.action, effect.job_id, effect.attempt_id,
                                 {"state": "done", "identity_verified": True,
                                  "rate_limit": signal})
        except TransportEffectUnknown as error:
            return EffectUnknown(str(error), effect)
        return EffectReceipt(effect.action, effect.job_id, effect.attempt_id,
                             result if isinstance(result, Mapping) else {})

    def observe(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        runtime_job = self.runtime_job(job)
        observation = self.transport.observe(runtime_job)
        if (isinstance(observation, Mapping)
                and isinstance(observation.get("rate_limit"), Mapping)):
            observation = deepcopy(dict(observation))
            observation["rate_limit"] = self._record_rate_limit(runtime_job, observation["rate_limit"])
        return observation

    def workspace_is_open(self, job: Mapping[str, Any]) -> bool:
        return self.transport.workspace_is_open(job)


class InMemoryEffectAdapter(EffectPort):
    """Deterministic effect adapter for reducer and compatibility tests."""

    def __init__(self, outcomes: Mapping[str, Any] | None = None,
                 observations: Mapping[str, Mapping[str, Any]] | None = None):
        self.outcomes = dict(outcomes or {})
        self.observations = {key: deepcopy(dict(value)) for key, value in (observations or {}).items()}
        self.effects: list[Effect] = []

    def execute(self, effect: Effect) -> EffectReceipt | EffectUnknown:
        self.effects.append(effect)
        value = self.outcomes.get(effect.action, {})
        if isinstance(value, EffectUnknown):
            return value
        if callable(value):
            value = value(effect)
        if isinstance(value, EffectUnknown):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("in-memory effect outcomes must be mappings")
        return EffectReceipt(effect.action, effect.job_id, effect.attempt_id, deepcopy(dict(value)))

    def observe(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        return deepcopy(self.observations.get(job_id_of(job),
                                              {"state": "unknown", "identity_verified": False}))


class DeterministicObservationAdapter:
    """Observation-only adapter with stable job/attempt keyed responses."""

    def __init__(self, observations: Mapping[str, Mapping[str, Any]]):
        self.observations = {key: deepcopy(dict(value)) for key, value in observations.items()}

    def observe(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        key = job_id_of(job)
        value = self.observations.get(key)
        if value is None:
            raise RuntimeError(f"no deterministic observation for {key}")
        return deepcopy(value)


__all__ = ["DeterministicObservationAdapter", "InMemoryEffectAdapter", "NativeHerdrAdapter"]
