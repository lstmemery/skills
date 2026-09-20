---
name: diagnosing-bugs
description: Diagnose bugs, failures, and performance regressions using an observed failure signal and targeted probes.
---

# Diagnosing Bugs

Find and fix the cause of the user's exact symptom. Finish with evidence that
the original scenario now passes, a meaningful regression test or a documented
missing test seam, and temporary instrumentation removed.

Read relevant domain glossary and ADRs when exploring the codebase. Redact
secrets before showing any command, output, or artifact; use environment
variables for credentials and quote only signal-bearing captured lines.

## Establish the feedback

Build and run one command that reaches the actual failing path and detects the
reported symptom. Capture the invocation and redacted failure. A nearby crash
or a command that merely runs successfully is not an adequate signal. Tighten
setup, assertion specificity, and determinism enough to distinguish causes.
For repro techniques or human-only actions, read [FEEDBACK.md](FEEDBACK.md).

For intermittent failures, record workload, trials, failure rate, and conditions;
increase reproducibility where feasible. Retain those conditions for the final
comparison. A single passing run does not establish that a flake is fixed.
For performance regressions, pin the workload and record baseline measurements
before changing code; prefer a profiler, timing harness, or query plan to logs.

If no permitted feedback path can reproduce or detect the symptom, record the
attempts and missing evidence. Ask for the smallest actionable prerequisite
that the active runtime permits. Keep the diagnosis unresolved; do not present
a speculative code change as a verified fix.

## Narrow and test

Preserve the original scenario, then minimize enough to separate plausible
causes. Remove inputs, callers, configuration, or steps one at a time while
checking that the same symptom remains. Stop minimizing when additional cuts
would not change the next diagnostic decision; exhaustive minimality is not a
goal on its own.

Start with the strongest falsifiable hypothesis. One is sufficient when the
observed signal directly identifies a cause. If alternatives remain plausible,
or a probe contradicts the first explanation, rank the competing hypotheses
and choose a discriminating probe. Each hypothesis names its prediction:
“If X is the cause, changing Y will change the observed symptom in this way.”
Share the current hypothesis or ranked alternatives with the user; proceed with
permitted probes while awaiting any useful steering.

Change one variable per probe. Use debugger/REPL inspection when available;
otherwise add targeted logs at the distinctions between hypotheses. Give
instrumentation a unique prefix for cleanup. Retain evidence of which
prediction passed or failed; a plausible story alone does not settle the cause.

## Fix and verify

When a correct test seam exists, turn the reproducer into a regression test
before the fix, observe the expected failure, apply the fix, then observe a
pass. The test must exercise the real call pattern: a one-caller unit test
cannot lock down a bug that requires several callers or a longer chain.

If no suitable seam exists, document the architectural limitation rather than
adding an implementation-mirroring test. The original feedback command still
has to verify the fix. Re-run it against the original, unminimized scenario;
for flakes and performance compare the recorded conditions and measurements,
including remaining uncertainty. Run repository-required relevant checks.

Remove tagged debug instrumentation and dispose of task-owned throwaway
reproducers or retain them in a clearly marked debug location. Report the
confirmed cause, fix, verification, and material limits; include that evidence
in a commit/PR description when those artifacts are part of the task.
