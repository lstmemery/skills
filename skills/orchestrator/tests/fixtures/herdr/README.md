# Herdr response samples

The JSON files in this directory are read-only Herdr 0.9.3 samples captured
from `herdr status --json`, `herdr agent get <existing-pane-id>`, and
`herdr workspace list`. Every returned string is redacted except public enum,
status, and version values. Socket paths, IDs, paths, labels, and names are not
recorded.

The agent sample came from an auto-detected agent and has no `name` field. The
helper must verify `result.agent.name` against a named worker created by that
helper during host binding; this worker-only field cannot be established from
the read-only sample. The `created_workspace` shape is covered with a fake
response in the unit test, using the installed Herdr 0.9.3 `PaneMoveResult`
schema. Capturing a live move response requires a mutating host exercise and
remains an operator verification step.

`codex-usage-limit.txt` is a synthetic, secret-free pane snapshot for the
usage-limit banner reported in LEDGER.md at 2026-10-25T02:17:49Z. It preserves
the Codex error-square prefix and the reported 8:10 PM reset clock; surrounding
UI and task text are reconstructed, not a historical raw capture.
