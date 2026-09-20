---
name: implement
description: Implement work from a spec or tickets, verify it, review it, and commit.
disable-model-invocation: true
---

# Implement

Implement the requested spec or tickets. Finish with verified behavior, every
review finding dispositioned, a commit, and the applicable ticket closeout.
Follow the task's existing authorization, isolation, and review boundaries.

For local Markdown tickets, read the [local-ticket command](../setup-matt-pocock-skills/LOCAL-TICKETS.md), use the canonical shared feature directory, and acquire the ticket’s ownership claim before work. Keep the returned claim ID; triage status does not substitute for ownership.

1. **Build.** For behavior with a meaningful runnable test, load and follow `tdd`
   at a seam settled by the request, spec, ticket, or existing contract. Its seam
   rule governs unresolved design choices. When no meaningful test seam exists,
   record that limitation and use applicable verification. TDD returns here at
   green; keep refactoring outside its red → green cycles.
2. **Verify.** Run typechecking and affected test files during implementation.
   Complete the repository's required checks and full suite at final verification.
3. **Review.** Load and follow `code-review` against the completed change. Preserve
   its Standards/Spec separation and explicit missing-spec procedure.
4. **Refactor and resolve.** With tests green, clean up structure. Accept and fix
   each applicable finding or record why it was rejected; leave no accepted
   finding unresolved. Rerun affected checks after changes, refreshing review
   evidence where the reviewed behavior changed. Final verification covers the
   actual candidate being committed.
5. **Commit and close.** Commit to the task's current branch. When finished and
   verified, use the local helper’s `complete` operation with the current owner,
   claim ID, and actual verification evidence for local implementation tickets.
   It sets `done` and retires the active claim. For other trackers, use their
   configured closeout operation. Decision and
   wayfinding tickets terminate as `resolved` under their own workflows.
