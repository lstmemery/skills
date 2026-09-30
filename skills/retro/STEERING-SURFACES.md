# Matt's steering surfaces and checks

Inspect only surfaces relevant to the observed issue. Paths are repo-relative unless marked `~`; the runtime profile determines which files are readable.

## Steering surfaces

- **Repository instructions:** `CLAUDE.md` and/or `AGENTS.md` in the affected repository.
- **Context navigation:** `CONTEXT-MAP.md` and the relevant per-context `CONTEXT.md`.
- **Agent documentation:** `docs/agents/`.
- **Memory:** `memory/MEMORY.md` as the index, relevant one-fact files, and feedback memories.
- **Skills:** the public skills repository and `skills-local`; check `skills-lock.json` hashes and `localEdit` reasons before proposing skill changes.
- **Orchestrator:** its `PREFERENCES.md` and relevant files under `references/`.
- **Jail:** `tools/pii-jail/jail-config/`, `tools/pii-jail/token-allowlist.txt`, `tools/pii-jail/gate.sh`, and related verification scripts.
- **Claude Code settings:** `~/.claude/settings.json`, especially `hooks` and `skillOverrides`. Read only the relevant keys and redact any credential values before showing output.

Keep the implementation-versus-review boundary. The implementation agent has the most context pressure; review agents receive a diff. Prefer an automated check for fixed mechanical rules. Put judgment calls in the review guidance used by reviewers.

## Existing checks

Check these before suggesting a new guardrail:

- Skills: `tools/skills-check.sh`, `tools/skills-lock.py --check`, and `tools/test_skills_lock.py`.
- Orchestration: tests under `skills/orchestrator/tests/`.
- PII jail: `tools/pii-jail/verify-jail.sh`, `tools/pii-jail/gate.sh`, and `tools/pii-jail/leak-scan.py`.
- Code review: load and follow skill `code-review` for its standards, capture, and review workflow.

Use the narrowest existing check that covers the failure. If it does not cover the relevant case, show that gap before recommending a new check.
