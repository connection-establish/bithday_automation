#!/usr/bin/env python3
"""Turn your birthday list into a calendar file (.ics) for the MacroDroid method.

Each person becomes a yearly-repeating event:
    Title    = the full birthday message (ready to send)
    Location = the person's WhatsApp number (digits only, with country code)
    Time     = SEND_TIME every year

Usage:
    py birthdays_to_calendar.py                      reads birthdays.json
    py birthdays_to_calendar.py my_backup.json       reads another file
Output: birthdays.ics  (import it into a NEW Google Calendar, see the guide)

Works with the Wish Desk backup and with the desktop app's birthdays.json.
Python 3.8+, standard library only.
"""
import json
import re
import sys
from datetime import date, datetime, timedelta

SEND_TIME = "080000"        # 08:00:00 every year
TIMEZONE = "Asia/Colombo"

DEFAULT_TPL = {
    "friend": "Happy birthday {name}! \U0001F382 Hope your day is full of joy and good food. Let's catch up soon!",
    "family": "Happy birthday {name}! \U0001F389 Wishing you health, happiness and everything you wish for. Love you!",
    "colleague": "Happy birthday {name}! Hope you have a wonderful day and a great year ahead.",
    "formal": "Dear {name}, warm birthday wishes to you. May the year ahead bring you success and good health.",
}


def is_leap(y):
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def next_occurrence(month, day, today):
    def build(y):
        d = 28 if (month == 2 and day == 29 and not is_leap(y)) else day
        return date(y, month, d)
    # Feb 29 repeats only in leap years, so use Feb 28 for a reliable yearly event
    if month == 2 and day == 29:
        day = 28
    n = build(today.year)
    return n if n >= today else build(today.year + 1)


def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def fold(line):
    """Fold to 75 bytes per line as the iCalendar format requires."""
    out, cur, size = [], "", 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > (74 if not out else 73):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += b
    out.append(cur)
    return "\r\n ".join(out)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "birthdays.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"Could not read {path}: {e}")
    people = data.get("bdays") if isinstance(data, dict) else None
    if not people:
        sys.exit("No birthdays found in that file.")
    tpl = {**DEFAULT_TPL, **(data.get("tpl") or {})}

    today = date.today()
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BirthdayBot//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:BirthdayBot"]
    count = 0
    for i, b in enumerate(people):
        try:
            _, m, d = (int(x) for x in b["date"].split("-"))
            phone = re.sub(r"\D", "", str(b["phone"]))
            name = str(b["name"]).strip()
        except (KeyError, ValueError):
            print(f"Skipped an entry that is missing a name, number or date: {b}")
            continue
        if len(phone) < 8:
            print(f"Skipped {name}: phone number looks too short")
            continue
        message = tpl.get(b.get("tone", "friend"), tpl["friend"]).replace("{name}", name.split()[0])
        start = next_occurrence(m, d, today).strftime("%Y%m%d")
        end_time = SEND_TIME[:2] + "0500"
        lines += [
            "BEGIN:VEVENT",
            f"UID:birthdaybot-{b.get('id', i)}-{phone}@local",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={TIMEZONE}:{start}T{SEND_TIME}",
            f"DTEND;TZID={TIMEZONE}:{start}T{end_time}",
            "RRULE:FREQ=YEARLY",
            f"SUMMARY:{esc(message)}",
            f"LOCATION:{phone}",
            f"DESCRIPTION:{esc('Birthday message for ' + name)}",
            "END:VEVENT",
        ]
        count += 1
    lines.append("END:VCALENDAR")

    with open("birthdays.ics", "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(fold(l) for l in lines) + "\r\n")
    print(f"Created birthdays.ics with {count} yearly event(s).")


if __name__ == "__main__":
    main()