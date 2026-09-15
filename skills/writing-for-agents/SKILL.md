---
name: writing-for-agents
disable-model-invocation: true
description: Writing documents for agents. Use when creating or editing skills, or modifying AGENTS.md or CLAUDE.md.
---

Reference for writing any document an agent consumes: a skill, an `AGENTS.md` / `CLAUDE.md`, a doc reached by a pointer. The packaging differs; the writing does not: the same levers make each one predictable, since the agent takes the same _process_ every run rather than producing the same output.

When the document you're writing is a skill, read [`SKILL-MECHANICS.md`](SKILL-MECHANICS.md) for frontmatter, invocation choice, and router skills.

## The skill contract

Write an agent-facing document as an executable contract. Put the contract near the top of the document, then disclose rationale and branch-specific reference. The contract names:

- **Job and audience**: what outcome the document owns and who consumes it.
- **Inputs and trusted sources**: what the agent may use, and which source wins on conflict.
- **Success predicates**: independently observable conditions for a correct result.
- **Output shape**: schema, required fields, length, and audience when shape matters.
- **Process choice**: the dependencies, gates, and branch rules that justify ordered work.
- **Tool cards**: purpose, selection rule, input/output schemas, side effects, authorization, timeout, retry, validation, and recovery.
- **Stop, ask, and fallback rules**: when to finish, record a blocker, request a decision, or report missing evidence.
- **Durable state and evidence**: what survives a hand-off or compaction, and where artifacts, decisions, and verification live.

Treat each item as a predicate or decision rule that a reviewer, script, or tool result can inspect. Keep narrative rationale in disclosed reference. A contract can be short; its checks must be complete.

## Context pointers

A **context pointer** is a reference held in the agent's context that names some out-of-context material and encodes the condition for reaching it. A skill's description is one; a line in `AGENTS.md` naming a doc is the same object. The pointer's _wording_, not its target, decides when the agent reaches the material, and how reliably. A must-have target behind a weakly worded pointer is a variance bug: sharpen the wording first, and inline the material only if sharpening fails.

A pointer does two jobs: state what the material is, and list the **branches** that should trigger reaching it (a branch is a distinct case the document handles, so different runs take different paths through it). Every word of an always-loaded pointer costs on every turn, so it earns even harder pruning than the body:

- **Front-load the leading word**: the pointer is where it does its triggering work.
- **One trigger per branch.** Synonyms that rename a single branch are one branch written twice; collapse them and keep only genuinely distinct branches.
- **Cut identity the body already carries.**

## The two loads

Every document and pointer you add spends one of two budgets:

- **Context load** is the cost of always-loaded material on the agent's window: an `AGENTS.md` line, a skill description, anything sitting in context every turn, spending tokens and attention whether or not it fires.
- **Cognitive load** is the cost on the human: which documents exist and when to reach for each. The human is the index. Not a cost to minimise: it is the price of human agency; spend it where human judgement matters, remove it where it does not.

Material reached only through a pointer escapes context load at the price of the pointer's own line; material with no pointer at all rides entirely on cognitive load.

## Information hierarchy

A document is built from two content types: **steps** (the ordered actions the agent performs) and **reference** (definitions, rules, facts consulted on demand). The two mix freely: all steps (a recipe), all reference (a review's rules, this skill), or both. The core decision is where each piece sits on the **information hierarchy**, a ladder ranked by how immediately the agent needs the material:

1. **In-file step** is the primary tier: what the agent does, in order.
2. **In-file reference** is consulted on demand. Often a legitimately flat peer-set (every rule of a review on one rung), which is a fine arrangement, not a smell.
3. **Disclosed reference** is pushed out into a separate file, reached by a context pointer, loaded only when the pointer fires. Spans a sibling file in the same folder through fully external reference that lives anywhere and any document can point at.

Push too little down and the top bloats; push too much and you hide material the agent actually needs. That tension is the whole decision.

**Progressive disclosure** is the move down the ladder (out of the main file and behind a pointer) so the top stays legible. Not primarily a token optimisation: it is how the hierarchy is protected. Branching is the cleanest disclosure test: inline what every branch needs, and push behind a pointer what only some branches reach. When a document has steps, in-file reference that should be disclosed buries them and turns attending to them into a coin-flip: a variance lever, not just a legibility one.

**Co-location** is the within-file companion: where the ladder decides _how far down_ a piece sits, co-location decides _what sits beside it_ once there. Keep a concept's definition, rules, and caveats under one heading rather than scattered, so reading one part brings its neighbours with it. The test: the document should read like documentation written for the agent. Grouped material reads that way; scattered material does not. (Distinct from duplication: that repeats one meaning in two places; scattering fragments one meaning across many.)

**Sprawl** is the failure mode here: a document simply too long, even when every line is live and unique. Attention thins across the excess, and every extra line is one more to keep relevant. The cure is the ladder: disclose reference behind pointers, and split by branch or sequence so each path carries only what it needs.

## Outcome-first process

State the destination, success predicates, constraints, and available context before prescribing a route. Let the agent choose an efficient path when the route is open. Keep ordered steps when a dependency, safety boundary, hand-off, or intermediate gate requires them; remove ceremonial choreography that adds no check or decision.

Use this decomposition rule:

- Start with one focused call or sequence.
- Chain subtasks when their order is fixed and each has a gate.
- Route when branches need different expertise or rules.
- Parallelize when work is independent and separate perspectives improve confidence.
- Use orchestrator-workers only when subtasks cannot be predicted in advance.

Each added branch, gate, or hand-off earns its maintenance and context cost through a measurable reliability gain. State the invariant that selects a branch and the predicate that closes it.

## Steps and completion criteria

Every step ends on a **completion criterion**, the condition that tells the agent the work is done. Two properties make it a lever:

- **Clarity**: can the agent tell done from not-done? A vague bound ("understanding reached") invites **premature completion**: ending the step before it is genuinely done, attention slipping to _being done_. The visible steps still ahead (the **post-completion steps**) supply the pull; the criterion's clarity is the resistance. Defend in order: **sharpen the bound first** (local and cheap); only if it is irreducibly fuzzy _and_ you observe the rush, hide the later steps by splitting the sequence. Hiding only works across a real context boundary (a hand-off or a subagent dispatch; an inline call leaves the later steps in context and clears nothing).
- **Demand**: how much it requires. "Every modified model accounted for" forces thorough work where "produce a change list" does not. Demand drives **legwork** (the digging the agent does within the work, latent in the wording rather than written as its own step), and it is not step-bound: "every rule applied" binds a body of flat reference just as "every step done" binds a sequence, which is how an all-reference document still carries an exhaustiveness bar.

The strongest criteria are both checkable and exhaustive. Prefer atomic predicates: a file exists, a schema validates, a test passes, a citation is present, a side effect is observed, or a blocker is recorded. Include an explicit **evidence missing** state; absence of evidence is not a factual negative. Completion criteria should also name the ask state when the agent cannot proceed safely.

## When to split

Splitting one document into two spends one of the two loads, so split only when the cut earns it:

- **By sequence**: split a run of steps where the post-completion steps tempt the agent to rush the one in front of it. Keeping them out of view drives more legwork on the current task. Beware the reverse: merging sequences exposes each step's later steps to what follows, inviting premature completion.
- **By invocation**, skill-specific: see [`SKILL-MECHANICS.md`](SKILL-MECHANICS.md).

## Context layout and durable state

Treat context as a budget even when the model advertises a large window. Long-context retrieval has position effects and quality degrades at high water marks. Put identity, non-negotiable invariants, the current task, and the output contract at a context boundary (the beginning or the point of generation). Disclose large references, summarize what is stable, and repeat only the compact contract needed at output time. Test critical facts at the beginning, middle, and end of realistic contexts.

When work spans turns, hand-offs, or compaction, preserve a compact state record: decisions, constraints, artifact identifiers, verification results, blockers, unresolved questions, and the next predicate. Drop transient narration and repeated search history. Pass the state forward as the source of truth; do not require the next run to reconstruct it from a full transcript.

## Tool cards and consequential actions

Write the agent-computer interface as a tool card whenever a document authorizes tools:

1. **Purpose and selection** — what the tool accomplishes and the condition for choosing it.
2. **Schema** — machine-readable input and output fields, types, required values, and bounds.
3. **Effects and authority** — reads, writes, external side effects, credentials, and the approval boundary.
4. **Execution policy** — timeout, retry conditions, idempotency, and rate or retrieval budget.
5. **Observation and recovery** — how to validate the result, record evidence, handle a malformed result, and recover or stop.

For multi-step tool work, make the loop visible: **plan → act → observe → checkpoint**. Distinguish function calls that connect the agent to tools from structured formats used for the final user-facing result. Put a human denial or approval point on sensitive calls when the surrounding system supports one.

## Verification and evaluation

Ground reflection in an external oracle. A test, schema validator, tool result, citation check, artifact diff, or human decision must be able to trigger a change. A model-generated critique without an oracle is a hypothesis, not evidence; omit a generic “critique your answer” step.

Evaluate the harness as well as the prose. First capture a small trace set and inspect plan, tool choice, parameters, execution, state change, and final outcome separately. Then run a versioned regression set covering atomic constraints, chained dependencies, context placement, tool failures and fallbacks, refusal or ask states, and output shape. Record the model snapshot, reasoning effort, tool configuration, skill version, and artifacts for each run. Promote a revision only when critical predicates hold without regression.

## Length, shape, and audience

Treat length and style as separate product constraints from correctness. Replace “be detailed” with a target shape when shape matters (for example, a three-paragraph explanation and a five-row table). Evaluate factual correctness, instruction compliance, audience fit, and length independently; longer text is not evidence of better reasoning.

## GPT-6 Astra profile

For Astra-targeted documents, record the pinned model snapshot, reasoning effort, available tools, and skill version in every evaluation. Use its documented structured outputs, function calling, MCP, skills, and selective-context capabilities, while retaining schemas, gates, and external checks. Treat launch claims about template adherence, ambiguity handling, and continuity as hypotheses until the local snapshot eval confirms them. Judge quality through observable traces, state changes, schema validity, citations, tests, and user-visible claims; hidden chain-of-thought length or eloquence is not evidence. Re-run the regression set when the alias, snapshot, tool set, or prompt changes.

## Leading words

A **leading word** is a compact concept already living in the model's pretraining that the agent thinks with while running the document (_lesson_, _fog of war_, _tracer bullets_). Repeated as a token, never as a sentence, it accumulates a distributed definition and anchors a whole region of behaviour in the fewest tokens, by recruiting priors the model already holds. Coining your own works if you define it clearly, but a made-up word recruits no priors: you pay in definition tokens what a pretrained word gives free; reach for an existing word first.

It anchors twice. In the body, _execution_: the agent reaches for the same behaviour every time the word appears, and inside flat reference it focuses attention on a class of thing to look for. In a pointer, _invocation_: when the same word lives in your prompts, your docs, and your codebase, the agent links that shared language to the material and reaches it more reliably.

Hunt for opportunities to refactor with leading words. A triad spelled out at three sites, a pointer spending a sentence to gesture at one idea. Each is a passage begging to collapse into a single token:

- "fast, deterministic, low-overhead" → _tight_ (a _tight_ loop).
- "a loop you believe in" → _red_, turning a fuzzy gate into a binary observable state (the loop goes _red_ on the bug, or it doesn't).

You win twice: fewer tokens, and a sharper hook for the agent to hang its thinking on. Assume every document is carrying restatements that leading words retire. Go find them.

**Negation** is the failure mode beside this lever: steering by prohibition drags the forbidden behaviour into context and makes it _more_ available, not less. _Don't think of an elephant_, and the elephant is all there is; the negation is a weak modifier the strongly-activated concept overruns, so the ban half-reads as an instruction to do the thing. Prompt the **positive**: state the target behaviour ("write one-line comments") so the banned one is never spoken. A prohibition earns its place only as a hard guardrail you cannot phrase positively; even then, pair it with the positive target so attention lands on what to do.

## Pruning

- Keep each meaning in a **single source of truth**: one authoritative place, so changing the behaviour is a one-place edit. **Duplication** (the same meaning in more than one place) costs maintenance and tokens, and inflates a meaning's prominence on the ladder past its real rank. (The accidental inverse of a leading word, which repeats a token on purpose, never the meaning.)
- The **environment** is a source of truth too (`package.json` scripts, config files, the directory layout, `--help` output), and a document that restates it is a **cache**: a copy of a lookup, earning its load only when the lookup is expensive. Cache what the agent cannot find by looking: the unwritten convention, the reason behind a choice, the gotcha no config confesses. Leave the one-file, one-command lookups to the environment, where they cannot go stale.
- Check every line for **relevance**: does it still bear on what the document does? A line loses relevance by never bearing on the task (mere exposition, or a branch that should be disclosed) or by going stale as the behaviour or world it describes changes. Shorter documents are easier to keep relevant. Without a pruning discipline the default fate is **sediment**: stale layers that settle because adding feels safe and removing feels risky, until you must core down through them to find what is still live.
- Hunt **no-ops** sentence by sentence: an instruction the model already obeys by default pays load to say nothing. The test (does it change behaviour versus the default?) is model-relative, not reader-relative: two people disagreeing about a no-op disagree about the default, and settle it by running the document, not by debate. When a sentence fails, delete the whole sentence rather than trim words from it. The test also grades leading words: a word too weak to beat the default (_be thorough_ when the agent is already thorough-ish) is a no-op, and the fix is a stronger word (_relentless_), not a different technique.
