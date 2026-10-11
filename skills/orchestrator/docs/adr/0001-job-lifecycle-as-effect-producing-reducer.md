# Job lifecycle as an effect-producing state reducer

## Context
`scripts/herdr_jobs/engine.py` is the coupling hub of the managed-jobs package. One class mixes pure lifecycle decisions (admission, launch, collect, finish, retry), durable checkpointing, Herdr side effects and result projection. Checkpoint ordering, rate-limit retry identity, pane verification and workspace closeout are safety properties that are hard to test through that surface. The jailed design review of 2026-10-10 (`research/skill-scripts-design-20261010` in the agents repo, R2) compared two designs for deepening it.

## Decision
Restructure the job lifecycle as an effect-producing state reducer (the review's Design A). Pure transitions take the persisted job state plus an observation and return the next state plus a list of effects. A small composition of `StateStore`, `AdmissionService` and `NativeHerdrAdapter` persists state and executes effects. Migrate one phase at a time behind a compatibility runner that compares result JSON and persisted state with the current `Engine`. The CLI keeps a single `run` operation and the injected transport factory.

## Status
accepted (direction chosen by Matt on 2026-10-10; implementation pending)

## Alternatives
- Design B, phase services behind a coordinator: easier to migrate incrementally, but a wider interface, and transition rules stay spread across services. Rejected because ambiguous-effect rules would still change in several places at once.

## Consequences
- Transition rules become exhaustively unit-testable without Herdr, and effect execution is tested at one adapter seam.
- It requires an explicit transition/effect data model and a careful migration of the current checkpoint order. An unknown effect outcome must never be treated as a failed effect.
- Follow-up: Vikunja ticket for the R2 refactor. The managed launcher has not yet run live (#1158), so the compatibility runner is the main regression guard.

## Reevaluate when
The first migrated phase cannot preserve current checkpoint ordering or Codex prompt verification without leaking Herdr detail into transitions.
