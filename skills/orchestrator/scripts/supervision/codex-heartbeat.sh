#!/bin/bash
# One supervision pass over every managed run listed in active-runs.txt (one run dir per line;
# the dir holds manifest/out-*/run like d1024b-exec). Driven by codex-heartbeat.timer (systemd
# --user, every 2 min) so it survives the coordinator session. Per run it:
#   - resumes the helper, labels new panes "codex · <run> <job>"
#   - on a Codex usage-limit banner records the reset time, and at reset+5 min sends ONE
#     same-pane continuation (text, 2 s pause, Enter — immediate Enter does not submit)
#   - feeds backlog/task-<job>.md files (ORDER file) while fewer than 4 jobs are active
#   - runs finish when every job has DONE, then drops the run from active-runs.txt
set -u
SUP=/home/deadhand/.local/share/orchestrator/supervision
H=/home/deadhand/.claude/skills/orchestrator/scripts/herdr-jobs.py
HC=/home/deadhand/.config/orchestrator/host-contract.json
P=/home/deadhand/.claude/skills/orchestrator/launch-policy.json
CAP=4
MSG="The usage limit has reset. Continue the task from where you stopped; do not repeat changes already made."
export HERDR_SOCKET_PATH=/home/deadhand/.config/herdr/herdr.sock HERDR_ENV=1
export PATH=/home/deadhand/.local/bin:/home/deadhand/.local/npm-global/bin:/usr/local/bin:/usr/bin:/bin
exec 9>"$SUP/codex-heartbeat.lock"; flock -n 9 || exit 0
log() { echo "$(date -u +%FT%TZ) $*" >> "$R/heartbeat.log"; }
wake() { echo "$(date -u +%H:%MZ) $(basename "$R"): $*" >> "$SUP/wake-queue.txt"; }

[ -f "$SUP/active-runs.txt" ] || exit 0
for R in $(cat "$SUP/active-runs.txt"); do
  [ -d "$R/run" ] || continue
  cd "$R" || continue
  name=$(basename "$R")
  python3 $H resume --run-dir run --wait-seconds 20 > last-resume.json 2>&1
  S=$(python3 $H status --run-dir run --summary --wait-seconds 0 2>&1)
  echo "$S" | grep -q '^job ' || { log "status-failed: $(echo $S | head -c 120)"; continue; }
  rows=$(echo "$S" | awk -F'|' 'NR>2 {gsub(/ /,"",$1);gsub(/ /,"",$2);print $1,$2}')
  active=0; now=$(date -u +%s)
  while read -r j p; do
    [ -f "out-$j/DONE" ] && { [ -f "out-$j/.woke" ] || { touch "out-$j/.woke"; wake "$j DONE"; }; continue; }
    [ "$p" = "-" ] && continue
    active=$((active+1)); w=${p%%:*}
    grep -qx "$w" labeled 2>/dev/null || { herdr workspace rename "$w" "codex · $name $j" >/dev/null && echo "$w" >> labeled; }
    txt=$(herdr pane read "$p" --source recent-unwrapped --lines 15 2>/dev/null)
    if echo "$txt" | grep -qi "usage limit" && ! echo "$txt" | grep -q "Working ("; then
      until_f="quota-$j.until"
      if [ ! -f "$until_f" ]; then
        when=$(echo "$txt" | tr '\n' ' ' | grep -oE "try again at [^.]*[AP]M" | tail -1 | sed -e 's/try again at //' -e 's/\([0-9]\)\(st\|nd\|rd\|th\),/\1/')
        t=$(date -d "$when" +%s 2>/dev/null) || t=$((now+3600))
        [ "$t" -lt "$((now-600))" ] && t=$((t+86400))
        echo "$t" > "$until_f"; log "BANNER $j $p reset=$(date -u -d @$t +%FT%TZ) ($when)"; wake "$j hit Codex usage limit, auto-continue at $(date -u -d @$((t+300)) +%H:%MZ)"
      fi
      if [ "$now" -ge "$(( $(cat "$until_f") + 300 ))" ]; then
        herdr pane send-text "$p" "$MSG" && sleep 2 && herdr pane send-keys "$p" Enter
        rm -f "$until_f"; log "continued $j $p"; wake "$j continued after quota reset"
      fi
    else
      rm -f "quota-$j.until"
    fi
  done <<< "$rows"
  # feed the backlog only when no job is waiting on quota
  if [ -f ORDER ] && ! ls quota-*.until >/dev/null 2>&1; then
    for j in $(cat ORDER); do
      [ "$active" -ge "$CAP" ] && break
      echo "$rows" | awk '{print $1}' | grep -qx "$j" && continue
      [ -f "backlog/task-$j.md" ] || continue
      cp "backlog/task-$j.md" "task-$j.md"; mkdir -p "out-$j"
      req=$(python3 -c 'import json;print(json.load(open("run/state.json"))["request_id"])' 2>/dev/null || echo "$name")
      python3 - "$j" "$req" <<'PY'
import json,sys
j,req=sys.argv[1],sys.argv[2]
json.dump({"schema_version":1,"request_id":req,"concurrency":4,"jobs":[{"job_id":j,"name":j,"task_kind":"ordinary","task_file":f"task-{j}.md","cwd":".","output_expectation":f"out-{j}/report.md and out-{j}/DONE","writes_repository":False,"editable_paths":[f"out-{j}","/home/deadhand/.local/share/orchestrator/worktrees"],"override":{"runtime":"codex","model":"gpt-6.1-sol","provider":"codex","effort":"high","instruction":"Matt AFK authorization: retros, audits and skill improvements while Codex quota allows."}}]},open(f"add-{j}.json","w"))
PY
      python3 $H add --run-dir run --host-contract $HC --manifest "add-$j.json" --policy $P --wait-seconds 30 > "add-$j.out" 2>&1
      if python3 -c "import json,sys;sys.exit(0 if json.load(open(sys.argv[1])).get(\"error\") else 1)" "add-$j.out"; then log "add-failed $j: $(head -c 150 add-$j.out)"; wake "adding $j failed"; break; fi
      log "added $j"; active=$((active+1))
    done
  fi
  rows=$(python3 $H status --run-dir run --summary --wait-seconds 0 2>&1 | awk -F'|' 'NR>2 {gsub(/ /,"",$1);print $1}')
  n=0; t=0; for j in $rows; do t=$((t+1)); [ -f "out-$j/DONE" ] && n=$((n+1)); done
  pend=0; [ -f ORDER ] && for j in $(cat ORDER); do echo "$rows" | grep -qx "$j" || pend=1; done
  log "done=$n/$t active=$active backlog_pending=$pend"
  if [ "$pend" -eq 0 ] && [ "$n" -ge "$t" ] && [ "$t" -gt 0 ]; then
    python3 $H finish --run-dir run --host-contract $HC --wait-seconds 10 > last-finish.json 2>&1
    if ! python3 -c 'import json,sys;d=json.load(open("last-finish.json"));j=d.get("jobs",d.get("checkpoint",{}).get("jobs",[]));sys.exit(0 if j and all(x.get("workspace_closed") for x in j) else 1)'; then
      log "finish left open workspaces; retrying next pass"; continue
    fi
    log "finished; removed from active-runs"; wake "all $t jobs DONE and finished — review reports"
    grep -vx "$R" "$SUP/active-runs.txt" > "$SUP/active-runs.tmp"; mv "$SUP/active-runs.tmp" "$SUP/active-runs.txt"
  fi
done
/home/deadhand/.local/share/orchestrator/supervision/wake-coordinator.py
