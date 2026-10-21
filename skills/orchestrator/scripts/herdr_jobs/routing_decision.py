"""Coordinator route records, separate from native capability and admission proof."""

from copy import deepcopy

from .model_ladder import HARNESS_ALIASES, billing_pool, validate_escalation
from .records import JobError, digest, encoded, fields, integer, text


PIN_FIELDS = ("runtime", "provider", "model", "effort")


def assessment_record(value):
    fields(value, ["task_class", "difficulty_evidence", "sufficiency_reason"],
           ["escalation_steps"], "routing assessment")
    text(value["task_class"], "routing task_class", 100)
    evidence = fields(value["difficulty_evidence"], ["source", "summary"],
                      label="routing difficulty evidence")
    text(evidence["source"], "difficulty source", 1000)
    text(evidence["summary"], "difficulty summary", 4000)
    text(value["sufficiency_reason"], "sufficiency reason", 4000)
    if "escalation_steps" in value:
        integer(value["escalation_steps"], "escalation steps", 0, 2)
    return value


def decision_for(spec, policy):
    """Compile the coordinator's manifest; never infer pins from resolved fields."""
    requested = deepcopy(spec.get("requested_override", spec.get("override") or {}))
    pins = {key: requested[key] for key in PIN_FIELDS if key in requested}
    selected = spec.get("model_route") or {}
    escalation = spec.get("escalation")
    assessment = spec.get("routing_assessment")
    if assessment is not None:
        assessment_record(assessment)
        if spec.get("task_class") not in (None, assessment["task_class"]):
            raise JobError("invalid_input", "routing assessment task class differs from the assignment")
    elif escalation:
        assessment = {
            "task_class": spec.get("task_class", "implementation" if spec["writes_repository"] else "ordinary"),
            "difficulty_evidence": {"source": escalation["failure"]["source"],
                                    "summary": escalation["failure"]["summary"]},
            "sufficiency_reason": escalation["expected_improvement"],
        }
    else:
        assessment = {
            "task_class": spec.get("task_class", spec.get("task_kind", "ordinary")),
            "difficulty_evidence": {"source": spec["task_file"],
                                    "summary": spec.get("output_expectation", "Assigned bounded work")},
            "sufficiency_reason": ("User's exact model/effort constraint" if {"model", "effort"} & pins.keys()
                                   else "Cheapest compatible primary; no difficulty escalation"),
        }
    level = selected.get("level")
    if level is not None and level > 1 and not ({"model", "effort"} & pins.keys()) and not escalation:
        raise JobError("decision_needed", "harness/provider preference does not authorize higher capacity; "
                       "record per-task difficulty and escalation or request a representable lower route")
    if escalation:
        validate_escalation(escalation, spec["model_level"])
        steps = assessment.get("escalation_steps", 1)
        if steps < 1:
            raise JobError("invalid_input", "escalation requires at least one recorded step")
    resolved = {"runtime": spec["route"]["runtime"], "provider": spec.get("provider"),
                "model": spec.get("model"), "effort": selected.get("effort", (spec.get("override") or {}).get("effort"))}
    for key, value in pins.items():
        normalized = HARNESS_ALIASES.get(value, value) if key == "runtime" else billing_pool(value) if key == "provider" else value
        if resolved[key] != normalized:
            raise JobError("conflict", f"route violates the user's {key} pin")
    return {"schema_version": 1, "job_id": spec["job_id"],
            "task_sha256": digest(spec.get("task", "").encode()),
            "requested": requested, "pins": pins, "resolved": resolved,
            "task_class": assessment["task_class"],
            "difficulty_evidence": deepcopy(assessment["difficulty_evidence"]),
            "sufficiency_reason": assessment["sufficiency_reason"],
            "level": level, "choice": selected.get("choice", "explicit" if pins else "policy"),
            "csv_sha256": policy.get("model_ladder", {}).get("sha256"),
            "escalation": deepcopy(escalation),
            "escalation_steps": assessment.get("escalation_steps", 1 if escalation else 0),
            "fallback": deepcopy(selected.get("fallback"))}


def validate_decision(spec, policy):
    expected = decision_for(spec, policy)
    if spec.get("route_decision") != expected:
        raise JobError("decision_needed", "missing or changed coordinator route decision; re-prepare the assignment")
    return expected


def decision_digest(spec):
    return digest(encoded(spec["route_decision"]))
