# Agent control

Read this branch when starting, prompting, waiting for, or interacting with a recognized Herdr agent. Apply the targeting, topology, and ownership rules in [SKILL.md](SKILL.md).

## Identity and readiness

Agent targets are unique live names or the pane IDs hosting them, never terminal IDs or bare kind labels. Names match `[a-z][a-z0-9_-]{0,31}`. A name follows the current pane occupant and clears when the agent exits, is released, or is replaced.

`idle` and `done` mean ready for input. CLI/API seen state distinguishes them: focus marks a target seen; reads do not. Each TUI client's Done badge can differ. `blocked` means a recognized approval/question UI. `unknown` means detection is inconclusive, not completion.

An available shell pane has the shell itself at its interactive foreground prompt, with no running foreground command, editor, or agent. Obtain the pane using the selected topology, then start the requested kind with a useful unique name:

```bash
herdr agent start reviewer --kind codex --pane <returned-pane-id>
```

Inspect `herdr agent` for installed kinds and options; pass native arguments only after `--`. Successful start means Herdr detected the expected agent in that pane and found it ready. Startup defaults to 30 seconds. A startup approval/question returns `agent_not_ready` immediately while retaining the name for inspection and `send-keys`. Wait for idle before prompting.

## Submission and waits

```bash
herdr agent prompt reviewer "Review the current diff and report actionable findings." --wait --timeout 120000
```

`prompt` respects live bracketed-paste mode and writes text plus encoded Enter as one ordered submission. Success means both were written, not that a turn began. Codex on Windows uses longer submit delays for larger prompts. A pre-existing approval/question is rejected with `agent_blocked` before input. Inspect that UI and obtain any missing user authorization before answering; reuse authorization already supplied for the action.

Normal work uses `--wait`, which settles on `idle`, `done`, or `blocked`; do not repeat these defaults with `--until`. From a non-working state, it must first observe `working` or `blocked` activity within five seconds of submission. Otherwise it reports `agent_prompt_stalled`, or `timeout` if the caller's timeout expires first. The timeout includes submission. Once activity is seen, a wait without a timeout can be indefinite. It tracks lifecycle, not a specific turn: when already working, the current turn's completion can satisfy it.

Use `--until` only for a specific state, for example `herdr agent wait reviewer --until blocked --timeout 120000`. Standalone `wait` otherwise has the same settled-state defaults.

Inspect every failed wait or blocked result before deciding what to send:

```bash
herdr agent get reviewer
herdr agent read reviewer --source recent-unwrapped --lines 120
```

Apply the core's inspect-before-resend rule. For truncated transcripts or read-source selection, read [COMMANDS.md](COMMANDS.md#read-output).

Use logical keys for interactive controls, for example `herdr agent send-keys reviewer esc` or `ctrl+c`. Herdr validates every key before writing bytes. Use pane input only when raw terminal control is intentional. Read the actual result and check the requested outcome before reporting completion.
