"""Effect-port adapters for the reducer lifecycle."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .lifecycle import Effect, EffectPort, EffectReceipt, EffectUnknown, job_id_of
from .admission_service import AdmissionService
from .transport import Deadline, EffectUnknown as TransportEffectUnknown, NativeTransport


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
            else:
                result = self.transport.effect(effect.action, job, prompt)
        except TransportEffectUnknown as error:
            return EffectUnknown(str(error), effect)
        return EffectReceipt(effect.action, effect.job_id, effect.attempt_id,
                             result if isinstance(result, Mapping) else {})

    def observe(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.transport.observe(job)

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
