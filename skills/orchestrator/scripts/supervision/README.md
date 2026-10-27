# Desktop supervision sources

The canonical heartbeat is [`../codex-heartbeat.sh`](../codex-heartbeat.sh),
with Q9 adoption proposed in this candidate. The historical n3 source was
reviewed at `d6c2a7e`; the hashes below describe that earlier live comparison,
not deployment of this candidate. D9's older copy in this directory was removed
on rebase. The wake helper remains a byte-for-byte desktop
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
both clear quota and capacity wait/sent markers. Quota continuation uses the
managed helper and its shared recovery budget;
`run/quota-recovery/JOB.json` supplies coordinator artifact/effect reconciliation.
See [the record contract](../../references/quota-recovery.md#heartbeat-adoption-and-effect-reconciliation).
All observing/launching helper calls defer fresh quota recovery. Pending effects
and spent budgets never fall through to capacity input. Capacity alone retains
its existing five-minute backoff and two-second pause, using `capacity-JOB.until`
and `.sent`. Legacy quota markers hold recovery until reconciled. Finish
requires explicit `finish_complete: true` and a nonempty top-level `jobs` list;
each job needs both `pane_id` and `workspace_closed`, with a null pane or true
closure. Marker and workspace cleanup remain separate from coordinator acceptance.

Heartbeat tests share the canonical offline replay harness in
`../../tests/test_codex_heartbeat.py`, including D9's distinct lifecycle checks.
`../../tests/test_supervision.py` covers wake-helper identity and queue handling.

Quota detection now comes from positive-budget managed status observations and
Q9's `continue` preflight, both using the shared classifier. The heartbeat no
longer parses quota banners or chooses a reset timestamp. Q9 journals intent
before input and leaves ambiguous delivery unresolved; the supervisor retains
that result instead of replaying it. The candidate requires the matching helper
with `--defer-quota-recovery` and `continue --reconciliation` support. Live
validation and deployment require a separate authorized action.
