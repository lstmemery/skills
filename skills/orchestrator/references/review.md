# Human code inspection

Read before presenting repository changes, processing code feedback, opening a
PR, or integrating a local result. Preferences own the human decision boundary.

## Prepare an inspectable candidate

Delegate preparation to the coding worker or a bounded review worker. Produce:

- intended repository/target branch, explicit comparison base, and candidate
  commit SHA; identify the exact comparison used;
- the complete task patch and changed-file inventory, including new files;
- access to full current files and a list of binary/large/otherwise omitted
  changes requiring a different inspection method;
- checks and agent-review findings with their evidence and unresolved items;
- the intended next action and any existing authorization for that action.

Prefer a committed candidate, since local commits are permitted. Before marking
it ready, account for task changes still staged, unstaged, or untracked. Commit
them or provide an explicit immutable snapshot; a HEAD SHA cannot identify
uncommitted work. Generated artifacts outside the candidate remain separately
identified. Use the task's actual base, not an assumed `main` or a last-turn diff.

## Herdr view

Use the installed reviewr contract if compatible. Its documented branch scope
includes commits plus uncommitted changes; default uncommitted scope can be empty
after the worker commits. Open one diff/file view next to the worker with the
correct worktree and base, preserving the user's focus. Verify its target and
diff coverage against the candidate record.

Discover available plugin/pane commands through current Herdr help. Prefer an
explicit target or standalone viewer process in an explicitly created pane.
An action that acts on the UI-focused workspace is unsuitable for unattended
placement unless the installed contract provides a safe explicit target. Do not
assume a Treehouse allocation fires a Herdr worktree-created plugin event.

If reviewr cannot run, expose the saved patch and files through a usable local
viewer/pager and report the missing preferred surface. Preserve the candidate
and requested decision boundary. Omitted files stay visible in the inventory;
an unavailable or partial viewer is not proof the user has inspected the code.

## Present and revise

Return a short card:

```text
Ready for your code review: <task>
Open: <workspace / review pane / fallback location>
Candidate: <base> -> <candidate>, <changed-file count>
Checks: <summary; links to evidence and omissions>
Next decision: <open PR / apply locally / another named action>
```

Let the user inspect actual code and send feedback to the worker. Record submitted
feedback and disposition durably; reviewr documents comments as in-memory until
sent/copied. Pending comments must not be silently lost during cleanup. A review
decision is keyed to the candidate and action, not a generic permission to ship.

Before executing an authorized action, verify that candidate identity and scope
still match. A changed patch returns to inspection; preserve earlier approval as
history. Run any newly necessary checks. Opening a PR and merging it have separate
authority and observable results. Verify the actual PR/local integration outcome
before claiming completion.

## Tool card

**Purpose/selection:** expose code for human inspection at the task's review
boundary; use reviewr when available, otherwise an explicit local artifact view.
**Input:** task identity, worktree, comparison base/candidate, intended pane and
inspection artifacts. **Output:** actual viewer location, target verification,
candidate record, omissions, and user decision if one was provided.
**Effects:** creates a pane/process; reviewr may write private baseline refs and
submits comments only on user action. Opening a view does not authorize publish,
merge, or cleanup. **Execution:** one viewer per candidate/worktree; bounded
startup observation. Inspect an ambiguous start before retrying to avoid duplicate
panes. **Recovery:** retain artifacts, report viewer gaps, and preserve the gate.

Reference: [reviewr](https://github.com/persiyanov/herdr-reviewr). The installed
version is authoritative for exact flags and capabilities.
