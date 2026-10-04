"""Owner of the finding-evidence contract shared by disposition sync and integration preflight.

review_dispositions.py (sync) and integration-preflight.py (gate) must accept or
reject the same review-evidence.json sidecars; both import this module instead of
keeping copies. Amend the contract only here, and update
skills/orchestrator/references/review.md — the prose owner — in the same change.
"""

FINDING_EVIDENCE_FIELDS = frozenset(
    {"confidence", "reproducer", "evidence", "unresolved_assumption"}
)
FINDING_CONFIDENCE = frozenset({"high", "medium", "low"})
REVIEW_EVIDENCE_SCHEMA_VERSIONS = frozenset({1, 2})


def finding_evidence_error(finding, review_name, schema_version):
    """Return the contract violation for a finding as a message, or None."""
    has_metadata = bool(finding.keys() & FINDING_EVIDENCE_FIELDS)
    finding_id = finding["id"]
    if schema_version == 2 and not has_metadata:
        return f"{review_name}/{finding_id} schema version 2 finding requires evidence metadata"
    if not has_metadata:
        return None

    confidence = finding.get("confidence")
    reproducer = finding.get("reproducer")
    evidence = finding.get("evidence")
    assumption = finding.get("unresolved_assumption")
    if type(confidence) is not str or confidence not in FINDING_CONFIDENCE:
        return f"{review_name}/{finding_id} has invalid finding confidence"
    if reproducer is not None and (
        not isinstance(reproducer, str) or not reproducer.strip()
    ):
        return f"{review_name}/{finding_id} has an invalid reproducer"
    if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
        return f"{review_name}/{finding_id} has invalid finding evidence"
    if assumption is not None and (
        not isinstance(assumption, str) or not assumption.strip()
    ):
        return f"{review_name}/{finding_id} has an invalid unresolved assumption"
    has_reproducer = isinstance(reproducer, str) and bool(reproducer.strip())
    has_unresolved_evidence = (
        isinstance(evidence, str)
        and bool(evidence.strip())
        and isinstance(assumption, str)
        and bool(assumption.strip())
    )
    if not (has_reproducer or has_unresolved_evidence):
        return f"{review_name}/{finding_id} needs a reproducer or evidence and unresolved assumption"
    return None
