#!/usr/bin/env python3
"""Enforce the skill coverage policy documented in CONTRIBUTING.md.

Fails when a skill has neither a runnable suite nor a recorded no-suite
rationale, when claimed suite or trigger-fixture paths do not exist, when a
skill with executable code hides behind a no-suite rationale, or when a
routing-critical skill lacks valid positive and negative trigger fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
REGISTRY_PATH = ROOT / "tests" / "skill-coverage.json"
CASE_ID_RE = re.compile(r"[a-z0-9-]+")


def skill_directories() -> list[str]:
    return sorted(
        path.parent.name
        for path in SKILLS_DIR.glob("*/SKILL.md")
        if path.is_file()
    )


def has_executable_code(skill_dir: Path) -> bool:
    """Executable code is any .py file, or any file with the execute bit,
    excluding files under a tests/ directory (the skill's own tests)."""
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        if "tests" in rel.parts:
            continue
        if path.suffix == ".py":
            return True
        if path.stat().st_mode & 0o111:
            return True
    return False


def validate_trigger_fixture(path: Path, errors: list[str], label: str) -> None:
    if not path.is_file():
        errors.append(f"{label}: fixture file not found: {path}")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: unreadable fixture {path}: {exc}")
        return
    if not isinstance(payload, dict) or payload.get("version") != 1:
        errors.append(f"{label}: {path} must be a JSON object with version 1")
        return
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append(f"{label}: {path} must have a non-empty cases array")
        return
    ids: set[str] = set()
    prompts: set[str] = set()
    counts = {"yes": 0, "no": 0}
    for index, case in enumerate(cases):
        where = f"{label}: {path} case {index}"
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
        if activation not in counts:
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
            f"{label}: {path} needs at least one positive and one negative case; "
            f"found {counts['yes']} yes / {counts['no']} no"
        )


def validate_entry(entry: dict, skill_dir: Path, errors: list[str]) -> None:
    label = f"registry entry {entry.get('name')!r}"
    coverage = entry.get("coverage")
    if coverage not in {"suite", "no-suite"}:
        errors.append(f"{label}: coverage must be 'suite' or 'no-suite'")
        return

    suites = entry.get("suites")
    if coverage == "suite":
        if not isinstance(suites, list) or not suites:
            errors.append(f"{label}: coverage 'suite' needs a non-empty suites array")
        else:
            for suite in suites:
                suite_path = ROOT / suite
                if not suite_path.exists():
                    errors.append(f"{label}: suite path not found: {suite}")
                    continue
                if suite_path.is_file():
                    has_tests = suite_path.name.startswith("test_")
                else:
                    has_tests = any(suite_path.glob("test_*.py"))
                if not has_tests:
                    errors.append(f"{label}: suite path resolves to no test_*.py files: {suite}")
    else:
        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(f"{label}: coverage 'no-suite' requires a non-empty rationale")
        if has_executable_code(skill_dir):
            errors.append(
                f"{label}: skill has executable code, so it cannot carry a no-suite "
                f"rationale; give it a suite"
            )

    routing_critical = entry.get("routing_critical")
    if routing_critical is True:
        triggers = entry.get("triggers")
        if not isinstance(triggers, str) or not triggers.strip():
            errors.append(f"{label}: routing_critical skill needs a triggers path")
        else:
            validate_trigger_fixture(ROOT / triggers, errors, label)
    elif routing_critical is not False:
        errors.append(f"{label}: routing_critical must be a boolean")


def main() -> int:
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"skill-coverage: cannot read {REGISTRY_PATH}: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    entries = registry.get("skills")
    if not isinstance(entries, list):
        print("skill-coverage: registry must have a skills array", file=sys.stderr)
        return 1

    actual = skill_directories()
    listed = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            errors.append("registry entry: every entry needs a string name")
            continue
        listed.append(entry["name"])
        skill_dir = SKILLS_DIR / entry["name"]
        if not (skill_dir / "SKILL.md").is_file():
            errors.append(
                f"registry entry {entry['name']!r}: no such skill directory with a SKILL.md"
            )
            continue
        validate_entry(entry, skill_dir, errors)

    duplicated = {name for name in listed if listed.count(name) > 1}
    for name in sorted(duplicated):
        errors.append(f"registry entry {name!r}: listed more than once")

    missing = set(actual) - set(listed)
    for name in sorted(missing):
        errors.append(
            f"skill {name!r}: missing from tests/skill-coverage.json; every skill needs "
            f"a suite or a recorded no-suite rationale"
        )

    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        print(
            f"skill-coverage: {len(errors)} violation(s) across {len(actual)} skills",
            file=sys.stderr,
        )
        return 1

    suite_count = sum(1 for e in entries if isinstance(e, dict) and e.get("coverage") == "suite")
    routing_count = sum(
        1 for e in entries if isinstance(e, dict) and e.get("routing_critical") is True
    )
    print(
        f"skill-coverage: OK — {len(actual)} skills; {suite_count} with suites, "
        f"{len(actual) - suite_count} with recorded no-suite rationales, "
        f"{routing_count} routing-critical with trigger fixtures"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
