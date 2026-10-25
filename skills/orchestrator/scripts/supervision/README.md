# Desktop supervision snapshot

These executable files are byte-for-byte copies observed on 2026-10-25 for D9:

| Vendored file | Live source | SHA-256 |
|---|---|---|
| `codex-heartbeat.sh` | `/home/deadhand/.local/share/orchestrator/supervision/codex-heartbeat.sh` | `43121cf1f6f2f044eb026bbe1b65fea2a560b8218a1296c7dde53a68fd2e0621` |
| `wake-coordinator.py` | `/home/deadhand/.local/share/orchestrator/supervision/wake-coordinator.py` | `aca87f87a0080f347db2b3686bf0a6ab25e0fbd216e0defb3fc6cc7e2f422353` |

Read [the unattended reference](../../references/unattended.md#desktop-codex-heartbeat)
before use. Paths, route, authorization text, and the backlog cap of 4 are pinned
to Matt's desktop batch. Loading this skill does not install these scripts,
write enrollment or coordinator state, or enable/restart any systemd unit.
Do not execute this snapshot for a syntax check: it controls live workers.
Offline tests redirect all host paths and stub Herdr/helper calls; syntax checks
use `bash -n` and Python compilation without running module-level code.

Pending follow-up: replace the heartbeat banner grep with the shared s1 Codex
detector from `d1024b/s1-codex-usage-banner` once merged. s1 is already in D9's
base via `72e7710`; integration into the live heartbeat remains pending. Use
`herdr_jobs.admission.rate_limit_from_output` with `source="pane"` and
`runtime="codex"`, then test the continuation policy before a separately
authorized deployment. This snapshot deliberately retains the observed regex
and its documented limits.
