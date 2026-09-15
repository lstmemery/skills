---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

Use TDD where possible, at seams the request, the spec, or existing tests have already settled — load and follow skill `tdd` for the loop and the seam rules.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, load and follow skill `code-review` to review the work.

Refactor at green state: with the tests passing, clean up structure. The red → green loop stays refactor-free.

Resolve every accepted review finding before committing: apply it, or record the reason it was rejected. Accepted findings are never left unresolved.

Commit your work to the current branch.

Closeout: when the work is accepted — finished and verified — set the ticket's `Status:` line to `done`, the terminal state for an implementation ticket. Decision and wayfinding tickets terminate as `resolved` under their own workflow; leave them to it.
