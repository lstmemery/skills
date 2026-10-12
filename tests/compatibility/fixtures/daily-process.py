#!/usr/bin/env python3
"""Offline process adapter for both daily-run entry points."""
import json
import os
from pathlib import Path
import signal
import sys

name = Path(sys.argv[0]).name
if name == "date":
    print(os.environ["COMPAT_DAY"])
    raise SystemExit(0)
with Path(os.environ["COMPAT_CALLS"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps([name, *sys.argv[1:]]) + "\n")
if name == "codex" and os.environ["COMPAT_PRIMARY"] != "success":
    print("HTTP 401 Unauthorized" if os.environ["COMPAT_PRIMARY"] == "auth"
          else "connection timed out", file=sys.stderr)
    raise SystemExit(1)
if name == "orchestrator" and sys.argv[1] == "launch":
    print(json.dumps({"taskId": os.environ["COMPAT_TASK_ID"]}))
    raise SystemExit(0)
if name == "orchestrator" and os.environ["COMPAT_TASK"] == "interrupted":
    os.kill(int(os.environ["COMPAT_RUNNER_PID"]), signal.SIGTERM)
    raise SystemExit(0)
if name == "orchestrator" and os.environ["COMPAT_TASK"] != "succeeded":
    print(json.dumps({"status": os.environ["COMPAT_TASK"]}))
    raise SystemExit(0)
fixture = json.loads(Path(os.environ["COMPAT_FIXTURE"]).read_text())
archive = Path(os.environ["COMPAT_ARCHIVE"])
for filename, key in (("candidates.json", "candidates"), ("selection.json", "selection")):
    (archive / filename).write_text(json.dumps(fixture[key]), encoding="utf-8")
(archive / "brief.md").write_text("# Completed brief\n", encoding="utf-8")
if name == "orchestrator":
    print(json.dumps({"status": "succeeded"}))
