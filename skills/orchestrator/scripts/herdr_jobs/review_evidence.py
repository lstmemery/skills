"""Shared review-response parsing and finding-evidence contract.

review_dispositions.py (sync) and integration-preflight.py (gate) must accept or
reject the same review-evidence.json sidecars; both import this module instead of
keeping copies. Amend the contract only here, and update
skills/orchestrator/references/review.md — the prose owner — in the same change.
"""

import re


FINDING_LINE = re.compile(
    r"^\s*-\s*`(?P<review>[^`]+)/(?P<finding>[^`]+)`\s*:\s*"
    r"(?P<disposition>fixed|rejected)\s+—\s+(?P<detail>\S.*)$"
)
FINDING_REFERENCE_LINE = re.compile(r"^\s*-\s*`[^`]+/[^`]+`")


def parse_review_response(text, *, reject_malformed=False):
    """Parse response dispositions, optionally rejecting malformed references.

    Preflight historically ignores malformed response lines; disposition sync
    rejects lines that look like finding references but do not match the grammar.
    """
    dispositions = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        match = FINDING_LINE.match(line)
        if match is None:
            if reject_malformed and FINDING_REFERENCE_LINE.match(line):
                raise ValueError(f"malformed review disposition at review-response.md:{line_number}")
            continue
        key = (match.group("review"), match.group("finding"))
        if key in dispositions:
            raise ValueError(f"duplicate disposition for {key[0]}/{key[1]}")
        dispositions[key] = (match.group("disposition"), match.group("detail").strip())
    return dispositions

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
