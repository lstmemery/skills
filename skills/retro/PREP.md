# Retro-prep digest step

Contract for building a retro-prep corpus (`index.md` plus per-session digests),
and the deterministic check that verifies it. Finding R-09 (2026-10-02 build):
a coordinator digest was clipped, one digest shipped with no timeline, and the
day's main coordinator was labeled `interactive` because the role came from its
first prompt (`/clear`) instead of the `/orchestrator` invocation later in the
session.

## Builder rules

1. **Cover through the session end.** Every digest's timeline and its
   `Session span:` header must reach the session's end as listed in `index.md`
   (`End in window`). If the build window cuts the session off, the index end is
   the cutoff — the digest must still reach it.
2. **No empty digests.** A digest with zero timeline entries must not ship. If a
   session genuinely has no reportable activity, drop the digest row from the
   plan and say so in the build evidence instead.
3. **Record invocation evidence and derive the role from it.** For each digest,
   add a top-level `- Invocation evidence: ...` line copied from the session
   launch arguments, skill/agent invocation, or brief recorded in the
   transcript. Do not infer a role from arbitrary prompt text or copy it from
   the index. The verifier recognizes explicit coordinator, reviewer, and
   worker invocation forms in this field. If the transcript has no clear
   invocation evidence, record no field; the role is `unknown` regardless of
   the index Role cell. The verifier reports known disagreements and
   `verify --write` updates the index Role cell from the invocation evidence.

## Verification

After writing the corpus, run the check and fix everything it reports before
delivering:

```bash
python3 skills/retro/scripts/retro_prep_digest.py verify --corpus CORPUS --report coverage.json
```

- Exit `0` — all listed sessions pass. `coverage.json` reports per-session
  digest end-time coverage (last timeline entry and gap to the listed session
  end) and the timeline entry count, satisfying the R-09 acceptance: coverage
  is reported per session and every digest has a non-zero timeline.
- Exit `2` — failures: truncated digests (timeline or span ends before the
  listed session end), empty digests, span clips, and known index Role cells
  that disagree with invocation evidence. Fix the corpus; do not ship a
  failing build.
- `--write` updates each index Role cell from the invocation evidence and
  flags empty digests with `[empty-digest]`. Missing or unrecognized evidence
  is written as `unknown`. Treat `--write` as a triage aid, not a substitute
  for rebuilding the affected digests.
- Every listed session must have a digest: a listed session with no digest
  file fails verify (`NO-DIGEST`, exit `2`). This is the R-09 acceptance
  default. Pass `--allow-missing-digests` only when a build intentionally
  omits digests (the build policy gives digests to interactive/coordinator
  and friction-heavy worker sessions); those sessions are then reported
  without failing.

The verifier reads only `index.md` and `sessions/*.md` — never raw
transcripts — so it is safe to run inside the retro's read boundaries. The
corpus builder must preserve the invocation evidence from its transcript in
the digest metadata for the verifier to use.
