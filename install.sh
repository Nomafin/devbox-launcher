#!/usr/bin/env bash
# Install (or update) the devbox launcher as a systemd --user service, along
# with `agentctl` and the agents' reaper timer and restore-at-boot unit. On a
# Mac the same three are launchd agents instead; see docs/macos.md.
#
# Run this as the user whose Claude sessions the launcher will manage — it
# installs into that user's home and runs under their systemd instance. Only
# `loginctl enable-linger` needs sudo.
#
# Re-running is the supported way to update: the venv is reused, the package is
# force-reinstalled, and the service is restarted.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV="${LAUNCHER_VENV:-$HOME/.local/share/devbox-launcher/venv}"
BASE_DIR="${LAUNCHER_BASE_DIR:-$HOME/projects}"
HOST="${LAUNCHER_HOST:-127.0.0.1}"
PORT="${LAUNCHER_PORT:-8765}"
# Tailnet login allowed to use "Run command" (a shell on the box). Empty = off.
RUN_USER="${LAUNCHER_RUN_USER:-}"
# The Clone and New project pages, through the `gh` login. Empty = off.
GITHUB="${LAUNCHER_GITHUB:-}"
# Whose repo invitations the Clone page accepts. Empty = none.
GITHUB_INVITES_FROM="${LAUNCHER_GITHUB_INVITES_FROM:-}"
# The Schedules page and the job that runs them (macOS for now). Empty = off.
SCHEDULES="${LAUNCHER_SCHEDULES:-}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="devbox-launcher.service"
BIN_DIR="$HOME/.local/bin"
AGENTS_CONF_DIR="$HOME/.config/agents"
COMMANDS_DIR="$HOME/.claude/commands"

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }

[ "$(id -u)" -ne 0 ] || die "run this as your own user, not root — the launcher runs as you and starts agent sessions as you"

# --- prerequisites ----------------------------------------------------------
note "checking prerequisites"
for cmd in python3 tmux git; do
    command -v "$cmd" >/dev/null || die "$cmd is required but not on PATH"
done

python3 - <<'PY' || die "Python 3.12 or newer is required"
import sys
sys.exit(0 if sys.version_info >= (3, 12) else 1)
PY

python3 -c 'import venv' 2>/dev/null || die "the python venv module is missing (on Debian/Ubuntu: apt install python3-venv)"

if command -v claude >/dev/null; then
    CLAUDE_BIN_DIR="$(dirname "$(command -v claude)")"
else
    # Not fatal: you may be installing before Claude Code, and the unit's PATH
    # is fixed up by re-running this script. But the launcher cannot start a
    # single session until `claude` exists, so say so loudly.
    CLAUDE_BIN_DIR="$HOME/.local/bin"
    warn "the 'claude' CLI is not on PATH; assuming $CLAUDE_BIN_DIR — install Claude Code and re-run this script"
fi

# A terminal app can put a shim of its own first on PATH, from a temporary
# directory that will be gone when launchd next starts the job.
case "$CLAUDE_BIN_DIR" in
    /var/folders/*|/private/var/folders/*|/tmp/*|/private/tmp/*)
        if [ -x "$HOME/.local/bin/claude" ]; then
            CLAUDE_BIN_DIR="$HOME/.local/bin"
        else
            warn "the 'claude' on PATH is in a temporary directory ($CLAUDE_BIN_DIR); install Claude Code permanently and re-run this script"
        fi
        ;;
esac

command -v tailscale >/dev/null || warn "tailscale is not installed; see SECURITY.md before exposing this service any other way"

# --- install ----------------------------------------------------------------
note "creating the project base dir ($BASE_DIR)"
mkdir -p "$BASE_DIR"

# For agentctl in a terminal, where the units' environment is not set.
mkdir -p "$HOME/.config/devbox-launcher"
printf '%s\n' "$BASE_DIR" > "$HOME/.config/devbox-launcher/base-dir"

note "creating the venv ($VENV)"
mkdir -p "$(dirname "$VENV")"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"

note "installing the launcher package"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet --force-reinstall "$REPO_DIR"

# Catches a partial or hot-patched install before it becomes a restart loop.
# Fails with the real ImportError, which is what you want to read.
note "verifying the package imports"
"$VENV/bin/python" -c 'import launcher.app' || die "the installed package does not import cleanly"

# --- agents -----------------------------------------------------------------
# agentctl is the command line to the same agent registry the web UI uses; the
# reaper and restore units run it too (from the venv, not via PATH).
note "linking agentctl into $BIN_DIR"
mkdir -p "$BIN_DIR"
ln -sfn "$VENV/bin/agentctl" "$BIN_DIR/agentctl"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH; add it (see the README for non-interactive ssh)" ;;
esac

note "installing the agents tmux config ($AGENTS_CONF_DIR/tmux.conf)"
mkdir -p "$AGENTS_CONF_DIR"
install -m 0644 "$REPO_DIR/agents/tmux.conf" "$AGENTS_CONF_DIR/tmux.conf"

# /handoff makes a running agent rewrite its own brief before a fresh start.
note "installing the /handoff command ($COMMANDS_DIR/handoff.md)"
mkdir -p "$COMMANDS_DIR"
if [ -f "$COMMANDS_DIR/handoff.md" ] && ! cmp -s "$REPO_DIR/agents/handoff.md" "$COMMANDS_DIR/handoff.md"; then
    cp "$COMMANDS_DIR/handoff.md" "$COMMANDS_DIR/handoff.md.bak"
    warn "replaced an existing $COMMANDS_DIR/handoff.md (previous copy kept as handoff.md.bak)"
fi
install -m 0644 "$REPO_DIR/agents/handoff.md" "$COMMANDS_DIR/handoff.md"

# --- units -------------------------------------------------------------------
render() {
    sed -e "s|@VENV@|$VENV|g" \
        -e "s|@BASE_DIR@|$BASE_DIR|g" \
        -e "s|@HOST@|$HOST|g" \
        -e "s|@PORT@|$PORT|g" \
        -e "s|@RUN_USER@|$RUN_USER|g" \
        -e "s|@CLAUDE_BIN_DIR@|$CLAUDE_BIN_DIR|g" \
        -e "s|@LOG_DIR@|${LOG_DIR:-}|g" \
        -e "s|@GITHUB@|$GITHUB|g" \
        -e "s|@SCHEDULES@|$SCHEDULES|g" \
        -e "s|@GITHUB_INVITES_FROM@|$GITHUB_INVITES_FROM|g" \
        "$1" > "$2"
}

install_systemd() {
    mkdir -p "$UNIT_DIR"
    note "writing $UNIT_DIR/$UNIT"
    render "$REPO_DIR/devbox-launcher.service.in" "$UNIT_DIR/$UNIT"
    note "writing the agents reaper and restore units"
    render "$REPO_DIR/agents-reap.service.in" "$UNIT_DIR/agents-reap.service"
    install -m 0644 "$REPO_DIR/agents-reap.timer" "$UNIT_DIR/agents-reap.timer"
    render "$REPO_DIR/agents-restore.service.in" "$UNIT_DIR/agents-restore.service"

    # Without lingering, the service dies at logout and never comes back after a
    # reboot — which is exactly when you most want it (you are away from the box).
    if [ ! -e "/var/lib/systemd/linger/$USER" ]; then
        note "enabling lingering so the service survives logout and reboot (needs sudo)"
        sudo loginctl enable-linger "$USER" \
            || warn "could not enable lingering; run: sudo loginctl enable-linger $USER"
    fi

    note "starting the service"
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT"
    systemctl --user restart "$UNIT"

    # The reaper parks idle agents (3 days) and deletes idle scratch sessions
    # (12 hours). Restore only runs at boot: starting it now would start every
    # agent marked active, which an install has no business doing.
    note "enabling the agents reaper timer and restore-at-boot unit"
    systemctl --user enable --now agents-reap.timer
    systemctl --user enable agents-restore.service
}

# Reloading a job that was just unloaded can fail while launchd finishes
# removing it, so give it a moment.
launchd_load() {
    local target="$LAUNCHD_DOMAIN/$1" attempt
    launchctl bootout "$target" 2>/dev/null || true
    launchctl enable "$target"
    for attempt in 1 2 3 4 5; do
        launchctl bootstrap "$LAUNCHD_DOMAIN" "$AGENT_DIR/$1.plist" 2>/dev/null && return 0
        sleep 1
    done
    die "launchd would not load $1; try: launchctl bootstrap $LAUNCHD_DOMAIN $AGENT_DIR/$1.plist"
}

# LaunchAgents in the gui domain, not daemons: they run in the logged-in
# session, which is where Claude Code's Keychain login can be read.
install_launchd() {
    AGENT_DIR="$HOME/Library/LaunchAgents"
    LAUNCHD_DOMAIN="gui/$(id -u)"
    launchctl print "$LAUNCHD_DOMAIN" >/dev/null 2>&1 \
        || die "no desktop session for $USER: log in to this Mac (see docs/macos.md) and re-run this script"

    mkdir -p "$AGENT_DIR" "$LOG_DIR"
    local label
    for label in devbox.launcher devbox.agents-reap devbox.agents-restore; do
        note "writing $AGENT_DIR/$label.plist"
        render "$REPO_DIR/launchd/$label.plist.in" "$AGENT_DIR/$label.plist"
        plutil -lint -s "$AGENT_DIR/$label.plist" || die "$label.plist is not a valid plist"
    done

    # The restore job is left for launchd to load at the next login: loading it
    # now would start every agent marked active, which an install has no
    # business doing.
    note "starting the launcher and the agents reaper"
    launchd_load "devbox.launcher"
    launchd_load "devbox.agents-reap"

    local job="devbox.agents-schedule"
    if [ -n "$SCHEDULES" ]; then
        note "starting the schedules job"
        render "$REPO_DIR/launchd/$job.plist.in" "$AGENT_DIR/$job.plist"
        plutil -lint -s "$AGENT_DIR/$job.plist" || die "$job.plist is not a valid plist"
        launchd_load "$job"
    else
        launchctl bootout "$LAUNCHD_DOMAIN/$job" 2>/dev/null || true
        rm -f "$AGENT_DIR/$job.plist"
    fi
}

macos_notes() {
    # ssh runs a command without the login files that add Homebrew to PATH, so
    # Copy terminal command would fail with "tmux: command not found".
    if ! env -i HOME="$HOME" /bin/zsh -c 'command -v tmux' >/dev/null 2>&1; then
        warn "tmux is not on PATH for commands run over ssh; add this to ~/.zshenv:"
        # shellcheck disable=SC2016  # $PATH is for ~/.zshenv to expand
        printf '    export PATH="%s:%s:$PATH"\n' "$(dirname "$(command -v tmux)")" "$BIN_DIR" >&2
    fi
    if fdesetup status 2>/dev/null | grep -q 'FileVault is On'; then
        warn "FileVault is on: after a power cut this Mac waits for a password before anything starts; see docs/macos.md"
    fi
    if pmset -g 2>/dev/null | grep -Eq '^ *sleep +[1-9]'; then
        warn "this Mac can go to sleep, which takes the launcher and agents offline; see docs/macos.md"
    fi
}

case "$(uname -s)" in
    Darwin)
        LOG_DIR="$HOME/Library/Logs/devbox-launcher"
        LOG_HINT="$LOG_DIR/launcher.log"
        install_launchd
        ;;
    *)
        [ -z "$SCHEDULES" ] || warn "LAUNCHER_SCHEDULES only works on macOS for now; ignoring it"
        LOG_HINT="journalctl --user -u $UNIT -n 50"
        install_systemd
        ;;
esac

# A service can look alive in between crash-loop restarts, so only an actual
# HTTP answer proves the install worked.
note "waiting for the launcher to answer on http://$HOST:$PORT/"
for attempt in $(seq 1 10); do
    if curl -fsS -o /dev/null "http://$HOST:$PORT/" 2>/dev/null; then
        printf '\033[32mok:\033[0m launcher is up on http://%s:%s/\n' "$HOST" "$PORT"
        break
    fi
    [ "$attempt" -lt 10 ] || die "launcher did not answer; check: $LOG_HINT"
    sleep 2
done

[ "$(uname -s)" != Darwin ] || macos_notes

cat <<EOF

Next step — publish it to your tailnet (this is the intended way to reach it):

    tailscale serve --bg $PORT

It will then be at https://<this-host>.<your-tailnet>.ts.net

Do NOT use 'tailscale funnel', and do not put this on a public port or a
reverse proxy without authentication in front of it. Read SECURITY.md first.
EOF
