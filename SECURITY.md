# Security model

Read this before you install. The summary is: **this service has no
authentication, and it exists to start AI agents that can modify your code.**
Its only access control is the network it is reachable on.

## What an attacker gets

Anyone who can open the UI can:

- **start, stop, park and resume any agent in any of your projects**, with
  your Claude account and your Unix user's permissions — that is read and
  write access to every repo under `LAUNCHER_BASE_DIR`, and whatever else that
  user can do;
- **create new agents** (*New agent*), each of which is a git worktree and a
  branch `agent/<name>` cut in your repo, and **start scratch sessions**
  (*Quick chat*);
- **start a fresh conversation for any agent** (*Fresh conversation*), seeded
  from its brief. A stale brief makes the first tap refuse, but *Fresh
  anyway* is one more tap; the old conversation is not deleted, just no longer
  the one the agent resumes;
- **archive any named agent**, which removes its worktree. Archive refuses
  when the worktree has uncommitted changes, commits on no remote, or a
  gitignored file at its top level, and there is no force button — so it
  cannot destroy unsaved work, but it can retire an agent you meant to keep
  (the branch and the brief, under `~/.config/agents/archive/`, survive);
- **read the last 20 lines of any agent's output** via *Show output*, which
  may contain source code, file paths or error text — and, through *Open in
  the Claude app*, the link to a live agent's session (opening it still needs
  your Claude account);
- **rename a repo's `origin` remote to `github`** (a legacy button, shown only
  for a GitHub `origin`; besides agent worktrees and branches, the only write
  the launcher performs inside a repo);
- **complete a Claude sign-in** if the box is signed out — which binds the box
  to *their* Claude account.
- **restart any agent in a different permission mode** — *Default
  (settings)*, *Accept edits*, *Auto* or *Plan*. `auto` lets the agent act
  without asking. `bypassPermissions` and `dontAsk` are not offered, and the
  server refuses any mode outside that list, so the page cannot be used to
  switch the permission check off entirely;
- **run arbitrary shell commands as your user**, *if* you have enabled **Run
  command** by setting `LAUNCHER_RUN_USER` — see below.

There is no login, no session cookie, no CSRF token, and no audit log.

## Why it is built that way

It is a single-user tool for one person's development box, reached over a
private tailnet. Adding an auth layer in front of a service whose entire purpose
is to be tapped from a phone in ten seconds would have meant a second credential
to lose. The tailnet *is* the credential.

That reasoning holds exactly as long as the deployment assumptions do.

## Deployment requirements

**Do:**

- Keep the bind address on loopback (`LAUNCHER_HOST=127.0.0.1`, the default).
- Expose it with `tailscale serve`, which terminates TLS and makes it reachable
  only from devices on your tailnet.
- Treat every device on that tailnet as trusted with your source code. If your
  tailnet has devices you do not control, restrict access with a Tailscale ACL.

**Do not:**

- Use `tailscale funnel`. That publishes the service to the public internet.
- Bind to `0.0.0.0` or forward the port, on a LAN you share or otherwise.
- Put it behind a plain reverse proxy without authentication.
- Run it on a multi-user machine where other users can reach loopback.

## Behaviours that will surprise you

Two things the launcher does deliberately, which are reasonable for its intended
deployment and would be indefensible outside it:

- **It pre-accepts Claude Code's workspace trust prompt.** `launcher/trust.py`
  writes `hasTrustDialogAccepted` and `remoteDialogSeen` into `~/.claude.json`
  for each project it launches. That prompt is a safety gate asking whether you
  trust the code in a directory; the launcher answers yes on your behalf,
  because there is no way to answer an interactive prompt from a phone. Only put
  directories you trust under `LAUNCHER_BASE_DIR`.
- **It runs agents unattended.** A running agent can be picked up from the
  Claude app at any time, days later. The reaper parks agents idle for three
  days; stop the ones you are not using sooner.
- **It restarts agents at boot.** `agents-restore.service` resumes every agent
  that was running when the box went down, without anyone at the keyboard.

## agentctl

`agentctl` has no access control of its own: it is a command for the Unix
user who owns the agents, and anything running as that user (including a
Claude session) can create, start, stop or archive agents with it, the same
as with the web UI. It writes `~/.config/agents/`, which should stay readable
only by that user.

## Run command

Off by default. When `LAUNCHER_RUN_USER` is set, the UI offers a **Run
command** button that runs whatever you paste, as the launcher's Unix user, in
the project's directory. It exists so that *you* can run what Claude may not —
a production deploy, an interactive login — from the phone.

Two checks gate it, and it matters what they do and do not stop:

- Requests must carry a `Tailscale-User-Login` header equal to
  `LAUNCHER_RUN_USER`. Tailscale Serve sets that header on every request it
  proxies, from the identity of the tailnet device making it. So through
  Serve, only you can reach it.
- POSTs must be same-origin (`Sec-Fetch-Site`, falling back to `Origin`
  matching the host), so a hostile page opened in the phone's browser cannot
  make it run a command through the header Serve would add.

**Neither stops a process on the box itself.** `127.0.0.1:8765` is reachable
locally and the header can be forged there — by a Claude session, for example,
which is exactly the actor whose permission check the feature exists to
respect. The launcher cannot tell the difference, so the control is a rule for
the agent, not a wall: put the block in
[docs/claude-md-snippet.md](docs/claude-md-snippet.md) into the box's
`~/.claude/CLAUDE.md`, which tells every session to hand you the command and
never call `/run` itself. If that trade-off is not acceptable for your box,
leave `LAUNCHER_RUN_USER` empty and the feature does not exist.

Every run is written to the journal (`run: <user> ran '<cmd>' in <dir>`). Input
typed at a prompt is not logged, so passwords pasted into a login stay out of
the log.

## Sign-in over the web UI

When the box's Claude login expires, the UI offers a **Sign in** button that
drives `claude auth login` in a tmux session, shows you the authorize URL and
posts your pasted code back.

This works from a phone because the OAuth flow redirects to a hosted callback at
`platform.claude.com`, not to localhost. The code you paste is single-use and
short-lived, and it is sent to the CLI over a local tmux `send-keys`, not to any
third party.

There is intentionally **no Logout button** — it is easy to fumble on a phone
and would recreate the outage the sign-in flow exists to fix.

## Reporting a vulnerability

Open a GitHub issue for anything that is not itself sensitive. For a finding you
would rather not post publicly, use GitHub's private vulnerability reporting on
this repository.

This is a personal project maintained on a best-effort basis. There is no SLA.
