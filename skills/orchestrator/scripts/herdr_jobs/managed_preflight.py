"""Project collected managed jobs into the integration gate's evidence layout."""

import io
from pathlib import Path
import re
import tarfile

from . import records
from .preflight_batch import done_verdict
from .review_evidence import AXES, ReviewEvidenceError
from .worker_records import WorkerIdentity, validate_result


REVIEW_SIDECAR = re.compile(r"^(review|review-r[1-9][0-9]*)/review-evidence\.json$")
CAPTURE_BUNDLE = re.compile(r"^(review|review-r[1-9][0-9]*)/capture\.tar$")
CAPTURE_DIRECTORY = re.compile(r"^(review|review-r[1-9][0-9]*)/capture(?:/blobs)?$")
CAPTURE_PAYLOAD = re.compile(
    r"^(review|review-r[1-9][0-9]*)/capture/(manifest\.json|COMPLETE|diff\.patch|commits\.txt|blobs/[0-9a-f]{64})$"
)
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise ReviewEvidenceError("managed identity or pane is not a single safe token")
    return value


def parse_native_authors(specs):
    """Parse explicit identities for candidates imported from native runs."""
    authors = {}
    for spec in specs:
        task_id, separator, identity_and_pane = spec.partition("=")
        identity, comma, pane = identity_and_pane.partition(",")
        if not separator or not comma or not task_id or not identity or not pane or "," in pane:
            raise ReviewEvidenceError(
                "--native-author must be TASK_ID=AUTHOR_IDENTITY,PANE_ID"
            )
        task_id = token(task_id)
        if task_id in authors:
            raise ReviewEvidenceError(f"native author for task {task_id} is declared more than once")
        authors[task_id] = {"agent_name": token(identity), "pane_id": token(pane)}
    return authors


def collected_files(root, request_id, job):
    """Verify the same attempt-bound receipt and revision produced by collect."""
    if not job["settled"] or job["collection_error"] or job["pending_effect"]:
        raise ReviewEvidenceError("managed job is not settled with a complete collection")
    job_id = token(job["spec"]["job_id"])
    attempt = job["attempt_id"]
    if not isinstance(attempt, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt):
        raise ReviewEvidenceError("managed job has an invalid attempt ID")
    if job["agent_name"] != "c9-" + attempt[:24]:
        raise ReviewEvidenceError("managed agent identity does not match its attempt")
    token(job["pane_id"])
    collection = job["collection"]
    if collection is None or collection["outcome"] != "complete" or collection["unresolved"]:
        raise ReviewEvidenceError("managed collection is incomplete or unresolved")
    revision = collection["revision"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ReviewEvidenceError("managed collection revision is invalid")
    directory = root / "collected" / job_id / revision
    receipt_path = directory / "receipt.json"
    if collection["receipt_path"] != str(receipt_path):
        raise ReviewEvidenceError("managed receipt is outside its collection revision")
    raw = records.bounded_file(root, str(receipt_path.relative_to(root)), 262144)
    if records.digest(raw) != collection["receipt_sha256"]:
        raise ReviewEvidenceError("managed receipt digest mismatch")
    identity_error = ReviewEvidenceError("managed receipt identity or outcome mismatch")
    receipt, _ = records.validate_receipt(
        raw, (request_id, job_id, attempt), label="managed receipt", identity_error=identity_error)
    if receipt["outcome"] != "complete" or receipt["unresolved"]:
        raise identity_error
    artifacts = collection["artifacts"]
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= records.MAX_RECEIPT_ARTIFACTS:
        raise ReviewEvidenceError(
            f"managed collection needs 1–{records.MAX_RECEIPT_ARTIFACTS} artifacts")
    files = {}
    hashes = []
    for index, item in enumerate(artifacts):
        source = item["source"]
        path = directory / f"artifact-{index}"
        if item["path"] != str(path):
            raise ReviewEvidenceError("managed artifact is outside its collection revision")
        # Also validate source paths, which become paths in the projection.
        pieces = source.split("/")
        if any(part in ("", ".", "..") for part in pieces) or "\\" in source:
            raise ReviewEvidenceError("managed artifact source is not a bounded relative path")
        if source in files or source == "receipt.json":
            raise ReviewEvidenceError("managed collection repeats an artifact")
        data = records.bounded_file(root, str(path.relative_to(root)))
        digest = records.digest(data)
        if digest != item["sha256"] or len(data) != item["bytes"]:
            raise ReviewEvidenceError("managed artifact digest or size mismatch")
        files[source] = data
        hashes.append({"source": source, "sha256": digest})
    _, actual_revision = records.validate_receipt(
        raw, (request_id, job_id, attempt), hashes,
        label="managed receipt", identity_error=identity_error)
    if receipt["artifacts"] != list(files) or actual_revision != revision:
        raise ReviewEvidenceError("managed collection revision does not match its receipt and artifacts")
    return files


def expand_capture_bundles(files):
    """Read receipt-pinned tar bytes; never extract archive paths to disk.

    Capture manifests and payload hashes are checked by the gate's existing
    capture verifier after projection, just as for individually collected files.
    """
    expanded = dict(files)
    total = sum(len(data) for source, data in files.items() if not CAPTURE_BUNDLE.fullmatch(source))
    for source, raw in files.items():
        match = CAPTURE_BUNDLE.fullmatch(source)
        if match is None:
            continue
        del expanded[source]
        seen = set()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for index, member in enumerate(archive):
                if index >= 20002:
                    raise ReviewEvidenceError("managed capture bundle exceeds 20002 entries")
                if member.name in seen:
                    raise ReviewEvidenceError("managed capture bundle repeats a path")
                seen.add(member.name)
                if member.isdir() and CAPTURE_DIRECTORY.fullmatch(member.name):
                    continue
                payload = CAPTURE_PAYLOAD.fullmatch(member.name)
                if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or payload is None:
                    raise ReviewEvidenceError("managed capture bundle has an unsafe member")
                total += member.size
                if not 0 <= member.size <= 16777216 or total > 67108864:
                    raise ReviewEvidenceError("managed capture bundle exceeds payload size limits")
                path = member.name
                if path in expanded:
                    raise ReviewEvidenceError("managed capture bundle conflicts with collected evidence")
                with archive.extractfile(member) as stream:
                    data = stream.read(member.size + 1)
                if len(data) != member.size:
                    raise ReviewEvidenceError("managed capture bundle has a truncated member")
                expanded[path] = data
    return expanded


def materialize_runs(run_dirs, output_dir, native_run_dirs=(), native_author_specs=()):
    """Return a durable evidence snapshot and provenance, without changing jobs."""
    try:
        return _materialize_runs(
            run_dirs, output_dir, native_run_dirs, native_author_specs
        )
    except (records.JobError, KeyError, TypeError, ValueError, tarfile.TarError) as error:
        raise ReviewEvidenceError(f"invalid preflight evidence input: {error}") from error


def _materialize_runs(run_dirs, output_dir, native_run_dirs=(), native_author_specs=()):
    jobs = []
    states = []
    roots = [Path(path).resolve() for path in run_dirs]
    if len(set(roots)) != len(roots):
        raise ReviewEvidenceError("managed run inputs must be unique")
    for root in roots:
        raw = records.bounded_file(root, "state.json", 67108864)
        state = records.parse_json(raw)
        records.version(state["schema_version"])
        if not isinstance(state["jobs"], list):
            raise ReviewEvidenceError("unsupported managed state")
        states.append((root, "state.json", raw))
        for job in state["jobs"]:
            files = expand_capture_bundles(collected_files(root, state["request"]["request_id"], job))
            provenance = {
                "run_dir": str(root), "state_sha256": records.digest(raw),
                "request_id": state["request"]["request_id"], "job_id": job["spec"]["job_id"],
                "attempt_id": job["attempt_id"], "agent_name": job["agent_name"],
                "pane_id": job["pane_id"], "collection": job["collection"],
            }
            jobs.append((job, files, provenance))

    projected = {}
    authors = {}
    roster = []

    def put(path, data):
        if path in projected:
            raise ReviewEvidenceError(f"ambiguous managed evidence for {path}")
        projected[path] = data

    def row(task_id, job):
        roster.append(f"{task_id} {token(job['agent_name'])} {token(job['pane_id'])}\n")

    for job, files, _ in jobs:
        writer = job["spec"]["writes_repository"]
        if type(writer) is not bool:
            raise ReviewEvidenceError("managed writes_repository must be explicit")
        if not writer:
            continue
        identity = job["spec"]["worker_result"]
        if "result.json" not in files:
            raise ReviewEvidenceError("managed coding job must collect result.json")
        # Validate the worker record without reading mutable worker output.
        result = records.parse_json(files["result.json"])
        task_id = token(result["task_id"])
        if task_id in authors:
            raise ReviewEvidenceError(f"multiple managed coding jobs for task {task_id}")
        directory = "task-" + records.digest(task_id.encode())[:24]
        authors[task_id] = (job, directory)
        # The shared validator is file based. Validate after writing this exact
        # immutable payload below, before returning any input to the gate.
        put(f"{directory}/result.json", files["result.json"])
        row(task_id, job)
        for artifact in result["artifacts"]:
            if artifact["path"] not in files:
                raise ReviewEvidenceError("managed result artifact was not collected")
        if "review-response.md" in files:
            put(f"{directory}/review-response.md", files["review-response.md"])
        if (result["task_id"], result["assignment_revision"]) != (
                identity["task_id"], identity["assignment_revision"]):
            raise ReviewEvidenceError("managed result does not match its assignment")

    native_authors = parse_native_authors(native_author_specs)
    if native_authors and not native_run_dirs:
        raise ReviewEvidenceError("--native-author requires at least one --native-run")
    if native_run_dirs and not native_authors:
        raise ReviewEvidenceError("--native-run requires an explicit --native-author for each candidate")
    if native_run_dirs and not run_dirs:
        raise ReviewEvidenceError("--native-run requires at least one managed review run")
    native_roots = []
    native_sources = {}
    native_imports = []
    for source in native_run_dirs:
        original_root = Path(source)
        if original_root.is_symlink():
            raise ReviewEvidenceError("native run root cannot be a symlink")
        root = original_root.resolve()
        if root in native_roots:
            raise ReviewEvidenceError("native run inputs must be unique")
        if root in roots:
            raise ReviewEvidenceError("managed and native run inputs must be separate")
        native_roots.append(root)
        workers_raw = records.bounded_file(root, "workers.json", 4194304)
        workers_state = records.parse_json(workers_raw)
        if not isinstance(workers_state, dict) or not isinstance(workers_state.get("workers"), list):
            raise ReviewEvidenceError("native workers.json must contain a workers array")
        seen_task_ids = set()
        seen_directories = set()
        imported_from_root = 0
        for worker in workers_state["workers"]:
            worker = records.fields(
                worker, ("task_id", "assignment_revision", "directory"),
                label="native worker roster entry",
            )
            task_id = token(worker["task_id"])
            revision = records.integer(
                worker["assignment_revision"], "native assignment_revision", 1, 2**63 - 1
            )
            relative = records.text(worker["directory"], "native worker directory", 4096)
            parts = relative.split("/")
            if (
                parts[0] != "tasks" or "\\" in relative
                or any(part in ("", ".", "..") for part in parts)
            ):
                raise ReviewEvidenceError("native worker directory must be bounded under tasks/")
            if task_id in seen_task_ids or relative in seen_directories:
                raise ReviewEvidenceError("native worker roster repeats a task or directory")
            seen_task_ids.add(task_id)
            seen_directories.add(relative)
            if task_id not in native_authors:
                continue
            if task_id in native_sources:
                raise ReviewEvidenceError(f"native task {task_id} appears in more than one run")
            result_relative = f"{relative}/result.json"
            result_raw = records.bounded_file(root, result_relative, 16777216)
            result = records.parse_json(result_raw)
            if not isinstance(result, dict):
                raise ReviewEvidenceError("native worker result must be a JSON object")
            if (result.get("task_id"), result.get("assignment_revision")) != (task_id, revision):
                raise ReviewEvidenceError("native result does not match its worker roster")
            candidate = result.get("candidate")
            if not isinstance(candidate, dict):
                raise ReviewEvidenceError("native worker result has no candidate record")
            response_relative = f"{relative}/review-response.md"
            try:
                response_raw = records.bounded_file(root, response_relative, 16777216)
            except FileNotFoundError:
                response_raw = None
            native_sources[task_id] = {
                "root": root,
                "workers_raw": workers_raw,
                "result_relative": result_relative,
                "result_raw": result_raw,
                "response_relative": response_relative,
                "response_raw": response_raw,
                "result": result,
                "revision": revision,
                "workers_sha256": records.digest(workers_raw),
                "result_sha256": records.digest(result_raw),
                "response_sha256": records.digest(response_raw) if response_raw is not None else None,
            }
            imported_from_root += 1
        if not imported_from_root:
            raise ReviewEvidenceError(
                f"native run {root} has no task named by --native-author"
            )
    if set(native_sources) != set(native_authors):
        missing = sorted(set(native_authors) - set(native_sources))
        raise ReviewEvidenceError(
            "native author task is not present in a supplied workers.json: " + ", ".join(missing)
        )

    for task_id, source in native_sources.items():
        if task_id in authors:
            raise ReviewEvidenceError(f"multiple coding candidates for task {task_id}")
        directory = "task-" + records.digest(task_id.encode())[:24]
        native_identity = native_authors[task_id]
        author = {
            "agent_name": native_identity["agent_name"],
            "pane_id": native_identity["pane_id"],
            "spec": {
                "job_id": task_id,
                "worker_result": {"task_id": task_id, "assignment_revision": source["revision"]},
            },
        }
        authors[task_id] = (author, directory)
        put(f"{directory}/result.json", source["result_raw"])
        if source["response_raw"] is not None:
            put(f"{directory}/review-response.md", source["response_raw"])
        row(task_id, author)
        import_record = {
            "schema_version": 1,
            "source_kind": "native-run",
            "source_run": str(source["root"]),
            "task_id": task_id,
            "assignment_revision": source["revision"],
            "result_path": source["result_relative"],
            "result_sha256": source["result_sha256"],
            "workers_sha256": source["workers_sha256"],
            "review_response_sha256": source["response_sha256"],
            "author_identity": native_identity["agent_name"],
            "author_pane": native_identity["pane_id"],
            "candidate": source["result"]["candidate"],
        }
        native_imports.append(import_record)
        states.append((source["root"], "workers.json", source["workers_raw"]))
        states.append((source["root"], source["result_relative"], source["result_raw"]))
        if source["response_raw"] is not None:
            states.append((source["root"], source["response_relative"], source["response_raw"]))

    if not authors:
        raise ReviewEvidenceError("preflight inputs contain no coding candidates")

    for job, files, _ in jobs:
        for source, raw in files.items():
            match = REVIEW_SIDECAR.fullmatch(source)
            if match is None:
                continue
            review_name = match[1]
            evidence = records.parse_json(raw)
            task_id = token(evidence["task_id"])
            if task_id not in authors:
                raise ReviewEvidenceError(f"managed review has no coding job for task {task_id}")
            author, directory = authors[task_id]
            if "review_of" in job["spec"]:
                if job["spec"]["review_of"] != author["spec"]["job_id"]:
                    raise ReviewEvidenceError("scheduled review_of disagrees with coding job")
                done_verdict(files.get(f"{review_name}/done.json"), evidence, required=True)
            if author["agent_name"] == job["agent_name"] or author["pane_id"] == job["pane_id"]:
                raise ReviewEvidenceError("managed reviewer is the candidate author or shares its pane")
            for key, expected in (("author_identity", author["agent_name"]),
                                  ("reviewer_identity", job["agent_name"])):
                if key in evidence and evidence[key] != expected:
                    raise ReviewEvidenceError(f"managed {key} disagrees with helper state")
                evidence[key] = expected
            row(task_id, job)
            prefix = review_name + "/"
            for path, data in files.items():
                if path.startswith(prefix) and path != source:
                    put(f"{directory}/{path}", data)
            put(f"{directory}/{source}", records.encoded(evidence) + b"\n")
            done_path = f"{directory}/{review_name}/done.json"
            if done_path not in projected:
                counts = {axis: len(evidence["axes"][axis]["findings"]) for axis in AXES}
                done = {"task_id": task_id, "capture_id": evidence["capture_id"]}
                if review_name == "review":
                    done.update(standards_findings=counts["standards"], spec_findings=counts["spec"])
                else:
                    done.update(new_findings=sum(counts.values()), unfixed=sum(
                        finding["disposition"] not in ("fixed", "rejected")
                        for axis in AXES for finding in evidence["axes"][axis]["findings"]
                    ))
                put(done_path, records.encoded(done) + b"\n")
    put("workers.txt", "".join(roster).encode())
    provenance = [item for _, _, item in jobs]
    put("managed-jobs.json", records.encoded(provenance) + b"\n")
    if native_imports:
        put("native-imports.json", records.encoded(native_imports) + b"\n")
    snapshot_id = records.digest(records.encoded({
        path: records.digest(data) for path, data in sorted(projected.items())
    }))
    destination = output_dir / "managed-inputs" / snapshot_id
    if destination.is_symlink():
        raise ReviewEvidenceError("managed evidence snapshot was modified")
    for path in destination.rglob("*"):
        if path.is_symlink() or (not path.is_dir() and str(path.relative_to(destination)) not in projected):
            raise ReviewEvidenceError("managed evidence snapshot was modified")
    for path, data in projected.items():
        target = destination / path
        if target.exists():
            if records.bounded_file(destination, path) != data:
                raise ReviewEvidenceError("managed evidence snapshot was modified")
        else:
            records.atomic_bytes(target, data)
    for task_id, (job, directory) in authors.items():
        identity = job["spec"]["worker_result"]
        validate_result(destination / directory / "result.json",
                        WorkerIdentity(task_id, identity["assignment_revision"]), repository_changes=True)
    for root, relative, raw in states:
        if records.bounded_file(root, relative, 67108864) != raw:
            raise ReviewEvidenceError("preflight source changed while preparing integration evidence")
    return destination, provenance, native_imports
