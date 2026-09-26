# Bind and verify the host

This package has offline verification. Its host binding is intentionally unconfigured. Complete these checks on the authorized host before adopting the managed-job skill branch. The jail cannot perform them.

## Bind the installed contract

Copy [host-contract.example.json](../examples/host-contract.example.json) to the host's chosen configuration location. The example contains no claims about installed response shapes.

1. Run the read-only discovery commands `herdr --help`, `herdr agent`, `herdr status`, `orchestrator help --json --compact`, and `orchestrator doctor --json --compact`. Capture only the outputs needed for the binding.
2. Hash the exact stdout bytes of the first, second, and fourth commands with SHA-256. Store those three digests. Future changes stop preflight for reverification.
3. From `herdr status`, bind JSON key paths for a stable identity of this server/session and its server version. Bind the version value. The identity must distinguish different servers that might reuse pane IDs.
4. From the installed agent command contract and a known owned worker, bind paths for lifecycle state, pane ID, kind, and name in `herdr agent get` output. Record only kinds verified as supported by this host. The adapter rejects an unrecognized state or mismatched worker identity.
5. Verify the documented split and move response paths: `result.pane.pane_id` and `result.move_result.pane.pane_id`. If the installed API differs, adjust the narrow adapter and its response fixtures before use.
6. For jail jobs, verify the actual launcher/export mechanism. This version supports a stable host directory that mirrors the worker workspace root and preserves job-specific relative paths. Bind `host_root` and `worker_root` only when that property has been demonstrated. Ensure the launcher returns after artifacts have been exported. If this host instead allocates dynamic per-container export paths, adapt `check_export` and the output binding before enabling jail work; do not invent a mapping or use publication markers as transport.
7. Check that the internal Python launcher monitor is recognized appropriately during the finite `omp-train --harness codex exec` run. Its host-side exit record must survive agent exit. Confirm native model arguments for every ordinary runtime/model combination to be enabled.

Set `verified: true` only after those facts are established. It is an operator assertion about a verified contract, not a script-generated attestation. No keys or private configuration snapshots belong in this file. A changed session, server version, or help digest stops execution rather than silently retargeting an existing run.

## Exercise the integration

Use a small authorized ordinary-agent job, a jail job, and then a mixed batch. Verify the results through the public helper command:

- retained pane IDs match actual moves;
- provider/model/task choices match the request and policy;
- workers write the expected artifacts and attempt-bound receipts;
- jail output is collected on the host after the launcher exits;
- a prompt timeout followed by resume does not submit again;
- an exited jail job releases a slot, while missing output remains incomplete;
- `status` does not launch or prompt;
- only owned resources were created, and cleanup remains an explicit operator action.

Record the installed CLI/server versions, exact outputs used for bindings, helper revision, manifest/policy digests, model snapshot/effort where available, and artifact paths. Then run a small trace comparison using the intended weaker coordinator. Promote the skill changes only after critical predicates pass without an observed regression. Offline fake success is not evidence of host integration success.
