# Deep research reference

## Worker contract

Load when assigned a research slice. The coordinator supplies the objective,
non-goals, shared definitions/date window/comparison axes, artifact path, and
acceptance condition. Include access to the shared
[evidence contract](../research/EVIDENCE.md); read it before gathering.

Execute the slice in your own thread. You may make concurrent tool calls within
it. You do not spawn subagents — nested fanout multiplies raw token use while
hiding it from the coordinator's visible counter, and one level of delegation
already captures the parallelism win. Depth is exactly one: workers never
dispatch.

Find the owner sources, open them, and retain exact support in the shared row
format. Supply support or an explicit could-not-verify item for each requested
part. Note cross-slice contradictions rather than settling another slice's
claims alone. Return the artifact path, coverage, and gaps. The coordinator owns
synthesis and release; a worker does not queue its artifact. Research-only work
does not run unrelated formatters, linters, or project-wide tests.

Set source coverage from the question's acceptance criteria rather than an
arbitrary source quota. If the task explicitly requires a number or type of
independent sources, meet it or record the unmet requirement.

## Execution shapes

- **Wide:** enumerate the candidate/item set first. Use consistent fields per
  item or chunk. Aggregate after all chunks land; whether they ran sequentially
  or as dispatched workers is a budget decision the coordinator records.
  Name missing items and deduplicate coverage.
- **Deep:** retain a small graph of dependent questions. Investigate the
  currently answerable questions, inspect their evidence, then plan the next
  round around the remaining causal or documentary gaps.
- **Mixed:** use the wide pass to identify candidates and selection criteria;
  use the deep pass on the selected shortlist. Preserve why candidates were
  excluded and which claims remain unresolved.

## Fanout budget

Multi-agent work can roughly increase token cost an order of magnitude over
single-thread work; it pays only when slices are genuinely independent and the
question is high-stakes enough that coverage matters more than spend. Record the
intended worker count during scoping as part of the effort budget, and stop at
it. If the enumerable item set exceeds the budget, take the highest-yield slices
and name the unworked ones as coverage gaps rather than expanding the fanout.

## Evidence and maintenance

The operational evidence-row and claim-check rules have one owner:
[research/EVIDENCE.md](../research/EVIDENCE.md). Load that contract for evidence
work, not the historical rationale. Keep both skill folders together when
packaging this dependency; a missing reference is a packaging failure.

When evaluating or changing this skill, read [MAINTENANCE.md](MAINTENANCE.md)
for inherited research rationale and the historical worker-cost observation.
Check changed behavior on a single-owner lookup, broad comparison, dependent
source chain, contradiction, unavailable source, and delegated slice. Verify
actual source support and outputs, not merely headings or word counts.
