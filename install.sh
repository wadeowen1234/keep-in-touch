#!/bin/bash
# Keep in Touch installer (also the updater: rerun it any time, your people and settings are kept).
#
#   curl -fsSL https://raw.githubusercontent.com/wadeowen1234/keep-in-touch/main/install.sh | bash
#
# Puts the app in ~/KeepInTouch, checks Messages every 15 minutes (and on wake),
# keeps the settings page running at http://localhost:8787, then opens it.
set -euo pipefail

REPO="${KIT_REPO:-wadeowen1234/keep-in-touch}"
BRANCH="${KIT_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/$REPO/$BRANCH"
FILES="keep_in_touch.py settings.html qr.js uninstall.sh"
DEST="$HOME/KeepInTouch"
PY="/usr/bin/python3"
AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
PORT=8787

say() { printf '%s\n' "$*"; }
die() { printf '\n%s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "Keep in Touch only runs on a Mac (it reads the Messages app)."

# Python comes with Apple's free Command Line Tools.
if ! xcode-select -p >/dev/null 2>&1 || ! "$PY" -c 'import sys; assert sys.version_info >= (3, 8)' >/dev/null 2>&1; then
  xcode-select --install >/dev/null 2>&1 || true
  die "First, install Apple's Command Line Tools (free, includes Python):
  1. A pop-up should have just appeared. Click Install, then Agree.
  2. Wait for it to finish (5-15 minutes).
  3. Run the same install command again."
fi

# Get the app files: from this folder if you downloaded the repo, otherwise from GitHub.
SELF="${BASH_SOURCE[0]:-}"
SRC=""
if [ -n "$SELF" ] && [ -f "$SELF" ] && [ -f "$(dirname "$SELF")/keep_in_touch.py" ]; then
  SRC="$(cd "$(dirname "$SELF")" && pwd)"
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for f in $FILES; do
  if [ -n "$SRC" ]; then
    cp "$SRC/$f" "$TMP/$f"
  else
    curl -fsSL "$RAW/$f" -o "$TMP/$f" || die "Couldn't download $f. Check your internet connection and try again."
  fi
done
"$PY" -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$TMP/keep_in_touch.py" \
  || die "The download looks broken. Try again in a minute."

mkdir -p "$DEST" "$AGENTS"
for f in $FILES; do cp "$TMP/$f" "$DEST/$f"; done
chmod +x "$DEST/keep_in_touch.py" "$DEST/uninstall.sh"

# Your private ntfy channel. Kept if you already have one.
if [ -f "$DEST/config.json" ]; then
  TOPIC="$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["ntfy_topic"])' "$DEST/config.json")"
  UPDATED=1
else
  UPDATED=0
  TOPIC=""
  if { exec 3</dev/tty; } 2>/dev/null; then
    say ""
    say "Press Return to create your private notification channel (recommended)."
    say "Already use ntfy and want a channel you subscribed to before? Paste its name instead."
    printf '> '
    read -r TOPIC <&3 || TOPIC=""
    exec 3<&-
  fi
  TOPIC="$(printf '%s' "$TOPIC" | tr -d '[:space:]')"
  if [ -n "$TOPIC" ] && ! printf '%s' "$TOPIC" | grep -Eq '^[A-Za-z0-9_-]{1,64}$'; then
    say "That name has characters ntfy doesn't allow, so I made a new one instead."
    TOPIC=""
  fi
  if [ -z "$TOPIC" ]; then
    # Long and random: anyone who knows it can see your banners, so it works like a password.
    TOPIC="kit-$(LC_ALL=C tr -dc 'abcdefghijkmnpqrstuvwxyz23456789' </dev/urandom | head -c 14)"
  fi
  "$PY" - "$DEST/config.json" "$TOPIC" <<'EOF'
import json, sys
path, topic = sys.argv[1], sys.argv[2]
cfg = {
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": topic,
    "notify_hour": 11,
    "awake_hours": [9, 22],
    "nudge_after_days": 2,
    "nudge_repeat_days": 2,
    "snooze_days": 3,
    "count_their_texts": False,
    "mac_banners": True,
    "people": [],
}
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
EOF
fi

write_agent() {  # label, command, extra plist keys
  cat > "$AGENTS/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$DEST/keep_in_touch.py</string>
    <string>$2</string>
  </array>
  <key>RunAtLoad</key><true/>
  $3
  <key>StandardOutPath</key><string>$DEST/log.txt</string>
  <key>StandardErrorPath</key><string>$DEST/log.txt</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$UID_NUM/$1" 2>/dev/null || true
  # bootout finishes asynchronously; retry bootstrap briefly if launchd is still tearing down.
  for _ in 1 2 3 4 5; do
    launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$1.plist" 2>/dev/null && return 0
    sleep 1
  done
  die "Couldn't start the background job $1. Try running the installer again."
}

write_agent com.keepintouch.agent run "<key>StartInterval</key><integer>900</integer>"
write_agent com.keepintouch.settings serve "<key>KeepAlive</key><true/>"

# Wait for the settings page to come up, then open it.
for _ in $(seq 1 20); do
  curl -s -o /dev/null "http://localhost:$PORT/" && break
  sleep 0.5
done

say ""
if [ "$UPDATED" = 1 ]; then
  say "Updated Keep in Touch. Your people and settings were kept."
else
  say "Installed Keep in Touch."
fi
say ""
say "Your private channel: $TOPIC"
say "Treat it like a password. Anyone who knows it can see your banners."
say ""
say "Your browser is opening http://localhost:8787 to finish setup."
say "(Bookmark it. It only works on this Mac.)"
say ""
open "http://localhost:$PORT" >/dev/null 2>&1 || true
