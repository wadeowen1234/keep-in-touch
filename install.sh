#!/bin/bash
# Keep in Touch installer.
# Copies the app to ~/KeepInTouch, runs the check every 15 minutes (and on wake),
# and keeps the settings page running at http://localhost:8787
set -e

DEST="$HOME/KeepInTouch"
SRC="$(cd "$(dirname "$0")" && pwd)"
PY="/usr/bin/python3"
AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

if ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  echo "Python isn't installed yet. Run:  xcode-select --install"
  echo "Click Install in the pop-up, wait for it to finish, then run this installer again."
  exit 1
fi

mkdir -p "$DEST" "$AGENTS"
cp "$SRC/keep_in_touch.py" "$SRC/settings.html" "$DEST/"

if [ ! -f "$DEST/config.json" ]; then
  echo ""
  echo "What's your ntfy channel name? (the one you subscribed to in the ntfy app)"
  read -r -p "Paste it, or press Enter to make a new one: " TOPIC
  TOPIC="$(echo "$TOPIC" | tr -d '[:space:]')"
  [ -z "$TOPIC" ] && TOPIC="kit-$(openssl rand -hex 8)"
  cat > "$DEST/config.json" <<EOF
{
  "ntfy_server": "https://ntfy.sh",
  "ntfy_topic": "$TOPIC",
  "notify_hour": 11,
  "awake_hours": [9, 22],
  "nudge_after_days": 2,
  "nudge_repeat_days": 2,
  "snooze_days": 3,
  "count_their_texts": false,
  "mac_banners": true,
  "people": []
}
EOF
else
  TOPIC=$("$PY" -c "import json;print(json.load(open('$DEST/config.json'))['ntfy_topic'])")
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
  launchctl bootout "gui/$UID_NUM" "$AGENTS/$1.plist" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$1.plist"
}

write_agent com.keepintouch.agent run "<key>StartInterval</key><integer>900</integer>"
write_agent com.keepintouch.settings serve "<key>KeepAlive</key><true/>"

REAL_PY=$("$PY" -c 'import os,sys;print(os.path.realpath(sys.executable))')

echo ""
echo "Installed to $DEST"
echo ""
echo "Your ntfy channel:  $TOPIC"
echo "(Subscribe to exactly this in the ntfy app on your iPhone if you haven't.)"
echo ""
echo "LAST STEP: give Python Full Disk Access so it can read Messages and Contacts."
echo "System Settings is opening. Click +, press Cmd+Shift+G, paste each path, add it, and switch it on:"
echo ""
echo "    $PY"
echo "    $REAL_PY"
echo ""
echo "Then restart the settings page:"
echo "    launchctl kickstart -k gui/$UID_NUM/com.keepintouch.settings"
echo ""
echo "and open http://localhost:8787 to add your people."
echo ""
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" || true
