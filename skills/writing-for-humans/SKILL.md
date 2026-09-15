---
name: writing-for-humans
description: Copy-edit a model's human-facing prose for clarity, flow, and reader effort while preserving meaning, evidence, uncertainty, and the requested voice. Use when a draft, answer, report, progress update, or explanation needs a final readability edit; skip code, data, quotations, and user-requested wording unless the user asks for those edits.
---

# Writing for humans

## Contract

**Job.** Turn an existing agent draft into prose a busy human can scan, understand, and use. This is a copy edit: preserve the draft's claims, scope, evidence, uncertainty, citations, and requested tone while reducing avoidable reader effort.

**Inputs and authority.** Use the user's request and source draft as the source of truth. Preserve quoted text, code, commands, identifiers, numbers, links, citations, legal or safety wording, and explicit uncertainty unless the user authorizes substantive rewriting. If a readability improvement would change a claim, ask or leave that passage unchanged. Do not invent evidence, examples, transitions, or conclusions.

**Success predicates.**

- The first useful sentence gives the reader the answer, outcome, or purpose.
- Each paragraph has one job, starts with its point or a clear transition, and follows a visible order.
- Headings, lists, and tables expose the document's structure when they reduce search effort; headings describe the content beneath them.
- Subjects and actions are easy to identify. Familiar words replace needless jargon; necessary technical terms are defined at first use.
- Sentences carry one main idea, use concrete verbs, and vary in length naturally. Split a sentence when its clauses impose avoidable memory load; keep a longer sentence when the relationship or qualification would be lost.
- The edit removes repetition, throat-clearing, filler, inflated phrasing, vague references, mannered metaphor, and unexplained shorthand.
- The edit keeps the original facts, qualifiers, attribution, links, citations, examples, and level of detail needed for the reader's decision.
- The finished prose fits the requested length and format. It is readable without becoming choppy, childish, falsely certain, or generic.
- A final meaning check finds no changed number, actor, causal strength, time, modality, or source attribution.

**Output.** Return the edited prose in the input's format. When useful, add a short Edit note with 1–3 material choices; omit it when the user asked for only clean copy. Never expose this contract, a hidden critique, or internal reasoning.

## Process

1. **Set the reader contract.** Infer or use the named audience, task, medium, tone, length, and required format. If the audience is missing, choose a general informed reader and retain domain precision. If two plausible audiences would require materially different facts or tone, ask one concise question before changing the draft.
2. **Map before polishing.** Write a one-line private summary of the draft's purpose and list its load-bearing claims, caveats, citations, and requested actions. Identify the answer, the reason it matters, and the next action. Move existing material to that order; do not add content.
3. **Edit structure first.** Lead with the answer or purpose. Use a descriptive heading when a section needs a signpost. Put the important qualification beside the claim it limits. Give each paragraph one topic and use a list for genuinely parallel items or ordered steps. Keep headings short, specific, and front-loaded.
4. **Edit sentences and words.** Put the actor and verb near the front when that clarifies responsibility. Prefer concrete verbs, everyday words, and one stable term for each concept. Replace noun-heavy phrases, false starts, double negatives, redundant pairs, vague this/it/there references, and unexplained acronyms. Use active voice when it makes the actor or action clearer; retain passive voice when the actor is unknown, irrelevant, deliberately backgrounded, or the passive is the clearest conventional form. Keep needed hedges such as may, suggests, and we cannot verify.
5. **Edit for human rhythm.** Break overloaded sentences at a natural boundary. Vary short and medium sentences. Keep a longer sentence when it efficiently expresses a dependency, contrast, or condition. Remove decorative metaphor and stock filler when a literal phrase says the same thing. Use bullets, tables, emphasis, and whitespace when they support the reader's task; do not format every paragraph.
6. **Run the oracle.** Compare the edit against the draft, checking every number, named entity, actor, qualifier, citation, link, code span, quotation, and requested action. Check that headings match their sections, list order is intact, and no paragraph hides a necessary condition. If a change cannot be checked, revert it or mark the uncertainty.
7. **Stop.** Return when every changed passage passes the meaning check and the output satisfies the requested shape. Ask only when a safe copy edit cannot resolve an audience, factual, or scope conflict; otherwise preserve the passage and report the limitation briefly.

## Quick checks

Use these as prompts for inspection, not as universal laws:

- **Lead:** Can a skimming reader find the answer or purpose in the opening?
- **Map:** Can each heading or paragraph be summarized in a few words?
- **Actors:** For each important action, can the reader tell who does it?
- **Load:** Does any sentence hold multiple independent claims that should be separated?
- **Words:** Is a familiar word available? Is every technical term needed and explained?
- **Evidence:** Did the edit preserve attribution, uncertainty, numbers, and links?
- **Shape:** Does the result match the requested medium, length, and formatting?
- **Read aloud:** Where a sentence is hard to say or follow, revise its structure rather than merely shortening it.

Do not force a grade level, a fixed sentence length, active voice, or a short answer when the audience, subject, or evidence calls for something else. Readability formulas are diagnostics; comprehension and meaning preservation decide.

## Model notes

Keep this skill portable. Apply the same contract to GPT-6 Astra, Claude Opus, Claude Fable, and other models. For Astra, keep the skill small and contextual: load it for a human-facing copy edit, not for every code or data task. Astra's official guidance favors concise, contextual skill descriptions and progressive disclosure; the skill's completion predicates and meaning check are the relevant persistent contract.

For Claude Opus, explicitly request the desired visible length when the product needs it; effort changes thinking more reliably than visible response length. For Claude Fable, name the desired writing density and formatting when needed: ask for literal, direct prose, complete sentences, and the amount of structure the content warrants. These are model-specific steering hints, not replacements for the copy-edit contract. Re-run the checks after changing a model, prompt, or skill version.

## Evaluation

Test the skill with a small, versioned fixture set and an external comparison:

1. Dense technical explanation: verify lead, paragraph map, definitions, and preserved caveats.
2. Research summary: verify every citation, number, attribution, and uncertainty survives.
3. Progress update: verify outcome-first reporting and no invented status.
4. User-specified warm or formal voice: verify the voice survives while filler is removed.
5. Deliberately difficult case: verify a necessary passive, quotation, code block, table, and long conditional sentence are preserved where clearer.

For each fixture, record model and snapshot, effort, prompt, input, output, changed claims, and human judgments of clarity, usefulness, tone, and faithfulness. Treat readability scores as secondary diagnostics. Promote a revision only when meaning preservation and audience fit hold without regressions.
