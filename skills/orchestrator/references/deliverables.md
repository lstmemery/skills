# Deliverables manifest

Use `research/<project>/CURRENT.md` under the workspace root as the current
deliverables manifest for each ongoing human-in-the-loop project.

For HITL task completion, follow the single access-path verification policy in
the [worker contract](workers.md#hitl-access-path-completion). Apply it to the
final report and `outcome.md` listed here.

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
