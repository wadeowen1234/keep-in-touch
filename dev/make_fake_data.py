#!/usr/bin/env python3
"""Build a fake Messages chat.db and Contacts DB for testing off-Mac.

Usage:
  python3 dev/make_fake_data.py /tmp/kittest
  export KIT_CHAT_DB=/tmp/kittest/chat.db KIT_CONTACTS_DIR=/tmp/kittest/ab KIT_DRY_RUN=1 KIT_NOW=2026-11-20T10:30
  python3 keep_in_touch.py status      # or: run, serve (add KIT_PORT=8799)
"""
import datetime as dt, os, sqlite3, sys

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/kittest"
os.makedirs(os.path.join(out, "ab", "Sources", "X"), exist_ok=True)
APPLE = 978307200
ns = lambda iso: int((dt.datetime.fromisoformat(iso).timestamp() - APPLE) * 1e9)

db = os.path.join(out, "chat.db")
if os.path.exists(db): os.remove(db)
c = sqlite3.connect(db)
c.executescript("""
CREATE TABLE handle(ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat(ROWID INTEGER PRIMARY KEY, chat_identifier TEXT);
CREATE TABLE message(ROWID INTEGER PRIMARY KEY, date INTEGER, is_from_me INTEGER, handle_id INTEGER);
CREATE TABLE chat_message_join(chat_id INTEGER, message_id INTEGER);
INSERT INTO handle VALUES (1,'+18475550101'),(2,'+18475550102'),(3,'sam@icloud.com'),(9,'+13125550199');
INSERT INTO chat VALUES (1,'+18475550101'),(2,'+18475550102'),(3,'sam@icloud.com'),(4,'chat99887766'),(9,'+13125550199');
""")
msgs = [(1, "2026-11-15T20:00", 1, 1, 1), (2, "2026-11-19T09:00", 0, 1, 1), (3, "2026-11-18T12:00", 1, 2, 2),
        (4, "2026-10-10T12:00", 1, 3, 3), (5, "2026-11-20T08:00", 1, 0, 4)]
msgs += [(100 + i, "2026-11-1{}T12:00".format(i), 1, 9, 9) for i in range(5)]
for rowid, iso, mine, handle, chat in msgs:
    c.execute("INSERT INTO message VALUES (?,?,?,?)", (rowid, ns(iso), mine, handle))
    c.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat, rowid))
c.commit()

ab = sqlite3.connect(os.path.join(out, "ab", "Sources", "X", "AddressBook-v22.abcddb"))
ab.executescript("""
DROP TABLE IF EXISTS ZABCDRECORD; DROP TABLE IF EXISTS ZABCDPHONENUMBER; DROP TABLE IF EXISTS ZABCDEMAILADDRESS;
CREATE TABLE ZABCDRECORD(Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
CREATE TABLE ZABCDPHONENUMBER(ZOWNER INTEGER, ZFULLNUMBER TEXT);
CREATE TABLE ZABCDEMAILADDRESS(ZOWNER INTEGER, ZADDRESS TEXT);
INSERT INTO ZABCDRECORD VALUES (1,'Dad',NULL,NULL),(2,'Mom',NULL,NULL),(3,'Sam','Lee',NULL),(4,'Jordan','Park',NULL);
INSERT INTO ZABCDPHONENUMBER VALUES (1,'(847) 555-0101'),(2,'847-555-0102'),(4,'312.555.0199');
INSERT INTO ZABCDEMAILADDRESS VALUES (3,'Sam@iCloud.com');
""")
ab.commit()
print("Fake data written to", out)
