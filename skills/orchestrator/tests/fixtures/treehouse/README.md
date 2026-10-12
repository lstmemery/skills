# Treehouse response sample

`get-lease.json` is a redacted copy of the live `treehouse get --lease --json`
response retained for the 2026-10-11 incident in the dogwalk run's
`pin-worktree.json`. `status-live.json` preserves the corresponding live status
fields, including a recovered slot with `recovery_reason`. Paths, lease IDs,
holders, and the recovery message are redacted; response fields and types are
preserved. The offline allocation test adapts the leased record to its fake
worktree and verifies that acquisition reconciles it through `status` before
running Git verification. The test does not invoke Treehouse `get` or `return`.
