# Cost-conscious model routing

Read before selecting an ordinary worker's level, fallback, or escalation.
[model-ladder.csv](../model-ladder.csv) is the executable tuple authority, copied
verbatim from the user's `~/Documents/model-ladder.csv` on 2026-10-25. The
[launch policy](../launch-policy.json) references that packaged file and defaults
to level 1. Future CSV changes are reviewable repository changes; do not mutate
the source CSV, maintain a second tuple table in prose, or edit runtime defaults.
The source and packaged copies drifted before 2026-10-25. The
`test_packaged_ladder_matches_documents_copy_when_present` regression test
compares them when `/home/deadhand/Documents/model-ladder.csv` is available,
and reports a unified diff on drift; it skips when that optional local copy is
absent (such as in CI).
OpenCode Go was retired on 2026-10-25 and is no longer a primary or fallback
route in the packaged ladder.

## Choose the cheapest sufficient level

Start routine, bounded work at level 1: deterministic tool operations need no
agent at all. Levels 2–4 add reasoning effort for investigation, unfamiliar
code, and tasks whose previous attempt exposed a concrete difficulty. Level 5
is a different model step; check its exact effort support before use.
Reserve levels 6–7 for demonstrated cross-module difficulty, complex diagnosis,
or a failed lower-level approach. Level 8 is the ceiling for the hardest
reasoning when evidence justifies it. This is selection guidance, not a model
benchmark or an automatic classifier.

Keep level 1 as the default for bounded work. Trial level 2 for sign-sensitive
derivations and technical-paper audits, and require an independent
recomputation before relying on the result. The trial is a task-class
comparison, not an automatic route change.

An explicit user model, effort, provider, or harness remains pinned by dimension.
Record only the requested dimensions in `override`, together with the user's
instruction. A harness/provider preference is not a model/effort override.
The coordinator decision records the original pins separately from the resolved
tuple. See [route decisions](route-decisions.md) for constraint completion,
per-task evidence, actionable holds and the shared native dispatch entrypoint.
A named `omp` stays `omp`; `pi` is never its alias.

For an unpinned worker, set `model_level` to an integer 1–8; omission selects
level 1 unless an older policy explicitly sets an ordinary runtime. An
instruction-only override preserves that instruction and still uses the ladder.
The helper pins the CSV hash, requested tuple, native aliases, and effective
choice. Preview a direct route without launching or writing state:

```sh
python3 scripts/model-route.py --level 6
python3 scripts/model-route.py --level 4 --fallback-reason rate_limit \
  --evidence 'Primary Codex reported its usage-limit banner; reset recorded in run state'
```

The returned argv contains only model/provider/effort arguments. It is not
launch authorization or capability validation. Ordinary native dispatch must
use `scripts/native-dispatch.py` with the managed manifest and host contract.
It consumes the same coordinator decision, exact discovery and shared provider
lease before creating a pane; do not run preview argv directly. Preserve the existing host
binding, startup verification, isolated worktree, and route-specific isolation;
the explicit local lane uses a host worktree and no jail. Direct local Pi starts
must use `scripts/local-lane.py start`, which applies the same
`task_class`/`local_lane_scope` validator as managed jobs. Do not start the
local model through a raw `herdr agent start --approve` command.
The managed local lane requires an allowlisted `task_class` (`triage`,
`summary`, `contract_artifact`, `bounded_code_edit`, `inventory`, or
`local_file_analysis`) and refuses missing or unsupported classes before
launch. Keep its work trivial, self-contained, validator-checkable, and
limited to supplied local inputs. Exclude networked, credentialed, destructive,
and review-verdict tasks; never include secrets in the prompt or inspect them
in the process environment. Both direct and managed launches replace the pane
shell with an `env -i` allowlist containing `HOME`, `PATH`, `TERM`, `LANG`,
Herdr pane integration values, and Pi agent/session directory values when set;
API keys and tokens are not passed to Pi. Each request declares `local_lane_scope` with
`network_access`, `credential_access`, `credentials_in_environment`, and
`destructive_actions` all false; configured MCP tools and extensions stay
available under the user's settings and must remain within this scope.

Escalate at most one level per demonstrated failure, with at most two upward
steps per assignment before rescoping with the coordinator. Record the previous
level, failure/check evidence, next level, and expected improvement. Reuse the
existing investigation, artifacts, and worktree. Reconcile the old worker's
terminal state before retrying. For implementation work, raise a level only
after a concrete reasoning failure. Inspect the full patch, run
`git diff --check`, and run every acceptance test at every level. A failing
check or a harder task label alone is not a reasoning failure. Do not launch
expensive parallel probes merely to guess which model might work; split only
independently useful work that fits the budget.
Explicit user requests can select any level immediately and remain fixed.

The user's approximate 2× cost increase per level is an ordering heuristic.
Do not derive dollar prices, token budgets, or quota sizes from it. There are
two active subscription pools, Codex and Claude; preserve their observed
remaining capacity.

## Calibration by model and harness

These are local routing hypotheses for the installed fleet, not capability
claims inferred from a model name. Keep the same brief core, protected paths,
required context, result schema, and external checks while measuring each
configuration. Record model/version, harness/version, effort, prompt and tool
registry, context packet, retries, checks, latency, and cost.

| Worker or orchestrator | Starting contract | Calibration and promotion signal |
|---|---|---|
| Claude Code + Opus 5.5 orchestrator | Complete handoffs, hooks/permission rules, high effort for synthesis | Compare high with max on a fixed regression set; treat max-turn output as partial until checked. |
| Codex + gpt-6.1-sol orchestrator | Outcome-first brief, transport-bound IDs, deterministic trace checks | Use high for ordinary orchestration; promote to max only for measured high-risk synthesis or debugging. |
| Codex CLI + gpt-6-luna low | Literal mode, short context packet, exact fields, one or two named checks | Retain low only after exact-ID, schema-recovery, and forbidden-edit cases pass; raise on ambiguity or cross-file effects. |
| Codex CLI + gpt-6-luna medium through max | Same authority boundary with a bounded tool loop | Sweep effort on identical cases and keep the lowest setting that preserves recovery and verification. |
| Codex CLI + gpt-6.1-sol high/max | Multi-file implementation with explicit protected paths and acceptance tests | Use high as baseline; reserve max for difficult diagnosis or targeted review after a cheaper pass exposes real uncertainty. |
| Claude Code + Sonnet 5.5 | Complete subagent prompt, allowlist, max-turn bound, and acceptance checks | Start medium/high for routine implementation or exploration; promote to Opus for unresolved ambiguity or long-horizon synthesis. |
| Claude Code + Opus 5.5 worker | Hard diagnosis or independent review with deterministic oracle | Start high; use max only when the unresolved case has enough decision value. |
| Jailed Codex + gpt-6-astra xhigh | Required-context manifest with exact quotes, source roles, and an external evaluator | Xhigh is suitable for deep evidence chains; use a cheaper extractor before synthesis when that reduces cost without weakening evidence. |
| omp / oh-my-pi | Validate installed task-agent metadata, effort mapping, structured output, and isolation policy | Use cheap roles for literal slices and slower roles for inference only after the configured ceiling and retry behavior are verified. |

Worker mode stays explicit in every brief and never selects a ladder level.
Promote only on a concrete trigger such as ambiguity, cross-file impact,
conflicting evidence, a failed external check, or a schema-recovery failure;
confidence or extra tokens alone are not promotion evidence.

## Fallback and quota evidence

Use only the CSV's fallback at the selected level when the primary is concretely
unavailable, rate limited, or exhausted. The managed job must include its explicit
`model_level` and `model_fallback` with exactly `reason` (`unavailable`,
`rate_limit`, or `exhausted`) and nonempty `evidence`. The coordinator is
responsible for the evidence's truth. Unknown quota, slowness, poor output,
missing supervision, or a generic timeout does not authorize fallback.
Fallback is a deliberate new assignment attempt; the helper does not switch
harnesses or replay an ambiguous launch automatically. It rejects fallback
alongside explicit tuple pins; obtain a new user instruction to change a pin.

Inspect available read-only limits/reset snapshots and record source/time.
Unknown usage remains unknown. A capacity admission wait is not exhaustion.
Honor Retry-After and reset times; use the existing bounded 60-second default
when a concrete rate-limit signal lacks metadata. A fallback uses its own pool's
admission and backoff, and may wait too. For observed pool-wide exhaustion,
record a wildcard backoff (quote `*` to prevent shell expansion):

```sh
python3 scripts/herdr-admission.py rate-limit --provider codex --model '*' \
  --reset-at '<observed reset timestamp>'
```

`codex` and `openai-codex` share the Codex pool; `claude` and `claude-code`
share the Claude pool. The retired `opencode go` and `opencode-go` aliases
continue to share a pool for explicit and saved routes; those routes require
Pi. Admission counts and resets are shared across provider aliases.
Older alias-keyed leases migrate on read; colliding alias backoffs retain the
later reset. Older policies with different caps for aliases use the stricter
cap. Pool exhaustion blocks every model in that pool; model-specific rate limits
continue to block only that model. Never treat switching harnesses as new
quota. Quota stalls never raise the ladder level: wait for reset/backoff or use
only the evidenced same-level fallback.

## Evidence and task telemetry

Investigatory claims cite their source and label unknowns. Before saying
something is absent, check both the host and jail namespaces; if either cannot
be checked, report the absence as unknown. Apply this gate to worker briefs as
well as coordinator summaries.

The managed coordinator writes one `telemetry.json` per task attempt. Its
versioned route record has `lane`, `level`, `choice`, `model`, `effort`,
`harness`, and `provider`; the task record also has `started_at`, `ended_at`,
`continuation_count`, `continuation_reasons`, `quota_signals`, a
`coordinator_grade` slot that remains `pending`, and `independent_validation`.
The actual coordinator grade lives in coordinator-owned `coordinator-grades.json`
in the run directory. A non-pending grade in worker-writable telemetry is
treated as tampering and ignored. Workers must not edit telemetry or claim the
coordinator's grade. A quota retry records its signal/reset and continuation
reason, and local-lane completion records the independent validator result.

## Native contracts and capability gaps

`claude` is the CSV harness alias for runtime `claude-code`, Herdr kind `claude`.
Its managed start requires the verified host binding, exact model/effort
discovery, and the Claude admission pool. Claude effort is capped at `xhigh`;
Haiku starts additionally require the pane-scoped compaction environment
described in [managed jobs](managed-jobs.md). Offline support does not attest a
live host contract or authorize a manual fallback launch.
`opencode go` maps to native provider `opencode-go`, and every OpenCode Go tuple
must use Pi. Pi passes `--provider opencode-go`; it passes models unchanged with
`--model` and efforts with `--thinking`. Codex/OpenCode Go combinations are
rejected while loading the ladder and preparing an explicit request. Preflight
rejects them before host discovery. The transport also rejects them before any
Herdr action, and native argument construction fails closed on such a mapping.
Explicit Codex routes through other custom providers remain blocked until a
provider-specific exact-model discovery adapter is verified.

OpenCode Go is retired from the packaged ladder. Generic Pi discovery and
provider/harness validation remain available for explicit routes and saved
runs; their regression tests use fixture CSVs rather than active ladder rows.
Pi discovery uses its native offline model table, disambiguated by provider.
The helper additionally checks installed SDK model effort metadata composed
with its native, credential-blind `models.json` loader; a model that clamps
the requested effort fails before effects. Changed SDK contracts, provider
extensions, and runtimes lacking the native configured-metadata adapter remain
capability gaps rather than assumed upgrades.

Codex discovery accepts both the older flat model catalog and current
`runtimes[].models` catalog. Claude's installed discovery may be partial and
contain only `opus`/`sonnet` aliases. That does not verify the CSV's exact
Claude versions: hold the affected choice until an exact available catalog
adapter is verified. Level 8 can recover to its configured Codex fallback with
that evidence. Levels 1–7 keep their Codex primaries; an unavailable Claude
fallback is a gap, not permission to invent another route.

## Preserved routes and migration

The ladder applies only to ordinary host agents. Deep research keeps jailed
Codex `gpt-6-astra` at xhigh effort and its independent source/claim check and
privacy guard. The jail default must still be confirmed at admission; exact
jail model discovery remains outside the managed adapter. Shopping keeps
jailed Claude Opus 5.5 at xhigh effort via the host pane-command route; the
Codex-only managed helper still refuses it. A named non-jail harness requires
an explicit ordinary assignment as documented in preferences. No host access
from inside a jail is introduced. `jail-agent` keeps the existing defaults and
`omp` harness identity.

Policies without `model_ladder` retain their schema-v1 runtime selection
contract. Existing explicit manifests and saved runs remain pinned; resume
never reselects a level. New runs and additions pin the full CSV hash so later
CSV drift conflicts rather than changing an existing fleet. Shared settings
and credentials require separate reviewable proposals; prefer native arguments
scoped to the selected run. User code inspection and approval boundaries apply
before PR creation, integration, or merge as before.
