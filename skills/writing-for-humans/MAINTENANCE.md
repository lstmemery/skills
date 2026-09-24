# Maintenance and evaluation

Read when changing or evaluating [writing-for-humans](SKILL.md). The routine workflow remains one editing pass followed by comparison with the source.

## Fixtures and oracles

Use a small, versioned set of drafts covering the behavior changed by a revision:

| Fixture | Check against the input and requested result |
|---|---|
| Dense technical explanation | Clear lead and paragraph order; necessary definitions and caveats survive. |
| Research summary | Every citation, number, source attribution, and uncertainty survives. |
| Progress update | Outcome is findable; status, ownership, and next actions are not invented or strengthened. |
| Requested warm or formal voice | Tone remains recognizable while repetition and filler are removed. |
| Deliberately difficult draft | Necessary passive voice, quotation, code block, table, and long conditional remain intact where they are clearer. |

Meaning preservation is checked against the source draft, not against the editor's account of what it changed. Include at least one trap relevant to the revision: “may” becoming “will,” correlation becoming causation, a changed number, a citation assigned to a broader claim, or an actor silently replaced. A human judgment can assess clarity and voice; a diff can identify passages needing comparison. Readability formulas are secondary diagnostics.

Record input, output, user constraints, prompt/skill version, available model/snapshot and effort, detected meaning changes, and judgments of clarity, usefulness, tone, and faithfulness. Mark unavailable metadata unknown. Compare representative cases before and after changing the workflow; repeat variable cases before attributing an outcome to the wording. Report fixtures not run as untested.

## Model notes

The copy-edit contract is model-neutral. The user's voice, evidence, and format requirements govern every model. Recheck affected fixtures after a model, prompt, or skill change; do not add a model-specific branch to ordinary copy editing without an observed need.
