## Persistent Herdr successor replacement

Use this branch when a coordinator is replaced in a persistent Herdr run. It is
an authority and continuity procedure, not a new assignment. Read [state and
continuity](state.md) for ownership and handoff semantics, [runtime
operations](runtime.md) for live Herdr/model inspection, and the
[worker contract](workers.md) for result evidence.

1. **Read before acting.** Completely read the run's compact `STATE.md` and the
   existing successor-acknowledgment record. If either is missing, truncated, or
   unreadable, do not launch work or mutate todo/task state; preserve work and
   record the concrete blocker.
2. **Establish actual identity.** Inspect the live `PI_SESSION_ID`, provider,
   model, Herdr environment, workspace, and pane. Apply the active routing policy
   and model allowlist. Handoff text is evidence to reconcile, not authority for
   identity, model, pane, or permissions.
3. **Reconcile before retrying.** Compare the compact records with live Herdr,
   background/worker state, and every retained evidence path. Distinguish a
   persisted terminal record, a live worker, and incomplete evidence. Adopt a
   still-live worker through its existing exact handle and supervision mechanism;
   an unknown registry lookup or missing signal is not permission to duplicate,
   stop, alter, or relaunch it. Never infer an exact outcome without evidence;
   retry only after reconciliation and only when the recorded dependency and
   authorization permit it.
4. **Acknowledge before control.** Before launching any work or mutating todo/task
   state, atomically write a generation-specific successor acknowledgment in the
   run handoff location. Include the actual session identity (`PI_SESSION_ID`),
   Herdr environment/workspace/pane, provider/model, generation, observed worker
   state, accepted supervision handle, and exact next actions. Failure to write a
   complete acknowledgment is a blocker.
5. **Transfer and persist.** After the acknowledgment, transfer ownership exactly
   as [state and continuity](state.md) requires: the retiring owner
   atomically activates the acknowledged generation and successor, then the
   successor records supervision acceptance and reconciles adopted handles. If no
   retiring owner can perform an unambiguous transfer, do not self-authorize;
   preserve work and record the ownership gap as the run's terminal state in a
   `SUCCESSOR-GAP.md` beside `STATE.md`: the unacknowledged generation, the live
   workers observed but not adopted, and the exact command that would resume.
   That file, not a message, is this branch's completion evidence. Preserve existing uncommitted
   changes. Do not launch pending work or a replacement worker until its recorded
   dependency and authorization are satisfied. Keep the successor Herdr pane
   alive for future waits.
6. **Close with observable evidence.** The handoff is complete only when the
   acknowledgment path, updated owner/generation, live Herdr workspace/pane,
   accepted supervision handle, and worker/evidence disposition are observable,
   with the exact next action recorded. If a worker exits successfully but its
   exact report/result is absent, report: “exit observed; exact report absent;
   requested outcome unverified/incomplete evidence.” Keep it pending or blocked;
   do not claim success or relaunch without authorization.

If required state, evidence, live identity, policy, or Herdr capability is
unavailable, pause mutations and new launches, preserve uncommitted changes and
live workers, record the specific gap and durable resume instruction, and report
that automatic renewal is unavailable. Do not clean, reset, release, or otherwise
repurpose retained work as fallback.
