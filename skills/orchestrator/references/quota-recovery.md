# Provider usage-limit recovery

Use this procedure only after observing a provider's explicit usage-limit or
quota banner/status, or a structured provider 429. A generic timeout, slow
response, echoed prompt, or report discussing quotas is not evidence. The
read-only classifier `rate_limit_from_output` recognizes supported Codex and
Claude terminal banners and structured Pi provider errors; preserve the exact
redacted status excerpt and runtime as the evidence.

## Worker recovery

1. Stop treating the worker as active work. Record its exact task/attempt ID,
   runtime/model/provider, observed time, redacted banner/status excerpt, and
   provider-supplied reset timestamp in the run's task record. Do not copy
   credential-bearing output. If the provider gives a reset timestamp, persist
   it through `herdr-admission.py rate-limit --reset-at`; use
   `--retry-after` for a relative delay. If a concrete limit has no reset
   metadata, use the existing bounded 60-second default. Keep the attempt and
   output directory; do not create a duplicate attempt merely because its
   pane is idle.
2. Reconcile the exact worker handle and result/artifacts before acting. For a
   direct worker, when the recorded reset has passed, allow **one same-worker
   continuation** on that exact handle, with the original brief, assignment
   revision, worktree, and acceptance criteria. Record continuation count 1
   and reset evidence in `STATE.md`. If the handle cannot accept a continuation,
   do not silently relaunch. Managed Herdr jobs cannot continue the same pane;
   their one allowed recovery is one fresh attempt in a new output directory,
   only after the recorded reset, with the same pinned route and assignment.
3. If the reset is unknown, unavailable, or the primary remains exhausted,
   when a reset cannot be awaited, the only quota fallback is the exact same-level configured model in
   `model-ladder.csv`, with concrete limit evidence and the normal explicit
   fallback fields. Treat that as one new attempt with a fresh directory,
   provider admission, and the same assignment revision. Explicit user-pinned
   routes do not fall back without new user authorization. Never escalate a
   reasoning level to solve quota exhaustion. Do not do both the continuation
   and fallback for the same observed stall; once either path is used, further
   recovery needs coordinator disposition or user direction.

## Coordinator recovery and ownership

A coordinator that sees its own usage-limit banner/status stops task and run
mutations immediately. Before it becomes unable to act, it records the reset,
generation, exact live worker handles and last observations, pending decisions,
and one next action per task. It must not claim an automatic wake or restart.

Continue coordination only after the recorded reset on the same session, or
use the configured same-level coordinator fallback when concrete quota evidence
and the active routing policy allow it. A replacement coordinator is a
persistent successor: follow [successor.md](successor.md), including a
generation-specific acknowledgment and transfer by the retiring owner. The
successor first reconciles live workers and retained artifacts and adopts
workers only through their existing handles. The prior coordinator stops
mutating after transfer. If quota exhaustion prevents an unambiguous retiring
owner transfer, do not self-authorize a replacement or mutate task state; record
the ownership gap and durable resume instruction as specified by the
successor procedure. Human takeover is the authority when no retiring owner can
transfer control.

One reset wait or one same-level fallback is the entire automatic quota
recovery budget for that stalled owner. After it is consumed, stop and record a
blocked state with evidence; further retries require an explicit coordinator
or user decision. Human approval remains required for any provider/model change
outside the configured same-level fallback.
