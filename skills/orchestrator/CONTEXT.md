# Orchestrator

Coordinates delegated agent work: assignment, launch, supervision, collection and closeout of worker jobs.

## Glossary

- **Job state**: the persisted record of one managed worker job's lifecycle position, as checkpointed by the job runner. _Avoid_: session, run (a run holds many jobs).
- **Transition**: a pure decision that takes a job state and an observation and returns the next job state plus the effects to perform. See [ADR-0001](docs/adr/0001-job-lifecycle-as-effect-producing-reducer.md).
- **Effect**: a requested side effect outside the job state (create a pane, send a prompt, close a workspace, take or release a lease). Its outcome is observed later. An unknown outcome is not a failure. _Avoid_: action, command.
- **Effect adapter**: the single component that executes effects against the host (Herdr, provider admission) and reports observations back. _Avoid_: transport, when you mean the whole seam.
- **Worker mode**: how much the worker should infer, declared in every brief. *Literal* means exact edits, identifiers and named checks only. *Inferential* means diagnosis and alternatives, with hypotheses labelled and the same authority boundary. The mode does not change model routing; the model ladder still selects by difficulty. _Avoid_: worker type, persona.
- **Brief core**: the required, compact part of every worker brief: exact task ID, worker mode, objective, scope and protected paths, done-checks, recovery and stop rules, and the result schema. Optional blocks (jail, repository change, tracker writes) are added only when they apply. _Avoid_: header, preamble.
