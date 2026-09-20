"""Offline transport: no subprocess, socket, provider, or Herdr access."""

import os
from pathlib import Path
import time

from herdr_jobs.records import JobError, load_json, save
from herdr_jobs.transport import EffectUnknown


class FakeHost:
    def __init__(self, fixture_path, deadline):
        self.path = Path(fixture_path)
        self.deadline = deadline
        self.fixture = load_json(self.path)
        self.events_path = self.path.with_name("events.json")
        self.events = load_json(self.events_path) if self.events_path.exists() else []

    def event(self, action, job):
        self.events.append({"action": action, "job_id": job["spec"]["job_id"],
                            "pane_id": job["pane_id"], "attempt_id": job["attempt_id"]})
        save(self.events_path, self.events)

    def preflight(self, request, existing=None, status_only=False):
        if self.fixture.get("preflight_error"):
            raise JobError("unavailable_capability", self.fixture["preflight_error"])
        return {"session_id": self.fixture.get("session_id", "fixture-session"),
                "models": {job["job_id"]: job["model"] for job in request["jobs"] if job["model"]},
                "binding": {"jail_export": {"host_root": str(self.path.parent / "export"), "worker_root": "/work/out"}}}

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
        if action in ("prompt", "jail"):
            (self.path.parent / f"prompt-{job['spec']['job_id']}.txt").write_text(prompt)
            self.receipt(job, mode)
        crash = self.fixture.get("crash_after")
        if crash == action and not (self.path.parent / "crashed").exists():
            (self.path.parent / "crashed").write_text(action)
            os._exit(91)
        if mode == "slow" and action == "split":
            time.sleep(0.5)
        if mode == "split_unknown" and action == "split":
            raise EffectUnknown("split response lost")
        if mode in ("prompt_timeout", "ambiguous") and action in ("prompt", "jail"):
            raise EffectUnknown("prompt response lost")
        if action == "split":
            return {"pane_id": f"old:{job['spec']['job_id']}"}
        if action == "move":
            return {"pane_id": f"moved:{job['spec']['job_id']}"}
        return {}

    def observe(self, job):
        self.event("observe", job)
        mode = self.fixture.get("jobs", {}).get(job["spec"]["job_id"], "normal")
        if mode == "exited_jail":
            return {"state": "exited", "identity_verified": True, "exit_code": 0}
        lifecycle = "blocked" if mode == "blocked" else "working" if mode in ("working", "ambiguous") else "done"
        return {"state": lifecycle, "identity_verified": True}
