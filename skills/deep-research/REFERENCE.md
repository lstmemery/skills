# Deep Research — evidence, templates, options

Companion to [SKILL.md](SKILL.md). Every rule in the skill traces to a measured result below.

## 1. Why each rule exists

| Rule in SKILL.md | Evidence | Source |
|---|---|---|
| Fan out into parallel subagents | Orchestrator-worker (Opus 4 lead + Sonnet 4 subagents) beat single-agent Opus 4 by **90.2%** on Anthropic's internal research eval; subagents compress separate search spaces into separate context windows | [PRIMARY] https://www.anthropic.com/engineering/multi-agent-research-system |
| Shape- and effort-scaled execution | Anthropic's shipped heuristics scale calls and workers with question complexity; early agents spawned 50 subagents for simple queries until these rules were added | [PRIMARY] Anthropic engineering blog (same URL) |
| Spend is the lever, but bounded | Token usage alone explains **~80%** of BrowseComp performance variance; multi-agent costs ~15x chat tokens — worth it only for high-value parallelizable work | [PRIMARY] Anthropic engineering blog |
| Test-time compute genuinely scales | BrowseComp accuracy scales smoothly with browsing effort; best-of-N confidence voting over 64 samples adds **15-25%** | [PRIMARY] https://arxiv.org/abs/2504.12516 |
| Query breadth/quality beats model swapping | Holding the model fixed and improving retrieval lifts GPT-5 from **55.9% → 70.1%** on BrowseComp-Plus *with fewer search calls*; a weak retriever (BM25 + Search-R1) collapses to 3.86% | [PRIMARY] https://arxiv.org/abs/2508.06600 |
| Name the implicit context | Leading DR agents (Gemini DR, OpenAI DR) score **under 68%** rubric compliance; the top failure is missed implicit context | [PRIMARY] https://arxiv.org/abs/2511.07685 |
| Synthesize independent sources when a claim has multiple owners | Second dominant rubric failure is "inadequate reasoning over retrieved information" — quoting is not synthesizing | [PRIMARY] https://arxiv.org/abs/2511.07685 |
| Audit cited **and** uncited claims | ReportBench verifies cited content against originals *and* validates non-cited claims; DEER requires report-wide verification of both plus evidence-quality scoring | [PRIMARY] https://arxiv.org/abs/2508.15804 · https://arxiv.org/abs/2512.17776 |
| Content farms are leads, not evidence | Anthropic's testers found agents "consistently chose SEO-optimized content farms over authoritative but less highly-ranked sources"; fixed at the prompt layer | [PRIMARY] Anthropic engineering blog |
| No near-duplicate searches | Redundancy penalties on repeated similar queries improve deep-search agents; stepwise information-gain rewards beat global rewards (+11.2% / +4.2% at 3B/7B) | [VERIFIED] https://arxiv.org/abs/2509.10446 · https://arxiv.org/abs/2505.15107 |
| Artifacts, not prose relay | Anthropic persists plans to memory and subagent outputs to the filesystem, passing references — avoids the "game of telephone" through context | [PRIMARY] Anthropic engineering blog |
| Honest not-found + stop rule | RL-trained web agents show emergent honesty when answers are unfindable; browsing agents otherwise show high calibration error (91% on BrowseComp) | [PRIMARY] https://arxiv.org/abs/2504.03160 · https://arxiv.org/abs/2504.12516 |
| Scale effort per query, not per skill | Query-adaptive agent architectures hit equal-or-better quality at **6-45%** of fixed multi-agent cost | [VERIFIED] https://arxiv.org/abs/2502.04180 |
| Treat discovery results as leads | Static benchmarks suffer contamination and temporal misalignment; live-web evaluation (DR-Arena) correlates 0.94 Spearman with human preference leaderboards | [VERIFIED] https://arxiv.org/abs/2601.10504 |

Full derivation, benchmark table, and failure-mode catalogue for this skill live in the host repo and are not shipped in the bundle. Inspect the runtime-provided prior-art locations before searching; they may contain operator-injected files.

## 2. Worker contract

Load this section when delegating a slice. Adapt the artifact path to the runtime’s designated output directory.

Copy the prompt per slice into its delegation. The shared contract travels with the delegation; each worker gets only its own slice.

```
# Target
Slice N: <sub-question>. Non-goals: <what other slices own>.

# Change
1. Search wide first (short queries), evaluate the landscape, then narrow.
2. Follow every claim to the source that owns it — official docs, source code,
   specs, papers, first-party APIs. Roundups and listicles are leads only.
3. Open each source before citing any number from it.
4. Write findings to the agreed output root as rows:
   claim | URL | exact quote or number | retrieval date | PRIMARY or SECONDARY
5. Note contradictions with other likely slices rather than resolving them alone.

# Acceptance
- >= <k> distinct primary sources, each opened this session.
- Every number in the artifact traceable to a quote in the same row.
- Explicit "could not verify" list for anything unresolved.
- No formatters, linters, or project-wide test suites.
```

## 3. Citation audit

Run before writing the report, against the merged artifact set:

1. Extract every number, date, version, price, and superlative in the draft.
2. For each: is there a row with a URL opened this session? If not → re-open or drop.
3. Confirm the quote actually supports the claim (not a nearby similar number).
4. Check vendor self-reported figures are labelled as such.
5. Check every claim not carrying a citation — uncited load-bearing statements are the blind spot rubric benchmarks penalise.
6. Tag: `[PRIMARY]` (owner's page), `[INDEPENDENT]` (third-party corroboration), `[DISCOVERED, unverified]` (surfaced by a discovery layer, never opened), `[PRIOR-NOTE]` (prior art supplied by the runtime).
7. Anything still `[DISCOVERED, unverified]` goes in the gap statement, not the TL;DR.

## 4. Wide vs deep execution shapes

- **Deep** (one hard question, long causal chain): fewer subagents, more iterations each; replan after every round; keep a running sub-question graph.
- **Wide** (large-N sweep: 50 competitors, 200 packages): one subagent per chunk with identical structured per-item output, then aggregate — MapReduce-shaped decomposition beats iterative depth here (+5.11-17.50% Item F1, 45.8% less runtime, https://arxiv.org/abs/2602.01331).
- **Mixed**: run the wide sweep first to enumerate candidates, then a deep pass on the shortlist.

## 5. Anti-patterns

- Spawning subagents before checking the runtime-provided prior art.
- Overlapping slices — two agents running the same searches, findings double-counted.
- A report whose TL;DR contains a number that appears nowhere in the artifacts.
- Smoothing over a contradiction between two sources instead of naming it.
- "Comprehensive" reports with no gap statement — absence of admitted gaps means the audit did not happen.
- Saving artifacts outside the runtime-designated output root or queueing an intermediate artifact.
