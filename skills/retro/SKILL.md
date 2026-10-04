---
name: retro
description: "Review a named or current coding and orchestration session for evidence-backed improvements to an agent's environment. Use after a run with avoidable navigation, checking, steering, tool-use, access, delegation, or decision friction."
disable-model-invocation: true
---

# Retro

The user asked for a retrospective: identify changes to the agent environment that could improve future work. Review the session and its run records; do not redo the task.

## Run

1. Load and follow skill `writing-for-agents` for the writing style. Follow the active runtime profile for readable sources and output location.
2. Read the session the user names. If they name none, use only the current session. For orchestrated work, also read that run's state, brief revisions, worker results, and review files. See [SOURCES.md](SOURCES.md) for Matt's host locations and safe-reading rules. Report inaccessible or missing sources as gaps; do not substitute unrelated sessions.
3. Inspect the relevant steering surfaces and existing checks in [STEERING-SURFACES.md](STEERING-SURFACES.md), following its implementation-versus-review principle.
4. Look for candidates in these categories. Use a category only when its trigger applies:
   - **Navigation** — use when finding relevant files or dependencies took avoidable time.
   - **Automated checks** — use when an error escaped a check, or an existing check is absent, unwired, or silently ineffective. Find the repo's current check before proposing another.
   - **Coding standards** — use when review missed a mistake. A mechanical rule belongs in a deterministic check; a judgment call belongs in reviewer guidance.
   - **Steering-file bloat** — use when guidance is duplicated, unusually large, stale, or does not change behavior. Check all applicable surfaces, not only `AGENTS.md`.
   - **Tool economy** — use when calls, output, or retries were avoidably expensive.
   - **No-ops** — use when an instruction failed to change behavior or caused conflicting behavior.
   - **Information access** — use when needed evidence or a safe read path was unavailable.
   - **Orchestration & delegation** — use for ambiguous briefs, lost prompts, worker or watcher failures, harness/runtime choice, wasted retries, or friction in human decisions.
5. Present findings ranked by severity. For each, give **severity**, **evidence pointer**, **proposed change**, and **cost** (XS/S/M/L with a short estimate). Keep evidence minimal and redacted. Separate verified events from inference. Mark changes already completed as resolved; do not recommend them again as new work.
6. Write the retrospective as a Markdown file at the workspace root, named `retro-<date>-<topic>.md`, unless the user asks for another workspace-relative name.

## Approval and routing

Do not change steering files, memory, code, tracker state, or live settings during analysis. First present the ranked findings. For each item Matt approves, use only the route he selects:

- **Vikunja finding:** file through the tracker MCP in Backlog. Use Queue only when Matt explicitly asks to queue it.
- **Feedback memory:** update the relevant feedback memory under the memory rules; keep it factual and narrowly scoped.
- **Small fix:** make it on an isolated branch, run applicable checks, and leave it for human review. Do not merge or push.

If approval or a route is unclear, save the retrospective and ask before acting. A clear user request to make a specific change counts as approval for that change only.

## References

- [SOURCES.md](SOURCES.md) — session and run-record locations; redaction requirements.
- [STEERING-SURFACES.md](STEERING-SURFACES.md) — Matt's steering surfaces and existing checks.
- [PREP.md](PREP.md) — retro-prep digest-step builder contract and the corpus verification command.
