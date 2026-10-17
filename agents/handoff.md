---
description: Rewrite this agent's brief so a fresh conversation can pick up from here
argument-hint: "[brief path]"
---
Rewrite your agent brief so that a brand-new conversation, with no memory of
this one, can carry on the work.

The brief is the file at `$ARGUMENTS` if a path was given; otherwise the path in
the `AGENT_BRIEF` environment variable (run `printenv AGENT_BRIEF`). If neither
exists, say that this session is not a named agent and stop.

Replace the whole file with exactly these sections:

# <agent name, e.g. notes·drafts>
## Mission
One or two lines: what this agent is for. Keep the existing mission unless the
user changed it in this conversation.
## State
What is done, what is in progress, the branch and any uncommitted work,
anything deployed or half-deployed.
## Next steps
The concrete next actions, in order.
## Gotchas
What cost time and would again: commands, paths, traps, decisions and why.

Be specific (file paths, commands, PR numbers), keep it under about 60 lines,
then answer with one line saying the brief is updated, and that the next
conversation is started with Fresh (the launcher's Fresh conversation, or
`agentctl start <project>/<name> --fresh`), not `/clear`.
