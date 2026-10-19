"""Conservative discovery and cleanup for panes owned by managed Herdr runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .admission import AdmissionStore
from .records import JobError


def _json_command(argv):
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise JobError("unavailable_capability", completed.stderr.strip() or completed.stdout.strip())
    try:
        value = json.loads(completed.stdout)
        return value.get("result", value)
    except (ValueError, AttributeError) as error:
        raise JobError("unavailable_capability", f"invalid JSON from {' '.join(argv[:2])}") from error


def _run_states(root):
    """Yield parseable managed checkpoints, skipping unreadable or malformed files."""
    for state_path in sorted(root.glob("**/state.json")):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(state, dict):
            yield state_path, state


def _managed_runs(root, leases):
    lease_ids = {item["lease_id"] for item in leases}
    found = []
    for state_path, state in _run_states(root):
        jobs = state.get("jobs")
        if not isinstance(jobs, list):
            continue
        matching = [job for job in jobs if isinstance(job, dict)
                    and job.get("admission_lease_id") in lease_ids
                    and job.get("admission_acquired") is True
                    and job.get("settled") is True]
        if matching:
            request = state.get("request")
            found.append({"run_dir": str(state_path.parent),
                          "request_id": request.get("request_id") if isinstance(request, dict) else None,
                          "lease_ids": sorted({job["admission_lease_id"] for job in matching}),
                          "workspace_ids": sorted({job.get("workspace_id") for job in matching
                                                    if isinstance(job.get("workspace_id"), str)}),
                          "jobs": [job.get("spec", {}).get("job_id") for job in matching]})
    return found


def _run_directory_for_id(root, run_id):
    for state_path, state in _run_states(root):
        request = state.get("request")
        if isinstance(request, dict) and request.get("request_id") == run_id:
            return str(state_path.parent)
    return None


def _active_lease_ids(root, leases):
    lease_ids = {item["lease_id"] for item in leases}
    active = set()
    for state_path, state in _run_states(root):
        jobs = state.get("jobs", [])
        active.update(job["admission_lease_id"] for job in jobs if isinstance(job, dict)
                      and job.get("admission_lease_id") in lease_ids
                      and job.get("admission_acquired") is True
                      and job.get("settled") is not True)
    return active


def _caller_workspace(env):
    workspace_id = env.get("HERDR_WORKSPACE_ID")
    if workspace_id:
        return workspace_id
    pane_id = env.get("HERDR_PANE_ID", "")
    return pane_id.partition(":")[0] if ":" in pane_id else None


def _reconciliation_evidence(lease, *, herdr, run_command):
    effect = lease.get("pending_effect")
    if effect is None:
        return None
    pane_id = effect.get("pane_id") if isinstance(effect, dict) else None
    if not isinstance(pane_id, str) or not pane_id:
        return None
    agent = run_command([herdr, "agent", "get", pane_id], capture_output=True,
                        text=True, check=False)
    if agent.returncode:
        return None
    try:
        agent_value = json.loads(agent.stdout)
        agent_record = agent_value.get("result", {}).get("agent", agent_value.get("result", {}))
    except (ValueError, AttributeError):
        return None
    status = agent_record.get("agent_status") if isinstance(agent_record, dict) else None
    if status not in ("done", "error"):
        return None
    pane = run_command([herdr, "pane", "read", pane_id, "--source", "recent-unwrapped", "--lines", "40"],
                       capture_output=True, text=True, check=False)
    if pane.returncode:
        return None
    if not re.search(r"(?:^|\n)(?:[^\n]*[$#>%])\s*$", pane.stdout):
        return None
    return f"Herdr agent {pane_id} is {status} and its recent pane output ends at a shell prompt."


def _release_orphan(lease, evidence, *, run_command):
    if evidence is None:
        return {"lease_id": lease["lease_id"], "released": False,
                "run_id": lease.get("run_id"), "reason": "Herdr could not verify a terminal agent at a shell prompt"}
    admission_cli = Path(__file__).resolve().parents[1] / "herdr-admission.py"
    completed = run_command([sys.executable, str(admission_cli), "release", "--lease-id",
                             lease["lease_id"], "--reconciliation-evidence", evidence],
                            capture_output=True, text=True, check=False)
    if completed.returncode:
        return {"lease_id": lease["lease_id"], "released": False,
                "run_id": lease.get("run_id"), "reason": completed.stderr.strip() or completed.stdout.strip()}
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        return {"lease_id": lease["lease_id"], "released": False,
                "run_id": lease.get("run_id"), "reason": "admission release returned invalid JSON"}
    return {"lease_id": lease["lease_id"], "run_id": lease.get("run_id"), **result}


def cleanup_stale(args, *, env=None, herdr="herdr", admission_dir=None):
    """Report mapped leases by default; --apply delegates to managed finish."""
    env = os.environ if env is None else env
    if not 0 <= args.wait_seconds <= 60:
        raise JobError("invalid_input", "wait-seconds must be finite and between 0 and 60")
    root = Path(args.runs_root or (Path.home() / ".local/share/orchestrator/runs")).expanduser()
    store = AdmissionStore(admission_dir or (Path.home() / ".local/state/orchestrator"))
    leases = store._load()["leases"]
    runs = _managed_runs(root, leases)
    live = _json_command([herdr, "workspace", "list"])
    workspaces = live.get("workspaces", []) if isinstance(live, dict) else []
    open_ids = {item.get("workspace_id") for item in workspaces if isinstance(item, dict)}
    own_workspace = _caller_workspace(env)
    actions = []
    for run in runs:
        live_workspaces = [value for value in run["workspace_ids"] if value in open_ids]
        if own_workspace in live_workspaces:
            actions.append({**run, "action": "refuse", "reason": "run includes the coordinator workspace"})
        elif live_workspaces:
            actions.append({**run, "action": "finish"})
        else:
            actions.append({**run, "action": "finish", "reason": "workspace already absent; finish reconciles by run state"})
    mapped = {lease_id for run in runs for lease_id in run["lease_ids"]}
    active_ids = _active_lease_ids(root, leases)
    orphaned = []
    for lease in leases:
        if lease["lease_id"] in mapped or lease["lease_id"] in active_ids:
            continue
        run_dir = _run_directory_for_id(root, lease.get("run_id"))
        evidence = _reconciliation_evidence(lease, herdr=herdr, run_command=subprocess.run)
        orphaned.append({"lease_id": lease["lease_id"], "run_id": lease.get("run_id"),
                         "run_dir": run_dir,
                         "action": "release" if evidence is not None else "refuse",
                         "reconciliation_evidence": evidence,
                         "reason": (None if evidence is not None else
                                    "lease could not be released; reconcile run directory " + (run_dir or "unknown"))})
    active = [{"lease_id": lease["lease_id"], "run_id": lease.get("run_id"),
               "action": "retain", "reason": "managed job is not settled; worker remains open"}
              for lease in leases if lease["lease_id"] in active_ids]
    orphaned = [item for item in orphaned if item["lease_id"] not in active_ids]
    result = {"schema_version": 1, "mode": "apply" if args.apply else "dry-run",
              "actions": actions, "retained_active": active,
              "orphan_leases": orphaned, "complete": True}
    if not args.apply:
        for item in orphaned:
            item["action"] = "would_release" if item["action"] == "release" else "refuse"
        return result
    finished = []
    for action in actions:
        if action["action"] != "finish":
            finished.append({"run_dir": action["run_dir"], "finished": False,
                             "issue": action.get("reason", "refused")})
            result["complete"] = False
            continue
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "herdr-jobs.py"),
                   "finish", "--run-dir", action["run_dir"], "--wait-seconds", str(args.wait_seconds)]
        state_path = Path(action["run_dir"]) / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        contract = state.get("host_contract")
        if isinstance(contract, str) and contract:
            command += ["--host-contract", contract]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        try:
            finish_result = json.loads(completed.stdout)
        except ValueError:
            finish_result = {"error": completed.stderr.strip() or completed.stdout.strip()}
        finished.append({"run_dir": action["run_dir"], "exit_code": completed.returncode,
                         "result": finish_result})
        if completed.returncode != 0:
            result["complete"] = False
    result["finished"] = finished
    released = []
    for item in orphaned:
        if item["action"] != "release":
            released.append({"lease_id": item["lease_id"], "released": False,
                             "run_dir": item["run_dir"], "reason": item["reason"]})
            result["complete"] = False
            continue
        lease = next(value for value in leases if value["lease_id"] == item["lease_id"])
        released.append(_release_orphan(lease, item["reconciliation_evidence"],
                                        run_command=subprocess.run))
        if not released[-1].get("released"):
            result["complete"] = False
    result["released_orphans"] = released
    return result


def close_workspace(workspace_id, *, env=None, herdr="herdr", runs_root=None,
                    admission_dir=None, run_command=subprocess.run):
    """Preserve ordinary close behavior while refusing lease-bound workspaces."""
    env = os.environ if env is None else env
    if workspace_id == _caller_workspace(env):
        raise JobError("decision_needed", "refusing to close the coordinator workspace")
    root = Path(runs_root or (Path.home() / ".local/share/orchestrator/runs")).expanduser()
    leases = AdmissionStore(admission_dir or (Path.home() / ".local/state/orchestrator")).status()["leases"]
    active_ids = {item["lease_id"] for item in leases}
    for state_path, state in _run_states(root):
        jobs = state.get("jobs", [])
        for job in jobs:
            if (isinstance(job, dict) and job.get("workspace_id") == workspace_id
                    and job.get("admission_lease_id") in active_ids):
                raise JobError("decision_needed", f"workspace is bound to a provider lease; finish run directory {state_path.parent} or use cleanup-stale")
    completed = run_command([herdr, "workspace", "close", workspace_id],
                             capture_output=True, text=True, check=False)
    if completed.returncode:
        raise JobError("unavailable_capability", completed.stderr.strip() or completed.stdout.strip())
    return {"closed": True, "workspace_id": workspace_id}
