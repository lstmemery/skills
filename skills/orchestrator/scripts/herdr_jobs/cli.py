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


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise JobError("invalid_input", message)


def parser():
    result = Parser(description="Launch, checkpoint, and collect independent Herdr jobs. JSON output; Python 3.11+ / POSIX.")
    commands = result.add_subparsers(dest="operation", required=True)
    for name in ("run", "resume", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--run-dir", required=True)
        sub.add_argument("--host-contract", help="verified host CLI and jail export binding")
        sub.add_argument("--wait-seconds", type=float, default=30)
        if name == "run":
            sub.add_argument("--manifest", required=True)
            sub.add_argument("--policy", required=True)
            sub.add_argument("--preview", action="store_true", help="offline validation; no writes or host calls")
    return result


def main(argv=None, transport_factory=NativeTransport):
    engine = None
    try:
        args = parser().parse_args(argv)
        if not 0 <= args.wait_seconds <= 60:
            raise JobError("invalid_input", "wait-seconds must be finite and between 0 and 60")
        root = Path(args.run_dir).absolute()
        request = prepare(args.manifest, args.policy) if args.operation == "run" else None
        if request is not None:
            Engine(root, None, None).validate_preview(request)
        if args.operation == "run" and args.preview:
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
                if request is not None:
                    from_request = engine.state["request_digest"]
                    if digest(encoded(request)) != from_request:
                        raise JobError("conflict", "request content or policy changed; original run preserved")
                request = engine.state["request"]
            if args.operation != "run" and not exists:
                raise JobError("invalid_input", "run state does not exist")
            if args.wait_seconds == 0:
                if exists:
                    result = engine.result()
                    next_argv = [sys.executable, ENTRYPOINT, "resume", "--run-dir", str(root)]
                else:
                    result = {"schema_version": 1, "request_id": request["request_id"], "run_dir": str(root),
                              "batch_state": "checkpointed", "run_created": False, "collection_complete": False,
                              "concurrency": request["concurrency"], "active_jobs": 0,
                              "jobs": [{"job_id": job["job_id"], "phase": "pending"} for job in request["jobs"]]}
                    next_argv = [sys.executable, ENTRYPOINT, "run", "--run-dir", str(root),
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
            capabilities = transport.preflight(request, existing=engine.state, status_only=args.operation == "status")
            if not exists:
                engine.initialize(request, capabilities, binding_path)
            else:
                engine.bind(capabilities)
            engine.drive(status_only=args.operation == "status")
            result = engine.result()
            if result["batch_state"] == "collected":
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
