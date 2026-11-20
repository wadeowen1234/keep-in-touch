#!/bin/bash
# Removes Keep in Touch.
#
#   bash ~/KeepInTouch/uninstall.sh
#   (or) curl -fsSL https://raw.githubusercontent.com/wadeowen1234/keep-in-touch/main/uninstall.sh | bash
#
# Stops the background jobs. Asks before deleting your people and settings.
set -uo pipefail

DEST="$HOME/KeepInTouch"
AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

for label in com.keepintouch.agent com.keepintouch.settings; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
done

# Cancel banners already queued on ntfy so nothing arrives after you uninstall.
if [ -f "$DEST/keep_in_touch.py" ] && [ -f "$DEST/config.json" ]; then
  /usr/bin/python3 "$DEST/keep_in_touch.py" cancel-all >/dev/null 2>&1 || true
fi
echo "Stopped Keep in Touch. No more banners will be scheduled."

ANSWER="n"
if { exec 3</dev/tty; } 2>/dev/null; then
  printf 'Also delete your people and settings in %s? [y/N] ' "$DEST"
  read -r ANSWER <&3 || ANSWER="n"
  exec 3<&-
fi
case "$ANSWER" in
  y|Y|yes|YES)
    rm -rf "$DEST"
    echo "Deleted $DEST."
    ;;
  *)
    echo "Kept $DEST. Reinstalling later will pick up where you left off."
    ;;
esac

echo ""
echo "To finish tidying up (optional):"
echo "  1. Remove your channel from the ntfy app on your iPhone (or delete the app)."
echo "  2. System Settings > Privacy & Security > Full Disk Access: select the python3"
echo "     entries and click the minus (-) button."
