# Orchestrator Preferences

Apply only non-comment content under User Preferences. Current explicit user
instructions take precedence. Resolve runtime and model names against live
capabilities; unknown usage is not exhaustion.

## Managed Herdr Jobs

For independent ordinary-agent and jail-agent jobs in an authorized Herdr host
session, [launch-policy.json](launch-policy.json) is the executable authority
and [managed-jobs.md](references/managed-jobs.md) defines request overrides,
receipts, collection, and recovery. Keep executable routing in that policy
rather than duplicating it in prose. The coordinator retains task intent and
acceptance of worker output.

## User Preferences

- Run up to 4 workers at once by default: use concurrency 4 in managed-job
  manifests unless quota, a shared resource, or the user says otherwise
  (Matt 2026-10-27; was 8 from 2026-10-18).
- Deliver each finished report produced by focused or deep research through the
  active runtime's delivery contract by default, after the research coordinator
  completes synthesis and validation. Do not ask again whether to send it. Honor
  an explicit opt-out in the current request. Deliver only finished reports,
  not plans, drafts, or worker slices. Report the observed delivery state; say a
  report was emailed only after the delivery helper verifies receipt.
  Jailed report fact-check requirements before PrivateBin/PDF delivery are in
  [the research reference](../research/REFERENCE.md#jailed-research-delivery).
- Delegate as much execution as possible. Use the coordinator for routing,
  task state, and user communication; delegate substantive reasoning and review.
  Fast models are preferred for coordinators. No exact model is pinned here.
- Explicit model and effort requests remain fixed; change defaults only through
  an accepted policy update backed by a task-class comparison.
- Ordinary workers use the cost-conscious eight-level CSV ladder according to
  [model routing](references/model-routing.md). Start at the level the
  coordinator judges best suited to the job (not necessarily level 1; Matt
  2026-10-24), escalate on demonstrated failure with recorded evidence, and
  preserve the three independent
  billing pools. That reference owns ordinary model defaults and same-level
  fallback selection; explicit requests and the research/shopping jail rules win.
- Inside Herdr, place each worker in a new pane in its own new workspace; the
  managed path's topology is set and validated in
  [launch-policy.json](launch-policy.json). Preserve the user's focus. Support different harnesses across workers.
- Label every worker workspace `<harness> · <short task>` (e.g. `codex · meal plan Oct24-30`,
  `jail · Well Dweller tips`) right after launch; relabel others on cleanup (Matt 2026-10-24).
- Always give the user absolute paths for every file or folder mentioned in reports,
  task cards, and decision docs, never relative or `~`-elided (Matt 2026-10-24).
- Ask Matt before launching any host `gpt-6-astra` worker; use `gpt-6.1-sol` instead by
  default. Jailed workers may keep astra (Matt 2026-10-24).
- Use isolated worktrees for repository changes, including small fixes. Prefer
  standalone Treehouse for their lifecycle. Research and other work that does
  not change a repository uses an ordinary task output folder.
- Provide a Herdr diff/file pane beside a coding worker, using reviewr when
  compatible. Show the complete task change, including committed work.
- Local commits are allowed. Present code for the user's decision before opening
  a PR or applying/merging changes into the target branch, including local-only
  fixes. Approval to open a PR does not also authorize its later merge.
- When writing worker briefs or making commits, omit AI co-author and attribution
  trailers from commit messages and PR bodies, including any that a harness or
  template might otherwise request.
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
- Effort cap (Matt 2026-10-12): `gpt-6-astra` and Claude Opus 5.5 never run above
  xhigh, in or out of the jail; pass xhigh explicitly where a route defaults higher.
- `jail-agent` is the canonical fish entry point. The live function currently
  defaults to the `omp` harness; pass `--harness codex` for Codex or `--claude`
  for Claude. `omp-train` remains a forwarding alias for one release cycle or
  30 days, whichever is longer, and passes its arguments unchanged.
  Shopping workers that need Claude use `jail-agent --claude -p "…"` (the
  jail's managed Claude settings pin the model and effort). Deep research uses
  `jail-agent --harness codex` with the jailed Codex research default:
  `gpt-6-astra` at xhigh effort (user instruction 2026-10-03, effort capped at
  xhigh 2026-10-12; never substitute another model or effort).
  Research workers keep their worker contract, including the mandatory
  independent source/claim check and the same jail privacy guard.
  The managed Herdr job helper's jail route is Codex-only (schema v1), so
  jailed Codex research fits its `deep_research` route, while jailed Claude
  work (shopping) launches with the host pane-command route. Exact jail
  model selection there is unsupported until its separate catalog is
  verified, so confirm at admission that the jail's runtime default is
  `gpt-6-astra` at xhigh effort; if it cannot be confirmed, hold the launch —
  do not substitute.
  Managed jailed Codex jobs use optional `task_class` for their per-run
  stream idle timeout: `short_routine` and missing or unknown classes use
  300000 ms; explicitly setting `long_form_research` uses 600000 ms. This is
  an idle-stream limit, not a total job deadline, and leaves shared Codex
  configuration untouched.
  Within the managed helper, a named non-jail runtime is honoured by
  re-issuing the job as `task_kind: ordinary`; a runtime override on the jail
  route itself is refused, never converted.
  On a host Herdr session, launch jailed Codex research with the native route
  for the exact `jail-agent --harness codex exec --skip-git-repo-check`
  process as a pane command in its own workspace — the host `codex` binary is
  not the jail. Shopping workers load skill `shopping`; deep research workers
  follow their worker contract. Inside a jail, use only an available in-jail
  route; never reach outward to a host Herdr session.
- "Luna agents" (or "luna workers") means the **Codex** harness running
  `gpt-6-luna` at **max** effort (`codex -m gpt-6-luna -c
  model_reasoning_effort=max`, Herdr kind `codex`). This named request is pinned
  at the ladder's level 4 primary. The level 4 fallback uses `claude-sonnet-5-5`
  at high effort through the Claude harness and provider. Preflight verifies
  its exact model and effort before launch.
- Retry tuning is run-scoped. Prefer a supported per-run override and record it
  in run state. Never edit shared pi/Codex settings for transient rate limits;
  when a runtime has no supported per-run override, record the staged change and
  wait for authorization before applying it or retrying work under it.
