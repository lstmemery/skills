#!/usr/bin/env python3
"""Host-side launcher receipt: completion survives disappearance of the agent."""

import argparse
import os
from pathlib import Path
import subprocess

from herdr_jobs.records import JobError, fields, identifier, load_json, now, save, text, version


def execute(request, run_process=subprocess.run):
    fields(request, ["schema_version", "job_id", "attempt_id", "argv", "exit_path"])
    version(request["schema_version"])
    identifier(request["job_id"], "job_id")
    identifier(request["attempt_id"], "attempt_id")
    argv = request["argv"]
    if not isinstance(argv, list) or len(argv) != 6 or argv[:5] != ["omp-train", "--harness", "codex", "exec", "--skip-git-repo-check"]:
        raise JobError("invalid_input", "launcher request must use the supported omp-train Codex command")
    text(argv[-1], "task")
    text(request["exit_path"], "exit path", 4096)
    try:
        result = run_process(argv, check=False)
        exit_code = result.returncode
    except OSError:
        exit_code = 127
    save(request["exit_path"], {"schema_version": 1, "job_id": request["job_id"],
                              "attempt_id": request["attempt_id"], "exit_code": exit_code, "exited_at": now()})
    return exit_code if exit_code >= 0 else 128 - exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    if Path("/ctx").exists() or os.environ.get("HERDR_ENV") != "1":
        raise JobError("unavailable_capability", "jail-runner executes only in the authorized Herdr host session")
    return execute(load_json(args.request))


if __name__ == "__main__":
    raise SystemExit(main())
