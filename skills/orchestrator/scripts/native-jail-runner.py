#!/usr/bin/env python3
"""Run a coordinator-authorized jail-agent command and persist its exit status."""

import argparse
import os
from pathlib import Path
import subprocess

from herdr_jobs.records import JobError, fields, identifier, load_json, now, save, text, version


def execute(request, run_process=subprocess.run):
    fields(request, ["schema_version", "job_id", "attempt_id", "cwd", "argv", "exit_path"],
           label="native jail launch request")
    version(request["schema_version"])
    identifier(request["job_id"], "job_id")
    identifier(request["attempt_id"], "attempt_id")
    cwd = Path(text(request["cwd"], "cwd", 4096)).resolve(strict=True)
    if not cwd.is_dir():
        raise JobError("invalid_input", "cwd must be a directory")
    argv = request["argv"]
    prefixes = (["jail-agent", "--harness", "codex", "exec", "--skip-git-repo-check"],
                ["codex-jail-agent", "--harness", "codex", "exec", "--skip-git-repo-check"],
                ["omp-train", "--harness", "codex", "exec", "--skip-git-repo-check"])
    if not isinstance(argv, list) or len(argv) < 6 or argv[:5] not in prefixes:
        raise JobError("invalid_input", "argv must use a supported jail-agent Codex exec command")
    for index, value in enumerate(argv):
        text(value, f"argv[{index}]")
    text(argv[-1], "task")
    exit_path = text(request["exit_path"], "exit path", 4096)
    try:
        result = run_process(argv, cwd=str(cwd), check=False)
        exit_code = result.returncode
    except OSError:
        exit_code = 127
    save(exit_path, {"schema_version": 1, "job_id": request["job_id"],
                     "attempt_id": request["attempt_id"], "exit_code": exit_code,
                     "exited_at": now()})
    return exit_code if exit_code >= 0 else 128 - exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    if Path("/ctx").exists() or os.environ.get("HERDR_ENV") != "1":
        raise JobError("unavailable_capability", "native jail runner executes only in the authorized Herdr host session")
    return execute(load_json(args.request))


if __name__ == "__main__":
    raise SystemExit(main())
