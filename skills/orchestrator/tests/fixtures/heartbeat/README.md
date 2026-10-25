# Offline pane replay fixtures

These saved text fixtures reconstruct the banner shapes described in the
2026-10-25 heartbeat task. They are synthetic, credential-free inputs; no live
pane was read. `wrapped.txt` breaks both matching phrases over physical lines,
`truncated.txt` omits the reset time, and `complete.txt` supplies an explicit
calendar date. Tests set TZ=UTC and a deterministic epoch for boundary checks.

The command doubles follow the repository helper's status summary columns and
JSON `jobs`, `error`, and `finish_complete` fields. The suite exercises the Bash
entrypoint as a subprocess against a temporary SUP directory. It never executes
the live supervisor or real Herdr/quota commands.
