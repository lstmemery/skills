# Deliverables manifest

Use `research/<project>/CURRENT.md` under the workspace root as the current
deliverables manifest for each ongoing human-in-the-loop project.

For HITL task completion, the final report and `outcome.md` must each name
verified and unverified access paths in separate lists. Track each relevant
path independently in the completion checklist; only mark a path verified when
that path was checked. A successful helper or an incomplete session does not
prove verification. If the user closes the pane early, record a partial outcome
with the paths verified so far, the paths still unverified, and any paths whose
status is unknown.

Update it whenever the coordinator delivers a human-facing artifact for an
ongoing project. Replace the superseded entry and retain that generation's path
in the new entry's `Supersedes` field. Record the artifact's role, absolute
path, delivery status (`draft/unsent` or `sent`), and application status
(`pending` or `applied`). Set the `Updated` line to the timestamp and coordinator
identity.

For “where is the latest …” questions and transfer requests, read
`CURRENT.md` first. For a transfer, copy every listed artifact and verify each
entry at the destination by checking that it exists and comparing its SHA-256
with the source. Report any missing manifest or failed verification; do not
guess from run directories or prior handoffs.

Start from [the current manifest template](../templates/current-manifest.md).
