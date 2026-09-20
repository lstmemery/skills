# Work through an existing map

Apply [SKILL.md](SKILL.md)'s map rules and session limit.

1. **Load the map** at low resolution, orient to Destination and Notes, and query its frontier; do not preload every ticket body.
2. **Choose and claim.** Use the user's named ticket, otherwise the first frontier ticket in tracker order. Recheck blockers and claims before assigning it to the driving dev. A blocked or already claimed named ticket needs its dependency/ownership resolved before work. If the frontier is empty, distinguish active claims or blockers from remaining fog and from a completed map.
3. **Resolve the question.** Load only relevant related/closed tickets, the skills named in Notes, and the ticket type's skills. When the decision method is unclear, use `grilling` and `domain-modeling`. Human tickets require the live human exchange. Research follows the core's proportional delegation rule.

## Record and update

Post the answer as a resolution comment, close the ticket, and append its linked title and gist to Decisions so far. For local files, use the shared helper’s `resolve` operation with the accepted answer, gist, and evidence; it updates the answer, status, and map together with recoverable steps. Preserve the claim ID returned when taking the ticket. Link any result assets and evidence.

Refresh current tracker state before edits. Create newly precise tickets and then wire their dependencies; remove graduated fog patches. If the answer exposes work beyond the destination, close the affected ticket with an explanation in Out of scope instead of resolving it on the route. Update or retire invalidated tickets while preserving evidence of their disposition and concurrent sessions' edits.

A session is complete when its resolution and map updates are recorded, or its blocker is explicit with the next action. Report by ticket/map title. Stop after one non-research ticket, even if its resolution opens the next one.
