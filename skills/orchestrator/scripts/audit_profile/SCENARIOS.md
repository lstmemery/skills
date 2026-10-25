# Pilot acceptance scenarios

Public seams: trusted collector functions/CLI and launch_profile(snapshot, worker,
output). The worker receives only schema-validated JSON and a fresh output mount.

- Preserve a legitimate service-state enum and reject every other state/output.
- Project certificate identity (public DER SHA-256) and UTC expiry only.
- Reject unsupported operations, malformed/oversized schemas and extra fields.
- Capture complete stdout/stderr privately; refuse errors, timeout and oversize.
- Verify confinement before any worker instruction; fail closed without fallback.
- Plant fake bare, cookie, PEM, WireGuard, URL/header and encoded/split values;
  keep them out of snapshots, worker streams, polling returns and artifacts.
- Deny file/subprocess/SSH/network/agent/fd/credential access in the real container.
- Bind attestation to schema, collector, profile, bootstrap, snapshot and worker.
- Run one bounded real read-only audit after synthetic acceptance passes.
