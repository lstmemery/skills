#!/bin/bash
# One supervision pass over every managed run listed in active-runs.txt (one run dir per line;
# the dir holds manifest/out-*/run like d1024b-exec). Driven by codex-heartbeat.timer (systemd
# --user, every 2 min) so it survives the coordinator session. Per run it:
#   - resumes the helper, labels new panes "codex · <run> <job>"
#   - delegates classified Codex quota recovery to the journaled managed continue operation
#     after coordinator reconciliation; ambiguous delivery is never replayed
#   - on an idle Codex model-capacity banner sends ONE continuation after a 5 min backoff
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
CAPACITY_MSG="The provider retry wait has elapsed. Continue the task from where you stopped; do not repeat changes already made."
export HERDR_SOCKET_PATH=/home/deadhand/.config/herdr/herdr.sock HERDR_ENV=1
export PATH=${HEARTBEAT_PATH:-/home/deadhand/.local/bin:/home/deadhand/.local/npm-global/bin:/usr/local/bin:/usr/bin:/bin}:${PATH:-}
# herdr-jobs splits the *current* pane to create workers; systemd has none, so use the coordinator's.
export HERDR_PANE_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["pane_id"])' "$SUP/coordinator.json")
export HERDR_WORKSPACE_ID=${HERDR_PANE_ID%%:*}
exec 9>"$SUP/codex-heartbeat.lock"; flock -n 9 || exit 0
log() { echo "$(date -u +%FT%TZ) $*" >> "$R/heartbeat.log"; }
wake() { echo "$(date -u +%H:%MZ) $(basename "$R"): $*" >> "$SUP/wake-queue.txt"; }

# Exit 6 can mean a delivered start whose acknowledgement was lost. It must
# still reserve capacity, but need not block unrelated backlog. Never replay it.
helper_launch_safe() {
  python3 - "$1" "$2" <<'PY_SAFE'
import json,sys
try:
  d=json.load(open(sys.argv[1]))
  rc=int(sys.argv[2])
  assert isinstance(d,dict) and not d.get("error") and isinstance(d["jobs"],list)
  if rc == 0:
    assert not any(j.get("rate_limit_seen") or (j.get("observed") or {}).get("rate_limit") for j in d["jobs"])
    sys.exit(0)
  assert rc == 6 and d["batch_state"] == "unresolved_effect" and d["jobs"]
  assert any(j["pending_effect"] is not None for j in d["jobs"])
  for j in d["jobs"]:
    o=j["observed"]
    assert o["identity_verified"] is True and not o.get("rate_limit")
    assert isinstance(j["pane_id"],str) and j["pane_id"]
    if j["pending_effect"] is not None:
      assert j["pending_effect"]["action"] == "start"
      assert j["route"] == {"mode":"agent","runtime":"codex"}
      assert o["state"] == "working"
      assert j["issue_code"] == "effect_delivery_unproven"
    else:
      assert not j["issue"] and o["state"] in ("working","idle","done","exited")
except (OSError,KeyError,TypeError,ValueError,AssertionError):
  sys.exit(1)
PY_SAFE
}

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
  launches_ok=1
  # resume can launch pending jobs, so it must obey the same quota gate as add.
  if [ "$qleft" -gt 10 ]; then
    resume_rc=0
    python3 "$H" resume --run-dir run --defer-quota-recovery --wait-seconds 20 > last-resume.json 2>&1 || resume_rc=$?
    if ! helper_launch_safe last-resume.json "$resume_rc"; then
      log "resume-failed (see last-resume.json); observing existing jobs only"; launches_ok=0
    fi
  fi
  status_rc=0
  python3 "$H" status --run-dir run --defer-quota-recovery --wait-seconds 20 > last-status.json 2>&1 || status_rc=$?
  # Use current structured observations for admission; the human summary omits
  # identity and pending-effect evidence. Partial results still allow supervision.
  case "$status_rc" in
    0|6|10) ;;
    *) log "status-failed"; continue ;;
  esac
  helper_launch_safe last-status.json "$status_rc" || launches_ok=0
  rows=$(python3 - <<'PY_ROWS' 2>>heartbeat.log
import json,sys
with open("last-status.json") as f:
  d=json.load(f)
assert not d.get("error") and isinstance(d["jobs"],list)
for j in d["jobs"]:
  if j.get("collection_error"):
    print(f'collection-error {j["job_id"]}: {j["collection_error"]}',file=sys.stderr)
  o=j.get("observed") or {}
  quota=bool(j.get("rate_limit_seen") or o.get("rate_limit"))
  pending=j.get("pending_effect")
  blocked=bool(j.get("rate_limit_recoveries",0) or j.get("continuation_count",0)
               or (pending and (not quota or pending["action"] not in ("start","prompt")))
               or j.get("settled") or j.get("collection")
               or j.get("workspace_closed"))
  print(j["job_id"],j["pane_id"] or "-",int(quota),int(blocked))
PY_ROWS
  ) || { log "status-failed: invalid job records"; continue; }
  active=0; now=${HEARTBEAT_NOW:-$(date -u +%s)}
  while read -r j p quota recovery_blocked; do
    [ -n "$j" ] && [ -n "$p" ] || continue
    if [ -f "out-$j/DONE" ] || [ -f "out-$j/CANCELLED" ]; then
      rm -f "quota-$j.until" "quota-$j.until.sent" "capacity-$j.until" "capacity-$j.until.sent"
    fi
    [ -f "out-$j/CANCELLED" ] && continue
    [ -f "out-$j/DONE" ] && { [ -f "out-$j/.woke" ] || { touch "out-$j/.woke"; wake "$j DONE"; }; continue; }
    [ "$p" = "-" ] && continue
    active=$((active+1)); w=${p%%:*}
    grep -qx "$w" labeled 2>/dev/null || { "$HERDR" workspace rename "$w" "codex · $name $j" >/dev/null && echo "$w" >> labeled; }
    # Legacy wait/sent markers cannot prove whether old unjournaled input landed.
    if [ -f "quota-$j.until" ] || [ -f "quota-$j.until.sent" ]; then
      launches_ok=0; log "legacy quota recovery held $j $p; reconcile prior delivery"; continue
    fi
    if [ "$quota" -eq 1 ]; then
      launches_ok=0
      if [ "$recovery_blocked" -eq 1 ]; then
        log "quota recovery held $j $p; budget spent or effect unresolved"
        wake "$j quota recovery held; inspect saved effects and recovery budget"
        continue
      fi
      continue_rc=0
      python3 "$H" continue --run-dir run --job "$j" --defer-quota-recovery --wait-seconds 30 \
        --reconciliation "run/quota-recovery/$j.json" > "last-continue-$j.json" 2>&1 || continue_rc=$?
      if [ "$continue_rc" -eq 0 ] && python3 - "$j" "$p" <<'PY_CONTINUE'
import json,sys
try:
  d=json.load(open(f"last-continue-{sys.argv[1]}.json"))
  assert not d.get("error")
  j=next(j for j in d["jobs"] if j["job_id"] == sys.argv[1])
  before=json.load(open("last-status.json"))
  original=next(item for item in before["jobs"] if item["job_id"] == sys.argv[1])
  assert j["attempt_id"] == original["attempt_id"]
  o=j["observed"]
  assert j["pane_id"] == sys.argv[2] and j["pending_effect"] is None
  assert j["rate_limit_recoveries"] == 1 and j["continuation_count"] == 1
  assert o["identity_verified"] is True and o["state"] == "working" and o["prompt_verified"] is True
except (OSError,KeyError,TypeError,ValueError,StopIteration,AssertionError):
  sys.exit(1)
PY_CONTINUE
      then
        log "continued $j $p through managed quota recovery"; wake "$j continued through journaled quota recovery"
      else
        log "quota continuation held $j $p exit=$continue_rc (see last-continue-$j.json); inspect before further action"
        wake "$j quota recovery waiting or unresolved; inspect last-continue-$j.json"
      fi
      continue
    fi
    [ "$recovery_blocked" -eq 1 ] && continue
    txt=$("$HERDR" pane read "$p" --source recent-unwrapped --lines 15 2>/dev/null)
    txt=$(printf '%s\n' "$txt" | tr '\n\r' '  ' | tr -s '[:space:]' ' ')
    if ! echo "$txt" | grep -q "Working (" && echo "$txt" | grep -qiF "Selected model is at capacity. Please try a different model."; then
      until_f="capacity-$j.until"
      if [ ! -f "$until_f" ]; then
        echo "$now" > "$until_f"; log "CAPACITY $j $p backoff=300s"; wake "$j hit Codex model capacity, auto-continue in five minutes"
      fi
      if [ ! -f "$until_f.sent" ] && [ "$now" -ge "$(( $(cat "$until_f") + 300 ))" ]; then
        if "$HERDR" pane send-text "$p" "$CAPACITY_MSG" && sleep "${HEARTBEAT_SEND_DELAY:-2}" && "$HERDR" pane send-keys "$p" Enter; then
          touch "$until_f.sent"; log "continued $j $p"; wake "$j continued after capacity wait"
        else
          log "continue-failed $j $p"
        fi
      fi
    else
      rm -f "capacity-$j.until" "capacity-$j.until.sent"
    fi
  done <<< "$rows"
  if [ "$launches_ok" -eq 1 ] && [ -f ORDER ] && ! compgen -G "quota-*.until*" >/dev/null && ! compgen -G "capacity-*.until*" >/dev/null && [ "${qleft:--1}" -gt 10 ]; then
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
      if ! python3 "$H" add --run-dir run --defer-quota-recovery --host-contract "$HC" --manifest "add-$j.json" --policy "$P" --wait-seconds 30 > "add-$j.out" 2>&1 ||
         ! python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if isinstance(d,dict) and not d.get("error") and isinstance(d.get("jobs"),list) else 1)' "add-$j.out"; then
        log "add-failed $j (see add-$j.out)"; wake "adding $j failed"; break
      fi
      log "added $j"; active=$((active+1))
    done
  fi
  status_rc=0
  S=$(python3 "$H" status --run-dir run --defer-quota-recovery --summary --wait-seconds 0 2>&1) || status_rc=$?
  case "$status_rc" in 0|6|10) ;; *) log "status-failed before finish"; continue ;; esac
  echo "$S" | grep -q '^job ' || { log "status-failed before finish"; continue; }
  rows=$(echo "$S" | awk -F'|' 'NR>2 {gsub(/ /,"",$1);print $1}')
  n=0; t=0; for j in $rows; do t=$((t+1)); { [ -f "out-$j/DONE" ] || [ -f "out-$j/CANCELLED" ]; } && n=$((n+1)); done
  pend=0; [ -f ORDER ] && for j in $(cat ORDER); do echo "$rows" | grep -qx "$j" || pend=1; done
  log "done=$n/$t active=$active backlog_pending=$pend"
  if [ "$pend" -eq 0 ] && [ "$n" -ge "$t" ] && [ "$t" -gt 0 ]; then
    finish_rc=0
    python3 "$H" finish --run-dir run --defer-quota-recovery --host-contract "$HC" --wait-seconds 10 > last-finish.json 2>&1 || finish_rc=$?
    if { [ "$finish_rc" -ne 0 ] && [ "$finish_rc" -ne 6 ] && [ "$finish_rc" -ne 10 ]; } ||
       ! python3 -c 'import json,sys;d=json.load(open("last-finish.json"));j=d["jobs"];sys.exit(0 if not d.get("error") and d["finish_complete"] is True and isinstance(j,list) and j and all(isinstance(x,dict) and "pane_id" in x and "workspace_closed" in x and (x["workspace_closed"] is True or x["pane_id"] is None) for x in j) else 1)'; then
      log "finish left open workspaces; retrying next pass"; continue
    fi
    log "finished; removed from active-runs"; wake "all $t jobs DONE and finished — review reports"
    grep -Fvx -- "$R" "$SUP/active-runs.txt" > "$SUP/active-runs.tmp"; mv "$SUP/active-runs.tmp" "$SUP/active-runs.txt"
  fi
done < "$SUP/active-runs.txt"
"$WAKE_COORDINATOR"
