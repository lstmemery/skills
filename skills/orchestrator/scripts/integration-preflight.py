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

# Standalone script importing its sibling herdr_jobs package; inserting the
# script directory keeps the import working both when run directly (python3
# scripts/integration-preflight.py) and when loaded via importlib (tests).
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from herdr_jobs import review_evidence
from herdr_jobs.managed_preflight import materialize_runs
# Keep historical parser imports available to callers; the domain owns their rules.
from herdr_jobs.review_evidence import (
    AXES,
    EVIDENCE_NAME,
    FINDING_EVIDENCE_FIELDS,
    REVIEW_EVIDENCE_SCHEMA_VERSIONS,
    ReviewEvidenceError as PreflightError,
    VerifiedCapture,
    finding_evidence_error,
    parse_review_markdown,
    parse_review_response,
    parse_worker_identities,
)


CAPTURE_VERIFIER = Path(__file__).resolve().parents[2] / "code-review" / "scripts" / "capture.py"
CAPTURE_VERIFICATION_KEYS = frozenset({"outcome", "capture_id", "coverage", "gaps"})
SHA = re.compile(r"^[0-9a-f]{40,64}$")

# Version-1 captures have no trustworthy creation timestamp. The sole historical
# prose exception is therefore pinned to the exact review records selected by
# the previously-ready batch9 preflight below. Capture payloads are still
# verified at use time, and every record field below must continue to match.
LEGACY_PRECEDENT = {
    "report": "preflight-9706b575d1279316.json",
    "sha256": "9706b575d12793163f61b5120fcd728b74ee8a2b73961fa1b341b1cc272ea8a7",
}
# These fields occur in both the observed review pin and its precedent report
# entry. Keep their comparison inventory in one place; only the observed pin
# additionally records per-axis finding IDs.
LEGACY_REVIEW_SHARED_FIELDS = (
    "review_id",
    "base",
    "head",
    "capture_id",
    "author_identity",
    "reviewer_identity",
    "review_markdown_sha256",
    "review_evidence_sha256",
    "done_sha256",
    "capture_manifest_sha256",
    "capture_complete_sha256",
)
LEGACY_REVIEW_RECORD_FIELDS = (*LEGACY_REVIEW_SHARED_FIELDS, "finding_ids")
LEGACY_PRECHANGE_REVIEWS = {
    "1131": {
        "review_id": "review",
        "base": "f499c221c811a52d38c0c7fce7867e7fad6a1b66",
        "head": "a04abcb8648feff4388f4ea01911467d99243913",
        "capture_id": "15f9dd10725129432a9127ef0ae1971a959b01f6813f0d770dc814710a1f72b4",
        "author_identity": "luna-b9-1131",
        "reviewer_identity": "luna-rev-b9-1131",
        "review_markdown_sha256": "cc9216db19a54acadcfce4d0e851047e19480136e09beb702f4cdca45977119c",
        "review_evidence_sha256": "c045a1646f6718078cc312f94f67bf8c8fd4d62913741a211cdd1a8a538c67a4",
        "done_sha256": "ad654a039db2767b0fce6ad79768c22a2beb2838154f3f9754487a1bb86517c4",
        "capture_manifest_sha256": "4ec8a769b4612e4165f2f1dd7f03005b9ec6953c90585286de14ac283aec8e90",
        "capture_complete_sha256": "43f71027bb8fb28eb545689dea27963b1b6dcaa264f47b0d87fdd540a20b2b94",
        "finding_ids": {"standards": [], "spec": []},
    },
    "1132": {
        "review_id": "review",
        "base": "f499c221c811a52d38c0c7fce7867e7fad6a1b66",
        "head": "c833ae969f8c25ba37b29a5237e0b38912dbf834",
        "capture_id": "682443f118202ff06f713c5d698c0fca1e46a29644bbed5287b583e09e9aa7f0",
        "author_identity": "luna-b9-1132",
        "reviewer_identity": "luna-rev-b9-1132",
        "review_markdown_sha256": "5873e5c51498ca38c0ca2e20031e1c1b090cb6d8fa17b45cc3807d03e560daac",
        "review_evidence_sha256": "3f47fb960d0e5f0ef90905c03ce3d7e13e131c7a6df948ed38ffb4966a2ac19d",
        "done_sha256": "c349a36c9afa7950a2a3401e649bb9a11ef7bc5feafe17836a88e63379c036ab",
        "capture_manifest_sha256": "90afac994db11157ee7aed6385f22e58d86e2f999597ee756ad0e37b0118931b",
        "capture_complete_sha256": "d02487e46e59f520ca8489a8994c47b69fc829440214da8ab06c889c019ed9aa",
        "finding_ids": {"standards": [], "spec": []},
    },
    "1133": {
        "review_id": "review",
        "base": "f499c221c811a52d38c0c7fce7867e7fad6a1b66",
        "head": "a608df624400f542e774889dd31f399147b7600a",
        "capture_id": "33fe037532b26ed7dfb6f7b904767b34b4d73df6e79744bcae71df6a8ca28b94",
        "author_identity": "luna-b9-1133",
        "reviewer_identity": "luna-rev-b9-1133",
        "review_markdown_sha256": "0cdbf111492255b753e675dd5fe9949cd84f5538c53ec5713f162551c65ba72d",
        "review_evidence_sha256": "3839149ab20f4c77853aa0e84c0a08857e4f4b8aaed6bef74228282e79a3aaa3",
        "done_sha256": "0725d48f9862d63b55f04a3c4e14970a740cdf460c16d646e87447e8417a29ac",
        "capture_manifest_sha256": "19675c5c92980384f398cf5111437e37bceaae536cd2b46eab86e27d7f3196e2",
        "capture_complete_sha256": "2053eea221d58c882a69f5eb01dbd5010e80f69c764b6a1c0cce03b6bde51a45",
        "finding_ids": {"standards": [], "spec": []},
    },
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--target", required=True, help="local target branch")
    parser.add_argument("--run-dir", required=True, type=Path, help="batch run directory")
    parser.add_argument("--managed-run", action="append", default=[], type=Path,
                        help="managed job run to adapt from collected evidence (repeatable)")
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
    verify_capture_payload(review_dir, manifest_path.parent, manifest)
    return {
        "base": base,
        "head": head,
        "capture_id": capture_id,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_file(manifest_path),
        "complete_sha256": sha256_file(complete_path),
    }


def verify_capture_payload(review_dir, capture_dir, manifest):
    if not CAPTURE_VERIFIER.is_file():
        raise PreflightError(f"{review_dir.name} capture verifier is unavailable")
    try:
        result = subprocess.run(
            [sys.executable, str(CAPTURE_VERIFIER), "verify", "--capture", str(capture_dir)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PreflightError(f"{review_dir.name} capture payload verification failed") from error
    try:
        verification = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise PreflightError(f"{review_dir.name} capture verifier returned invalid output") from error
    if not isinstance(verification, dict):
        raise PreflightError(f"{review_dir.name} capture verifier returned a non-object result")
    if result.returncode != 0:
        raise PreflightError(
            f"{review_dir.name} capture payload verification failed (exit {result.returncode})"
        )
    missing = CAPTURE_VERIFICATION_KEYS - verification.keys()
    if missing:
        raise PreflightError(
            f"{review_dir.name} capture verifier is missing required keys: "
            + ", ".join(sorted(missing))
        )
    if (
        verification["outcome"] != "verified"
        or verification["capture_id"] != manifest["capture_id"]
        or verification["coverage"] != "complete"
        or verification["gaps"] != []
    ):
        raise PreflightError(f"{review_dir.name} capture verifier did not verify complete matching coverage")


def legacy_review_record_matches(task_id, observed):
    expected = LEGACY_PRECHANGE_REVIEWS.get(str(task_id))
    fields = set(LEGACY_REVIEW_RECORD_FIELDS)
    return (
        expected is not None
        and set(expected) == fields
        and set(observed) == fields
        and all(observed[field] == expected[field] for field in LEGACY_REVIEW_RECORD_FIELDS)
    )


def legacy_review_precedent(task_id, observed, run_dir):
    """Return the precedent digest only for exact records in the ready batch."""
    if not legacy_review_record_matches(task_id, observed):
        return None

    precedent_path = run_dir / "integration-preflight" / LEGACY_PRECEDENT["report"]
    try:
        if sha256_file(precedent_path) != LEGACY_PRECEDENT["sha256"]:
            return None
        report = load_json(precedent_path, "legacy preflight report")
    except (OSError, PreflightError):
        return None
    if report.get("outcome") != "ready":
        return None
    matching_candidates = [
        candidate
        for candidate in report.get("candidates", [])
        if isinstance(candidate, dict)
        and str(candidate.get("task_id")) == str(task_id)
        and candidate.get("status") == "ready"
    ]
    if len(matching_candidates) != 1:
        return None
    reviews = matching_candidates[0].get("reviews")
    if not isinstance(reviews, list):
        return None
    expected = LEGACY_PRECHANGE_REVIEWS[str(task_id)]
    matching_reviews = [
        review
        for review in reviews
        if isinstance(review, dict)
        and all(
            review.get(field) == expected[field]
            for field in LEGACY_REVIEW_SHARED_FIELDS
        )
        and review.get("review_path") == expected["review_id"]
        and review.get("finding_count") == 0
    ]
    if len(matching_reviews) != 1:
        return None
    return LEGACY_PRECEDENT["sha256"]


class SubprocessCaptureVerifier:
    """Adapt the existing completed-capture lookup and subprocess verifier."""

    def verify(self, review_dir, capture_id):
        capture = find_capture(review_dir, capture_id)
        return VerifiedCapture(
            base=capture["base"], head=capture["head"], capture_id=capture["capture_id"],
            manifest_sha256=capture["manifest_sha256"], complete_sha256=capture["complete_sha256"],
        )


def review_records(task_dir, task_id, workers_path):
    return review_evidence.read_review_records(
        task_dir, task_id, workers_path,
        capture_verifier=SubprocessCaptureVerifier(),
        legacy_precedent=lambda observed: legacy_review_precedent(
            task_id, observed, workers_path.parent
        ),
    )


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
        review_evidence.validate_chain_authors(chain)
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
        input_dir = run_dir
        managed_jobs = None
        if args.managed_run:
            input_dir, managed_jobs = materialize_runs(args.managed_run, run_dir)
        workers_path = input_dir / "workers.txt"
        if len(set(args.candidate)) != len(args.candidate):
            raise PreflightError("candidate arguments must be unique")
        deferred = parse_deferred(args.defer)
        discovered_tasks = discover_candidate_tasks(input_dir, workers_path)
        candidates = [
            candidate_report(repo, input_dir, workers_path, target_head, spec)
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
        if managed_jobs is not None:
            report.update(managed_jobs=managed_jobs, managed_input_dir=str(input_dir))
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
