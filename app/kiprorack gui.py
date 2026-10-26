"""
kiprorack_gui.py - AJA Ki Pro RACK setup utility (standalone; does not use akpi.py).

Runs the Ki Pro Rack SOP 4.0 on the Racks you select:
  4.1 Record-Play   4.2 encode type   4.3 clip number 1   4.4 take number 1
  4.5 custom clip name (from the CSV)
  4.6 format slot 1 -> 4.7 verify 99%, change to slot 2 -> 4.8 format slot 2
  -> 4.9 verify 99%, set back to slot 1

Inventory CSV (same file as the Ultra app):   DVR_IP,CLIP_NAME

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
# SOP steps (each takes a Rack and returns a message, or raises RackError)
# ---------------------------------------------------------------------------
def step_setup(r, encode):
    r.set("eParamID_MediaState", 0)                         # 4.1
    r.set("eParamID_EncodeType", encode)                    # 4.2
    r.set("eParamID_ClipNumber", 1)                         # 4.3
    r.set("eParamID_CustomTake", 1)                         # 4.4
    if not r.csv_clip_name:                                 # 4.5
        raise RackError("no clip name in the CSV for this unit")
    r.set("eParamID_CustomClipName", r.csv_clip_name)
    r.refresh()
    return f"set: Record-Play, encode {r.encoding}, clip 1, take {r.take}, name {r.clip_name}"


def step_format(r, idx):
    r.goto_slot(Rack.CYCLE[idx])
    r.format_current()
    return f"{Rack.CYCLE[idx]} formatted"


def step_verify(r):
    r.refresh()
    msg = f"{r.slot}: {r.pct_raw}% available, {len(r.clips)} clip(s)"
    if r.pct < 99:
        raise RackError(msg + " (expected 99%)")
    return msg


def step_goto(r, idx):
    r.goto_slot(Rack.CYCLE[idx])
    return f"on {r.slot}"


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
    return {ip: f.result() for ip, f in futs.items()}


def run_sop(units, encode, confirm, setup_only=False):
    """confirm(message) -> bool is asked at the pause points; No stops the run."""
    logs = {u.ip: [] for u in units}
    active = list(units)
    stopped = None
    stop_units = []

    def record(label, results, drop_failed=True):
        nonlocal active
        keep = []
        for u in active:
            ok, msg = results[u.ip]
            logs[u.ip].append((label, ok, msg))
            log(f"[{u.name}] {label}: {msg}", "ok" if ok else "err")
            if ok or not drop_failed:
                keep.append(u)
        active = keep

    def names(lst, limit=12):
        n = [u.name for u in lst]
        return ", ".join(n[:limit]) + (f" (+{len(n) - limit} more)" if len(n) > limit else "")

    log("SOP 4.1-4.5: media state, encode type, clip/take number, clip name", "warn")
    before = list(active)
    record("4.1-4.5 setup", phase(active, lambda u: step_setup(u, encode)))
    failed = [u for u in before if u not in active]

    if setup_only:
        active = []
    elif failed and active:
        if not confirm(f"Setup (4.1-4.5) FAILED on {len(failed)} unit(s):\n{names(failed)}\n\n"
                       f"Details are in the Console tab. Those units are skipped.\n\n"
                       f"Continue and format the remaining {len(active)}?"):
            stopped, stop_units, active = "after setup", list(active), []

    if active:
        log("SOP 4.6: formatting slot 1 (D1)", "warn")
        record("4.6 format D1", phase(active, lambda u: step_format(u, 0)))

    if active:
        ver = phase(active, step_verify)
        record("4.7 verify D1", ver, drop_failed=False)
        low = [u for u in active if not ver[u.ip][0]]
        msg = f"Slot 1 formatted on {len(active)} unit(s).\n"
        msg += "ALL show 99% available.\n" if not low else f"NOT at 99%: {names(low)}\n"
        msg += "\nContinue: change to slot 2 and format it (4.7 -> 4.8)?"
        if confirm(msg):
            log("SOP 4.7: changing to slot 2 (D2)", "warn")
            record("4.7 change to D2", phase(active, lambda u: step_goto(u, 1)))
        else:
            stopped, stop_units, active = "after slot 1 format", list(active), []

    if active:
        log("SOP 4.8: formatting slot 2 (D2)", "warn")
        record("4.8 format D2", phase(active, lambda u: step_format(u, 1)))

    if active:
        record("4.9 verify D2", phase(active, step_verify), drop_failed=False)
        log("SOP 4.9: setting back to slot 1 (D1)", "warn")
        record("4.9 back to D1", phase(active, lambda u: step_goto(u, 0)), drop_failed=False)

    for u in units:
        entries = logs[u.ip]
        if stopped and u in stop_units:
            entries.append(("stopped", False, f"stopped by user {stopped}"))
        done_label = "4.1-4.5 setup" if setup_only else "4.9 back to D1"
        completed = any(e[0] == done_label for e in entries)
        u.format_failed = (not completed) or any(not e[1] for e in entries)
        u.format_result = "; ".join(f"{e[0]}: {'ok' if e[1] else 'FAILED'} ({e[2]})" for e in entries) or "not run"
    try:
        for u in units:
            u.refresh()
    except RackError:
        pass
    return units


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
    def __init__(self, app, units):
        super().__init__(app)
        self.title("Rack SOP Results")
        self.geometry("760x420")
        ttk.Button(self, text="Close", command=self.destroy).pack(side="bottom", pady=(0, 12))
        bad = sum(1 for u in units if u.format_failed)
        ttk.Label(self, text=f"{len(units) - bad} of {len(units)} completed"
                             + (f"  -  {bad} NEED ATTENTION" if bad else ""),
                  font=("", 10, "bold")).pack(side="bottom", pady=8)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        tree = ttk.Treeview(frame, columns=("n", "r"), show="headings")
        tree.heading("n", text="Rack")
        tree.heading("r", text="Result")
        tree.column("n", width=110, anchor="w")
        tree.column("r", width=600, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.tag_configure("ok", foreground="#2e7d32")
        tree.tag_configure("fail", foreground="#c62828")
        for u in units:
            tree.insert("", "end", values=(u.name, u.format_result),
                        tags=("fail" if u.format_failed else "ok",))
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")


class SopDialog(tk.Toplevel):
    def __init__(self, app, units):
        super().__init__(app)
        self.app, self.units = app, units
        self.title("Rack Setup (SOP 4.0) - DATA LOSS WARNING")
        self.geometry("580x600")
        self.minsize(480, 460)

        b = ttk.Frame(self)
        b.pack(side="bottom", pady=14)
        ttk.Button(b, text="Run Full SOP 4.1-4.9", command=lambda: self._go(False)).pack(side="left", padx=6)
        ttk.Button(b, text="Setup Only 4.1-4.5 (no format)", command=lambda: self._go(True)).pack(side="left", padx=6)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        tk.Label(self, text="FULL SOP FORMATS BOTH DRIVES ON EVERY LISTED RACK",
                 fg="#c62828", font=("", 11, "bold")).pack(pady=(14, 4))
        ttk.Label(self, justify="left",
                  text="4.1-4.5: Record-Play, encode type, clip 1, take 1, clip name from CSV.\n"
                       "4.6-4.9: format slot 1, verify 99%, slot 2, format, verify, back to slot 1.\n"
                       "It pauses for your OK after slot 1 is verified.").pack(anchor="w", padx=14)
        ttk.Label(self, text="Encode type:", font=("", 10, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        self.enc = tk.StringVar(value="")
        ttk.Combobox(self, textvariable=self.enc, state="readonly",
                     values=[c[0] for c in ENCODE_CHOICES]).pack(fill="x", padx=14)
        ttk.Label(self, text=f"{len(units)} Rack(s)  -  unit  ->  clip name that will be set:",
                  font=("", 10, "bold")).pack(anchor="w", padx=14, pady=(10, 0))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=14, pady=4)
        lb = tk.Listbox(frame, selectmode="none", activestyle="none")
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        for i, u in enumerate(units):
            if u.csv_clip_name:
                lb.insert("end", f"{u.name}  ->  {u.csv_clip_name}")
            else:
                lb.insert("end", f"{u.name}  ->  (NO CLIP NAME IN CSV - will be skipped)")
                lb.itemconfig(i, fg="#c62828")
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _go(self, setup_only):
        choice = self.enc.get()
        if not choice:
            messagebox.showwarning("Pick an encode type", "Choose HQ (launch) or PX (WDR) first.", parent=self)
            return
        encode = dict(ENCODE_CHOICES)[choice]
        if not setup_only and not messagebox.askyesno(
                "Format both drives?",
                f"This ERASES slot 1 and slot 2 on {len(self.units)} Rack(s).\n\nContinue?", parent=self):
            return
        app = self.app
        app.run_job("Rack SOP", run_sop, self.units, encode,
                    lambda m: call_on_main(app, lambda: messagebox.askyesno("Rack SOP - continue?", m)),
                    setup_only, on_done=lambda r: (app.refresh_table(), ResultsDialog(app, r)))
        self.destroy()


COLUMNS = [("name", "Name"), ("ip", "IP"), ("csv_clip_name", "Clip Name (CSV)"), ("mode", "Mode"),
           ("encoding", "Encoding"), ("state", "State"), ("slot", "Slot"), ("pct_raw", "Media %"),
           ("take", "Take #")]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AJA Ki Pro Rack Setup Utility")
        self.geometry("1150x640")
        self.units = []
        self.buttons = []

        side = ttk.Frame(self, width=230)
        side.pack(side="right", fill="y", padx=8, pady=8)
        ttk.Label(side, text="AJA Ki Pro RACK", font=("", 13, "bold")).pack(pady=(0, 12))
        ttk.Button(side, text="Load Inventory CSV", command=self.load).pack(fill="x", pady=3)
        ttk.Separator(side).pack(fill="x", pady=8)
        ttk.Button(side, text="Select All", command=lambda: self.tree.selection_set(self.tree.get_children())).pack(fill="x", pady=3)
        ttk.Button(side, text="Clear Selection", command=lambda: self.tree.selection_remove(self.tree.get_children())).pack(fill="x", pady=3)
        ttk.Separator(side).pack(fill="x", pady=8)
        for text, cmd in (("Refresh Status", self.refresh_job), ("Rack Setup (SOP 4.0)", self.open_sop)):
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
        self.nb.add(tf, text="Racks")

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
            if on_done:
                self.after(0, lambda: on_done(result) if result is not None else None)
        threading.Thread(target=worker, daemon=True).start()

    def refresh_table(self):
        keep = {u.ip for u in self.selected()}
        self.tree.delete(*self.tree.get_children())
        sel = []
        for u in self.units:
            iid = self.tree.insert("", "end", values=[getattr(u, k, "") or "" for k, _ in COLUMNS])
            if u.ip in keep:
                sel.append(iid)
        if sel:
            self.tree.selection_set(sel)
        self.nb.select(0)

    # -- actions -----------------------------------------------------------
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
        targets = self.selected()
        if not targets:
            messagebox.showwarning("No Racks selected", "Select at least one Rack (or click Select All) first.")
            return

        def refresh(units):
            for u in units:
                try:
                    u.refresh()
                except RackError as e:
                    log(str(e), "err")
            return units
        self.run_job("Refresh", refresh, targets, on_done=lambda r: self.refresh_table())

    def open_sop(self):
        targets = self.selected()
        if not targets:
            messagebox.showwarning("No Racks selected", "Select at least one Rack (or click Select All) first.")
            return
        SopDialog(self, targets)


if __name__ == "__main__":
    App().mainloop()
