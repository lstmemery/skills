# Cost-conscious model routing

Read before selecting an ordinary worker's level, fallback, or escalation.
[model-ladder.csv](../model-ladder.csv) is the executable tuple authority, copied
verbatim from the user's `~/Documents/model-ladder.csv` on 2026-10-10. The
[launch policy](../launch-policy.json) references that packaged file and defaults
to level 1. Future CSV changes are reviewable repository changes; do not mutate
the source CSV, maintain a second tuple table in prose, or edit runtime defaults.

## Choose the cheapest sufficient level

Start routine, bounded work at level 1: deterministic tool operations need no
agent at all. Levels 2–4 add reasoning effort for investigation, unfamiliar
code, and tasks whose previous attempt exposed a concrete difficulty. Level 5
is a different model/provider step; check its exact effort support before use.
Reserve levels 6–7 for demonstrated cross-module difficulty, complex diagnosis,
or a failed lower-level approach. Level 8 is the ceiling for the hardest
reasoning when evidence justifies it. This is selection guidance, not a model
benchmark or an automatic classifier.

An explicit user model, effort, provider, or harness remains pinned. Record it
in `override` with the user's instruction. Those fields take precedence over
`model_level` and bypass ladder model/effort defaults. Missing runtime uses the
selected level's harness (or the default level's harness); missing model/effort
uses that harness's native default. Supply the complete explicit tuple when
the user pins one. A named `omp` stays `omp`; `pi` is never its alias.

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
launch authorization or capability validation. A direct launcher must apply
the same native catalog/config/effort checks as managed preflight and reserve
shared provider admission before creating a pane. Preserve the existing host
binding, startup verification, isolated worktree, and jail requirements.

Escalate at most one level per demonstrated failure, with at most two upward
steps per assignment before rescoping with the coordinator. Record the previous
level, failure/check evidence, next level, and expected improvement. Reuse the
existing investigation, artifacts, and worktree. Reconcile the old worker's
terminal state before retrying. Quality/difficulty calls for escalation, not
quota fallback. Do not launch expensive parallel probes merely to guess which
model might work; split only independently useful work that fits the budget.
Explicit user requests can select any level immediately and remain fixed.

The user's approximate 2× cost increase per level is an ordering heuristic.
Do not derive dollar prices, token budgets, or quota sizes from it. There are
three separate subscription pools; preserve their observed remaining capacity.

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
share the Claude pool; `opencode go` and `opencode-go` share the OpenCode Go
pool. OpenCode Go routes always use Pi. Admission counts and resets are shared
across provider aliases.
Older alias-keyed leases migrate on read; colliding alias backoffs retain the
later reset. Older policies with different caps for aliases use the stricter
cap. Pool exhaustion blocks every model in that pool; model-specific rate limits
continue to block only that model. Never treat switching harnesses as new quota.

## Native contracts and capability gaps

`claude` is the CSV harness alias for runtime `claude-code`, Herdr kind `claude`.
`opencode go` maps to native provider `opencode-go`, and every OpenCode Go tuple
must use Pi. Pi passes `--provider opencode-go`; it passes models unchanged with
`--model` and efforts with `--thinking`. Codex/OpenCode Go combinations are
rejected while loading the ladder and preparing an explicit request. Preflight
rejects them before host discovery. The transport also rejects them before any
Herdr action, and native argument construction fails closed on such a mapping.
Explicit Codex routes through other custom providers remain blocked until a
provider-specific exact-model discovery adapter is verified.

Pi's read-only `pi --offline --list-models` output includes
`opencode-go/gpt-6-luna`. The installed Pi model metadata reports `low`,
`medium`, `xhigh`, and `max` support for that exact model, so levels 1–4 retain
their configured `gpt-6-luna` fallbacks at the requested efforts. Managed-job
preflight still checks the exact model and effort against the installed Pi
catalog before launch; it does not replace a missing model or effort with a
different choice.

Pi discovery uses its native offline model table, disambiguated by provider.
The ladder additionally checks installed SDK model effort metadata composed
with its native, credential-blind `models.json` loader; a model
that clamps the requested effort fails before effects. On the inspected Pi
1.1.0 build, `opencode-go/mimo-v2.6-pro` supports through `high`, so level 5's
`max` primary is a capability gap. Recover using its configured Codex fallback
with this unavailable evidence, or separately restore/verify exact Pi effort
support. Changed SDK contracts, provider extensions, and runtimes lacking the
native configured-metadata adapter remain gaps rather than assumed upgrades.

Codex discovery accepts both the older flat model catalog and current
`runtimes[].models` catalog. Claude's installed discovery may be partial and
contain only `opus`/`sonnet` aliases. That does not verify the CSV's exact
Claude versions: hold the affected choice until an exact available catalog
adapter is verified. Level 8 can recover to its configured Codex fallback with
that evidence. Levels 6–7 keep their usable Codex primaries; an unavailable
Claude fallback is a gap, not permission to invent another route.

## Preserved routes and migration

The ladder applies only to ordinary host agents. Deep research keeps jailed
Codex `gpt-6-astra` at max effort and its independent source/claim check and
privacy guard. The jail default must still be confirmed at admission; exact
jail model discovery remains outside the managed adapter. Shopping keeps
jailed Claude Opus 5.5 at xhigh effort via the host pane-command route; the
Codex-only managed helper still refuses it. A named non-jail harness requires
an explicit ordinary assignment as documented in preferences. No host access
from inside a jail is introduced. `omp-train` defaults and `omp` identity are
unchanged.

Policies without `model_ladder` retain their schema-v1 runtime selection
contract. Existing explicit manifests and saved runs remain pinned; resume
never reselects a level. New runs and additions pin the full CSV hash so later
CSV drift conflicts rather than changing an existing fleet. Shared settings
and credentials require separate reviewable proposals; prefer native arguments
scoped to the selected run. User code inspection and approval boundaries apply
before PR creation, integration, or merge as before.
