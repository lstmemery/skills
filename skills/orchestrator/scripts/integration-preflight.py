#!/usr/bin/env python3
"""Validate independent review evidence before integrating candidate branches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


AXES = ("standards", "spec")
EVIDENCE_NAME = "review-evidence.json"
FINDING_LINE = re.compile(
    r"^\s*-\s*`(?P<review>[^`]+)/(?P<finding>[^`]+)`\s*:\s*"
    r"(?P<disposition>fixed|rejected)\s+—\s+(?P<detail>\S.*)$"
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")
SHA = re.compile(r"^[0-9a-f]{40,64}$")


class PreflightError(ValueError):
    """An input or evidence record cannot be checked safely."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--target", required=True, help="local target branch")
    parser.add_argument("--run-dir", required=True, type=Path, help="batch run directory")
    parser.add_argument(
        "--candidate",
        action="append",
        required=True,
        help="candidate branch, commit SHA, or BRANCH=REV to pin both",
    )
    parser.add_argument(
        "--defer",
        action="append",
        default=[],
        metavar="TASK[:REASON]",
        help="explicitly defer one discovered task from this integration batch",
    )
    return parser.parse_args(argv)


def git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        raise PreflightError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def resolve_commit(repo, revision):
    if not revision or revision.startswith("-"):
        raise PreflightError(f"invalid git revision {revision!r}")
    return git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").stdout.strip()


def resolve_candidate(repo, spec):
    if "=" in spec:
        branch, revision = spec.split("=", 1)
        if not branch or not revision:
            raise PreflightError(f"invalid candidate {spec!r}; expected BRANCH=REV")
        branch_ref = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
        branch_head = resolve_commit(repo, branch_ref)
        revision_head = resolve_commit(repo, revision)
        if branch_head != revision_head:
            raise PreflightError(f"candidate branch {branch} no longer points to {revision_head}")
        return branch.removeprefix("refs/heads/"), revision_head

    local_ref = spec if spec.startswith("refs/heads/") else f"refs/heads/{spec}"
    local_branch = git(repo, "show-ref", "--verify", "--quiet", local_ref, check=False)
    if local_branch.returncode == 0:
        return local_ref.removeprefix("refs/heads/"), resolve_commit(repo, local_ref)
    return None, resolve_commit(repo, spec)


def load_json(path, description):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PreflightError(f"missing {description}: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise PreflightError(f"cannot read {description}: {path}") from error
    if not isinstance(value, dict):
        raise PreflightError(f"{description} must be a JSON object: {path}")
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def task_result(run_dir, branch, head):
    matches = []
    for result_path in sorted(run_dir.glob("*/result.json")):
        try:
            result = load_json(result_path, "worker result")
        except PreflightError:
            continue
        candidate = result.get("candidate")
        if not isinstance(candidate, dict) or candidate.get("head") != head:
            continue
        if branch is not None and candidate.get("branch") != branch:
            continue
        matches.append((result_path.parent, result_path, result))
    if len(matches) != 1:
        raise PreflightError(
            f"expected one worker result for candidate head {head}, found {len(matches)}"
        )
    return matches[0]


def parse_worker_identities(path, task_id):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise PreflightError(f"missing workers.txt: {path}") from error
    except OSError as error:
        raise PreflightError(f"cannot read workers.txt: {path}") from error

    identities = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[0] == str(task_id) and fields[1] not in identities:
            identities.append(fields[1])
    if not identities:
        raise PreflightError(f"workers.txt has no roster entry for task {task_id}")
    return identities


def discover_candidate_tasks(run_dir, workers_path):
    task_ids = set()
    for result_path in sorted(run_dir.glob("*/result.json")):
        try:
            result = load_json(result_path, "worker result")
        except PreflightError as error:
            raise PreflightError(
                f"cannot discover batch candidates from {result_path}: {error}"
            ) from error
        if isinstance(result.get("candidate"), dict):
            task_id = result.get("task_id")
            if (
                not isinstance(task_id, (str, int))
                or isinstance(task_id, bool)
                or not str(task_id)
            ):
                raise PreflightError(f"candidate result has no valid task_id: {result_path}")
            task_ids.add(str(task_id))

    try:
        worker_lines = workers_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        worker_lines = []
    except OSError as error:
        raise PreflightError(f"cannot read workers.txt: {workers_path}") from error
    for line in worker_lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1] != "diffpane" and fields[0]:
            task_ids.add(fields[0])
    return task_ids


def parse_deferred(specs):
    deferred = {}
    for spec in specs:
        task_id, separator, reason = spec.partition(":")
        task_id = task_id.strip()
        reason = reason.strip() if separator else ""
        if not task_id:
            raise PreflightError("--defer needs a task ID")
        if separator and not reason:
            raise PreflightError(f"--defer {task_id}: needs a non-empty reason")
        if task_id in deferred:
            raise PreflightError(f"task {task_id} is deferred more than once")
        deferred[task_id] = reason
    return deferred


def find_capture(review_dir, capture_id):
    matches = []
    for manifest_path in review_dir.rglob("manifest.json"):
        relative_parts = manifest_path.relative_to(review_dir).parts
        if not any(part == "capture" or part.startswith("capture-") for part in relative_parts[:-1]):
            continue
        complete_path = manifest_path.parent / "COMPLETE"
        if not complete_path.is_file():
            continue
        try:
            manifest = load_json(manifest_path, "capture manifest")
        except PreflightError:
            continue
        if manifest.get("capture_id") == capture_id:
            matches.append((manifest_path, complete_path, manifest))
    if len(matches) != 1:
        raise PreflightError(
            f"{review_dir.name} capture {capture_id!r} must identify one completed capture; "
            f"found {len(matches)}"
        )
    manifest_path, complete_path, manifest = matches[0]
    if not complete_path.read_text(encoding="utf-8").strip():
        raise PreflightError(f"{review_dir.name} capture COMPLETE marker is empty")
    if manifest.get("coverage") != "complete" or manifest.get("gaps") != []:
        raise PreflightError(f"{review_dir.name} capture coverage is incomplete")
    base = manifest.get("base")
    head = manifest.get("head")
    if not isinstance(base, str) or not SHA.fullmatch(base):
        raise PreflightError(f"{review_dir.name} capture has an invalid base SHA")
    if not isinstance(head, str) or not SHA.fullmatch(head):
        raise PreflightError(f"{review_dir.name} capture has an invalid head SHA")
    return {
        "base": base,
        "head": head,
        "capture_id": capture_id,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_file(manifest_path),
        "complete_sha256": sha256_file(complete_path),
    }


def review_markdown_has_axes(review_dir):
    path = review_dir / "review.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise PreflightError(f"missing review.md in {review_dir.name}") from error
    except OSError as error:
        raise PreflightError(f"cannot read review.md in {review_dir.name}") from error
    for axis in AXES:
        if re.search(rf"^##\s+{axis}\s*$", text, re.IGNORECASE | re.MULTILINE) is None:
            raise PreflightError(f"{review_dir.name}/review.md lacks a {axis.title()} section")
    return path


def review_records(task_dir, task_id, workers_path):
    response_path = task_dir / "review-response.md"
    try:
        response_text = response_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        response_text = ""
    except OSError as error:
        raise PreflightError(f"cannot read review-response.md: {response_path}") from error

    dispositions = {}
    for line in response_text.splitlines():
        match = FINDING_LINE.match(line)
        if match is None:
            continue
        key = (match.group("review"), match.group("finding"))
        if key in dispositions:
            raise PreflightError(f"duplicate disposition for {key[0]}/{key[1]}")
        dispositions[key] = match.group("disposition")

    worker_ids = parse_worker_identities(workers_path, task_id)
    records = []
    diagnostics = []
    for review_dir in sorted(path for path in task_dir.iterdir() if path.is_dir() and (path.name == "review" or path.name.startswith("review-"))):
        evidence_path = review_dir / EVIDENCE_NAME
        if not evidence_path.is_file():
            if (review_dir / "review.md").is_file() or (review_dir / "done.json").is_file():
                diagnostics.append(f"{review_dir.name} lacks {EVIDENCE_NAME}")
            continue
        try:
            evidence = load_json(evidence_path, "review evidence")
            if evidence.keys() != {
                "schema_version",
                "task_id",
                "author_identity",
                "reviewer_identity",
                "capture_id",
                "axes",
            }:
                raise PreflightError(f"{review_dir.name} review evidence has unexpected fields")
            if (
                type(evidence["schema_version"]) is not int
                or evidence["schema_version"] != 1
                or str(evidence["task_id"]) != str(task_id)
            ):
                raise PreflightError(f"{review_dir.name} review evidence has the wrong schema or task ID")
            author = evidence["author_identity"]
            reviewer = evidence["reviewer_identity"]
            if not isinstance(author, str) or not author or not isinstance(reviewer, str) or not reviewer:
                raise PreflightError(f"{review_dir.name} identities must be non-empty strings")
            if author == reviewer:
                raise PreflightError(f"{review_dir.name} reviewer is the candidate author")
            if author not in worker_ids or reviewer not in worker_ids:
                raise PreflightError(f"{review_dir.name} identities are not both listed in workers.txt")
            if author != worker_ids[0]:
                raise PreflightError(
                    f"{review_dir.name} author identity does not match the first task worker in workers.txt"
                )
            capture_id = evidence["capture_id"]
            if not isinstance(capture_id, str) or not capture_id:
                raise PreflightError(f"{review_dir.name} has no capture ID")
            capture = find_capture(review_dir, capture_id)
            done_path = review_dir / "done.json"
            done = load_json(done_path, "review done record")
            if str(done.get("task_id")) != str(task_id) or done.get("capture_id") != capture_id:
                raise PreflightError(f"{review_dir.name}/done.json does not match its task and capture")
            review_markdown = review_markdown_has_axes(review_dir)
            axes = evidence["axes"]
            if not isinstance(axes, dict) or set(axes) != set(AXES):
                raise PreflightError(f"{review_dir.name} must record both Standards and Spec axes")
            finding_ids = set()
            finding_count = 0
            for axis in AXES:
                axis_record = axes[axis]
                if not isinstance(axis_record, dict) or set(axis_record) != {"status", "findings"}:
                    raise PreflightError(f"{review_dir.name} {axis} record is malformed")
                findings = axis_record["findings"]
                if axis_record["status"] != "complete" or not isinstance(findings, list):
                    raise PreflightError(f"{review_dir.name} {axis} review is incomplete")
                for finding in findings:
                    if not isinstance(finding, dict) or set(finding) - {"id", "disposition", "reason"} or not {"id", "disposition"} <= set(finding):
                        raise PreflightError(f"{review_dir.name} has a malformed finding")
                    finding_id = finding["id"]
                    disposition = finding["disposition"]
                    if not isinstance(finding_id, str) or not IDENTIFIER.fullmatch(finding_id):
                        raise PreflightError(f"{review_dir.name} has an invalid finding ID")
                    if finding_id in finding_ids:
                        raise PreflightError(f"{review_dir.name} repeats finding ID {finding_id}")
                    finding_ids.add(finding_id)
                    if disposition not in {"fixed", "rejected"}:
                        raise PreflightError(f"{review_dir.name}/{finding_id} has no terminal disposition")
                    reason = finding.get("reason", "")
                    if not isinstance(reason, str) or (disposition == "rejected" and not reason.strip()):
                        raise PreflightError(f"{review_dir.name}/{finding_id} needs a rejection reason")
                    if dispositions.get((review_dir.name, finding_id)) != disposition:
                        raise PreflightError(f"missing recorded disposition for {review_dir.name}/{finding_id}")
                    finding_count += 1
            if review_dir.name == "review":
                standards_findings = done["standards_findings"]
                spec_findings = done["spec_findings"]
                if (
                    type(standards_findings) is not int
                    or standards_findings != len(axes["standards"]["findings"])
                ):
                    raise PreflightError(f"{review_dir.name} Standards count disagrees with done.json")
                if (
                    type(spec_findings) is not int
                    or spec_findings != len(axes["spec"]["findings"])
                ):
                    raise PreflightError(f"{review_dir.name} Spec count disagrees with done.json")
            else:
                new_findings = done["new_findings"]
                unfixed = done["unfixed"]
                if type(new_findings) is not int or new_findings != finding_count:
                    raise PreflightError(f"{review_dir.name} finding count disagrees with done.json")
                if type(unfixed) is not int or unfixed != 0:
                    raise PreflightError(f"{review_dir.name} reports unfixed review findings")
            records.append(
                {
                    "review_id": review_dir.name,
                    "author_identity": author,
                    "reviewer_identity": reviewer,
                    "base": capture["base"],
                    "head": capture["head"],
                    "capture_id": capture_id,
                    "review_path": str(review_dir.relative_to(task_dir)),
                    "review_evidence_sha256": sha256_file(evidence_path),
                    "done_sha256": sha256_file(done_path),
                    "review_markdown_sha256": sha256_file(review_markdown),
                    "capture_manifest_sha256": capture["manifest_sha256"],
                    "capture_complete_sha256": capture["complete_sha256"],
                    "finding_count": finding_count,
                }
            )
        except (KeyError, OSError, PreflightError, TypeError) as error:
            diagnostics.append(f"{review_dir.name}: {error}")
    return records, diagnostics


def is_ancestor(repo, ancestor, descendant):
    result = git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    return result.returncode == 0


def commit_distance(repo, base, head):
    result = git(repo, "rev-list", "--count", f"{base}..{head}")
    return int(result.stdout.strip())


def select_review_chain(repo, expected_base, candidate_head, records):
    current = expected_base
    selected = []
    remaining = list(records)
    while current != candidate_head:
        choices = [
            record
            for record in remaining
            if record["base"] == current
            and is_ancestor(repo, record["head"], candidate_head)
            and record["head"] != current
        ]
        if not choices:
            raise PreflightError(
                f"no complete review capture chain covers {expected_base}..{candidate_head}"
            )
        farthest = max(commit_distance(repo, current, record["head"]) for record in choices)
        chosen = [
            record
            for record in choices
            if commit_distance(repo, current, record["head"]) == farthest
        ]
        next_head = chosen[0]["head"]
        if any(record["head"] != next_head for record in chosen):
            raise PreflightError("review captures do not form a single candidate ancestry")
        selected.extend(chosen)
        remaining = [record for record in remaining if record not in chosen]
        current = next_head
    return selected


def candidate_report(repo, run_dir, workers_path, target_head, spec):
    report = {"requested": spec, "status": "blocked", "issues": []}
    try:
        branch, head = resolve_candidate(repo, spec)
        report["head"] = head
        if branch is not None:
            report["branch"] = branch
        task_dir, result_path, result = task_result(run_dir, branch, head)
        result_candidate = result["candidate"]
        task_id = result["task_id"]
        report["task_id"] = str(task_id)
        report["result_sha256"] = sha256_file(result_path)
        if result.get("outcome") != "ready" or result.get("unresolved"):
            raise PreflightError("worker result is not ready or has unresolved items")
        if result_candidate.get("head") != head:
            raise PreflightError("worker result head does not match the candidate ref")
        if branch is not None and result_candidate.get("branch") != branch:
            raise PreflightError("worker result branch does not match the requested branch")
        merge_base = git(repo, "merge-base", target_head, head).stdout.strip()
        report["base"] = merge_base
        if result_candidate.get("base") != merge_base:
            raise PreflightError(
                f"worker base {result_candidate.get('base')} differs from target merge base {merge_base}"
            )
        if is_ancestor(repo, head, target_head):
            raise PreflightError("candidate is already reachable from the target branch")
        records, diagnostics = review_records(task_dir, task_id, workers_path)
        response_path = task_dir / "review-response.md"
        report["review_response_sha256"] = (
            sha256_file(response_path) if response_path.is_file() else None
        )
        if not records:
            detail = "; ".join(diagnostics) if diagnostics else "no review directories found"
            raise PreflightError(f"missing independent review evidence ({detail})")
        try:
            chain = select_review_chain(repo, merge_base, head, records)
        except PreflightError as error:
            if diagnostics:
                detail = "; ".join(diagnostics)
                raise PreflightError(f"{error} ({detail})") from error
            raise
        authors = {record["author_identity"] for record in chain}
        if len(authors) != 1:
            raise PreflightError("review records disagree about the candidate author identity")
        report.update(
            {
                "status": "ready",
                "task_id": str(task_id),
                "branch": result_candidate["branch"],
                "base": merge_base,
                "head": head,
                "workers_sha256": sha256_file(workers_path),
                "reviews": chain,
            }
        )
        if diagnostics:
            report["ignored_review_records"] = diagnostics
    except (KeyError, OSError, PreflightError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        report["issues"].append(str(error))
    return report


def block_candidate(report, reason):
    report["status"] = "blocked"
    if reason not in report["issues"]:
        report["issues"].append(reason)


def write_report(run_dir, report):
    data = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    payload = data.encode("utf-8")
    report_hash = hashlib.sha256(payload).hexdigest()
    output_dir = run_dir / "integration-preflight"
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_path = output_dir / f"preflight-{report_hash[:16]}.json"
    if output_path.exists():
        if output_path.read_bytes() != payload:
            raise PreflightError(f"refusing to overwrite conflicting evidence: {output_path}")
        return output_path
    descriptor, temporary_name = tempfile.mkstemp(prefix=".preflight-", dir=output_dir)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output_path)
        directory_fd = os.open(output_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return output_path


def main(argv=None):
    args = parse_args(argv)
    repo = args.repo.resolve()
    run_dir = args.run_dir.resolve()
    if not repo.is_dir() or not run_dir.is_dir():
        print("repo and run directory must exist", file=sys.stderr)
        return 2
    target_ref = args.target if args.target.startswith("refs/heads/") else f"refs/heads/{args.target}"
    try:
        top = git(repo, "rev-parse", "--show-toplevel").stdout.strip()
        if Path(top).resolve() != repo:
            raise PreflightError("--repo must name the git working tree root")
        target_head = resolve_commit(repo, target_ref)
        workers_path = run_dir / "workers.txt"
        if len(set(args.candidate)) != len(args.candidate):
            raise PreflightError("candidate arguments must be unique")
        deferred = parse_deferred(args.defer)
        discovered_tasks = discover_candidate_tasks(run_dir, workers_path)
        candidates = [
            candidate_report(repo, run_dir, workers_path, target_head, spec)
            for spec in args.candidate
        ]
        selected_task_ids = [
            candidate["task_id"] for candidate in candidates if "task_id" in candidate
        ]
        selected_tasks = set(selected_task_ids)
        batch_issues = []
        if len(selected_task_ids) != len(selected_tasks):
            batch_issues.append("more than one --candidate resolves to the same task")
        conflicts = sorted(selected_tasks.intersection(deferred))
        if conflicts:
            batch_issues.append(f"tasks are both candidates and deferred: {', '.join(conflicts)}")
        unknown_deferred = sorted(set(deferred) - discovered_tasks)
        if unknown_deferred:
            batch_issues.append(f"--defer references unknown tasks: {', '.join(unknown_deferred)}")
        unaccounted = sorted(discovered_tasks - selected_tasks - set(deferred))
        if unaccounted:
            batch_issues.append(f"unaccounted coding candidates: {', '.join(unaccounted)}")
        for candidate, spec in zip(candidates, args.candidate):
            if candidate.get("branch") is None or candidate.get("head") is None:
                continue
            try:
                branch_after, head_after = resolve_candidate(repo, spec)
            except PreflightError as error:
                block_candidate(candidate, f"candidate ref could not be rechecked: {error}")
                continue
            if head_after != candidate["head"] or (
                branch_after is not None and branch_after != candidate["branch"]
            ):
                block_candidate(candidate, "candidate branch moved during preflight")
        target_head_after = resolve_commit(repo, target_ref)
        if target_head_after != target_head:
            for candidate in candidates:
                block_candidate(candidate, "target branch moved during preflight")
        outcome = (
            "ready"
            if not batch_issues and all(candidate["status"] == "ready" for candidate in candidates)
            else "blocked"
        )
        report = {
            "schema_version": 1,
            "outcome": outcome,
            "repo": str(repo),
            "target_branch": args.target,
            "target_head": target_head,
            "target_head_after_check": target_head_after,
            "candidates": candidates,
            "deferred": [
                {"task_id": task_id, "reason": reason}
                for task_id, reason in sorted(deferred.items())
            ],
            "batch_issues": batch_issues,
        }
        evidence_path = write_report(run_dir, report)
        output = {"outcome": outcome, "evidence": str(evidence_path)}
        if outcome == "blocked":
            output["issues"] = [
                {"candidate": candidate["requested"], "issues": candidate["issues"]}
                for candidate in candidates
                if candidate["status"] != "ready"
            ]
            if batch_issues:
                output["issues"].append({"batch": "accounting", "issues": batch_issues})
        print(json.dumps(output, sort_keys=True))
        return 0 if outcome == "ready" else 1
    except (OSError, PreflightError, subprocess.CalledProcessError) as error:
        print(f"integration preflight error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
