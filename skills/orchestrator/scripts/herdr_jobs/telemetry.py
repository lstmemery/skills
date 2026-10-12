"""Launcher-owned task telemetry shared by managed and direct routes."""

from .records import JobError, fields, version


COORDINATOR_GRADES = {"accepted", "rework", "rejected"}


def task_telemetry(spec, resolved_model, provider):
    selected = spec.get("model_route") or {}
    override = spec.get("override") or {}
    if spec.get("local_lane") is True:
        lane = "local_lane"
    elif selected:
        lane = "model_ladder"
    elif any(key in override for key in ("runtime", "model", "provider", "effort")):
        lane = "explicit"
    else:
        lane = "ordinary"
    route = {"lane": lane, "level": selected.get("level"),
             "choice": selected.get("choice", "explicit" if lane == "explicit" else "default"),
             "model": resolved_model or spec.get("model"),
             "effort": selected.get("effort", override.get("effort")),
             "harness": spec["route"]["runtime"], "provider": provider}
    quota_signals = []
    fallback = selected.get("fallback")
    if isinstance(fallback, dict):
        evidence = fallback.get("evidence", {})
        quota_signals.append({"kind": fallback.get("reason"), "source": evidence.get("source"),
                              "observed_at": evidence.get("observed_at"),
                              "reset_at": evidence.get("reset_at")
                              or evidence.get("quota", {}).get("reset_at")})
    local = spec.get("local_lane") is True
    return {"schema_version": 1, "route": route, "started_at": None, "ended_at": None,
            "continuation_count": 0, "continuation_reasons": [],
            "quota_signals": quota_signals, "coordinator_grade": "pending",
            "independent_validation": {"status": "pending" if local else "not_required",
                                       "exit_code": None, "observed_at": None,
                                       "evidence": None}}


def validate_task_telemetry(value, expected_route):
    fields(value, ["schema_version", "route", "started_at", "ended_at", "continuation_count",
                   "continuation_reasons", "quota_signals", "coordinator_grade",
                   "independent_validation"], label="task telemetry")
    version(value["schema_version"])
    route = fields(value["route"], ["lane", "level", "choice", "model", "effort", "harness", "provider"],
                   label="task telemetry route")
    if route != expected_route:
        raise JobError("conflict", "stored task telemetry route differs from the pinned worker route")
    for key in ("started_at", "ended_at"):
        if value[key] is not None and not isinstance(value[key], str):
            raise JobError("conflict", f"stored telemetry {key} must be text or null")
    if type(value["continuation_count"]) is not int or value["continuation_count"] < 0:
        raise JobError("conflict", "stored telemetry continuation_count is invalid")
    if not isinstance(value["continuation_reasons"], list) or not isinstance(value["quota_signals"], list):
        raise JobError("conflict", "stored telemetry continuation and quota histories must be arrays")
    if value["coordinator_grade"] not in ("pending", *COORDINATOR_GRADES):
        raise JobError("conflict", "stored coordinator_grade is invalid")
    validation = fields(value["independent_validation"],
                         ["status", "exit_code", "observed_at", "evidence"],
                         label="task telemetry independent_validation")
    if validation["status"] not in ("pending", "passed", "failed", "not_required"):
        raise JobError("conflict", "stored independent validation status is invalid")
    if validation["exit_code"] is not None and type(validation["exit_code"]) is not int:
        raise JobError("conflict", "stored independent validation exit_code is invalid")
    if validation["observed_at"] is not None and not isinstance(validation["observed_at"], str):
        raise JobError("conflict", "stored independent validation timestamp is invalid")
    if validation["evidence"] is not None and not isinstance(validation["evidence"], str):
        raise JobError("conflict", "stored independent validation evidence is invalid")
    return value
