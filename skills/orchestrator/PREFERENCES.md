# Orchestrator Preferences

Apply only non-comment content under User Preferences. Current explicit user
instructions take precedence. Resolve runtime and model names against live
capabilities; unknown usage is not exhaustion.

## User Preferences

- Delegate as much execution as possible. Use the coordinator for routing,
  task state, and user communication; delegate substantive reasoning and review.
  Fast models are preferred for coordinators. No exact model is pinned here.
- Inside Herdr, place each worker in a new pane in its own new workspace.
  Preserve the user's focus. Support different harnesses across workers.
- Use isolated worktrees for repository changes, including small fixes. Prefer
  standalone Treehouse for their lifecycle. Research and other work that does
  not change a repository uses an ordinary task output folder.
- Provide a Herdr diff/file pane beside a coding worker, using reviewr when
  compatible. Show the complete task change, including committed work.
- Local commits are allowed. Present code for the user's decision before opening
  a PR or applying/merging changes into the target branch, including local-only
  fixes. Approval to open a PR does not also authorize its later merge.
- Every coding task runs the repository's applicable checks and the existing
  `code-review` skill's Standards/Spec review before human inspection. Resolve
  accepted findings and record any rejection with its reason. Follow that
  skill's explicit missing-spec procedure when a spec cannot be found.
- Continue supervision and routine authorized follow-ups while the user is away.
  Queue decisions that require the user and continue independent work.
- Maintain a compact task ledger and hand over to a fresh coordinator at safe
  boundaries before context gets large. Do not depend on a universal 200k trigger.
- Keep personal orchestration policy in this skill and standalone tool settings;
  an upstream project clone is not a workflow dependency. Use deterministic
  tools and scripts when they provide a concrete guarantee. Choose verification
  from task and repository policy; add project-wide modes only when explicitly
  selected.
- When the user names `omp`, launch `omp`. Treat it as a distinct harness from
  `pi`; register a missing process adapter when permitted, rather than substitute.
- Deep research and shopping use jailed Codex workers via
  `omp-train --harness codex exec` unless the user names another runtime.
  On a host Herdr session, launch that as a pane command in its own workspace,
  preserving the jail boundary. Shopping workers load skill `shopping`; deep
  research workers follow their worker contract. Inside a jail, use only an
  available in-jail route; never reach outward to a host Herdr session.
