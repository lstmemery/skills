#!/usr/bin/env python3
"""Run explicit skill-activation checks using isolated copies of this skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable


TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
CASES_FILE = TESTS_DIR / "trigger-cases.json"
HARNESS_NAMES = ("claude", "omp", "pi")
CLAUDE_PLUGIN_NAME = "shopping-trigger-test"
RUNNER_VERSION = 2
TIMEOUT_SECONDS = 75
CLAUDE_MAX_BUDGET_USD = "0.25"
ROUTER_SYSTEM_PROMPT = (
    "Classify whether a listed specialized skill directly applies to the user's request. "
    "Use the skill metadata as the routing rule. If a skill directly matches, load or read it "
    "before doing anything else. If none matches, do not load any skill. Do not complete the "
    "underlying task; stop after this routing decision."
)


def load_cases() -> list[dict[str, Any]]:
    payload = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("trigger-cases.json must have version 1 and a cases array")

    cases = payload["cases"]
    ids: set[str] = set()
    prompts: set[str] = set()
    counts = {"yes": 0, "no": 0}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} must be an object")
        case_id = case.get("id")
        activation = case.get("activation")
        prompt = case.get("prompt")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9-]+", case_id):
            raise ValueError(f"case {index} has an invalid id")
        if case_id in ids:
            raise ValueError(f"duplicate case id: {case_id}")
        ids.add(case_id)
        if activation not in counts:
            raise ValueError(f"{case_id}: activation must be 'yes' or 'no'")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{case_id}: prompt must be non-empty text")
        normalized_prompt = " ".join(prompt.split()).casefold()
        if normalized_prompt in prompts:
            raise ValueError(f"duplicate prompt for {case_id}")
        prompts.add(normalized_prompt)
        counts[activation] += 1

    if counts != {"yes": 8, "no": 5}:
        raise ValueError(f"expected 8 positive and 5 negative prompts; found {counts}")
    return cases


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from string_values(child)


def parse_json_lines(output: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def diagnostic_excerpt(output: str) -> str | None:
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(
            r"(?i)(api[ _-]?key|access[ _-]?token|refresh[ _-]?token|authorization|password)(\s*[:=]\s*)\S+",
            r"\1\2<redacted>",
            line,
        )
        line = re.sub(r"/home/[^/\s]+", "~", line)
        return line[:240]
    return None


def claude_skill_events(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    uses: list[dict[str, Any]] = []
    results: dict[str, bool] = {}
    for record in records:
        for node in walk_dicts(record):
            if node.get("type") in {"tool_use", "tool_call", "toolcall"} and node.get("name") == "Skill":
                args = node.get("input") or node.get("arguments") or {}
                skill_name = args.get("skill") if isinstance(args, dict) else None
                call_id = node.get("id")
                if isinstance(skill_name, str):
                    uses.append({"skill": skill_name, "id": call_id if isinstance(call_id, str) else None})
            elif node.get("type") == "tool_result":
                call_id = node.get("tool_use_id")
                if isinstance(call_id, str):
                    results[call_id] = node.get("is_error") is not True

    events = [
        {
            "tool": "Skill",
            "skill": use["skill"],
            "result": "success" if use["id"] in results and results[use["id"]] else (
                "error" if use["id"] in results else "missing"
            ),
        }
        for use in uses
    ]
    return events, bool(records)


def matches_shopping_reference(args: Any, skill_copy: Path) -> bool:
    source = str(skill_copy.resolve()).replace("\\", "/").casefold()
    for value in string_values(args):
        candidate = value.replace("\\", "/").casefold()
        if re.search(r"(?:^|[/ :])skill://shopping(?:/skill\.md)?(?:$|[?# ])", candidate):
            return True
        normalized_candidate = candidate.rstrip("/")
        if normalized_candidate in {source, f"{source}/skill.md"}:
            return True
        if re.search(r"(?:^|/)skills/shopping(?:/skill\.md)?(?:$|[?#\s\"'])", candidate):
            return True
    return False


def read_skill_events(records: list[dict[str, Any]], skill_copy: Path) -> tuple[list[dict[str, Any]], bool]:
    starts: dict[str, dict[str, Any]] = {}
    finishes: dict[str, dict[str, Any]] = {}
    structured = bool(records)
    for record in records:
        for node in walk_dicts(record):
            event_type = node.get("type")
            if event_type == "tool_execution_start" and node.get("toolName") == "read":
                call_id = node.get("toolCallId")
                if isinstance(call_id, str) and matches_shopping_reference(node.get("args", {}), skill_copy):
                    starts[call_id] = node
            elif event_type == "tool_execution_end" and node.get("toolName") == "read":
                call_id = node.get("toolCallId")
                if isinstance(call_id, str):
                    finishes[call_id] = node

    events: list[dict[str, Any]] = []
    for call_id, start in starts.items():
        end = finishes.get(call_id)
        events.append(
            {
                "tool": "read",
                "reference": next(iter(string_values(start.get("args", {}))), ""),
                "completed": end is not None and end.get("isError") is False,
            }
        )
    return events, structured


def copy_skill(destination: Path) -> Path:
    shutil.copytree(SKILL_DIR, destination)
    return destination


def make_omp_config(path: Path, skill_root: Path) -> None:
    # JSON strings are valid YAML scalars and avoid quoting path characters by hand.
    content = (
        "skills:\n"
        "  customDirectories:\n"
        f"    - {json.dumps(str(skill_root.resolve()))}\n"
        "  includeSkills:\n"
        "    - shopping\n"
    )
    path.write_text(content, encoding="utf-8")


def make_claude_plugin(plugin_root: Path) -> Path:
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    skills_root = plugin_root / "skills"
    skills_root.mkdir()
    copy_skill(skills_root / "shopping")
    manifest = {
        "name": CLAUDE_PLUGIN_NAME,
        "version": "0.0.1",
        "description": "Temporary isolated fixture for shopping trigger checks.",
    }
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return skills_root / "shopping"


def command_for(harness: str, binary: str, prompt: str, temp_root: Path) -> tuple[list[str], Path]:
    if harness == "claude":
        plugin_root = temp_root / "claude-plugin"
        skill_copy = make_claude_plugin(plugin_root)
        command = [
            binary,
            "-p",
            "--bare",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--plugin-dir",
            str(plugin_root),
            "--setting-sources",
            "project,local",
            "--system-prompt",
            ROUTER_SYSTEM_PROMPT,
            "--strict-mcp-config",
            "--tools",
            "Skill",
            "--effort",
            "low",
            "--max-budget-usd",
            CLAUDE_MAX_BUDGET_USD,
            prompt,
        ]
        return command, skill_copy

    skill_root = temp_root / f"{harness}-skills"
    skill_root.mkdir()
    skill_copy = copy_skill(skill_root / "shopping")
    if harness == "pi":
        command = [
            binary,
            "--print",
            "--mode",
            "json",
            "--no-session",
            "--no-context-files",
            "--no-extensions",
            "--no-skills",
            "--skill",
            str(skill_copy),
            "--system-prompt",
            ROUTER_SYSTEM_PROMPT,
            "--tools",
            "read",
            "--thinking",
            "off",
            prompt,
        ]
        return command, skill_copy

    config_path = temp_root / "omp-trigger-config.yml"
    make_omp_config(config_path, skill_root)
    command = [
        binary,
        "--print",
        "--mode=json",
        "--no-session",
        "--no-extensions",
        "--no-rules",
        "--tools=read",
        f"--system-prompt={ROUTER_SYSTEM_PROMPT}",
        f"--config={config_path}",
        "--skills=shopping",
        "--thinking=off",
        "--max-time=75s",
        f"--cwd={temp_root}",
        prompt,
    ]
    return command, skill_copy


def activation_evidence(harness: str, output: str, skill_copy: Path) -> tuple[bool | None, list[Any]]:
    records = parse_json_lines(output)
    if not records:
        return None, []

    if harness == "claude":
        events, _ = claude_skill_events(records)
        target = f"{CLAUDE_PLUGIN_NAME}:shopping"
        target_events = [event for event in events if event["skill"] == target]
        if any(event["result"] == "success" for event in target_events):
            return True, events
        if any(event["result"] == "missing" for event in target_events):
            return None, events
        return False, events

    events, _ = read_skill_events(records, skill_copy)
    completed = [event for event in events if event["completed"]]
    return bool(completed), events


def run_case(
    harness: str,
    binary: str,
    case: dict[str, Any],
    temp_root: Path,
    model: str | None,
    timeout: int,
) -> dict[str, Any]:
    command, skill_copy = command_for(harness, binary, case["prompt"], temp_root)
    if model:
        if harness == "claude":
            command[command.index("--max-budget-usd"):command.index("--max-budget-usd")] = ["--model", model]
        else:
            command[1:1] = ["--model", model]

    try:
        environment = os.environ.copy()
        if harness == "claude":
            isolated_home = temp_root / "home"
            isolated_home.mkdir()
            environment["HOME"] = str(isolated_home)
            environment["CLAUDE_CONFIG_DIR"] = str(temp_root / "claude-config")

        completed = subprocess.run(
            command,
            cwd=temp_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "id": case["id"],
            "expected": case["activation"],
            "observed": None,
            "status": "error",
            "reason": f"timed out after {timeout}s",
        }
    except OSError as error:
        return {
            "id": case["id"],
            "expected": case["activation"],
            "observed": None,
            "status": "error",
            "reason": f"could not start harness: {error.__class__.__name__}",
        }

    observed, events = activation_evidence(harness, completed.stdout, skill_copy)
    expected = case["activation"] == "yes"
    if observed == expected and (completed.returncode == 0 or observed is True):
        status = "pass"
        reason = None if completed.returncode == 0 else (
            f"activation was observed before harness exit {completed.returncode}"
        )
    elif completed.returncode != 0:
        status = "error"
        reason = f"harness exited {completed.returncode}"
    elif observed is None:
        status = "unobservable"
        reason = "no structured JSON event stream was found"
    else:
        status = "fail"
        reason = "observed activation did not match the fixture expectation"

    return {
        "id": case["id"],
        "expected": case["activation"],
        "observed": observed,
        "harness_exit_code": completed.returncode,
        "status": status,
        "reason": reason,
        "evidence": events,
        "diagnostic": diagnostic_excerpt(completed.stderr) if status in {"error", "unobservable"} else None,
    }


def cli_version(binary: str) -> str:
    temporary_directory: Any = None
    environment = os.environ.copy()
    if Path(binary).name == "claude":
        temporary_directory = tempfile.TemporaryDirectory(prefix="shopping-trigger-version-")
        isolated_root = Path(temporary_directory.name)
        isolated_home = isolated_root / "home"
        isolated_home.mkdir()
        environment["HOME"] = str(isolated_home)
        environment["CLAUDE_CONFIG_DIR"] = str(isolated_root / "claude-config")
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10, check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()
    if result.returncode != 0:
        return "unknown"
    return (result.stdout or result.stderr).strip().splitlines()[0][:160]


def result_summary(
    harness: str,
    version: str,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    model: str | None,
) -> dict[str, Any]:
    counts = {status: sum(result["status"] == status for result in results)
              for status in ("pass", "fail", "error", "unobservable", "pending")}
    if counts["error"] or counts["unobservable"]:
        status = "incomplete"
    elif counts["fail"]:
        status = "fail"
    else:
        status = "pass"
    return {
        "harness": harness,
        "cli_version": version,
        "runner_version": RUNNER_VERSION,
        "test_system_prompt": "isolated-skill-router-v1",
        "model_override": model,
        "status": status,
        "skill_source_sha256": hashlib.sha256((SKILL_DIR / "SKILL.md").read_bytes()).hexdigest(),
        "case_count": len(cases),
        "counts": counts,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=(*HARNESS_NAMES, "all"), help="harness to run")
    parser.add_argument("--cases", choices=("all", "positive", "negative"), default="all")
    parser.add_argument("--limit", type=int, help="run only the first N selected cases")
    parser.add_argument("--model", help="optional model override supported by the selected harness")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS, help="seconds per prompt (default: %(default)s)")
    parser.add_argument("--output-dir", type=Path, help="write structured results outside the repo")
    parser.add_argument("--validate-only", action="store_true", help="validate the prompt fixture without invoking a model")
    parser.add_argument("--dry-run", action="store_true", help="show the selected case and harness counts only")
    args = parser.parse_args()
    if not args.validate_only and not args.dry_run and not args.harness:
        parser.error("--harness is required unless --validate-only or --dry-run is used")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1 second")
    return args


def main() -> int:
    args = parse_args()
    try:
        cases = load_cases()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"invalid trigger fixture: {error}", file=sys.stderr)
        return 2

    if args.cases == "positive":
        cases = [case for case in cases if case["activation"] == "yes"]
    elif args.cases == "negative":
        cases = [case for case in cases if case["activation"] == "no"]
    if args.limit is not None:
        cases = cases[: args.limit]

    if args.validate_only or args.dry_run:
        print(json.dumps({"status": "valid", "selected_cases": len(cases), "harness": args.harness}, indent=2))
        return 0

    harnesses = HARNESS_NAMES if args.harness == "all" else (args.harness,)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict[str, Any]] = []
    for harness in harnesses:
        binary = shutil.which(harness)
        if not binary:
            summary = result_summary(
                harness,
                "unavailable",
                cases,
                [{"id": case["id"], "expected": case["activation"], "observed": None,
                  "status": "pending", "reason": "harness executable is not installed"} for case in cases],
                args.model,
            )
            summary["status"] = "pending"
        else:
            results = []
            with tempfile.TemporaryDirectory(prefix="shopping-trigger-") as directory:
                run_root = Path(directory)
                for index, case in enumerate(cases):
                    temp_root = run_root / case["id"]
                    temp_root.mkdir()
                    result = run_case(harness, binary, case, temp_root, args.model, args.timeout)
                    results.append(result)
                    if result["status"] in {"error", "unobservable"}:
                        for pending_case in cases[index + 1 :]:
                            results.append(
                                {
                                    "id": pending_case["id"],
                                    "expected": pending_case["activation"],
                                    "observed": None,
                                    "status": "pending",
                                    "reason": "not run after an invocation error or missing event stream",
                                }
                            )
                        break
            summary = result_summary(harness, cli_version(binary), cases, results, args.model)
        all_results.append(summary)
        serialized = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
        if args.output_dir:
            (args.output_dir / f"{harness}.json").write_text(serialized, encoding="utf-8")
        print(serialized, end="")

    return 1 if any(summary["status"] not in {"pass"} for summary in all_results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
