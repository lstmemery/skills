# Design It Twice

Read when exploring alternative interfaces for a **chosen** deepening candidate.
Use [SKILL.md](SKILL.md) and the project's `CONTEXT.md` for consistent vocabulary.
Finish with two materially different designs, their tradeoffs, and a recommendation;
implementation follows the user's selected design and calling workflow.

## Frame

Present the candidate's constraints and dependency categories from
[DEEPENING.md](DEEPENING.md), with a small illustrative sketch when needed to make
them concrete. The sketch frames the problem; it is not the selected design.

## Contrast

Start with **two contrasting designs**. Work inline when the candidate is bounded;
use independent parallel agents when separate exploration would materially improve
the comparison. Give each an accessible, bounded brief: relevant files, coupling,
dependency categories, what sits behind the seam, and shared domain/design terms.

Choose constraints that create a real tradeoff, for example:

- Minimize the interface and maximize leverage per entry point.
- Optimize flexibility and extension, or make the most common caller trivial.

Both designs must satisfy the same actual requirements. Each provides:

1. Interface: types, methods, parameters, invariants, ordering, and error modes.
2. A caller usage example.
3. Behavior and complexity hidden behind the seam.
4. Dependencies and adapters.
5. Tradeoffs in depth, locality, and seam placement.

Add another design only when a named tradeoff remains unresolved; target that
question, such as the consequences of a ports-and-adapters design. Agent count
is not a completion criterion.

## Recommend

Present each design, compare them, and recommend the stronger fit with reasons.
Propose a hybrid only when its combination resolves a concrete tradeoff. Record
unresolved decisions instead of implying the user has selected a design.
