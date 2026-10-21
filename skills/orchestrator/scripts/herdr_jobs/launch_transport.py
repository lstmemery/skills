"""Launch boundary used by both managed and native dispatch.

transport.NativeTransport contains low-level CLI primitives; it is not a public
worker launcher. This adapter requires batch preflight and a live, bound lease
before invoking any of those primitives.
"""

from copy import deepcopy
from pathlib import Path

from .admission import dispatch_lease_id, normalize_model, normalize_provider
from .records import JobError, digest, encoded, now, save
from .routing_decision import decision_digest, validate_decision
from .transport import NativeTransport


class LaunchTransport(NativeTransport):
    def preflight(self, request, existing=None, status_only=False):
        self._verified = {}
        self._authorized = {}
        policy = request["policy"]
        if not status_only:
            for spec in request["jobs"]:
                validate_decision(spec, policy)
                if spec["route"]["mode"] == "agent" and spec.get("model") is None:
                    raise JobError("decision_needed", "new launches require an exact model; native defaults are not discovery")
                if spec["route"]["runtime"] == "claude-code" and spec.get("model") in ("opus", "sonnet", "haiku"):
                    raise JobError("unavailable_capability", "Claude aliases do not establish an exact model version")
        capabilities = super().preflight(request, existing, status_only)
        # Publish no usable proof until discovery for the entire batch succeeds.
        if not status_only:
            self._verified = {spec["job_id"]: (digest(encoded(spec)), capabilities["models"].get(spec["job_id"]))
                              for spec in request["jobs"]}
        self._request = deepcopy(request)
        return capabilities

    def authorize(self, job, admission, request_id, root):
        spec = job["spec"]
        expected_lease = dispatch_lease_id(request_id, spec["job_id"], job["attempt_id"], root)
        if job["admission_lease_id"] != expected_lease:
            raise JobError("conflict", "admission lease is not bound to this run/job/attempt")
        if request_id != self._request["request_id"]:
            raise JobError("conflict", "dispatch request differs from preflight")
        self._authorized[job["attempt_id"]] = (admission, request_id, expected_lease)
        self._check(job)

    def _check(self, job):
        spec = job["spec"]
        verified = getattr(self, "_verified", {}).get(spec["job_id"])
        if verified != (digest(encoded(spec)), job["resolved_model"]):
            raise JobError("decision_needed", "dispatch requires the unchanged coordinator decision and successful exact discovery")
        authorization = getattr(self, "_authorized", {}).get(job["attempt_id"])
        if authorization is None or job.get("admission_acquired") is not True:
            raise JobError("decision_needed", "dispatch requires a shared admission lease")
        admission, request_id, lease_id = authorization
        if job["admission_lease_id"] != lease_id or job["agent_name"] != "c9-" + job["attempt_id"][:24]:
            raise JobError("conflict", "dispatch identity differs from the admitted attempt")
        expected_provider = spec.get("provider") or spec["route"]["runtime"]
        if normalize_provider(job["provider"]) != normalize_provider(expected_provider):
            raise JobError("conflict", "dispatch provider differs from the coordinator decision")
        status = admission.status(job["provider"], job["admission_model"])
        leases = [item for item in status["leases"] if item["lease_id"] == lease_id]
        if len(leases) != 1 or leases[0]["run_id"] != request_id:
            raise JobError("conflict", "shared admission lease is missing or belongs to another run")
        if (leases[0]["provider"] != normalize_provider(job["provider"])
                or leases[0]["model"] != normalize_model(job["resolved_model"] or "default")):
            raise JobError("conflict", "shared admission lease does not match the discovered route")
        if status["backoffs"]:
            raise JobError("decision_needed", "provider backoff holds dispatch; resume after reset")
        return lease_id

    def effect(self, action, job, prompt):
        lease_id = self._check(job)
        spec = job["spec"]
        evidence = {"schema_version": 1, "backend": self._request.get("backend", "managed"),
                    "job_id": spec["job_id"], "attempt_id": job["attempt_id"],
                    "decision_sha256": decision_digest(spec), "decision": spec["route_decision"],
                    "resolved_model": job["resolved_model"], "lease_id": lease_id,
                    "agent_name": job["agent_name"], "pane_id": job["pane_id"],
                    "action": action, "observed_at": now()}
        # This is launcher evidence, separate from worker-owned output contracts.
        save(Path(job["host_output"]) / "launch-evidence.json", evidence)
        return super().effect(action, job, prompt)
