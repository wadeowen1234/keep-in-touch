# Keep in Touch

Banner reminders to text the people you care about, based on your **real iMessage history**.

Pick your people and how often (Dad every 4 days, a friend every 30). When it's been that long since you texted them, a banner pops up on your iPhone. Text them and it resets on its own. No checking things off.

**Website:** https://wadeowen1234.github.io/keep-in-touch/

## What you need

- A **Mac** signed in to iMessage, with **Messages in iCloud** on (Messages → Settings → iMessage → Enable Messages in iCloud), so texts from your phone show up on the Mac.
- An **iPhone** with the free **[ntfy](https://apps.apple.com/app/ntfy/id1625396347)** app. No account needed.

Free. No sign-ups.

## Install (about 5 minutes)

1. On your Mac, open **Terminal**: press **⌘ Space**, type `Terminal`, press **Return**.
2. Paste this and press **Return**:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/wadeowen1234/keep-in-touch/main/install.sh | bash
   ```
3. When it asks about a channel, just press **Return**. It makes a private one for you.
4. Your browser opens **http://localhost:8787**. Follow the three setup steps there:
   1. **Scan the QR code** with your iPhone camera. It walks you through installing ntfy and subscribing. Then click **Send test banner**.
   2. **Allow access to Messages** (Full Disk Access). The page shows exactly where to click and checks that it worked.
   3. **Add your first 3 people.** Tap someone from "People you've texted most" and pick how often.

If it says you need Apple's Command Line Tools, click **Install** in the pop-up, wait for it to finish, and run the command again.

**Update:** run the same command again. Your people and settings are kept.

## Using it

- **Settings page:** http://localhost:8787 on your Mac (bookmark it). Change intervals, add or remove people, **Talked ✓** after a call or seeing someone, **Snooze**.
- **Banners:** tap one to open a text to that person. Long-press for **Texted ✓**, **Snooze 3d**, or **Stop nudging**.
- **Nudges:** if you still haven't texted 2 days after a reminder, you get a louder nudge, then every 2 days until you do. Turn them off per person.
- **Laptop closed?** Banners up to 3 days ahead are already queued, so they still arrive. Anything later sends when the Mac wakes up, only during the day, and only the latest one.

## Privacy

- Your messages stay on your Mac. It only reads **when** you last texted someone, never what you said, and it never sends anything.
- Banners go through [ntfy.sh](https://ntfy.sh), a free public notification service. It only sees what's in a banner: a name and a day count.
- Your channel name is random and works like a password. Don't share it.

## Troubleshooting

- **No banner on your phone:** in ntfy, check you're subscribed to the exact channel name shown on the settings page (capitals matter), and that notifications for ntfy are allowed in iPhone Settings.
- **"Can't read Messages yet":** the Full Disk Access step isn't done. Open the settings page and follow step 2, then click **Check again**.
- **Someone shows "No texts found":** their number or email doesn't match what's in Messages. Click **Edit** and fix it.
- **Log file:** `~/KeepInTouch/log.txt`

## Uninstall

```bash
bash ~/KeepInTouch/uninstall.sh
```

It stops everything, cancels queued banners, and asks before deleting your people and settings.

## For tinkerers

Everything is one Python file (`keep_in_touch.py`, standard library only, Python 3.8+) plus the settings page (`settings.html`). It runs as two LaunchAgents: `run` every 15 minutes and on wake, and `serve` for the settings page.

Terminal commands: `python3 ~/KeepInTouch/keep_in_touch.py status | test | check | talked NAME | snooze NAME [days] | nudges-off NAME | nudges-on NAME`

Test without touching real data:

```bash
python3 dev/make_fake_data.py /tmp/kittest
export KIT_CHAT_DB=/tmp/kittest/chat.db KIT_CONTACTS_DIR=/tmp/kittest/ab KIT_DRY_RUN=1 KIT_NOW=2026-11-20T10:30
echo '{"ntfy_topic":"test","people":[]}' > config.json
python3 keep_in_touch.py status
KIT_PORT=8799 python3 keep_in_touch.py serve
```

To install from a fork: `KIT_REPO=you/keep-in-touch` before `bash` in the install command, and change `SITE_URL` in `keep_in_touch.py`.
