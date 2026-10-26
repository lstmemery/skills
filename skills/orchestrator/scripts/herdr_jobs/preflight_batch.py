"""Assemble legacy managed-job outputs for the existing integration gate.

This is a staging tool, not an alternative readiness or disposition validator.
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile

from . import records
from .review_evidence import (
    AXES, apply_dispositions, parse_review_response, read_disposition_evidence,
)


TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
CAPTURE_CLI = Path(__file__).resolve().parents[3] / "code-review/scripts/capture.py"


def safe_token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value) or value in (".", ".."):
        records.invalid("identity must be a safe single token")
    return value


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        records.invalid(f"git {' '.join(args[:1])} failed")
    return result.stdout.strip()


def prepare(args):
    """Publish a new batch; source evidence and coordinator choices stay intact."""
    try:
        return _prepare(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        records.invalid(f"cannot prepare preflight batch: {error}")


def _prepare(args):
    repo = args.repo.resolve()
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        records.invalid("output must not already exist")
    task_id = safe_token(args.task_id)
    raw_state = records.bounded_file(args.managed_run, "state.json", 67108864)
    state = records.parse_json(raw_state)
    records.version(state["schema_version"])
    jobs = [job for job in state["jobs"] if job["spec"]["job_id"] == args.job_id]
    if len(jobs) != 1:
        records.invalid("expected exactly one author job")
    author = jobs[0]
    if (not author["settled"] or author["pending_effect"] or author["collection_error"]
            or author["collection"]["outcome"] != "complete"
            or author["collection"]["unresolved"]):
        records.invalid("author job needs a settled, complete, unresolved-free collection")
    author_identity = safe_token(author["agent_name"])
    pane = safe_token(author["pane_id"])
    branch, separator, requested_head = args.candidate.partition("=")
    if separator:
        head = git(repo, "rev-parse", "--verify", f"{requested_head}^{{commit}}")
        if git(repo, "rev-parse", "--verify", f"refs/heads/{branch}") != head:
            records.invalid("candidate branch does not match pinned head")
    else:
        head = git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
        branch = head
    target_ref = args.target if args.target.startswith("refs/heads/") else f"refs/heads/{args.target}"
    target = git(repo, "rev-parse", "--verify", target_ref)
    base = git(repo, "merge-base", target, head)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".preflight-batch-", dir=output.parent) as temporary:
        staging = Path(temporary) / "batch"
        task = staging / task_id
        task.mkdir(parents=True)
        roster = [f"{task_id} {author_identity} {pane}\n"]
        sources = []
        reviewed_head = base
        response = ["# Review response\n\n", "Coordinator: record a disposition for every finding below.\n\n"]
        for index, path in enumerate(args.review_output):
            source = path if (path / "review-evidence.json").is_file() else path / "review"
            name = "review" if index == 0 else f"review-r{index}"
            if source.is_symlink():
                records.invalid("review source must not be a symlink")
            document = read_disposition_evidence(source)
            evidence = records.parse_json(document.original)
            if str(evidence["task_id"]) != task_id or evidence["author_identity"] != author_identity:
                records.invalid("review task or author identity disagrees with author job")
            reviewer = safe_token(evidence["reviewer_identity"])
            if reviewer == author_identity:
                records.invalid("reviewer is the candidate author")
            destination = task / name
            destination.mkdir()
            records.atomic_bytes(destination / "review-evidence.json", document.original)
            records.atomic_bytes(destination / "review.md", records.bounded_file(source, "review.md"))
            size = 0
            for entry in (source / "capture", *(source / "capture").rglob("*")):
                if entry.is_symlink():
                    records.invalid("capture must not contain a symlink")
                if entry.is_dir():
                    continue
                relative = str(entry.relative_to(source))
                data = records.bounded_file(source, relative)
                size += len(data)
                if size > 67108864:
                    records.invalid("capture exceeds 64 MiB")
                records.atomic_bytes(destination / relative, data)
            verification = subprocess.run(
                [sys.executable, str(CAPTURE_CLI), "verify", "--capture", str(destination / "capture")],
                capture_output=True, text=True, timeout=60,
            )
            if verification.returncode:
                records.invalid(f"{name} capture payload verification failed")
            verified = records.parse_json(verification.stdout.encode())
            if (verified["outcome"] != "verified" or verified["coverage"] != "complete"
                    or verified["gaps"] or verified["capture_id"] != evidence["capture_id"]):
                records.invalid(f"{name} capture does not match complete review evidence")
            manifest = records.parse_json(records.read_regular(destination / "capture/manifest.json"))
            if (manifest["base"] not in (base, reviewed_head)
                    or git(repo, "merge-base", reviewed_head, manifest["head"]) != reviewed_head):
                records.invalid(f"{name} capture is out of round order or has a coverage gap")
            reviewed_head = manifest["head"]
            for axis in AXES:
                for finding in evidence["axes"][axis]["findings"]:
                    response.append(f"<!-- `{name}/{finding['id']}`: choose disposition and rationale -->\n")
            roster.append(f"{task_id} {reviewer}\n")
            sources.append({"round": name, "directory": str(source.resolve())})
        if reviewed_head != head:
            records.invalid("review chain does not reach candidate head")
        result = {"task_id": task_id, "outcome": "ready", "candidate": {
            "repo": str(repo), "branch": branch, "base": base, "head": head,
        }}
        records.atomic_bytes(task / "result.json", records.encoded(result) + b"\n")
        response_raw = (records.read_regular(args.review_response) if args.review_response
                        else "".join(response).encode())
        records.atomic_bytes(task / "review-response.md", response_raw)
        if args.review_response:
            documents = [read_disposition_evidence(task / source["round"]) for source in sources]
            decisions = parse_review_response(response_raw.decode(), reject_malformed=True)
            for update in apply_dispositions(documents, decisions):
                records.atomic_bytes(update.path, update.replacement)
        for index, source in enumerate(sources):
            directory = task / source["round"]
            evidence = records.parse_json(records.read_regular(directory / "review-evidence.json"))
            counts = {axis: len(evidence["axes"][axis]["findings"]) for axis in AXES}
            done = {"task_id": task_id, "capture_id": evidence["capture_id"]}
            if index == 0:
                done.update(standards_findings=counts["standards"], spec_findings=counts["spec"])
            else:
                done.update(new_findings=sum(counts.values()), unfixed=sum(
                    finding["disposition"] not in ("fixed", "rejected")
                    for axis in AXES for finding in evidence["axes"][axis]["findings"]
                ))
            records.atomic_bytes(directory / "done.json", records.encoded(done) + b"\n")
        records.atomic_bytes(staging / "workers.txt", "".join(roster).encode())
        records.atomic_bytes(staging / "assembly.json", records.encoded({
            "schema_version": 1, "managed_run": str(args.managed_run.resolve()),
            "state_sha256": records.digest(raw_state), "author_job_id": args.job_id,
            "reviews": sources,
        }) + b"\n")
        staging.rename(output)
    return {"outcome": "prepared", "batch": str(output), "task_dir": str(output / task_id)}
