# `CLAUDE.md` block for boxes that enable Run command

The launcher cannot tell a request from your phone apart from one a Claude
session makes on the box itself (see SECURITY.md → *Run command*). The missing
piece is a rule for the agent. Put this in the box's `~/.claude/CLAUDE.md`
(every project's sessions read it), replacing the host name:

```markdown
## Commands the user has to run themselves
This box is usually driven from a phone over Remote Control, where
`! <command>` cannot be typed. When the user must run a command (it needs
their approval, e.g. changes production, or it is interactive, e.g. a login),
do not suggest `! <command>`. Give the exact command in a code block and ask
them to run it from the launcher: **Run command** on the project's row at
https://<host>.<your-tailnet>.ts.net (or directly
https://<host>.<your-tailnet>.ts.net/run/<project-slug>). It runs in that
project's launch directory, so write the command for that directory (use `cd`
inside it if it must run elsewhere). Then ask them to paste back the output
(the modal has a Copy output button).

Never call the launcher's /run endpoints yourself (curl, a browser, or
anything else). They exist so the user runs what you may not; calling them
would sidestep the permission check.

When work needs its own branch and checkout, the user can tap **New
instance** on the project's row in the launcher; it appears as
`devbox-<slug>--<name>` in the Claude app and works in
`.claude/worktrees/<name>`.
```

If you manage the box with a configuration tool, keep this in a marked block
so the rest of the file stays yours (Ansible's `blockinfile`, for example).
