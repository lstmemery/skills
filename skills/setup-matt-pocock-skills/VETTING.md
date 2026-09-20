# Vet a candidate skill

Use only when adding or evaluating a skill for this suite.

Read its complete `SKILL.md` and every script it ships or invokes. Check writes, deletions, network sends, credentials, and assumed paths. Popularity, install counts, or a familiar author can prioritize inspection; they are not evidence of what the skill does.

Check fit with the existing library and repo configuration: duplicate workflows, incompatible workspace roots or skill-loading mechanisms, runtime-profile assumptions, and tracker/label vocabulary mismatches. Use the active runtime contract and this repo's `docs/agents/` files as the comparison sources. Prefer one owner for an existing workflow over another copy that can silently drift.
