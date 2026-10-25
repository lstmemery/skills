#!/usr/bin/python3
"""Deliver queued heartbeat events to the coordinator, only if Herdr shows the exact recorded
Claude session idle in the recorded pane. Otherwise keep the queue (never type into a shell)."""
import json, subprocess, pathlib, time
S = pathlib.Path("/home/deadhand/.local/share/orchestrator/supervision")
q = S / "wake-queue.txt"
if not q.exists() or not q.read_text().strip():
    raise SystemExit
cfg = json.loads((S / "coordinator.json").read_text())
agents = json.loads(subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=15).stdout)["result"]["agents"]
me = [a for a in agents if a.get("pane_id") == cfg["pane_id"] and a.get("agent") == cfg["agent"]
      and a.get("agent_session", {}).get("value") == cfg["session"]]
if not me or me[0].get("agent_status") != "idle":
    raise SystemExit
lines = q.read_text().strip().splitlines()
text = ("[codex-heartbeat] " + "; ".join(lines[-8:]) +
        " — reconcile per the orchestrator skill (LEDGER, reports, decision doc). Not user input.")
r = subprocess.run(["herdr", "agent", "prompt", cfg["pane_id"], text], capture_output=True, text=True, timeout=30)
if r.returncode == 0:
    with open(S / "wake-sent.log", "a") as f:
        f.write(time.strftime("%FT%TZ", time.gmtime()) + " " + text + "\n")
    q.write_text("")
