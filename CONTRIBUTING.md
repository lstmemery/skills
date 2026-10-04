# Contributing

## Skill coverage policy

Every skill in `skills/` carries one of two recorded coverage outcomes, and an
automated check enforces this. The policy exists so a reader can tell, per
skill, what is regression-tested, what is deliberately not, and where the
routing risk sits. (Owner decision D-42, card #1153.)

### Coverage classes

1. **Executable code requires a suite.** A skill with executable code must
   have a runnable suite. Executable code means any `.py` file, any shell
   script (`.sh`/`.bash`, including `*.template.sh` files), or any file with
   the execute bit, inside the skill directory — excluding files under a
   `tests/` directory (a skill's own tests are not its runtime code). Suites
   are `unittest` tests living either in `skills/<name>/tests/` or in the
   repo-level `tests/` directory. A script-bearing skill must not be
   rationalised away with a no-suite rationale: templates that are copied
   into consuming repos before they run (e.g. `*.template.sh`) still ship as
   checked-in code here, so the skill records a suite that exercises the
   template in place — `bash -n` structural checks plus offline behaviour
   tests driven with piped stdin and synthetic input (`diagnosing-bugs` and
   `wizard` in `tests/` are the worked examples). The consuming repo still
   owns the template's runtime behavior; the in-repo suite pins the contract
   the copied copy starts from.
2. **Routing-critical skills require trigger fixtures.** A skill is
   routing-critical when its `SKILL.md` description encodes an explicit
   activation rule the harness selector is expected to follow: a boundary
   with a sibling skill ("route broad comparisons to deep-research"), a
   refuse condition ("routine vocabulary lookup stays in project docs"), or
   an explicit-request gate ("only when explicitly requested"). Each
   routing-critical skill must have positive and negative trigger fixtures at
   `skills/<name>/tests/trigger-cases.json`. The per-skill classification is
   recorded in the registry (`routing_critical`); change it in the same
   commit that changes the description.
3. **Every skill records a coverage outcome.** Every skill either has a suite
   or a no-suite rationale in the registry. A skill with executable code may
   not carry a no-suite rationale.

### Trigger fixture schema

`trigger-cases.json` follows the schema introduced by `skills/shopping`:

```json
{
  "version": 1,
  "cases": [
    {"id": "kebab-case-unique-id", "activation": "yes", "prompt": "a request that should activate the skill"},
    {"id": "another-unique-id", "activation": "no", "prompt": "a request that should not"}
  ]
}
```

At least one positive (`yes`) and one negative (`no`) case; ids are strict
kebab-case (`[a-z0-9]+` groups joined by single hyphens — no leading,
trailing, or doubled hyphens) and unique; prompts are non-empty and unique.
Schema validation lives in `tests/trigger_schema.py`, the single source of
truth shared by the coverage check and
`skills/shopping/tests/run_triggers.py`. To go further and measure real
routing, run a harness activation check: start from `--validate-only`, then
reuse the runner pattern in `skills/shopping/tests/run_triggers.py` (isolated
skill copy, routing-only system prompt, explicit skill-load events as the
only activation evidence).

### The registry

`tests/skill-coverage.json` is the single table of record, one entry per
skill directory:

- `name` — directory under `skills/`.
- `coverage` — `"suite"` or `"no-suite"`.
- `suites` — paths to test files or test directories (required for
  `"suite"`; each must exist and resolve to `test_*.py` files).
- `routing_critical` — boolean; when true, `triggers` points at the skill's
  fixture file.
- `rationale` — required non-empty text for `"no-suite"`: why there is no
  suite, and what would justify adding one.

Adding a skill means adding its registry entry in the same change.

### The check

```sh
python3 tests/check_skill_coverage.py
```

The check fails when: a skill directory is missing from the registry (or the
registry names a nonexistent skill); a `no-suite` entry has an empty
rationale; a skill with executable code (including shell-script templates)
is marked `no-suite`; a claimed suite path does not exist or resolves to no
tests; a routing-critical skill has a missing or structurally invalid fixture
file (malformed ids, wrong version, missing positive/negative cases), or
lacks both positive and negative cases. `tests/test_skill_coverage.py` runs
the same check inside `python3 -m unittest`, so a normal test run enforces
the policy.

Full local check set (the test directories are not packages, so run
unittest from inside each):

```sh
cd tests && python3 -m unittest discover
cd skills/orchestrator/tests && python3 -m unittest discover
cd skills/shopping/tests && python3 -m unittest discover
python3 skills/shopping/tests/run_triggers.py --validate-only
python3 tests/check_skill_coverage.py
```

### Local skills

The local-only skills in the agents repo (`skills-local/`) follow this same
policy in their own repo; nothing local is tracked by this registry.
