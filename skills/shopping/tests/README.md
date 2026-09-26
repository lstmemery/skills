# Shopping trigger checks

`trigger-cases.json` contains eight purchase/offer prompts and five general
recommendation or information prompts. The runner counts a case as activated
only when the harness emits an explicit skill-load event. The assistant's
answer wording is not evidence of activation.

Run the offline fixture check with:

```sh
python3 skills/shopping/tests/run_triggers.py --validate-only
```

Run a harness from the repository root with results under the ignored test-artifact folder:

```sh
python3 skills/shopping/tests/run_triggers.py --harness claude --output-dir skills/shopping/tests/.artifacts/trigger-results
python3 skills/shopping/tests/run_triggers.py --harness omp --output-dir skills/shopping/tests/.artifacts/trigger-results
python3 skills/shopping/tests/run_triggers.py --harness pi --output-dir skills/shopping/tests/.artifacts/trigger-results
```

Each run starts a fresh, non-interactive process for each prompt. It makes a
temporary copy of this checkout's `shopping` skill, then configures only that
copy for the selected harness. It does not edit installed skills or harness
configuration. Every harness receives the same routing-only system prompt: use
listed skill metadata to decide whether a skill directly applies, load it if so,
then stop without doing the underlying task. This prevents unrelated global
assistant instructions from changing the routing result. Claude receives a
temporary plugin and runs in bare mode with temporary home/config directories
and project/local settings, so its user profile, installed skills, and stored
credentials stay out of the run. It can authenticate only through
environment-provided credentials; if none are available, the run records that
error. Pi receives an explicit skill path
with other discovered skills disabled; omp receives a temporary custom skill
directory overlay. Claude session persistence is off and each prompt has a
`$0.25` budget ceiling. Tool access is limited to Claude's `Skill` tool or the
harness's read tool. The fixture does not request a purchase.

The result records these activation signals:

- **Claude Code:** a `Skill` tool call naming the temporary
  `shopping-trigger-test:shopping` plugin skill in `stream-json` output.
- **omp and Pi:** a successful `read` tool event for `skill://shopping` or the
  temporary copy's `SKILL.md` path in JSON event output.

A positive prompt passes only with a matching event; a negative prompt passes
only without one. If a harness exits unsuccessfully or emits no structured
event stream, the runner records an error or an unobservable result instead of
guessing from the answer. Result records identify the controlled prompt as
`isolated-skill-router-v1` and include `runner_version` for comparison.
Repeat the same fixture after a description change to track missed positive
routes and negative over-triggering. Keep the harness version and
`skill_source_sha256` with each result so runs can be compared.
