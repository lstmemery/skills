# Skill mechanics

Read this branch of [writing-for-agents](SKILL.md) when creating or editing a skill. The active runtime contract owns loading commands, discovery behavior, and supported metadata.

## Discovery and explicit loading

**Automatic discovery** lets the agent select a skill from the names and descriptions the harness exposes. **Explicit loading** follows a user request or an applicable instruction naming that skill. These are different operations: a manual invocation policy does not, by itself, establish whether another document can explicitly load the skill.

Preserve the existing invocation policy unless the user requests a change. This library uses `disable-model-invocation: true` for manual entrypoints and, where present, `policy.allow_implicit_invocation: false` in `agents/openai.yaml`. Retain those settings and unrelated UI metadata; let the target runtime interpret them. Do not infer that descriptions are always loaded, always hidden, or inaccessible to explicit references across every harness.

Write discoverable descriptions as precise context pointers: capability, meaningful trigger branches, and boundaries that prevent likely misrouting. Manual descriptions can be short human summaries. New entrypoints should follow the target environment's skill-creation guidance and user intent.

## Composition and shared reference

A **router skill** is an entrypoint that selects or composes named workflows. Keep its branches and return boundary clear, and use the runtime's supported mechanism for loading dependencies. A router may reduce what the human must remember while still increasing the instructions and work loaded by a run.

Split off a skill when it needs a distinct invocation or independent task contract. Use an ordinary linked reference for detail that only supports an existing branch. Shared material has one owner; explicit pointers can reach that owner using the runtime's allowed mechanism. Invocation policy alone is not a reason to duplicate shared reference or move it outside a skill directory.

When changing a router or invocation policy, verify the actual loading path in the target harness. A static link check establishes file reachability, not discovery reliability.
