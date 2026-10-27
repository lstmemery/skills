"""Assemble legacy managed-job outputs for the existing integration gate.

This is a staging tool, not an alternative readiness or disposition validator.
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile

from . import records, review_dispositions
from .review_evidence import AXES, read_disposition_evidence


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


def select_job(state, job_id):
    jobs = [job for job in state["jobs"] if job["spec"]["job_id"] == job_id]
    if len(jobs) != 1:
        records.invalid(f"expected exactly one managed job: {job_id}")
    return jobs[0]


def resolve_identity(value, state):
    identities = {job["agent_name"] for job in state["jobs"] if job.get("agent_name")}
    if value in identities:
        return value
    matches = {identity for identity in identities if isinstance(value, str)
               and len(value) >= 16 and identity.startswith(value)}
    if len(matches) != 1:
        records.invalid("sidecar identity is unknown or ambiguous in managed state")
    return matches.pop()


def review_source(job):
    """Select a review directory and its unambiguous declared report root."""
    roots = set()
    if job.get("host_output"):
        roots.add(Path(job["host_output"]))
    spec = job["spec"]
    for value in spec.get("editable_paths", []):
        path = Path(value)
        if not path.is_absolute():
            path = Path(spec["cwd"]) / path
        roots.add(path)
    sources = set()
    for root in roots:
        if root.is_symlink():
            records.invalid("review output root must not be a symlink")
        if not root.is_dir():
            continue
        candidates = [root, root / "review", *root.glob("review-r*")]
        for path in candidates:
            if (path / "review-evidence.json").exists():
                if path.is_symlink():
                    records.invalid("review source must not be a symlink")
                sources.add((path.absolute(), root.absolute()))
    if len(sources) != 1:
        records.invalid(f"review job {spec['job_id']} needs exactly one review source and report root")
    return sources.pop()


def reviewer_verdict(source, evidence, *, required, report_root):
    verdicts = set()
    if (source / "done.json").exists():
        done = records.parse_json(records.bounded_file(source, "done.json"))
        for key in ("task_id", "capture_id"):
            if key in done and done[key] != evidence[key]:
                records.invalid(f"review done.json {key} disagrees with sidecar")
        if "verdict" in done:
            if done["verdict"] not in ("approve", "approve-with-nits", "changes-requested"):
                records.invalid("invalid reviewer verdict")
            verdicts.add(done["verdict"])
    if required:
        for root, filename in ((source, "review.md"), (report_root, "report.md")):
            if (root / filename).exists():
                text = records.bounded_file(root, filename).decode()
                verdicts.update(re.findall(
                    r"^Verdict: (approve|approve-with-nits|changes-requested)\.?[ \t]*$",
                    text, re.MULTILINE,
                ))
    if len(verdicts) > 1 or (required and not verdicts):
        records.invalid("review requires one unambiguous explicit verdict in done.json or report.md")
    return next(iter(verdicts)) if verdicts else None


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
    author = select_job(state, args.job_id)
    if (not author["settled"] or author["pending_effect"] or author["collection_error"]
            or author["collection"]["outcome"] != "complete"
            or author["collection"]["unresolved"]):
        records.invalid("author job needs a settled, complete, unresolved-free collection")
    author_identity = safe_token(author["agent_name"])
    pane = safe_token(author["pane_id"])
    managed_reviews = bool(args.review_job)
    if managed_reviews:
        if len(set(args.review_job)) != len(args.review_job):
            records.invalid("duplicate review job IDs")
        if len(set(args.author_alias_job)) != len(args.author_alias_job):
            records.invalid("duplicate author alias job IDs")
        alias_jobs = [select_job(state, job_id) for job_id in args.author_alias_job]
        author_aliases = {safe_token(job["agent_name"]) for job in alias_jobs}
        author_panes = {pane, *(safe_token(job["pane_id"]) for job in alias_jobs)}
        review_jobs = [select_job(state, job_id) for job_id in args.review_job]
        review_locations = [review_source(job) for job in review_jobs]
        review_paths = [source for source, _ in review_locations]
        if len(set(review_paths)) != len(review_paths):
            records.invalid("multiple jobs resolve to the same review source")
    else:
        if args.author_alias_job:
            records.invalid("author aliases require --review-job")
        review_jobs = []
        review_locations = [(path, None) for path in args.review_output]
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
        for index, (path, report_root) in enumerate(review_locations):
            source = path if (path / "review-evidence.json").is_file() else path / "review"
            name = "review" if index == 0 else f"review-r{index}"
            if source.is_symlink():
                records.invalid("review source must not be a symlink")
            if managed_reviews:
                document = read_disposition_evidence(source)
                evidence = records.parse_json(document.original)
                original_identities = {key: evidence.get(key) for key in
                                       ("author_identity", "reviewer_identity")}
                if type(evidence["schema_version"]) is not int or evidence["schema_version"] != 2:
                    records.invalid("job-based assembly requires schema version 2 evidence")
                job = review_jobs[index]
                reviewer = safe_token(job["agent_name"])
                reviewer_pane = safe_token(job["pane_id"])
                if reviewer in {author_identity, *author_aliases} or reviewer_pane in author_panes:
                    records.invalid("review job shares author identity or pane")
                if resolve_identity(evidence.get("reviewer_identity", reviewer), state) != reviewer:
                    records.invalid("reviewer identity disagrees with managed job")
                source_author = resolve_identity(evidence.get("author_identity", author_identity), state)
                if source_author not in {author_identity, *author_aliases}:
                    records.invalid("author identity disagrees with managed job; name an explicit --author-alias-job")
                evidence = {"schema_version": 2, "task_id": evidence["task_id"],
                            "author_identity": author_identity, "reviewer_identity": reviewer,
                            "capture_id": evidence["capture_id"], "axes": evidence["axes"]}
                evidence_raw = records.encoded(evidence) + b"\n"
            else:
                document = read_disposition_evidence(source)
                evidence_raw = document.original
                evidence = records.parse_json(evidence_raw)
            if str(evidence["task_id"]) != task_id or evidence["author_identity"] != author_identity:
                records.invalid("review task or author identity disagrees with author job")
            reviewer = safe_token(evidence["reviewer_identity"])
            if reviewer == author_identity:
                records.invalid("reviewer is the candidate author")
            destination = task / name
            destination.mkdir()
            records.atomic_bytes(destination / "review-evidence.json", evidence_raw)
            read_disposition_evidence(destination)
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
            roster.append(f"{task_id} {reviewer}" + (f" {reviewer_pane}" if managed_reviews else "") + "\n")
            source_record = {"round": name, "directory": str(source.resolve())}
            verdict = reviewer_verdict(source, evidence, required=managed_reviews,
                                       report_root=report_root)
            if verdict:
                source_record["verdict"] = verdict
            if managed_reviews:
                source_record["job_id"] = review_jobs[index]["spec"]["job_id"]
                source_record["source_identities"] = original_identities
            sources.append(source_record)
        if reviewed_head != head:
            records.invalid("review chain does not reach candidate head")
        result = {"task_id": task_id, "outcome": "ready", "candidate": {
            "repo": str(repo), "branch": branch, "base": base, "head": head,
        }}
        records.atomic_bytes(task / "result.json", records.encoded(result) + b"\n")
        response_raw = (records.read_regular(args.review_response) if args.review_response
                        else "".join(response).encode())
        records.atomic_bytes(task / "review-response.md", response_raw)
        if args.review_response or managed_reviews:
            review_dispositions.sync_dispositions(task)
        for index, source in enumerate(sources):
            directory = task / source["round"]
            evidence = records.parse_json(records.read_regular(directory / "review-evidence.json"))
            counts = {axis: len(evidence["axes"][axis]["findings"]) for axis in AXES}
            done = {"task_id": task_id, "capture_id": evidence["capture_id"]}
            if "verdict" in source:
                done["verdict"] = source["verdict"]
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
            "reviews": sources, "author_alias_jobs": args.author_alias_job,
        }) + b"\n")
        staging.rename(output)
    return {"outcome": "prepared", "batch": str(output), "task_dir": str(output / task_id)}
