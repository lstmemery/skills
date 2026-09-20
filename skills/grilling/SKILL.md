---
name: grilling
description: Stress-test a plan, decision, or idea through a relentless interview when the user asks to be grilled.
---

# Grilling

Reach a shared understanding by working a **design tree**: decisions and the
decisions that depend on them. Finish when every relevant branch is settled or
explicitly excluded, no material decision is silently assumed, and the user
confirms the concrete understanding. Existing explicit decisions count; do not
ask the user to approve the same choice again.

## Rounds

The **frontier** contains all material decisions whose prerequisites are
settled. Ask the whole frontier in one round and wait for its answers before
asking dependent questions. An answer that depends on another open question
belongs to a later round. Recompute after each answer; prune branches the
selected scope makes irrelevant.

For each question give real alternatives and a recommendation. Put evidence,
tradeoffs, and disagreement with the premise in surrounding prose; make the
question itself the decision. Avoid asking the user for routine implementation
choices already determined by the agreed scope.

Use the harness's interactive question facility when available, with one call
carrying the whole frontier. Follow its actual schema and limits, concise labels,
and recommendation support; never add an Other choice when the UI supplies one.
When no interactive facility exists, read
[QUESTION-FORMAT.md](QUESTION-FORMAT.md) for the inline format.

A bare `A` or `a` means agreement with every recommendation in the current
round. Record those decisions and advance without asking for elaboration.
Silence is not an answer, approval, or selection of the displayed default.

## Facts and records

Find facts yourself. A quick check stays inline; delegate a substantial,
independent factual sweep when its value earns the overhead and delegation is
available. Give it a bounded question and permitted sources. While it runs,
ask unrelated frontier questions; only its dependent decisions wait.

Keep a compact record of settled decisions, rejected alternatives with material
reasons, unresolved prerequisites, and the next frontier. The user owns design
choices; observations and proposals remain distinguishable from those choices.

Prepare authorized reversible drafts and supporting documents so the final
shared-understanding check has a concrete result to inspect. Before executing
a still-unconfirmed design, obtain confirmation of that result; cite the
remaining decision rather than adding a generic approval cycle. Confirmation
does not grant unrelated publication, installation, or external-action authority.
