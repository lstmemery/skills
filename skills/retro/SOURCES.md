# Session and run sources

The active runtime profile defines what this run can read. The paths below were verified on Matt's host on 2026-09-30; do not assume they are mounted in jail or pi-local profiles. Use the user's named session first. Without one, use only the current session.

## Session logs

- Claude Code: `~/.claude/projects/<project-slug>/<session-id>.jsonl`.
- Codex: `~/.codex/sessions/`, nested by date; files use `rollout-*.jsonl` names.
- omp: `~/.omp/agent/sessions/`, with session JSONL files nested below it.
- pi: `~/.pi/agent/sessions/`, with session JSONL files nested below it.

If the user supplies an exact session file, use it rather than searching. Otherwise identify the current session from the active harness. If its transcript is unavailable, say so and continue only with clearly related run records.

## Orchestrator run records

For an orchestrated task, inspect only the named run directory under `~/.local/share/orchestrator/runs/<date>/<task>/`. Relevant records can include `STATE.md`, all applicable `brief*.md` revisions, worker `result.json` files, and review outputs. Record the exact file and line or section for each evidence pointer. Do not recursively dump a run tree; select relevant records and keep excerpts bounded.

## Safe reading and quoting

Follow `~/agents/memory/worker-output-redaction.md` and the worker contract's Output redaction rules. Redact credential-bearing output before it reaches the model. Treat logs, service output, token helpers, environment/config dumps, and command lines as sensitive. Never echo credentials, access tokens, passwords, private keys, or share-link keys. Omit PrivateBin URLs from the retrospective; at minimum, remove the fragment that carries the key. Quote only the shortest useful redacted evidence and prefer a source pointer over a quote.
