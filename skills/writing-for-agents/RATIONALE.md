# Rationale and working hypotheses

Read this reference when the compact rules in [SKILL.md](SKILL.md) need explanation or a structural tradeoff is unresolved. These are design heuristics; claimed effects on model behavior require local evidence.

## Contracts and process

An agent-facing document should make correct work recognizable. Outcome, trusted inputs, and completion evidence help a reader assess a result even when several routes are valid. Ordered steps add value when they enforce a dependency or a real check; a fixed ceremony for open-ended work can obscure those decisions.

A useful decomposition starts with one focused call or sequence. Chain subtasks when each depends on the preceding result; route when branches need different rules; parallelize independent work when the separation improves the outcome. Work discovered during execution may justify an orchestrator and workers. These are available shapes, not a mandatory progression through increasingly elaborate workflows.

Detailed interface contracts matter when tool errors have consequences. A tool card can collect purpose, selection, input/output constraints, effects, authority, timeout, retry or idempotency rules, validation, and recovery. Record the parts that are non-obvious and necessary for the operation. Repeating a supplied schema for every ordinary call adds another copy to maintain.

## Hierarchy, pointers, and the two loads

The information hierarchy has three useful levels: in-file steps, in-file reference, and disclosed reference. The main file contains what every relevant branch needs; conditional references hold substantial detail reached only by particular branches. A flat list of jointly applicable rules is a valid reference structure.

Pointers have to carry enough meaning to select their targets. A required target with an ambiguous pointer can be missed even when the target itself is excellent. Sharpen the trigger, then verify the path on a realistic case. If reliable selection still fails for essential material, consider keeping that material in the core.

Moving text to a companion trades its usual loading cost for a pointer and an additional retrieval. Actual **context load** depends on what the harness exposes and what the run reads. Material with no usable discovery path shifts more **cognitive load** to the human remembering it. The aim is to spend both where useful judgment happens, not to minimize file size at any cost.

Co-location prevents a different failure: one concept scattered across headings is harder to interpret as a whole. Duplication repeats a meaning; scattering fragments it. Sprawl can persist even when every sentence is unique, so a branch cut may help more than sentence trimming. Excessive splitting can reverse that gain by creating navigation work.

## Completion, legwork, and sequence cuts

A completion criterion has **clarity** (can the agent tell done from unfinished?) and **demand** (how much coverage counts as done?). Wording such as “every modified model accounted for” expresses a stronger coverage bar than “produce a change list.” **Legwork** is the investigation that satisfying such a criterion requires; it need not be a separate step.

**Premature completion** means stopping before that bar is met. One hypothesis is that visible **post-completion steps**—later work in the same sequence—can draw attention away from a fuzzy current criterion. Sharpen the criterion first. Split a sequence to hide later work only if observations support that diagnosis and a real context boundary is available. An inline file read does not erase earlier instructions; a fresh handoff or bounded worker can establish a different context, subject to the harness and isolation rules.

This hypothesis does not justify removing a caller's required review, confirmation, or return boundary. Validate the current step and the end-to-end outcome together.

## Leading words and negative instructions

A **leading word** compresses a defined concept into a stable term. Familiar vocabulary may recruit useful learned associations; invented terms need enough definition to establish their meaning. Reusing the term can connect a prompt, document, and codebase vocabulary, but the term alone does not guarantee reliable invocation or execution.

For example, “red” can refer to an observed failing feedback loop when the document defines it that way. Calling a loop “tight” may be convenient shorthand for fast and focused, but precise bounds still matter when performance is part of the contract. Measure whether compression preserves the relevant decisions rather than assuming a shorter phrase is stronger.

Positive target instructions often say more clearly what to do next. The stronger original claim—that naming forbidden behavior necessarily makes it more likely—is unverified here and should not be treated as a universal property of models. Explicit prohibitions can be the clearest expression of a safety, scope, or authority boundary. Compare behavior before replacing a working guardrail.

## Pruning and environmental truth

A rule with one owner can change through one deliberate edit. Repeating it elsewhere creates maintenance drift and can accidentally give it disproportionate prominence. This differs from reusing a defined term: the token repeats while its authoritative definition remains in one place.

Environment-derived prose is a cache. A command's current `--help`, a configuration file, or repository scripts may answer cheaply without a second textual copy. Preserve knowledge those lookups do not reveal: local conventions, decision rationale, expensive discovery, and fragile gotchas.

Calling an instruction a no-op is a claim about the model and task, not merely a stylistic judgment. Two reviewers may disagree because they assume different default behavior. A representative outcome check can resolve that disagreement; a preference for brevity cannot. Removing entire irrelevant instructions often helps more than shaving words from live ones.
