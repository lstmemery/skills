# Research evidence contract

Shared by focused investigations and deep research. Load when building an
evidence table or checking an investigation's claims. A single lookup keeps
this same substance with its answer without requiring a table.

## Sources and records

Use sources that own the claim: official documents, source code, specifications,
first-party APIs, or original papers. Secondary sources may own independent
analysis or corroborate a claim; search snippets and roundups are discovery
leads until opened and assessed. Label author/vendor measurements as self-reported.

Record:

`claim | URL or supplied source path | exact quote or number | retrieved_at | source_role | status`

Roles: `PRIMARY`, `INDEPENDENT`, `PRIOR-NOTE`, `DISCOVERED, unverified`.
Statuses: `opened`, `prior-note`, `discovered-unverified`, `unresolved`.
Retain units, denominator, version, time boundary, and qualification where they
matter. An unresolved row names the missing evidence and attempted retrieval.

Supplied reports remain `PRIOR-NOTE` evidence until their underlying source is
opened. Reopen prior claims when material to the answer or liable to change.
If reopening fails, preserve the prior-note label and explain its limit.
Discovery-only items belong in gaps, never as retained factual findings.

## Claim check

Before delivery, compare every material claim in the answer, including uncited
prose and the summary, with captured source evidence:

- The passage supports the exact assertion, not a nearby value or stronger claim.
- Numbers, dates, versions, prices, comparisons, and superlatives retain their
  units, denominators, scope, time, attribution, and uncertainty.
- Recommendations are identified as judgments and explain the tradeoff.
- Contradictions retain both sources and a disposition; unresolved conflicts
  stay visible rather than being averaged or silently reconciled.
- Every requested part has support or an explicit could-not-verify statement.

Checking captured evidence is an audit. It does not require fetching the same
complete, sufficiently current passage twice. Fetch again when evidence is
stale for the question, incomplete, ambiguous, contradicted, or missing.
Record pass or the specific gap. Fix, remove, or qualify unsupported wording
before release; an unsupported claim does not pass merely because it has a URL.
