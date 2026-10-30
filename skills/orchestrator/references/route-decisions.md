# Coordinator decisions and dispatch

Use `herdr-jobs.py` for managed dispatch or `native-dispatch.py` for the native
entrypoint. Both accept the managed manifest, policy, host contract and run
directory, and share the same engine, discovery, provider admission, identity
checks and durable job roster. Native dispatch is not a raw Herdr escape hatch:

```sh
python3 scripts/native-dispatch.py run --manifest REQUEST.json \
  --policy launch-policy.json --run-dir RUN_DIR --preview
python3 scripts/native-dispatch.py run --manifest REQUEST.json \
  --policy launch-policy.json --run-dir RUN_DIR --host-contract HOST_CONTRACT
python3 scripts/native-dispatch.py resume --run-dir RUN_DIR
```

A route preview is not launch authorization. Do not translate its native argv
into a manual `herdr agent start`, or remove fields from a failed manifest to
get past discovery. `transport.NativeTransport` is a low-level implementation
primitive; launch entrypoints use `LaunchTransport`. Calling primitives or an
external Herdr CLI directly is outside the supported dispatch contract.

## Decision record

Preparation compiles a `route_decision` for each job from the coordinator's
manifest. The record binds the job ID and task digest to the requested pin
dimensions, resolved model/provider/harness/effort, task class, difficulty
source and summary, sufficiency reason, ladder level/choice, CSV hash, fallback
observation and escalation evidence/count. `requested_override` preserves the
submitted override before completion. A resolved tuple is never evidence that
the user requested those model and effort values.

The coordinator may supply a task-specific assessment:

```json
{
  "task_class": "interview",
  "routing_assessment": {
    "task_class": "interview",
    "difficulty_evidence": {
      "source": "attempt-1/check.json",
      "summary": "The first attempt missed conflicting requirements."
    },
    "sufficiency_reason": "One reasoning-effort step should resolve the conflict.",
    "escalation_steps": 1
  }
}
```

Without an assessment, a level-1 assignment records its supplied task file and
output expectation as the bounded-work evidence. An escalation records its
failure source/summary and expected improvement. This is coordinator testimony,
not an automated claim that the model is sufficient. `escalation_steps` must
be at most two; the coordinator records the cumulative count for the assignment.
The existing structured escalation validator still requires a concrete failure,
and rejects quota/operational failures as a reason to increase capacity.
Implementation escalations require a reasoning failure.

An explicit model or effort stays authoritative. A harness/provider-only pin
selects the cheapest compatible configured tuple without adding model/effort
fields to the user override. Either CSV column may satisfy the initial constraint
(`choice: constraint` when it comes from the fallback column); this does not
claim that another provider failed or authorize an operational fallback. If
that constrained choice is above level 1, hold for per-task escalation evidence.
Thus “use Claude for five interviews” cannot silently become five Opus-high
assignments. Unsupported combinations are holds; the helper never invents a
tuple. Operational fallback still requires its same-level structured observation.

The live adapter recompiles and compares the decision before discovery. It
publishes batch capability proof only after every required model is discovered.
Claude aliases such as `opus` and `sonnet` cannot establish an exact version.
Every effect checks the unchanged spec and resolved model, an admitted attempt
identity, and the actual shared lease. A boolean in a worker record is not proof
of admission. Retry-ready workers rediscover capabilities before a fresh start.

## Holds, evidence and lease lifetime

Routing holds during preparation or live preflight write an append-only record in
`RUN_DIR/preflight-holds/`, with the blocker and submitted request. Secret-like
payloads are represented by a digest. Preview remains read-only. The original
manifest is not edited, and preflight failures create no pane or worker. Correct
an assignment in a new manifest; failed records remain when the run succeeds.
Existing initialized runs still reject changed requests.

The engine's state contains the job/attempt roster. Task telemetry includes a
`launch` record with backend, decision, decision digest and lease ID. The guarded
transport also writes `launch-evidence.json` before effects, including the exact
resolved model, backend, lease and pane/agent identity. Worker acceptance and
coordinator grading remain separate.

- A capacity-queued job has no lease or pane. Resume retries admission for the
  same attempt; it does not create another worker record.
- An active, blocked, waiting-for-input or ambiguously started worker retains
  its lease. Parking the coordinator or reaching a call timeout does not free
  provider capacity. Resume observes the same pane/name/attempt first.
- A verified settled worker releases its lease. A recorded bounded quota retry
  releases the reconciled old attempt and obtains a fresh lease after backoff.
- Unknown effects are never replayed to get around admission. A manual release
  requires reconciliation; it is not permission to start a duplicate worker.

Legacy runs without a decision can still be inspected with `status`; launching
new work requires a prepared coordinator decision. The fixed local direct
launcher compiles the same decision schema via `local-lane.py preview` and
requires that record on `start --route-decision DECISION.json`. It validates the
fixed local policy tuple, task digest and scope, discovers the exact Pi model
offline before effects, and checks the shared lease before resetting the pane,
starting Pi or submitting the prompt. Its native launch telemetry retains the
decision and lease. It remains outside the integer ladder and cannot replace a
held ordinary assignment. External non-Herdr launch tools are outside this
supported dispatch contract.
