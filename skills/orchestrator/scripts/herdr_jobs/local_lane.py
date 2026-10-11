"""Read-only admission checks for the opt-in local Pi route."""

import argparse
from pathlib import Path
import subprocess
import sys

from .records import JobError, encoded, load_json, policy_record


POLICY_PATH = Path(__file__).resolve().parents[2] / "launch-policy.json"


def _run_readonly(argv):
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JobError("unavailable_capability", f"local lane precondition command failed: {argv[0]}") from error
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def check_preconditions(config, run=_run_readonly):
    """Require the model server and its reserved GPU headroom before a start."""
    unit = config["service_unit"]
    code, output, _ = run(["systemctl", "--user", "is-active", unit])
    if code != 0 or output != "active":
        raise JobError("unavailable_capability", f"local lane requires active user service {unit}")

    code, output, _ = run([
        "nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits",
    ])
    if code != 0:
        raise JobError("unavailable_capability", "local lane GPU memory could not be checked")
    rows = [line.strip() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or not rows[0].isdecimal():
        raise JobError("unavailable_capability", "local lane requires one readable NVIDIA GPU memory record")
    free_mib = int(rows[0])
    minimum = config["minimum_free_gpu_memory_mib"]
    if free_mib < minimum:
        raise JobError("unavailable_capability",
                       f"local lane needs at least {minimum} MiB free GPU memory; observed {free_mib} MiB")
    return {"service_unit": unit, "service_state": "active",
            "free_gpu_memory_mib": free_mib, "minimum_free_gpu_memory_mib": minimum}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight",))
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    args = parser.parse_args(argv)
    try:
        policy = policy_record(load_json(args.policy))
        config = policy.get("local_lane")
        if config is None:
            raise JobError("unavailable_capability", "local lane is not configured by launch policy")
        result = check_preconditions(config)
        print(encoded({"outcome": "ready", "checks": result}).decode())
        return 0
    except JobError as error:
        print(encoded({"outcome": "blocked", "error": error.kind, "message": str(error)}).decode())
        return 4 if error.kind == "unavailable_capability" else 2
    except OSError as error:
        print(encoded({"outcome": "blocked", "error": "io_error", "message": str(error)}).decode())
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
