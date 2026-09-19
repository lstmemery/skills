---
name: teach
description: Teach a user a new skill or concept through a stateful seminar workspace.
disable-model-invocation: true
argument-hint: "What would you like to learn about?"
---

# Teach

## Contract

**Job.** Turn the user's mission into durable, transferable capability across
multiple sessions. The user is the learner; the workspace is the source of
continuity.

**Inputs and source order.** Use, in order: the user's current request; the
workspace state; operator teaching preferences; curated primary sources in
`RESOURCES.md`; newly verified sources. Treat model memory as a lead, never as
evidence for a factual or high-stakes claim.

**Success predicates.** A run is complete only when the relevant lesson target
has (a) a learner attempt, (b) contingent help no larger than the diagnosed
gap, (c) an independent verification, (d) a reduced-help or no-help attempt,
and (e) recorded delayed-retrieval and transfer plans or results. Persist the
evidence and the next capability in a learning record.

**Output.** Work inside one seminar directory at the profile's workspace root:

```text
MISSION.md
RESOURCES.md
NOTES.md
lessons/NNNN-<slug>.html
reference/<topic>.html
learning-records/NNNN-<slug>.md
assets/<reusable-component>
```

Use the companion format files for exact templates. Keep lessons short,
keyboard/screen-reader accessible, printable, and usable through a plain-text
path. Link every lesson to the glossary (when present), relevant references,
and the next lesson.

## Route

1. **Load state.** Read `MISSION.md`, `NOTES.md`, prior learning records,
   `RESOURCES.md`, and available assets. Load operator preferences before the
   first lesson. If no mission exists or its *Why* is vague, ask one compact
   question about the concrete outcome; write nothing that depends on the
   answer.
2. **Choose the branch.**
   - New seminar: create the directory, mission, resources stub, notes, and
     shared stylesheet before the first lesson.
   - Continuing: begin with two or three reworded retrieval prompts from prior
     records; do not reteach an established capability.
   - Mission change: confirm the changed outcome, update `MISSION.md`, and add
     a record explaining the change.
   - High-stakes, executable, mathematical, or scientific claim: select an
     external check before teaching it.
3. **Ground.** Find and record high-trust sources before making load-bearing
   claims. Annotate each resource with URL, role, retrieval date, and what it
   supports. Record gaps instead of filling them from memory.
4. **Design one lesson.** Choose one observable capability justified by the
   mission and learner evidence. Put only prerequisite knowledge in the lesson;
   move reusable detail to `reference/`. Reuse assets; create a component only
   when a later lesson can reuse it.
5. **Run the evidence loop** below in the conversation, one compact question at
   a time. Wait for the learner's attempt before escalating help.
6. **Persist and schedule.** Write or update a learning record only for
   demonstrated understanding, a misconception, prior knowledge, or a
   completed delayed/transfer check. Schedule the next no-AI retrieval at a
   longer interval after success; keep fragile skills separate until they can
   be retrieved alone. Update `NOTES.md` with decisions, evidence, blockers,
   and the next predicate, not a transcript.

## Evidence loop

State the target and ask for confidence (0–100) before the answer. Require a
prediction, definition, plan, or attempt. Diagnose the specific error, then
use the smallest sufficient rung:

```text
question → diagnostic feedback → smallest hint → worked substep → full example
```

Pair diagnostic feedback with brief, specific encouragement that supports
persistence without praising completion as mastery.

After a successful assisted attempt, require a same-session attempt with one
fewer rung. End with blank-page retrieval, one near-transfer item, and one
far-transfer item tied to the mission. Mark each result as assisted,
independent, or transfer; compare confidence with performance. Do not infer
learning from immediate correctness, fluent explanations, satisfaction, or
confidence alone.

Verification is separate from generation. Cite the primary source for factual
claims. For code use a compiler, tests, or executable example; for mathematics
use a calculation, counterexample, or theorem prover; for science use the
authoritative source and units; for open-ended claims label them
`conjecture`, `plausible`, `source-supported`, or `independently checked`.
State exactly what was checked. If the check fails or sources conflict, show
the conflict, stop the claim, and record the gap or ask the learner to choose
which authority to follow.

## Lesson contract

Each HTML lesson contains, in this order: masthead and links; title, target,
duration, and mission link; retrieval warm-up; minimal explanation with the
key “why” decisions; an attempt/feedback task; verification; reduced-help
retrieval; near and far transfer; a primary-source assignment with scope; a
plain-text/offline alternative; next review date; and an invitation for
follow-up questions. Use equal-length quiz options where options are used and
shuffle them without formatting clues. Never count an AI-assisted answer as
mastery.

## State schemas

**Learning record.** Follow `LEARNING-RECORD-FORMAT.md`. Add only supported
fields: attempt, diagnosed error, highest support rung, verification and
result, delayed retrieval (date and no-AI result), near/far transfer, confidence
and calibration, and the next move. Number from the highest existing record.

**Mission.** Follow `MISSION-FORMAT.md`: one concrete Why, observable success
criteria, constraints, and out-of-scope boundary. Keep it brief.

**Resources.** Follow `RESOURCES-FORMAT.md`; separate Knowledge and Wisdom,
annotate every entry, and keep a visible Gaps section.

## Stop, ask, and fallback

Stop when the success predicates and required files are satisfied. Ask one
question when the mission, learner attempt, authority for a disputed claim, or
safe next action is genuinely missing. If the learner declines AI or tools,
run the offline/no-AI path. If a source, tool, or verification check is
unavailable, label the evidence missing, preserve the artifact, and schedule a
recheck; do not convert absence into a negative result. Escalate disputed
feedback or high-stakes judgments to a human authority or primary source.

## Privacy and evaluation

Store learning evidence, not sensitive personal details; tell the learner what
is retained when they volunteer private information. Do not require a
proprietary tool for core learning. Over the seminar, track assisted accuracy
and hint rung, delayed no-AI retrieval, near/far transfer, confidence
calibration, learner choice/critique/verification, access mode, attrition, time,
hint exposure, and tool failures. These are measurements, not proof that the
skill is validated; compare with ordinary chat or no-agent work when feasible.

## Pointers

- Read `MISSION-FORMAT.md`, `LEARNING-RECORD-FORMAT.md`, and
  `RESOURCES-FORMAT.md` when creating or editing those artifacts.
- Read `SEMINAR-PREFERENCES.md` from the context root, when the environment
  provides one, before the first lesson in a seminar; otherwise take teaching
  preferences from `NOTES.md`.
- Use the workspace's existing assets and glossary before adding new ones.
