# Feedback techniques

Read when selecting or improving the command that detects the reported bug.
Use only environments and services the active runtime permits.

Prefer an existing failing test or CLI fixture where it reaches the symptom.
Other useful seams include an HTTP script against an authorized dev server,
a headless browser assertion, a captured request/event replay, a minimal
throwaway harness, a property/fuzz loop, automated bisection, or a differential
run against a known-good version/configuration. Choose the smallest mechanism
that observes the actual bug; no technique or fixed trial count is mandatory.

Tighten the loop by caching unrelated setup, pinning time/seeds/inputs, isolating
state, and asserting on the exact wrong value, timing, or error. Preserve the
original scenario alongside any reduced fixture. For timing-dependent bugs,
controlled stress or scheduling perturbations can raise the reproduction rate;
record them so before/after runs remain comparable.

If a human must perform part of the reproduction, use
[scripts/hitl-loop.template.sh](scripts/hitl-loop.template.sh) to structure the
step and captured result. It is a template, not a runnable script: it prompts
through an interactive terminal, which an agent session does not have, and its
app address comes from `APP_URL`. Adapt a copy, hand it to the user with the run
command, and read the answers they paste back. Keep secrets out of output. Do not claim the human step was run until its result is
available. If no permitted repro exists, report attempts and the precise
missing prerequisite: a sanitized captured artifact, access supplied through
the appropriate host workflow, or separately authorized instrumentation.
A restrictive runtime cannot be bypassed to obtain the missing evidence.
