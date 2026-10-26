# Legacy managed-job assembly fixtures

Synthetic, credential-free records model the two coordinator assemblies:

- `integration-d2d3/ASSEMBLY.md`: a report-only author job, an independent clean
  review with optional `axis_reviewers`, and counts derived from its sidecar.
- `integration-449/ASSEMBLY.md`: initial and delta reviews from separate output
  directories, legacy initial-round done keys in the delta output, version-2
  findings and coordinator-owned dispositions.

`author-state.json` contains only the managed-state fields used by this staging
boundary. `round-findings.json` leaves decisions pending. Integration tests
create a real Git history and capture payloads at runtime, so review-chain and
payload checks run through the production capture verifier and preflight gate.
No historical source report or user disposition is copied into these fixtures.
