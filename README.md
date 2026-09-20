# Skills

Agent skills for real engineering: grilling sessions that align on intent
before code, specs and tracer-bullet tickets, TDD, dual-axis code review,
domain modelling, and source-grounded research. Built for AI coding agents
(Claude Code, Codex, omp, and anything that reads `SKILL.md` files).

Most skills here are adapted from [mattpocock/skills](https://github.com/mattpocock/skills)
(MIT) and reworked for my agent runtimes and workflows — the provenance table
below says exactly what changed. A few are original.

## Install

With [skills.sh](https://skills.sh) or any agent that reads `SKILL.md` files:

```bash
npx skills@latest add lstmemery/skills
```

As a Claude Code plugin:

```bash
claude plugins install lstmemery-skills
```

Skills that assume per-repo configuration (issue tracker, triage labels, docs
locations) want `setup-matt-pocock-skills` run once per repo — it is included.

## Provenance

Every skill is one of: **original** (written here), **adapted** (started from
mattpocock/skills, substantially reworked), or **upstream** (carried from
mattpocock/skills with light harness adaptations).

| Skill | Origin | What changed |
|---|---|---|
| deep-research | original | — |
| diagnosing-bugs | upstream | carried from [mattpocock/skills](https://github.com/mattpocock/skills) |
| herdr | upstream | carried from [herdrdev/herdr](https://github.com/herdrdev/herdr) |
| tutor | original | — |
| wayfinder | adapted | shared decision-ticket map and harness-neutral routing |
| wizard | upstream | carried from [mattpocock/skills](https://github.com/mattpocock/skills) |
| writing-for-humans | original | — |
| research | adapted | rebuilt from a 12-line upstream stub; Consensus MCP route, citation audit, evidence-table recipe |
| domain-modeling | adapted | rewritten and condensed; boundary/ownership framing |
| handoff | adapted | fully rewritten |
| writing-for-agents | adapted | runtime-profile rules expanded |
| teach | adapted | stateful seminar workspace |
| tdd | adapted | ordered recipe, completion criteria per step |
| to-spec | adapted | condensed; authority order for inputs |
| to-tickets | adapted | condensed to coherent-scope tickets |
| code-review | adapted | three review modes (branch / fixed point / working tree) |
| codebase-design | adapted | consumer-contract test doctrine |
| prototype | adapted | capture rules for exploratory vs production asks |
| grilling | adapted | interactive ask-facility routing |
| grill-with-docs | upstream | wrapper pairing `grilling` + `domain-modeling` |
| setup-matt-pocock-skills | upstream | carried verbatim |
| implement | adapted | closeout rules for tracker ticket states |
| orchestrator | upstream (BSL 1.1) | carried verbatim from [backnotprop/orchestrator](https://github.com/backnotprop/orchestrator); its CLI self-installs on first use |

## License

MIT — see [LICENSE](LICENSE). Upstream skills derive from
[mattpocock/skills](https://github.com/mattpocock/skills) by Matt Pocock.

Exception: [`skills/orchestrator`](skills/orchestrator/SKILL.md) is carried
from [backnotprop/orchestrator](https://github.com/backnotprop/orchestrator)
under **Business Source License 1.1** (Additional Use Grant permits production
use; converts to Apache-2.0 on 2029-07-09). Its full BSL text ships inside the
skill folder as `LICENSE` and is not re-licensed by this repo's MIT grant.
