#!/bin/bash
# One supervision pass over every managed run listed in active-runs.txt (one run dir per line;
# the dir holds manifest/out-*/run like d1024b-exec). Driven by codex-heartbeat.timer (systemd
# --user, every 2 min) so it survives the coordinator session. Per run it:
#   - resumes the helper, labels new panes "codex · <run> <job>"
#   - on a Codex usage-limit banner records the reset time, and at reset+5 min sends ONE
#     same-pane continuation (text, 2 s pause, Enter — immediate Enter does not submit)
#   - feeds backlog/task-<job>.md files (ORDER file) up to the stored run concurrency
#   - runs finish when every job has DONE, then drops the run from active-runs.txt
set -uo pipefail
SUP=${SUP:-/home/deadhand/.local/share/orchestrator/supervision}
H=${HERDR_JOBS:-/home/deadhand/.claude/skills/orchestrator/scripts/herdr-jobs.py}
HERDR=${HERDR:-herdr}
QUOTA_AXI=${QUOTA_AXI:-quota-axi}
WAKE_COORDINATOR=${WAKE_COORDINATOR:-$SUP/wake-coordinator.py}
HC=${HC:-/home/deadhand/.config/orchestrator/host-contract.json}
P=${P:-/home/deadhand/.claude/skills/orchestrator/launch-policy.json}
MSG="The usage limit has reset. Continue the task from where you stopped; do not repeat changes already made."
export HERDR_SOCKET_PATH=/home/deadhand/.config/herdr/herdr.sock HERDR_ENV=1
export PATH=${HEARTBEAT_PATH:-/home/deadhand/.local/bin:/home/deadhand/.local/npm-global/bin:/usr/local/bin:/usr/bin:/bin}:${PATH:-}
# herdr-jobs splits the *current* pane to create workers; systemd has none, so use the coordinator's.
export HERDR_PANE_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pane_id"])' "$SUP/coordinator.json")
export HERDR_WORKSPACE_ID=${HERDR_PANE_ID%%:*}
exec 9>"$SUP/codex-heartbeat.lock"; flock -n 9 || exit 0
log() { echo "$(date -u +%FT%TZ) $*" >> "$R/heartbeat.log"; }
wake() { echo "$(date -u +%H:%MZ) $(basename "$R"): $*" >> "$SUP/wake-queue.txt"; }

[ -f "$SUP/active-runs.txt" ] || exit 0
while IFS= read -r R || [ -n "$R" ]; do
  [ -d "$R/run" ] || continue
  cd "$R" || continue
  name=$(basename "$R")
  # Matt 2026-10-25: never start new Codex workers at >=90% quota used (<=10% left in any window).
  # Unknown or stale quota counts as "no".
  qleft=$("$QUOTA_AXI" --provider codex --json --no-credential-refresh 2>/dev/null | python3 -c '
import json,sys,math
try:
  p=json.load(sys.stdin)["providers"][0]
  assert p["state"]["stale"] is False
  values=[w["percentRemaining"] for w in p["windows"]]
  assert values and all(type(v) in (int,float) and math.isfinite(v) and 0 <= v <= 100 for v in values)
  # Ceil preserves the exact >10 and >30 decisions in shell integer comparisons.
  print(math.ceil(min(values)))
except (KeyError,IndexError,TypeError,ValueError,AssertionError): print(-1)') || qleft=-1
  if [ "${qleft:--1}" -le 10 ]; then
    [ -f ORDER ] && log "quota gate: codex ${qleft}% left (<=10 or unknown); not starting new workers"
    # Matt 2026-10-25: at the quota limit, wake the coordinator once per window to write a decision doc.
    if [ ! -f "$SUP/quota-gate.notified" ]; then touch "$SUP/quota-gate.notified"; wake "QUOTA GATE: codex ${qleft}% left - write the decision doc with Matt's unblock tasks, then wait for the reset"; fi
  elif [ "${qleft:--1}" -gt 30 ]; then rm -f "$SUP/quota-gate.notified"
  fi
  CAP=$(python3 -c 'import json; c=json.load(open("run/state.json"))["request"]["concurrency"]; assert type(c) is int and c > 0; print(c)' 2>/dev/null) || { log "invalid stored concurrency"; continue; }
  # resume can launch pending jobs, so it must obey the same quota gate as add.
  if [ "$qleft" -gt 10 ]; then
    if ! python3 "$H" resume --run-dir run --wait-seconds 20 > last-resume.json 2>&1 ||
       ! python3 -c 'import json,sys;d=json.load(open("last-resume.json"));sys.exit(0 if isinstance(d,dict) and not d.get("error") and isinstance(d.get("jobs"),list) else 1)'; then
      log "resume-failed (see last-resume.json)"; continue
    fi
  fi
  S=$(python3 "$H" status --run-dir run --summary --wait-seconds 0 2>&1) || { log "status-failed"; continue; }
  echo "$S" | grep -q '^job ' || { log "status-failed: $(echo $S | head -c 120)"; continue; }
  rows=$(echo "$S" | awk -F'|' 'NR>2 {gsub(/ /,"",$1);gsub(/ /,"",$2);print $1,$2}')
  active=0; now=${HEARTBEAT_NOW:-$(date -u +%s)}
  while read -r j p; do
    [ -n "$j" ] && [ -n "$p" ] || continue
    if [ -f "out-$j/DONE" ] || [ -f "out-$j/CANCELLED" ]; then
      rm -f "quota-$j.until" "quota-$j.until.sent"
    fi
    [ -f "out-$j/CANCELLED" ] && continue
    [ -f "out-$j/DONE" ] && { [ -f "out-$j/.woke" ] || { touch "out-$j/.woke"; wake "$j DONE"; }; continue; }
    [ "$p" = "-" ] && continue
    active=$((active+1)); w=${p%%:*}
    grep -qx "$w" labeled 2>/dev/null || { "$HERDR" workspace rename "$w" "codex · $name $j" >/dev/null && echo "$w" >> labeled; }
    txt=$("$HERDR" pane read "$p" --source recent-unwrapped --lines 15 2>/dev/null)
    txt=$(printf '%s\n' "$txt" | tr '\n\r' '  ' | tr -s '[:space:]' ' ')
    if echo "$txt" | grep -qiE "usage limit|hit your usage" && ! echo "$txt" | grep -q "Working ("; then
      until_f="quota-$j.until"
      if [ ! -f "$until_f" ]; then
        when=$(echo "$txt" | tr '\n' ' ' | grep -oE "try again at [^.]*[AP]M" | tail -1 | sed -e 's/try again at //' -e 's/\([0-9]\)\(st\|nd\|rd\|th\),/\1/')
        t=""
        [ -n "$when" ] && t=$(date -d "$when" +%s 2>/dev/null)
        [ -n "$t" ] || t=$((now+3600))
        [ "$t" -lt "$((now-600))" ] && t=$((t+86400))
        echo "$t" > "$until_f"; log "BANNER $j $p reset=$(date -u -d @$t +%FT%TZ) ($when)"; wake "$j hit Codex usage limit, auto-continue at $(date -u -d @$((t+300)) +%H:%MZ)"
      fi
      if [ ! -f "$until_f.sent" ] && [ "$now" -ge "$(( $(cat "$until_f") + 300 ))" ]; then
        if "$HERDR" pane send-text "$p" "$MSG" && sleep "${HEARTBEAT_SEND_DELAY:-2}" && "$HERDR" pane send-keys "$p" Enter; then
          touch "$until_f.sent"; log "continued $j $p"; wake "$j continued after quota reset"
        else
          log "continue-failed $j $p"
        fi
      fi
    else
      rm -f "quota-$j.until" "quota-$j.until.sent"
    fi
  done <<< "$rows"
  if [ -f ORDER ] && ! ls quota-*.until >/dev/null 2>&1 && [ "${qleft:--1}" -gt 10 ]; then
    for j in $(cat ORDER); do
      [ "$active" -ge "$CAP" ] && break
      echo "$rows" | awk '{print $1}' | grep -qx "$j" && continue
      [ -f "backlog/task-$j.md" ] || continue
      cp "backlog/task-$j.md" "task-$j.md"; mkdir -p "out-$j"
      req=$(python3 -c 'import json;print(json.load(open("run/state.json"))["request_id"])' 2>/dev/null || echo "$name")
      python3 - "$j" "$req" <<'PY'
import json,sys,os
j,req=sys.argv[1],sys.argv[2]
extra=[l.strip() for l in open(f"backlog/editable-{j}.txt")] if os.path.exists(f"backlog/editable-{j}.txt") else []
# ladder route: backlog/route-<job>.json holds the ladder row as an explicit tuple (Matt 2026-10-25)
route=json.load(open(f"backlog/route-{j}.json")) if os.path.exists(f"backlog/route-{j}.json") else {"runtime":"codex","model":"gpt-6-luna","provider":"codex","effort":"low","instruction":"Ladder level 1 default (no route file)."}
conc=json.load(open("run/state.json"))["request"]["concurrency"]
json.dump({"schema_version":1,"request_id":req,"concurrency":conc,"jobs":[{"job_id":j,"name":j,"task_kind":"ordinary","task_file":f"task-{j}.md","cwd":".","output_expectation":f"out-{j}/report.md and out-{j}/DONE","writes_repository":False,"editable_paths":[f"out-{j}","/home/deadhand/.local/share/orchestrator/worktrees"]+[e for e in extra if e],"override":route}]},open(f"add-{j}.json","w"))
PY
      if ! python3 "$H" add --run-dir run --host-contract "$HC" --manifest "add-$j.json" --policy "$P" --wait-seconds 30 > "add-$j.out" 2>&1 ||
         ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(d,dict) and not d.get("error") and isinstance(d.get("jobs"),list) else 1)' "add-$j.out"; then
        log "add-failed $j (see add-$j.out)"; wake "adding $j failed"; break
      fi
      log "added $j"; active=$((active+1))
    done
  fi
  rows=$(python3 "$H" status --run-dir run --summary --wait-seconds 0 2>&1 | awk -F'|' 'NR>2 {gsub(/ /,"",$1);print $1}')
  n=0; t=0; for j in $rows; do t=$((t+1)); { [ -f "out-$j/DONE" ] || [ -f "out-$j/CANCELLED" ]; } && n=$((n+1)); done
  pend=0; [ -f ORDER ] && for j in $(cat ORDER); do echo "$rows" | grep -qx "$j" || pend=1; done
  log "done=$n/$t active=$active backlog_pending=$pend"
  if [ "$pend" -eq 0 ] && [ "$n" -ge "$t" ] && [ "$t" -gt 0 ]; then
    if ! python3 "$H" finish --run-dir run --host-contract "$HC" --wait-seconds 10 > last-finish.json 2>&1 ||
       ! python3 -c 'import json,sys;d=json.load(open("last-finish.json"));j=d.get("jobs",[]);sys.exit(0 if not d.get("error") and d.get("finish_complete") is True and j and all(x.get("workspace_closed") is True or not x.get("pane_id") for x in j) else 1)'; then
      log "finish left open workspaces; retrying next pass"; continue
    fi
    log "finished; removed from active-runs"; wake "all $t jobs DONE and finished — review reports"
    grep -Fvx -- "$R" "$SUP/active-runs.txt" > "$SUP/active-runs.tmp"; mv "$SUP/active-runs.tmp" "$SUP/active-runs.txt"
  fi
done < "$SUP/active-runs.txt"
"$WAKE_COORDINATOR"
