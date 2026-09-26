"""Narrow host CLI adapter; all live access is gated before discovery."""

import os
from pathlib import Path, PurePosixPath
import selectors
import shlex
import shutil
import subprocess
import sys
import time

from .records import JobError, digest, fields, integer, load_json, parse_json, save, text, version


class BudgetExpired(Exception):
    pass


class EffectUnknown(Exception):
    pass


class Deadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return max(0, self.end - time.monotonic())

    def check(self):
        if not self.remaining():
            raise BudgetExpired()


def command(argv, deadline):
    deadline.check()
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
    except OSError as error:
        raise JobError("unavailable_capability", f"cannot execute {argv[0]}: {error.strerror}") from error
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                deadline.check()
                for key, _ in selector.select(min(deadline.remaining(), 0.2)):
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        selector.unregister(key.fileobj)
                        continue
                    chunks[key.data].extend(part)
                    if len(chunks[key.data]) > 1048576:
                        raise JobError("unavailable_capability", "CLI response exceeded 1 MiB")
        try:
            process.wait(timeout=max(0.001, deadline.remaining()))
        except subprocess.TimeoutExpired as error:
            raise BudgetExpired() from error
        return process.returncode, bytes(chunks["stdout"]), bytes(chunks["stderr"])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def at(value, path):
    if not isinstance(path, list) or not path or not all(isinstance(key, str) for key in path):
        raise JobError("unavailable_capability", "host JSON paths must be nonempty lists of keys")
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise JobError("unavailable_capability", f"host response lacks configured field: {'.'.join(path)}")
        value = value[key]
    return value


class NativeTransport:
    def __init__(self, binding_path, deadline):
        if Path("/ctx").exists() or os.environ.get("HERDR_ENV") != "1":
            raise JobError("unavailable_capability", "live execution requires a non-jail Herdr host session")
        if not binding_path:
            raise JobError("unavailable_capability", "provide a verified --host-contract; no host schema is assumed")
        self.deadline = deadline
        self.binding = fields(load_json(binding_path), ["schema_version", "verified", "herdr_help_sha256",
                              "herdr_agent_help_sha256", "supported_kinds", "orchestrator_help_sha256",
                              "server_version", "paths", "jail_export"])
        version(self.binding["schema_version"])
        if self.binding["verified"] is not True:
            raise JobError("unavailable_capability", "host contract is not verified")
        fields(self.binding["paths"], ["session_id", "server_version", "agent_state", "agent_pane_id",
                                      "agent_kind", "agent_name"])
        self.herdr = shutil.which("herdr")
        self.orchestrator = shutil.which("orchestrator")
        if not self.herdr or not self.orchestrator:
            raise JobError("unavailable_capability", "herdr and orchestrator must already be installed")

    def raw(self, argv):
        code, output, _ = command(argv, self.deadline)
        if code:
            raise JobError("unavailable_capability", f"{Path(argv[0]).name} {argv[1]} exited {code}; inspect the host CLI")
        return output

    def read(self, argv):
        return parse_json(self.raw(argv))

    def preflight(self, request, existing=None, status_only=False):
        help_bytes = self.raw([self.herdr, "--help"])
        agent_help = self.raw([self.herdr, "agent"])
        orchestrator_help = self.raw([self.orchestrator, "help", "--json", "--compact"])
        if digest(help_bytes) != self.binding["herdr_help_sha256"] or digest(agent_help) != self.binding["herdr_agent_help_sha256"] or digest(orchestrator_help) != self.binding["orchestrator_help_sha256"]:
            raise JobError("unavailable_capability", "CLI help contract changed; reverify the host binding")
        status = self.read([self.herdr, "status"])
        paths = self.binding["paths"]
        if at(status, paths["server_version"]) != self.binding["server_version"]:
            raise JobError("unavailable_capability", "Herdr server version changed; reverify the host binding")
        session = text(at(status, paths["session_id"]), "host session identity", 4096)
        kinds = self.binding["supported_kinds"]
        if not isinstance(kinds, list) or not all(isinstance(kind, str) for kind in kinds):
            raise JobError("unavailable_capability", "supported_kinds must list verified Herdr kinds")
        previous = {job["spec"]["job_id"]: job for job in existing["jobs"]} if existing else {}
        planned = [job for job in request["jobs"] if not status_only and
                   (job["job_id"] not in previous or previous[job["job_id"]]["phase"] in ("pending", "split", "moved"))]
        available = []
        if planned:
            doctor = self.read([self.orchestrator, "doctor", "--json", "--compact"])
            available = at(doctor, ["runtimeSummary", "availableIds"])
        if not isinstance(available, list) or not all(isinstance(item, str) for item in available):
            raise JobError("unavailable_capability", "doctor returned an invalid availableIds list")
        models = {key: job["resolved_model"] for key, job in previous.items() if job["resolved_model"] is not None}
        for job in planned:
            if job["kind"] not in kinds:
                raise JobError("unavailable_capability", f"Herdr kind is not verified as installed: {job['kind']}")
            if not Path(job["cwd"]).is_dir():
                raise JobError("unavailable_capability", f"{job['job_id']}: working directory is unavailable")
            runtime = job["route"]["runtime"]
            if job["route"]["mode"] == "jail":
                self.check_export()
                if not shutil.which("omp-train"):
                    raise JobError("unavailable_capability", "omp-train is unavailable")
                if job["model"] is not None:
                    raise JobError("unavailable_capability", "exact jail model discovery is not verified; omit the model or verify a new adapter")
            elif runtime not in available:
                raise JobError("unavailable_capability", f"exact runtime unavailable: {runtime}; no substitution")
            if job["model"] is not None and job["job_id"] not in models:
                catalog = self.read([self.orchestrator, "models", runtime, "--json", "--compact"])
                if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
                    raise JobError("unavailable_capability", "model discovery returned no models list")
                matches = [item["id"] for item in catalog["models"] if isinstance(item, dict)
                           and isinstance(item.get("id"), str)
                           and job["model"] in (item["id"], item.get("displayName"))]
                if len(set(matches)) != 1:
                    raise JobError("unavailable_capability", f"exact model unavailable or ambiguous: {job['model']}")
                models[job["job_id"]] = matches[0]
        return {"session_id": session, "models": models, "binding": self.binding}

    def check_export(self):
        export = self.binding["jail_export"]
        if export is None:
            raise JobError("unavailable_capability", "jail artifact export has not been bound")
        fields(export, ["verified", "host_root", "worker_root"], label="jail_export")
        worker_root = PurePosixPath(text(export["worker_root"], "export worker_root", 4096))
        if (export["verified"] is not True or not worker_root.is_absolute()
                or len(worker_root.parts) < 2 or ".." in worker_root.parts):
            raise JobError("unavailable_capability", "jail export needs a verified absolute worker workspace root")
        root = Path(text(export["host_root"], "export host_root", 4096))
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise JobError("unavailable_capability", "jail export root must be an existing absolute directory")

    def effect(self, action, job, prompt):
        pane = job["pane_id"]
        spec = job["spec"]
        if action == "split":
            argv = [self.herdr, "pane", "split", "--current", "--direction", "right", "--cwd", spec["cwd"], "--no-focus"]
        elif action == "move":
            argv = [self.herdr, "pane", "move", pane, "--new-workspace", "--label", job["agent_name"], "--no-focus"]
        elif action == "start":
            argv = [self.herdr, "agent", "start", job["agent_name"], "--kind", spec["kind"], "--pane", pane]
            if job["resolved_model"] is not None:
                argv += ["--", "--model", job["resolved_model"]]
        elif action == "prompt":
            argv = [self.herdr, "agent", "prompt", job["agent_name"], prompt]
        elif action == "jail":
            launch = ["omp-train", "--harness", "codex", "exec", "--skip-git-repo-check", prompt]
            launch_request = Path(job["exit_record"]).with_name("launch.json")
            save(launch_request, {"schema_version": 1, "job_id": spec["job_id"], "attempt_id": job["attempt_id"],
                                  "argv": launch, "exit_path": job["exit_record"]})
            runner = Path(__file__).resolve().parents[1] / "jail-runner.py"
            argv = [self.herdr, "pane", "run", pane,
                    shlex.join([sys.executable, str(runner), "--request", str(launch_request)])]
        else:
            raise ValueError(f"unknown effect: {action}")
        try:
            result = self.read(argv)
            if not isinstance(result, dict) or not isinstance(result.get("result"), dict) or result.get("ok") is False or result.get("success") is False:
                raise JobError("unavailable_capability", "host rejected effect or returned malformed JSON")
            if action == "split":
                return {"pane_id": text(at(result, ["result", "pane", "pane_id"]), "pane ID", 200)}
            if action == "move":
                return {"pane_id": text(at(result, ["result", "move_result", "pane", "pane_id"]), "moved pane ID", 200)}
            return {}
        except (BudgetExpired, JobError, UnicodeError) as error:
            raise EffectUnknown(f"{action}: response unavailable or unverified; reconcile before repeating") from error

    def observe(self, job):
        if job["spec"]["route"]["mode"] == "jail" and Path(job["exit_record"]).exists():
            record = fields(load_json(job["exit_record"]), ["schema_version", "job_id", "attempt_id", "exit_code", "exited_at"], label="launcher exit")
            version(record["schema_version"])
            if record["job_id"] != job["spec"]["job_id"] or record["attempt_id"] != job["attempt_id"]:
                raise JobError("conflict", "jail exit record does not match this job attempt")
            integer(record["exit_code"], "launcher exit code", -255, 255)
            return {"state": "exited", "identity_verified": True, "exit_code": record["exit_code"]}
        result = self.read([self.herdr, "agent", "get", job["pane_id"]])
        paths = self.binding["paths"]
        pane = at(result, paths["agent_pane_id"])
        kind = at(result, paths["agent_kind"])
        name = at(result, paths["agent_name"])
        if pane != job["pane_id"] or kind != job["spec"]["kind"]:
            raise JobError("unavailable_capability", "observed worker identity differs from the owned job")
        if job["spec"]["route"]["mode"] == "agent" and name != job["agent_name"]:
            raise JobError("unavailable_capability", "observed agent name differs from the owned job")
        state = at(result, paths["agent_state"])
        if state not in ("idle", "done", "working", "blocked", "unknown"):
            raise JobError("unavailable_capability", "unrecognized worker lifecycle state")
        return {"state": state, "identity_verified": True}
