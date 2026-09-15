# Tutor reference

This file is disclosed reference for `$tutor`; the contract in `SKILL.md` is the operating instruction.

## Evidence that shapes the loop

| Design choice | Evidence | Implication |
|---|---|---|
| Ask before telling; scaffold with questions | MathDial reports guiding learners through “various scaffolding questions” ([EMNLP 2023](https://aclanthology.org/2023.findings-emnlp.372/)). | Use questions that expose the learner's next reasoning step. |
| Explanations over answer dumping | NewtBot study recommends explanations, examples, and analogies and withholding complete solutions until understanding is shown ([ACM 2024](https://doi.org/10.1145/3613905.3647957)). | Preserve productive effort; reveal only the next substep. |
| Adapt help rather than applying a fixed fade | An ITS experiment found fade-in assistance outperformed fade-out ([2020](https://doi.org/10.1007/s11251-020-09520-7)). | Increase or decrease support from observed performance; there is no universal schedule. |
| Diagnose from work, not mind-reading | Misconceptions are not directly observable; infer them from typical mistakes ([ITS research 2023](https://dl.acm.org/doi/10.1145/3545945.3569806)). | State diagnoses as hypotheses and test them. |
| Evaluate pedagogical moves, not fluency | AI-tutor evaluation proposes dimensions for locating mistakes and grounding guidance in confusion ([NAACL 2025](https://aclanthology.org/2025.naacl-long.57/)). | Check whether feedback addresses the learner's error, not just style or final accuracy. |
| Retrieval and spacing build durable memory | A review reports robust retrieval effects (Hedges' g ≈ 0.50–0.63) ([Nature Reviews Psychology 2022](https://doi.org/10.1038/s44159-022-00089-1)). | Add no-help recall and spaced review; assisted success is not retention. |
| Calibrate trust | Disclaimers reduced overreliance on incorrect LLM-tutor advice ([HCOMP 2024](https://doi.org/10.1609/hcomp.v12i1.31597)). | Mark uncertainty and invite verification when the tutor may be wrong. |
| Treat GenAI as an instrument, not pedagogy | OECD says general-purpose GenAI generates outputs and has “no pedagogical intent” ([OECD 2026](https://www.oecd.org/content/dam/oecd/en/publications/reports/2026/07/policies-supporting-responsible-and-systematic-genai-adoption-in-higher-education_0f8d9b9f/c4e5621f-en.pdf)). | The interaction protocol supplies the pedagogy; do not equate task completion with learning. |
| Protect agency and privacy | UNESCO calls for data-privacy protection and human-centered regulation ([UNESCO 2023](https://www.unesco.org/en/articles/guidance-generative-ai-education-and-research)). | Minimize retained learner data and keep consequential judgments reviewable. |

## Practical diagnosis

Classify the learner's turn before choosing help:

- **No attempt:** ask for a prediction, definition, or first step.
- **Factual gap:** give a concise correction, then request retrieval in the learner's words.
- **Process gap:** point to the first incorrect or missing move; ask what rule would justify it.
- **Conceptual confusion:** contrast the learner's model with the source using one counterexample.
- **Transfer gap:** vary surface details while preserving the underlying structure.
- **Confidence error:** ask what evidence supports the confidence estimate, then verify.

Do not infer a stable ability from one answer. Use repeated, independent attempts.

## When to research

Research content when the source is incomplete, the learner requests outside context, or a claim is current or high-stakes. Search primary or review sources, cite them inline, and keep the source's claims separate from external claims. If evidence conflicts, show both positions and the unresolved point. Do not research merely to make a short source question longer.

## Stop and ask

Ask the learner when the source, goal, or requested level is genuinely underspecified; when a high-stakes decision needs a qualified human; or when two interpretations lead to different answers. Otherwise make a reasonable assumption, state it briefly, and continue.
