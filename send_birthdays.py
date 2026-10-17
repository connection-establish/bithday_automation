import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # very old Python
    ZoneInfo = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BDAYS_FILE = os.path.join(APP_DIR, "birthdays.json")
CONF_FILE = os.path.join(APP_DIR, "config.json")
SENT_FILE = os.path.join(APP_DIR, "sent.json")
TONES = ["friend", "family", "colleague", "formal"]

DEFAULT_CONF = {
    "token": "", "phone_id": "", "template": "birthday_wish", "lang": "en",
    "tz": "Asia/Colombo", "send_time": "08:00", "auto_send": False,
    "tone_templates": {t: "" for t in TONES},
}


# ---------- storage ----------
def read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_conf():
    conf = dict(DEFAULT_CONF)
    saved = read_json(CONF_FILE, {})
    conf.update(saved)
    conf["tone_templates"] = {**DEFAULT_CONF["tone_templates"], **saved.get("tone_templates", {})}
    return conf


def load_bdays():
    data = read_json(BDAYS_FILE, {"bdays": []})
    items = data.get("bdays", []) if isinstance(data, dict) else []
    for b in items:
        b.setdefault("id", uuid.uuid4().hex[:8])
        b.setdefault("tone", "friend")
    return items


def save_bdays(items):
    write_json(BDAYS_FILE, {"bdays": items})


# ---------- date helpers ----------
def now_in(tz):
    try:
        return datetime.now(ZoneInfo(tz))
    except Exception:
        return datetime.now()


def is_leap(y):
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def is_birthday(b, now):
    _, m, d = (int(x) for x in b["date"].split("-"))
    if (m, d) == (now.month, now.day):
        return True
    return (m, d) == (2, 29) and (now.month, now.day) == (2, 28) and not is_leap(now.year)


def next_birthday(b, today):
    _, m, d = (int(x) for x in b["date"].split("-"))

    def build(y):
        if m == 2 and d == 29 and not is_leap(y):
            return date(y, 2, 28)
        return date(y, m, d)

    n = build(today.year)
    return n if n >= today else build(today.year + 1)


def parse_date(s):
    s = s.strip()
    if re.fullmatch(r"\d{2}-\d{2}", s):  # month-day only
        s = "2000-" + s
    datetime.strptime(s, "%Y-%m-%d")
    return s


# ---------- WhatsApp sending ----------
def send_wish(conf, b):
    if not conf["token"] or not conf["phone_id"]:
        raise RuntimeError("Add your access token and Phone number ID in Settings first.")
    to = re.sub(r"\D", "", str(b["phone"]))
    template = conf["tone_templates"].get(b.get("tone", "friend")) or conf["template"]
    first = str(b["name"]).split()[0]
    payload = {
        "messaging_product": "whatsapp", "to": to, "type": "template",
        "template": {
            "name": template, "language": {"code": conf["lang"]},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": first}]}],
        },
    }
    req = urllib.request.Request(
        f"https://graph.facebook.com/v21.0/{conf['phone_id']}/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {conf['token']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode("utf-8", "replace"))
    return template, (body.get("messages") or [{}])[0].get("id")


def send_due(conf, bdays, log, dry=False, force=None):
    """Send to everyone whose birthday is today (or just `force`). Returns (ok, failed)."""
    now = now_in(conf["tz"])
    sent = read_json(SENT_FILE, {})
    targets = [force] if force else [b for b in bdays if is_birthday(b, now)]
    log(f"Date {now:%Y-%m-%d} | {len(targets)} contact(s) to handle")
    ok = failed = 0
    for b in targets:
        key = f"{b['id']}-{now.year}"
        if not force and key in sent:
            log(f"- {b['name']}: already sent this year, skipped")
            continue
        if dry:
            log(f"- {b['name']} ({b['tone']}): would send to {b['phone']}")
            continue
        try:
            template, mid = send_wish(conf, b)
            ok += 1
            if not force:
                sent[key] = datetime.now().isoformat(timespec="seconds")
            log(f"- {b['name']}: sent with template '{template}'")
        except Exception as e:
            failed += 1
            log(f"- {b['name']}: FAILED {e}")
        time.sleep(1)
    if not dry:
        write_json(SENT_FILE, sent)
    log(f"Done. Sent {ok}, failed {failed}." if not dry else "Preview finished. Nothing was sent.")
    return ok, failed


# ---------- desktop window ----------
def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("Birthday Wishes")
            self.geometry("860x620")
            self.minsize(720, 500)
            self.conf = load_conf()
            self.bdays = load_bdays()
            self.q = queue.Queue()
            self.busy = False
            self.last_auto = None
            self.build()
            self.refresh()
            self.poll()
            self.tick()

        # layout
        def build(self):
            bar = ttk.Frame(self, padding=(10, 10, 10, 4))
            bar.pack(fill="x")
            for text, cmd in [("Add birthday", self.add), ("Edit", self.edit), ("Delete", self.delete),
                              ("Import Wish Desk backup", self.import_backup), ("Settings", self.settings)]:
                ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6))

            cols = ("name", "phone", "date", "tone", "next")
            self.tree = ttk.Treeview(self, columns=cols, show="headings", height=10, selectmode="browse")
            for c, label, w in [("name", "Name", 200), ("phone", "WhatsApp number", 150), ("date", "Birthday", 110),
                                ("tone", "Tone", 90), ("next", "Next birthday", 180)]:
                self.tree.heading(c, text=label)
                self.tree.column(c, width=w, anchor="w")
            self.tree.pack(fill="both", expand=True, padx=10, pady=4)
            self.tree.bind("<Double-1>", lambda e: self.edit())

            self.status = ttk.Label(self, font=("Segoe UI", 11, "bold"), padding=(10, 6))
            self.status.pack(fill="x")

            act = ttk.Frame(self, padding=(10, 0, 10, 6))
            act.pack(fill="x")
            ttk.Button(act, text="Preview today", command=lambda: self.run_worker(dry=True)).pack(side="left", padx=(0, 6))
            ttk.Button(act, text="Send today's wishes", command=self.send_today).pack(side="left", padx=(0, 6))
            ttk.Button(act, text="Send test to selected", command=self.send_test).pack(side="left")

            self.logbox = tk.Text(self, height=9, state="disabled", wrap="word")
            self.logbox.pack(fill="both", expand=False, padx=10, pady=(0, 10))

        def log(self, msg):
            self.logbox.config(state="normal")
            self.logbox.insert("end", f"[{datetime.now():%H:%M:%S}] {msg}\n")
            self.logbox.see("end")
            self.logbox.config(state="disabled")

        def refresh(self):
            today = now_in(self.conf["tz"]).date()
            rows = sorted(self.bdays, key=lambda b: next_birthday(b, today))
            self.tree.delete(*self.tree.get_children())
            names_today = []
            for b in rows:
                n = next_birthday(b, today)
                days = (n - today).days
                when = "TODAY" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
                if days == 0:
                    names_today.append(b["name"])
                self.tree.insert("", "end", iid=b["id"],
                                 values=(b["name"], b["phone"], b["date"], b["tone"], f"{n:%d %b} ({when})"))
            self.status.config(text=("Today: " + ", ".join(names_today)) if names_today
                               else "No birthdays today.")

        def selected(self):
            sel = self.tree.selection()
            return next((b for b in self.bdays if b["id"] == sel[0]), None) if sel else None

        # birthday form
        def form(self, b=None):
            win = tk.Toplevel(self)
            win.title("Edit birthday" if b else "Add birthday")
            win.transient(self)
            win.grab_set()
            f = ttk.Frame(win, padding=14)
            f.pack()
            vals = {k: tk.StringVar(value=(b or {}).get(k, d)) for k, d in
                    [("name", ""), ("phone", ""), ("date", ""), ("tone", "friend")]}
            rows = [("Name", "name"), ("WhatsApp number (e.g. 94771234567)", "phone"),
                    ("Birthday (YYYY-MM-DD, or MM-DD)", "date")]
            for i, (label, k) in enumerate(rows):
                ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=4)
                ttk.Entry(f, textvariable=vals[k], width=32).grid(row=i, column=1, padx=8)
            ttk.Label(f, text="Tone").grid(row=3, column=0, sticky="w", pady=4)
            ttk.Combobox(f, textvariable=vals["tone"], values=TONES, state="readonly", width=29).grid(row=3, column=1, padx=8)

            def save():
                try:
                    name = vals["name"].get().strip()
                    phone = re.sub(r"\D", "", vals["phone"].get())
                    if not name or len(phone) < 8:
                        raise ValueError("Enter a name and a WhatsApp number with country code.")
                    d = parse_date(vals["date"].get())
                except ValueError as e:
                    messagebox.showerror("Check the form", str(e) if "Enter" in str(e)
                                         else "Birthday must look like 1990-05-21 or 05-21.", parent=win)
                    return
                rec = b or {"id": uuid.uuid4().hex[:8]}
                rec.update(name=name, phone=phone, date=d, tone=vals["tone"].get())
                if b is None:
                    self.bdays.append(rec)
                save_bdays(self.bdays)
                self.refresh()
                win.destroy()

            ttk.Button(f, text="Save", command=save).grid(row=4, column=1, sticky="e", pady=(10, 0))

        def add(self):
            self.form()

        def edit(self):
            b = self.selected()
            if b:
                self.form(b)
            else:
                messagebox.showinfo("Edit", "Select a birthday in the list first.")

        def delete(self):
            b = self.selected()
            if b and messagebox.askyesno("Delete", f"Delete {b['name']}?"):
                self.bdays.remove(b)
                save_bdays(self.bdays)
                self.refresh()

        def import_backup(self):
            path = filedialog.askopenfilename(title="Wish Desk backup", filetypes=[("JSON", "*.json"), ("All", "*.*")])
            if not path:
                return
            data = read_json(path, None)
            if not data or not isinstance(data.get("bdays"), list):
                messagebox.showerror("Import", "That file doesn't contain a birthdays list.")
                return
            have = {(re.sub(r"\D", "", b["phone"]), b["date"]) for b in self.bdays}
            added = 0
            for b in data["bdays"]:
                try:
                    rec = {"id": b.get("id") or uuid.uuid4().hex[:8], "name": b["name"],
                           "phone": re.sub(r"\D", "", b["phone"]), "date": parse_date(b["date"]),
                           "tone": b.get("tone") if b.get("tone") in TONES else "friend"}
                except (KeyError, ValueError):
                    continue
                if (rec["phone"], rec["date"]) not in have:
                    self.bdays.append(rec)
                    added += 1
            save_bdays(self.bdays)
            self.refresh()
            self.log(f"Imported {added} birthday(s).")

        # settings
        def settings(self):
            win = tk.Toplevel(self)
            win.title("Settings")
            win.transient(self)
            win.grab_set()
            f = ttk.Frame(win, padding=14)
            f.pack()
            c = self.conf
            sv = {k: tk.StringVar(value=c[k]) for k in ["token", "phone_id", "template", "lang", "tz", "send_time"]}
            tv = {t: tk.StringVar(value=c["tone_templates"].get(t, "")) for t in TONES}
            auto = tk.BooleanVar(value=c["auto_send"])
            rows = [("Access token", "token"), ("Phone number ID", "phone_id"), ("Default template name", "template"),
                    ("Template language", "lang"), ("Time zone", "tz"), ("Auto-send time (HH:MM)", "send_time")]
            for i, (label, k) in enumerate(rows):
                ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=3)
                ttk.Entry(f, textvariable=sv[k], width=44, show="*" if k == "token" else "").grid(row=i, column=1, padx=8)
            r = len(rows)
            ttk.Label(f, text="Optional template per tone (blank = default)").grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 2))
            for j, t in enumerate(TONES):
                ttk.Label(f, text=t).grid(row=r + 1 + j, column=0, sticky="w", pady=2)
                ttk.Entry(f, textvariable=tv[t], width=44).grid(row=r + 1 + j, column=1, padx=8)
            ttk.Checkbutton(f, text="Send automatically while this app is open (after the time above)",
                            variable=auto).grid(row=r + 6, column=0, columnspan=2, sticky="w", pady=(10, 0))

            def save():
                if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", sv["send_time"].get().strip()):
                    messagebox.showerror("Settings", "Time must look like 08:00.", parent=win)
                    return
                for k in sv:
                    self.conf[k] = sv[k].get().strip()
                self.conf["tone_templates"] = {t: tv[t].get().strip() for t in TONES}
                self.conf["auto_send"] = auto.get()
                write_json(CONF_FILE, self.conf)
                self.refresh()
                win.destroy()

            ttk.Button(f, text="Save", command=save).grid(row=r + 7, column=1, sticky="e", pady=(10, 0))

        # sending
        def run_worker(self, dry=False, force=None):
            if self.busy:
                self.log("Already working, please wait.")
                return
            self.busy = True
            conf, bdays = dict(self.conf), list(self.bdays)

            def work():
                try:
                    send_due(conf, bdays, self.q.put, dry=dry, force=force)
                except Exception as e:
                    self.q.put(f"ERROR {e}")
                self.q.put("__done__")

            threading.Thread(target=work, daemon=True).start()

        def send_today(self):
            now = now_in(self.conf["tz"])
            due = [b for b in self.bdays if is_birthday(b, now)]
            if not due:
                messagebox.showinfo("Send", "No birthdays today.")
                return
            names = "\n".join(f"- {b['name']} ({b['tone']})" for b in due)
            if messagebox.askyesno("Review before sending", f"Send birthday wishes to:\n\n{names}\n\nSend now?"):
                self.run_worker()

        def send_test(self):
            b = self.selected()
            if not b:
                messagebox.showinfo("Test", "Select a person in the list first.")
                return
            if messagebox.askyesno("Send test", f"Send a birthday message to {b['name']} ({b['phone']}) right now?"):
                self.run_worker(force=b)

        def poll(self):
            try:
                while True:
                    msg = self.q.get_nowait()
                    if msg == "__done__":
                        self.busy = False
                    else:
                        self.log(msg)
            except queue.Empty:
                pass
            self.after(150, self.poll)

        def tick(self):
            if self.conf["auto_send"] and not self.busy:
                now = now_in(self.conf["tz"])
                if now.strftime("%H:%M") >= self.conf["send_time"] and self.last_auto != now.date():
                    self.last_auto = now.date()
                    self.log("Auto-send started.")
                    self.run_worker()
            self.after(30000, self.tick)

    App().mainloop()


if __name__ == "__main__":
    if "--auto" in sys.argv:
        c = load_conf()
        ok, bad = send_due(c, load_bdays(), print, dry="--dry-run" in sys.argv)
        sys.exit(1 if bad else 0)
    run_gui()
