# Orchestrator Preferences

Preferences are optional. Replace `No preferences set.` with plain-language
instructions for choosing agents, models, fallbacks, and limit behavior.

The current user request always wins. The skill still checks live runtime
availability and provider limits before applying a preference.

Preferences may use human model names. The skill resolves them against live
runtime catalogs and aliases instead of relying on remembered model slugs.

<!--
Example:

Use Fable only for extensive UI work.
Use GPT-5.6 Sol for most deep execution work.
Use Grok 4.5 Agent for simple coding tasks.
Use Pi with Kimi 2.7 for exploration.
When Fable is out of usage, use GPT-5.6.
When GPT-5.6 is out of usage, use Opus.
When every allowed provider is out of usage, pause new work until limits reset
and notify me.
-->

## User Preferences

- When the user asks for an omp job or names the `omp` runtime, launch
  runtime `omp`. It is registered in `~/.orchestrator/config.json`
  (process adapter around the `omp` CLI). Never substitute `pi` for `omp`:
  `pi` is the predecessor harness with its own sessions and auth, not a
  fallback for omp.
- Inside Herdr (`HERDR_ENV=1`), spin up new child agents in their own new
  workspace (`herdr pane split`, then `herdr pane move --new-workspace
  --label <task-name> --no-focus`) and drive them with `herdr agent`
  commands — not as orchestrator background launch tasks. Orchestrator
  launch stays for `shell` tasks and for work outside Herdr.
- Deep research and shopping jobs run as omp-train codex agents — the
  `omp-train --harness codex exec` pane command in their own Herdr workspace
  (PII jail), never a bare codex or omp agent — unless the user names a
  different runtime in the request. Shopping jobs load and follow
  `skill://shopping`; deep research jobs follow the deep-research worker
  contract. (Set 2026-09-16 by user instruction.)
