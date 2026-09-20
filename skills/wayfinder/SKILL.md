---
name: wayfinder
description: Map a large effort as shared decision tickets and resolve its route one session at a time.
disable-model-invocation: true
---

# Wayfinder

Chart or resume a shared map of decisions too large for one session. Planning is the default: resolve decisions, rather than deliver the destination, unless the map's **Notes** explicitly includes execution. The effort is done when the way to its destination is clear, with no open tickets or unresolved in-scope fog.

**Session limit:** resolve at most one non-research ticket; research tickets are the exception and may also run independently. Charting resolves no human ticket. Human-in-the-loop tickets require a live exchange; an agent never supplies the human's side.

## Route

Use the requested intent and available map context. A supplied map URL/number/path or an unambiguous map already established in the conversation selects **resume**. A loose new effort selects **chart**. An omitted ID does not prove the user wants a new map: inspect available context and clarify only if the intended map or mode remains ambiguous.

- For a new map, read [CHART.md](CHART.md).
- For an existing map, read [WORK.md](WORK.md).

Use the configured issue tracker's **Wayfinding operations** for storage, child relationships, blocking, claims, ordering, and resolution. If no tracker is configured, tell the user `/setup-matt-pocock-skills` can configure it and use the [local Markdown convention](../setup-matt-pocock-skills/issue-tracker-local.md) for this effort, within the active runtime's write boundaries.

## Map rules — apply in both modes

- **Destination:** the spec, decision, or change this effort leads to. It fixes scope and orients every session.
- **Map:** the canonical issue labelled `wayfinder:map`, with child decision tickets. It is an index: each decision's detail lives in its ticket; **Decisions so far** holds a linked title and one-line gist. Refer to maps and tickets by their linked titles in human-facing output, never bare IDs.
- **Ticket:** a precise question or prerequisite sized for one session, with a `## Question` body and `wayfinder:<type>` label. Assets are linked, not pasted. Create tickets before wiring dependency edges; native blocking is canonical where supported, with the configured body convention as fallback.
- **Claim:** assign the ticket to the dev driving the map before work; use the configured equivalent for local files. An open unassigned ticket is unclaimed. Concurrent sessions may edit the tracker: recheck claims/blockers before taking a ticket and preserve unrelated edits.
- **Frontier:** open, unblocked, unclaimed children in tracker order. A ticket is unblocked only when every blocker is closed.
- **Fog:** in-scope work whose question cannot yet be stated precisely belongs in **Not yet specified**. A precise question is a ticket even when blocked or unanswered. Promote newly precise fog into tickets after a resolution and remove the promoted patch, so it has one home. Fog excludes existing tickets, settled decisions, and out-of-scope work; do not pre-slice it into imagined tickets.
- **Out of scope:** work beyond the destination belongs in its own section. Close a mis-scoped ticket and link its title there with the reason; it is not a resolution in **Decisions so far**. It returns only in a fresh effort with a redrawn destination.

## Ticket types and research effort

| Type | Mode | Resolve with |
|---|---|---|
| `research` | Agent | Documentation, APIs, or other sources supplying a fact a decision needs; load and follow `research`. |
| `prototype` | Human | A cheap artifact that makes a look/behavior question concrete; load and follow `prototype`, link the asset, and get the human's reaction. |
| `grilling` | Human | The default decision conversation; load and follow `grilling` and `domain-modeling`. |
| `task` | Agent or human | Prerequisite work needed to decide, such as provisioning access. Do it when authorized and possible; otherwise give the human precise steps. Record completion and resulting facts or credential locations, never secret values. |

Handle bounded research lookups inline. Delegate substantial independent research when a separate agent adds value; load `research` in that worker too. Give it the claimed ticket, question, sources/constraints, and output location; inspect findings before resolution. Preserve evidence in a durable artifact linked from the ticket. Use the calling workflow's isolation policy for any repository changes; a read-only lookup needs no throwaway branch merely to exist.

## Local Markdown mechanics

When using local Markdown, follow the shared [local-ticket command](../setup-matt-pocock-skills/LOCAL-TICKETS.md) for frontier, claim, release/reassignment, and accepted resolution.
Use the canonical shared feature root from every code worktree. A cancelled
blocker does not satisfy a dependency. Preserve human decision requirements and
the session limit above; the helper owns the file updates and recovery.
