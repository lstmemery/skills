# Focused investigation reference

## Investigation report

Use these fields when several claims or sources need a durable report. Combine
sections when short; a single lookup follows the concise branch in SKILL.md.

- Question, audience, preparation date, scope and recency boundary.
- Cited answer, followed by findings organized around the requested parts.
- Brief method: prior-art reuse, sources opened, and limits of verification.
- Caveats, contradictions, and explicit could-not-verify items.
- Sources annotated with support, role, and retrieval date.

Use a comparison table or recommendation only when the question calls for one.
The answer must stand alone without the working notes. Before delivery, confirm
the shared [evidence contract](EVIDENCE.md), valid filename/date, and the active
runtime's delivery requirements. Deliver only the finished report.

## Worker contract

For a delegated focused investigation, give one bounded question, non-goals,
source context, and a path in the active output root. Include or provide access
to [EVIDENCE.md](EVIDENCE.md); the worker must read it.

The worker returns an artifact containing evidence rows for every requested
part or an explicit unresolved row, plus contradictions and retrieval gaps.
Pass the artifact path and unresolved-items list, not an unsupported prose
relay. The coordinator reads the artifact and owns scope changes, synthesis,
claim checking, and final delivery. A worker never queues its slice artifact.

## Retrieval and worker recovery

Use the runtime's filesystem, search, and fetch tools. Search results locate
sources; open/fetch yields source text or an error. Validate the source identity,
retrieval date, and supporting passage. A redirect, paywall, truncated result,
or malformed response does not establish the missing claim. Try one alternate
owner URL or one transient retry, then retain a gap if it remains unavailable.

If a worker's artifact is malformed or incomplete, request one correction.
If it remains incomplete, narrow the answer with the gap visible, or route to
deep-research when the actual question has expanded. No retrieval or artifact
operation authorizes unrelated account writes or external messages.
