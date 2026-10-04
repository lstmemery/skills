---
name: pr
description: "Use when writing a PR body."
metadata:
  credits:
    skill: show-me
    author: Dex Horthy
    organisation: Humanlayer
    url: "https://github.com/humanlayer/skills/blob/main/plugins/show-me/skills/show-me/SKILL.md"
---

Use this template for writing the PR body:

```markdown
## Summary

<the problem and the behaviour change>

<optional: the smallest visual that clarifies this change>

## Evidence

<the checks actually run, with before and after when available>

## Merge Danger

**Door:** <one-way or two-way>

<optional: description>

**Blast Radius:** <one-word description>

<optional: potential ramifications of merge>
```

If the repository or the user provides a PR template, follow it and map these sections onto it.

## Sections

Skip all preambles and keep prose brief. Use the user's domain language from `GLOSSARY.md`.

### Summary

Lead with the problem and the behaviour change: what was wrong or missing, and what behaves differently after the change.

Add a visual only when it materially clarifies this change — the smallest view that makes the key point clear. When one does, read [references/visuals.md](references/visuals.md) for the menu of forms (pseudocode, call trees, component trees, file trees, Mermaid, diffs, whole blocks) and their placement guidance.

### Evidence

Report the checks actually run, with before and after when available. Never invent a failing-before test, console output, or screenshot; when no before exists, say so.

Screenshots are S-tier - when the environment is set up for it, the change is visual, and the screenshot comes from a real run.

Execution-based evidence is A-tier. Test results, console output. Show the exact test that now fails and passes, using pseudocode.

### Merge Danger

Required in every PR body; it may be a single line.

Describe whether it's a one-way or two-way door. You can walk back through two-way doors, but not one-way doors. A PR that is cheap to roll back is lower risk. Changes that involve destructive actions or hard-to-reverse decisions are one-way doors.

The blast radius is the potential impact or scope of the changes introduced by this PR. Consider all possibilities. Examples are layout shift, breakages for consumers, mobile responsiveness, etc.
