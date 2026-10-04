#!/usr/bin/env python3
"""Single source of truth for trigger-fixture (trigger-cases.json) validation.

The schema is documented in CONTRIBUTING.md and was introduced by
skills/shopping. Two consumers share this module so the checks cannot
diverge:

- tests/check_skill_coverage.py — the repo-wide coverage policy check.
- skills/shopping/tests/run_triggers.py — the shopping runner, which adds a
  shopping-specific exact case-count requirement on top.
"""

from __future__ import annotations

import re
from typing import Any

# Strict kebab-case: lowercase alphanumeric groups joined by single hyphens.
# Rejects leading/trailing hyphens, doubled hyphens, uppercase, underscores.
CASE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

ACTIVATIONS = ("yes", "no")


def schema_errors(payload: Any) -> list[str]:
    """Return the schema violations in a decoded trigger-cases.json payload.

    An empty list means the payload is schema-valid: an object with
    ``version`` 1, a non-empty ``cases`` array of unique-id kebab-case cases
    with 'yes'/'no' activation and unique non-empty prompts, and at least one
    positive and one negative case.
    """
    if not isinstance(payload, dict):
        return ["must be a JSON object with version 1"]

    errors: list[str] = []
    if payload.get("version") != 1:
        errors.append("must be a JSON object with version 1")

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("must have a non-empty cases array")
        return errors

    ids: set[str] = set()
    prompts: set[str] = set()
    counts = dict.fromkeys(ACTIVATIONS, 0)
    for index, case in enumerate(cases):
        where = f"case {index}"
        if not isinstance(case, dict):
            errors.append(f"{where} must be an object")
            continue
        case_id = case.get("id")
        activation = case.get("activation")
        prompt = case.get("prompt")
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            errors.append(f"{where} has an invalid id (want kebab-case)")
            continue
        if case_id in ids:
            errors.append(f"{where} duplicates id {case_id}")
        ids.add(case_id)
        if not isinstance(activation, str) or activation not in counts:
            errors.append(f"{where} ({case_id}) activation must be 'yes' or 'no'")
            continue
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{where} ({case_id}) prompt must be non-empty text")
            continue
        normalized = " ".join(prompt.split()).casefold()
        if normalized in prompts:
            errors.append(f"{where} ({case_id}) duplicates an earlier prompt")
        prompts.add(normalized)
        counts[activation] += 1

    if counts["yes"] < 1 or counts["no"] < 1:
        errors.append(
            "needs at least one positive and one negative case; "
            f"found {counts['yes']} yes / {counts['no']} no"
        )
    return errors


def count_activations(cases: list[dict[str, Any]]) -> dict[str, int]:
    """Count 'yes'/'no' activations across schema-valid cases."""
    counts = dict.fromkeys(ACTIVATIONS, 0)
    for case in cases:
        counts[case["activation"]] += 1
    return counts
