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

For procedures that replace credentials or change an existing service, also
verify behavior with synthetic values and mocked effects: installed configuration
shape, activation, already-working state, partial failure, rollback, and
revocation order as applicable. Keep the review evidence with the candidate's
absolute path and SHA-256. Test extracted logic in isolation; do not execute
the human procedure against live services. For an unattended handoff, apply the
[procedure-verification matrix](../orchestrator/references/unattended.md#verify-behavior-of-human-run-procedures)
and its separate-review requirement. A mock pass leaves future human execution
unverified. Link the corrected copy in runnable handoff commands after changes.

Done when the concrete procedure is reviewable, static and applicable behavioral
checks pass or each unrun check is named in the hand-off message with its reason,
and the user has the path and run command. For an authorized repeatable setup
path, commit it and link it from README. Never validate by executing the human
procedure yourself.

## Edit wizards

For an edit wizard, make every screen reviewable on its own. State the source
(`reviewer comment <ID>`, `AI finding <ID> from run <run>`, or `Matt decision`),
and classify the edit as **value correction**, **wording**, or **formatting
only**. For a value correction, say explicitly that the old value is wrong and
identify the source of the replacement value (file and run, when applicable).
For invisible character edits, print both codepoints, such as `U+002D` →
`U+2212`, alongside the characters if useful. Do not label a value correction
as a decimal-places or formatting change.

Combine mechanical bulk edits into one **Find & Replace** step. Show the exact
find pattern, replacement, and expected match count; do not make a screen per
occurrence.

Example screen:

```text
Source: AI finding AF-12, analysis run 2026-10-18-r3
Kind: Value correction — the old value 0.738 is wrong.
Replace with: 0.77 (analysis.csv, run 2026-10-18-r3, column corrected_rate)
```
