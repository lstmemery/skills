# Evaluation and model notes

Read when evaluating instruction changes or model-dependent behavior in [writing-for-agents](SKILL.md). Ordinary document edits need only checks proportional to what changed.

## Evaluate observable decisions

Choose realistic cases that could expose the changed behavior. Compare outputs with an external oracle: source evidence, schema, artifact diff, test, observed tool effect, or a human judgment about the task. A model critique can propose a failure to investigate; it does not establish one.

For structural or consequential changes, inspect selected branch, files loaded, tool selection and arguments, execution, state change, and final result separately. Cover the affected normal case, an important edge case, and relevant failure/ask/fallback states. A broader suite may include chained dependencies, output shape, authority boundaries, and recovery. Ordinary wording edits do not require every category.

Record skill version, inputs, expected predicates, output artifacts, and available model/snapshot, effort, and tool configuration. Mark unavailable metadata unknown. Keep before/after cases comparable and repeat variable cases before attributing a difference to wording. Report untested behavior as untested.

## Context placement and durable state

Long-context reliability is an evaluation question for the target setup. When a task depends on retrieving critical facts from long context, test realistic placements near the beginning, middle, and end. Keep durable state compact and measure whether a successor can recover decisions, constraints, artifacts, verification, and the next action without reconstructing the whole transcript.

Putting compact invariants near a task or generation boundary, disclosing large references, and summarizing stable facts are candidate interventions. They do not guarantee retrieval or justify repeating the entire contract at every stage.

## Model-specific hypotheses

For Astra-targeted documents, preserve the original evaluation intent: record the actual model/snapshot and configuration when available, verify supported tools and structured-output facilities in the target environment, and test template adherence, ambiguity handling, and continuity through observable results. This reference makes no current product-capability or launch-performance claim.

Treat assumptions about positive versus negative wording, leading words, default thoroughness, context layout, and instruction no-ops as hypotheses that may vary by model and task. Re-run affected cases when the model, prompt, tools, or skill change. Judge correctness, instruction compliance, audience fit, and length separately; eloquence, visible verbosity, or inferred hidden reasoning is not evidence of success.
