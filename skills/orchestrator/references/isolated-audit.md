# Credential-isolated snapshot audit pilot

This is an opt-in offline worker profile. It is implemented beside the existing
orchestration launcher because collection and worker admission belong there. It
does not change the ordinary launcher, host configuration, or the pii-jail policy.
Existing ordinary workers do not acquire this protection automatically.

The trusted coordinator runs the collector on the source host. The collector
supports exactly two operations:

- Local service state: fixed `systemctl show --property=ActiveState --value -- UNIT`.
  The output is one closed state enum and a maintainer-selected identifier.
- TLS certificate identity/expiry: obtain the peer's public DER certificate,
  emit its SHA-256 fingerprint and UTC expiry. Subject names, issuer text,
  application bodies, credentials, and private key objects never enter the
  snapshot. This inventories the certificate presented by an endpoint; it does
  **not** assert chain or hostname authentication. It can inventory expired and
  self-signed certificates. The collector sends no application request or client
  credential and loads no client certificate or system trust store.

The plan is trusted configuration, never a worker-supplied request. It accepts
only the two documented record shapes, numeric IPs, bounded ports, DNS-form SNI,
identifiers and service unit names. The collector validates the whole plan
before contacting sources. A service on another host requires running this
same collector there through a separately reviewed restricted transport; no
SSH shell/broker is included in the pilot.

## Run without installing anything

From `skills/orchestrator/scripts` in this checkout:

```sh
python3 -B -m audit_profile collect --plan ../examples/isolated-audit/plan.json > /ABS/SAFE/snapshot.json
python3 -B -m audit_profile launch --snapshot /ABS/SAFE/snapshot.json \
  --worker ../examples/isolated-audit/worker.py --output /ABS/SAFE/new-audit > /ABS/SAFE/launch.json
```

Check the collector's exit code before launch. `new-audit` must not exist.
These commands are two separate trusted steps; never feed a failed collection
into a worker or replace the plan with a shell command. Only collector-produced,
reviewed snapshots should be supplied to launch. Schema validation constrains
shape, not the provenance of intentionally supplied values or worker source.

Dependencies: Linux with unprivileged namespaces, root-owned `/usr/bin/bwrap`
with `--bind-fd`, `--ro-bind-data`, and `--assert-userns-disabled`, a root-owned
`/usr/bin/python3` runtime, `/usr/bin/ldd`, and OpenSSL/systemctl for collection.
No users, packages, services, or system-wide files are created by this profile.
Unsupported platforms, missing tools and failed checks refuse launch; there is
no ordinary-host fallback. The tests require the real isolation facilities and
fail rather than skipping them.

## Boundary and launch evidence

The rootless Bubblewrap container has its own user, mount, PID, network, IPC,
and UTS namespaces, UID/GID 65534, no capabilities, `NoNewPrivs`, and disabled
nested user namespaces. The UID is a container identity, not a newly provisioned
host account. The host caller remains the trusted collector/launcher identity.

The only host runtime exposed is Python's root-owned standard library (with
site-packages hidden), its interpreter and the ELF dependencies of its native
stdlib modules. The worker receives its own Python source, a read-only profile,
a sealed schema-checked JSON snapshot and a fresh writable output directory.
It has no host home, `/etc`, `/run`, `/sys`, SSH binary, agent socket, provider
credential, host process namespace, network route, or inherited input handles.
`/tmp` and `/home/worker` are empty container directories. No arbitrary command,
extra mount, executable path, environment passthrough or provider setup is
accepted by the launch API.

A trusted bootstrap checks the actual environment, identity, capabilities,
namespace separation, root/mount inventory, read-only flags, descriptor inventory,
and sealed input hashes **in the worker container before executing worker code**.
It emits the first framed attestation record; the parent validates that record
and never forwards an unverified worker stream. The attestation binds snapshot,
worker, collector, schema, profile, bootstrap, full implementation and effective
confinement configuration. These are integrity evidence from the trusted local
launcher, not a cryptographic signature or remote attestation protocol.

Collector subprocess stdout/stderr are captured in bounded memory, never teed,
logged raw, or streamed. A timeout, nonzero exit, oversized or malformed result
returns a fixed value-free refusal. Worker streams are also bounded and buffered
until process completion; failed/timed-out worker streams are withheld. The
launch CLI emits one JSON result only after verification and completion, so it
has no early polling/spill channel. A killed/failed worker can leave files in its
fresh output directory; do not collect/publish those directories after refusal.
Treat output files as untrusted, and never follow output symlinks on the host.

## Verification and scope limits

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/orchestrator/tests -p test_audit_profile.py -v
```

The matrix has positive controls (planted fixture values exist) and negative
controls (none occur in the snapshot/result/worker artifacts). It covers bare
values; cookies/equals signs; complete/unterminated PEM; WireGuard keys; headers
and URLs; encoded/tabbed objects; split chunks; stdout/stderr; subprocess polling;
failures/timeouts/oversize; malformed JSON/schema; direct/nested process reads;
missing isolation; deliberately shared network; and pre-existing output secrets.
It exercises real Bubblewrap namespaces, real OpenSSL and the public CLI.

The worker is an offline Python audit process. This pilot does not launch an AI
provider harness or expose MCP tools, and makes no hook/MCP/hosted-tool containment
claim. A provider proxy with no readable provider key, Gitleaks/transcript gates,
remote forced-command readers, and broader service operations remain future work.
The lack of a network route and host sockets also excludes live MCP/SSH access
from this profile. No historical transcript cleanup or credential rotation is
performed. An existing pii-jail container is a separate security boundary and
has not been changed or attested by these tests.

The trusted collector, coordinator-provided plan/worker, root-owned runtime,
Bubblewrap and kernel are in the trusted computing base. A malicious host
administrator or intentionally secret-bearing worker source/snapshot is outside
this pilot's claim. Before deploying an AI worker, keep provider authentication
outside this container and review the exact tool and output collection path.

Rollback: stop using the opt-in CLI and retain/remove only this branch's isolated
worktree and safe audit outputs. There are no installed hooks, services or users
to undo. Integration requires the normal review boundary; do not merge as part
of running these commands.
