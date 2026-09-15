---
name: research
description: >-
  Use for a focused, source-grounded lookup or small investigation: one fact,
  document, API, or a few sources. Route broad comparisons, literature
  reviews, landscape scans, buy/build decisions, or high-stakes investigations
  needing parallel evidence to `deep-research`.
---

# Research

## Contract

**Job.** Answer a focused question for the requesting user and leave an auditable Markdown report in the repo that survives a hand-off.

**Inputs and source order.** Use the user's request first, then supplied prior art, then sources that own the claims (official documentation, source code, specifications, first-party APIs, or the original paper). Treat search results, roundups, and model memory as leads. A prior report is evidence only when its claim is marked `[PRIOR-NOTE]`; reopen it when the claim is load-bearing or may have changed.

**Success predicates.** The run is complete only when:

- the question, audience, date or recency boundary, and out-of-scope items are explicit (or the report says they were not supplied);
- every load-bearing claim, number, date, version, price, and superlative is supported by an opened source or labelled `[PRIOR-NOTE]`;
- source roles, contradictions, and evidence gaps are visible;
- the report is saved with the required shape and the finished report alone is published.

**Output.** Write one dated Markdown report where the workspace already keeps such notes — match the existing convention; if there is none, a `research/` folder under the workspace root — and say where it landed. **NEVER a scratch or trash directory.** Filename: `<topic>-<YYYY-MM-DD>.md`. It contains:

1. goal, audience, scope, date, and a brief method note (verified versus discovered);
2. a cited answer or TL;DR (use at most eight bullets when a summary helps);
3. findings organized by the question's parts;
4. caveats and failure modes;
5. a gap statement, including explicit `could not verify` items;
6. annotated sources with URL, role, support, and retrieval date.

## Route and effort

Choose the smallest branch that can satisfy the contract:

- **Single lookup:** one owner document, API page, or fact. Work inline and cite the exact passage or value.
- **Focused investigation:** a handful of independent sources or a short causal chain. Search sequentially or in a small parallel batch; keep one evidence table and audit it before reporting.
- **Delegated run:** use one background agent only when the user requests it or the reading is long and independent of the work you must continue. Pass an artifact path and structured rows, not a prose relay. Load the worker card in [REFERENCE.md](REFERENCE.md#worker-contract).
- **Deep route:** hand broad, comparative, literature, landscape, buy/build, or high-stakes work to `deep-research` before gathering sources.

A single-owner fact uses inline work or at most one worker. If the task changes branch mid-run, record the new scope and why.

## Literature route

For academic or scientific questions, use the Consensus MCP (`xd://mcp__consensus_search`) when appropriate to discover peer-reviewed literature. If the user asks for full text or full-text excerpts, set `include_full_text_chunks: true`; otherwise leave filters unset unless the user specifies them. Treat Consensus results as literature-discovery and evidence-extraction material: verify important claims against the paper's full text or the publisher/preprint source, and preserve the returned paper URL exactly when citing it. When presenting Consensus-derived findings in the response, cite papers using the numbered references returned by Consensus and include its required source list and usage/sign-up message. In a repository report, use stable paper citations and clearly distinguish Consensus-discovered evidence from independently verified primary-source evidence.

## Recipe

1. **Scope.** Inspect the supplied prior-art locations before the first web search. State the question, audience, decision (if any), recency window, units or definitions that could change the answer, and out-of-scope items. Write a compact plan for more than a single lookup.

   **Done when:** the branch is chosen, prior art is reused or recorded absent, and the scope boundary and stop rule are written down.

2. **Gather.** Discover broadly enough to find the owner, then open the owner source before relying on it. Follow claims to first-party documentation, code, specifications, APIs, or original papers. Use secondary sources only to locate or corroborate an owner source. Record evidence rows as:

   `claim | URL | exact quote or number | retrieval date | source role | status`

   where status is `opened`, `prior-note`, or `discovered-unverified`.

   **Done when:** every requested part has an opened source or an explicit unresolved row; discovery-only items are confined to the gap list.

3. **Synthesize.** Answer the question from the evidence rows; explain why one primary source is sufficient for a single-owner fact. Where a claim has multiple owners, synthesize independent sources rather than stacking quotations. Record both sides of a contradiction and resolve it or carry it into the gap statement. Keep recommendations separate from source claims and name the tradeoff when making a decision.

   **Done when:** each finding maps to evidence, every contradiction has a disposition, and recommendations are distinguishable from facts.

4. **Audit.** Load the citation checklist in [REFERENCE.md](REFERENCE.md#citation-audit). Reopen load-bearing claims and check that each citation supports the exact wording, unit, qualifier, and time boundary. Audit uncited claims as well as cited ones. Apply the stop rule: stop when the scoped parts are covered, load-bearing evidence has been reopened, additional results repeat the existing answer, and remaining uncertainty is named.

   **Done when:** every retained load-bearing claim is cited or marked `[PRIOR-NOTE]`, all unresolved items appear in the gap list, and the audit records pass or the blocker that prevents it.

5. **Report.** Write the final report in the output shape above. Keep the TL;DR within its evidence; label source roles (`[PRIMARY]`, `[INDEPENDENT]`, `[PRIOR-NOTE]`, or `[DISCOVERED, unverified]`) and never use the last label for a retained factual claim.

   **Done when:** a reader can understand and audit the answer without the working notes, the filename and date are valid, and the gap statement is present.

6. **Publish.** The finished PDF report is the deliverable. Deliver only the finished report; do not publish plans, evidence tables, delegated artifacts, or audits. After the report file lands, convert it:

   - Convert next to the Markdown: `pandoc <report>.md -o <report>.pdf --pdf-engine=typst -V papersize=a4 -V margin-x=2cm -V margin-y=2cm -V mainfont="DejaVu Serif" -V monofont="DejaVu Sans Mono" -V fontsize=10pt` (a house typst template can be passed with `--include-in-header`).

   Report the conversion result in your response; if it fails, say so — never skip silently.

   **Done when:** the release gate in [REFERENCE.md](REFERENCE.md#release-gate) passes and the written PDF is reported. Delivery beyond the written file — paste upload, email — belongs to your deployment's report-delivery flow, not this skill.

## Stop, ask, and fallback

Ask one focused question when the audience, decision criterion, date boundary, or safe scope cannot be inferred and choosing it would change the answer. Ask before making a consequential recommendation when the user's tradeoff is unknown. Otherwise make the smallest reasonable assumption and state it.

If a source is unavailable, a page is paywalled, a tool fails, or two sources remain inconsistent, preserve the evidence row, label the gap, and report what could not be verified. Absence of evidence is not evidence of absence. If a delegated worker returns malformed or incomplete rows, request a corrected artifact once; if it remains incomplete, finish with the gap recorded or route to `deep-research` when the scope has grown.

## Durable state

For work that spans turns, preserve the plan, branch, scope assumptions, prior artifacts, evidence-table path, opened-source URLs, decisions, unresolved items, audit result, and next completion predicate in a compact note. The final report is the source of truth for the hand-off; do not require a later agent to reconstruct state from search history.
