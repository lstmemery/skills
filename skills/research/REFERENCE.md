# Research reference

This file holds branch-specific tool and audit detail for
[`SKILL.md`](SKILL.md). Keep the entrypoint focused on the recipe.

## Worker contract

Use only for a delegated focused investigation. Give the worker one bounded
question and this shared contract:

```text
# Target
<focused question>. Non-goals: <parts owned elsewhere>.

# Sources
Inspect supplied prior art first. Find the source that owns each claim:
official docs, source code, specifications, first-party APIs, or original
papers. Search results and secondary pages are leads only. Open every source
before citing it.

# Artifact
Write to the agreed output root as rows:
claim | URL | exact quote or number | retrieval date | PRIMARY, INDEPENDENT,
PRIOR-NOTE, or DISCOVERED, unverified | opened, prior-note, or unresolved

# Acceptance
Every requested sub-question has an evidence row or an explicit could-not-
verify row. Record contradictions without resolving claims owned by another
slice. Return the artifact path and unresolved-items list; do not relay
findings through prose.
```

The orchestrator owns scope changes, synthesis, citation audit, and the final
report. A worker does not publish or queue its artifact.

## Tool cards

Use the runtime's named filesystem and search/open tools; the skill does not
assume a particular provider.

**Filesystem/search.** Select when checking supplied prior art or locating a
candidate source. Input is a read-only path or query; output is paths, titles,
snippets, or result URLs. It has no external side effect. Inspect the returned
item before treating it as a source. Retry one transient tool failure; record a
second failure as an unresolved gap.

**Source open/fetch.** Select for every claim that will appear in the report.
Input is one URL or owner path; output is the retrieved source text or a
structured error. Opening can consume network or retrieval budget but does not
authorize writes. Record retrieval date and exact supporting passage. A
redirect, truncated page, paywall, or malformed result is not evidence: try one
alternate owner URL, then preserve the gap.

**Artifact write/deliver.** Select only after synthesis and audit pass. Input
is the final report path; output is the written file (Markdown, plus the
converted PDF when the recipe's publish step applies). Deliver only after the
release gate below passes; a failed conversion is reported, never skipped
silently.

## Citation audit

Before publishing, inspect the draft and evidence table:

1. Extract every number, date, version, price, named comparison, and
   superlative. Reopen its source or remove/qualify the claim.
2. Confirm the quoted passage supports the exact claim, including units,
   denominator, modality, and time boundary.
3. Check uncited load-bearing prose; add a citation, mark it as a
   recommendation, or drop it.
4. Label source roles. A vendor or author's own result is `[PRIMARY]` and
   remains self-reported; corroboration is `[INDEPENDENT]`.
5. Keep `[DISCOVERED, unverified]` items out of findings and TL;DR; put them in
   the gap statement.
6. Record contradictions with both URLs and the disposition. Do not silently
   average, harmonize, or choose the newer-looking number.

```markdown
# <Specific topic>

**Prepared:** YYYY-MM-DD  
**Audience:** <who will use this>  
**Scope:** <question, date boundary, definitions, and exclusions>

## Method
<What was opened and what was only discovered; prior-art reuse.>

## Answer / TL;DR
- <cited claim>

## Findings
### <question part>
<Evidence-backed synthesis.>

## Caveats and failure modes
<Limits, source conflicts, and decision tradeoffs.>

## Gap statement
<Could not verify: ...>

## Sources
| URL | Support | Role | Retrieved |
|---|---|---|---|
```

Use a comparison table only when the question is comparative. Use a verdict or
recommendation only when the question calls for a decision; state the tradeoff.

## Release gate

Record pass/fail before delivering. All applicable checks must pass:

- scope, branch, prior-art check, and stop rule recorded;
- every requested part has evidence or an explicit unresolved item;
- findings trace to opened sources or `[PRIOR-NOTE]` rows;
- load-bearing cited and uncited claims were audited;
- contradictions are resolved or named in the gap statement;
- report shape, date, filename, and audience are valid;
- the written PDF exists and is reported; delivery beyond the written file
  belongs to the deployment's report-delivery flow, not this skill.

If a check fails, fix it or leave the report undelivered with the blocker
stated.
