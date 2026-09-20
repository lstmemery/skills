# Remote machines and server identity

Read this branch for remote hosts, saved machine profiles, or multiple Herdr servers.

IDs and live agent names are server-scoped: different machines can both have `w1:p1` or `reviewer`. Selecting a machine in the TUI does not retarget commands in a pane; they retain inherited session/socket context. Run remote commands on the intended host with its explicit session and rediscover IDs there.

`herdr machine list` shows saved connection profiles, not a cross-machine pane inventory; use `--json` for scripts. Add, remove, enable, or disable profiles only when requested. Removing a profile disconnects the client but does not stop remote sessions.

Adding a machine uses its remote default session unless `--remote-session` is supplied. Setup asks before stopping an incompatible server and defaults to No. Replacement requires the user's consent; missing methods or version mismatch are not that consent. Experimental handoff is not part of `machine add`.

Use installed group help for syntax and apply [SKILL.md](SKILL.md)'s ownership and server-stop boundaries.
