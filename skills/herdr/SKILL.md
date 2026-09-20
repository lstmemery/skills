---
name: herdr
description: Control Herdr panes, layouts, commands, and agents only when explicitly requested; requires HERDR_ENV=1.
---

# Herdr

Confirm the requested state or result from live responses, report the target by name or label, and clean up only task-owned temporary layout or state why it remains. Agent lifecycle readiness alone is not proof the task is complete.

## Enter and discover

Before any control command, check `test "${HERDR_ENV:-}" = 1`. If it fails, report that this agent is outside Herdr and stop; do not control another client's focused session.

Use `herdr --help`, then the relevant bare **command group** (`herdr agent`, `herdr pane`, `herdr workspace`, `herdr tab`, `herdr worktree`, `herdr terminal`, `herdr notification`, `herdr integration`, `herdr session`, or `herdr machine`). Installed help owns syntax. Bare `herdr` launches or attaches the TUI; do not use it for discovery. An incomplete-looking mutating command such as `herdr workspace create` can execute with defaults; never probe mutations by omitting arguments.

Read IDs and state from returned JSON. Server errors are JSON on stderr with exit 1; syntax errors exit 2.

## Target and topology

- Use `--current` for the calling pane, an explicit pane ID, or a unique live agent name. Omitted targets can use another client's UI focus.
- Workspace (`w1`), tab (`w1:t1`), and pane (`w1:p1`) IDs are opaque; closed tab/pane IDs are not reused. Discover with `workspace list`, `tab list --workspace "$HERDR_WORKSPACE_ID"`, `pane current --current`, `pane list --workspace "$HERDR_WORKSPACE_ID"`, or `agent list`.
- **Moves change IDs.** After `pane move`, use `.result.move_result.pane.pane_id` or the live agent name. `.result.move_result.previous_pane_id` remains usable only through the moved process's inherited caller context, not as a general target.
- Creation returns the next handles: `workspace create` → `.result.workspace`, `.result.tab`, `.result.root_pane`; `tab create` → `.result.tab`, `.result.root_pane`; `pane split` → `.result.pane`.
- Use the topology and working directory explicitly requested for the task. When Orchestrator is the caller, apply its selected workspace/worktree policy. Otherwise default to a sibling pane in the current tab and current directory; do not invent a new workspace or worktree.
- Preserve focus with `--no-focus` for background work unless the user asked to switch. For a sibling split, honor the requested direction; otherwise inspect `herdr pane layout --pane "$HERDR_PANE_ID"`, split wide panes right and narrow/tall panes down, and avoid repeated splits that leave unusable geometry. Preserve cwd explicitly, for example `herdr pane split --current --direction right --cwd "$PWD" --no-focus`.

Use pane commands for raw terminals and ordinary processes; use agent commands for recognized agents and lifecycle-aware control. `agent start` needs an existing available shell pane and never creates or moves layout.

## Select the operation

- **Start, prompt, wait for, or interact with an agent:** read [AGENT-CONTROL.md](AGENT-CONTROL.md) before control input.
- **Run an ordinary command or interpret terminal output:** read [COMMANDS.md](COMMANDS.md). Agent-output recovery uses its read guidance too.
- **Remote hosts, saved machines, or multiple servers:** read [REMOTE.md](REMOTE.md) before selecting targets or changing profiles.

## Ownership and recovery

Inspect state and output before retrying a failed wait or prompt. A timeout or stalled response does **not** prove nondelivery; never blindly resubmit work.

Close only workspaces, tabs, panes, and sessions you created unless the user requested broader cleanup. `workspace close --group` also closes linked worktree workspaces; it is not a retry for `workspace_group_close_required`.

`--trust-repository` grants per-request Git trust; use it only after user verification of that repository. Check `herdr status` before relying on new server features after an update. A missing method does not authorize stopping or replacing the server. `herdr server stop` kills pane processes: require the user's explicit intent to stop them. Never kill the main Herdr process; isolated experiments use named test sessions.
