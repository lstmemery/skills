# Orchestrator Preferences

Apply only non-comment content under User Preferences. Current explicit user
instructions take precedence. Resolve runtime and model names against live
capabilities; unknown usage is not exhaustion.

## Managed Herdr Jobs

For independent ordinary-agent and omp-train jobs in an authorized Herdr host
session, [launch-policy.json](launch-policy.json) is the executable authority
and [managed-jobs.md](references/managed-jobs.md) defines request overrides,
receipts, collection, and recovery. Keep executable routing in that policy
rather than duplicating it in prose. The coordinator retains task intent and
acceptance of worker output.

## User Preferences

- Deliver each finished report produced by focused or deep research through the
  active runtime's delivery contract by default, after the research coordinator
  completes synthesis and validation. Do not ask again whether to send it. Honor
  an explicit opt-out in the current request. Deliver only finished reports,
  not plans, drafts, or worker slices. Report the observed delivery state; say a
  report was emailed only after the delivery helper verifies receipt.
- Delegate as much execution as possible. Use the coordinator for routing,
  task state, and user communication; delegate substantive reasoning and review.
  Fast models are preferred for coordinators. No exact model is pinned here.
- Inside Herdr, place each worker in a new pane in its own new workspace; the
  managed path's topology is set and validated in
  [launch-policy.json](launch-policy.json). Preserve the user's focus. Support different harnesses across workers.
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
- When the queued decisions are ready for the user, write one decision doc
  (quick answer sheet first, then per item: what is ready, the recommendation,
  exact commands, and a one-line reply format) and upload it with the
  `research-delivery` skill's PrivateBin step. Give the user the link and the
  reply format; do not wait to be asked. The doc must contain no secrets or
  account numbers. [Unattended runs](references/unattended.md#decision-doc)
  owns how it is built and checked against the ledger.
- Maintain a compact task ledger and hand over to a fresh coordinator at safe
  boundaries before context gets large. Do not depend on a universal 200k trigger.
- Keep personal orchestration policy in this skill and standalone tool settings;
  an upstream project clone is not a workflow dependency. Use deterministic
  tools and scripts when they provide a concrete guarantee. Choose verification
  from task and repository policy; add project-wide modes only when explicitly
  selected.
- When the user names `omp`, launch `omp`. Treat it as a distinct harness from
  `pi`; register a missing process adapter when permitted, rather than substitute.
- "omp-train" with no harness named means Claude Opus 5.5 at xhigh effort:
  `omp-train --claude -p "…"` (the jail's managed Claude settings pin that
  model and effort); that remains the jailed default for shopping. Deep
  research, and an explicit no-harness `omp-train` for research work, uses
  the jailed Codex research default instead: `gpt-6-astra` at max effort
  (user instruction 2026-10-03; never substitute another model or effort).
  Research workers keep their worker contract, including the mandatory
  independent source/claim check and the same jail privacy guard.
  The managed Herdr job helper's jail route is Codex-only (schema v1), so
  jailed Codex research fits its `deep_research` route, while jailed Claude
  work (shopping) launches with the host pane-command route. Exact jail
  model selection there is unsupported until its separate catalog is
  verified, so confirm at admission that the jail's runtime default is
  `gpt-6-astra` at max effort; if it cannot be confirmed, hold the launch —
  do not substitute.
  Within the managed helper, a named non-jail runtime is honoured by
  re-issuing the job as `task_kind: ordinary`; a runtime override on the jail
  route itself is refused, never converted.
  On a host Herdr session, launch jailed Codex research with the native route
  for the exact `omp-train --harness codex exec --skip-git-repo-check`
  process as a pane command in its own workspace — the host `codex` binary is
  not the jail. Shopping workers load skill `shopping`; deep research workers
  follow their worker contract. Inside a jail, use only an available in-jail
  route; never reach outward to a host Herdr session.
- "Luna agents" (or "luna workers") means the **Codex** harness running
  `gpt-6-luna` at **max** effort (`codex -m gpt-6-luna -c
  model_reasoning_effort=max`, Herdr kind `codex`), not pi. Use pi with
  `openai-codex/gpt-6-luna` only as a fallback when Codex itself is broken,
  and say so. (User instruction 2026-09-28, after Codex was repaired.)
- Retry tuning is run-scoped. Prefer a supported per-run override and record it
  in run state. Never edit shared pi/Codex settings for transient rate limits;
  when a runtime has no supported per-run override, record the staged change and
  wait for authorization before applying it or retrying work under it.
