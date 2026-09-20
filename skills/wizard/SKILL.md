---
name: wizard
description: Generate Bash wizards for human-only setup, credentials, dashboard actions, migrations, or cutovers; handle agent-executable steps directly.
---

# Wizard

Author a Bash script that guides a human through steps only they can perform. **Humans run the wizard; the agent scopes, authors, and verifies it statically. Never run it end-to-end:** it opens browsers, waits for input, and may write credentials or perform irreversible actions.

Use [template.sh](template.sh) as the source of the interaction helpers. Copy its library unchanged above the `STAGES` marker and author only the stages. Wizards are ephemeral by default: use a scratch or `scripts/` path. Commit only when the user wants a repeatable setup path in the repo.

## Scope and author

1. **Read the repo and map the procedure.** For setup, inspect relevant environment-variable declarations/examples, README, compose/framework configuration, and CI `secrets.*`/`vars.*` references. For migration, identify current state, target state, and irreversible actions. Use existing decisions instead of asking cold.
2. **Specify stages and values.** Each stage has one focused task in dependency order. For every captured value, identify where the human gets it, where it goes (`.env`, CI, both, or nowhere), and whether it is secret. Give concrete URLs and UI/command steps. Check current docs or ask about missing information; never invent a dashboard path.
3. **Build the reviewable draft.** Copy the template, replace the example stages, and set `TOTAL_STAGES` accurately. Use its documented helpers. Present the ordered stages, value destinations, and draft for the user's corrections; accepted scope and authorization remain valid.

Open the relevant URL before asking for its value. Use hidden entry for secrets, persist values only to their intended destinations, and send to CI only the secrets it needs. Confirm before every irreversible action. Keep each stage short enough that clearing the screen does not hide information needed for that task. Do not hand-edit the template library.

## Verify and hand off

Run `bash -n <script>` and `shellcheck` if available, then make the script executable. Trace it statically: every scoped value is captured and lands at its declared destination, every CI secret/variable name matches its workflow reference, stages are ordered/count correctly, secrets use hidden input, and irreversible actions have confirmation gates.

Done when the concrete procedure is reviewable, static checks pass or limitations are explicit, and the user has the path and run command. For an authorized repeatable setup path, commit it and link it from README. Never validate by executing the human procedure yourself.
