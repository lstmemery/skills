"""Preflight and launch helpers for the opt-in local Pi route."""

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid

from .records import (JobError, encoded, ensure_local_lane_admitted, load_json,
                      now, parse_json, policy_record, save, text)
from .routing_decision import decision_for, validate_decision
from .prompt import PromptNotStarted, has_active_turn_status, submit_and_verify
from .security import contains_secret_material
from .telemetry import launch_telemetry, task_telemetry, validate_task_telemetry


POLICY_PATH = Path(__file__).resolve().parents[2] / "launch-policy.json"
LOCAL_ENV_KEYS = ("HOME", "PATH", "TERM", "LANG")
PI_ENV_KEYS = ("PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR")
HERDR_ENV_KEYS = ("HERDR_SESSION", "HERDR_BIN_PATH", "HERDR_SOCKET_PATH")


class _PromptDeadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return max(0, self.end - time.monotonic())


class UnknownDirectLocalLaneEffect(JobError):
    """A direct start or follow-up may have created a worker; keep its lease."""

    def __init__(self, message, effect):
        super().__init__("unresolved_effect", message)
        self.effect = effect


def _run_readonly(argv):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JobError("unavailable_capability", f"local lane precondition command failed: {argv[0]}") from error
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _run_host_command(argv, *, env=None):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=30, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JobError("unavailable_capability", f"local lane host command failed: {argv[0]}") from error
    return result.returncode, result.stdout, result.stderr


def _create_telemetry_file(path, telemetry):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded(telemetry) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _direct_local_lane_telemetry(local_config):
    spec = {
        "local_lane": True,
        "model_route": {"lane": "local_lane", "level": None,
                        "choice": "explicit_opt_in", "effort": local_config["effort"]},
        "override": {"effort": local_config["effort"]},
        "model": local_config["model"],
        "route": {"runtime": "pi"},
    }
    telemetry = task_telemetry(spec, local_config["model"], local_config["provider"])
    return validate_task_telemetry(telemetry, telemetry["route"])


def direct_route_spec(local_config, task_class, local_lane_scope, name, prompt):
    """Compile the fixed local assignment for coordinator preview and validation."""
    task_class, local_lane_scope = ensure_local_lane_admitted(
        local_config, task_class, local_lane_scope, "direct")
    pins = {"instruction": "Explicit scoped local-lane opt-in", "runtime": "pi",
            "provider": local_config["provider"], "model": local_config["model"],
            "effort": local_config["effort"]}
    return {"job_id": text(name, "agent name", 32), "task_class": task_class,
            "task_kind": "ordinary", "writes_repository": True, "local_lane": True,
            "task_file": "direct-local-lane", "output_expectation": "Bounded local-lane result",
            "task": local_lane_instruction(task_class, local_lane_scope) + "\n\n" + prompt,
            "route": {"mode": "agent", "runtime": "pi"},
            "model": local_config["model"], "provider": local_config["provider"],
            "override": pins, "requested_override": pins,
            "model_route": {"lane": "local_lane", "level": None,
                            "choice": "explicit_opt_in", "effort": local_config["effort"]}}


def local_lane_environment(source_env=None, pane_id=None):
    """Return the allowlisted environment used by the local Pi process."""
    source = os.environ if source_env is None else source_env
    result = {}
    for key in LOCAL_ENV_KEYS:
        value = source.get(key)
        if key in ("HOME", "PATH") and not value:
            raise JobError("unavailable_capability", f"local lane requires {key} in the launcher environment")
        if value:
            result[key] = value
    result.setdefault("TERM", "xterm-256color")
    result.setdefault("LANG", "C.UTF-8")
    for key in PI_ENV_KEYS + HERDR_ENV_KEYS:
        value = source.get(key)
        if value:
            result[key] = value
    result["HERDR_ENV"] = "1"
    if pane_id is not None:
        result["HERDR_PANE_ID"] = text(pane_id, "pane ID", 200)
    return result


def local_lane_instruction(task_class, local_lane_scope):
    return (
        f"local_lane: task_class: {task_class}; "
        f"declared_scope: {encoded(local_lane_scope).decode()}; "
        "this allowlisted class is limited to trivial, self-contained, "
        "validator-checkable work using supplied local inputs in the assigned throwaway worktree. "
        "Do not access network, credentials, secrets, auth files, or environment; "
        "do not perform destructive actions or review verdicts. "
        "Never include a credential or secret in the prompt or output. "
        "Configured MCP tools and extensions remain available under the user's configuration; "
        "use them only within this scope and never disclose or pass credential material. "
        "Do not launch jail work or another agent. "
        "The coordinator independently runs the declared validator before accepting output"
        "\nevidence_gate: investigatory claims cite their source and label unknowns; "
        "before claiming something is absent, check both the host and jail namespaces, "
        "or label the result unknown if either cannot be checked"
    )


def verify_pane_worktree(pane_id, *, herdr="herdr", run_command=_run_host_command):
    """Require the target pane cwd to be a registered linked Git worktree."""
    code, output, _ = run_command([herdr, "pane", "get", pane_id])
    if code:
        raise JobError("unavailable_capability", "could not inspect the local-lane pane working directory")
    try:
        pane = parse_json(output).get("result", {}).get("pane")
        cwd = pane.get("cwd") if isinstance(pane, dict) else None
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("missing pane cwd")
        worktree = Path(cwd).resolve(strict=True)
        if not worktree.is_dir():
            raise ValueError("pane cwd is not a directory")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, JobError) as error:
        raise JobError("unavailable_capability", "local-lane pane cwd could not be verified") from error

    def git(*args):
        code, output, _ = run_command(["git", "-C", str(worktree), *args])
        if code:
            raise JobError("unavailable_capability", "local-lane pane cwd is not a verified Git worktree")
        return output.decode("utf-8", errors="strict").strip()

    try:
        root = Path(git("rev-parse", "--show-toplevel")).resolve(strict=True)
        common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
        listing = git("worktree", "list", "--porcelain", "-z")
        registered = [Path(field[len("worktree "):]).resolve(strict=True)
                      for field in listing.split("\0") if field.startswith("worktree ")]
    except (OSError, RuntimeError, UnicodeError, JobError):
        raise JobError("unavailable_capability", "local-lane pane cwd is not a verified Git worktree")
    if root != worktree or common == root / ".git" or worktree not in registered or not registered or registered[0] == worktree:
        raise JobError("unavailable_capability",
                       "direct local-lane pane must use a registered linked worktree, not the primary checkout")
    return str(worktree)


def sanitize_pane_environment(pane_id, environment, *, herdr="herdr", run_command=_run_host_command):
    """Replace the pane shell with an env -i shell before any local Pi start."""
    assignments = [f"{key}={value}" for key, value in environment.items()]
    shell_command = "exec " + shlex.join([
        "/usr/bin/env", "-i", *assignments,
        "/bin/bash", "--noprofile", "--norc", "-i",
    ])
    code, _, _ = run_command([herdr, "pane", "run", pane_id, shell_command], env=environment)
    if code:
        raise JobError("unavailable_capability", "could not reset the local-lane pane environment")
    code, _, _ = run_command([
        herdr, "pane", "wait-output", pane_id, "--match", "bash-", "--timeout", "30000",
    ], env=environment)
    if code:
        raise JobError("unavailable_capability", "sanitized local-lane shell did not become ready")


def start_direct_local_lane(local_config, *, task_class, local_lane_scope, pane, name, prompt,
                            telemetry_path,
                            source_env=None, herdr="herdr", run_command=_run_host_command,
                            policy=None, admission_service=None, route_decision=None):
    """Validate and start the explicitly scoped direct Herdr local-lane route."""
    task_class, local_lane_scope = ensure_local_lane_admitted(
        local_config, task_class, local_lane_scope, "direct")
    if route_decision is None:
        raise JobError("decision_needed", "direct local-lane start requires a coordinator route decision")
    pane_id = text(pane, "pane ID", 200)
    agent_name = text(name, "agent name", 32)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", agent_name):
        raise JobError("invalid_input", "direct local-lane agent name must match [a-z][a-z0-9_-]{0,31}")
    prompt = text(prompt, "prompt", 100000)
    worker_prompt = text(local_lane_instruction(task_class, local_lane_scope)
                         + "\n\n" + prompt, "local-lane prompt", 100000)
    if contains_secret_material(worker_prompt):
        raise JobError("invalid_input", "local_lane prompt contains secret-like material; remove credentials or secrets before launch")
    policy = policy or policy_record(load_json(POLICY_PATH))
    if local_config != policy.get("local_lane"):
        raise JobError("conflict", "direct local-lane configuration differs from policy")
    spec = direct_route_spec(local_config, task_class, local_lane_scope, agent_name, prompt)
    spec["route_decision"] = route_decision
    validate_decision(spec, policy)
    if telemetry_path is None:
        raise JobError("invalid_input", "direct local-lane start requires a telemetry file")
    telemetry_path = Path(text(str(telemetry_path), "telemetry file path", 4096)).absolute()
    if telemetry_path.exists() or telemetry_path.is_symlink():
        raise JobError("conflict", "direct local-lane telemetry file already exists")
    environment = local_lane_environment(source_env, pane_id)
    # Deferred to break transport.py -> local_lane.py -> transport.py.
    from .transport import pi_catalog

    code, output, _ = run_command(["pi", "--offline", "--list-models"], env=environment)
    if code:
        raise JobError("unavailable_capability", "Pi exact model discovery failed")
    try:
        catalog = pi_catalog(output.decode("utf-8", errors="strict"))
    except UnicodeError as error:
        raise JobError("unavailable_capability", "Pi exact model discovery returned invalid text") from error
    resolved_model = local_config["provider"] + "/" + local_config["model"]
    if not any(item["id"] == resolved_model for item in catalog["models"]):
        raise JobError("unavailable_capability", "exact model unavailable in Pi offline discovery")
    telemetry = _direct_local_lane_telemetry(local_config)
    try:
        _create_telemetry_file(telemetry_path, telemetry)
    except FileExistsError as error:
        raise JobError("conflict", "direct local-lane telemetry file already exists") from error

    # Defer to break the engine.py -> local_lane.py -> admission_service.py -> engine.py import cycle.
    from .admission_service import AdmissionService

    admission_service = admission_service or AdmissionService()
    lease_id = "direct-local-" + uuid.uuid4().hex
    admission_job = {
        "provider": local_config["provider"],
        "admission_model": resolved_model,
        "admission_lease_id": lease_id,
    }
    effect_context = {
        "attempted": False,
        "completed": False,
        "action": "start",
        "observation": {"state": "unknown", "identity_verified": False,
                        "prompt_verified": False},
    }
    pending_effect = {
        "action": effect_context["action"],
        "status": "unknown",
        "pane_id": pane_id,
        "agent_name": agent_name,
        "observation": effect_context["observation"],
    }
    admission = admission_service.acquire(
        admission_job,
        admission_service.provider_cap(policy, local_config["provider"]),
        lease_id,
        pending_effect=pending_effect,
    )
    if not admission["admitted"]:
        raise JobError("unavailable_capability",
                       f"direct local-lane admission denied: {admission['reason']}")

    def authorize():
        validate_decision(spec, policy)
        status = admission_service.store.status(local_config["provider"], resolved_model)
        leases = [item for item in status["leases"] if item["lease_id"] == lease_id]
        if (len(leases) != 1 or leases[0]["run_id"] != lease_id
                or leases[0]["model"] != resolved_model.casefold()):
            raise JobError("conflict", "direct local-lane shared admission lease is missing or changed")
        if status["backoffs"]:
            raise JobError("decision_needed", "provider backoff holds direct local-lane dispatch")

    telemetry["launch"] = launch_telemetry(spec, "native", lease_id)
    try:
        save(telemetry_path, telemetry)
        authorize()
        verify_pane_worktree(pane_id, herdr=herdr, run_command=run_command)
        result = _launch_direct_local_lane(
            local_config, task_class, pane_id, agent_name, worker_prompt, environment,
            herdr, run_command, effect_context, telemetry, telemetry_path, authorize,
        )
        result["telemetry"] = telemetry
        result["telemetry_file"] = str(telemetry_path)
        return result
    except Exception as error:
        if effect_context["attempted"]:
            effect = {
                "action": effect_context["action"],
                "status": "unknown",
                "pane_id": pane_id,
                "agent_name": agent_name,
                "lease_id": lease_id,
                "observation": effect_context["observation"],
            }
            try:
                to_record = {key: value for key, value in effect.items() if key != "lease_id"}
                effect = admission_service.record_unknown_effect(admission_job, to_record)["pending_effect"]
                effect["lease_id"] = lease_id
                effect["recorded"] = True
            except Exception as record_error:
                effect["recorded"] = True
                effect["record_error"] = str(record_error)[:1000]
            message = (
                f"direct local-lane {effect['action']} effect is unresolved; inspect pane {pane_id} "
                f"with 'herdr agent get {agent_name}' and 'herdr pane read {pane_id} "
                "--source recent-unwrapped --lines 120'. Keep admission lease "
                f"{lease_id} until reconciliation proves no worker started; release only with "
                "the observed absence recorded via 'herdr-admission release --lease-id "
                f"{lease_id} --reconciliation-evidence <observation>'."
            )
            if "record_error" in effect:
                message += " The latest observation could not be saved; the original pending effect marker and admission lease remain."
            raise UnknownDirectLocalLaneEffect(message, effect) from error
        raise
    finally:
        if not effect_context["attempted"]:
            admission_service.release(
                admission_job,
                reconciliation_evidence=("launcher confirmed Herdr agent start was not issued; "
                                         "the direct worker was absent by construction"),
            )
        elif effect_context["completed"]:
            observation = effect_context["observation"]
            admission_service.release(
                admission_job,
                reconciliation_evidence=(
                    f"Herdr verified direct worker {agent_name} on pane {pane_id} reached "
                    f"state={observation['state']} with identity_verified=true after prompt verification"
                ),
            )


def _launch_direct_local_lane(local_config, task_class, pane_id, agent_name, worker_prompt,
                              environment, herdr, run_command, effect_context,
                              telemetry, telemetry_path, authorize):
    authorize()
    sanitize_pane_environment(pane_id, environment, herdr=herdr, run_command=run_command)
    start = [herdr, "agent", "start", agent_name, "--kind", "pi", "--pane", pane_id,
             "--", "--provider", local_config["provider"], "--model", local_config["model"], "--approve"]

    def observe():
        code, output, _ = run_command([herdr, "agent", "get", pane_id], env=environment)
        if code:
            return {"state": "unknown", "identity_verified": False, "prompt_verified": False}
        try:
            response = parse_json(output)
            agent = response.get("result", {}).get("agent")
        except (AttributeError, JobError):
            agent = None
        identity_verified = (
            isinstance(agent, dict)
            and agent.get("pane_id") == pane_id
            and agent.get("agent") == "pi"
            and agent.get("name") == agent_name
        )
        if not identity_verified:
            return {"state": "unknown", "identity_verified": False, "prompt_verified": False,
                    "launch_issue": "observed direct local-lane worker identity differs"}
        state = agent.get("agent_status")
        observation = {"state": state if state in ("idle", "done", "working", "blocked") else "unknown",
                       "identity_verified": True, "prompt_verified": False}
        if state == "blocked":
            observation["launch_issue"] = "direct local-lane worker is blocked; inspect the pane"
        elif state == "working":
            code, terminal, _ = run_command([
                herdr, "pane", "read", pane_id, "--source", "visible", "--lines", "200",
            ], env=environment)
            observation["prompt_verified"] = code == 0 and has_active_turn_status(terminal)
        return observation

    authorize()
    telemetry["started_at"] = now()
    save(telemetry_path, validate_task_telemetry(telemetry, telemetry["route"]))
    effect_context["attempted"] = True
    try:
        code, output, _ = run_command(start, env=environment)
    except Exception as error:
        effect_context["observation"] = observe()
        raise JobError("unavailable_capability",
                       "Herdr direct local-lane start response is unavailable") from error
    if code:
        effect_context["observation"] = observe()
        raise JobError("unavailable_capability", "Herdr could not verify the direct local-lane Pi start")
    try:
        _require_herdr_result(output, "direct local-lane start")
    except JobError:
        effect_context["observation"] = observe()
        raise

    def submit():
        authorize()
        code, output, _ = run_command(
            [herdr, "agent", "prompt", agent_name, worker_prompt], env=environment)
        if code:
            raise PromptNotStarted("Herdr did not confirm delivery of the direct local-lane prompt")
        _require_herdr_result(output, "direct local-lane prompt")

    try:
        effect_context["action"] = "prompt"
        submit_and_verify(submit, observe, deadline=_PromptDeadline(30))
    except PromptNotStarted as error:
        raise JobError("unavailable_capability", str(error)) from error
    effect_context["action"] = "execution"
    while True:
        observation = observe()
        effect_context["observation"] = observation
        if observation.get("identity_verified") is not True:
            raise JobError("unavailable_capability",
                           "direct local-lane worker identity could not be verified; inspect before retrying")
        if observation.get("state") == "done":
            break
        if observation.get("state") == "blocked":
            raise JobError("unavailable_capability",
                           observation.get("launch_issue") or "direct local-lane worker is blocked")
        if observation.get("state") != "working":
            raise JobError("unavailable_capability",
                           "direct local-lane worker did not reach a verified terminal state; inspect before retrying")
        time.sleep(0.2)
    telemetry["ended_at"] = now()
    save(telemetry_path, validate_task_telemetry(telemetry, telemetry["route"]))
    effect_context["completed"] = True
    return {"outcome": "ready", "task_class": task_class, "pane_id": pane_id,
            "agent_name": agent_name}


def _require_herdr_result(output, operation):
    response = parse_json(output)
    if (not isinstance(response, dict) or not isinstance(response.get("result"), dict)
            or response.get("ok") is False or response.get("success") is False):
        raise JobError("unavailable_capability", f"Herdr returned an invalid {operation} response")


def check_preconditions(config, run=_run_readonly):
    """Require the model server and its reserved GPU headroom before a start."""
    unit = config["service_unit"]
    code, output, _ = run(["systemctl", "--user", "is-active", unit])
    if code != 0 or output != "active":
        raise JobError("unavailable_capability", f"local lane requires active user service {unit}")

    code, output, _ = run([
        "nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits",
    ])
    if code != 0:
        raise JobError("unavailable_capability", "local lane GPU memory could not be checked")
    rows = [line.strip() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or not rows[0].isdecimal():
        raise JobError("unavailable_capability", "local lane requires one readable NVIDIA GPU memory record")
    free_mib = int(rows[0])
    minimum = config["minimum_free_gpu_memory_mib"]
    if free_mib < minimum:
        raise JobError("unavailable_capability",
                       f"local lane needs at least {minimum} MiB free GPU memory; observed {free_mib} MiB")
    return {"service_unit": unit, "service_state": "active",
            "free_gpu_memory_mib": free_mib, "minimum_free_gpu_memory_mib": minimum}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "preview", "start"))
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--task-class")
    parser.add_argument("--local-lane-scope-json")
    parser.add_argument("--pane")
    parser.add_argument("--name")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--telemetry-file", type=Path)
    parser.add_argument("--route-decision", type=Path)
    parser.add_argument("--herdr", default=shutil.which("herdr") or "herdr")
    args = parser.parse_args(argv)
    try:
        policy = policy_record(load_json(args.policy))
        config = policy.get("local_lane")
        if config is None:
            raise JobError("unavailable_capability", "local lane is not configured by launch policy")
        if args.command in ("preview", "start"):
            if (args.task_class is None or args.local_lane_scope_json is None
                    or args.name is None or args.prompt_file is None):
                raise JobError("invalid_input", "direct local-lane preview/start requires task class, scope, name, and prompt file")
            scope = parse_json(args.local_lane_scope_json.encode())
            ensure_local_lane_admitted(config, args.task_class, scope, "direct")
            prompt = args.prompt_file.read_text(encoding="utf-8")
            spec = direct_route_spec(config, args.task_class, scope, args.name, prompt)
            if args.command == "preview":
                print(encoded({"route_decision": decision_for(spec, policy),
                               "capabilities": "unverified"}).decode())
                return 0
            if args.pane is None or args.telemetry_file is None:
                raise JobError("invalid_input", "direct local-lane start requires pane and telemetry file")
            if args.route_decision is None:
                raise JobError("decision_needed", "direct local-lane start requires --route-decision")
            spec["route_decision"] = load_json(args.route_decision)
            validate_decision(spec, policy)
            check_preconditions(config, run=_run_readonly)
            result = start_direct_local_lane(config, task_class=args.task_class,
                                             local_lane_scope=scope, pane=args.pane,
                                             name=args.name, prompt=prompt, herdr=args.herdr,
                                             telemetry_path=args.telemetry_file, policy=policy,
                                             route_decision=spec["route_decision"],
                                             run_command=_run_host_command)
            print(encoded(result).decode())
            return 0
        result = check_preconditions(config)
        print(encoded({"outcome": "ready", "checks": result}).decode())
        return 0
    except JobError as error:
        blocked = {"outcome": "blocked", "error": error.kind, "message": str(error)}
        if isinstance(error, UnknownDirectLocalLaneEffect):
            blocked["pending_effect"] = error.effect
        print(encoded(blocked).decode())
        return 4 if error.kind in ("unavailable_capability", "unresolved_effect") else 2
    except OSError as error:
        print(encoded({"outcome": "blocked", "error": "io_error", "message": str(error)}).decode())
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
