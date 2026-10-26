"""Journal a bounded quota continuation before sending input to an owned pane."""

from pathlib import Path

from .records import JobError, now
from .transport import BudgetExpired, EffectUnknown


CONTINUATION_TEXT = (
    "Continue your assigned task from the existing checkpoint. Preserve the original brief, "
    "assignment revision, worktree, and acceptance criteria; reconcile completed effects before "
    "acting and finish the required artifacts and receipt."
)


def continue_job(engine, job_id):
    matches = [job for job in engine.state["jobs"] if job["spec"]["job_id"] == job_id]
    if len(matches) != 1:
        raise JobError("invalid_input", "continue requires exactly one existing job ID")
    job = matches[0]
    route = job["spec"]["route"]
    if route["mode"] != "agent" or route["runtime"] != "codex":
        raise JobError("decision_needed", "same-pane continuation requires an existing Codex agent job")
    pending = job["pending_effect"]
    quota_interrupted_submission = pending is not None and pending["action"] in ("start", "prompt")
    receipt = Path(job["host_output"]) / "receipt.json"
    if (receipt.exists() or receipt.is_symlink() or not job["pane_id"] or job["workspace_closed"] or job["settled"]
            or job["collection"] is not None or (pending is not None and not quota_interrupted_submission)):
        raise JobError("decision_needed", "job is completed, unowned, or has an unresolved effect; inspect before continuation")
    if job["rate_limit_recoveries"] >= 1 or job["telemetry"]["continuation_count"] >= 1:
        raise JobError("decision_needed", "the single quota recovery budget is already consumed")
    evidence = engine.transport.continuation_evidence(job)
    recorded = next((entry["evidence"] for entry in reversed(job["history"])
                     if entry["action"] == "continue_quota_observed"
                     and entry["attempt_id"] == job["attempt_id"]
                     and entry["pane_id"] == job["pane_id"]
                     and entry["evidence"]["sha256"] == evidence["sha256"]), None)
    if recorded is not None:
        evidence["reset_at"] = recorded["reset_at"]
    else:
        job["history"].append({"action": "continue_quota_observed", "attempt_id": job["attempt_id"],
                               "pane_id": job["pane_id"], "evidence": evidence, "observed_at": now()})
        engine.checkpoint()
    if evidence["reset_at"] > evidence["observed_at"]:
        raise JobError("decision_needed", "parsed Codex reset has not passed")
    if engine.deadline.remaining() <= 2:
        raise JobError("unavailable_capability", "insufficient call budget for delayed continuation")
    engine.reconcile_coordinator_checkpoint()
    engine.coordinator_takeover_reconciled = True
    if quota_interrupted_submission:
        job["history"].append({"action": pending["action"], "attempt_id": job["attempt_id"],
                               "reconciled_by": "owned Codex quota banner before continuation",
                               "observed_at": now()})
        job["phase"] = "submitted"
    job["pending_effect"] = {"action": "continue", "started_at": now()}
    job["rate_limit_recoveries"] += 1
    job["telemetry"]["continuation_count"] += 1
    job["telemetry"]["continuation_reasons"].append({
        "reason": "same_pane_quota", "attempt_id": job["attempt_id"], "pane_id": job["pane_id"],
        "observed_at": now(), "reset_at": evidence["reset_at"],
    })
    job["telemetry"]["quota_signals"].append({"kind": "rate_limit", **evidence})
    job["history"].append({"action": "continue_intent", "attempt_id": job["attempt_id"],
                           "pane_id": job["pane_id"], "evidence": evidence, "observed_at": now()})
    engine.checkpoint()
    try:
        observation = engine.transport.continue_prompt(job, CONTINUATION_TEXT, evidence)
    except (BudgetExpired, EffectUnknown, JobError, OSError) as error:
        job["issue"] = "continuation delivery is unproven; inspect the owned pane before further action"
        engine.checkpoint()
        raise JobError("unresolved_effect", job["issue"]) from error
    record_continuation_working(job, observation)
    engine.checkpoint()


def record_continuation_working(job, observation):
    """Reconcile a journaled submission only from a verified Working observation."""
    job["observed"] = {**observation, "observed_at": now()}
    job["rate_limit_seen"] = False
    job["rate_limit_until"] = None
    job["telemetry"]["ended_at"] = None
    job["phase"] = "submitted"
    job["activity_seen"] = True
    job["pending_effect"] = None
    job["issue"] = None
    job["issue_code"] = None
    job["history"].append({"action": "continue", "attempt_id": job["attempt_id"],
                           "pane_id": job["pane_id"], "reconciled_by": "observed Codex Working state",
                           "observed_at": now()})
