# Security model

Read this before you install. The summary is: **this service has no
authentication, and it exists to start AI agents that can modify your code.**
Its only access control is the network it is reachable on.

## What an attacker gets

Anyone who can open the UI can:

- **start a Claude Code session in any of your projects**, with your Claude
  account and your Unix user's permissions — that is read and write access to
  every repo under `LAUNCHER_BASE_DIR`, and whatever else that user can do;
- **read the last 20 lines of any listener's output** via *Show output*, which
  may contain source code, file paths or error text;
- **rename a repo's `origin` remote to `github`** (the single write the launcher
  ever performs inside a repo);
- **complete a Claude sign-in** if the box is signed out — which binds the box
  to *their* Claude account.

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
- **It runs agents unattended.** A listener sitting at *No session yet* can be
  picked up from the Claude app at any time, days later. Stop the ones you are
  not using.

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
