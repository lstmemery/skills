# Synthetic integration preflight records

These records use the same run layout as the batch records: a batch-level
`workers.txt`, a task `result.json`, `review/` with Markdown and `done.json`,
and a capture placeholder. Identities and task content are synthetic. The test
replaces `@BASE@` and `@HEAD@` with commits from a temporary repository, then
generates a complete capture payload before invoking preflight.

The `review.md` finding IDs, `review-evidence.json` sidecar, and indexed lines
in `review-response.md` make axis membership, reviewer identity, and finding
disposition explicit for the preflight validator.
