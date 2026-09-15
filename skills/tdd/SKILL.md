---
name: tdd
description: Test-driven development. Use when the user wants to build features or fix bugs test-first, writes the failing test first, mentions "red-green-refactor", "test list", or "tracer bullet", or wants integration tests.
---

# Test-Driven Development

TDD is the red → green loop. This skill is the ordered recipe that makes that loop produce tests worth keeping: what a good test is, where tests go, the anti-patterns, and the completion criteria for each step. Every section applies on every cycle: consult them before and during the loop, not after.

When exploring the codebase, read `CONTEXT.md` (if it exists) so test names and interface vocabulary match the project's domain language, and respect ADRs in the area you're touching.

## The recipe

Run the steps in order. Each has a completion criterion; do not advance past an unmet one.

1. **List the behavior scenarios.** Before any test, write the scenarios the change must support, one line each — the test list. Order them so the first is the simplest path through the system. Work one **vertical slice** at a time: each cycle walks one scenario end to end and responds to what the last cycle taught you.
2. **Settle the seam.** A **seam** is the boundary you test at: the interface where you observe behavior without reaching past it. Write down the seam under test before writing any test. A seam is already settled when the request names it, an approved spec or ticket fixes it, or an existing test or contract pins it — proceed without asking. Ask the user only when choosing a new seam would change scope or trade off a real design decision. Settling up front is how testing effort lands on critical paths and complex logic instead of every edge case.

   When the shape of that interface is itself in question (how deep the module is, where the seam belongs, what the interface should expose), load and follow skill `codebase-design` for the vocabulary. It is the shared source of the module, interface, depth, seam, adapter, leverage and locality terms, and it is a reference to consult, not a session to run.
3. **Red.** Turn exactly one scenario from the list into a runnable test at that seam.

   Done when: the test fails with an assertion or behavior failure that names the scenario — not a setup, import, or syntax error — and the expected value comes from an **independent source of truth** (a known-good literal, a worked example, the spec), never recomputed the way the code will compute it. A test that cannot fail for the right reason proves nothing when it later passes.
4. **Green.** Write only enough code to pass that test. Don't anticipate future tests or add speculative features.

   Done when: the new test passes **and every previous test still passes**. One seam, one test, one minimal implementation per cycle.
5. **Repeat.** Take the next scenario from the list as its own slice. A scenario you cannot test at the settled seam is a design smell — return to `codebase-design`, not to implementation-coupled tests.

Refactoring is not part of the loop. The red → green cycle ends at green. Refactoring at green state, and resolving accepted review findings, are explicit steps of the implementation workflow — load and follow skill `implement` — which runs them between review and commit. `code-review` reports findings; it does not apply them.

## What a good test is

Tests verify behavior through a module's interface, not implementation details. Code can change entirely; tests shouldn't. A good test reads like a specification: "user can checkout with valid cart" tells you exactly what capability exists, and it survives refactors because it doesn't care about internal structure. Name tests for the caller-visible behavior they specify, not the method they exercise. Multiple assertions are fine when they describe one outcome; split them when they describe unrelated ones.

See [tests.md](tests.md) for examples and [mocking.md](mocking.md) for mocking guidelines.

## Anti-patterns

- **Implementation-coupled**: asserts wiring, field copies, forwarding, defaults, mock echoes, or source text, or verifies through a side channel (querying the database instead of using the interface). Not the same as testing an encapsulated submodule's own contract at that submodule's seam, which is legitimate — the failure is asserting how the code is written rather than what it does. The tell: the test breaks when you refactor but behavior hasn't changed.
- **Tautological**: the assertion recomputes the expected value the way the code does (`expect(add(a, b)).toBe(a + b)`, a snapshot derived by hand the same way, a constant asserted equal to itself), so it passes by construction and can never disagree with the code. Expected values must come from an independent source of truth: a known-good literal, a worked example, the spec. This is a red-step completion criterion, not a style preference.
- **Horizontal slicing**: writing all tests first, then all implementation. Bulk tests verify _imagined_ behavior: you test the _shape_ of things rather than user-facing behavior, the tests go insensitive to real changes, and you commit to test structure before understanding the implementation. The recipe's one-scenario-per-cycle rule is the alternative.
