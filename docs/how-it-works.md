# How it works

`devbox-launcher.service` (systemd `--user`) runs a FastAPI app bound to
`127.0.0.1:8765`, fronted by Tailscale Serve. It is one of two front ends to
the same **agent registry**; the other is the `agentctl` command line, which is
also what the reaper timer and the restore-at-boot unit run.

## Code layout

`launcher/app.py` holds the routes and the page state. `launcher/ui.py` holds
everything the page is made of — CSS, markup and the client script — inline in
the module. There is no static mount and no build step: the service is one
uvicorn app, and a phone on the tailnet should not need a second round trip to
style a page this small. `launcher/agents/` is the whole agent model; the web
app imports `agents.service` directly, and `agentctl` is the same functions
behind a command line.

| Module | Role |
|---|---|
| `app.py` | Routes and page state |
| `ui.py` | All HTML, CSS and client-side JS |
| `agents/model.py` | The `Agent` record, states, names, permission modes, `AgentError` codes |
| `agents/registry.py` | `~/.config/agents/registry.json`, written only under an exclusive `flock` |
| `agents/service.py` | Every operation: new, start, stop, park, archive, mode, scratch, reap, restore |
| `agents/runtime.py` | tmux on the `agents` socket, and the `claude` command line |
| `agents/briefs.py` | Briefs, the seed for a fresh conversation, staleness |
| `agents/places.py` | Which project a directory or slug belongs to |
| `agents/cli.py` | `agentctl` |
| `agents/importer.py` | One-time migration of the old launcher instances |
| `tmuxctl.py` | The launcher's own tmux socket (sign-in and Run command only) |
| `authctl.py` | Drives `claude auth login` in a tmux session |
| `discovery.py` | Scans the base dir for repos and groups |
| `gitremote.py` | The legacy GitHub-`origin` note and its rename button |
| `trust.py` | Seeds workspace trust in `~/.claude.json` |
| `hostinfo.py` | RAM/disk readout for the page header |
| `worktrees.py` | Every git worktree call: create, inspect, remove |
| `runctl.py` | "Run command": a shell command in a tmux session the page can read and type into |
| `config.py` | Base-dir and run-user resolution |

## Agents

An agent is one long-lived interactive Claude conversation with a name. Its
key is `<project>/<name>`, and its display name — the tmux session and the
name in the Claude app — is `<project>/<name>`.

- **main** works in the project's own checkout (a group's `claude-project/`).
  Every project has one, created the first time it is used. It can be stopped
  and parked, never archived.
- **A named agent** works in its own worktree,
  `<launch dir>/.claude/worktrees/<name>`, on branch `agent/<name>` (reused if
  it exists, otherwise cut from the project's current checkout), so several
  agents can edit the same repo at once.
- **A scratch session** (*Quick chat*, `agentctl scratch`) is a one-off in the
  project's checkout: no brief, branch or worktree, deleted when stopped and
  by the reaper after 12 idle hours.

State lives in `~/.config/agents/` (`AGENTS_HOME` overrides it):
`registry.json` (each write keeps the previous generation as
`registry.json.bak`), `briefs/<project>/<name>.md`, and `archive/` for the
briefs of archived agents. Every operation is one registry transaction under
an exclusive `flock`, so the page, `agentctl` and the timers can act on the
same agent at once without losing a write. A start holds the lock while the
session settles, so a second start waits and then finds it running.

## Starting and stopping

**Start** seeds workspace trust in `~/.claude.json` (see SECURITY.md), then
creates a detached tmux session `<project>/<name>` on socket
`tmux -u -L agents` (config `~/.config/agents/tmux.conf`), in the agent's
workdir, running

```
claude --remote-control <project>/<name> -n <project>/<name> \
       --resume <id> | --session-id <new id> --append-system-prompt-file <seed>
       [--permission-mode <mode>]
```

with `AGENT_NAME` and `AGENT_BRIEF` in its environment. It is an ordinary
interactive session, so the same conversation is in the Claude app, on the
page and in a terminal attached to the tmux session. tmux supplies the PTY the
CLI needs.

The session is created with tmux **`remain-on-exit`**, so a process that dies
on start leaves its pane (and its error) behind. Start polls for about five
seconds: a pane that dies in that window is a failed start, and Claude's own
error goes on the row. If the folder-trust prompt shows anyway, it is answered
once. Health afterwards is scraped from the visible screen: the line
`/remote-control is active` means the app can reach it (*Live*); a running
session without it is *Starting…* for 30 s, then *Not connected*.

**Stop** kills the session and keeps the conversation id, so the next Start
resumes it with `--resume`. A conversation that can no longer be resumed fails
the start with a hint to start fresh.

`-u` matters on every `tmux -L agents` call: on a box running `LANG=C`, tmux
does not otherwise assume UTF-8 and Claude's pane output comes back
mangled. Targets are always exact (`=<name>:`), so `notes/drafts` can never
hit `notes/drafts2`.

## Briefs, /handoff and Fresh

Each agent has a brief with four sections: Mission, State, Next steps,
Gotchas. The page shows its Mission line under the row. **Fresh
conversation** starts a new conversation seeded from the brief: the brief is
appended to the system prompt together with a preamble telling the agent to
keep it current and to rewrite it on `/handoff`. `/handoff` is a Claude Code
command (`~/.claude/commands/handoff.md`, installed by `install.sh`) that makes
the running agent rewrite its own brief.

Fresh refuses (exit 4) when the brief is **stale** — not updated since the
agent's last activity, with a two-minute grace for the reply to `/handoff`
itself. The row then offers **Fresh anyway** for one render; the next refresh
puts back plain Fresh, which checks again. Activity is the newest of the
conversation's transcript, typing in tmux and pane output after the first
minute of a start (a resumed conversation redraws its history, which is not
use).

A new conversation is always Fresh, not `/clear` inside the agent: after
`/clear` the agent's recorded conversation is still the old one, so a later
Resume or restore brings that back and the brief is never consulted.

## Parking, the reaper and restore

**Park** stops an agent and hides it behind the page's **Parked (N)** button;
Start wakes it with its conversation. `agents-reap.timer` runs
`agentctl reap` ten minutes after boot and every 30 minutes: it parks agents
idle for three days (counted from the last start at the earliest, so a woken
agent gets three more days) and deletes scratch sessions idle for 12 hours. It
never touches a session with a client attached.

tmux does not survive a reboot. `agents-restore.service` runs
`agentctl restore` at boot and resumes every agent that was running when the
box went down; stopped and parked agents stay down. It is enabled by
`install.sh` but never started by it — starting it would start every agent
marked running. The unit is `Type=oneshot` with `RemainAfterExit=yes` and
`KillMode=process`, because the agents' tmux server it starts lives in its
cgroup and the defaults would kill every restored agent the moment restore
returned. It waits up to a minute for DNS before starting anything.

An agent whose Claude exits mid-day is told apart by the exit status tmux
keeps for the dead pane: **status 0** (`/exit`) reads as stopped and the next
reap marks it so, so a reboot leaves it closed; **any other status** is
**Crashed (exit N)** in red, with the dead pane's output under *Show output*.
The reaper leaves a crashed agent alone, and restore resumes it.

## Archive

**Archive** (named agents only, not `main`, not scratch) stops the agent,
removes its worktree and moves its brief to `~/.config/agents/archive/`. The
branch is left behind. Before removing anything `worktrees.blockers()` refuses
— and says why — on uncommitted changes, commits on no remote, or a gitignored
*file* at the worktree root (a `.env`, a scratch note; `git status` is silent
about those but `git worktree remove` deletes them). A gitignored directory
such as `node_modules/` does not block, an unborn `HEAD` is treated as safe,
and any check that errors **fails closed**. There is no force: commit and push,
then archive.

## agentctl

`agentctl` exits with the `AgentError` code of whatever went wrong, so a
script (or a terminal wrapper) can act on the reason: 2 invalid, 3 unknown,
4 stale brief, 5 start failed, 6 refused, 7 exists, 8 worktree missing.
`--json` prints one JSON object either way. Targets are `<project>/<name>`,
or `--dir <path>` with just a name, where the directory may be anywhere inside
the project (a worktree counts as its project; a git repo nested inside a
project is a project of its own). Typing the project's own name means `main`.

## One batched status pass

Each page poll lists the agents socket once (`list-sessions` for presence,
age and activity, `list-panes -a` for dead panes and their exit status) and
then captures the screen of each *running* agent, whose text answers health
and the connect URL together. A tmux server that does not answer, or a
registry that cannot be read, is never treated as "nothing running": the page
renders the projects with one row saying why (for the registry, where its
`.bak` is).

## Polling cadence

4 s while something is genuinely in motion (a row you just tapped, or one still
starting), 20 s otherwise, and nothing at all while the tab is hidden or a
*New agent* form is open. *Not connected* deliberately does not count as
motion.

## Two ways to press a button

Every control is a real `<form>` posting to
`/agents/<project>/<name>/<action>` (or `/projects/<slug>/new|scratch`,
`/login/start`), so the UI works with JavaScript off. The client script
upgrades those to `fetch` calls against the `/api/*` twins
(`GET /api/projects`, `POST /api/agents/<project>/<name>/<action>`), which do
identical work and return the page's state as JSON — including the
**server-rendered HTML for each row**. The client never builds a row itself,
so there is no second copy of the template to drift out of step with `ui.py`.

## Permission mode

`claude --permission-mode <mode>` is fixed when the process starts. The row's
chips (**Default (settings)**, **Accept edits**, **Auto**, **Plan**) map to
`agents.model.PERMISSION_MODES` (`default`, `acceptEdits`, `auto`, `plan`),
recorded per agent in the registry, so restore and the next Start use it.

- **Default (settings)** records nothing and passes no flag, so a
  `defaultMode` in `~/.claude/settings.json`, or else the CLI's own default,
  applies.
- Picking a mode on a **running** agent restarts it and resumes the same
  conversation.
- An unknown mode is refused before anything reaches `bash -lc`.
- `bypassPermissions` and `dontAsk` are deliberately missing: the first is
  refused outright for a cloud-reachable session, and a mis-tap on either would
  be hard to undo from a phone. Adding one is a line in
  `agents.model.PERMISSION_MODES` and a label in `ui._MODE_LABELS`.

## Run command

`runctl.py` runs one shell command per project in tmux session
`launcher-run-<slug>` on the launcher's own socket (`tmux -L
devbox-launcher`), in the project's launch dir, via `bash -lc`. The page polls
`/api/run/<slug>` for the pane text and exit code (tmux `remain-on-exit` keeps
the dead pane and its output), posts typed input with `send-keys -l` followed
by Enter, and sends Ctrl-C for interrupt — the same mechanics as the sign-in
flow. The last command per project is kept in
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

## Signing in from the UI

The box's Claude login expires on its own schedule, and when it does every
agent fails identically — so the launcher can restore it without a shell.

A red banner appears on `/` whenever `claude auth status` reports
`loggedIn: false`. **Sign in** runs `claude auth login` in a tmux session named
`launcher-login` on the launcher's socket, scrapes the authorize URL off the
pane and shows it as a link, then posts your pasted code back with
`send-keys -l`. On success it kills the login session; agents that failed for
want of a login are started again with **Start**.

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

## Migrating from listeners and instances

Earlier versions ran `claude remote-control` listeners on `tmux -L
devbox-launcher`, with *instances* recorded in
`~/.local/state/devbox-launcher/desired.json`. `agentctl import [--dry-run]`
registers each recorded instance whose worktree exists as a named agent (with
a brief, state *stopped*); it stops nothing. Old listeners keep running until
you stop them: `tmux -L devbox-launcher kill-session -t '=devbox-<slug>'`.
