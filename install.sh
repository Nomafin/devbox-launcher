#!/usr/bin/env bash
# Install (or update) the devbox launcher as a systemd --user service.
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
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="devbox-launcher.service"

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

command -v tailscale >/dev/null || warn "tailscale is not installed; see SECURITY.md before exposing this service any other way"

# --- install ----------------------------------------------------------------
note "creating the project base dir ($BASE_DIR)"
mkdir -p "$BASE_DIR"

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

note "writing $UNIT_DIR/$UNIT"
mkdir -p "$UNIT_DIR"
sed -e "s|@VENV@|$VENV|g" \
    -e "s|@BASE_DIR@|$BASE_DIR|g" \
    -e "s|@HOST@|$HOST|g" \
    -e "s|@PORT@|$PORT|g" \
    -e "s|@RUN_USER@|$RUN_USER|g" \
    -e "s|@CLAUDE_BIN_DIR@|$CLAUDE_BIN_DIR|g" \
    "$REPO_DIR/devbox-launcher.service.in" > "$UNIT_DIR/$UNIT"

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

# The service reports "active (running)" in between crash-loop restarts, so
# only an actual HTTP answer proves the install worked.
note "waiting for the launcher to answer on http://$HOST:$PORT/"
for attempt in $(seq 1 10); do
    if curl -fsS -o /dev/null "http://$HOST:$PORT/" 2>/dev/null; then
        printf '\033[32mok:\033[0m launcher is up on http://%s:%s/\n' "$HOST" "$PORT"
        break
    fi
    [ "$attempt" -lt 10 ] || die "launcher did not answer; check: journalctl --user -u $UNIT -n 50"
    sleep 2
done

cat <<EOF

Next step — publish it to your tailnet (this is the intended way to reach it):

    tailscale serve --bg $PORT

It will then be at https://<this-host>.<your-tailnet>.ts.net

Do NOT use 'tailscale funnel', and do not put this on a public port or a
reverse proxy without authentication in front of it. Read SECURITY.md first.
EOF
