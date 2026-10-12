"""Reducer-facing admission service.

The existing :class:`AdmissionStore` remains the locked durable provider
counter.  This small service keeps policy and lease mutations out of pure
transitions and gives adapters a stable composition point.
"""

from __future__ import annotations

from typing import Any, Mapping

from .admission import AdmissionStore, backoff_delay
from .engine import Engine
from .model_ladder import billing_pool


class AdmissionService:
    def __init__(self, store: AdmissionStore | None = None):
        self.store = store or AdmissionStore()

    def acquire(self, job: Mapping[str, Any], cap: int, request_id: str, *, bypass: bool = False,
                pending_effect: Mapping[str, Any] | None = None) -> dict[str, Any]:
        options = {"bypass_admission": bypass}
        if pending_effect is not None:
            options["pending_effect"] = pending_effect
        return self.store.acquire(job["provider"], job.get("admission_model", "default"),
                                  job["admission_lease_id"], cap, request_id,
                                  **options)

    def release(self, job: Mapping[str, Any], *, reconciliation_evidence: str | None = None) -> dict[str, Any]:
        if reconciliation_evidence is None:
            return self.store.release(job["admission_lease_id"])
        return self.store.release(job["admission_lease_id"], reconciliation_evidence=reconciliation_evidence)

    def record_unknown_effect(self, job: Mapping[str, Any], effect: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.record_unknown_effect(job["admission_lease_id"], effect)

    def rate_limit(self, job: Mapping[str, Any], signal: Mapping[str, Any], default_seconds: int = 60) -> dict[str, Any]:
        delay = signal.get("retry_after_seconds")
        source = signal.get("source") or "metadata"
        if delay is None:
            delay, source = backoff_delay(default_seconds=default_seconds)
        return self.store.note_rate_limit(job["provider"], job.get("admission_model", "default"), delay, source)

    @staticmethod
    def provider_cap(policy: Mapping[str, Any], provider: str) -> int:
        config = policy.get("provider_admission", {})
        return Engine.provider_cap_from_config(config, provider)

    @staticmethod
    def pool(provider: str) -> str:
        return billing_pool(provider)


__all__ = ["AdmissionService"]
