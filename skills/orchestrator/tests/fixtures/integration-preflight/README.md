# Synthetic integration preflight records

These records use the same run layout as the batch records: a batch-level
`workers.txt`, a task `result.json`, `review/` with Markdown and `done.json`,
and a completed capture manifest. Identities, task content, and capture metadata
are synthetic. `@BASE@` and `@HEAD@` are replaced with commits from a temporary
test repository.

The `review-evidence.json` sidecar and indexed lines in `review-response.md`
make reviewer identity, axis completion, and finding disposition explicit for
the preflight validator.
