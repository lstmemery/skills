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
                      parse_json, policy_record, text)
from .prompt import PromptNotStarted, has_active_turn_status, submit_and_verify
from .security import contains_secret_material


POLICY_PATH = Path(__file__).resolve().parents[2] / "launch-policy.json"
LOCAL_ENV_KEYS = ("HOME", "PATH", "TERM", "LANG")
PI_ENV_KEYS = ("PI_CODING_AGENT_DIR", "PI_CODING_AGENT_SESSION_DIR")
HERDR_ENV_KEYS = ("HERDR_SESSION", "HERDR_BIN_PATH", "HERDR_SOCKET_PATH")


class _PromptDeadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return max(0, self.end - time.monotonic())


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
    )


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
                            source_env=None, herdr="herdr", run_command=_run_host_command,
                            policy=None, admission_service=None):
    """Validate and start the explicitly scoped direct Herdr local-lane route."""
    task_class, local_lane_scope = ensure_local_lane_admitted(
        local_config, task_class, local_lane_scope, "direct")
    pane_id = text(pane, "pane ID", 200)
    agent_name = text(name, "agent name", 32)
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", agent_name):
        raise JobError("invalid_input", "direct local-lane agent name must match [a-z][a-z0-9_-]{0,31}")
    prompt = text(prompt, "prompt", 100000)
    worker_prompt = text(local_lane_instruction(task_class, local_lane_scope)
                         + "\n\n" + prompt, "local-lane prompt", 100000)
    if contains_secret_material(worker_prompt):
        raise JobError("invalid_input", "local_lane prompt contains secret-like material; remove credentials or secrets before launch")

    environment = local_lane_environment(source_env, pane_id)
    policy = policy or policy_record(load_json(POLICY_PATH))
    from .admission_service import AdmissionService

    admission_service = admission_service or AdmissionService()
    lease_id = "direct-local-" + uuid.uuid4().hex
    admission_job = {
        "provider": local_config["provider"],
        "admission_model": local_config["model"],
        "admission_lease_id": lease_id,
    }
    admission = admission_service.acquire(
        admission_job,
        admission_service.provider_cap(policy, local_config["provider"]),
        lease_id,
    )
    if not admission["admitted"]:
        raise JobError("unavailable_capability",
                       f"direct local-lane admission denied: {admission['reason']}")
    try:
        return _launch_direct_local_lane(
            local_config, task_class, pane_id, agent_name, worker_prompt, environment,
            herdr, run_command,
        )
    finally:
        admission_service.release(admission_job)


def _launch_direct_local_lane(local_config, task_class, pane_id, agent_name, worker_prompt,
                              environment, herdr, run_command):
    sanitize_pane_environment(pane_id, environment, herdr=herdr, run_command=run_command)
    start = [herdr, "agent", "start", agent_name, "--kind", "pi", "--pane", pane_id,
             "--", "--provider", local_config["provider"], "--model", local_config["model"], "--approve"]
    code, output, _ = run_command(start, env=environment)
    if code:
        raise JobError("unavailable_capability", "Herdr could not verify the direct local-lane Pi start")
    _require_herdr_result(output, "direct local-lane start")

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

    def submit():
        code, output, _ = run_command(
            [herdr, "agent", "prompt", agent_name, worker_prompt], env=environment)
        if code:
            raise PromptNotStarted("Herdr did not confirm delivery of the direct local-lane prompt")
        _require_herdr_result(output, "direct local-lane prompt")

    try:
        submit_and_verify(submit, observe, deadline=_PromptDeadline(30))
    except PromptNotStarted as error:
        raise JobError("unavailable_capability", str(error)) from error
    while True:
        observation = observe()
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
    return {"outcome": "ready", "task_class": task_class, "pane_id": pane_id, "agent_name": agent_name}


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
    parser.add_argument("command", choices=("preflight", "start"))
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--task-class")
    parser.add_argument("--local-lane-scope-json")
    parser.add_argument("--pane")
    parser.add_argument("--name")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--herdr", default=shutil.which("herdr") or "herdr")
    args = parser.parse_args(argv)
    try:
        policy = policy_record(load_json(args.policy))
        config = policy.get("local_lane")
        if config is None:
            raise JobError("unavailable_capability", "local lane is not configured by launch policy")
        if args.command == "start":
            if (args.task_class is None or args.local_lane_scope_json is None
                    or args.pane is None or args.name is None or args.prompt_file is None):
                raise JobError("invalid_input", "direct local-lane start requires task class, scope, pane, name, and prompt file")
            scope = parse_json(args.local_lane_scope_json.encode())
            ensure_local_lane_admitted(config, args.task_class, scope, "direct")
            check_preconditions(config)
            prompt = args.prompt_file.read_text(encoding="utf-8")
            result = start_direct_local_lane(config, task_class=args.task_class,
                                             local_lane_scope=scope, pane=args.pane,
                                             name=args.name, prompt=prompt, herdr=args.herdr,
                                             policy=policy)
            print(encoded(result).decode())
            return 0
        result = check_preconditions(config)
        print(encoded({"outcome": "ready", "checks": result}).decode())
        return 0
    except JobError as error:
        print(encoded({"outcome": "blocked", "error": error.kind, "message": str(error)}).decode())
        return 4 if error.kind == "unavailable_capability" else 2
    except OSError as error:
        print(encoded({"outcome": "blocked", "error": "io_error", "message": str(error)}).decode())
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
