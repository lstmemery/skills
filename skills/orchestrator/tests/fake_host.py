"""Offline transport: no subprocess, socket, provider, or Herdr access."""

import os
from pathlib import Path
import time

from herdr_jobs.records import JobError, load_json, save
from herdr_jobs.transport import EffectUnknown, RateLimited


class FakeHost:
    def __init__(self, fixture_path, deadline):
        self.path = Path(fixture_path)
        self.deadline = deadline
        self.fixture = load_json(self.path)
        self.events_path = self.path.with_name("events.json")
        self.events = load_json(self.events_path) if self.events_path.exists() else []
        self.rate_limited_jobs = set()
        self.rate_limited_attempts = set()

    def event(self, action, job):
        self.events.append({"action": action, "job_id": job["spec"]["job_id"],
                            "pane_id": job["pane_id"], "attempt_id": job["attempt_id"],
                            "at": time.time()})
        save(self.events_path, self.events)

    def preflight(self, request, existing=None, status_only=False):
        if self.fixture.get("preflight_error"):
            raise JobError("unavailable_capability", self.fixture["preflight_error"])
        return {"session_id": self.fixture.get("session_id", "fixture-session"),
                "models": {job["job_id"]: job["model"] for job in request["jobs"] if job["model"]},
                "binding": {"jail_export": {"host_root": str(self.path.parent / "export"), "worker_root": "/worker-workspace"}}}

    def receipt(self, job, mode):
        output = Path(job["host_output"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "result.md").write_text("Independent fixture result.\n")
        receipt = {"schema_version": 1, "request_id": self.fixture["request_id"],
                   "job_id": job["spec"]["job_id"], "attempt_id": job["attempt_id"],
                   "outcome": "complete", "artifacts": ["result.md"], "unresolved": []}
        if mode == "stale":
            receipt["attempt_id"] = "another-attempt"
        elif mode == "missing_artifact":
            receipt["artifacts"] = ["missing.md"]
        elif mode == "escape":
            receipt["artifacts"] = ["../result.md"]
        elif mode == "symlink":
            link = output / "link.md"
            if not link.exists():
                link.symlink_to(output / "result.md")
            receipt["artifacts"] = ["link.md"]
        elif mode == "failed":
            receipt["outcome"] = "failed"
            receipt["unresolved"] = ["Synthetic failure"]
        if mode == "malformed":
            (output / "receipt.json").write_text("not JSON")
        elif mode not in ("missing", "working", "blocked", "ambiguous"):
            save(output / "receipt.json", receipt)

    def effect(self, action, job, prompt):
        self.event(action, job)
        mode = self.fixture.get("jobs", {}).get(job["spec"]["job_id"], "normal")
        codex_start = (action == "start" and job["spec"]["route"]["mode"] == "agent"
                       and job["spec"]["route"]["runtime"] == "codex")
        if action == "start" and mode == "rate_limited_start_once" and job["spec"]["job_id"] not in self.rate_limited_jobs:
            self.rate_limited_jobs.add(job["spec"]["job_id"])
            self.rate_limited_attempts.add(job["attempt_id"])
            output = Path(job["host_output"])
            output.mkdir(parents=True, exist_ok=True)
            (output / "rate-limit-evidence.md").write_text("Provider rejected this attempt with HTTP 429.\n")
            raise RateLimited({"retry_after_seconds": 0.2, "source": "retry-after"})
        pi_retry_start = (action == "start" and job["spec"]["route"]["runtime"] == "pi"
                          and job.get("retry_profile") is not None)
        if action in ("prompt", "jail") or codex_start or pi_retry_start:
            (self.path.parent / f"prompt-{job['spec']['job_id']}.txt").write_text(prompt)
            rate_limited_first_attempt = (mode in ("rate_limited_once", "rate_limited_without_metadata_once")
                                          and job["spec"]["job_id"] not in self.rate_limited_jobs)
            if rate_limited_first_attempt:
                self.rate_limited_jobs.add(job["spec"]["job_id"])
                self.rate_limited_attempts.add(job["attempt_id"])
                output = Path(job["host_output"])
                output.mkdir(parents=True, exist_ok=True)
                (output / "rate-limit-evidence.md").write_text("Provider rejected this attempt with HTTP 429.\n")
            else:
                self.receipt(job, mode)
        crash = self.fixture.get("crash_after")
        if crash == action and not (self.path.parent / "crashed").exists():
            (self.path.parent / "crashed").write_text(action)
            os._exit(91)
        if mode == "slow" and action == "split":
            time.sleep(0.5)
        if mode == "split_unknown" and action == "split":
            raise EffectUnknown("split response lost")
        if mode == "blocked" and codex_start:
            return {"launch_blocked": True, "issue": "Codex is showing a trust or resume dialog"}
        if mode in ("prompt_timeout", "ambiguous") and (action in ("prompt", "jail") or codex_start):
            raise EffectUnknown("prompt response lost")
        if action == "split":
            return {"pane_id": f"old:{job['spec']['job_id']}"}
        if action == "move":
            return {"pane_id": f"moved:{job['spec']['job_id']}"}
        if codex_start:
            self.event("prompt_verified", job)
            return {"prompt_submitted": True}
        return {}

    def observe(self, job):
        self.event("observe", job)
        mode = self.fixture.get("jobs", {}).get(job["spec"]["job_id"], "normal")
        if mode == "rate_limited_start_once" and job["attempt_id"] in self.rate_limited_attempts:
            raise JobError("unavailable_capability", "worker was not registered after provider rejection")
        if mode in ("rate_limited_once", "rate_limited_without_metadata_once") and job["attempt_id"] in self.rate_limited_attempts:
            delay = None if mode == "rate_limited_without_metadata_once" else 0.2
            source = None if delay is None else "retry-after"
            return {"state": "done", "identity_verified": True,
                    "rate_limit": {"retry_after_seconds": delay, "source": source}}
        if mode == "exited_jail":
            return {"state": "exited", "identity_verified": True, "exit_code": 0}
        lifecycle = "blocked" if mode == "blocked" else "working" if mode in ("working", "ambiguous") else "done"
        observation = {"state": lifecycle, "identity_verified": True}
        if job["spec"]["route"]["mode"] == "agent" and job["spec"]["route"]["runtime"] == "codex":
            observation["prompt_verified"] = lifecycle == "working" and mode != "ambiguous"
            if lifecycle == "blocked":
                observation["launch_issue"] = "Codex is showing a trust or resume dialog"
        return observation
