---
name: to-tickets
description: "Split a plan, spec, or conversation into small, business-valued tickets with validated blocking edges and publish them to the configured tracker."
disable-model-invocation: true
---

# To Tickets

**Job.** Turn a plan/spec or current conversation into AFK-ready implementation tickets. Each ticket is a small, demonstrable vertical slice with observable acceptance criteria and explicit blockers.

**Inputs and authority.** Use explicit user decisions first, then the supplied spec/conversation, repository glossary and ADRs, and tracker conventions. Fetch a referenced spec or issue in full, including comments. If tracker vocabulary is unavailable, stop and direct the user to `/setup-matt-pocock-skills`.

**A ticket is publishable only when:**

- it names one narrow, usable business outcome and the end-to-end boundaries it crosses, without duplicating another ticket's scope;
- acceptance criteria are observable and sufficient to write a test before sizing or publication;
- it is independently verifiable in one reviewable change and fits the project's normal iteration; split or make a bounded spike when uncertainty prevents that;
- every `Blocked by` reference names a real ticket, has no self-edge, participates in an acyclic graph, and reverse edges agree;
- the graph has at least one unblocked frontier ticket;
- no first step waits for a human decision. Route human-only work through `wizard`, and route unresolved product/design choices back to `to-spec`;
- ticket-specific acceptance stays separate from the repository Definition of Done/verification policy.

## Process

1. **Gather.** Read the source and relevant domain/ADR/tracker docs. Search existing issues for duplicates when the tracker supports it. Identify the user outcome, constraints, and unresolved decisions.
2. **Slice.** Draft tracer-bullet tickets that cross the necessary layers and are demoable alone. Preserve business value in every split; a layer-only task belongs under a value-bearing ticket or is a bounded research/prototype task. State the verification path and acceptance criteria for each ticket.
3. **Handle dependencies.** Add an edge only for a genuine product or technical gate, never a preferred implementation order. For a wide mechanical refactor, use explicit **expand → migrate batches → contract**: migration batches block on expand; contract blocks on every batch; verify compatibility at each phase. If a batch cannot stay green alone, use an integration-and-verify ticket and say so.
4. **Validate.** Apply the publishability gate above once to the complete graph. Record `Evidence missing: …` for unknowns. Return unestimable or unverifiable work to the spec's unresolved decisions or make it a bounded spike; a manual-only skill is not implicitly invoked by that return.
5. **Review.** Present the numbered graph with each title, outcome, acceptance criteria, and blockers. Ask only: is granularity right, are blockers genuine, and should anything merge/split? Iterate until approved. If approval is unavailable, keep the draft unpublished.
6. **Publish.** Publish blockers first using the configured tracker and apply `ready-for-agent`; the frontier is any ticket whose blockers are complete. Local Markdown uses the [local-ticket helper](../setup-matt-pocock-skills/LOCAL-TICKETS.md) to validate and publish the approved graph in dependency order; retain the local template below and canonical textual `Blocked by` fields. Real trackers use native blocking links where supported and retain the semantic field for portability. Leave parent issues unchanged. Record the artifact identifiers and validation evidence.

**Tool card and loop.** Repository read/search takes a path or query and returns text (read-only). Tracker publish takes ticket fields, labels, and blocker edges and returns identifiers/status; local fallback takes a destination and content and returns a written file. For local Markdown, use the helper preview/apply and operation identity instead of reconstructing file edits. Capture each read before slicing; after publication verify the returned records, title, outcome, criteria, status, and edges. Retry a transient read once; on a missing or malformed result, stop publication and record recovery or fallback.

## Ticket shape

```markdown
# <NN>: <Ticket title>

**What to build:** <one end-to-end, user-visible outcome>

**Blocked by:** <numbers/titles, or None (can start immediately)>

**Status:** ready-for-agent

- [ ] <observable acceptance criterion>
- [ ] <observable acceptance criterion>

**Definition of Done:** <link or reference to the repository policy; do not copy it here>
```

For a real issue tracker, use the same outcome and acceptance fields, plus a parent reference when applicable. Avoid file paths, code snippets, and layer-by-layer task lists; a prototype may contribute only a compact decision-rich state/schema/type shape, labelled as such.
