# Deep Research — historical rationale

Read when maintaining or evaluating [SKILL.md](SKILL.md). The table below is
inherited historical rationale, not evidence verified by this revision. Its
source labels and measurements describe the earlier document's assertions;
reopen the originals before relying on them for a current claim. The
single-thread execution constraint active between 2026-09-20 and 2026-09-24 was
withdrawn on 2026-09-24; the measurement it rested on forbids nested fanout, not
orchestrated delegation.

## 1. Why each rule exists

| Rule in SKILL.md | Evidence | Source |
|---|---|---|
| No nested fanout (depth-one rule) | Orchestrator-worker delegation beat single-agent Opus 4 by **90.2%** on Anthropic's internal research eval — that pattern (separate workers, one per slice, orchestrated) is unaffected. What was measured 2026-09-16 on deployed harnesses: a SINGLE worker spawning its own parallel subagent threads multiplied raw token use several times per job and hid that cost from the caller's visible counter. In-worker fan-out is forbidden; delegation of slices to separate workers at the orchestrator level stays available. | [PRIMARY] https://www.anthropic.com/engineering/multi-agent-research-system + [MEASURED] 2026-09-16 per-session token audit |
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
