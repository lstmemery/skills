# Desktop supervision snapshot

These executable files are byte-for-byte copies refreshed from the live sources
on 2026-10-25 for D9 sync:

| Vendored file | Live source | SHA-256 |
|---|---|---|
| `codex-heartbeat.sh` | `/home/deadhand/.local/share/orchestrator/supervision/codex-heartbeat.sh` | `95746fb8a458a7e53f3cb20bf1c9f49eb21f1bca4f0f1a0db7cad01fcbe26783` |
| `wake-coordinator.py` | `/home/deadhand/.local/share/orchestrator/supervision/wake-coordinator.py` | `aca87f87a0080f347db2b3686bf0a6ab25e0fbd216e0defb3fc6cc7e2f422353` |

Read [the unattended reference](../../references/unattended.md#desktop-codex-heartbeat)
before use. Paths, route, authorization text, and the backlog cap of 4 are pinned
to Matt's desktop batch. Loading this skill does not install these scripts,
write enrollment or coordinator state, or enable/restart any systemd unit.
Do not execute this snapshot for a syntax check: it controls live workers.
Offline tests redirect all host paths and stub Herdr/helper calls; syntax checks
use `bash -n` and Python compilation without running module-level code.

The refreshed heartbeat retains the desktop executable directories in `PATH`
(including the orchestrator binary) and checks the JSON `error` field from
managed `add`, so reported launch failures stop that pass's feeder and queue a
wake. It reads `HERDR_PANE_ID` from `coordinator.json` and derives
`HERDR_WORKSPACE_ID` from the pane prefix: systemd has no current pane, while
`herdr-jobs` splits the current pane to launch workers. Keep that recorded pane
current during coordinator succession; it is both the split origin and wake
target. `CANCELLED` markers skip worker supervision and count as terminal beside
`DONE`. During finish, a job with no pane needs no workspace closure; jobs with
panes still require `workspace_closed`. These rules avoid supervising cancelled
workers or waiting forever for a workspace that was never created. Marker and
workspace cleanup remain separate from coordinator acceptance.

Pending follow-up: replace the heartbeat banner grep with the shared s1 Codex
detector from `d1024b/s1-codex-usage-banner` once merged. s1 is already in D9's
base via `72e7710`; integration into the live heartbeat remains pending. Use
`herdr_jobs.admission.rate_limit_from_output` with `source="pane"` and
`runtime="codex"`, then test the continuation policy before a separately
authorized deployment. This snapshot deliberately retains the observed regex
and its documented limits.
