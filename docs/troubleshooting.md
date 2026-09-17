# Troubleshooting

Failure modes seen in practice, with the signature that identifies each one.

## Service down

```bash
systemctl --user status devbox-launcher.service
journalctl --user -u devbox-launcher.service -n 50
```

## The service gave up restarting

`systemctl --user status` says `failed`, "start request repeated too quickly".

That is the `StartLimitBurst=5` guard doing its job — the app is dying at
startup. Read the traceback:

```bash
journalctl _SYSTEMD_USER_UNIT=devbox-launcher.service -n 40
```

Fix the cause, then `systemctl --user reset-failed devbox-launcher.service` and
re-run `./install.sh`.

Without this guard a bad deploy just loops — roughly 700 restarts in an hour —
while the unit still reads `active (running)` between crashes.

## Every Start does nothing / the box is logged out

The UI shows a red **"This box is signed out of Claude"** banner, and listeners
show `session failed — … · Error: You must be logged in to use Remote Control.`

The `claude` CLI lost its login, so `claude remote-control` exits immediately and
*every* project fails the same way. Confirm:

```bash
claude auth status        # {"loggedIn": false, …}; exits non-zero when out
```

**Fix from the phone:** tap the banner → **Sign in**. Or from a shell on the box:
`claude auth login`.

The refresh token has a finite life (`refreshTokenExpiresAt` in
`~/.claude/.credentials.json`) and once it lapses the CLI clears both tokens, so
this recurs on its own schedule rather than after any change you made. Restore
replays `desired.json` at the next boot, so listeners come back once the login is
good.

## A listener won't start / stays blank

Inspect it directly:

```bash
tmux -L devbox-launcher ls
tmux -L devbox-launcher capture-pane -t devbox-<slug> -p | tail -20
```

- `Enable Remote Control? (y/n)` — the enable-prompt suppression failed.
- `Workspace not trusted` — trust seeding did not take; check `~/.claude.json`.

## Green in the launcher but no session in the Claude app

The listener shows `Ready · Capacity: 0/32` and never `Connected`/`1/32`.
remote-control registered the *environment*, but session creation is failing a
GitHub repo-access check. Confirm with a debug run:

```bash
tmux -L dbg new-session -d -s t -c <launch-dir> \
  bash -lc 'claude remote-control --name dbg --spawn session --debug-file /tmp/rc.log'
sleep 12; grep -iE 'git_repo_url|Session creation|access check' /tmp/rc.log
tmux -L dbg kill-server
```

If you see `Session creation failed with status 400: GitHub repository access
check failed`, the account's Claude GitHub App does not grant access to that
repo. This fails **on every machine**, laptop included — it is not a launcher
bug. Two fixes:

- **Bypass** (what the launcher's one-tap button does):
  `git -C <launch-dir> remote rename origin github`. `git_repo_url` becomes
  `null`, the check is skipped, and the session creates. Trade-off: git uses
  `github` instead of `origin` from then on.
- **Proper:** grant the Claude GitHub App access to the repos at
  <https://github.com/settings/installations> (plus the org's installations for
  org repos). Then GitHub-backed remotes work and you can rename back.

Repos with **no remote** — a fresh `git init` — skip the check entirely.

## Start fails with an archived environment

The listener sits at `Ready · Capacity: 0/32` with a
`Session failed: Process exited with error cse_…` line, and the project is
missing from the Claude app.

The retained resume pointer at
`~/.claude/projects/<encoded-cwd>/bridge-pointer.json` names an environment the
server has since **archived**. On start the bridge adopts and re-queues that dead
session, the server answers `end_session reason=archived`, and the child exits.
Log signature:

```
[bridge:init] Found prior environment env_… in pointer; requesting reuse
[bridge:init] reconnectSession(session_…) failed: … 400: Session not found.
[bridge:init] Adopted session cse_… re-queued via bridge/reconnect
[bridge:ws]   <<< {"reason":"archived","subtype":"end_session"}
[ERROR] Bridge session failed: Process exited with error
```

The launcher **self-heals** this: it detects the failure, deletes the pointer and
starts once more, which registers a fresh environment. Healthy Stop→Start still
reuses the pointer, so resume is unaffected.

By hand: delete that `bridge-pointer.json` and press Start. Archiving an
environment in the app's Code tab is what strands the pointer.

## Stopped projects still appear in the Claude app

Expected — this is not a Stop failure. `claude remote-control` **retains one
environment per project directory** as a resume point, and explicitly skips
"archive+deregister to allow resume" even on a clean shutdown. There is no CLI
flag to disable retention.

Stop does free the compute — the tmux process dies and RAM is freed — and sends
Ctrl-C so the entry flips to **offline** promptly. To actually remove an entry,
do it in the Claude app's Code tab.

## URL not reachable

```bash
tailscale serve status
tailscale serve --bg 8765     # re-publish
```

Also check that tailnet HTTPS certificates are enabled in the Tailscale admin
console under DNS, and that the client device has MagicDNS on.

## Web app died after a reboot

Confirm lingering is enabled:

```bash
loginctl show-user "$USER" | grep Linger     # want: Linger=yes
sudo loginctl enable-linger "$USER"
```

If the web app is down after a reboot the listeners cannot be restored either —
restore runs inside it. Fix lingering first, then re-check the UI.

## Redeploying killed my running listeners

It should not. The tmux server is a child of `devbox-launcher.service`, so the
default `KillMode=control-group` would take every listener down on restart; the
shipped unit sets `KillMode=process`.

If you wrote your own unit, carry that setting over. (A *reboot* does clear
listeners, but they are restored automatically.)

## Never hot-patch a single module

Copying one module onto the box is what caused a real outage: a `tmuxctl.py` from
a checkout predating the health work landed next to a newer `app.py`, and the
service crash-looped on
`AttributeError: module 'launcher.tmuxctl' has no attribute 'CONNECTED'`.

Always `git pull` and re-run `./install.sh`, which force-reinstalls the package,
imports it before restarting (so a broken module aborts with its real traceback
while the running UI stays up) and then waits for an HTTP 200.
