"""Job transitions are checkpointed before and after each external effect."""

from pathlib import Path
import math
import re
import time
import uuid

from .admission import AdmissionStore, backoff_delay, normalize_model, normalize_provider
from .records import (JobError, atomic_bytes, bounded_file, digest, encoded, fields,
                      ensure_task_admitted, integer, load_json, now, parse_json, read_regular, save,
                      text, version)
from .retry import prepare_pi_run_profile
from .transport import BudgetExpired, EffectUnknown, RateLimited
from .worker_records import (DispositionInput, WorkerIdentity, record_disposition,
                             result_schema_markdown, validate_result)


PHASES = {"pending", "split", "moved", "retry_ready", "ready", "submitted"}
WORKER_DISPOSITIONS = {"completed", "blocked", "failed"}


class Engine:
    def __init__(self, root, transport, deadline, admission_store=None):
        self.root = Path(root).absolute()
        self.path = self.root / "state.json"
        self.transport = transport
        self.deadline = deadline
        self.admission = admission_store or AdmissionStore()
        self.state = None

    def admission_config(self):
        return self.state["request"]["policy"].get("provider_admission", {
            "default_provider_cap": 4, "provider_caps": {}, "default_backoff_seconds": 60})

    def provider_cap(self, provider):
        config = self.admission_config()
        return config["provider_caps"].get(provider.casefold(), config["default_provider_cap"])

    @staticmethod
    def provider_for(spec, resolved_model):
        if spec.get("provider"):
            return spec["provider"].casefold()
        if resolved_model and "/" in resolved_model:
            return resolved_model.split("/", 1)[0].casefold()
        return spec["route"]["runtime"].casefold()

    def admission_lease_id(self, request_id, job_id, attempt_id):
        identity = encoded({"run_dir": str(self.root), "request_id": request_id,
                            "job_id": job_id, "attempt_id": attempt_id})
        return "managed-" + digest(identity)[:40]

    def acquire_admission(self, job, bypass_admission=False):
        result = self.admission.acquire(job["provider"], job["admission_model"],
                                        job["admission_lease_id"], self.provider_cap(job["provider"]),
                                        self.state["request"]["request_id"],
                                        bypass_admission=bypass_admission)
        job["admission_acquired"] = result["lease_held"]
        if not result["admitted"]:
            job["admission_wait"] = {"reason": result["reason"], "provider": result["provider"],
                                      "provider_active": result["provider_active"], "cap": result["cap"],
                                      "retry_at": result["retry_at"]}
        else:
            job["admission_wait"] = None
        if not result["admitted"] and result["reason"] == "backoff" and result["lease_held"]:
            self.release_admission(job)
        return result["admitted"]

    def release_admission(self, job):
        if job["admission_acquired"]:
            self.admission.release(job["admission_lease_id"])
            job["admission_acquired"] = False

    def note_rate_limit(self, job, signal):
        if job["rate_limit_seen"]:
            return job["rate_limit_until"]
        retry_after = signal.get("retry_after_seconds")
        if retry_after is None:
            delay, source = backoff_delay(default_seconds=self.admission_config()["default_backoff_seconds"])
        else:
            delay = retry_after
            source = signal.get("source") or "metadata"
        backoff = self.admission.note_rate_limit(job["provider"], job["admission_model"], delay, source)
        job["rate_limit_seen"] = True
        job["rate_limit_until"] = backoff["retry_at"]
        job["history"].append({"action": "provider_rate_limit", "attempt_id": job["attempt_id"],
                               "provider": job["provider"], "model": job["admission_model"],
                               "retry_at": backoff["retry_at"], "source": source,
                               "observed_at": now()})
        return backoff["retry_at"]

    def retry_rate_limited_attempt(self, job, lifecycle, receipt_valid):
        if (not job["rate_limit_seen"] or receipt_valid
                or lifecycle not in ("idle", "done", "exited")):
            return False
        previous = {"attempt_id": job["attempt_id"], "agent_name": job["agent_name"],
                    "host_output": job["host_output"], "worker_output": job["worker_output"],
                    "exit_record": job["exit_record"], "pane_id": job["pane_id"]}
        if job["pending_effect"]:
            previous["pending_action"] = job["pending_effect"]["action"]
            job["pending_effect"] = None
        self.release_admission(job)
        job["history"].append({"action": "rate_limit_retry", **previous,
                               "retry_at": job["rate_limit_until"], "observed_at": now()})

        attempt = uuid.uuid4().hex
        output_name = f"{job['spec']['job_id']}-{attempt}"
        job["attempt_id"] = attempt
        job["agent_name"] = "c9-" + attempt[:24]
        job["host_output"] = str(Path(previous["host_output"]).with_name(output_name))
        job["worker_output"] = str(Path(previous["worker_output"]).with_name(output_name))
        job["exit_record"] = str(self.root / "lifecycle" / job["spec"]["job_id"] / f"{attempt}.json")
        job["pane_id"] = None
        job["retry_profile"] = None
        if job.get("retry_override") is not None and job["spec"]["route"]["runtime"] == "pi":
            profile_root = self.root / "runtime" / "pi" / job["spec"]["job_id"] / attempt
            job["retry_profile"] = prepare_pi_run_profile(profile_root / "agent", profile_root / "sessions",
                                                          job["retry_override"])
        job["admission_lease_id"] = self.admission_lease_id(
            self.state["request"]["request_id"], job["spec"]["job_id"], attempt)
        job["admission_acquired"] = False
        job["rate_limit_seen"] = False
        job["rate_limit_until"] = None
        job["phase"] = "pending"
        job["pending_effect"] = None
        job["observed"] = None
        job["activity_seen"] = False
        job["settled"] = False
        job["collection"] = None
        job["collection_error"] = None
        job["issue"] = None
        return True

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
                # Preview only checks prompt size; the verified host binding
                # supplies the worker root for a real jail job.
                output = Path("workspace-root") / f"c9-{digest(request['request_id'].encode())[:12]}" / f"{spec['job_id']}-{attempt}"
            else:
                output = self.root / "workers" / spec["job_id"] / attempt
            job = {"spec": spec, "attempt_id": attempt, "worker_output": str(output)}
            if len(self.prompt(job).encode()) > 100000:
                raise JobError("invalid_input", f"{spec['job_id']}: managed prompt exceeds 100000 UTF-8 bytes")
        self.state = None

    def load(self):
        state = fields(load_json(self.path, 67108864), ["schema_version", "request", "request_digest", "host_contract",
                       "session_id", "binding_digest", "jobs", "created_at", "updated_at"],
                       ["retry_override"], label="state")
        version(state["schema_version"])
        fields(state["request"], ["schema_version", "request_id", "concurrency", "jobs", "policy", "policy_digest"],
               ["retry_override"], label="stored request")
        if state.get("retry_override") != state["request"].get("retry_override"):
            raise JobError("conflict", "run retry override does not match the pinned request")
        if not isinstance(state["request"]["jobs"], list):
            raise JobError("conflict", "stored request jobs must be a list")
        if digest(encoded(state["request"])) != state["request_digest"]:
            raise JobError("conflict", "stored request digest does not match its contents")
        if not isinstance(state["jobs"], list) or len(state["jobs"]) != len(state["request"]["jobs"]):
            raise JobError("conflict", "stored jobs do not match the request")
        self.state = state
        for job, spec in zip(state["jobs"], state["request"]["jobs"]):
            fields(job, ["spec", "attempt_id", "agent_name", "resolved_model", "phase", "pane_id", "previous_pane_ids",
                         "pending_effect", "history", "observed", "activity_seen", "settled", "collection",
                         "collection_error", "host_output", "worker_output", "issue", "exit_record"],
                   ["provider", "admission_model", "admission_lease_id", "admission_acquired", "rate_limit_seen",
                    "rate_limit_until", "retry_override", "retry_profile", "workspace_id", "workspace_closed",
                    "worker_disposition", "worker_disposition_override", "admission_wait"],
                   label="stored job")
            if job["spec"] != spec or not isinstance(job["phase"], str) or job["phase"] not in PHASES:
                raise JobError("conflict", "stored job differs from its request or has an invalid phase")
            if not isinstance(job["attempt_id"], str) or re.fullmatch(r"[0-9a-f]{32}", job["attempt_id"]) is None:
                raise JobError("conflict", "stored job attempt ID is invalid")
            job.setdefault("provider", self.provider_for(spec, job["resolved_model"]))
            job.setdefault("admission_model", (job["resolved_model"] or "default").casefold())
            job.setdefault("admission_lease_id", self.admission_lease_id(
                state["request"]["request_id"], spec["job_id"], job["attempt_id"]))
            job["provider"] = normalize_provider(job["provider"])
            job["admission_model"] = normalize_model(job["admission_model"])
            text(job["admission_lease_id"], "stored admission_lease_id", 512)
            job.setdefault("admission_acquired", False)
            job.setdefault("rate_limit_seen", False)
            job.setdefault("rate_limit_until", None)
            job.setdefault("workspace_id", None)
            job.setdefault("workspace_closed", False)
            job.setdefault("worker_disposition", None)
            job.setdefault("worker_disposition_override", None)
            job.setdefault("admission_wait", None)
            if type(job["admission_acquired"]) is not bool or type(job["rate_limit_seen"]) is not bool:
                raise JobError("conflict", "stored admission flags must be booleans")
            if type(job["workspace_closed"]) is not bool:
                raise JobError("conflict", "stored workspace_closed must be a boolean")
            if job["workspace_id"] is not None:
                text(job["workspace_id"], "stored workspace_id", 512)
            if job["worker_disposition"] is not None and job["worker_disposition"] not in WORKER_DISPOSITIONS:
                raise JobError("conflict", "stored worker disposition is invalid")
            if job["worker_disposition_override"] is not None:
                override = fields(job["worker_disposition_override"], ["disposition", "reason"],
                                  label="stored worker disposition override")
                if override["disposition"] not in WORKER_DISPOSITIONS:
                    raise JobError("conflict", "stored worker disposition override is invalid")
                text(override["reason"], "stored worker disposition override reason", 10000)
            if job["admission_wait"] is not None:
                wait = fields(job["admission_wait"], ["reason", "provider", "provider_active", "cap", "retry_at"],
                              label="admission wait")
                if wait["reason"] not in ("capacity", "backoff"):
                    raise JobError("conflict", "stored admission wait reason is invalid")
                normalize_provider(wait["provider"])
                integer(wait["provider_active"], "admission wait active count", 0, 1000000)
                integer(wait["cap"], "admission wait cap", 1, 64)
                if wait["retry_at"] is not None and (
                        type(wait["retry_at"]) not in (int, float) or not math.isfinite(wait["retry_at"])):
                    raise JobError("conflict", "stored admission retry time is invalid")
            if job["rate_limit_until"] is not None and (
                    type(job["rate_limit_until"]) not in (int, float) or not math.isfinite(job["rate_limit_until"])):
                raise JobError("conflict", "stored rate_limit_until must be a finite timestamp or null")
            expected_retry = state["request"].get("retry_override", {}).get(spec["route"]["runtime"])
            job.setdefault("retry_override", expected_retry)
            job.setdefault("retry_profile", None)
            if job["retry_override"] != expected_retry:
                raise JobError("conflict", "stored job retry override differs from the pinned request")
            if spec["route"]["runtime"] == "pi" and expected_retry is not None and job.get("retry_profile") is None:
                raise JobError("conflict", "stored pi retry override has no run-scoped profile")
            if job.get("retry_profile") is not None:
                profile = fields(job["retry_profile"], ["agent_dir", "session_dir", "settings_sha256"],
                                 label="stored pi retry profile")
                agent_dir = text(profile["agent_dir"], "stored pi agent directory", 4096)
                session_dir = text(profile["session_dir"], "stored pi session directory", 4096)
                expected_root = self.root / "runtime" / "pi" / spec["job_id"] / job["attempt_id"]
                if (Path(agent_dir) != expected_root / "agent"
                        or Path(session_dir) != expected_root / "sessions"):
                    raise JobError("conflict", "stored pi retry profile escapes its run-scoped directory")
                if (Path(agent_dir).is_symlink() or Path(session_dir).is_symlink()
                        or not Path(session_dir).is_dir()):
                    raise JobError("conflict", "stored pi retry profile directories are invalid")
                if (not isinstance(profile["settings_sha256"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", profile["settings_sha256"]) is None):
                    raise JobError("conflict", "stored pi settings digest is invalid")
                try:
                    settings_bytes = read_regular(Path(agent_dir) / "settings.json", 1048576)
                except (OSError, JobError) as error:
                    raise JobError("conflict", "stored pi retry settings are unavailable") from error
                if digest(settings_bytes) != profile["settings_sha256"]:
                    raise JobError("conflict", "stored pi retry settings changed after profile preparation")
            if job["pending_effect"] is not None:
                fields(job["pending_effect"], ["action", "started_at"], label="pending effect")
                if job["pending_effect"]["action"] not in (
                        "split", "move", "retry_setup", "start", "prompt", "jail", "workspace_close"):
                    raise JobError("conflict", "unknown pending effect")
            for flag in ("settled", "activity_seen"):
                if type(job[flag]) is not bool:
                    raise JobError("conflict", f"stored {flag} must be boolean")
            if not isinstance(job["history"], list) or not isinstance(job["previous_pane_ids"], list):
                raise JobError("conflict", "invalid stored job history")
            if job["observed"] is not None:
                observation = fields(job["observed"], ["state", "observed_at", "identity_verified"],
                                     ["exit_code", "prompt_verified", "launch_issue", "rate_limit"], label="observation")
                if "prompt_verified" in observation and type(observation["prompt_verified"]) is not bool:
                    raise JobError("conflict", "stored prompt_verified must be a boolean")
                if "launch_issue" in observation and not isinstance(observation["launch_issue"], str):
                    raise JobError("conflict", "stored launch_issue must be text")
                if "rate_limit" in observation:
                    signal = fields(observation["rate_limit"], ["retry_after_seconds", "source"],
                                    label="rate_limit observation")
                    delay = signal["retry_after_seconds"]
                    if delay is not None and (type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0):
                        raise JobError("conflict", "stored rate-limit delay must be finite and nonnegative")
                    if signal["source"] is not None and signal["source"] not in ("retry-after", "reset"):
                        raise JobError("conflict", "stored rate-limit source is invalid")
            if job["collection"] is not None:
                fields(job["collection"], ["revision", "receipt_sha256", "outcome", "unresolved", "artifacts",
                                           "receipt_path", "collected_at", "acceptance"], label="collection")
                if not isinstance(job["collection"]["artifacts"], list) or not isinstance(job["collection"]["unresolved"], list):
                    raise JobError("conflict", "invalid stored collection")
            for name in ("host_output", "worker_output", "exit_record", "agent_name", "attempt_id"):
                text(job[name], f"stored {name}", 4096)
            if ((job["phase"] != "pending" or job["pending_effect"] is not None)
                    and not job["settled"] and not job["admission_acquired"]):
                self.acquire_admission(job, bypass_admission=True)
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
        jobs = [self.new_job(spec, request, capabilities) for spec in request["jobs"]]
        self.state = {"schema_version": 1, "request": request, "request_digest": digest(encoded(request)),
                      "host_contract": str(Path(binding_path).absolute()) if binding_path else None,
                      "session_id": capabilities["session_id"], "binding_digest": digest(encoded(capabilities["binding"])),
                      "jobs": jobs, "created_at": now(), "updated_at": now()}
        self.state["retry_override"] = request.get("retry_override")
        self.validate_prompts()
        self.checkpoint()

    def new_job(self, spec, request, capabilities):
        attempt = uuid.uuid4().hex
        resolved_model = capabilities["models"].get(spec["job_id"])
        provider = self.provider_for(spec, resolved_model)
        suffix = f"c9-{digest(request['request_id'].encode())[:12]}/{spec['job_id']}-{attempt}"
        if spec["route"]["mode"] == "jail":
            export = capabilities["binding"]["jail_export"]
            host_output = str(Path(export["host_root"]) / suffix)
            worker_output = str(Path(export["worker_root"]) / suffix)
        else:
            host_output = str(self.root / "workers" / spec["job_id"] / attempt)
            worker_output = host_output
        runtime = spec["route"]["runtime"]
        retry_override = request.get("retry_override", {}).get(runtime)
        retry_profile = None
        if runtime == "pi" and retry_override is not None:
            profile_root = self.root / "runtime" / "pi" / spec["job_id"] / attempt
            retry_profile = prepare_pi_run_profile(profile_root / "agent", profile_root / "sessions",
                                                   retry_override)
        return {"spec": spec, "attempt_id": attempt, "agent_name": "c9-" + attempt[:24],
                "resolved_model": resolved_model, "provider": provider,
                "admission_model": (resolved_model or "default").casefold(),
                "admission_lease_id": self.admission_lease_id(request["request_id"], spec["job_id"], attempt),
                "admission_acquired": False, "rate_limit_seen": False, "rate_limit_until": None,
                "retry_override": retry_override, "retry_profile": retry_profile,
                "phase": "pending", "pane_id": None, "previous_pane_ids": [], "workspace_id": None,
                "workspace_closed": False, "worker_disposition": None,
                "worker_disposition_override": None,
                "admission_wait": None,
                "pending_effect": None, "history": [], "observed": None,
                "activity_seen": False, "settled": False, "collection": None,
                "collection_error": None, "host_output": host_output,
                "worker_output": worker_output, "issue": None,
                "exit_record": str(self.root / "lifecycle" / spec["job_id"] / "exit.json")}

    def append_jobs(self, request, capabilities):
        stored = self.state["request"]
        if request["request_id"] != stored["request_id"]:
            raise JobError("conflict", "added jobs must use the existing request_id")
        if request["concurrency"] != stored["concurrency"]:
            raise JobError("conflict", "added jobs must use the existing run concurrency")
        if request["policy_digest"] != stored["policy_digest"]:
            raise JobError("conflict", "added jobs must use the run's pinned launch policy")
        if (request.get("retry_override") is not None
                and request["retry_override"] != stored.get("retry_override")):
            raise JobError("conflict", "added jobs must use the run's pinned retry_override")
        old_ids = {job["job_id"] for job in stored["jobs"]}
        if any(job["job_id"] in old_ids for job in request["jobs"]):
            raise JobError("conflict", "an added job_id already exists in this run")
        identities = {(job["worker_result"]["task_id"], job["worker_result"]["assignment_revision"])
                      for job in stored["jobs"] if "worker_result" in job}
        for spec in request["jobs"]:
            identity = spec.get("worker_result")
            if identity and (identity["task_id"], identity["assignment_revision"]) in identities:
                raise JobError("conflict", "worker task/revision already exists in this run")
            if identity:
                identities.add((identity["task_id"], identity["assignment_revision"]))
        if len(stored["jobs"]) + len(request["jobs"]) > 128:
            raise JobError("invalid_input", "a managed run may contain at most 128 jobs")
        self.bind(capabilities)
        self.state["request"]["jobs"].extend(request["jobs"])
        self.state["request_digest"] = digest(encoded(self.state["request"]))
        self.state["jobs"].extend(self.new_job(spec, self.state["request"], capabilities)
                                   for spec in request["jobs"])
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
        worker_result = spec.get("worker_result")
        receipt = {"schema_version": 1, "request_id": self.state["request"]["request_id"],
                   "job_id": spec["job_id"], "attempt_id": job["attempt_id"],
                   "outcome": "complete", "artifacts": ["result.json"] if worker_result else ["result.md"],
                   "unresolved": []}
        instructions = ""
        if spec["task_kind"] == "shopping":
            instructions = "Load and follow skill shopping.\n"
        elif spec["task_kind"] == "deep_research":
            instructions = "Load and follow skill deep-research and its worker contract. Execute this job in your own thread.\n"
        current = (spec.get("override") or {}).get("instruction")
        if current:
            instructions = f"Current user instruction (highest precedence):\n{current}\n\n{instructions}"
        output_contract = (
            "Write result.md and any supporting artifacts inside that directory. Then write receipt.json atomically, "
            "using the following identity and relative artifact paths. Set outcome to complete, partial, blocked, or failed; "
            "list all unresolved items as strings. Write the receipt last, after artifacts are durable. "
            "Keep receipts and intermediate artifacts out of any publication queue. "
            "A receipt records your outcome; the coordinator assesses correctness.\n")
        worker_contract = ""
        if worker_result:
            output_contract = (
                "Write result.json and every artifact it names inside the output directory, using relative paths. "
                "Then write receipt.json atomically; its artifacts array must include result.json and every artifact "
                "path from result.json. Map result outcome ready to receipt outcome complete; preserve blocked and failed. "
                "Write the receipt last, after the result and artifacts are durable.\n")
            worker_contract = (
                f"Worker identity: task_id={worker_result['task_id']}; "
                f"assignment_revision={worker_result['assignment_revision']}.\n"
                + result_schema_markdown() + "\n")
        return (f"{instructions}{spec['task']}\n\nManaged-job output contract:\n"
                f"Expected result: {spec['output_expectation']}\n"
                f"Output directory: {job['worker_output']}\n"
                + worker_contract + output_contract + encoded(receipt).decode() + "\n")

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
        except RateLimited as error:
            job["pending_effect"] = None
            job["phase"] = "ready"
            self.note_rate_limit(job, error.signal)
            job["history"].append({"action": "rate_limit_effect", "attempt_id": job["attempt_id"],
                                   "effect": action, "observed_at": now()})
            self.retry_rate_limited_attempt(job, "done", receipt_valid=False)
            self.checkpoint()
            return False
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
            job["workspace_id"] = result.get("workspace_id")
            job["phase"] = "moved"
        elif action == "retry_setup":
            job["phase"] = "retry_ready"
        elif action == "start":
            if result.get("prompt_submitted") is True:
                job["phase"] = "submitted"
                job["activity_seen"] = True
            elif result.get("launch_blocked") is True:
                job["phase"] = "ready"
                observation = result.get("observation", {"state": "blocked", "identity_verified": True})
                job["observed"] = {**observation, "observed_at": now()}
                job["issue"] = result.get("issue", "Codex launch is blocked; inspect the owned pane")
            else:
                job["phase"] = "ready"
        else:
            job["phase"] = "submitted"
        job["history"].append({"action": action, "observed_at": now(), "pane_id": job["pane_id"]})
        job["pending_effect"] = None
        self.checkpoint()
        return True

    def launch(self, job):
        spec = job["spec"]
        ensure_task_admitted(spec["task_kind"], spec["job_id"])
        while job["phase"] != "submitted" and job["pending_effect"] is None:
            if not self.acquire_admission(job):
                return
            if job["phase"] == "pending":
                if job["pane_id"] is None:
                    action = "split"
                else:
                    action = "jail" if job["spec"]["route"]["mode"] == "jail" else "start"
            elif job["phase"] == "split":
                action = "move"
            elif job["phase"] == "moved":
                if job.get("retry_profile") is not None:
                    action = "retry_setup"
                else:
                    action = "jail" if job["spec"]["route"]["mode"] == "jail" else "start"
            elif job["phase"] == "retry_ready":
                action = "start"
            else:
                observation = job["observed"]
                if observation is None:
                    self.observe(job)
                    observation = job["observed"]
                if not observation or observation.get("identity_verified") is not True:
                    if not job["issue"]:
                        job["issue"] = "worker startup identity is unverified; the task prompt was not sent"
                    return
                if observation["state"] != "idle":
                    job["issue"] = (observation.get("launch_issue") or
                                     "worker was not idle after startup; task prompt was not sent")
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

    def finish(self, disposition_overrides=None, override_reason=None):
        """Record worker dispositions and close only settled, collected jobs."""
        disposition_overrides = disposition_overrides or {}
        if not isinstance(disposition_overrides, dict):
            raise JobError("invalid_input", "coordinator disposition overrides must be an object")
        if bool(disposition_overrides) != (override_reason is not None):
            raise JobError("invalid_input", "coordinator disposition overrides require a reason, and a reason requires an override")
        if override_reason is not None:
            override_reason = text(override_reason, "coordinator override reason", 10000)
            if not override_reason.strip():
                raise JobError("invalid_input", "coordinator override reason must not be blank")
        worker_job_ids = {job["spec"]["job_id"] for job in self.state["jobs"]
                          if job["spec"].get("worker_result")}
        unknown_overrides = set(disposition_overrides) - worker_job_ids
        if unknown_overrides:
            raise JobError("invalid_input", "coordinator disposition override references job(s) without a worker result: "
                           + ", ".join(sorted(unknown_overrides)))
        for job_id, disposition in disposition_overrides.items():
            text(job_id, "coordinator override job_id", 100)
            if disposition not in WORKER_DISPOSITIONS:
                raise JobError("invalid_input", "coordinator disposition override must be completed, blocked, or failed")
        outcomes = {}
        for job in self.state["jobs"]:
            job_id = job["spec"]["job_id"]
            worker = job["spec"].get("worker_result")
            validation = "pending" if worker else "not_requested"
            if not job["settled"] or job["collection"] is None or job["collection_error"]:
                outcomes[job_id] = {"finished": False, "issue": "worker is not settled with a collected receipt",
                                    "worker_result_validation": validation}
                continue
            if worker:
                try:
                    result_path = Path(job["host_output"]) / "result.json"
                    identity = WorkerIdentity(worker["task_id"], worker["assignment_revision"])
                    validate_result(result_path, identity, repository_changes=job["spec"]["writes_repository"])
                    result = load_json(result_path)
                    receipt_sources = {item["source"] for item in job["collection"]["artifacts"]}
                    required_sources = {"result.json", *(item["path"] for item in result["artifacts"])}
                    if not required_sources <= receipt_sources:
                        raise JobError("invalid_input", "receipt did not collect result.json and every result artifact")
                    for relative in required_sources:
                        bounded_file(job["host_output"], relative)
                    contract_disposition = {"ready": "completed", "blocked": "blocked",
                                            "failed": "failed"}[result["outcome"]]
                    requested_override = disposition_overrides.get(job_id)
                    stored_override = job.get("worker_disposition_override")
                    if requested_override is not None:
                        requested_record = {"disposition": requested_override, "reason": override_reason}
                        if stored_override is not None and stored_override != requested_record:
                            raise JobError("conflict", "coordinator disposition override differs from its recorded decision")
                        if stored_override is None:
                            job["worker_disposition_override"] = requested_record
                            stored_override = requested_record
                            self.checkpoint()
                    effective_override = stored_override
                    worker_disposition = (effective_override["disposition"] if effective_override
                                          else contract_disposition)
                    result_hash = digest(read_regular(result_path))
                    evidence = (f"Managed job {job_id} settled in pane {job['pane_id']}; "
                                f"result.json SHA-256 {result_hash}.")
                    if effective_override:
                        evidence += (" Coordinator override recorded via --coordinator-disposition-override: "
                                    f"worker outcome {result['outcome']} maps to {contract_disposition}; "
                                    f"coordinator disposition is {worker_disposition}. "
                                    f"Reason: {effective_override['reason']}")
                    disposition = {"task_id": worker["task_id"],
                                   "assignment_revision": worker["assignment_revision"],
                                   "disposition": worker_disposition,
                                   "summary": result["summary"], "evidence": evidence}
                    disposition_path = Path(job["host_output"]) / "disposition.json"
                    if disposition_path.exists():
                        previous = load_json(disposition_path)
                        fields(previous, ["task_id", "assignment_revision", "disposition", "summary", "evidence"],
                               label="disposition.json")
                        if previous != disposition:
                            raise JobError("conflict", "existing disposition differs from the collected worker result")
                    else:
                        record_disposition(Path(job["host_output"]), DispositionInput(
                            identity=identity, disposition=disposition["disposition"],
                            summary=result["summary"], evidence=evidence))
                    job["worker_disposition"] = disposition["disposition"]
                    job["issue"] = None
                    validation = "validated"
                    self.checkpoint()
                except (JobError, OSError, UnicodeError, KeyError) as error:
                    job["issue"] = f"worker result/disposition could not be verified: {error}"
                    outcomes[job_id] = {"finished": False, "issue": job["issue"],
                                        "worker_result_validation": "failed"}
                    self.checkpoint()
                    continue

            release = self.admission.release(job["admission_lease_id"])
            job["admission_acquired"] = False
            job["history"].append({"action": "finish_release", "released": release["released"],
                                   "observed_at": now()})
            self.checkpoint()
            if not job.get("workspace_closed", False):
                pending = job["pending_effect"]
                if pending is not None and pending["action"] != "workspace_close":
                    outcomes[job_id] = {"finished": False,
                                        "issue": "another external effect is still unresolved",
                                        "worker_result_validation": validation}
                    continue
                if pending is None:
                    job["pending_effect"] = {"action": "workspace_close", "started_at": now()}
                    job["issue"] = None
                    self.checkpoint()
                try:
                    if self.transport.workspace_is_open(job):
                        closed = self.transport.close_workspace(job)
                        reconciled = False
                    else:
                        closed = {"closed": True, "workspace_id": job["workspace_id"]}
                        reconciled = True
                except (JobError, BudgetExpired, UnicodeError) as error:
                    job["issue"] = f"workspace close is unverified: {error}"
                    outcomes[job_id] = {"finished": False, "issue": job["issue"],
                                        "worker_result_validation": validation}
                    self.checkpoint()
                    continue
                job["workspace_closed"] = True
                job["pending_effect"] = None
                job["history"].append({"action": "workspace_closed", "workspace_id": closed["workspace_id"],
                                       "observed_at": now(),
                                       **({"reconciled_by": "workspace absent from Herdr list"}
                                          if reconciled else {})})
            job["issue"] = None
            self.checkpoint()
            outcomes[job_id] = {"finished": True, "workspace_closed": True,
                                "worker_disposition": job.get("worker_disposition"),
                                "worker_result_validation": validation}
            if worker:
                outcomes[job_id]["worker_result_outcome"] = result["outcome"]
                if job.get("worker_disposition_override") is not None:
                    outcomes[job_id]["coordinator_override_reason"] = job["worker_disposition_override"]["reason"]
        return {"jobs": outcomes, "complete": all(item["finished"] for item in outcomes.values())}

    def ensure_snapshot(self, path, expected):
        try:
            current = bounded_file(self.root, str(path.relative_to(self.root)))
            if current == expected:
                return
        except FileNotFoundError:
            pass
        atomic_bytes(path, expected)

    @staticmethod
    def is_codex_pending_start(job, pending):
        return bool(pending and pending["action"] == "start"
                    and job["spec"]["route"]["mode"] == "agent"
                    and job["spec"]["route"]["runtime"] == "codex")

    def observe(self, job):
        if job.get("workspace_closed", False):
            return
        if job["pending_effect"] and job["pending_effect"]["action"] == "workspace_close":
            return
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
        codex_start = self.is_codex_pending_start(job, pending)
        if codex_start and job["collection"] and not job["collection_error"]:
            job["phase"] = "submitted"
            job["pending_effect"] = None
            job["activity_seen"] = True
            job["issue"] = None
            job["history"].append({"action": "start", "reconciled_by": "matching receipt", "observed_at": now()})
        try:
            observation = self.transport.observe(job)
        except JobError as error:
            job["observed"] = {"state": "unknown", "observed_at": now(), "identity_verified": False}
            job["settled"] = False
            job["issue"] = str(error)
            return
        job["observed"] = {**observation, "observed_at": now()}
        if not observation["identity_verified"]:
            job["issue"] = observation.get("launch_issue", "worker identity unverified")
            job["settled"] = False
            return
        lifecycle = observation["state"]
        rate_limit = observation.get("rate_limit")
        if rate_limit is not None:
            self.note_rate_limit(job, rate_limit)
            if job["pending_effect"] and lifecycle in ("idle", "done", "exited"):
                job["history"].append({"action": "rate_limit_effect_reconciled",
                                       "pending_action": job["pending_effect"]["action"],
                                       "attempt_id": job["attempt_id"], "observed_at": now()})
                job["pending_effect"] = None
                job["phase"] = "submitted"
        if lifecycle == "working" and self.has_verified_working_activity(job):
            job["activity_seen"] = True
        pending = job["pending_effect"]
        codex_start = self.is_codex_pending_start(job, pending)
        if codex_start and observation.get("prompt_verified"):
            job["phase"] = "submitted"
            job["pending_effect"] = None
            job["activity_seen"] = True
            job["history"].append({"action": "start", "reconciled_by": "observed Codex Working state", "observed_at": now()})
        elif (pending and pending["action"] == "prompt" and observation.get("prompt_verified")
              and job["spec"]["route"]["mode"] == "agent"):
            job["phase"] = "submitted"
            job["pending_effect"] = None
            job["activity_seen"] = True
            job["issue"] = None
            job["history"].append({"action": "prompt", "reconciled_by": "observed visible Working state",
                                   "observed_at": now()})
        elif codex_start and lifecycle == "blocked":
            job["phase"] = "ready"
            job["pending_effect"] = None
            job["issue"] = observation.get("launch_issue", "Codex startup is blocked; inspect the owned pane")
            job["history"].append({"action": "start", "reconciled_by": "observed launch dialog", "observed_at": now()})
        elif pending and pending["action"] == "jail" and lifecycle == "exited":
            job["phase"] = "submitted"
            job["pending_effect"] = None
            job["history"].append({"action": "jail", "reconciled_by": "owned launcher exit record", "observed_at": now()})
        elif pending and pending["action"] == "start" and not codex_start and lifecycle in ("idle", "done", "blocked"):
            job["phase"] = "ready"
            job["pending_effect"] = None
            job["history"].append({"action": "start", "reconciled_by": "owned worker identity", "observed_at": now()})
        receipt_valid = job["collection"] is not None and job["collection_error"] is None
        if receipt_valid or lifecycle == "exited":
            job["activity_seen"] = True
        job["settled"] = bool(job["phase"] == "submitted" and job["pending_effect"] is None
                              and lifecycle in ("idle", "done", "exited")
                              and (job["activity_seen"] or receipt_valid or lifecycle == "exited"))
        if job["settled"]:
            self.release_admission(job)
        if self.retry_rate_limited_attempt(job, lifecycle, receipt_valid):
            return
        if job["pending_effect"]:
            job["issue"] = "effect delivery remains unproven; it will not be replayed"
        elif lifecycle == "blocked":
            job["issue"] = observation.get("launch_issue", "worker needs input; approval dialogs remain with the coordinator")
        elif lifecycle == "unknown":
            job["issue"] = "worker activity is unknown"
        elif lifecycle == "exited" and observation["exit_code"] != 0:
            job["issue"] = f"jail launcher exited {observation['exit_code']}"
        elif job["phase"] == "submitted" and not job["settled"]:
            if job["spec"]["route"]["mode"] == "agent":
                job["issue"] = ("prompt delivery is unverified; a matching receipt or visible Working marker is needed; "
                                 "inspect the owned pane before retrying")
            else:
                job["issue"] = ("jail launcher has not produced a matching exit record or collected receipt; "
                                 "inspect the owned pane before retrying")
        else:
            job["issue"] = None

    def active(self, job):
        return not job["settled"] and (job["phase"] != "pending" or job["pending_effect"] is not None)

    @staticmethod
    def has_verified_working_activity(job):
        observation = job["observed"]
        if not observation or observation["state"] != "working":
            return False
        route_mode = job["spec"]["route"]["mode"]
        if route_mode == "agent":
            return observation.get("identity_verified") is True and observation.get("prompt_verified") is True
        return route_mode == "jail" and observation.get("identity_verified") is True

    def drive(self, status_only=False):
        try:
            while True:
                self.deadline.check()
                observed_in_launch = set()
                if not status_only:
                    for job in self.state["jobs"]:
                        if job["settled"] or job["pending_effect"] or job["phase"] == "submitted":
                            continue
                        active_count = sum(self.active(item) for item in self.state["jobs"])
                        if job["phase"] == "pending" and active_count >= self.state["request"]["concurrency"]:
                            continue
                        self.launch(job)
                        self.observe(job)
                        self.checkpoint()
                        observed_in_launch.add(job["spec"]["job_id"])
                for job in self.state["jobs"]:
                    self.deadline.check()
                    if job["spec"]["job_id"] in observed_in_launch:
                        continue
                    self.observe(job)
                    self.checkpoint()
                if status_only or all(job["settled"] for job in self.state["jobs"]):
                    return
                if not any(self.has_verified_working_activity(job) for job in self.state["jobs"]):
                    launchable = any(job["phase"] in ("pending", "split", "moved", "retry_ready", "ready")
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
                         "provider": job["provider"], "model": job["admission_model"],
                         "admission_acquired": job["admission_acquired"],
                         "attempt_id": job["attempt_id"], "resolved_model": job["resolved_model"],
                         "observed": job["observed"], "settled": job["settled"],
                         "pending_effect": job["pending_effect"], "issue": job["issue"],
                         "workspace_id": job.get("workspace_id"),
                         "workspace_closed": job.get("workspace_closed", False),
                         "worker_disposition": job.get("worker_disposition"),
                         "admission_wait": job.get("admission_wait"),
                         "collection": job["collection"], "collection_error": job["collection_error"]})
        all_collected = all(job["collection"] is not None and job["collection_error"] is None for job in jobs)
        unresolved = any(job["pending_effect"] is not None for job in jobs)
        partial = any(job["issue"] or (job["settled"] and job["collection_error"])
                      or (job["collection"] and (job["collection"]["outcome"] != "complete" or job["collection"]["unresolved"]))
                      for job in jobs)
        batch = "unresolved_effect" if unresolved else "partial" if partial else "collected" if all_collected and all(job["settled"] for job in jobs) else "active"
        provider_active = {}
        for job in self.state["jobs"]:
            provider = job["provider"]
            if provider not in provider_active:
                provider_active[provider] = self.admission.status(provider)["active_count"]
        return {"schema_version": 1, "request_id": self.state["request"]["request_id"], "run_dir": str(self.root),
                "batch_state": batch, "collection_complete": all_collected,
                "concurrency": self.state["request"]["concurrency"], "active_jobs": sum(self.active(job) for job in self.state["jobs"]),
                "provider_active": provider_active,
                "updated_at": self.state["updated_at"], "jobs": jobs,
                "retry_override": self.state.get("retry_override"), "acceptance": "pending"}
