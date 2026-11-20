# Keep in Touch

Banner reminders to text people, based on your actual iMessage history. Your Mac checks Messages every 15 minutes (and when it wakes up), works out who's due, and queues banners on your iPhone through the free ntfy app. Banners still arrive when the laptop is shut.

## Setup (about 20 minutes)

1. **iPhone:** install **ntfy**, tap **+**, subscribe to a random channel name like `kit-owen-7q4mz9x2` (server: ntfy.sh), and allow notifications.
2. **Mac:** unzip this folder in Downloads, open Terminal, and run:
   ```
   bash ~/Downloads/keep-in-touch/install.sh
   ```
   Paste your channel name when it asks. If it says Python isn't installed, run `xcode-select --install`, click Install, and run the installer again.
3. **Full Disk Access:** System Settings opens to Privacy & Security → Full Disk Access. Click **+**, press **Cmd+Shift+G**, paste each path the installer printed, add it, and switch it on. This lets the app read Messages and Contacts. It only reads. It never sends texts or changes anything.
4. **Restart the settings page** with the command the installer printed, then open **http://localhost:8787** and bookmark it.

## The settings page (localhost:8787)

- **Add someone:** tap one of the people you text most, or search your contacts, pick an interval, and add.
- **Each person:** change their interval, toggle nudges, tap **Talked ✓** after a call or hangout, **Snooze**, or **Edit** to change their name and numbers or remove them.
- **Settings:** reminder time, nudge timing, snooze length, Mac banners, and whether their texts count.
- **Send test banner:** confirms your phone is connected.

It only runs on your Mac. Your phone can't open it.

## Banner buttons (on your phone)

Long-press a banner to get:
- **Texted ✓:** resets their timer, for when you reached them some other way
- **Snooze 3d:** pushes them back 3 days
- **Stop nudging:** on nudges only; turns off follow-ups for that person

Your Mac applies the tap within a minute if the settings page is running, or at its next check. If the Mac is off for more than about 12 hours, a tap can get lost, so just redo it.

## How it decides

- **Reminder:** on the day you hit someone's interval, at your reminder time (11am by default).
- **Nudge:** 2 days later if you still haven't texted, then every 2 days until you do. Nudges are louder.
- **You text them** from your phone or your Mac: the next check sees it (Messages in iCloud syncs your phone's texts), cancels pending banners, and restarts the timer.
- **Mac closed for days:** banners up to 3 days out are already queued. Anything later sends when the Mac wakes, only during waking hours, and only the latest one.
- Tapping a banner opens a text to that person.

## Terminal commands (optional)

`python3 ~/KeepInTouch/keep_in_touch.py status`, `talked Dad`, `snooze Dad 5`, `nudges-off Sam`, `nudges-on Sam`, `test`

## Troubleshooting

- Log: `~/KeepInTouch/log.txt`
- Page says "macOS blocked access": Full Disk Access isn't on for one of the Python paths. After fixing it, restart the page (step 4).
- Someone shows "No texts found": their number or email doesn't match Messages. Click Edit and fix it.
- Turn everything off:
  ```
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.keepintouch.agent.plist
  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.keepintouch.settings.plist
  ```

**Privacy:** ntfy.sh is a public server. Your random channel name works like a password, so don't share it. Banners contain only names and day counts.
