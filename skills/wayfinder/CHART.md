# Chart a new map

Apply [SKILL.md](SKILL.md)'s map rules and session limit.

1. **Set the destination.** Load and follow `grilling` and `domain-modeling` to settle what spec, decision, or change the effort leads to. This sets scope.
2. **Survey breadth-first.** Surface open decisions across the space and the first steps takeable now. If no fog remains and the whole journey fits one session, report that a map is unnecessary and ask how the user wants to proceed.
3. **Create the map** using the body below, labelled `wayfinder:map`. Fill Destination and Notes, leave Decisions so far empty, and sketch only in-scope fog in Not yet specified.
4. **Create precise tickets** as children, then wire blocking edges in a second pass once their IDs exist. Questions that are not yet precise remain fog.
5. **Resolve or dispatch research** according to the core's effort rule, claiming each ticket first. Record completed findings through the resolution procedure in [WORK.md](WORK.md#record-and-update); leave active research with its claim and artifact pointer.

Stop after charting and its research work; do not hand-resolve a human ticket in this session. Report the map, initial frontier, and research still active.

## Map body

```markdown
## Destination

<One or two lines describing where the effort leads.>

## Notes

<Domain, relevant skills, standing preferences, and any explicit execution override.>

## Decisions so far

<!-- Linked ticket title and one-line gist per resolution; detail stays in its ticket. -->

## Not yet specified

<In-scope questions not yet precise enough to ticket.>

## Out of scope

<Work beyond the destination, with reasons and any closed ticket links.>
```

Open tickets are found through the tracker's child/frontier query, not duplicated in the map body except where the tracker's fallback child relationship requires a list.
