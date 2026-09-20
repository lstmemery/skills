---
name: tdd
description: Use test-first development for features, fixes, and integration tests; test lists, tracer bullets, and red-green cycles.
---

# Test-Driven Development

Run one behavior scenario through red → green at a time. Return when the agreed
scenarios pass with earlier tests still green, or identify the blocked scenario.
Refactoring and review belong to the calling workflow; this skill returns to it
without invoking `implement`. When `implement` is the caller, it owns review,
green-state refactoring, finding resolution, and commit.

Read `CONTEXT.md` when present and respect relevant ADRs. Use their domain terms
in interface and test names.

## The loop

1. **List scenarios.** Before testing, write one line per required behavior.
   Start with the simplest path; each cycle handles one vertical slice and uses
   what the previous cycle taught you.
2. **Settle the seam.** Record the interface where behavior will be observed.
   Proceed when the request, approved spec/ticket, or existing test/contract fixes
   it. Ask only when a new seam changes scope or trades off a real design choice.
   When interface shape or seam placement is in question, load `codebase-design`
   as a reference; use its module, interface, depth, seam, adapter, leverage, and
   locality vocabulary. Finish this step before writing the test.
3. **Red.** Make exactly one scenario runnable at that seam. It must fail for
   the scenario's assertion or behavior, not setup, import, or syntax. Derive the
   expected value from an independent source: a known-good literal, worked
   example, or spec. Show the relevant failing result before implementation.
4. **Green.** Write only enough code to pass that test. Finish when the new test
   and every previous test pass. One seam, one test, one minimal implementation
   per cycle; refactoring is outside this loop.
5. **Repeat or return.** Take the next scenario as its own slice. If it cannot
   be tested at the settled seam, revisit the design through `codebase-design`
   instead of reaching into implementation details. Return after the last slice.

## Test quality

Tests describe caller-visible behavior through the module's interface and survive
internal refactors. Name the capability, not the method. Multiple assertions may
specify one outcome; separate unrelated outcomes. Testing an encapsulated
submodule's own contract at its own seam is legitimate.

Reject these patterns during every cycle:

- **Implementation coupling:** wiring, field copies, forwarding, defaults, mock
  echoes, source-text assertions, or verification through a side channel instead
  of the interface. A behavior-preserving refactor should not break the test.
- **Tautology:** recomputing the expected value the way the implementation does.
  A test must be capable of disagreeing with the implementation.
- **Horizontal slicing:** writing all tests before any implementation. Keep the
  scenario → red → green sequence so later tests reflect observed behavior.

When a test's quality is unclear, read [tests.md](tests.md) for examples. Before
choosing a test double or interaction assertion, read [mocking.md](mocking.md)
for fidelity, contract coverage, and the side-effect exception.
