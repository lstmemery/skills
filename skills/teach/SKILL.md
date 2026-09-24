---
name: teach
description: Teach a skill or concept through a stateful seminar workspace.
disable-model-invocation: true
argument-hint: "What would you like to learn about?"
---

# Teach

Build durable, transferable capability from the user's mission. The user is the
learner; the seminar workspace preserves continuity.

Use sources in this order: current request, workspace state, operator teaching
preferences, curated primary sources in `RESOURCES.md`, newly verified sources.
Model memory is a lead, never evidence for factual or high-stakes claims.

A lesson completes with a learner attempt, help matched to the diagnosed gap,
independent verification, a reduced-help or no-help attempt, and recorded delayed
retrieval and transfer plans or results. Persist the evidence and next capability.
Immediate correctness, fluency, satisfaction, confidence, and AI-assisted answers
alone do not establish mastery.

## Workspace

Use one seminar directory under the active profile's workspace root:

```text
MISSION.md
RESOURCES.md
NOTES.md
GLOSSARY.md                  # created when demonstrated terms warrant it
lessons/NNNN-<slug>.html
reference/<topic>.html
learning-records/NNNN-<slug>.md
assets/<reusable-component>
```

For a new seminar, `GLOSSARY.md` is the canonical terminology record. Preserve
an existing seminar's established canonical glossary location and format; do not
create a competing record or migrate it merely to match the new-workspace layout.
When creating or updating the glossary, read
[GLOSSARY-FORMAT.md](GLOSSARY-FORMAT.md), including its demonstrated-understanding
gate. Lessons link to the canonical record or a derived browser view. Maintain
each definition once; an optional `reference/glossary.html` view links back to
its canonical source.

Keep lessons short, keyboard/screen-reader accessible, printable, and usable
through a plain-text path. Link to the glossary when present, relevant references,
and the next lesson. Reuse existing assets before creating new ones.

## Route

1. **Load state.** Read mission, notes, prior learning records, resources, and
   available assets. Before the first lesson, read `SEMINAR-PREFERENCES.md` from
   the workspace root that holds the seminar directories, if present; otherwise
   use preferences in the seminar's `NOTES.md` and ask once for a missing
   convention rather than inventing one.
   If the mission is missing or its Why is vague, ask one compact question about
   the concrete outcome and wait before writing dependent content.
2. **Prepare the branch.** A new seminar needs its directory, mission, resources
   stub, notes, and shared stylesheet before the first lesson. A continuing seminar
   begins with two or three reworded retrieval prompts from prior records; avoid
   reteaching established capability. Confirm a changed mission and record why.
   When creating/editing mission or resources, read [MISSION-FORMAT.md](MISSION-FORMAT.md)
   or [RESOURCES-FORMAT.md](RESOURCES-FORMAT.md) respectively.
3. **Ground.** Record high-trust sources before load-bearing claims, with URL,
   role, retrieval date, and what each supports. Preserve resource gaps. Select
   an external check before teaching high-stakes, executable, mathematical, or
   scientific claims.
4. **Design one lesson.** Select an observable capability justified by the mission
   and learner evidence. Include only prerequisites; put reusable detail in
   `reference/`. Add a component only when a later lesson can reuse it.
5. **Run the evidence loop.** Ask one compact question at a time and wait for the
   learner's attempt before escalating help. Use the loop below.
6. **Persist and schedule.** Read [LEARNING-RECORD-FORMAT.md](LEARNING-RECORD-FORMAT.md)
   when writing a record. Record demonstrated understanding, a misconception,
   stated prior knowledge, a mission change, or completed delayed/transfer checks;
   use only fields supported by evidence and number from the highest existing
   record. Schedule the next no-AI retrieval at a longer interval after success;
   keep fragile skills separate until retrievable alone. Update notes with
   decisions, evidence, blockers, and the next predicate, not a transcript.

## Evidence loop

State the target and ask confidence (0–100) before the answer. Require a
prediction, definition, plan, or attempt. Diagnose its specific gap, then use
the smallest sufficient rung:

```text
question → diagnostic feedback → smallest hint → worked substep → full example
```

Pair feedback with brief, specific encouragement without praising completion as
mastery. After an assisted success, require a same-session attempt with one fewer
rung. End with blank-page retrieval, a near-transfer item, and a far-transfer item
tied to the mission. Mark results assisted, independent, or transfer; compare
confidence with demonstrated performance.

Verify independently of generation: cite primary sources for facts; use a compiler,
test, or executable example for code; a calculation, counterexample, or theorem
prover for mathematics; authoritative sources and units for science. Label
open-ended claims `conjecture`, `plausible`, `source-supported`, or `independently
checked`, stating exactly what was checked. If verification fails or sources
conflict, stop the claim, show the conflict, and record the gap or ask which
authority to follow.

## Lesson format

Each HTML lesson contains, in order: masthead and links; title, target, duration,
and mission link; retrieval warm-up; minimal explanation with key why decisions;
attempt/feedback task; verification; reduced-help retrieval; near and far transfer;
a scoped primary-source assignment; plain-text/offline alternative; next review
date; invitation for follow-up questions. Quiz options have equal length, shuffled
order, and no formatting clues to the answer.

## Missing evidence and privacy

Ask one focused question when the mission, learner attempt, disputed authority,
or safe next action is genuinely missing. If the learner declines AI/tools, use
the offline/no-AI path. An unavailable source, tool, or check is missing evidence,
not a negative result: preserve the artifact and schedule a recheck. Escalate
disputed feedback or high-stakes judgments to a human authority or primary source.

Retain learning evidence, not sensitive personal details; explain retention when
private information is volunteered. Core learning must not require a proprietary
tool. Across the seminar, track assisted accuracy/hint rung, delayed no-AI
retrieval, near/far transfer, confidence calibration, learner choice/critique/
verification, access mode, attrition, time, hint exposure, and tool failures.
These measurements do not validate the skill by themselves; compare with ordinary
chat or no-agent work when feasible.
