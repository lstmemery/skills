---
name: codebase-design
description: Design module interfaces and test seams; find deepening opportunities or consult the shared design vocabulary.
---

# Codebase Design

Design deep modules: substantial behavior behind a small interface, at a seam
that lets callers and tests exercise that behavior. This is a shared reference,
not a separate interview or implementation workflow. Use the project's
`CONTEXT.md` for domain names and respect the relevant ADRs.

## Vocabulary

Use these architecture terms consistently; avoid substituting component, service,
unit, API, signature, or boundary when you mean the concepts below.

- **Module:** an interface and its implementation, at any scale: function, class,
  package, or tier-spanning slice.
- **Interface:** the module's complete caller-facing surface: types, invariants,
  ordering, error modes, configuration, and performance characteristics. It is
  broader than a language's interface declaration or public methods.
- **Implementation:** the code inside a module. This describes substance;
  **adapter** describes its role at a seam.
- **Depth:** behavior exercisable per unit of interface a caller must learn.
  A **deep** module hides substantial behavior; a **shallow** module exposes
  nearly as much complexity as its implementation. Count leverage, not code lines.
- **Seam:** a place to alter behavior without editing there; the location of a
  module's interface. Its placement is a separate design choice from its contents.
- **Adapter:** a concrete thing satisfying an interface at a seam.
- **Leverage:** capability gained per unit of interface learned, reused across
  callers and tests.
- **Locality:** change, bugs, knowledge, and verification concentrated in one place.

## Principles

- **Deletion test:** if deleting a module makes complexity vanish, it was a
  pass-through. If complexity spreads across callers, it was earning its keep.
- **Depth belongs to the interface.** A deep module may contain small encapsulated
  submodules; callers need not learn their interfaces.
- **The interface is the consumer's test surface.** Consumer contract tests use
  the same seam as callers. Tests of an encapsulated submodule's own contract at
  its own seam are also legitimate. Reject assertions about wiring, field copies,
  forwarding, defaults, mock echoes, or source text: they describe implementation.
- **One adapter is a hypothetical seam; two make a real one.** Introduce a port
  only when concrete variation justifies it, such as production and test adapters.
- **Keep the caller's burden small.** Reduce methods and parameters, make
  dependencies explicit, and keep computation results observable. Hide complexity
  where doing so increases leverage and locality.

## Choose the needed reference

- When these terms need a concrete illustration, read [EXAMPLES.md](EXAMPLES.md)
  for depth diagrams and dependency-injection examples.
- When deepening a cluster, read [DEEPENING.md](DEEPENING.md) to classify dependencies,
  choose adapters, and replace obsolete tests at the new interface.
- When comparing alternative interfaces for a chosen candidate, read
  [DESIGN-IT-TWICE.md](DESIGN-IT-TWICE.md) for contrasting designs and a recommendation.
