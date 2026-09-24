"""Job transitions are checkpointed before and after each external effect."""

from pathlib import Path
import time
import uuid

from .records import (JobError, atomic_bytes, bounded_file, digest, encoded, fields,
                      integer, load_json, now, parse_json, save, text, version)
from .transport import BudgetExpired, EffectUnknown


JAIL_WORKER_ROOT = "/work/out"  # the jail profile's output root; see docs/agents/runtime-profile.md

PHASES = {"pending", "split", "moved", "ready", "submitted"}


class Engine:
    def __init__(self, root, transport, deadline):
        self.root = Path(root).absolute()
        self.path = self.root / "state.json"
        self.transport = transport
        self.deadline = deadline
        self.state = None

    def checkpoint(self):
        self.state["updated_at"] = now()
        if len(encoded(self.state)) > 67108864:
            raise JobError("conflict", "checkpoint exceeds 64 MiB; previous durable state retained")
        save(self.path, self.state)

    def validate_preview(self, request):
        self.state = {"request": request}
        for spec in request["jobs"]:
            attempt = "0" * 32
            if spec["route"]["mode"] == "jail":
                output = Path(JAIL_WORKER_ROOT) / f"c9-{digest(request['request_id'].encode())[:12]}" / f"{spec['job_id']}-{attempt}"
            else:
                output = self.root / "workers" / spec["job_id"] / attempt
            job = {"spec": spec, "attempt_id": attempt, "worker_output": str(output)}
            if len(self.prompt(job).encode()) > 100000:
                raise JobError("invalid_input", f"{spec['job_id']}: managed prompt exceeds 100000 UTF-8 bytes")
        self.state = None

    def load(self):
        state = fields(load_json(self.path, 67108864), ["schema_version", "request", "request_digest", "host_contract",
                       "session_id", "binding_digest", "jobs", "created_at", "updated_at"], label="state")
        version(state["schema_version"])
        fields(state["request"], ["schema_version", "request_id", "concurrency", "jobs", "policy", "policy_digest"], label="stored request")
        if not isinstance(state["request"]["jobs"], list):
            raise JobError("conflict", "stored request jobs must be a list")
        if digest(encoded(state["request"])) != state["request_digest"]:
            raise JobError("conflict", "stored request digest does not match its contents")
        if not isinstance(state["jobs"], list) or len(state["jobs"]) != len(state["request"]["jobs"]):
            raise JobError("conflict", "stored jobs do not match the request")
        for job, spec in zip(state["jobs"], state["request"]["jobs"]):
            fields(job, ["spec", "attempt_id", "agent_name", "resolved_model", "phase", "pane_id", "previous_pane_ids",
                         "pending_effect", "history", "observed", "activity_seen", "settled", "collection",
                         "collection_error", "host_output", "worker_output", "issue", "exit_record"], label="stored job")
            if job["spec"] != spec or not isinstance(job["phase"], str) or job["phase"] not in PHASES:
                raise JobError("conflict", "stored job differs from its request or has an invalid phase")
            if job["pending_effect"] is not None:
                fields(job["pending_effect"], ["action", "started_at"], label="pending effect")
                if job["pending_effect"]["action"] not in ("split", "move", "start", "prompt", "jail"):
                    raise JobError("conflict", "unknown pending effect")
            for flag in ("settled", "activity_seen"):
                if type(job[flag]) is not bool:
                    raise JobError("conflict", f"stored {flag} must be boolean")
            if not isinstance(job["history"], list) or not isinstance(job["previous_pane_ids"], list):
                raise JobError("conflict", "invalid stored job history")
            if job["observed"] is not None:
                fields(job["observed"], ["state", "observed_at", "identity_verified"], ["exit_code"], label="observation")
            if job["collection"] is not None:
                fields(job["collection"], ["revision", "receipt_sha256", "outcome", "unresolved", "artifacts",
                                           "receipt_path", "collected_at", "acceptance"], label="collection")
                if not isinstance(job["collection"]["artifacts"], list) or not isinstance(job["collection"]["unresolved"], list):
                    raise JobError("conflict", "invalid stored collection")
            for name in ("host_output", "worker_output", "exit_record", "agent_name", "attempt_id"):
                text(job[name], f"stored {name}", 4096)
        integer(state["request"]["concurrency"], "stored concurrency", 1, 64)
        self.state = state

    def initialize(self, request, capabilities, binding_path):
        if self.path.exists():
            self.load()
            if digest(encoded(request)) != self.state["request_digest"]:
                raise JobError("conflict", "this run directory already holds a different request or policy")
            self.bind(capabilities)
            return
        if any(path.name != ".lock" for path in self.root.iterdir()):
            raise JobError("conflict", "new run directory contains unrelated files")
        jobs = []
        for spec in request["jobs"]:
            attempt = uuid.uuid4().hex
            suffix = f"c9-{digest(request['request_id'].encode())[:12]}/{spec['job_id']}-{attempt}"
            if spec["route"]["mode"] == "jail":
                export = capabilities["binding"]["jail_export"]
                host_output = str(Path(export["host_root"]) / suffix)
                worker_output = str(Path(export["worker_root"]) / suffix)
            else:
                host_output = str(self.root / "workers" / spec["job_id"] / attempt)
                worker_output = host_output
            jobs.append({"spec": spec, "attempt_id": attempt, "agent_name": "c9-" + attempt[:24],
                         "resolved_model": capabilities["models"].get(spec["job_id"]),
                         "phase": "pending", "pane_id": None, "previous_pane_ids": [],
                         "pending_effect": None, "history": [], "observed": None,
                         "activity_seen": False, "settled": False, "collection": None,
                         "collection_error": None, "host_output": host_output,
                         "worker_output": worker_output, "issue": None,
                         "exit_record": str(self.root / "lifecycle" / spec["job_id"] / "exit.json")})
        self.state = {"schema_version": 1, "request": request, "request_digest": digest(encoded(request)),
                      "host_contract": str(Path(binding_path).absolute()) if binding_path else None,
                      "session_id": capabilities["session_id"], "binding_digest": digest(encoded(capabilities["binding"])),
                      "jobs": jobs, "created_at": now(), "updated_at": now()}
        self.validate_prompts()
        self.checkpoint()

    def validate_prompts(self):
        for job in self.state["jobs"]:
            if len(self.prompt(job).encode()) > 100000:
                raise JobError("invalid_input", f"{job['spec']['job_id']}: managed prompt exceeds 100000 UTF-8 bytes")

    def bind(self, capabilities):
        if capabilities["session_id"] != self.state["session_id"]:
            raise JobError("conflict", "run belongs to a different Herdr server/session")
        if digest(encoded(capabilities["binding"])) != self.state["binding_digest"]:
            raise JobError("conflict", "host binding changed; reconcile this run before changing adapters")
        for job in self.state["jobs"]:
            if capabilities["models"].get(job["spec"]["job_id"]) != job["resolved_model"]:
                raise JobError("conflict", "model resolution changed during this run")

    def prompt(self, job):
        spec = job["spec"]
        receipt = {"schema_version": 1, "request_id": self.state["request"]["request_id"],
                   "job_id": spec["job_id"], "attempt_id": job["attempt_id"],
                   "outcome": "complete", "artifacts": ["result.md"], "unresolved": []}
        instructions = ""
        if spec["task_kind"] == "shopping":
            instructions = "Load and follow skill shopping.\n"
        elif spec["task_kind"] == "deep_research":
            instructions = "Load and follow skill deep-research and its worker contract. Execute this job in your own thread.\n"
        current = (spec.get("override") or {}).get("instruction")
        if current:
            instructions = f"Current user instruction (highest precedence):\n{current}\n\n{instructions}"
        return (f"{instructions}{spec['task']}\n\nManaged-job output contract:\n"
                f"Expected result: {spec['output_expectation']}\n"
                f"Output directory: {job['worker_output']}\n"
                "Write result.md and any supporting artifacts inside that directory. Then write receipt.json atomically, "
                "using the following identity and relative artifact paths. Set outcome to complete, partial, blocked, or failed; "
                "list all unresolved items as strings. Write the receipt last, after artifacts are durable. "
                "Keep receipts and intermediate artifacts out of any publication queue. "
                "A receipt records your outcome; the coordinator assesses correctness.\n"
                + encoded(receipt).decode() + "\n")

    def effect(self, job, action):
        self.deadline.check()
        prompt = self.prompt(job)
        if len(prompt.encode()) > 100000:
            raise JobError("invalid_input", "managed prompt exceeds 100000 UTF-8 bytes")
        Path(job["host_output"]).mkdir(parents=True, exist_ok=True, mode=0o700)
        job["pending_effect"] = {"action": action, "started_at": now()}
        job["issue"] = None
        self.checkpoint()
        try:
            result = self.transport.effect(action, job, prompt)
        except EffectUnknown as error:
            job["issue"] = str(error)
            self.checkpoint()
            return False
        if action == "split":
            job["pane_id"] = result["pane_id"]
            job["phase"] = "split"
        elif action == "move":
            job["previous_pane_ids"].append(job["pane_id"])
            job["pane_id"] = result["pane_id"]
            job["phase"] = "moved"
        elif action == "start":
            job["phase"] = "ready"
        else:
            job["phase"] = "submitted"
        job["history"].append({"action": action, "observed_at": now(), "pane_id": job["pane_id"]})
        job["pending_effect"] = None
        self.checkpoint()
        return True

    def launch(self, job):
        while job["phase"] != "submitted" and job["pending_effect"] is None:
            if job["phase"] == "pending":
                action = "split"
            elif job["phase"] == "split":
                action = "move"
            elif job["phase"] == "moved":
                action = "jail" if job["spec"]["route"]["mode"] == "jail" else "start"
            else:
                if job["observed"] and job["observed"]["state"] not in ("idle", "done"):
                    return
                action = "prompt"
            if not self.effect(job, action):
                return

    def collect(self, job):
        try:
            raw = bounded_file(job["host_output"], "receipt.json", 262144)
            receipt = fields(parse_json(raw), ["schema_version", "request_id", "job_id", "attempt_id",
                             "outcome", "artifacts", "unresolved"], label="receipt")
            version(receipt["schema_version"])
            expected = (self.state["request"]["request_id"], job["spec"]["job_id"], job["attempt_id"])
            if (receipt["request_id"], receipt["job_id"], receipt["attempt_id"]) != expected:
                raise JobError("conflict", "receipt belongs to a different request/job/attempt")
            if receipt["outcome"] not in ("complete", "partial", "blocked", "failed"):
                raise JobError("invalid_input", "invalid receipt outcome")
            if not isinstance(receipt["unresolved"], list) or not all(isinstance(item, str) for item in receipt["unresolved"]):
                raise JobError("invalid_input", "receipt unresolved must be a list of strings")
            artifacts = receipt["artifacts"]
            if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 32 or not all(isinstance(item, str) for item in artifacts):
                raise JobError("invalid_input", "receipt needs 1–32 artifact paths")
            if len(set(artifacts)) != len(artifacts) or "receipt.json" in artifacts:
                raise JobError("invalid_input", "duplicate or self-referential receipt artifact")
            payloads = []
            total = 0
            for relative in artifacts:
                data = bounded_file(job["host_output"], relative)
                total += len(data)
                if total > 67108864:
                    raise JobError("invalid_input", "job artifacts exceed 64 MiB")
                payloads.append((relative, data))
            if bounded_file(job["host_output"], "receipt.json", 262144) != raw:
                raise JobError("conflict", "receipt changed during collection")
            hashes = [{"source": path, "sha256": digest(data)} for path, data in payloads]
            revision = digest(raw + encoded(hashes))
            destination = self.root / "collected" / job["spec"]["job_id"] / revision
            copied = []
            for index, (path, data) in enumerate(payloads):
                output = destination / f"artifact-{index}"
                copied.append({"source": path, "path": str(output), "sha256": digest(data), "bytes": len(data)})
            collection = {"revision": revision, "receipt_sha256": digest(raw), "outcome": receipt["outcome"],
                          "unresolved": receipt["unresolved"], "artifacts": copied,
                          "receipt_path": str(destination / "receipt.json"), "collected_at": now(),
                          "acceptance": "pending"}
            if len(encoded(collection)) > 262144:
                raise JobError("invalid_input", "collection metadata exceeds 256 KiB")
            for item, (_, data) in zip(copied, payloads):
                self.ensure_snapshot(Path(item["path"]), data)
            self.ensure_snapshot(destination / "receipt.json", raw)
            job["collection"] = collection
            job["collection_error"] = None
        except (JobError, OSError, UnicodeError) as error:
            job["collection_error"] = str(error)

    def ensure_snapshot(self, path, expected):
        try:
            current = bounded_file(self.root, str(path.relative_to(self.root)))
            if current == expected:
                return
        except FileNotFoundError:
            pass
        atomic_bytes(path, expected)

    def observe(self, job):
        if job["pane_id"] is None:
            if job["pending_effect"]:
                job["issue"] = "launch identity is ambiguous; inspect owned resources before any new attempt"
            return
        pending = job["pending_effect"]
        if job["phase"] in ("pending", "split") and (pending is None or pending["action"] != "start"):
            return
        self.collect(job)
        if pending and pending["action"] in ("prompt", "jail") and job["collection"] and not job["collection_error"]:
            job["phase"] = "submitted"
            job["pending_effect"] = None
            job["issue"] = None
            job["history"].append({"action": pending["action"], "reconciled_by": "matching receipt", "observed_at": now()})
        try:
            observation = self.transport.observe(job)
        except JobError as error:
            job["observed"] = {"state": "unknown", "observed_at": now(), "identity_verified": False}
            job["settled"] = False
            job["issue"] = str(error)
            return
        job["observed"] = {**observation, "observed_at": now()}
        if not observation["identity_verified"]:
            job["issue"] = "worker identity unverified"
            job["settled"] = False
            return
        lifecycle = observation["state"]
        if lifecycle == "working":
            job["activity_seen"] = True
        pending = job["pending_effect"]
        if pending and pending["action"] == "jail" and lifecycle == "exited":
            job["phase"] = "submitted"
            job["pending_effect"] = None
            job["history"].append({"action": "jail", "reconciled_by": "owned launcher exit record", "observed_at": now()})
        if pending and pending["action"] == "start" and lifecycle in ("idle", "done", "blocked"):
            job["phase"] = "ready"
            job["pending_effect"] = None
            job["history"].append({"action": "start", "reconciled_by": "owned worker identity", "observed_at": now()})
        receipt_valid = job["collection"] is not None and job["collection_error"] is None
        if receipt_valid or lifecycle == "exited":
            job["activity_seen"] = True
        job["settled"] = bool(job["phase"] == "submitted" and job["pending_effect"] is None
                              and lifecycle in ("idle", "done", "exited")
                              and (job["activity_seen"] or receipt_valid or lifecycle == "exited"))
        if job["pending_effect"]:
            job["issue"] = "effect delivery remains unproven; it will not be replayed"
        elif lifecycle == "blocked":
            job["issue"] = "worker needs input; approval dialogs remain with the coordinator"
        elif lifecycle == "unknown":
            job["issue"] = "worker activity is unknown"
        elif lifecycle == "exited" and observation["exit_code"] != 0:
            job["issue"] = f"jail launcher exited {observation['exit_code']}"
        elif job["phase"] == "submitted" and not job["settled"]:
            job["issue"] = "submission activity is unproven; a valid receipt or observed work is needed"
        else:
            job["issue"] = None

    def active(self, job):
        return not job["settled"] and (job["phase"] != "pending" or job["pending_effect"] is not None)

    def drive(self, status_only=False):
        try:
            while True:
                self.deadline.check()
                if not status_only:
                    for job in self.state["jobs"]:
                        if job["settled"] or job["pending_effect"] or job["phase"] == "submitted":
                            continue
                        active_count = sum(self.active(item) for item in self.state["jobs"])
                        if job["phase"] == "pending" and active_count >= self.state["request"]["concurrency"]:
                            continue
                        self.launch(job)
                for job in self.state["jobs"]:
                    self.deadline.check()
                    self.observe(job)
                    self.checkpoint()
                if status_only or all(job["settled"] for job in self.state["jobs"]):
                    return
                if not any(job["observed"] and job["observed"]["state"] == "working" for job in self.state["jobs"]):
                    launchable = any(job["phase"] in ("pending", "split", "moved", "ready")
                                     and job["pending_effect"] is None for job in self.state["jobs"])
                    if not launchable or sum(self.active(job) for job in self.state["jobs"]) >= self.state["request"]["concurrency"]:
                        return
                time.sleep(min(0.1, self.deadline.remaining()))
        except BudgetExpired:
            self.checkpoint()

    def result(self):
        jobs = []
        for job in self.state["jobs"]:
            jobs.append({"job_id": job["spec"]["job_id"], "agent_name": job["agent_name"],
                         "phase": job["phase"], "pane_id": job["pane_id"], "route": job["spec"]["route"],
                         "attempt_id": job["attempt_id"], "resolved_model": job["resolved_model"],
                         "observed": job["observed"], "settled": job["settled"],
                         "pending_effect": job["pending_effect"], "issue": job["issue"],
                         "collection": job["collection"], "collection_error": job["collection_error"]})
        all_collected = all(job["collection"] is not None and job["collection_error"] is None for job in jobs)
        unresolved = any(job["pending_effect"] is not None for job in jobs)
        partial = any(job["issue"] or (job["settled"] and job["collection_error"])
                      or (job["collection"] and (job["collection"]["outcome"] != "complete" or job["collection"]["unresolved"]))
                      for job in jobs)
        batch = "unresolved_effect" if unresolved else "partial" if partial else "collected" if all_collected and all(job["settled"] for job in jobs) else "active"
        return {"schema_version": 1, "request_id": self.state["request"]["request_id"], "run_dir": str(self.root),
                "batch_state": batch, "collection_complete": all_collected,
                "concurrency": self.state["request"]["concurrency"], "active_jobs": sum(self.active(job) for job in self.state["jobs"]),
                "updated_at": self.state["updated_at"], "jobs": jobs, "acceptance": "pending"}
