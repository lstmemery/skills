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
