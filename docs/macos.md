# Running on a Mac

`install.sh` detects macOS and installs the same three things as on Linux, as
launchd agents in `~/Library/LaunchAgents`:

| Job | What it does | When it runs |
|---|---|---|
| `devbox.launcher` | The web app | At login; restarted after a crash |
| `devbox.agents-reap` | Parks idle agents, deletes idle scratch sessions | Every 30 minutes |
| `devbox.agents-restore` | Starts the agents that were running | At login, not during an install |

Prerequisites, from Homebrew: `brew install python tmux`. Install Claude Code
with its native installer, sign in once in Terminal, and run `./install.sh`.

The jobs run in your desktop session, because that is where Claude Code's
Keychain login can be read. **So the Mac must be logged in for anything to
run**, and the steps below are what keeps it that way on a headless machine.

## A Mac that stays up

Prevent sleep, and start again after a power cut:

```bash
sudo pmset -a sleep 0 disksleep 0 autorestart 1
```

Turn on automatic login in System Settings → Users & Groups, so a reboot comes
back to a logged-in session and an unlocked login Keychain.

**FileVault blocks automatic login.** With FileVault on, a restart stops at a
password prompt, and nothing here runs until someone answers it. You have to
choose:

- **FileVault off, automatic login on:** comes back by itself after a power cut,
  but the disk is not encrypted if the Mac is stolen.
- **FileVault on:** the disk stays encrypted, but an unplanned restart needs you
  at the machine. For a planned one, `sudo fdesetup authrestart` skips the prompt
  once.

## Reaching it over ssh

Commands run over ssh do not read the login files that add Homebrew to `PATH`.
To attach to an agent's tmux session or run `agentctl` over ssh, add to
`~/.zshenv`:

```bash
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
```

`install.sh` warns if this is missing. Turn on Remote Login in System Settings →
General → Sharing, or use Tailscale SSH where your Tailscale install supports it.

## Looking after it

```bash
launchctl print "gui/$(id -u)/devbox.launcher"        # state, pid, last exit
launchctl kickstart -k "gui/$(id -u)/devbox.launcher" # restart; agents keep running
tail -f ~/Library/Logs/devbox-launcher/launcher.log
```

Logs are in `~/Library/Logs/devbox-launcher/`. Nothing rotates them. launchd has
no start limit like systemd's: a launcher that crashes at start retries every
few seconds, and `launcher.log` is where that shows.

## Uninstalling

```bash
launchctl bootout "gui/$(id -u)/devbox.launcher"
launchctl bootout "gui/$(id -u)/devbox.agents-reap"
launchctl bootout "gui/$(id -u)/devbox.agents-restore" 2>/dev/null
rm ~/Library/LaunchAgents/devbox.*.plist
```

Running agents are tmux sessions and keep running until you stop them
(`tmux -L agents kill-server`). The venv, the registry in `~/.config/agents`, and
`~/.claude/commands/handoff.md` stay until you remove them.
