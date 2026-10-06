# How it works

`devbox-launcher.service` (systemd `--user`) runs a FastAPI app bound to
`127.0.0.1:8765`, fronted by Tailscale Serve.

## Code layout

`launcher/app.py` holds the routes and the state logic. `launcher/ui.py` holds
everything the page is made of — CSS, markup and the client script — inline in
the module. There is no static mount and no build step: the service is one
uvicorn app, and a phone on the tailnet should not need a second round trip to
style a page this small.

| Module | Role |
|---|---|
| `app.py` | Routes, orchestration, boot-time reconcile |
| `ui.py` | All HTML, CSS and client-side JS |
| `tmuxctl.py` | tmux driver: start/stop, batched snapshot, health scraping |
| `authctl.py` | Drives `claude auth login` in a tmux session |
| `discovery.py` | Scans the base dir for repos and groups |
| `state.py` | `desired.json` — which listeners should be running |
| `pointer.py` | Reads/clears the bridge resume pointer |
| `gitremote.py` | Detects and fixes the GitHub `origin` gotcha |
| `trust.py` | Seeds workspace trust in `~/.claude.json` |
| `hostinfo.py` | RAM/disk readout for the page header |
| `instances.py` | Instance slugs: `<project>--<name>` |
| `worktrees.py` | Every git call made for instances: create, inspect, remove |
| `runctl.py` | "Run command": a shell command in a tmux session the page can read and type into |
| `config.py` | Base-dir and run-user resolution |

## One batched status pass

Everything the page shows about every project comes from `tmuxctl.snapshot()`:
one `list-sessions` (presence *and* each session's age) plus one `list-panes -a`
(dead panes) for the whole list, then a single `capture-pane` per *running*
session whose text answers health, the connect URL and the failure reason
together. A poll therefore costs two tmux calls plus one per running listener,
rather than four per project.

## Polling cadence

4 s while something is genuinely in motion (a row you just tapped, or one still
starting), 20 s otherwise, and nothing at all while the tab is hidden.

`ready` deliberately does not count as motion: a registered listener rests there
indefinitely, and counting it meant a single idle project kept the box answering
every four seconds forever.

## Two ways to press a button

Every control is a real `<form>` posting to `/start/<slug>`, `/stop/<slug>` or
`/login/start`, so the UI works with JavaScript off. The client script upgrades
those to `fetch` calls against the `/api/*` twins (`GET /api/projects`,
`POST /api/start/<slug>`, `POST /api/stop/<slug>`), which do identical work and
return the page's state as JSON — including the **server-rendered HTML for each
row**. The client never builds a row itself, so there is no second copy of the
template to drift out of step with `ui.py`.

## Starting and stopping

**Start** seeds workspace trust in `~/.claude.json` (see SECURITY.md), then
launches a detached tmux session `devbox-<slug>` on socket `tmux -L
devbox-launcher`, running `claude remote-control --name devbox-<slug>` in the
repo dir. tmux supplies the PTY the Claude CLI needs — a plain systemd unit
cannot, because the CLI gates on an interactive trust and enable prompt.

The session is created with tmux **`remain-on-exit`**, so a listener that dies on
spawn leaves its pane (and its error) behind instead of taking the session with
it. `#{pane_dead}` is then a conclusive failure signal, and the error text can be
shown in the UI and logged. Without it, such a failure was indistinguishable from
a listener still starting up.

**Stop** kills that tmux session. Presence is `tmux has-session`, but the
displayed **health** is scraped from `capture-pane`: `Capacity: N/32` is the
honest signal, because the status word flips to `Connected` as soon as the cloud
bridge is up, seconds before any session exists. A dead pane outranks the text —
no `Capacity` line is ever coming — so Stop clears such a session immediately
rather than waiting out its Ctrl-C poll budget.

Stop sends **Ctrl-C** rather than killing outright, so the cloud bridge
disconnects cleanly and the entry flips to offline promptly. A bare
`kill-session`/SIGHUP left it looking active until a heartbeat lease timed out.

`CONNECTED` must **hold** for about 3 s to count: a doomed session occupies a
slot (`1/32`) for roughly a second before its child exits.

After Start, the launcher polls until the listener settles. A listener that fails
gets **one automatic retry** with its stale resume pointer cleared.

## Resume

**Start resumes the conversation, it does not open a fresh one.** The bridge
records the cloud session in `bridge-pointer.json`; `pointer.session_id()` reads
it and `start()` passes `--session-id <id>`, so a reboot picks up where you left
off. A project that has never run a listener has no pointer, so it registers
fresh.

Two consequences worth knowing:

- **`--session-id` needs the cloud id** (`session_…`) from the pointer, not the
  local transcript uuid in `~/.claude/projects/…/<uuid>.jsonl`. The flag resolves
  server-side; a transcript uuid fails with *"Could not reach the server to look
  up session"*, which reads like a network fault and is not.
- Resuming runs the listener in **classic single-session mode**, which prints
  `Single session · exits when complete` instead of `Capacity: N/32`, and
  **exits when the conversation ends** rather than staying up for new same-dir
  sessions. `health()` treats that line as CONNECTED; without it the launcher
  parks a perfectly healthy resumed listener at *starting…* forever.
  `--session-id` also cannot be combined with
  `--spawn`/`--capacity`/`--create-session-in-dir` — the CLI rejects the
  combination outright.

## Parallel sessions

A stopped row has a **Parallel sessions: off/on** toggle; it sets the mode the
*next* Start uses.

- **off** (default): `--spawn same-dir`, and Start resumes the recorded
  conversation, so the listener holds exactly that one conversation.
- **on**: `--spawn worktree`. Every session opened from the app against
  `devbox-<slug>` gets its own git worktree under `.claude/worktrees/`, so
  several can edit code at once. It never resumes — the CLI refuses
  `--session-id` together with `--spawn` — so Start is always a new
  conversation, and *Start fresh* is not offered.

The mode is kept per project in `desired.json` under `"parallel"`.

## Permission mode

`claude remote-control --permission-mode <mode>` is fixed when the listener
starts; there is no runtime key for it and the Claude app has no control over
it. The row's chips (**Ask**, **Accept edits**, **Auto**, **Plan**) map to
`tmuxctl.PERMISSION_MODES` (`default`, `acceptEdits`, `auto`, `plan`), and the
choice is kept per slug in `desired.json` under `"permissionMode"` so restore
replays it.

- **Ask** records nothing and passes no flag, so the CLI's own default — and a
  `defaultMode` in `~/.claude/settings.json` — applies.
- Picking a mode on a **running** row restarts the listener and resumes the
  same conversation. Unlike `--spawn`, the CLI accepts `--permission-mode`
  alongside `--session-id`, which is what lets a conversation come back in a
  different mode than it was created in. The resume pointer is kept (Retry
  clears it).
- An unknown mode is a `ValueError` before anything reaches `bash -lc`.
- `bypassPermissions` and `dontAsk` are deliberately missing: the first is
  refused outright for a cloud-reachable session, and a mis-tap on either would
  fail the listener with Start as the only way back. Adding one is a line in
  `tmuxctl.PERMISSION_MODES` and a label in `ui._MODE_LABELS`.

## Instances

An instance is an ordinary listener whose launch dir is a git worktree of the
project at `<launch dir>/.claude/worktrees/<name>`, on branch `<name>`. Its
slug is `<project>--<name>`; `--` is unambiguous because `slugify` collapses
every run of punctuation to a single `-`, so no folder can produce it.

`worktrees.py` is the only module that shells out to git. Create reuses the
branch if it exists, otherwise cuts it from the project's current `HEAD`.
Remove runs `blockers()` first and refuses — leaving the listener running — on
uncommitted changes, commits on no remote, a gitignored file at the worktree
root (a directory such as `node_modules/` does not block), an unborn `HEAD`
being treated as safe, and **fails closed** when any of those checks errors. The
branch is never deleted.

Instances are recorded in `desired.json` under `"instances"`, so
`reconcile()` relaunches them at boot and forgets an instance whose project has
disappeared. A worktree the Claude app created for one of its own parallel
sessions is not an instance and is never listed.

## Run command

`runctl.py` runs one shell command per project in tmux session
`launcher-run-<slug>` on the launcher's socket, in the project's launch dir,
via `bash -lc`. The page polls `/api/run/<slug>` for the pane text and exit
code (tmux `remain-on-exit` keeps the dead pane and its output), posts typed
input with `send-keys -l` followed by Enter, and sends Ctrl-C for interrupt —
the same mechanics as the sign-in flow. The last command per project is kept in
`~/.local/state/devbox-launcher/run-<slug>.json` so the row can show *Command
finished · exit N* after a reload; **Done** clears it. Every run is logged to
the journal as `run: <user> ran '<cmd>' in <dir> (<slug>)`; typed input is not.

Access: the feature exists only when `LAUNCHER_RUN_USER` is set, and every
`/run/*` and `/api/run/*` request must carry a matching `Tailscale-User-Login`
header (which Tailscale Serve adds to each request it proxies) and, for POSTs,
be same-origin (`Sec-Fetch-Site`, or an `Origin` matching the host). Neither
stops a process on the box itself — see SECURITY.md.

Without JavaScript or `<dialog>`, the button is a plain link to `/run/<slug>`,
the same panel as a page.

## Restore after reboot

tmux sessions do not survive a reboot, but the launcher restores them.
Start/Stop record intent in `~/.local/state/devbox-launcher/desired.json`, and
one reconcile pass at startup relaunches whatever was running.

Restore is **boot-only** — a listener that dies mid-day stays down and shows red
until you tap Start.

This is also why lingering matters: if the web app is down after a reboot the
listeners cannot be restored either, because restore runs inside it.

## Signing in from the UI

The box's Claude login expires on its own schedule, and when it does every
listener fails identically — so the launcher can restore it without a shell.

A red banner appears on `/` whenever `claude auth status` reports
`loggedIn: false`. **Sign in** runs `claude auth login` in a tmux session named
`launcher-login` (deliberately not `devbox-*`, so no project slug can collide
with it), scrapes the authorize URL off the pane and shows it as a link, then
posts your pasted code back with `send-keys -l`. On success it kills the login
session and runs the same `reconcile()` pass as boot, so the listeners in
`desired.json` come back without further tapping.

This works from a phone only because the OAuth flow redirects to a **hosted**
callback at `platform.claude.com/oauth/code/callback`, not to `localhost` —
nothing has to route back to the box.

The CLI rejects a bad code two different ways, and the UI treats them
differently because they need different things:

| pane says | process | UI |
|---|---|---|
| `Invalid code. Please make sure the full code was copied.` | stays at the prompt | shows the error, keeps the same link and paste box |
| `Login failed: …` | exits (pane dead) | offers **Start again** for a fresh link |

Rejections stay on the pane forever, so `/login/code` counts them before
submitting and only treats a *new* one as this attempt's failure — otherwise a
good second paste reads as a failure.
