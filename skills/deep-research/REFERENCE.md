# Deep research reference

## Worker contract

Load when assigned a research slice. The coordinator supplies the objective,
non-goals, shared definitions/date window/comparison axes, artifact path, and
acceptance condition. Include access to the shared
[evidence contract](../research/EVIDENCE.md); read it before gathering.

Execute the slice entirely in the current thread. Find the owner sources,
open them, and retain exact support in the shared row format. Supply support
or an explicit could-not-verify item for each requested part. Note cross-slice
contradictions rather than settling another slice's claims alone. Return the
artifact path, coverage, and gaps. The coordinator owns synthesis and release;
a worker does not queue its artifact or spawn further workers. Research-only
work does not run unrelated formatters, linters, or project-wide tests.

Set source coverage from the question's acceptance criteria rather than an
arbitrary source quota. If the task explicitly requires a number or type of
independent sources, meet it or record the unmet requirement.

## Execution shapes

- **Wide:** enumerate the candidate/item set first. Use consistent fields per
  item or chunk, process chunks sequentially in this thread, then aggregate.
  Name missing items and deduplicate coverage.
- **Deep:** retain a small graph of dependent questions. Investigate the
  currently answerable questions, inspect their evidence, then plan the next
  round around the remaining causal or documentary gaps.
- **Mixed:** use the wide pass to identify candidates and selection criteria;
  use the deep pass on the selected shortlist. Preserve why candidates were
  excluded and which claims remain unresolved.

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
