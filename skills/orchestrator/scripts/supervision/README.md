# Desktop supervision sources

The canonical heartbeat is [`../codex-heartbeat.sh`](../codex-heartbeat.sh),
reviewed with n3 and present in main at `d6c2a7e`. D9's older copy in this
directory was removed on rebase. The wake helper remains a byte-for-byte desktop
snapshot. Read-only comparison on 2026-10-26 found these SHA-256 values:

| Repository file | Live source | SHA-256 |
|---|---|---|
| `../codex-heartbeat.sh` | `/home/deadhand/.local/share/orchestrator/supervision/codex-heartbeat.sh` | `0cc651f4bd58b5185ac631264bbacfe60a8ee868e77a92eea06b6f12207a7bbc` |
| `wake-coordinator.py` | `/home/deadhand/.local/share/orchestrator/supervision/wake-coordinator.py` | `aca87f87a0080f347db2b3686bf0a6ab25e0fbd216e0defb3fc6cc7e2f422353` |

Read [the unattended reference](../../references/unattended.md#desktop-codex-heartbeat)
before use. Paths remain specific to Matt's desktop. Loading this skill does
not install scripts, write enrollment or coordinator state, or enable/restart
systemd units. Do not execute either live script for validation: they control
live workers. Offline tests override commands and paths; shell syntax checks use
`bash -n`, and Python compilation does not run module-level code.

The heartbeat preserves the stored `request.concurrency` and reads each job's
`backlog/route-<job>.json`, defaulting to Codex `gpt-6-luna`/low. Fresh quota must
exceed 10% in every reported window before either resume or add may launch work.
Failed or malformed helper responses block feeding; partial status results still
allow existing workers to be supervised. It retains the desktop executable
directories in `PATH` and uses `coordinator.json` for both the worker split origin
and the wake target. Keep the recorded identity current during succession.

`CANCELLED` markers skip pane supervision and count as terminal beside `DONE`;
both clear quota wait/sent markers. Successful continuation retains a sent marker
while the banner persists, with a default two-second pause before Enter. Finish
requires explicit `finish_complete: true` and a nonempty top-level `jobs` list;
each job needs both `pane_id` and `workspace_closed`, with a null pane or true
closure. Marker and workspace cleanup remain separate from coordinator acceptance.

Heartbeat tests share the canonical offline replay harness in
`../../tests/test_codex_heartbeat.py`, including D9's distinct lifecycle checks.
`../../tests/test_supervision.py` covers wake-helper identity and queue handling.

Pending follow-up: replace the heartbeat's banner grep with the shared s1 Codex
detector. s1 is already merged via `72e7710`; integration remains pending. Use
`herdr_jobs.admission.rate_limit_from_output` with `source="pane"` and
`runtime="codex"`, then validate recovery before a separately authorized
deployment. This rebase leaves n3's heartbeat and the live installation unchanged.
