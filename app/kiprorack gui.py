"""
kiprorack_gui.py - AJA Ki Pro RACK (DVRA) control utility. Standalone; does not use akpi.py.

Same style as the Ultra app, for Racks only:
  - Load inventory CSV (DVR_IP,CLIP_NAME); status table + Live View popup
  - Change Recorder Settings: mode, encoding, slot, clip names, take numbers
  - Format Drives: Both / D1 / D2 / Current slot (swaps and swaps back)
  - Make Stringouts / Download Stringouts (same /mediaedit calls as the Ultra app; unverified on Racks)
  - Browse Clips and Archive Clips (add clips straight from the browser)
Every action applies only to the Racks you SELECT - nothing defaults to "all".

Run:  python3 kiprorack_gui.py        Needs: pip install requests
"""

import csv
import json
import os
import queue
import re
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
        self.download_url = None         # set by Make Stringouts, used by Download Stringouts
        self.download_path = None
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
        self.storage_path = self.read("eParamID_StoragePath")       # e.g. /mnt/D1/AJA
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


STRINGOUT_TIMEOUT = 180


def _parse_job(text):
    """/mediaedit answers either JSON or 'key: value' lines. Returns a dict."""
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            return d
    except ValueError:
        pass
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip(",")
    return out


def _mediaedit(u, query):
    try:
        r = requests.get(f"http://{u.ip}/mediaedit?{query}", timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise RackError(f"{u.ip}: {e}")
    return _parse_job(r.text), r.text


def act_data_lan(u):
    u.set("eParamID_MediaState", 1)


def act_stringout(u, suffix, start_tc, dur_tc, download_dir, prefix_name=False):
    """Create a stringout (sub-clip) from <clip><suffix>.mov, named <clip>.mov,
    on the Rack's active slot. Same /mediaedit subclip call the Ultra app uses.
    NOTE: not yet verified on a real Rack."""
    u.refresh()
    act_data_lan(u)
    base = (u.storage_path or f"/mnt/{u.slot}/AJA").rstrip("/") + "/"
    clip = u.clip_name
    src = (base + clip + suffix + ".mov").replace("+", "%2b")
    dst = (base + clip + ".mov").replace("+", "%2b")
    job, raw = _mediaedit(u, f"action=subclip&arg1={src}&arg2={dst}&arg3={start_tc}&arg4={dur_tc}")
    if str(job.get("error", "none")) != "none" or "id" not in job:
        raise RackError(f"stringout job did not start: {raw.strip()[:200]}")
    job_id = job["id"]
    waited = 0
    while waited < STRINGOUT_TIMEOUT:
        time.sleep(3)
        waited += 3
        st, raw = _mediaedit(u, f"action=status&id={job_id}")
        status = str(st.get("status", ""))
        if status == "Completed":
            break
        if status.lower() in ("failed", "error", "aborted"):
            raise RackError(f"stringout job {status}: {raw.strip()[:200]}")
    else:
        raise RackError(f"stringout not complete after {STRINGOUT_TIMEOUT}s (last status: {status or 'none'})")
    fname = f"{u.name}_{clip}.mov" if prefix_name else f"{clip}.mov"
    u.download_url = f"http://{u.ip}/media/{clip}.mov"
    u.download_path = os.path.join(download_dir, fname)
    return f"stringout {clip}.mov created ({suffix}, start {start_tc}, dur {dur_tc}). Rack is in Data-LAN mode"


class NotOnRack(RackError):
    pass


def _download_file(u, url, path):
    """Stream url -> path with size check and up to 3 resume attempts.
    Raises NotOnRack if the file isn't on the Rack. Returns bytes written."""
    try:
        head = requests.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if head.status_code >= 400:
            raise NotOnRack(f"{os.path.basename(path)} not found on the Rack")
        total = int(head.headers["content-length"])
    except requests.exceptions.RequestException as e:
        raise RackError(f"{u.ip}: {e}")
    except (KeyError, ValueError):
        raise NotOnRack(f"{os.path.basename(path)} not found on the Rack")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    got, last_pct = 0, -10
    for attempt in range(1, 4):
        headers = {"Range": f"bytes={got}-"} if got else {}
        try:
            with requests.get(url, stream=True, headers=headers, timeout=30) as r:
                with open(path, "ab" if got else "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        got += len(chunk)
                        pct = int(got * 100 / total) if total else 100
                        if pct >= last_pct + 10:
                            last_pct = pct
                            log(f"[{u.name}] {os.path.basename(path)} {pct}%", "info")
        except requests.exceptions.RequestException as e:
            log(f"[{u.name}] download interrupted ({e}); attempt {attempt}/3", "warn")
        got = os.path.getsize(path) if os.path.exists(path) else 0
        if got >= total:
            return got
    raise RackError(f"{os.path.basename(path)} incomplete: {got} of {total} bytes")


def act_download(u):
    """Download the stringout made by Make Stringouts to this Rack's chosen folder."""
    if not u.download_url:
        raise RackError("no stringout made yet this session - use Make Stringouts first")
    act_data_lan(u)
    got = _download_file(u, u.download_url, u.download_path)
    return f"downloaded {os.path.basename(u.download_path)} ({got} bytes) to {os.path.dirname(u.download_path)}"


def clip_suffix(u, clip_file):
    """'UCS-17_2+1.mov' on a Rack named clip 'UCS-17' -> '_2+1'. None if it isn't a session/take clip."""
    base = clip_file[:-4] if clip_file.lower().endswith(".mov") else clip_file
    if not base.startswith(u.clip_name):
        return None
    suffix = base[len(u.clip_name):]
    return suffix if re.fullmatch(r"_\d+(\+\d+)?", suffix) else None


def act_archive(u, suffixes, root_dir):
    """Download <clip><suffix>.mov for each suffix into root_dir/<clip>/. An empty
    list downloads the Rack's stringout clip (<clip>.mov). Missing files are skipped."""
    act_data_lan(u)
    clip = u.clip_name
    names = [f"{clip}{sfx}.mov" for sfx in suffixes] or [f"{clip}.mov"]
    folder = os.path.join(root_dir, clip)
    ok_n, missing = 0, []
    for name in names:
        url = f"http://{u.ip}/media/" + name.replace("+", "%2b")
        try:
            _download_file(u, url, os.path.join(folder, name))
            ok_n += 1
        except NotOnRack:
            missing.append(name)
            log(f"[{u.name}] {name} not on this Rack - skipped", "warn")
    if ok_n == 0:
        raise RackError(f"none of the {len(names)} requested clip(s) were found on the Rack")
    msg = f"{ok_n} clip(s) archived to {folder}"
    return msg + (f"; not found: {', '.join(missing)}" if missing else "")


def phase(units, fn, max_workers=None):
    """Run fn(unit) on all units in parallel. Returns {ip: (ok, message)}; never raises."""
    def safe(u):
        try:
            return True, fn(u)
        except RackError as e:
            return False, str(e)
        except Exception as e:           # keep one bad unit from killing the run
            return False, f"unexpected error: {e}"
    with ThreadPoolExecutor(max_workers=max_workers or max(1, len(units))) as ex:
        futs = {u.ip: ex.submit(safe, u) for u in units}
    out = {ip: f.result() for ip, f in futs.items()}
    for u in units:
        ok, msg = out[u.ip]
        log(f"[{u.name}] {msg}", "ok" if ok else "err")
    return out


def run_action(units, fn, max_workers=None):
    """Runs fn on every unit, refreshes them, returns [(name, ok, message)]."""
    res = phase(units, fn, max_workers)
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


class StringoutDialog(tk.Toplevel):
    SESSION_RANGE = [str(i) for i in range(1, 51)]
    TAKE_RANGE = [""] + [str(i) for i in range(1, 11)]       # "" = whole session, no +take suffix
    HOURS = [f"{i:02d}" for i in range(24)]
    MINS_SECS = [f"{i:02d}" for i in range(60)]
    LEAD_MINUTES = [str(i) for i in range(0, 11)]
    DUR_MINUTES = [str(i) for i in range(0, 31)]

    def __init__(self, app, units):
        super().__init__(app)
        self.app, self.units = app, units
        self.title(f"Make Stringouts ({len(units)} Rack(s))")
        self.geometry("470x440")
        self.minsize(440, 400)

        btns = ttk.Frame(self)
        btns.pack(side="bottom", pady=16)
        ttk.Button(btns, text="Create Stringouts", command=self._confirm).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=6)
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, font=("", 10, "bold"), foreground="#1b5e20").pack(
            side="bottom", pady=(0, 4))

        ttk.Label(self, text="Clip / Take", font=("", 10, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        row = ttk.Frame(self)
        row.pack(fill="x", padx=14)
        ttk.Label(row, text="Session:").grid(row=0, column=0, sticky="w")
        self.session = tk.StringVar(value=self.SESSION_RANGE[0])
        ttk.Combobox(row, textvariable=self.session, state="readonly", width=6,
                     values=self.SESSION_RANGE).grid(row=0, column=1, padx=(4, 16))
        ttk.Label(row, text="Take (optional):").grid(row=0, column=2, sticky="w")
        self.take = tk.StringVar(value="")
        ttk.Combobox(row, textvariable=self.take, state="readonly", width=6,
                     values=self.TAKE_RANGE).grid(row=0, column=3, padx=(4, 0))

        ttk.Label(self, text="T-0 time (HH : MM : SS)", font=("", 10, "bold")).pack(anchor="w", padx=14, pady=(16, 2))
        t0 = ttk.Frame(self)
        t0.pack(fill="x", padx=14)
        self.h, self.m, self.s = tk.StringVar(value=""), tk.StringVar(value=""), tk.StringVar(value="00")
        ttk.Combobox(t0, textvariable=self.h, state="readonly", width=4, values=self.HOURS).pack(side="left")
        ttk.Label(t0, text=" : ").pack(side="left")
        ttk.Combobox(t0, textvariable=self.m, state="readonly", width=4, values=self.MINS_SECS).pack(side="left")
        ttk.Label(t0, text=" : ").pack(side="left")
        ttk.Combobox(t0, textvariable=self.s, state="readonly", width=4, values=self.MINS_SECS).pack(side="left")

        lead = ttk.Frame(self)
        lead.pack(fill="x", padx=14, pady=(8, 0))
        ttk.Label(lead, text="Start clip").pack(side="left")
        self.lead = tk.StringVar(value="1")
        ttk.Combobox(lead, textvariable=self.lead, state="readonly", width=4,
                     values=self.LEAD_MINUTES).pack(side="left", padx=6)
        ttk.Label(lead, text="min before T-0 (set automatically)").pack(side="left")

        ttk.Label(self, text="Duration (min : sec)", font=("", 10, "bold")).pack(anchor="w", padx=14, pady=(16, 2))
        dur = ttk.Frame(self)
        dur.pack(fill="x", padx=14)
        self.dm, self.ds = tk.StringVar(value="3"), tk.StringVar(value="00")
        ttk.Combobox(dur, textvariable=self.dm, state="readonly", width=4, values=self.DUR_MINUTES).pack(side="left")
        ttk.Label(dur, text=" : ").pack(side="left")
        ttk.Combobox(dur, textvariable=self.ds, state="readonly", width=4, values=self.MINS_SECS).pack(side="left")
        ttk.Label(dur, text="   (3:00 is the usual)", foreground="#555555").pack(side="left")

        for v in (self.session, self.take, self.h, self.m, self.s, self.lead, self.dm, self.ds):
            v.trace_add("write", lambda *_: self._update())
        self._update()

    def _suffix(self):
        return f"_{self.session.get()}+{self.take.get()}" if self.take.get() else f"_{self.session.get()}"

    def _start(self):
        if not (self.h.get() and self.m.get() and self.s.get()):
            return None
        t0 = int(self.h.get()) * 3600 + int(self.m.get()) * 60 + int(self.s.get())
        st = (t0 - int(self.lead.get()) * 60) % 86400
        return f"{st // 3600:02d}:{(st % 3600) // 60:02d}:{st % 60:02d}"

    def _update(self):
        st = self._start()
        self.summary.set("Set the T-0 time to see the start timehack." if st is None else
                         f"Clip {self._suffix()}   Start {st}   Duration {self.dm.get()}:{self.ds.get()}")

    def _confirm(self):
        start = self._start()
        if start is None:
            messagebox.showwarning("T-0 not set", "Select the T-0 hour and minute first.", parent=self)
            return
        m, sec = int(self.dm.get()), int(self.ds.get())
        if m == 0 and sec == 0:
            messagebox.showwarning("Duration", "Duration can't be 0:00.", parent=self)
            return
        names = ", ".join(u.name for u in self.units) if len(self.units) <= 6 else f"{len(self.units)} Racks"
        if not messagebox.askyesno("Confirm", f"Clip/Take: {self._suffix()}\nT-0: {self.h.get()}:{self.m.get()}:{self.s.get()}\n"
                                   f"Start: {start}  ({self.lead.get()} min before T-0)\n"
                                   f"Duration: {m}:{sec:02d}\nTargets: {names}\n\n"
                                   f"The Racks will be put in Data-LAN mode. Proceed?", parent=self):
            return
        folder = filedialog.askdirectory(title="Select stringouts download directory", parent=self)
        if not folder:
            return
        suffix, start_tc, dur_tc = self._suffix(), start + ":00", f"{m // 60:02d}:{m % 60:02d}:{sec:02d}:00"
        # Two Racks with the same clip name would overwrite each other's file - prefix the Rack name then.
        counts = {}
        for u in self.units:
            counts[u.clip_name] = counts.get(u.clip_name, 0) + 1
        self.app.do_action("Make Stringouts", self.units,
                           lambda u: act_stringout(u, suffix, start_tc, dur_tc, folder, counts[u.clip_name] > 1))
        self.destroy()


class ClipBrowserPanel(ttk.Frame):
    """What is actually recorded on each Rack right now (read-only). If on_pick is
    given, a button adds the selected session/take clips to the Archive list."""

    def __init__(self, master, app, units, on_pick=None):
        super().__init__(master)
        self.app, self.units, self.on_pick = app, units, on_pick
        ttk.Label(self, text="What's recorded on each selected Rack right now.", font=("", 9),
                  foreground="#555555", wraplength=280, justify="left").pack(anchor="w", pady=(0, 4))
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, pady=(0, 6))
        self.tree = ttk.Treeview(frame, columns=("rack", "clip"), show="headings", selectmode="extended")
        self.tree.heading("rack", text="Rack")
        self.tree.heading("clip", text="Clip")
        self.tree.column("rack", width=90, anchor="w")
        self.tree.column("clip", width=190, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.row_unit = {}
        if on_pick:
            ttk.Button(self, text="Add Selected Clips to Archive List", command=self._pick).pack(fill="x", pady=(0, 4))
        ttk.Button(self, text="Refresh", command=self.refresh).pack(fill="x")
        self._populate()
        self.refresh()

    def _populate(self):
        self.tree.delete(*self.tree.get_children())
        self.row_unit = {}
        for u in self.units:
            if not u.clips:
                self.tree.insert("", "end", values=(u.name, "(no clips)"))
            for c in u.clips:
                iid = self.tree.insert("", "end", values=(u.name, c))
                self.row_unit[iid] = (u, c)

    def refresh(self):
        def work(units):
            for u in units:
                try:
                    u.refresh()
                except RackError as e:
                    log(str(e), "err")
            return units
        self.app.run_job("Browse Clips", work, self.units, on_done=lambda r: self._populate())

    def _pick(self):
        found = []
        for iid in self.tree.selection():
            if iid in self.row_unit:
                u, c = self.row_unit[iid]
                sfx = clip_suffix(u, c)
                if sfx:
                    found.append(sfx)
        self.on_pick(found)


class BrowseClipsDialog(tk.Toplevel):
    def __init__(self, app, units):
        super().__init__(app)
        self.title("Browse Available Clips")
        self.geometry("560x440")
        self.minsize(380, 300)
        ttk.Button(self, text="Close", command=self.destroy).pack(side="bottom", pady=(0, 12))
        ClipBrowserPanel(self, app, units).pack(fill="both", expand=True, padx=10, pady=10)


class ArchiveDialog(tk.Toplevel):
    SESSION_RANGE = [str(i) for i in range(1, 51)]
    TAKE_RANGE = [""] + [str(i) for i in range(1, 51)]

    def __init__(self, app, units):
        super().__init__(app)
        self.app, self.units = app, units
        self.title(f"Archive Clips ({len(units)} Rack(s))")
        self.geometry("780x480")
        self.minsize(660, 400)

        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True, padx=12, pady=12)
        right = ttk.Frame(self)
        right.pack(side="right", fill="both", expand=True, padx=(0, 12), pady=12)

        ttk.Label(left, font=("", 9), text="Build the list of sessions/takes to archive, or add them from the\n"
                  "browser on the right. Leave the list empty to download each\nRack's stringout clip.").pack(anchor="w", pady=(0, 6))
        pick = ttk.Frame(left)
        pick.pack(fill="x")
        ttk.Label(pick, text="Session:").grid(row=0, column=0, sticky="w")
        self.session = tk.StringVar(value=self.SESSION_RANGE[0])
        ttk.Combobox(pick, textvariable=self.session, state="readonly", width=6,
                     values=self.SESSION_RANGE).grid(row=0, column=1, padx=(4, 16))
        ttk.Label(pick, text="Take (optional):").grid(row=0, column=2, sticky="w")
        self.take = tk.StringVar(value="")
        ttk.Combobox(pick, textvariable=self.take, state="readonly", width=6,
                     values=self.TAKE_RANGE).grid(row=0, column=3, padx=(4, 16))
        ttk.Button(pick, text="Add", command=self._add).grid(row=0, column=4)

        ttk.Label(left, text="Sessions/takes to archive:", font=("", 9)).pack(anchor="w", pady=(14, 2))
        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=True, pady=(0, 6))
        self.items = tk.Listbox(lf, selectmode="extended", activestyle="none")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.items.yview)
        self.items.configure(yscrollcommand=sb.set)
        self.items.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        ttk.Button(left, text="Remove Selected", command=self._remove).pack(anchor="w", pady=(0, 10))
        b = ttk.Frame(left)
        b.pack(pady=(0, 4))
        ttk.Button(b, text="Choose Folder && Archive", command=self._go).pack(side="left", padx=6)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        ClipBrowserPanel(right, app, units, on_pick=self._add_many).pack(fill="both", expand=True)

    def _add_many(self, suffixes):
        existing = set(self.items.get(0, "end"))
        for sfx in suffixes:
            if sfx not in existing:
                self.items.insert("end", sfx)
                existing.add(sfx)

    def _add(self):
        s, t = self.session.get(), self.take.get()
        self._add_many([f"_{s}+{t}" if t else f"_{s}"])

    def _remove(self):
        for i in reversed(self.items.curselection()):
            self.items.delete(i)

    def _go(self):
        suffixes = list(self.items.get(0, "end"))
        folder = filedialog.askdirectory(title="Select root download directory for archiving", parent=self)
        if not folder:
            return
        self.app.do_action("Archive Clips", self.units, lambda u: act_archive(u, suffixes, folder), 8)
        self.destroy()


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
        for text, cmd in (("Change Recorder Settings", self.open_settings), ("Format Drives", self.open_format),
                          ("Make Stringouts", self.open_stringout), ("Download Stringouts", self.download_stringouts),
                          ("Browse Clips", self.open_browse), ("Archive Clips", self.open_archive)):
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

    def do_action(self, label, units, fn, max_workers=None):
        self.run_job(label, run_action, units, fn, max_workers,
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

    def open_browse(self):
        t = self.need_selection()
        if t:
            BrowseClipsDialog(self, t)

    def open_archive(self):
        t = self.need_selection()
        if t:
            ArchiveDialog(self, t)

    def open_stringout(self):
        t = self.need_selection()
        if t:
            StringoutDialog(self, t)

    def download_stringouts(self):
        t = self.need_selection()
        if not t:
            return
        self.run_job("Download Stringouts", run_action, t, act_download, 8,
                     on_done=lambda rows: (self.refresh_table(), ResultsDialog(self, "Download Stringouts", rows)))


if __name__ == "__main__":
    App().mainloop()
