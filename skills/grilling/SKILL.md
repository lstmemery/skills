---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round, then wait for the user's answers before the next round.

**Route the round through the harness's interactive ask-question facility** when
the profile provides one — one call per round carrying every frontier question,
never one call per question. On the **host** profile that facility is the `ask`
tool, and it takes:

- `question` states the decision; `header` is a short display chip.
- `options` are the real alternatives, 2–5 of them. Keep `label`s short and put the tradeoff in each option's `description`.
- `recommended` marks your answer. You always have one — an unranked menu is not a grill.
- `multi: true` only when the decision genuinely takes several answers at once.
- Never add an "Other" option; the UI appends "Other (type your own)" itself.

A harness with no such facility formats the round inline instead, one block per
question, and the requirements are the same — numbered questions, real
alternatives, a marked recommendation:

```
❓ **Q1** — **<question title>**: <question body, may be several paragraphs, including the choices>

➡️ <your recommended answer>

---

❓ **Q2** — **<question title>**: <question body>

➡️ <your recommended answer>
```

Either way the evidence goes in the surrounding message text, not inside the
options: what you measured, what it rules out, and where you disagree with the
user's premise. The question carries the _decision_; the prose carries the _case_.

Shorthand: a reply that is just `A` (or `a`) means "I agree" — take every recommended option for that round and move on without asking the user to elaborate.

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), look it up yourself — a quick check runs inline; a substantive sweep goes to a **sub-agent** — and never ask the user for anything you could look up yourself. Don't block on a running exploration: it is an unsettled prerequisite, so only the questions downstream of it wait for the answer; ask the rest of the frontier now. The _decisions_ are the user's: put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.
