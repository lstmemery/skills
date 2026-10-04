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
3. **Role comes from the invocation, not the first prompt.** Scan every user
   turn for `You are the /orchestrator` (coordinator) and worker-dispatch
   patterns (`You are a worker/code reviewer/read-only reviewer`,
   `parent-dispatched worker task`, `Read and execute your brief` or
   `Read and execute /…`, `Work autonomously to completion`). Coordinator
   wins over worker. A session whose first prompt looks interactive but which
   contains an orchestrator invocation is a coordinator session.

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
  listed session end, beyond `--grace-minutes`, default 2), empty digests,
  span clips, and index.md coordinator rows that disagree with the
  invocation-based role. Fix the corpus; do not ship a failing build.
- `--write` annotates `index.md` in place: coordinator roles are corrected to
  `coordinator (was: <old>)` and empty digests are flagged
  `[empty-digest]`. Treat `--write` as a triage aid, not a substitute for
  rebuilding the affected digests.
- Every listed session must have a digest: a listed session with no digest
  file fails verify (`NO-DIGEST`, exit `2`). This is the R-09 acceptance
  default. Pass `--allow-missing-digests` only when a build intentionally
  omits digests (the build policy gives digests to interactive/coordinator
  and friction-heavy worker sessions); those sessions are then reported
  without failing.

`derive-role` classifies one prompt text from stdin (`role` TAB `basis`) and is
the same logic the verifier applies to digest user turns.

The verifier reads only `index.md` and `sessions/*.md` — never raw
transcripts — so it is safe to run inside the retro's read boundaries.
