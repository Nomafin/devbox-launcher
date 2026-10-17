# Troubleshooting

Failure modes seen in practice, with the signature that identifies each one.

## Any agent misbehaves — start here

```bash
agentctl ls                                   # every agent, its state and idle time
tmux -u -L agents ls                          # what is really running
tmux -u -L agents capture-pane -t '=<project>/<name>:' -p | tail -20
journalctl --user -u agents-reap -u agents-restore
```

Use `-u` on every `tmux -L agents` command. On a box whose shells run with
`LANG=C` (common on containers and minimal images), tmux does not otherwise
assume UTF-8, so an attach without it shows Claude's output garbled (agent
names are ASCII and unaffected). The `=` and the
trailing `:` make the target exact, so `notes/drafts` cannot hit
`notes/drafts2`.

`agentctl ls` marks an agent `running`, `crashed`, `failed` (its last start
failed; the reason is on the page), `broken` (worktree missing), or its
registry state (`stopped`, `parked`, `archived` with `--all`); a `*` means a
terminal is attached. `agentctl` exits non-zero with a reason, and the code
says what kind: 2 invalid, 3 unknown, 4 stale brief, 5 start failed,
6 refused, 7 exists, 8 worktree missing.

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

## Every Start fails / the box is logged out

The UI shows a red **"This box is signed out of Claude"** banner, and rows
show *Start failed* with `Not logged in` or `You must be logged in` in the
reason. The `claude` CLI lost its login, so *every* agent fails the same way.
Confirm:

```bash
claude auth status        # {"loggedIn": false, …}; exits non-zero when out
```

**Fix from the phone:** tap the banner → **Sign in**. Or from a shell on the box:
`claude auth login`. Then Start the agents again.

The refresh token has a finite life (`refreshTokenExpiresAt` in
`~/.claude/.credentials.json`) and once it lapses the CLI clears both tokens, so
this recurs on its own schedule rather than after any change you made.

## An agent is *Not connected* or blank

Capture its pane (above). If it shows a trust or permission prompt, attach and
answer it: `tmux -u -L agents attach -t '=<project>/<name>'`. If it says
`Workspace not trusted`, trust seeding did not take — check `~/.claude.json`.
A pane that shows Claude running normally but no `/remote-control is active`
line is usually the login (above) or a network blip: Stop, then Start.

## Crashed versus exited

tmux keeps a dead pane and its exit status (`remain-on-exit`), and that status
decides what the agent is:

- **Exit 0** — someone typed `/exit`. The row reads stopped (grey), and the
  next reap removes the dead pane and marks the agent stopped, so a reboot
  leaves it closed.
- **Anything else** — **Crashed (exit N)** in red, with the dead pane's last
  lines under *Show output*. The reaper leaves it alone (so the output stays
  readable) and a reboot resumes it. Tap **Resume** to bring it back now.

```bash
tmux -u -L agents list-panes -a -F '#{session_name} dead=#{pane_dead} status=#{pane_dead_status}'
```

## Start refuses with a stale brief

`has not been updated since its last activity`, exit 4. Fresh will not throw
away a conversation whose brief does not cover it. Run `/handoff` in the agent
(it rewrites the brief), then Fresh again — or *Fresh anyway* on the page,
`agentctl start <project>/<name> --fresh --force` from a shell.

If `/handoff` is unknown in a session, it was started before the command was
installed (Claude Code loads commands at start): ask it to "rewrite your brief
at `$AGENT_BRIEF` with sections Mission/State/Next steps/Gotchas" instead.

## A resumed agent comes back with the old conversation

Someone used `/clear` inside it. `/clear` starts a new conversation in the
same process, but the agent's recorded conversation is still the old one, so
Resume and restore bring that back and the brief is never consulted. Start a
new conversation with **Fresh conversation** (`agentctl start … --fresh`)
instead.

## Worktree missing

The row is red, *Worktree missing*: the agent's worktree directory was removed
outside the launcher. **Recreate worktree** (`agentctl start <project>/<name>
--repair`) rebuilds it from the agent's branch.

## Archive refuses

The row says what blocked it: uncommitted changes, commits that exist on no
remote, or a gitignored file at the worktree's top level. Fix it in the
worktree itself,

```bash
cd <launch dir>/.claude/worktrees/<name>
git status --short --ignored    # the `!!` lines at the top level count
git push -u <remote> agent/<name>
```

then archive again. There is no force path by design; from a shell,
`git worktree remove --force` does what you would expect (stop the agent
first).

## The registry looks wrong or an agent vanished

State is `~/.config/agents/registry.json`; every write keeps the previous
generation as `registry.json.bak` next to it. If the registry cannot be read,
the page shows an *Agents could not be listed* row naming the `.bak`. To undo a
bad write, copy the `.bak` back while nothing is acting on agents (no page
taps, reaper not running). Archived agents' briefs are under
`~/.config/agents/archive/`.

## After a reboot nothing is running

```bash
systemctl --user status agents-restore.service     # want: active (exited)
journalctl --user -u agents-restore
```

Restore only resumes agents that were running at shutdown; stopped, parked and
`/exit`-ed agents stay down. If nothing at all came back — the web app
included — check lingering (below) first. If restore ran but every agent
failed, it is usually the login or the network: the unit waits up to a minute
for DNS, then tries anyway, and each failed agent says why on its row.

## Agents get parked when they should not

The reaper parks agents idle for three days. Idle means no transcript writes,
no typing in tmux and no pane output, counted from the last start at the
earliest; a session with a terminal attached is never touched. See what it
did with `journalctl --user -u agents-reap`. Start wakes a parked agent with
its conversation.

## Stopped agents still appear in the Claude app

Expected — this is not a Stop failure. Remote Control keeps the session in the
app as a resume point, shown offline once the process is gone. Stop frees the
compute (the tmux process dies, RAM is freed). To remove an entry, archive it
in the app's Code tab.

## No Run command button / Run command answers 403

The button only exists when the service has `LAUNCHER_RUN_USER` set
(`systemctl --user show devbox-launcher.service -p Environment`). A 403 means
the request's `Tailscale-User-Login` header did not match it: check the exact
login Tailscale reports for your device,

```bash
tailscale whois <your-device-ip> | grep -i login
```

and that you are reaching the UI *through* `tailscale serve` — a direct
`http://127.0.0.1:8765` request carries no such header. A 403 on POST only,
with GET working, is the same-origin check: the page was opened from a
different host name than the one it posts to.

## An agent picked up the wrong permission mode

The chip shows what the *next* Start uses on a stopped row, and what the agent
was started with on a running one. The mode is on the command line:

```bash
ps -eo args | grep -o -- "--remote-control <project>/<name>.*--permission-mode [a-zA-Z]*"
```

**Default (settings)** passes no flag at all, so `defaultMode` in
`~/.claude/settings.json` wins there.

## `agentctl: command not found` over ssh

`install.sh` links `agentctl` into `~/.local/bin`, which many distributions
only add to `PATH` for login shells, so `ssh box agentctl ls` misses it. See
the README for the fix.

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

Without it neither the web app nor `agents-restore.service` (both user units)
starts at boot.

## Redeploying killed my running agents

It should not. An agents tmux server started from the page is a child of
`devbox-launcher.service`, so the default `KillMode=control-group` would take
every agent down on restart; the shipped unit sets `KillMode=process`. The
same goes for `agents-restore.service`, which also sets `RemainAfterExit=yes`.
If you wrote your own units, carry those settings over. (A *reboot* does clear
agents; restore brings back the ones that were running.)

## Legacy: the GitHub `origin` note

Projects whose `origin` points at GitHub carry an amber note and a **Rename
origin → github** button. That was a workaround for the old `claude
remote-control` listeners, whose session creation failed a GitHub
repo-access check. Agents are interactive `--remote-control` sessions and are
not affected; the button is harmless and can be ignored.

## Never hot-patch a single module

Copying one module onto the box is what caused a real outage: a `tmuxctl.py` from
a checkout predating the health work landed next to a newer `app.py`, and the
service crash-looped on
`AttributeError: module 'launcher.tmuxctl' has no attribute 'CONNECTED'`.

Always `git pull` and re-run `./install.sh`, which force-reinstalls the package,
imports it before restarting (so a broken module aborts with its real traceback
while the running UI stays up) and then waits for an HTTP 200.
