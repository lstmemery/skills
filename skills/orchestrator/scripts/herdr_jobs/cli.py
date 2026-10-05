"""JSON command boundary for the durable job engine."""

import argparse
from pathlib import Path
import sys

from .engine import Engine
from .records import JobError, digest, encoded, prepare, run_lock
from .transport import BudgetExpired, Deadline, NativeTransport


ENTRYPOINT = str(Path(__file__).resolve().parents[1] / "herdr-jobs.py")
EXIT_CODES = {"invalid_input": 2, "conflict": 3, "unavailable_capability": 4,
              "decision_needed": 5, "unresolved_effect": 6, "io_error": 7, "partial": 10}
WORKER_DISPOSITIONS = {"completed", "blocked", "failed"}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise JobError("invalid_input", message)


def parser():
    result = Parser(description="Launch, checkpoint, collect, and finish Herdr jobs and worker fleets. JSON output; Python 3.11+ / POSIX.")
    commands = result.add_subparsers(dest="operation", required=True)
    for name in ("run", "add", "resume", "status", "finish"):
        sub = commands.add_parser(name)
        sub.add_argument("--run-dir", required=True)
        sub.add_argument("--host-contract", help="verified host CLI and jail export binding")
        sub.add_argument("--wait-seconds", type=float, default=30)
        if name == "finish":
            sub.add_argument("--coordinator-disposition-override", action="append", metavar="JOB_ID=DISPOSITION",
                             help="explicitly override one worker disposition (repeatable; requires a reason)")
            sub.add_argument("--coordinator-override-reason",
                             help="required reason recorded with every coordinator disposition override")
        if name in ("run", "add"):
            sub.add_argument("--manifest", required=True)
            sub.add_argument("--policy", required=True)
            sub.add_argument("--preview", action="store_true", help="offline validation; no writes or host calls")
    return result


def parse_finish_overrides(values, reason):
    values = values or []
    if bool(values) != (reason is not None):
        raise JobError("invalid_input", "coordinator disposition overrides require --coordinator-override-reason, and a reason requires at least one override")
    overrides = {}
    for value in values:
        job_id, separator, disposition = value.partition("=")
        if (not separator or not job_id.strip() or not disposition.strip() or "=" in disposition
                or disposition.strip() not in WORKER_DISPOSITIONS):
            raise JobError("invalid_input", "coordinator disposition override must be JOB_ID=completed|blocked|failed")
        job_id = job_id.strip()
        if job_id in overrides:
            raise JobError("invalid_input", f"duplicate coordinator disposition override for {job_id}")
        overrides[job_id] = disposition.strip()
    if reason is not None:
        reason = reason.strip()
        if not reason or len(reason) > 10000:
            raise JobError("invalid_input", "coordinator override reason must contain 1 to 10000 characters")
    return overrides, reason


def finish_message(finish):
    validations = finish["jobs"]
    validated = [job_id for job_id, item in validations.items()
                 if item["worker_result_validation"] == "validated"]
    legacy = [job_id for job_id, item in validations.items()
              if item["worker_result_validation"] == "not_requested"]
    dispositions = []
    for job_id in validated:
        item = validations[job_id]
        detail = f"{job_id}: worker outcome {item['worker_result_outcome']} -> disposition {item['worker_disposition']}"
        if item.get("coordinator_override_reason"):
            detail += f" by coordinator override ({item['coordinator_override_reason']})"
        dispositions.append(detail)
    closed = [job_id for job_id, item in validations.items() if item["workspace_closed"]]
    parts = []
    if validated:
        parts.append(
            "Validated each worker result.json schema and assigned task/revision (including the repository candidate "
            "when required), and verified its receipt listed result.json and every declared artifact file for: "
            + ", ".join(validated) + "."
        )
        parts.append("Recorded dispositions: " + "; ".join(dispositions) + ".")
    if legacy:
        parts.append("Worker result validation was not requested for legacy receipt-only job(s): " + ", ".join(legacy) + ".")
    if closed:
        parts.append("Closed workspaces for: " + ", ".join(closed) + ".")
    parts.append("Acceptance remains pending; review the collected artifacts and outcomes.")
    return " ".join(parts)


def main(argv=None, transport_factory=NativeTransport):
    engine = None
    try:
        args = parser().parse_args(argv)
        if not 0 <= args.wait_seconds <= 60:
            raise JobError("invalid_input", "wait-seconds must be finite and between 0 and 60")
        disposition_overrides, override_reason = ({}, None)
        if args.operation == "finish":
            disposition_overrides, override_reason = parse_finish_overrides(
                args.coordinator_disposition_override, args.coordinator_override_reason)
        root = Path(args.run_dir).absolute()
        request = prepare(args.manifest, args.policy) if args.operation in ("run", "add") else None
        if args.operation == "add" and len(request["jobs"]) != 1:
            raise JobError("invalid_input", "add accepts exactly one new job; use run for an initial batch")
        if request is not None:
            Engine(root, None, None).validate_preview(request)
        if args.operation in ("run", "add") and args.preview:
            result = {"schema_version": 1, "batch_state": "preview", "request_id": request["request_id"],
                      "concurrency": request["concurrency"], "capabilities": "unverified",
                      "run_dir": str(root), "jobs": [{"job_id": job["job_id"], "route": job["route"],
                        "kind": job["kind"], "model": job["model"], "cwd": job["cwd"],
                        "effects": ["create workspace/pane", "start worker", "submit task", "collect artifacts"]}
                        for job in request["jobs"]]}
            print(encoded(result).decode())
            return 0
        deadline = Deadline(args.wait_seconds)
        with run_lock(root):
            engine = Engine(root, None, deadline)
            exists = (root / "state.json").exists()
            if exists:
                engine.load()
                if args.operation == "run" and request is not None:
                    from_request = engine.state["request_digest"]
                    if digest(encoded(request)) != from_request:
                        raise JobError("conflict", "request content or policy changed; original run preserved")
                if args.operation not in ("add",):
                    request = engine.state["request"]
            if args.operation in ("resume", "status", "finish") and not exists:
                raise JobError("invalid_input", "run state does not exist")
            if args.operation == "add" and not exists:
                raise JobError("invalid_input", "add requires an existing run; start it with run first")
            if args.wait_seconds == 0:
                if exists:
                    result = engine.result()
                    continuation = args.operation if args.operation in ("add", "finish") else "resume"
                    next_argv = [sys.executable, ENTRYPOINT, continuation, "--run-dir", str(root)]
                    if args.operation == "add":
                        next_argv += ["--manifest", str(Path(args.manifest).absolute()),
                                      "--policy", str(Path(args.policy).absolute())]
                    if args.operation == "finish":
                        for item in args.coordinator_disposition_override or []:
                            next_argv += ["--coordinator-disposition-override", item]
                        if override_reason is not None:
                            next_argv += ["--coordinator-override-reason", override_reason]
                else:
                    result = {"schema_version": 1, "request_id": request["request_id"], "run_dir": str(root),
                              "batch_state": "checkpointed", "run_created": False, "collection_complete": False,
                              "concurrency": request["concurrency"], "active_jobs": 0,
                              "jobs": [{"job_id": job["job_id"], "phase": "pending"} for job in request["jobs"]]}
                    next_argv = [sys.executable, ENTRYPOINT, args.operation, "--run-dir", str(root),
                                 "--manifest", str(Path(args.manifest).absolute()), "--policy", str(Path(args.policy).absolute())]
                    if args.host_contract:
                        next_argv += ["--host-contract", str(Path(args.host_contract).absolute())]
                result["observation"] = "local_only; no fresh host observation at zero budget"
                result["next_action"] = {"kind": "resume", "argv": next_argv, "message": "Continue with a positive call budget."}
                print(encoded(result).decode())
                return EXIT_CODES.get(result["batch_state"], 0)
            binding_path = args.host_contract or (engine.state["host_contract"] if exists else None)
            transport = transport_factory(binding_path, deadline)
            engine.transport = transport
            preflight_request = request if args.operation in ("run", "add") else engine.state["request"]
            capabilities = transport.preflight(preflight_request, existing=engine.state,
                                               status_only=args.operation in ("status", "finish"))
            if not exists:
                engine.initialize(request, capabilities, binding_path)
            else:
                engine.bind(capabilities)
                if args.operation == "add":
                    engine.append_jobs(request, capabilities)
            engine.drive(status_only=args.operation in ("status", "finish"))
            if args.operation == "finish":
                finish = engine.finish(disposition_overrides, override_reason)
            result = engine.result()
            if args.operation == "finish":
                result["finish"] = finish
                result["finish_complete"] = finish["complete"]
                if not finish["complete"]:
                    result["batch_state"] = "partial"
            if args.operation == "finish" and finish["complete"]:
                result["next_action"] = {"kind": "review", "argv": None, "message": finish_message(finish)}
            elif result["batch_state"] == "collected":
                result["next_action"] = {"kind": "review", "argv": None, "message": "Assess collected artifacts against the task."}
            else:
                result["next_action"] = {"kind": "inspect" if result["batch_state"] in ("partial", "unresolved_effect") else "resume",
                                         "argv": [sys.executable, ENTRYPOINT, "resume", "--run-dir", str(root)],
                                         "message": "Inspect per-job issues; resume reconciles without replaying ambiguous effects."}
        print(encoded(result).decode())
        return EXIT_CODES.get(result["batch_state"], 0)
    except BudgetExpired:
        print(encoded({"schema_version": 1, "error": "unavailable_capability", "message": "preflight exceeded the call budget; no new launch was attempted"}).decode())
        return 4
    except JobError as error:
        output = {"schema_version": 1, "error": error.kind, "message": str(error)}
        if engine is not None and engine.state is not None:
            output["checkpoint"] = engine.result()
            output["next_action"] = {"kind": "inspect", "argv": [sys.executable, ENTRYPOINT, "status", "--run-dir", str(engine.root), "--wait-seconds", "0"]}
        print(encoded(output).decode())
        return EXIT_CODES[error.kind]
    except (OSError, UnicodeError) as error:
        print(encoded({"schema_version": 1, "error": "io_error", "message": str(error)}).decode())
        return 7
    except KeyboardInterrupt:
        print(encoded({"schema_version": 1, "error": "interrupted", "message": "Resume from the durable checkpoint; effects may be unresolved."}).decode())
        return 130
