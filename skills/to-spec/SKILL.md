---
name: to-spec
description: "Synthesize the current conversation into a reviewable product spec and publish it to the configured issue tracker."
disable-model-invocation: true
---

# To Spec

**Job.** Turn the current conversation and repository evidence into a product requirements document (PRD) that another agent can implement and verify. The human has already supplied the brief; synthesize it rather than reopening settled questions.

**Inputs and authority.** Use, in order: explicit user decisions; repository `CONTEXT.md`/`CONTEXT-MAP.md`, relevant ADRs, and existing behavior; tracker conventions; clearly marked inferences. Never turn an inference into a requirement silently. If the tracker vocabulary is unavailable, stop and direct the user to `/setup-matt-pocock-skills`.

**Publish only when all are true:**

- the problem, desired outcome, actors, constraints, and out-of-scope boundary are stated;
- behavior covers each distinct actor/outcome and important alternate or failure path; stop when a new story duplicates an outcome or adds no acceptance-relevant behavior;
- every requirement is clear, singular, consistent, feasible, individually verifiable, and traceable to a goal/problem and a verification method;
- applicable quality attributes and human/operational constraints are recorded (or marked `Not applicable` with a reason);
- scenarios describe observable behavior (`Given/When/Then` where it clarifies a rule) and avoid implementation choreography;
- significant decisions record context, decision, consequences/trade-offs, alternatives, status, and owner; unresolved material decisions are exposed;
- testing decisions name external behavior, seams, modules, and repository precedent.

## Process

1. **Explore.** Read the relevant domain glossary, ADRs, existing behavior, and tracker instructions. Reuse project terms and existing test seams; propose the highest useful seam and keep the set small.
2. **Model.** Draft the problem, outcome, actor/outcome scenarios, constraints and quality attributes, decisions, verification approach, and explicit assumptions/unresolved decisions. Separate behavior from implementation choices.
3. **Resolve the gate.** Do not conduct an open-ended interview. If a material decision or test seam is absent and cannot be safely represented as an assumption, ask one focused question (or route a human-only action through `wizard`) and wait. Otherwise proceed with assumptions visibly marked.
4. **Write and validate.** Fill the template below. Check every requirement against the publish predicates; report missing evidence as `Evidence missing: …`, never as a negative fact. Do not publish a PRD that asserts an unmade material choice.
5. **Publish.** Use the configured tracker. For local Markdown, create the feature directory and `PRD.md`, then apply `ready-for-agent` as specified by the tracker conventions. Record the written artifact and validation evidence.

**Tool card and loop.** Repository read/search takes a path or query and returns text (read-only). Tracker publish takes the PRD fields and `ready-for-agent` label and returns an artifact identifier/status; local fallback takes a destination and content and returns a written file. Capture every read before deciding, verify every write, retry a transient read once, and on a missing or malformed result record the failure and stop publication or use the configured fallback.

## Output

```markdown
## Problem Statement
<user problem, affected actors, and goal>

## Solution
<observable behavior and boundaries>

## User Stories
1. As an <actor>, I want <capability>, so that <benefit>.
<one item per distinct outcome; include alternate/failure outcomes when relevant>

## Examples / Acceptance Scenarios
- **Scenario:** <name>
  - Given <precondition>
  - When <action>
  - Then <observable result>

## Constraints and Quality Attributes
- <performance, security/privacy, reliability/availability, accessibility/usability,
  observability, migration/rollback, and human/operational constraints as applicable>

## Implementation Decisions
- **Context:** …
- **Decision:** …
- **Consequences/trade-offs:** …
- **Alternatives considered:** …
- **Status / owner:** …

## Testing Decisions
- <external behavior, seam, module, and repository precedent>

## Traceability
- <requirement or scenario> → <goal/problem> → <verification method>

## Out of Scope
<explicit exclusions>

## Assumptions and Unresolved Decisions
- <assumption or `Evidence missing: …`; owner and next decision point when material>

## Further Notes
<only information needed by implementers or reviewers>
```

Do not include specific file paths or code snippets in the PRD. A prototype may contribute a compact state machine, reducer, schema, or type shape when that is the decision itself; label it as prototype-derived and omit demo code.
