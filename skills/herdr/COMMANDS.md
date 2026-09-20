# Commands and terminal output

Read this branch for ordinary processes, terminal snapshots, or agent transcript recovery. Apply [SKILL.md](SKILL.md)'s targeting and ownership rules.

## Run a command

Create or select a pane using the core topology rule; parse its returned ID. Then run and inspect:

```bash
herdr pane run <returned-pane-id> "just test"
herdr pane wait-output <returned-pane-id> --match "test result" --timeout 120000
herdr pane read <returned-pane-id> --source recent-unwrapped --lines 120
```

`pane run` sends command text and Enter atomically. `wait-output` searches the selected snapshot immediately, so existing output can match. Use `--match` for a literal substring or `--regex` for a Rust regex; without a timeout it can wait indefinitely. Check that matching output belongs to the requested run and establishes its outcome.

## Read output

| Source | Use |
|---|---|
| `visible` | Currently rendered viewport |
| `recent` | Recent rendered output including soft wraps |
| `recent-unwrapped` | Logs/transcripts with soft wraps joined |
| `detection` | Plain-text bottom-buffer snapshot used by agent detection |

Use text unless terminal colors/styling are evidence, in which case use `--format ansi`.

`--lines` reads available screen and host scrollback. If increasing it still omits a completed response, the agent may use the alternate screen: departed rows never enter host scrollback. A larger count cannot recover them. After that failed read, ask the agent to write the complete response to Markdown in a temporary directory and return the path, then read the file. This is a recovery path, not a required initial-output format; caller workflows may separately require durable result files.
