# devbox-launcher

A small web UI for starting and stopping [Claude Code](https://claude.com/claude-code)
`remote-control` listeners on a headless development box — one per git project —
so you can open a session from your phone without SSHing in.

The launcher only starts, stops and monitors listeners. You drive the actual
session in the Claude mobile app or at claude.ai/code.

```
┌─ devbox sessions ─────────────── RAM 1.2 of 12 GB · Disk 24.2 of 29.4 GB ─┐
│ ▍ acme         Live · 2 sessions · 3h   [Stop]  [Open in the Claude app]  │
│ ▍ blog         No session yet · 9d      [Stop]                            │
│ ▍ demo         Start failed                                               │
│     Error: You must be logged in to use Remote Control.                   │
│                                         [Retry]                           │
│ ▍ webapp                                [Start]  Parallel sessions: off   │
└───────────────────────────────────────────────────────────────────────────┘
```

> **Read [SECURITY.md](SECURITY.md) before installing.** This service starts AI
> agent sessions that can read and write your code, and it has **no
> authentication of its own**. It is designed to sit behind Tailscale.

## Requirements

- Linux with **systemd** (user services + lingering). Developed on Ubuntu 24.04
  in an LXC container.
- **Python 3.12+** with `venv`
- **tmux** — used as the process supervisor for listeners
- **git**
- **Claude Code CLI** on `PATH`, signed in to a claude.ai subscription that
  includes Remote Control
- **Tailscale** — the intended and only recommended way to reach the UI

## Install

```bash
git clone https://github.com/<you>/devbox-launcher.git
cd devbox-launcher
./install.sh
```

Run it as the user whose sessions you want to manage — not as root. It creates a
venv, installs the package, writes a systemd `--user` unit, enables lingering
(the one step that needs `sudo`), starts the service and waits for it to answer.

Then publish it to your tailnet:

```bash
tailscale serve --bg 8765
```

The UI is now at `https://<host>.<your-tailnet>.ts.net`. This needs tailnet
HTTPS certificates enabled once, in the Tailscale admin console under DNS.

Re-run `./install.sh` to update after a `git pull`; it force-reinstalls and
restarts. Running listeners survive the restart (the unit sets
`KillMode=process`).

## Configuration

All optional, read from the environment by the systemd unit:

| Variable | Default | Meaning |
|---|---|---|
| `LAUNCHER_BASE_DIR` | `~/projects` | Directory scanned for projects |
| `LAUNCHER_HOST` | `127.0.0.1` | Bind address — see SECURITY.md before changing |
| `LAUNCHER_PORT` | `8765` | Bind port |
| `LAUNCHER_VENV` | `~/.local/share/devbox-launcher/venv` | Install location (install-time only) |

## How projects are discovered

Everything under `LAUNCHER_BASE_DIR` that is either:

- a **git repo** — a directory containing `.git`; or
- a **group** — a directory containing a `claude-project/` subdirectory
  alongside several code repos. Claude launches in `claude-project/`, which acts
  as the config and history anchor, and reaches the sibling repos normally.

A directory that is both is treated as a repo.

## Using it

Tap **Start** and a listener comes up as `devbox-<slug>`; pick that name in the
Claude app. The coloured rail on each row is a health signal, not just
"process alive":

| Rail | Means |
|---|---|
| green — *Live* | a session is live and reachable from the app |
| amber — *No session yet* / *Starting…* | registered, but no session yet |
| red — *Start failed* | session creation failed; the CLI's error is shown |
| red — *Stuck* | up but silent for 30 s — wedged rather than slow |

Start takes about 7 seconds because it waits for the listener to settle, so the
row tells the truth immediately rather than flipping states under you.

Other controls: **Retry** (stop, clear the resume pointer, start again),
**Start fresh** (discard the recorded conversation), **Parallel sessions**
(`--spawn worktree`, so each session gets its own git worktree and several can
edit at once), and **Show output** (last 20 lines of the pane, fetched on
demand).

Listeners cost roughly 165 MB each and are started on demand. tmux sessions do
not survive a reboot, but the launcher records intent in
`~/.local/state/devbox-launcher/desired.json` and replays it at startup.

**The `origin` gotcha.** If a repo has an `origin` remote pointing at GitHub,
session creation can fail a repo-access check. The launcher detects this and
offers a one-tap **Rename origin → github** button. See
[docs/troubleshooting.md](docs/troubleshooting.md) for the proper fix
(granting the Claude GitHub App access to the repo).

## Documentation

- [SECURITY.md](SECURITY.md) — threat model; read it first
- [docs/how-it-works.md](docs/how-it-works.md) — architecture and design notes
- [docs/troubleshooting.md](docs/troubleshooting.md) — failure modes seen in practice

## Known limitations

- **It parses terminal output.** Health is scraped from `tmux capture-pane`,
  including the literal `Capacity: N/32` line that `claude remote-control`
  prints. This is coupled to Claude Code's TUI and **will break** when that
  output changes. There is no stable API for this; the tests pin the strings
  currently relied on.
- **It has been run in exactly one environment** — an Ubuntu 24.04 LXC on
  Proxmox. The install script should be portable to any systemd Linux, but
  yours may well be the second machine it has ever touched. Bug reports
  welcome.
- **Linux only.** `hostinfo.py` reads `/proc/meminfo` (and is lxcfs-aware so it
  reports a container's limit rather than the host's).
- **No multi-user support.** One launcher, one Unix user, one Claude account.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e . pytest httpx
.venv/bin/python -m pytest
```

262 tests, no network or tmux required — the tmux and CLI boundaries are driven
through injected runners.

## License

MIT — see [LICENSE](LICENSE).
