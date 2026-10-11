"""Narrow host CLI adapter; all live access is gated before discovery."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import time
import tomllib

from .records import (JobError, digest, fields, integer, load_json, parse_json, save, text,
                      effort_arguments, validate_protected_paths, validate_required_context,
                      validate_writer_intent, version)
from .admission import rate_limit_from_output
from .retry import codex_retry_cli_args, validate_codex_retry_provider, validate_pi_project_settings
from .worktrees import verify_repository_worktree
from .model_ladder import billing_pool, provider_arguments, validate_provider_harness


class BudgetExpired(Exception):
    pass


class EffectUnknown(Exception):
    def __init__(self, message, start_diagnostic=None):
        super().__init__(message)
        self.start_diagnostic = start_diagnostic


class RateLimited(Exception):
    def __init__(self, signal):
        super().__init__("provider returned a rate-limit response")
        self.signal = signal


ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[\"']?[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization)"
    r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*)(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)")
AUTH_SCHEME_TOKEN = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
KNOWN_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]+|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16}|glpat-[A-Za-z0-9_-]{12,}|tk_[A-Za-z0-9_-]{12,})\b")
URL_PASSWORD = re.compile(r"(?i)(https?://[^\s/:@]+:)([^\s/@]+)(@)")
ACTIVE_TURN_STATUS = re.compile(
    r"^(?:[•✳✴✢⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒|/\\-]\s*)?"
    r"working(?:\.{1,3}|…)?(?:\s+\(esc to interrupt\))?$",
    re.IGNORECASE,
)


def _cli_command_name(argv):
    parts = [Path(argv[0]).name, *argv[1:3]]
    return " ".join(parts)


def _safe_cli_text(value, maximum=2048):
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    value = ANSI_ESCAPE.sub("", str(value))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)
    value = AUTH_SCHEME_TOKEN.sub(r"\1 <redacted>", value)
    value = URL_PASSWORD.sub(r"\1<redacted>\3", value)
    value = SENSITIVE_ASSIGNMENT.sub(_redacted_assignment, value)
    value = KNOWN_TOKEN.sub("<redacted>", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > maximum:
        return value[:maximum - 3] + "..."
    return value


def _redacted_assignment(match):
    value = match.group("value")
    quote = value[0] if value[0] in ("\"", "'") and value.endswith(value[0]) else ""
    return match.group("prefix") + quote + "<redacted>" + quote


def cli_diagnostic(argv, exit_code, stdout, stderr):
    """Return a bounded Herdr command record with common credentials removed."""
    return {"command": _cli_command_name(argv), "exit_code": exit_code,
            "stdout": _safe_cli_text(stdout), "stderr": _safe_cli_text(stderr)}


def cli_diagnostic_message(diagnostic):
    return (f"{diagnostic['command']} exited {diagnostic['exit_code']}; stderr: "
            f"{diagnostic['stderr'] or '<empty>'}; response: "
            f"{diagnostic['stdout'] or '<empty>'}")


def catalog_models(catalog, runtime):
    if not isinstance(catalog, dict):
        raise JobError("unavailable_capability", "model discovery returned no catalog object")
    if "runtimes" in catalog:
        runtimes = catalog["runtimes"]
        if not isinstance(runtimes, list):
            raise JobError("unavailable_capability", "model discovery returned invalid runtimes")
        matching = [item for item in runtimes if isinstance(item, dict) and item.get("id") == runtime]
        if len(matching) != 1 or matching[0].get("status") != "available":
            raise JobError("unavailable_capability", f"{runtime} model discovery incomplete; verify an exact catalog "
                           "adapter before launch, or use the configured fallback with unavailable evidence")
        catalog = matching[0]
    if not isinstance(catalog.get("models"), list):
        raise JobError("unavailable_capability", "model discovery returned no models list")
    return catalog["models"]


def pi_catalog(output):
    lines = output.splitlines()
    if not lines or lines[0].split()[:2] != ["provider", "model"]:
        raise JobError("unavailable_capability", "Pi native model table contract changed")
    models = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 6:
            raise JobError("unavailable_capability", "Pi native model table contains a malformed row")
        models.append({"id": f"{parts[0]}/{parts[1]}"})
    return {"models": models}


def _codex_config():
    """Read the optional Codex config with a strict shape at the boundary."""
    config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    path = config_home / "config.toml"
    if not path.exists():
        return {}
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise JobError("unavailable_capability", f"Codex provider configuration is malformed: {error}") from error
    if not isinstance(value, dict):
        raise JobError("unavailable_capability", "Codex provider configuration must be an object")
    if "model_provider" in value and not isinstance(value["model_provider"], str):
        raise JobError("unavailable_capability", "Codex model_provider must be a string")
    providers = value.get("model_providers", {})
    if not isinstance(providers, dict):
        raise JobError("unavailable_capability", "Codex model_providers must be an object")
    for provider, settings in providers.items():
        if not isinstance(provider, str) or not isinstance(settings, dict):
            raise JobError("unavailable_capability", "Codex provider entries must be objects")
        if "base_url" in settings and not isinstance(settings["base_url"], str):
            raise JobError("unavailable_capability", f"Codex provider {provider} base_url must be a string")
    return value


def _native_model(runtime, resolved_model, provider=None):
    if runtime == "pi" and isinstance(resolved_model, str) and "/" in resolved_model:
        prefix, model = resolved_model.split("/", 1)
        if provider is None or billing_pool(prefix) == billing_pool(provider):
            return model
    return resolved_model


def _effort_for(job):
    override = job.get("spec", {}).get("override") or {}
    if "effort" in override:
        return override["effort"]
    route = job.get("spec", {}).get("model_route")
    return route.get("effort") if isinstance(route, dict) else None


class Deadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return max(0, self.end - time.monotonic())

    def check(self):
        if not self.remaining():
            raise BudgetExpired()


def command(argv, deadline, cwd=None):
    deadline.check()
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=cwd)
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
                              "herdr_agent_help_sha256", "herdr_pane_help_sha256", "supported_kinds", "orchestrator_help_sha256",
                              "server_version", "paths", "jail_export"], ["jail_default"])
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
        code, output, error_output = command(argv, self.deadline)
        if code:
            diagnostic = cli_diagnostic(argv, code, output, error_output)
            raise JobError("unavailable_capability", cli_diagnostic_message(diagnostic))
        return output

    def read(self, argv):
        return parse_json(self.raw(argv))

    def read_terminal(self, argv):
        return self.raw(argv).decode("utf-8")

    def preflight(self, request, existing=None, status_only=False):
        for job in request["jobs"]:
            label = job.get("job_id", "job") if isinstance(job, dict) else "job"
            validate_writer_intent(job, label)
            validate_protected_paths(job.get("protected_paths"), job.get("editable_paths", ()),
                                     base=job.get("cwd"))
            validate_provider_harness(job["route"]["runtime"], job.get("provider"))
            if (job["route"]["mode"] == "agent" and job["route"]["runtime"] == "codex"
                    and job.get("provider") is not None
                    and billing_pool(job["provider"]) != "codex"):
                raise JobError("unavailable_capability",
                               "Codex custom provider model discovery is not verified; no substitution")
        help_bytes = self.raw([self.herdr, "--help"])
        agent_help = self.raw([self.herdr, "agent", "--help"])
        pane_help = self.raw([self.herdr, "pane", "--help"])
        orchestrator_help = self.raw([self.orchestrator, "help", "--json", "--compact"])
        if (digest(help_bytes) != self.binding["herdr_help_sha256"]
                or digest(agent_help) != self.binding["herdr_agent_help_sha256"]
                or digest(pane_help) != self.binding["herdr_pane_help_sha256"]
                or digest(orchestrator_help) != self.binding["orchestrator_help_sha256"]):
            raise JobError("unavailable_capability", "CLI help contract changed; reverify the host binding")
        status = self.read([self.herdr, "status", "--json"])
        paths = self.binding["paths"]
        if at(status, paths["server_version"]) != self.binding["server_version"]:
            raise JobError("unavailable_capability", "Herdr server version changed; reverify the host binding")
        host_endpoint = text(at(status, paths["session_id"]), "host endpoint identity", 4096)
        kinds = self.binding["supported_kinds"]
        if not isinstance(kinds, list) or not all(isinstance(kind, str) for kind in kinds):
            raise JobError("unavailable_capability", "supported_kinds must list verified Herdr kinds")
        previous = {job["spec"]["job_id"]: job for job in existing["jobs"]} if existing else {}
        planned = [job for job in request["jobs"] if not status_only and
                   (job["job_id"] not in previous or previous[job["job_id"]]["phase"] in ("pending", "split", "moved"))]
        planned_ids = {job["job_id"] for job in planned}
        writer_checks = []
        if not status_only:
            for job in request["jobs"]:
                if not job["writes_repository"]:
                    continue
                old = previous.get(job["job_id"])
                if old is None or old["phase"] in ("pending", "split", "moved") or (
                        old["phase"] == "ready" and old["pending_effect"] is None):
                    writer_checks.append(job)
        for job in writer_checks:
            verify_repository_worktree(
                job["repository_worktree"], job["cwd"],
                lambda argv, cwd: command(argv, self.deadline, cwd=cwd),
            )
        available = []
        if planned:
            doctor = self.read([self.orchestrator, "doctor", "--json", "--compact"])
            available = at(doctor, ["runtimeSummary", "availableIds"])
        if not isinstance(available, list) or not all(isinstance(item, str) for item in available):
            raise JobError("unavailable_capability", "doctor returned an invalid availableIds list")
        # A pinned model is retained for finished jobs, but every new start,
        # including a resumed pending job, must rediscover and revalidate it.
        models = {key: job["resolved_model"] for key, job in previous.items()
                  if job["resolved_model"] is not None and key not in planned_ids}
        retry_override = request.get("retry_override", {})
        codex_config = None
        for job in planned:
            prior = previous.get(job["job_id"])
            requested_model = job.get("model")
            if requested_model is None and prior is not None:
                requested_model = prior.get("resolved_model")
            if job["kind"] not in kinds:
                raise JobError("unavailable_capability", f"Herdr kind is not verified as installed: {job['kind']}")
            if not Path(job["cwd"]).is_dir():
                raise JobError("unavailable_capability", f"{job['job_id']}: working directory is unavailable")
            runtime = job["route"]["runtime"]
            if runtime == "codex":
                codex_config = _codex_config()
            requested_effort = _effort_for({"spec": job})
            if (runtime in ("pi", "claude-code") and requested_effort is not None
                    and requested_model is None):
                raise JobError("unavailable_capability",
                               f"{runtime} effort {requested_effort} cannot be verified without an exact model")
            if runtime == "pi" and "pi" in retry_override:
                validate_pi_project_settings(job["cwd"], retry_override["pi"])
            if runtime == "codex" and "codex" in retry_override:
                retry = retry_override["codex"]
                selected = job.get("provider")
                configured = codex_config.get("model_provider", "openai")
                native_provider = (configured if selected is None else
                                   ("openai" if billing_pool(selected) == "codex"
                                    else billing_pool(selected)))
                if retry["provider_id"] != native_provider:
                    raise JobError("unavailable_capability",
                                   "Codex retry provider does not match the selected native provider")
                if native_provider in ("openai", "codex"):
                    raise JobError("unavailable_capability",
                                   "Codex retry override cannot target a built-in provider")
                provider_settings = codex_config.get("model_providers", {}).get(native_provider)
                if not isinstance(provider_settings, dict):
                    raise JobError("unavailable_capability",
                                   "Codex retry provider is not configured as a provider object")
            if job["route"]["mode"] == "jail":
                self.check_export()
                if not shutil.which("omp-train"):
                    raise JobError("unavailable_capability", "omp-train is unavailable")
                if job["model"] is not None:
                    raise JobError("unavailable_capability", "exact jail model discovery is not verified; omit the model or verify a new adapter")
                attestation = self.binding.get("jail_default")
                if not isinstance(attestation, dict):
                    raise JobError("unavailable_capability", "jail default model/effort attestation is missing")
                fields(attestation, ["verified", "harness", "model", "effort"], label="jail_default")
                if (attestation["verified"] is not True or attestation["harness"] != "codex"
                        or attestation["model"] != "gpt-6-astra" or attestation["effort"] != "max"):
                    raise JobError("unavailable_capability",
                                   "jail default is not verified as gpt-6-astra at max effort")
                validate_required_context(job.get("required_context"), base=job["cwd"],
                                          require_test_fixture=True, jail_visible=True)
            elif runtime not in available and not (runtime == "pi" and shutil.which("pi")):
                raise JobError("unavailable_capability", f"exact runtime unavailable: {runtime}; no substitution")
            if requested_model is not None and job["job_id"] not in models:
                if runtime == "pi":
                    catalog = pi_catalog(self.read_terminal(["pi", "--offline", "--list-models"]))
                else:
                    catalog = self.read([self.orchestrator, "models", runtime, "--json", "--compact"])
                items = catalog_models(catalog, runtime)
                requested = requested_model
                provider = job.get("provider")
                matched_items = [item for item in items if isinstance(item, dict)
                                 and isinstance(item.get("id"), str)
                                 and (requested == item["id"] if runtime == "claude-code" else
                                      requested in (item["id"], item.get("displayName"),
                                                    item["id"].split("/", 1)[-1] if runtime == "pi" else None))
                                 and (runtime != "pi" or provider is None
                                      or billing_pool(item["id"].split("/", 1)[0]) == billing_pool(provider))]
                matches = [item["id"] for item in matched_items]
                if len(set(matches)) != 1:
                    raise JobError("unavailable_capability", f"exact model unavailable or ambiguous: {requested}; "
                                   "restore exact model discovery or request the configured same-level fallback "
                                   "with this evidence; aliases are not proof of an exact version")
                selected_item = matched_items[0]
                if runtime == "claude-code":
                    efforts = selected_item.get("efforts")
                    if not isinstance(efforts, list) or not all(isinstance(value, str) for value in efforts):
                        raise JobError("unavailable_capability",
                                       "Claude exact model effort metadata is unavailable; no launch is authorized")
                    effort = _effort_for({"spec": job})
                    if effort is not None and effort not in efforts:
                        raise JobError("unavailable_capability",
                                       f"Claude model {requested} cannot preserve effort {effort}")
                models[job["job_id"]] = matches[0]
            if runtime == "pi" and requested_model is not None and requested_effort is not None:
                provider, model = models[job["job_id"]].split("/", 1)
                efforts = self.pi_model_efforts(provider, model)
                effort = requested_effort
                if effort not in efforts:
                    raise JobError("unavailable_capability", f"Pi/{provider}/{model} cannot preserve effort {effort}; "
                                   "native Pi would clamp it. Restore exact effort support or select the same-level "
                                   "configured fallback with unavailable evidence; no silent downgrade")
        return {"session_id": host_endpoint, "models": models, "binding": self.binding}

    def pi_model_efforts(self, provider, model):
        executable = shutil.which("pi")
        if not executable or not shutil.which("node"):
            raise JobError("unavailable_capability", "Pi native model effort discovery requires installed Pi and Node")
        agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent")))
        config_path = agent_dir / "models.json"
        candidates = [parent / "node_modules" / package / "dist/compat.js"
                      for parent in Path(executable).resolve().parents
                      for package in ("@earendil-works/pi-ai", "@mariozechner/pi-ai")]
        module = next((path for path in candidates if path.is_file()), None)
        if module is None:
            raise JobError("unavailable_capability", "Pi installed model capability module is unavailable; verify an adapter")
        package_root = next((parent for parent in Path(executable).resolve().parents
                             if (parent / "dist/core/model-config.js").is_file()
                             and (parent / "dist/core/provider-composer.js").is_file()), None)
        if config_path.exists() and package_root is None:
            raise JobError("unavailable_capability", "Pi configured model metadata needs a verified native composition adapter")
        script = Path(__file__).resolve().parents[1] / "pi-model-capabilities.mjs"
        argv = ["node", str(script), module.as_uri(), provider, model]
        if package_root is not None:
            argv += [package_root.as_uri() + "/", str(config_path)]
        result = self.read(argv)
        if (not isinstance(result, dict) or result.get("provider") != provider or result.get("model") != model
                or not isinstance(result.get("efforts"), list)
                or not all(isinstance(value, str) for value in result["efforts"])):
            raise JobError("unavailable_capability", "Pi model effort discovery returned invalid metadata")
        return result["efforts"]

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
        validate_provider_harness(spec["route"]["runtime"], spec.get("provider"))
        if action == "retry_setup":
            profile = fields(job.get("retry_profile"), ["agent_dir", "session_dir", "settings_sha256"],
                             label="run-scoped pi retry profile")
            return self.configure_pi_retry_environment(job, profile)
        codex_prompt_start = (action == "start" and spec["route"]["mode"] == "agent"
                              and spec["route"]["runtime"] == "codex")
        if action == "split":
            argv = [self.herdr, "pane", "split", "--current", "--direction", "right", "--cwd", spec["cwd"], "--no-focus"]
        elif action == "move":
            argv = [self.herdr, "pane", "move", pane, "--new-workspace", "--label", job["agent_name"], "--no-focus"]
        elif action == "start":
            argv = [self.herdr, "agent", "start", job["agent_name"], "--kind", spec["kind"], "--pane", pane]
            if codex_prompt_start:
                argv += ["--timeout", "5000", "--", "-C", spec["cwd"], "-c",
                         f"projects.{json.dumps(spec['cwd'])}.trust_level=\"trusted\""]
                argv += provider_arguments("codex", spec.get("provider"))
                if job["resolved_model"] is not None:
                    argv += ["-m", job["resolved_model"]]
                effort = (spec.get("override") or {}).get("effort")
                argv += effort_arguments("codex", effort)
                if job.get("retry_override") and "provider_id" in job["retry_override"]:
                    argv += codex_retry_cli_args(job["retry_override"])
                argv.append(prompt)
            elif spec["route"]["runtime"] == "pi":
                runtime_args = []
                runtime_args += provider_arguments("pi", spec.get("provider"))
                if job["resolved_model"] is not None:
                    runtime_args += ["--model", _native_model("pi", job["resolved_model"], spec.get("provider"))]
                effort = (spec.get("override") or {}).get("effort")
                runtime_args += effort_arguments("pi", effort)
                if runtime_args:
                    argv += ["--", *runtime_args]
            else:
                runtime_args = []
                if job["resolved_model"] is not None:
                    runtime_args += ["--model", job["resolved_model"]]
                effort = (spec.get("override") or {}).get("effort")
                runtime_args += effort_arguments(spec["route"]["runtime"], effort)
                if runtime_args:
                    argv += ["--", *runtime_args]
        elif action == "prompt":
            argv = [self.herdr, "agent", "prompt", job["agent_name"], prompt]
        elif action == "jail":
            timeout_ms = spec.get("jail_stream_idle_timeout_ms", 300000)
            launch = ["omp-train", "--harness", "codex", "exec", "--skip-git-repo-check", "-c",
                      f"model_providers.train-openai.stream_idle_timeout_ms={timeout_ms}", prompt]
            launch_request = Path(job["exit_record"]).with_name("launch.json")
            save(launch_request, {"schema_version": 1, "job_id": spec["job_id"], "attempt_id": job["attempt_id"],
                                  "argv": launch, "exit_path": job["exit_record"],
                                  "required_context": spec["required_context"],
                                  "protected_paths": spec["protected_paths"],
                                  "editable_paths": spec["editable_paths"]})
            runner = Path(__file__).resolve().parents[1] / "jail-runner.py"
            argv = [self.herdr, "pane", "run", pane,
                    shlex.join([sys.executable, str(runner), "--request", str(launch_request)])]
        else:
            raise ValueError(f"unknown effect: {action}")
        if codex_prompt_start:
            # A prompt-bearing Codex invocation makes Herdr's startup timeout
            # expected. Trust only a verified worker identity and live Working
            # state from the pane before reporting the effect as delivered.
            code, output, error_output = command(argv, self.deadline)
            start_diagnostic = cli_diagnostic(argv, code, output, error_output)
            job["start_diagnostic"] = start_diagnostic
            rate_limit = rate_limit_from_output(
                (error_output + b"\n" + output).decode("utf-8", "replace"),
                source="harness_error", exit_code=code, runtime=spec["route"]["runtime"])
            if rate_limit is not None:
                raise RateLimited(rate_limit)
            try:
                observation = self.observe(job)
            except (JobError, UnicodeError, BudgetExpired) as error:
                raise EffectUnknown("Codex startup could not be verified; inspect the owned pane before retrying",
                                    start_diagnostic=start_diagnostic) from error
            if observation["state"] == "blocked":
                return {"launch_blocked": True,
                        "issue": observation.get("launch_issue", "Codex startup is blocked"),
                        "observation": observation, "start_diagnostic": start_diagnostic}
            if observation.get("prompt_verified"):
                return {"prompt_submitted": True, "start_diagnostic": start_diagnostic}
            raise EffectUnknown("Codex prompt delivery is unverified; inspect the owned pane before retrying",
                                start_diagnostic=start_diagnostic)
        try:
            if action in ("start", "prompt"):
                code, output, error_output = command(argv, self.deadline)
                rate_limit = rate_limit_from_output(
                    (error_output + b"\n" + output).decode("utf-8", "replace"),
                    source="harness_error", exit_code=code, runtime=spec["route"]["runtime"])
                if rate_limit is not None:
                    raise RateLimited(rate_limit)
                if code:
                    raise JobError("unavailable_capability", f"{Path(argv[0]).name} {argv[1]} exited {code}; inspect the host CLI")
                result = parse_json(output)
            else:
                result = self.read(argv)
            if not isinstance(result, dict) or not isinstance(result.get("result"), dict) or result.get("ok") is False or result.get("success") is False:
                raise JobError("unavailable_capability", "host rejected effect or returned malformed JSON")
            if action == "split":
                return {"pane_id": text(at(result, ["result", "pane", "pane_id"]), "pane ID", 200)}
            if action == "move":
                moved = at(result, ["result", "move_result"])
                pane_id = text(at(result, ["result", "move_result", "pane", "pane_id"]), "moved pane ID", 200)
                workspace = moved.get("workspace") if isinstance(moved, dict) else None
                if not isinstance(workspace, dict):
                    workspace = moved.get("created_workspace") if isinstance(moved, dict) else None
                workspace_id = (workspace.get("workspace_id") if isinstance(workspace, dict) else None)
                workspace_id = workspace_id or (moved.get("workspace_id") if isinstance(moved, dict) else None)
                response = {"pane_id": pane_id}
                if workspace_id is not None:
                    response["workspace_id"] = text(workspace_id, "workspace ID", 512)
                return response
            return {}
        except RateLimited:
            raise
        except (BudgetExpired, JobError, UnicodeError) as error:
            raise EffectUnknown(f"{action}: response unavailable or unverified; reconcile before repeating") from error

    def configure_pi_retry_environment(self, job, profile):
        pane = job["pane_id"]
        assignments = ["PI_CODING_AGENT_DIR=" + profile["agent_dir"],
                       "PI_CODING_AGENT_SESSION_DIR=" + profile["session_dir"]]
        marker = f"__ORCH_PI_RETRY_ENV_{job['attempt_id']}__"
        commands = [
            "export " + " ".join(shlex.quote(item) for item in assignments),
            f"printf '\\n{marker}%s__%s\\n' \"$PI_CODING_AGENT_DIR\" \"$PI_CODING_AGENT_SESSION_DIR\"",
        ]
        try:
            for shell_command in commands:
                code, _, _ = command([self.herdr, "pane", "run", pane, shell_command], self.deadline)
                if code:
                    raise EffectUnknown("Pi retry environment command was rejected; inspect the owned pane")
            expected = marker + profile["agent_dir"] + "__" + profile["session_dir"]
            while self.deadline.remaining():
                terminal = self.read_terminal([self.herdr, "pane", "read", pane,
                                               "--source", "recent-unwrapped", "--lines", "100"])
                if expected in self.terminal_output(terminal):
                    return {"retry_environment_verified": True}
                time.sleep(min(0.1, self.deadline.remaining()))
        except (BudgetExpired, JobError, UnicodeError) as error:
            raise EffectUnknown("Pi retry environment could not be verified; inspect the owned pane") from error
        raise EffectUnknown("Pi retry environment did not persist in the owned pane; inspect before retrying")

    def observe(self, job):
        if job["spec"]["route"]["mode"] == "jail" and Path(job["exit_record"]).exists():
            record = fields(load_json(job["exit_record"]), ["schema_version", "job_id", "attempt_id", "exit_code", "exited_at"], label="launcher exit")
            version(record["schema_version"])
            if record["job_id"] != job["spec"]["job_id"] or record["attempt_id"] != job["attempt_id"]:
                raise JobError("conflict", "jail exit record does not match this job attempt")
            integer(record["exit_code"], "launcher exit code", -255, 255)
            return {"state": "exited", "identity_verified": True, "exit_code": record["exit_code"]}
        agent_route = job["spec"]["route"]["mode"] == "agent"
        codex = agent_route and job["spec"]["route"]["runtime"] == "codex"
        output = None
        if agent_route:
            terminal = self.read_terminal([self.herdr, "pane", "read", job["pane_id"],
                                           "--source", "visible", "--lines", "200"])
            output = self.terminal_output(terminal)
        pending = job.get("pending_effect")
        prompt_may_be_echoed = (job.get("phase") == "submitted"
                                or (pending and pending["action"] in ("prompt", "jail"))
                                or (codex and pending and pending["action"] == "start"))
        if agent_route and not prompt_may_be_echoed:
            dialog = self.startup_dialog(self.without_prompt_echo(output, job), codex=codex)
            if dialog:
                return {"state": "blocked", "identity_verified": False,
                        "prompt_verified": False, "launch_issue": dialog}
        try:
            result = self.read([self.herdr, "agent", "get", job["pane_id"]])
        except JobError:
            if agent_route and prompt_may_be_echoed:
                dialog = self.startup_dialog(self.without_prompt_echo(output, job), codex=codex)
                if dialog:
                    return {"state": "blocked", "identity_verified": False,
                            "prompt_verified": False, "launch_issue": dialog}
            raise
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
        if agent_route and (state != "working" or not prompt_may_be_echoed):
            dialog = self.startup_dialog(self.without_prompt_echo(output, job), codex=codex)
            if dialog:
                return {"state": "blocked", "identity_verified": False,
                        "prompt_verified": False, "launch_issue": dialog}
        observation = {"state": state, "identity_verified": True}
        rate_limit = (rate_limit_from_output(output, runtime=job["spec"]["route"]["runtime"])
                      if output is not None else None)
        if rate_limit is not None:
            observation["rate_limit"] = rate_limit
        if agent_route:
            observation["prompt_verified"] = self.prompt_landed(job, state, output)
        return observation

    @staticmethod
    def without_prompt_echo(output, job):
        output = ANSI_ESCAPE.sub("", output)
        spec = job.get("spec", {})
        override = spec.get("override") or {}
        for prompt_part in (spec.get("task"), spec.get("output_expectation"),
                            spec.get("name"), override.get("instruction")):
            if isinstance(prompt_part, str) and prompt_part.strip():
                pattern = re.compile(r"\s+".join(re.escape(part) for part in prompt_part.split()),
                                     re.IGNORECASE)
                output = pattern.sub("", output, count=1)
        return output

    @staticmethod
    def has_active_turn_status(output):
        clean = ANSI_ESCAPE.sub("", output)
        lines = [line.strip() for line in clean.splitlines() if line.strip()]
        return bool(lines and ACTIVE_TURN_STATUS.fullmatch(lines[-1]))

    @classmethod
    def prompt_landed(cls, job, state, output):
        return (state == "working"
                and cls.has_active_turn_status(cls.without_prompt_echo(output, job)))

    @staticmethod
    def terminal_output(result):
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            payload = result.get("result", result)
            if isinstance(payload, dict):
                for key in ("output", "text", "terminal", "snapshot", "lines"):
                    if isinstance(payload.get(key), str):
                        return payload[key]
                    lines = payload.get(key)
                    if isinstance(lines, list) and all(isinstance(line, str) for line in lines):
                        return "\n".join(payload[key])
        raise JobError("unavailable_capability", "Herdr pane read returned no terminal text")

    @staticmethod
    def codex_dialog(output):
        text_lower = output.casefold()
        if any(phrase in text_lower for phrase in (
            "use session directory", "select a session", "choose a session",
        )):
            return "Codex is showing a resume/session-selection dialog; do not choose a session"
        if any(phrase in text_lower for phrase in (
            "resume session", "resume the last session", "resume previous session",
        )):
            return "Codex is showing a resume/session-selection dialog; do not choose a session"
        trust_dialog = any(phrase in text_lower for phrase in (
            "do you trust", "trust the contents of this", "trust this folder",
            "trust this directory", "trust this project",
        ))
        if trust_dialog:
            return "Codex is showing a trust dialog; inspect and resolve it manually before relaunching"
        return None

    @classmethod
    def startup_dialog(cls, output, *, codex=False):
        if codex:
            dialog = cls.codex_dialog(output)
            if dialog:
                return dialog
        text_lower = output.casefold()
        if any(phrase in text_lower for phrase in (
            "continue without trusting", "trust and continue", "trust this folder",
            "trust this directory", "trust this project", "press enter to confirm",
            "authentication required", "sign in to continue",
        )):
            return "worker startup is waiting on a trust or authentication dialog; resolve it manually"
        return None

    def close_workspace(self, job):
        workspace_id = job.get("workspace_id")
        if not workspace_id:
            raise JobError("unavailable_capability", "the move response had no verified workspace ID to close")
        code, _, error_output = command([self.herdr, "workspace", "close", workspace_id], self.deadline)
        if code:
            detail = error_output.decode("utf-8", "replace").strip()
            suffix = f": {detail[:300]}" if detail else ""
            raise JobError("unavailable_capability", f"workspace close exited {code}{suffix}")
        return {"closed": True, "workspace_id": workspace_id}

    def workspace_is_open(self, job):
        workspace_id = job.get("workspace_id")
        if not workspace_id:
            raise JobError("unavailable_capability", "the move response had no verified workspace ID to reconcile")
        response = self.read([self.herdr, "workspace", "list"])
        if (not isinstance(response, dict) or response.get("ok") is False
                or response.get("success") is False):
            raise JobError("unavailable_capability", "Herdr workspace list returned an unexpected response shape")
        result = response.get("result")
        if (not isinstance(result, dict) or result.get("type") != "workspace_list"
                or not isinstance(result.get("workspaces"), list)):
            raise JobError("unavailable_capability", "Herdr workspace list returned an unexpected response shape")
        workspaces = result["workspaces"]
        workspace_ids = []
        for item in workspaces:
            if not isinstance(item, dict) or "workspace_id" not in item:
                raise JobError("unavailable_capability", "Herdr workspace list contains a malformed workspace")
            workspace_ids.append(text(item["workspace_id"], "workspace ID", 512))
        return workspace_id in workspace_ids
