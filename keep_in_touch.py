#!/usr/bin/env python3
"""
Keep in Touch
Reminds you to text people, based on your real iMessage history.

Reads ~/Library/Messages/chat.db (a read-only copy), figures out when you last
texted each person on your list, and schedules banner notifications on your
iPhone through ntfy, plus a Mac banner when you're on your laptop.

Commands:
  python3 keep_in_touch.py              check messages and schedule banners (the background job)
  python3 keep_in_touch.py serve        run the settings page at http://localhost:8787
  python3 keep_in_touch.py status       show everyone, when you last texted them, and when they're due
  python3 keep_in_touch.py test         send a test banner to your phone and Mac
  python3 keep_in_touch.py talked Dad   reset someone's timer by hand (a call, FaceTime, in person)
  python3 keep_in_touch.py snooze Dad 3 push someone's reminders back 3 days
  python3 keep_in_touch.py nudges-off Sam   stop follow-up nudges for someone (reminders still come)
  python3 keep_in_touch.py nudges-on Sam    turn follow-up nudges back on
  python3 keep_in_touch.py cancel-all       cancel every banner already queued on your phone (used by uninstall)
  python3 keep_in_touch.py check            check that macOS lets this read Messages (Full Disk Access)
"""
import datetime as dt
import fcntl
import glob
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
LOCK_PATH = os.path.join(APP_DIR, ".lock")
PAGE_PATH = os.path.join(APP_DIR, "settings.html")
QR_PATH = os.path.join(APP_DIR, "qr.js")
SITE_URL = "https://wadeowen1234.github.io/keep-in-touch"
FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
CHAT_DB = os.environ.get("KIT_CHAT_DB", os.path.expanduser("~/Library/Messages/chat.db"))
CONTACTS_DIR = os.environ.get("KIT_CONTACTS_DIR", os.path.expanduser("~/Library/Application Support/AddressBook"))
DRY_RUN = os.environ.get("KIT_DRY_RUN") == "1"
PORT = int(os.environ.get("KIT_PORT", "8787"))

APPLE_EPOCH = 978307200          # 2001-01-01 UTC, the zero point for Messages timestamps
NTFY_MAX_AHEAD = 70 * 3600       # ntfy.sh schedules up to 3 days ahead; stay a little under
SHOW_MAC_BANNER_WINDOW = 3 * 3600

DEFAULTS = {
    "ntfy_server": "https://ntfy.sh",
    "notify_hour": 11,
    "awake_hours": [9, 22],
    "nudge_after_days": 2,
    "nudge_repeat_days": 2,
    "snooze_days": 3,
    "count_their_texts": False,
    "mac_banners": True,
    "people": [],
}


class KitError(Exception):
    pass


class AccessError(KitError):
    """macOS is blocking Messages because Python doesn't have Full Disk Access."""


BLOCKED_MSG = ("macOS is blocking access to Messages. Open http://localhost:8787 and follow "
               "the Full Disk Access steps.")


# ---------- small helpers ----------

def now_ts():
    fake = os.environ.get("KIT_NOW")
    return dt.datetime.fromisoformat(fake).timestamp() if fake else time.time()


def log(msg):
    stamp = dt.datetime.fromtimestamp(now_ts()).strftime("%Y-%m-%d %H:%M")
    print("[{}] {}".format(stamp, msg), flush=True)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_config():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        raise KitError("No config.json found next to this script. Run the installer first.")
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


@contextmanager
def locked():
    """Only one copy (background job, settings page, button handler) touches state at a time."""
    with open(LOCK_PATH, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def norm(handle):
    """Turn '(847) 555-1234', '847-555-1234', '+18475551234' into one format."""
    h = (handle or "").strip()
    if not h or h.lower().startswith("chat"):
        return ""
    if "@" in h:
        return h.lower()
    digits = re.sub(r"\D", "", h)
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits if digits else ""


def apple_to_unix(value):
    v = float(value)
    if v > 1e11:          # newer macOS stores nanoseconds
        v = v / 1e9
    return v + APPLE_EPOCH


def local_day(ts):
    return dt.date.fromtimestamp(ts)


def at_hour(day, hour):
    return dt.datetime(day.year, day.month, day.day, int(hour)).timestamp()


def find_person(cfg, name):
    low = name.lower().strip()
    exact = [p for p in cfg["people"] if p["name"].lower() == low]
    matches = exact or [p for p in cfg["people"] if p["name"].lower().startswith(low)]
    if len(matches) != 1:
        raise KitError("Couldn't find exactly one person matching '{}'. Names: {}".format(
            name, ", ".join(p["name"] for p in cfg["people"]) or "none yet"))
    return matches[0]


def copy_db(path, tmp, name):
    try:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                shutil.copy2(path + suffix, os.path.join(tmp, name + suffix))
    except PermissionError:
        raise AccessError(BLOCKED_MSG)
    return sqlite3.connect(os.path.join(tmp, name))


def messages_access():
    """'ok', 'blocked' (no Full Disk Access), or 'missing' (Messages isn't set up on this Mac)."""
    try:
        with open(CHAT_DB, "rb") as f:
            f.read(16)
        return "ok"
    except PermissionError:
        return "blocked"
    except FileNotFoundError:
        # Without Full Disk Access, macOS can make the whole folder unreadable.
        try:
            os.listdir(os.path.dirname(CHAT_DB))
        except PermissionError:
            return "blocked"
        except OSError:
            pass
        return "missing"


def fda_paths():
    """The Python files to add under Full Disk Access (the launcher and the real binary)."""
    real = os.path.realpath(sys.executable)
    return ["/usr/bin/python3"] + ([real] if real != "/usr/bin/python3" else [])


# ---------- reading Messages and Contacts ----------

def read_messages(include_theirs):
    """Return {normalized handle: (unix time of last message, messages in last 90 days)}."""
    access = messages_access()
    if access == "blocked":
        raise AccessError(BLOCKED_MSG)
    if access == "missing":
        raise KitError("Can't find the Messages database at {}. Is Messages set up and signed in "
                       "on this Mac?".format(CHAT_DB))
    tmp = tempfile.mkdtemp()
    try:
        con = copy_db(CHAT_DB, tmp, "chat.db")
        mine_only = "" if include_theirs else "AND m.is_from_me = 1"
        rows = con.execute("""
            SELECT c.chat_identifier, m.date
            FROM chat c
            JOIN chat_message_join j ON j.chat_id = c.ROWID
            JOIN message m ON m.ROWID = j.message_id
            WHERE 1=1 {}""".format(mine_only)).fetchall()
        rows += con.execute("""
            SELECT h.id, m.date FROM handle h
            JOIN message m ON m.handle_id = h.ROWID
            WHERE 1=1 {}""".format(mine_only)).fetchall()
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    recent_cut = now_ts() - 90 * 86400
    last, counts = {}, {}
    for ident, date in rows:
        key = norm(ident)
        if not key or date is None:
            continue
        ts = apple_to_unix(date)
        if ts > last.get(key, 0):
            last[key] = ts
        if ts > recent_cut:
            counts[key] = counts.get(key, 0) + 1
    return {k: (last[k], counts.get(k, 0)) for k in last}


def read_contacts():
    """Your Mac Contacts: [{'name': ..., 'handles': [...]}]. Empty list if unavailable."""
    dbs = glob.glob(os.path.join(CONTACTS_DIR, "Sources", "*", "AddressBook-v22.abcddb"))
    dbs += glob.glob(os.path.join(CONTACTS_DIR, "AddressBook-v22.abcddb"))
    people = {}
    for i, path in enumerate(dbs):
        tmp = tempfile.mkdtemp()
        try:
            con = copy_db(path, tmp, "ab.abcddb")
            queries = [
                "SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER "
                "FROM ZABCDRECORD r JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK",
                "SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, e.ZADDRESS "
                "FROM ZABCDRECORD r JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK",
            ]
            for q in queries:
                try:
                    for pk, first, lastn, org, handle in con.execute(q):
                        name = " ".join(x for x in (first, lastn) if x) or org
                        h = norm(handle)
                        if not name or not h:
                            continue
                        entry = people.setdefault((i, pk), {"name": name, "handles": []})
                        if h not in entry["handles"]:
                            entry["handles"].append(h)
                except sqlite3.Error:
                    pass
            con.close()
        except (KitError, sqlite3.Error):
            pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    merged = {}
    for e in people.values():
        m = merged.setdefault(e["name"], {"name": e["name"], "handles": []})
        m["handles"] += [h for h in e["handles"] if h not in m["handles"]]
    return sorted(merged.values(), key=lambda e: e["name"].lower())


def last_texted(person, msgs, state):
    times = [msgs.get(norm(h), (0, 0))[0] for h in person.get("handles", [])]
    times.append(state.get("manual", {}).get(person["name"], 0))
    best = max(times) if times else 0
    return best or None


# ---------- notifications ----------

REMINDER_TITLES = [
    "📱 Time to text {name}",
    "👋 Check in with {name}",
    "💬 {name}, {days} days",
    "🫶 {name} would like to hear from you",
]
REMINDER_BODIES = [
    "It's been {days} days. Send something quick.",
    "Even a meme counts.",
    "30 seconds. Tap to open a text.",
    "{days} days since you last texted. Say hi.",
]
NUDGE_TITLES = [
    "⏰ Still haven't texted {name}",
    "🔁 Nudge: {name}",
    "🚨 {name} is at {days} days",
]
NUDGE_BODIES = [
    "{days} days now. Go, it'll take a minute.",
    "This is your follow-up. Tap to text.",
    "Long-press for Snooze or Stop nudging.",
]


def wording(key, kind, name, days):
    rnd = random.Random(key)
    titles, bodies = (NUDGE_TITLES, NUDGE_BODIES) if kind == "nudge" else (REMINDER_TITLES, REMINDER_BODIES)
    fill = {"name": name, "days": days}
    return rnd.choice(titles).format(**fill), rnd.choice(bodies).format(**fill)


def ntfy_url(cfg, *parts):
    base = cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    return "/".join([base] + list(parts))


def control_topic(cfg):
    return cfg["ntfy_topic"] + "-ctl"


def buttons(cfg, kind, name):
    """Long-press buttons on a banner. Tapping one posts a command the Mac picks up."""
    def btn(label, cmd):
        return {"action": "http", "label": label, "url": ntfy_url(cfg, control_topic(cfg)),
                "method": "POST", "body": "{}|{}".format(cmd, name), "clear": True}
    out = [btn("Texted ✓", "talked"), btn("Snooze {}d".format(cfg.get("snooze_days", 3)), "snooze")]
    if kind == "nudge":
        out.append(btn("Stop nudging", "nudges-off"))
    return out


def publish(cfg, title, body, priority, tags, click, when, now, actions=None):
    payload = {
        "topic": cfg["ntfy_topic"],
        "title": title,
        "message": body,
        "priority": priority,
    }
    if tags:
        payload["tags"] = tags
    if click:
        payload["click"] = click
    if actions:
        payload["actions"] = actions
    if when and when - now > 15:
        payload["delay"] = str(int(when))
    if DRY_RUN:
        when_txt = dt.datetime.fromtimestamp(when).strftime("%a %b %d %H:%M") if "delay" in payload else "now"
        labels = ", ".join(a["label"] for a in actions or [])
        print("    [phone {}] {} | {}{}".format(when_txt, title, body, "  [" + labels + "]" if labels else ""))
        return "dry-{}".format(random.randint(1000, 9999))
    req = urllib.request.Request(
        ntfy_url(cfg), data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r).get("id")


def cancel(cfg, msg_id):
    if DRY_RUN:
        print("    [cancel] {}".format(msg_id))
        return
    req = urllib.request.Request(ntfy_url(cfg, cfg["ntfy_topic"], msg_id), method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except (urllib.error.URLError, OSError):
        pass  # already delivered, already gone, or offline


def mac_banner(cfg, title, body):
    if not cfg.get("mac_banners", True):
        return
    if DRY_RUN or sys.platform != "darwin":
        print("    [mac] {} | {}".format(title, body))
        return
    script = "display notification {} with title {} sound name \"Glass\"".format(
        json.dumps(body, ensure_ascii=False), json.dumps(title, ensure_ascii=False))
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


# ---------- commands (from Terminal, the settings page, or banner buttons) ----------

def apply_command(cfg, state, cmd, name, days=None):
    p = find_person(cfg, name)
    now = now_ts()
    if cmd == "talked":
        state.setdefault("manual", {})[p["name"]] = now
        state.get("snooze", {}).pop(p["name"], None)
        return "Reset {}'s timer.".format(p["name"])
    if cmd == "snooze":
        days = int(days or cfg.get("snooze_days", 3))
        state.setdefault("snooze", {})[p["name"]] = now + days * 86400
        return "Snoozed {} for {} days.".format(p["name"], days)
    if cmd in ("nudges-off", "nudges-on"):
        p["nudges"] = cmd == "nudges-on"
        save_json(CONFIG_PATH, cfg)
        return "Nudges {} for {}.".format("on" if p["nudges"] else "off", p["name"])
    raise KitError("Unknown command: " + cmd)


def pull_button_presses(cfg, state):
    """Read commands sent by tapping banner buttons on the phone."""
    if DRY_RUN:
        return
    since = state.get("ctl_since") or "12h"
    url = ntfy_url(cfg, control_topic(cfg), "json") + "?poll=1&since=" + urllib.parse.quote(since)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            lines = r.read().decode("utf-8").splitlines()
    except (urllib.error.URLError, OSError):
        return
    for line in lines:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") != "message":
            continue
        state["ctl_since"] = msg["id"]
        cmd, _, name = (msg.get("message") or "").partition("|")
        try:
            log("button: " + apply_command(cfg, state, cmd.strip(), name.strip()))
        except KitError as e:
            log("button ignored: {}".format(e))


# ---------- the schedule ----------

def events_for(person, last_ts, cfg, state, now):
    """Every reminder/nudge this person should get, from their due date out to the ntfy horizon."""
    every = int(person["every_days"])
    hour = cfg.get("notify_hour", 11)
    last_day = local_day(last_ts) if last_ts else local_day(now) - dt.timedelta(days=every)
    due = last_day + dt.timedelta(days=every)
    base = "{}|{}".format(person["name"], last_day.isoformat())

    evs = [{"key": base + "|reminder|0", "kind": "reminder", "at": at_hour(due, hour)}]
    if person.get("nudges", True):
        day = due + dt.timedelta(days=cfg.get("nudge_after_days", 2))
        n = 1
        while n <= 100:
            t = at_hour(day, hour)
            evs.append({"key": "{}|nudge|{}".format(base, n), "kind": "nudge", "at": t})
            if t > now + NTFY_MAX_AHEAD:
                break
            n += 1
            day += dt.timedelta(days=cfg.get("nudge_repeat_days", 2))

    until = state.get("snooze", {}).get(person["name"], 0)
    if until > now:
        wake = local_day(until)
        evs = [e for e in evs if e["at"] >= at_hour(wake, hour) + 1]
        evs.insert(0, {"key": "{}|snooze|{}".format(base, wake.isoformat()),
                       "kind": "reminder", "at": at_hour(wake, hour)})
        due = max(due, wake)

    for e in evs:
        e["days"] = (local_day(e["at"]) - last_day).days
        e["name"] = person["name"]
        e["handle"] = (person.get("handles") or [""])[0]
    return evs, due, last_day


def awake(cfg, now):
    start, end = cfg.get("awake_hours", [9, 22])
    return start <= dt.datetime.fromtimestamp(now).hour < end


def send(cfg, e, when, now):
    title, body = wording(e["key"], e["kind"], e["name"], e["days"])
    priority = 4 if e["kind"] == "nudge" else 3
    # No ntfy tags: the iPhone app turns tags into an extra emoji in front of the title,
    # and every title already starts with one.
    tags = []
    click = "sms:" + norm(e["handle"]) if e["handle"] else None
    msg_id = publish(cfg, title, body, priority, tags, click, when, now, buttons(cfg, e["kind"], e["name"]))
    return msg_id, title, body


def run(cfg, state):
    now = now_ts()
    pull_button_presses(cfg, state)
    msgs = read_messages(cfg.get("count_their_texts", False))
    scheduled = state.setdefault("scheduled", {})
    delivered = state.setdefault("delivered", {})

    # 1. Anything ntfy already delivered since the last check: mark it done, echo it on the Mac.
    for key, info in list(scheduled.items()):
        if info["at"] <= now:
            delivered[key] = info["at"]
            del scheduled[key]
            if now - info["at"] < SHOW_MAC_BANNER_WINDOW:
                mac_banner(cfg, info.get("title", "Keep in Touch"), info.get("body", ""))

    wanted = {}
    catch_up = []
    for p in cfg["people"]:
        last = last_texted(p, msgs, state)
        evs, _, _ = events_for(p, last, cfg, state, now)
        past = [e for e in evs if e["at"] <= now]
        future = [e for e in evs if now < e["at"] <= now + NTFY_MAX_AHEAD]
        for e in future:
            wanted[e["key"]] = e
        if past:
            catch_up.append(past[-1])   # only the most recent missed one, never a pile-up

    # 2. Cancel banners that no longer make sense (you texted them, snoozed, nudges off, removed).
    for key, info in list(scheduled.items()):
        if key not in wanted:
            log("cancel: " + key)
            cancel(cfg, info["id"])
            del scheduled[key]

    # 3. Missed while the Mac was closed? Send it now (only during waking hours).
    for e in catch_up:
        if e["key"] in delivered or e["key"] in scheduled:
            continue
        cycle = e["key"].rsplit("|", 2)[0] + "|"
        if any(k.startswith(cycle) and t >= e["at"] for k, t in delivered.items()):
            continue  # already pinged them more recently than this (e.g. nudges just turned off)
        if not awake(cfg, now):
            continue
        log("send now: " + e["key"])
        _, title, body = send(cfg, e, None, now)
        mac_banner(cfg, title, body)
        delivered[e["key"]] = now

    # 4. Queue upcoming banners on ntfy so they arrive even with the laptop shut.
    for key, e in wanted.items():
        if key in scheduled or key in delivered:
            continue
        log("schedule: " + key)
        msg_id, title, body = send(cfg, e, e["at"], now)
        scheduled[key] = {"id": msg_id, "at": e["at"], "title": title, "body": body}

    # 5. Forget old history.
    cutoff = now - 60 * 86400
    state["delivered"] = {k: t for k, t in delivered.items() if t > cutoff}
    state["snooze"] = {k: t for k, t in state.get("snooze", {}).items() if t > now}
    state["last_run"] = now


def overview(cfg, state):
    """Everything the settings page and `status` show."""
    now = now_ts()
    access, access_error = "ok", None
    try:
        msgs = read_messages(cfg.get("count_their_texts", False))
    except AccessError as e:
        msgs, access, access_error = None, "blocked", str(e)
    except KitError as e:
        msgs, access, access_error = None, "missing", str(e)
    today = local_day(now)
    rows = []
    for p in cfg["people"]:
        last = last_texted(p, msgs, state) if msgs is not None else None
        _, due, _ = events_for(p, last, cfg, state, now)
        rows.append({
            "name": p["name"],
            "handles": p.get("handles", []),
            "every_days": p["every_days"],
            "nudges": p.get("nudges", True),
            "last_days_ago": (today - local_day(last)).days if last else None,
            "due_in": (due - today).days if msgs is not None else None,
            "snoozed_until": local_day(state["snooze"][p["name"]]).isoformat()
            if state.get("snooze", {}).get(p["name"], 0) > now else None,
        })
    return {
        "people": rows,
        "queued": len(state.get("scheduled", {})),
        "last_run": state.get("last_run"),
        "settings": {k: cfg[k] for k in DEFAULTS if k != "people"},
        "topic": cfg["ntfy_topic"],
        "now": now,
        "access": access,
        "access_error": access_error,
        "fda_paths": fda_paths(),
        "setup": cfg.get("setup", {}),
        "subscribe_url": "{}/subscribe.html#{}".format(SITE_URL, cfg["ntfy_topic"]),
        "ntfy_server": cfg.get("ntfy_server", "https://ntfy.sh"),
    }


def print_status(cfg, state):
    o = overview(cfg, state)
    if o["access"] != "ok":
        print("\n" + o["access_error"])
    print("\n{:<22}{:<8}{:<22}{:<18}{}".format("Name", "Every", "Last texted", "Due", "Nudges"))
    print("-" * 78)
    for r in o["people"]:
        ago = r["last_days_ago"]
        last_txt = "not found (check #)" if ago is None else (
            "today" if ago == 0 else "{} day{} ago".format(ago, "" if ago == 1 else "s"))
        left = r["due_in"]
        due_txt = "?" if left is None else "today" if left == 0 else ("in {}d".format(left) if left > 0 else "{}d overdue".format(-left))
        if r["snoozed_until"]:
            due_txt = "snoozed"
        print("{:<22}{:<8}{:<22}{:<18}{}".format(
            r["name"][:21], "{}d".format(r["every_days"]), last_txt, due_txt, "on" if r["nudges"] else "off"))
    print("\n{} banner{} queued on your phone.".format(o["queued"], "" if o["queued"] == 1 else "s"))
    missing = [r["name"] for r in o["people"] if r["last_days_ago"] is None]
    if missing and o["access"] == "ok":
        print("No messages found for: {}. Check their numbers in the settings page.".format(", ".join(missing)))
    print()


# ---------- settings page ----------

def suggestions(cfg):
    try:
        msgs = read_messages(True)
    except KitError:
        msgs = {}
    contacts = read_contacts()
    name_for = {}
    for c in contacts:
        for h in c["handles"]:
            name_for.setdefault(h, c["name"])
    taken = {norm(h) for p in cfg["people"] for h in p.get("handles", [])}
    frequent = sorted(((h, v) for h, v in msgs.items() if v[1] > 0 and h not in taken),
                      key=lambda x: -x[1][1])[:24]
    today = local_day(now_ts())
    return {
        "contacts": contacts,
        "frequent": [{"name": name_for.get(h, h), "handle": h, "count": v[1],
                      "last_days_ago": (today - local_day(v[0])).days} for h, v in frequent],
    }


def clean_person(data):
    name = str(data.get("name", "")).strip()
    handles = [norm(h) for h in data.get("handles", []) if norm(h)]
    every = int(data.get("every_days", 0))
    if not name:
        raise KitError("Give them a name.")
    if not handles:
        raise KitError("Add a phone number or iMessage email for {}.".format(name))
    if not 1 <= every <= 365:
        raise KitError("Pick an interval between 1 and 365 days.")
    return {"name": name, "handles": handles, "every_days": every, "nudges": bool(data.get("nudges", True))}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                with open(PAGE_PATH, "rb") as f:
                    return self.reply(200, f.read(), "text/html")
            if path == "/qr.js":
                with open(QR_PATH, "rb") as f:
                    return self.reply(200, f.read(), "text/javascript")
            with locked():
                cfg, state = load_config(), load_json(STATE_PATH, {})
                if path == "/api/overview":
                    return self.reply(200, overview(cfg, state))
                if path == "/api/suggestions":
                    return self.reply(200, suggestions(cfg))
            self.reply(404, {"error": "Not found"})
        except KitError as e:
            self.reply(400, {"error": str(e)})

    def do_POST(self):
        # The custom header blocks other websites from poking this page.
        if self.headers.get("X-Kit") != "1":
            return self.reply(403, {"error": "Forbidden"})
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "Bad request"})
        try:
            with locked():
                cfg, state = load_config(), load_json(STATE_PATH, {})
                msg = "Saved."
                if path == "/api/person":
                    person = clean_person(data)
                    original = data.get("original") or person["name"]
                    existing = [p for p in cfg["people"] if p["name"] == original]
                    clash = [p for p in cfg["people"] if p["name"] == person["name"] and p["name"] != original]
                    if clash:
                        raise KitError("You already have someone named {}.".format(person["name"]))
                    if existing:
                        existing[0].update(person)
                        msg = "Updated {}.".format(person["name"])
                    else:
                        cfg["people"].append(person)
                        msg = "Added {}.".format(person["name"])
                    save_json(CONFIG_PATH, cfg)
                elif path == "/api/remove":
                    cfg["people"] = [p for p in cfg["people"] if p["name"] != data.get("name")]
                    save_json(CONFIG_PATH, cfg)
                    msg = "Removed {}.".format(data.get("name"))
                elif path == "/api/action":
                    msg = apply_command(cfg, state, data.get("cmd", ""), data.get("name", ""), data.get("days"))
                elif path == "/api/settings":
                    s = data.get("settings", {})
                    for k in ("notify_hour", "nudge_after_days", "nudge_repeat_days", "snooze_days"):
                        if k in s:
                            cfg[k] = max(0, min(int(s[k]), 365))
                    for k in ("count_their_texts", "mac_banners"):
                        if k in s:
                            cfg[k] = bool(s[k])
                    if "awake_hours" in s:
                        cfg["awake_hours"] = [int(x) for x in s["awake_hours"]][:2]
                    save_json(CONFIG_PATH, cfg)
                elif path == "/api/test":
                    publish(cfg, "👋 Keep in Touch is connected", "Banners will show up like this.",
                            3, [], None, None, now_ts())
                    mac_banner(cfg, "👋 Keep in Touch is connected", "Mac banners work too.")
                    return self.reply(200, {"message": "Test banner sent. Check your phone."})
                elif path == "/api/open-fda":
                    subprocess.run(["open", FDA_SETTINGS_URL], check=False)
                    return self.reply(200, {"message": "Opened System Settings."})
                elif path == "/api/check-access":
                    if messages_access() != "ok" and self.fresh_process_can_read():
                        # Access was just granted, but this running copy still can't see it. Restart it.
                        if os.getppid() == 1:
                            threading.Timer(1.0, lambda: os._exit(0)).start()
                            return self.reply(200, {"message": "Access works. Restarting the page…",
                                                    "restarting": True})
                        msg = "Access works. Restart the settings page to pick it up."
                    elif messages_access() == "ok":
                        msg = "Messages access works."
                    else:
                        msg = "Still blocked. Double-check both Python entries are switched on."
                elif path == "/api/setup":
                    setup = cfg.setdefault("setup", {})
                    for k in ("phone_ok", "dismissed"):
                        if k in data:
                            setup[k] = bool(data[k])
                    save_json(CONFIG_PATH, cfg)
                    msg = "Saved."
                else:
                    return self.reply(404, {"error": "Not found"})
                cfg = load_config()
                try:
                    run(cfg, state)
                except AccessError:
                    pass  # the page shows the Full Disk Access steps
                save_json(STATE_PATH, state)
                return self.reply(200, {"message": msg, "overview": overview(cfg, state)})
        except KitError as e:
            self.reply(400, {"error": str(e)})
        except (urllib.error.URLError, OSError) as e:
            self.reply(502, {"error": "Saved, but couldn't reach ntfy: {}".format(e)})


    @staticmethod
    def fresh_process_can_read():
        try:
            r = subprocess.run([sys.executable, os.path.abspath(__file__), "check"],
                               capture_output=True, timeout=20)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


def button_watcher():
    """While the settings page is running, apply banner-button taps within a minute."""
    while True:
        time.sleep(60)
        try:
            with locked():
                cfg, state = load_config(), load_json(STATE_PATH, {})
                before = state.get("ctl_since")
                pull_button_presses(cfg, state)
                if state.get("ctl_since") != before:
                    run(cfg, state)
                save_json(STATE_PATH, state)
        except Exception as e:  # keep the page alive no matter what
            log("watcher: {}".format(e))


def serve():
    threading.Thread(target=button_watcher, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log("Settings page running at http://localhost:{}".format(PORT))
    server.serve_forever()


# ---------- entry point ----------

def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "run"
    try:
        if cmd == "serve":
            return serve()
        if cmd == "check":
            access = messages_access()
            print({"ok": "Messages access works.", "blocked": BLOCKED_MSG,
                   "missing": "Can't find Messages on this Mac."}[access])
            sys.exit(0 if access == "ok" else 1)
        with locked():
            cfg = load_config()
            state = load_json(STATE_PATH, {})
            if cmd == "run":
                try:
                    run(cfg, state)
                except AccessError:
                    # Tell them on the Mac, at most once a day, instead of failing silently.
                    if now_ts() - state.get("access_nag", 0) > 86400:
                        mac_banner(cfg, "Keep in Touch needs access",
                                   "Open localhost:8787 to finish setup (Full Disk Access).")
                        state["access_nag"] = now_ts()
                    save_json(STATE_PATH, state)
                    raise
                save_json(STATE_PATH, state)
            elif cmd == "cancel-all":
                for key, info in list(state.get("scheduled", {}).items()):
                    cancel(cfg, info["id"])
                    del state["scheduled"][key]
                save_json(STATE_PATH, state)
                print("Cancelled all queued banners.")
            elif cmd == "status":
                print_status(cfg, state)
            elif cmd == "test":
                publish(cfg, "👋 Keep in Touch is connected", "Banners will show up like this.",
                        3, [], None, None, now_ts())
                mac_banner(cfg, "👋 Keep in Touch is connected", "Mac banners work too.")
                print("Sent. Check your phone and your Mac.")
            elif cmd in ("talked", "snooze", "nudges-off", "nudges-on") and len(args) > 1:
                days = None
                rest = args[1:]
                if cmd == "snooze" and len(rest) > 1 and rest[-1].isdigit():
                    days, rest = rest[-1], rest[:-1]
                print(apply_command(cfg, state, cmd, " ".join(rest), days))
                run(load_config(), state)
                save_json(STATE_PATH, state)
            else:
                print(__doc__)
    except KitError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
