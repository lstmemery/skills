# Managed Herdr job tool card

Read [managed-jobs.md](managed-jobs.md) for the full contract. Preview each
request before any live call; a preview validates routing and prompts offline.

## Complete explicit tuple

An override pins the entire route. Include all five fields when specifying a
tuple; the instruction must reflect actual authorization.

```json
{
  "override": {
    "runtime": "codex",
    "model": "gpt-6-luna",
    "provider": "codex",
    "effort": "medium",
    "instruction": "Use the user-authorized Codex route to implement this task."
  }
}
```

Put that override on a normal job in a manifest, then validate it with:

```sh
python3 scripts/herdr-jobs.py run --manifest request.json \
  --policy launch-policy.json --run-dir RUN_DIR --preview
```

## Escalate from recorded evidence

A level above 1 needs structured evidence or a complete explicit tuple. For
level 2, the following records a concrete reasoning failure from the prior
level. Higher jumps also require `"authorization": "user"`.

```json
{
  "model_level": 2,
  "escalation": {
    "previous_level": 1,
    "failure": {
      "kind": "failure",
      "category": "reasoning",
      "source": "review.md",
      "observed_at": "2026-10-19T10:00:00Z",
      "summary": "The level 1 analysis contradicted the supplied API contract."
    },
    "expected_improvement": "Resolve the contract contradiction with a source-grounded implementation."
  }
}
```

Add these fields to a normal job and run the same `run --preview` command above.
The evidence must describe an observed failure, not anticipated difficulty;
operational and quota failures do not authorize a model-level escalation.

## Add and validate

- `run` starts an initial batch. `add` appends exactly one new job to an existing
  run; use the same `request_id`, pinned policy, and run concurrency, with a
  unique `job_id`. Keep the same run directory.
- Worker result commands take positional paths: `worker-records.py
  validate-result PATH --task-id ID --revision REVISION` validates a result;
  `worker-records.py check-closeout RUN_DIR` checks run closeout. `validate-result`
  also accepts `--repository-changes` when validating a repository candidate.
- `status` is observation-only. `status --summary` prints `job`, `pane`, `state`,
  `disposition`, and `lease`; `--wait-seconds 0` reports local state only.
