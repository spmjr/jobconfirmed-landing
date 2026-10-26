"""
kiprorack_gui.py - AJA Ki Pro RACK (DVRA) control utility. Standalone; does not use akpi.py.

Same style as the Ultra app, for Racks only:
  - Load inventory CSV (DVR_IP,CLIP_NAME); status table + Live View popup
  - Change Recorder Settings: mode, encoding, slot, clip names, take numbers
  - Format Drives: Both / D1 / D2 / Current slot (swaps and swaps back)
Every action applies only to the Racks you SELECT - nothing defaults to "all".

Run:  python3 kiprorack_gui.py        Needs: pip install requests
"""

import csv
import json
import queue
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

import requests

HTTP_TIMEOUT = 5
FORMAT_TIMEOUT = 180          # max seconds to wait for a format to finish
FORMAT_MIN_WAIT = 20          # always wait at least this long after sending format
SLOT_TIMEOUT = 120            # max seconds to wait for one slot toggle to land
POLL = 3
ENCODE_CHOICES = [("ProRes 422 HQ  (launch)", "1"), ("ProRes 422 PX  (WDR)", "4")]


# ---------------------------------------------------------------------------
# Logging (thread-safe; the GUI drains this queue into the Console tab)
# ---------------------------------------------------------------------------
LOG_Q = queue.Queue()


def log(msg, tag="info"):
    stamp = time.strftime("%H:%M:%S")
    LOG_Q.put((f"{stamp} {msg}\n", tag))
    print(f"{stamp} [{tag.upper()}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Rack device
# ---------------------------------------------------------------------------
class RackError(Exception):
    pass


class Rack:
    CYCLE = ["D1", "D2", "No Slot"]   # order the slot toggle walks through

    def __init__(self, ip, clip_name=None):
        self.ip = ip
        self.csv_clip_name = clip_name or None
        self.format_failed = None
        self.format_result = None
        self._check_is_rack()
        self.refresh()

    # -- low level ---------------------------------------------------------
    def _get(self, path, params=None):
        try:
            r = requests.get(f"http://{self.ip}/{path}", params=params, timeout=HTTP_TIMEOUT)
            return r.text
        except requests.exceptions.RequestException as e:
            raise RackError(f"{self.ip}: {e}")

    def _json(self, path, params):
        txt = self._get(path, params)
        try:
            return json.loads(txt)
        except ValueError:
            raise RackError(f"{self.ip}: invalid response")

    def _check_is_rack(self):
        page = self._get("index.html")
        if "Ki Pro Rack" not in page:
            raise RackError(f"{self.ip}: not a Ki Pro Rack (this app is for Racks only)")

    def read(self, pid, key="value", default=""):
        resp = self._json("config", {"action": "get", "paramid": pid})
        val = resp.get(key)
        return default if val is None else val

    def set(self, pid, value, strict=True):
        """Set a value. strict: also require the unit to echo the same value."""
        resp = self._json("config", {"action": "set", "paramid": pid, "value": str(value)})
        if strict and str(resp.get("value")) != str(value):
            raise RackError(f"{self.ip}: {pid} not applied (unit answered {resp})")

    # -- state -------------------------------------------------------------
    def refresh(self):
        self.name = self.read("eParamID_SysName")
        self.clip_name = self.read("eParamID_CustomClipName")
        self.mode = self.read("eParamID_MediaState", "value_name")
        self.take = self.read("eParamID_CustomTake", "value_name")
        self.encoding = self.read("eParamID_EncodeType", "value_name")
        self.state = self.read("eParamID_TransportState", "value_name")
        self.slot = self.read("eParamID_SelectedSlot", "value_name")
        self.loading = self.read("eParamID_MediaLoading", "value_name")
        self.pct_raw = self.read("eParamID_CurrentMediaAvailable")
        resp = self._json("clips", {"action": "get_playlists"})
        try:
            self.clips = resp["playlists"][0]["playlist"]["cliplist"]
        except (KeyError, IndexError, TypeError):
            self.clips = []

    @property
    def pct(self):
        try:
            return int(str(self.pct_raw).strip().rstrip("%"))
        except ValueError:
            return -1

    # -- slot / format -----------------------------------------------------
    def goto_slot(self, target):
        """Toggle until the active slot == target, verifying after each toggle.
        The toggle walks D1 -> D2 -> No Slot -> D1 (assumed from the Ultra; the
        verify-after-each-toggle means a different cycle still works)."""
        self.refresh()
        if self.slot == target:
            return
        for attempt in range(1, len(self.CYCLE) + 1):
            prev = self.slot
            self.set("eParamID_ChangeSlot", 6, strict=False)
            waited = 0
            while waited < SLOT_TIMEOUT:
                time.sleep(2)
                waited += 2
                self.refresh()
                if self.slot != prev:
                    break
            else:
                raise RackError(f"{self.name}: slot toggle did not register within {SLOT_TIMEOUT}s (still {self.slot})")
            if self.slot == target:
                log(f"[{self.name}] now on {self.slot}", "ok")
                return
        raise RackError(f"{self.name}: could not reach {target} (on {self.slot})")

    def format_current(self):
        """Format the active slot (StorageCommand=4, same as the web GUI's Format
        Media). Completion = after a minimum wait: 'Operation Complete', a slot
        assigned, no clips, 99% available, two polls in a row."""
        log(f"[{self.name}] formatting {self.slot} ...", "warn")
        self.set("eParamID_StorageCommand", 4, strict=False)
        time.sleep(FORMAT_MIN_WAIT)
        waited, good = FORMAT_MIN_WAIT, 0
        while waited < FORMAT_TIMEOUT:
            self.refresh()
            done = (self.loading.strip() == "Operation Complete" and self.slot in ("D1", "D2")
                    and not self.clips and self.pct >= 99)
            log(f"[{self.name}] format t+{waited}s slot={self.slot} loading={self.loading} "
                f"media%={self.pct_raw} clips={len(self.clips)} done={done}", "debug")
            good = good + 1 if done else 0
            if good >= 2:
                log(f"[{self.name}] format complete ({self.slot}, {self.pct_raw}% available)", "ok")
                return
            time.sleep(POLL)
            waited += POLL
        raise RackError(f"{self.name}: format not complete after {FORMAT_TIMEOUT}s "
                        f"(slot={self.slot}, loading={self.loading}, {self.pct_raw}%, {len(self.clips)} clip(s)) - verify manually")


# ---------------------------------------------------------------------------
# Actions (each takes a Rack, returns a message, or raises RackError)
# ---------------------------------------------------------------------------
def act_mode(u, value):
    u.set("eParamID_MediaState", value)
    u.refresh()
    return f"mode is now {u.mode}"


def act_encode(u, value):
    u.set("eParamID_EncodeType", value)
    u.refresh()
    return f"encoding is now {u.encoding}"


def act_slot(u, target):
    u.goto_slot(target)
    return f"on {u.slot}"


def act_next_slot(u):
    prev = u.slot
    u.set("eParamID_ChangeSlot", 6, strict=False)
    waited = 0
    while waited < SLOT_TIMEOUT:
        time.sleep(2)
        waited += 2
        u.refresh()
        if u.slot != prev:
            return f"{prev} -> {u.slot}"
    raise RackError(f"{u.name}: slot did not change within {SLOT_TIMEOUT}s (still {u.slot})")


def act_clip_name(u, name):
    if not name:
        raise RackError("no clip name for this unit")
    u.set("eParamID_CustomClipName", name)
    u.csv_clip_name = name
    u.refresh()
    return f"clip name is now {u.clip_name}"


def act_take(u, value):
    u.set("eParamID_CustomTake", value)
    u.refresh()
    return f"take number is now {u.take}"


def act_format(u, target):
    """target: both / D1 / D2 / current. Swaps as needed, formats, swaps back."""
    u.refresh()
    original = u.slot
    done = []
    if target == "current":
        if original not in ("D1", "D2"):
            raise RackError(f"no media in the active slot ({original}) - nothing to format")
        u.format_current()
        done.append(original)
    else:
        for slot in (["D1", "D2"] if target == "both" else [target]):
            u.goto_slot(slot)
            u.format_current()
            done.append(slot)
        if original in ("D1", "D2") and u.slot != original:
            try:
                u.goto_slot(original)
            except RackError as e:
                raise RackError(f"formatted {'+'.join(done)} but could not swap back to {original}: {e} - "
                                f"unit is on {u.slot}, verify manually")
    return f"formatted {'+'.join(done)}, on {u.slot}"


def phase(units, fn):
    """Run fn(unit) on all units in parallel. Returns {ip: (ok, message)}; never raises."""
    def safe(u):
        try:
            return True, fn(u)
        except RackError as e:
            return False, str(e)
        except Exception as e:           # keep one bad unit from killing the run
            return False, f"unexpected error: {e}"
    with ThreadPoolExecutor(max_workers=max(1, len(units))) as ex:
        futs = {u.ip: ex.submit(safe, u) for u in units}
    out = {ip: f.result() for ip, f in futs.items()}
    for u in units:
        ok, msg = out[u.ip]
        log(f"[{u.name}] {msg}", "ok" if ok else "err")
    return out


def run_action(units, fn):
    """Runs fn on every unit, refreshes them, returns [(name, ok, message)]."""
    res = phase(units, fn)
    for u in units:
        try:
            u.refresh()
        except RackError:
            pass
    return [(u.name, *res[u.ip]) for u in units]


# ---------------------------------------------------------------------------
# Loading the inventory (Retry / Skip / Cancel per unit)
# ---------------------------------------------------------------------------
def load_inventory(path, units, on_error):
    """on_error(ip, error) -> 'retry' | 'skip' | 'cancel'."""
    skipped = []
    with open(path, newline="") as f:
        rows = csv.reader(f)
        next(rows, None)                                   # header
        for row in rows:
            if not row or not row[0].strip():
                continue
            ip = row[0].strip()
            name = row[1].strip() if len(row) > 1 else None
            rack = None
            while True:
                try:
                    rack = Rack(ip, name)
                    break
                except RackError as e:
                    log(f"[{ip}] not loaded: {e}", "err")
                    choice = on_error(ip, e)
                    if choice == "retry":
                        continue
                    if choice == "cancel":
                        log(f"Load cancelled. {len(units)} unit(s) loaded before cancel.", "warn")
                        return units
                    skipped.append(ip)
                    break
            if rack:
                units.append(rack)
                log(f"[{rack.name}] {ip} connected", "ok")
    if skipped:
        log(f"{len(skipped)} unit(s) skipped: {', '.join(skipped)}", "warn")
    return units


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def call_on_main(app, fn):
    done, box = threading.Event(), {}

    def run():
        try:
            box["v"] = fn()
        except Exception:
            box["v"] = None
        finally:
            done.set()
    app.after(0, run)
    done.wait()
    return box.get("v")


class LoadErrorDialog(tk.Toplevel):
    def __init__(self, app, ip, error):
        super().__init__(app)
        self.result = "cancel"
        self.title("Rack Not Found")
        self.resizable(False, False)
        ttk.Label(self, text=f"Could not load {ip}", font=("", 11, "bold")).pack(padx=20, pady=(16, 6))
        ttk.Label(self, text=str(error), wraplength=420, foreground="#c62828").pack(padx=20, pady=(0, 10))
        ttk.Label(self, text="Retry: try again    Skip: leave it out    Cancel: stop loading\n"
                             "(units already loaded are kept)").pack(padx=20, pady=(0, 10))
        b = ttk.Frame(self)
        b.pack(pady=(0, 16))
        for text, val in (("Retry", "retry"), ("Skip", "skip"), ("Cancel", "cancel")):
            ttk.Button(b, text=text, command=lambda v=val: self._pick(v)).pack(side="left", padx=6)
        self.protocol("WM_DELETE_WINDOW", lambda: self._pick("cancel"))
        self.grab_set()

    def _pick(self, v):
        self.result = v
        self.destroy()


class ResultsDialog(tk.Toplevel):
    def __init__(self, app, title, rows):
        super().__init__(app)
        self.title(f"{title} - Results")
        self.geometry("700x400")
        self.minsize(420, 280)
        ttk.Button(self, text="Close", command=self.destroy).pack(side="bottom", pady=(0, 12))
        bad = sum(1 for r in rows if not r[1])
        ttk.Label(self, text=f"{len(rows) - bad} of {len(rows)} succeeded" + (f"  -  {bad} FAILED" if bad else ""),
                  font=("", 10, "bold")).pack(side="bottom", pady=8)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        tree = ttk.Treeview(frame, columns=("n", "r"), show="headings")
        tree.heading("n", text="Rack")
        tree.heading("r", text="Result")
        tree.column("n", width=110, anchor="w")
        tree.column("r", width=560, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.tag_configure("ok", foreground="#2e7d32")
        tree.tag_configure("fail", foreground="#c62828")
        for name, ok, msg in rows:
            tree.insert("", "end", values=(name, msg), tags=("ok" if ok else "fail",))
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")


class FormatDialog(tk.Toplevel):
    SLOT_CHOICES = {"Both (D1 then D2)": "both", "D1 only": "D1", "D2 only": "D2",
                    "Current slot (per Rack)": "current"}

    def __init__(self, app, units):
        super().__init__(app)
        self.app, self.units = app, units
        self.title("Format Drives - DATA LOSS WARNING")
        self.geometry("480x520")
        self.minsize(420, 380)

        b = ttk.Frame(self)
        b.pack(side="bottom", pady=14)
        ttk.Button(b, text="Format Selected Rack(s)", command=self._go).pack(side="left", padx=6)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        ttk.Label(self, text="This cannot be undone.", foreground="#c62828",
                  font=("", 9, "italic")).pack(side="bottom", pady=(4, 2))

        tk.Label(self, text="!!! WARNING WARNING WARNING !!!", fg="#c62828",
                 font=("", 11, "bold")).pack(pady=(14, 4))
        ttk.Label(self, text=f"This will format {len(units)} Rack(s):", font=("", 10, "bold")).pack(
            anchor="w", padx=14, pady=(6, 0))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=14, pady=(4, 10))
        lb = tk.Listbox(frame, height=8, fg="#c62828", selectmode="none", activestyle="none")
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        for u in units:
            lb.insert("end", f"{u.name}   (on {u.slot})")
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(self, text="Slot target:", font=("", 10, "bold")).pack(anchor="w", padx=14, pady=(4, 2))
        self.slot = tk.StringVar(value="Both (D1 then D2)")
        ttk.Combobox(self, textvariable=self.slot, state="readonly",
                     values=list(self.SLOT_CHOICES)).pack(fill="x", padx=14)
        ttk.Label(self, foreground="#8a6d00", font=("", 8), justify="left",
                  text="'Current' formats whichever slot each Rack is on. 'Both' formats D1, then D2,\n"
                       "then returns to the slot it started on. D1/D2 only switch first if needed,\n"
                       "then switch back afterwards.").pack(anchor="w", padx=14, pady=(2, 10))

    def _go(self):
        target = self.SLOT_CHOICES[self.slot.get()]
        app, units = self.app, self.units
        app.do_action("Format Drives", units, lambda u: act_format(u, target))
        self.destroy()


class ChangeSettingsDialog(tk.Toplevel):
    def __init__(self, app, units):
        super().__init__(app)
        self.app, self.units = app, units
        self.title(f"Change Recorder Settings ({len(units)} Rack(s))")
        self.geometry("400x520")
        self.resizable(False, False)

        def section(text):
            ttk.Label(self, text=text, font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 2))

        def row(*buttons):
            f = ttk.Frame(self)
            f.pack(fill="x", padx=12)
            for text, cmd in buttons:
                ttk.Button(f, text=text, command=cmd).pack(side="left", expand=True, fill="x")

        section("Mode")
        row(("Data-LAN Mode", lambda: self._run("Set Mode: Data-LAN", lambda u: act_mode(u, 1))),
            ("Record-Play Mode", lambda: self._run("Set Mode: Record-Play", lambda u: act_mode(u, 0))))
        section("Encoding")
        row(("High-Quality (ProRes 422 HQ)", lambda: self._run("Set Encoding: HQ", lambda u: act_encode(u, 1))),
            ("Proxy (ProRes 422 PX)", lambda: self._run("Set Encoding: PX", lambda u: act_encode(u, 4))))
        section("Storage Slot")
        row(("Go to D1", lambda: self._run("Go to D1", lambda u: act_slot(u, "D1"))),
            ("Go to D2", lambda: self._run("Go to D2", lambda u: act_slot(u, "D2"))),
            ("Next Slot (1 toggle)", lambda: self._run("Next Slot", act_next_slot)))
        section("Clip Names")
        row(("Apply Names from Inventory CSV", lambda: self._run(
            "Apply Clip Names", lambda u: act_clip_name(u, u.csv_clip_name))),)
        row(("Load New Clip Name CSV", self._new_csv),)
        section("Take Numbers")
        row(("Reset Take Numbers to 1", lambda: self._run("Reset Take Numbers", lambda u: act_take(u, 1))),)
        ttk.Button(self, text="Close", command=self.destroy).pack(pady=14)

    def _run(self, label, fn):
        self.app.do_action(label, self.units, fn)
        self.destroy()

    def _new_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")], parent=self)
        if not path:
            return
        names = {}
        with open(path, newline="") as f:
            rows = csv.reader(f)
            next(rows, None)
            for r in rows:
                if len(r) > 1 and r[0].strip():
                    names[r[0].strip()] = r[1].strip()
        todo = [u for u in self.units if u.ip in names]
        if not todo:
            messagebox.showinfo("No matches", "None of the selected Racks' IPs are in that CSV.", parent=self)
            return
        self._run("Change Clip Names", lambda u: act_clip_name(u, names.get(u.ip)))


class LiveViewDialog(tk.Toplevel):
    """Queries the Racks directly (the main table shows the inventory CSV's names)."""
    COLS = [("name", "Name"), ("ip", "IP"), ("clip_name", "Clip Name (live)"), ("mode", "Mode"),
            ("encoding", "Encoding"), ("state", "State"), ("slot", "Slot"), ("pct_raw", "Media %"),
            ("take", "Take #")]

    def __init__(self, app, units):
        super().__init__(app)
        self.app, self.units = app, units
        self.title("Live View - Actual Current Rack State")
        self.geometry("900x360")
        ttk.Label(self, foreground="#555555", font=("", 9),
                  text="Queried directly from the Racks. The main table shows your inventory CSV.").pack(
            anchor="w", padx=10, pady=(10, 4))
        self.tree = ttk.Treeview(self, columns=[c[0] for c in self.COLS], show="headings")
        for k, label in self.COLS:
            self.tree.heading(k, text=label)
            self.tree.column(k, width=95, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        b = ttk.Frame(self)
        b.pack(pady=(0, 10))
        ttk.Button(b, text="Refresh Live View", command=self._refresh).pack(side="left", padx=6)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="left", padx=6)
        self._populate()
        self._refresh()

    def _populate(self):
        self.tree.delete(*self.tree.get_children())
        for u in self.units:
            self.tree.insert("", "end", values=[getattr(u, k, "") or "" for k, _ in self.COLS])

    def _refresh(self):
        def work():
            for u in self.units:
                try:
                    u.refresh()
                except RackError as e:
                    log(str(e), "err")
            try:
                self.after(0, self._populate)
            except tk.TclError:
                pass
        threading.Thread(target=work, daemon=True).start()


COLUMNS = [("name", "Name"), ("ip", "IP"), ("clip", "Clip Name"), ("mode", "Mode"),
           ("encoding", "Encoding"), ("state", "State"), ("slot", "Slot"), ("pct_raw", "Media %"),
           ("take", "Take #")]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AJA Ki Pro Rack (DVRA) Interface Utility")
        self.geometry("1150x640")
        self.units = []
        self.buttons = []

        side = ttk.Frame(self, width=230)
        side.pack(side="right", fill="y", padx=8, pady=8)
        ttk.Label(side, text="AJA Ki Pro RACK", font=("", 13, "bold")).pack(pady=(0, 2))
        ttk.Label(side, text="DVRA Interface Utility").pack(pady=(0, 12))
        ttk.Button(side, text="Load Inventory CSV", command=self.load).pack(fill="x", pady=3)
        ttk.Button(side, text="Refresh / Show Status", command=self.refresh_job).pack(fill="x", pady=3)
        ttk.Separator(side).pack(fill="x", pady=8)
        ttk.Button(side, text="Select All Racks", command=lambda: self.tree.selection_set(self.tree.get_children())).pack(fill="x", pady=3)
        ttk.Button(side, text="Clear Selection", command=lambda: self.tree.selection_remove(self.tree.get_children())).pack(fill="x", pady=3)
        ttk.Button(side, text="Live View", command=self.open_live).pack(fill="x", pady=3)
        ttk.Separator(side).pack(fill="x", pady=8)
        for text, cmd in (("Change Recorder Settings", self.open_settings), ("Format Drives", self.open_format)):
            b = ttk.Button(side, text=text, command=cmd, state="disabled")
            b.pack(fill="x", pady=3)
            self.buttons.append(b)
        ttk.Separator(side).pack(fill="x", pady=8)
        ttk.Button(side, text="Quit", command=self.destroy).pack(fill="x")

        main = ttk.Frame(self)
        main.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        self.status = tk.StringVar(value="Load an inventory CSV to begin.")
        ttk.Label(main, textvariable=self.status).pack(side="bottom", anchor="w")
        self.nb = ttk.Notebook(main)
        self.nb.pack(fill="both", expand=True)

        tf = ttk.Frame(self.nb)
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended")
        for k, label in COLUMNS:
            self.tree.heading(k, text=label)
            self.tree.column(k, width=105, anchor="center")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.nb.add(tf, text="Rack Status")

        cf = ttk.Frame(self.nb)
        self.text = tk.Text(cf, wrap="word", state="disabled", height=10)
        csb = ttk.Scrollbar(cf, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=csb.set)
        for tag, color in (("ok", "#2e7d32"), ("warn", "#8a6d00"), ("err", "#c62828"), ("debug", "#777777")):
            self.text.tag_configure(tag, foreground=color)
        self.text.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self.nb.add(cf, text="Console Log")
        self.after(100, self._drain)

    def _drain(self):
        try:
            while True:
                msg, tag = LOG_Q.get_nowait()
                self.text.configure(state="normal")
                self.text.insert("end", msg, tag)
                self.text.see("end")
                self.text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain)

    # -- helpers -----------------------------------------------------------
    def selected(self):
        """Explicit selection only - never falls back to 'all'."""
        iids = self.tree.get_children()
        return [self.units[iids.index(i)] for i in self.tree.selection()]

    def need_selection(self):
        targets = self.selected()
        if not targets:
            messagebox.showwarning("No Racks selected",
                                   "Select at least one Rack in the table (or click 'Select All Racks') first.")
        return targets

    def set_busy(self, busy, label=""):
        self.status.set(f"Running: {label} ..." if busy else "Ready.")
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")

    def run_job(self, label, fn, *args, on_done=None):
        self.set_busy(True, label)

        def worker():
            try:
                result = fn(*args)
            except Exception as e:
                log(f"{label} failed: {e}", "err")
                result = None
            self.after(0, lambda: self.set_busy(False, label))
            if on_done and result is not None:
                self.after(0, lambda: on_done(result))
        threading.Thread(target=worker, daemon=True).start()

    def do_action(self, label, units, fn):
        self.run_job(label, run_action, units, fn,
                     on_done=lambda rows: (self.refresh_table(), ResultsDialog(self, label, rows)))

    def refresh_table(self):
        keep = {u.ip for u in self.selected()}
        self.tree.delete(*self.tree.get_children())
        sel = []
        for u in self.units:
            u.clip = u.csv_clip_name or u.clip_name      # table shows the CSV name; Live View shows the unit's
            iid = self.tree.insert("", "end", values=[getattr(u, k, "") or "" for k, _ in COLUMNS])
            if u.ip in keep:
                sel.append(iid)
        if sel:
            self.tree.selection_set(sel)
        self.nb.select(0)

    # -- buttons -----------------------------------------------------------
    def load(self):
        path = filedialog.askopenfilename(initialdir=".", title="Select inventory CSV",
                                          filetypes=(("CSV Files", "*.csv"),))
        if not path:
            return
        self.units = []

        def ask(ip, err):
            def show():
                d = LoadErrorDialog(self, ip, err)
                self.wait_window(d)
                return d.result
            return call_on_main(self, show) or "cancel"

        def done(units):
            self.units = units
            for b in self.buttons:
                b.configure(state="normal" if units else "disabled")
            self.refresh_table()
            log(f"Loaded {len(units)} Rack(s).", "ok" if units else "err")

        self.run_job("Load Inventory", load_inventory, path, self.units, ask, on_done=done)

    def refresh_job(self):
        if not self.units:
            messagebox.showinfo("No inventory", "Load an inventory CSV first.")
            return

        def refresh(units):
            for u in units:
                try:
                    u.refresh()
                except RackError as e:
                    log(str(e), "err")
            return units
        self.run_job("Refresh", refresh, self.units, on_done=lambda r: self.refresh_table())

    def open_live(self):
        t = self.need_selection()
        if t:
            LiveViewDialog(self, t)

    def open_settings(self):
        t = self.need_selection()
        if t:
            ChangeSettingsDialog(self, t)

    def open_format(self):
        t = self.need_selection()
        if t:
            FormatDialog(self, t)


if __name__ == "__main__":
    App().mainloop()
