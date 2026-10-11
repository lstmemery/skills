"""Reducer-facing admission service.

The existing :class:`AdmissionStore` remains the locked durable provider
counter.  This small service keeps policy and lease mutations out of pure
transitions and gives adapters a stable composition point.
"""

from __future__ import annotations

from typing import Any, Mapping

from .admission import AdmissionStore, backoff_delay
from .model_ladder import billing_pool


class AdmissionService:
    def __init__(self, store: AdmissionStore | None = None):
        self.store = store or AdmissionStore()

    def acquire(self, job: Mapping[str, Any], cap: int, request_id: str, *, bypass: bool = False) -> dict[str, Any]:
        return self.store.acquire(job["provider"], job.get("admission_model", "default"),
                                  job["admission_lease_id"], cap, request_id,
                                  bypass_admission=bypass)

    def release(self, job: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.release(job["admission_lease_id"])

    def rate_limit(self, job: Mapping[str, Any], signal: Mapping[str, Any], default_seconds: int = 60) -> dict[str, Any]:
        delay = signal.get("retry_after_seconds")
        source = signal.get("source") or "metadata"
        if delay is None:
            delay, source = backoff_delay(default_seconds=default_seconds)
        return self.store.note_rate_limit(job["provider"], job.get("admission_model", "default"), delay, source)

    @staticmethod
    def provider_cap(policy: Mapping[str, Any], provider: str) -> int:
        config = policy.get("provider_admission", {})
        if not isinstance(config, Mapping):
            config = {}
        caps = config.get("provider_caps", {})
        if not isinstance(caps, Mapping):
            caps = {}
        target = billing_pool(provider)
        matched = [cap for name, cap in caps.items()
                   if isinstance(name, str) and billing_pool(name) == target
                   and type(cap) is int and 1 <= cap <= 64]
        default = config.get("default_provider_cap", 4)
        if type(default) is not int or not 1 <= default <= 64:
            default = 4
        return min(matched) if matched else default

    @staticmethod
    def pool(provider: str) -> str:
        return billing_pool(provider)


__all__ = ["AdmissionService"]
