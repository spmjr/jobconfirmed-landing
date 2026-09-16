"""
akpi_gui.py — GUI front-end for akpi.py (AJA Ki Pro Ultra Interface Utility)

This does NOT rewrite the backend. It imports akpi.py as a module and reuses
its real functions (getReq/setReq, dlWorker*, formatDvrs, changeStorageSettings,
changeClipNamesd, inventory_storage_slot_scan, etc.) exactly as they exist
today. Only the interactive layer (the recursive input()-driven menu/submenu
functions) is replaced with buttons, forms, a live status table, and a
console log pane.

Run this file instead of akpi.py:
    python akpi_gui.py

Requires akpi.py to be in the same folder (or on the Python path).
"""

import os
import sys
import re
import csv
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import concurrent.futures

import akpi  # your original script, imported as a library — unmodified


# ---------------------------------------------------------------------------
# Thread-safe console redirection with basic ANSI color support (akpi's Col
# class uses \033[92m / 93m / 91m / 1m / 0m — we map those to Tk tags so the
# green/yellow/red [SUCCESS]/[WARNING]/[ERROR] look from the CLI carries over)
# ---------------------------------------------------------------------------

ANSI_RE = re.compile(r'\033\[(\d+)m')

ANSI_TAG_MAP = {
    '92': 'ok',
    '93': 'warn',
    '91': 'err',
    '1': 'bold',
    '0': 'reset',
}


class QueueWriter:
    """A drop-in replacement for sys.stdout that pushes text onto a queue
    instead of writing to a real terminal, so background worker threads can
    safely produce output that the Tk main thread later drains and renders."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class ConsolePane(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.text = tk.Text(self, wrap="word", bg="#111111", fg="#dddddd",
                             insertbackground="#dddddd", state="disabled",
                             font=("Consolas", 10))
        scroll = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.text.tag_configure("ok", foreground="#4caf50")
        self.text.tag_configure("warn", foreground="#ffb300")
        self.text.tag_configure("err", foreground="#f44336")
        self.text.tag_configure("bold", font=("Consolas", 10, "bold"))

        self._q = queue.Queue()
        self._current_tag = None
        sys.stdout = QueueWriter(self._q)
        sys.stderr = QueueWriter(self._q)
        self.after(80, self._drain)

    def _drain(self):
        try:
            while True:
                chunk = self._q.get_nowait()
                self._append(chunk)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _append(self, chunk):
        self.text.configure(state="normal")
        # split on ANSI escape codes, keeping track of the active color tag
        pos = 0
        for m in ANSI_RE.finditer(chunk):
            piece = chunk[pos:m.start()]
            if piece:
                self.text.insert("end", piece, self._current_tag)
            code = m.group(1)
            tag = ANSI_TAG_MAP.get(code)
            self._current_tag = None if (tag is None or tag == "reset") else tag
            pos = m.end()
        piece = chunk[pos:]
        if piece:
            self.text.insert("end", piece, self._current_tag)
        self.text.see("end")
        self.text.configure(state="disabled")


# ---------------------------------------------------------------------------
# Background job runner — keeps the GUI responsive. Wraps every job in a
# broad try/except so a network error (which akpi's backend often handles by
# calling exit(-1)) can't silently wedge the UI in a "Running..." state.
# ---------------------------------------------------------------------------

def run_job(app, label, fn, *args, on_done=None, **kwargs):
    app.set_busy(True, label)

    def worker():
        try:
            result = fn(*args, **kwargs)
        except SystemExit:
            print(f"\033[91m[ERROR]\033[0m {label} aborted (backend called exit()). "
                  f"Check the log above for the underlying network/API error.\n")
            result = None
        except Exception:
            print(f"\033[91m[ERROR]\033[0m {label} failed:\n{traceback.format_exc()}\n")
            result = None
        finally:
            app.after(0, lambda: app.set_busy(False, label))
            if on_done is not None:
                app.after(0, lambda: on_done(result))

    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Backend action wrappers — thin orchestration copied out of akpi.py's
# recursive menu/submenu functions (menu(), changeConfigSubMenu(), dlManager(),
# stringout()) so it can run headless in a worker thread. The actual network
# calls and threading (Garfield/ThreadPoolExecutor, barriers, semaphores) are
# the exact same akpi functions as the CLI version used.
# ---------------------------------------------------------------------------

def load_inventory_from_file(path, dvrList):
    """Re-implementation of akpi.inventory_file_selection_loader that takes a
    path instead of popping its own file dialog (Tk dialogs must be created
    on the main thread, so the dialog itself happens in the GUI callback and
    only the network-heavy parsing/connectivity part runs in the worker)."""
    with open(path, 'r') as readPtr:
        csvReader = csv.reader(readPtr)
        header = next(csvReader)
        if header is not None:
            for row in csvReader:
                kiObj = akpi.KiProUltra(row[0].strip())
                if akpi.checkConnection(kiObj):
                    dvrList.append(kiObj)
                    setattr(kiObj, "check", True)
                    print(f"\033[92m[SUCCESS]\033[0m [{kiObj.ip}] [{kiObj.dvrName}] connected")
    return dvrList


def action_set_mode(dvrList, mode_value):
    setting = "config"
    changedParams = {'name': 'eParamID_MediaState', 'value': mode_value}
    reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': mode_value}
    for dvr in dvrList:
        akpi.setReq(dvr.ip, setting, reqParams, changedParams)
    for dvr in dvrList:
        dvr.reset()
    return dvrList


def action_set_encoding(dvrList, enc_value):
    setting = "config"
    changedParams = {'name': 'eParamID_EncodeType_Low_FR', 'value': enc_value}
    reqParams = {'action': 'set', 'paramid': 'eParamID_EncodeType_Low_FR', 'value': enc_value}
    for dvr in dvrList:
        akpi.setReq(dvr.ip, setting, reqParams, changedParams)
    for dvr in dvrList:
        dvr.reset()
    return dvrList


def action_swap_storage(dvrList):
    akpi.program_state.barrier = threading.Barrier(len(dvrList), timeout=None)
    with akpi.Garfield(max_workers=len(dvrList)) as executor:
        futures = [executor.submit(akpi.changeStorageSettings, dvr) for dvr in dvrList]
        concurrent.futures.wait(futures)
    return dvrList


def action_reset_take_numbers(dvrList):
    setting = "config"
    akpi.program_state.barrier = threading.Barrier(len(dvrList), timeout=None)
    resetClipTakeChange = {'name': 'eParamID_CustomTake', 'value': '1'}
    resetClipTakeParams = {'action': 'set', 'paramid': 'eParamID_CustomTake', 'value': '1'}
    event = threading.Event()
    lock = threading.Lock()
    with akpi.Garfield(max_workers=len(dvrList)) as executor:
        futures = [executor.submit(akpi.setReq2, dvr.ip, setting, resetClipTakeParams,
                                    resetClipTakeChange, event, lock) for dvr in dvrList]
        concurrent.futures.wait(futures)
    for dvr in dvrList:
        dvr.reset()
        print(f"\033[92m[SUCCESS]\033[0m [{dvr.dvrName}] take number reset to {dvr.takeNum}")
    return dvrList


def action_change_clip_names(dvrList, clipConfig):
    if not clipConfig:
        print("\033[93m[INFO]\033[0m No clip name mismatches found. Nothing to change.")
        return dvrList
    akpi.program_state.barrier = threading.Barrier(len(clipConfig.items()), timeout=None)
    with akpi.Garfield(max_workers=len(clipConfig.items())) as executor:
        futures = [executor.submit(akpi.changeClipNamesd, *item) for item in clipConfig.items()]
        concurrent.futures.wait(futures)
    for dvr in dvrList:
        dvr.reset()
        print(f"\033[92m[SUCCESS]\033[0m [{dvr.ip}] clip name is now [{dvr.clipName}]")
    return dvrList


def action_format_drives(dvrList):
    akpi.program_state.barrier = threading.Barrier(len(dvrList), timeout=None)
    with akpi.Garfield(max_workers=len(dvrList)) as executor:
        futures = [executor.submit(akpi.formatDvrsStager, dvr) for dvr in dvrList]
        concurrent.futures.wait(futures)
    return dvrList


def action_storage_check(dvrList):
    akpi.program_state.barrier = threading.Barrier(len(dvrList), timeout=120)
    with akpi.Garfield(max_workers=len(dvrList)) as executor:
        futures = [executor.submit(akpi.inventory_storage_slot_scan, dvr) for dvr in dvrList]
        concurrent.futures.wait(futures)
    for dvr in dvrList:
        print(f"\033[92m[SUCCESS]\033[0m [{dvr.ip}] [{dvr.dvrName}] storage check complete "
              f"(all loaded: {dvr.storage_all_loaded})")
    return dvrList


def action_set_data_lan_and_download_stringouts(dvrList):
    setting = "config"
    changedParams = {'name': 'eParamID_MediaState', 'value': '1'}
    reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '1'}
    totalErrors = 0
    for dvr in dvrList:
        totalErrors += akpi.setReq(dvr.ip, setting, reqParams, changedParams)
    if totalErrors != 0:
        print("\033[91m[ERROR]\033[0m Not all DVRs responded to Data-LAN mode command, aborting download.")
        return dvrList
    print("\033[92m[SUCCESS]\033[0m All DVRs in Data-LAN Mode")
    akpi.program_state.barrier = threading.Barrier(len(dvrList), timeout=None)
    with akpi.Garfield(max_workers=len(dvrList)) as executor:
        futures = [executor.submit(akpi.dlWorkerds, dvr) for dvr in dvrList]
        concurrent.futures.wait(futures)
    return dvrList


def action_stringout(dvrList, clipSessionId, startTh, duration, downloadDir):
    """Reimplementation of akpi.stringout(), input() prompts replaced by
    already-collected form values; same subclip request + polling logic."""
    clipSessionId = clipSessionId.replace("+", "%2b")

    setting = "config"
    changedParams = {'name': 'eParamID_MediaState', 'value': '1'}
    reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '1'}
    for dvr in dvrList:
        akpi.setReq(dvr.ip, setting, reqParams, changedParams)
    print("\033[92m[SUCCESS]\033[0m All DVRs in Data-LAN Mode")

    successStartedJobs = 0
    for dvr in dvrList:
        devPath = "/mnt/S1/AJA/" if dvr.actMediaSlot == "S1" else "/mnt/S2/AJA/"
        setting = "mediaedit"
        arg1 = (devPath + dvr.clipName + clipSessionId + ".mov").replace("+", "%2b")
        arg2 = (devPath + dvr.clipName + ".mov").replace("+", "%2b")
        url = (f"http://{dvr.ip}/{setting}?action=subclip&arg1={arg1}&arg2={arg2}"
               f"&arg3={startTh}&arg4={duration}")
        response = akpi.requests.get(url, timeout=5)
        cleanedResp = response.text.replace("\n", "\", \"")[:-4]
        cleanedResp = "{\"" + cleanedResp + "\"}"
        cleanedResp = cleanedResp.replace(": ", "\": \"")
        jobStatus = akpi.json.loads(cleanedResp)
        print(jobStatus)
        if jobStatus.get('error') != "none":
            print(f"\033[91m[ERROR]\033[0m Job failed to start on {dvr.dvrName}: {jobStatus.get('error')}")
        else:
            print(f"\033[92m[SUCCESS]\033[0m Job started on {dvr.dvrName}")
            setattr(dvr, "procId", jobStatus.get('id'))
            successStartedJobs += 1

    print("Polling for stringout job completion . . .")
    compCount, jobWatchDog = 0, 0
    setting = "mediaedit"
    while compCount < successStartedJobs and jobWatchDog < 10:
        compCount = 0
        for dvr in dvrList:
            if dvr.procId != 0:
                url = f"http://{dvr.ip}/{setting}?action=status&id={dvr.procId}"
                response = akpi.requests.get(url, timeout=5)
                cleanedResp = response.text.replace("\n", "\", \"")[:-4]
                cleanedResp = "{\"" + cleanedResp + "\"}"
                cleanedResp = cleanedResp.replace(": ", "\": \"")
                stat = akpi.json.loads(cleanedResp)
                if stat['status'] == "Completed":
                    compCount += 1
        akpi.sleep(3)
        jobWatchDog += 1

    for dvr in dvrList:
        stringoutName = dvr.clipName + ".mov"
        dvr.downloadStrings.append(f"http://{dvr.ip}/media/{stringoutName},{downloadDir}\\{stringoutName}")
    print("\033[93m[INFO]\033[0m Stringout creation complete. Ready to download via 'Download Stringouts'.")
    return dvrList


def action_archive(dvrList, dlList, rootDir):
    """Reimplementation of the list-mode branch of akpi.dlManager()."""
    for dvr in dvrList:
        for vid in dlList:
            fullVideoNameHTTP = dvr.clipName + vid + ".mov"
            fullVideoNameFile = fullVideoNameHTTP.replace("%2b", "+")
            dvr.downloadClips.append(
                f"http://{dvr.ip}/media/{fullVideoNameHTTP},{rootDir}\\{dvr.clipName}\\{fullVideoNameFile}")

    setting = "config"
    changedParams = {'name': 'eParamID_MediaState', 'value': '1'}
    reqParams = {'action': 'set', 'paramid': 'eParamID_MediaState', 'value': '1'}
    totalErrors = 0
    for dvr in dvrList:
        totalErrors += akpi.setReq(dvr.ip, setting, reqParams, changedParams)
    if totalErrors != 0:
        print("\033[91m[ERROR]\033[0m Not all DVRs responded to commands, aborting archive.")
        return dvrList
    print("\033[92m[SUCCESS]\033[0m All DVRs in Data-LAN Mode")

    akpi.program_state.barrier = threading.Barrier(len(dvrList), timeout=None)
    with akpi.Garfield(max_workers=len(dvrList)) as executor:
        futures = [executor.submit(akpi.dlWorker_archive, dvr) for dvr in dvrList]
        concurrent.futures.wait(futures)
    return dvrList


# ---------------------------------------------------------------------------
# Dialog windows
# ---------------------------------------------------------------------------

class ChangeSettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Change Recorder Settings")
        self.geometry("360x340")
        self.resizable(False, False)

        ttk.Label(self, text="Mode", font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        row = ttk.Frame(self); row.pack(fill="x", padx=12)
        ttk.Button(row, text="Data-LAN Mode", command=lambda: self._run(
            "Set Mode: Data-LAN", action_set_mode, app.dvrList, '1')).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Record-Play Mode", command=lambda: self._run(
            "Set Mode: Record-Play", action_set_mode, app.dvrList, '0')).pack(side="left", expand=True, fill="x")

        ttk.Label(self, text="Encoding", font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(16, 2))
        row = ttk.Frame(self); row.pack(fill="x", padx=12)
        ttk.Button(row, text="High-Quality (ProRes 422 HQ)", command=lambda: self._run(
            "Set Encoding: HQ", action_set_encoding, app.dvrList, '1')).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Low-Quality (ProRes 422 Proxy)", command=lambda: self._run(
            "Set Encoding: LQ", action_set_encoding, app.dvrList, '3')).pack(side="left", expand=True, fill="x")

        ttk.Label(self, text="Storage", font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(16, 2))
        ttk.Button(self, text="Swap Storage Slots", command=lambda: self._run(
            "Swap Storage Slots", action_swap_storage, app.dvrList)).pack(fill="x", padx=12)

        ttk.Label(self, text="Clip Names", font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(16, 2))
        ttk.Button(self, text="Load New Clip Name CSV...", command=self._change_clip_names).pack(fill="x", padx=12)

        ttk.Label(self, text="Take Numbers", font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(16, 2))
        ttk.Button(self, text="Reset Clip Take Numbers", command=lambda: self._run(
            "Reset Take Numbers", action_reset_take_numbers, app.dvrList)).pack(fill="x", padx=12)

        ttk.Button(self, text="Close", command=self.destroy).pack(pady=14)

    def _run(self, label, fn, *args):
        def done(dvrList):
            if dvrList is not None:
                self.app.refresh_table()
        run_job(self.app, label, fn, *args, on_done=done)
        self.destroy()

    def _change_clip_names(self):
        # File dialog happens on the main thread; network application runs in a worker thread.
        clipConfig = akpi.newConfigCreation(self.app.dvrList)
        self._run("Change Clip Names", action_change_clip_names, self.app.dvrList, clipConfig)


class StringoutDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Make Stringouts")
        self.geometry("420x260")
        self.resizable(False, False)

        ttk.Label(self, text="Clip/Take Number (e.g. _2+1 or _1)").pack(anchor="w", padx=12, pady=(14, 2))
        self.clip_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.clip_var).pack(fill="x", padx=12)

        ttk.Label(self, text="Starting Timehack — format HH:MM:SS:mm\n"
                              "(e.g. if T0 is 22:03:00:00, go back 1 min: 22:02:00:00)").pack(
            anchor="w", padx=12, pady=(12, 2))
        self.start_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.start_var).pack(fill="x", padx=12)

        ttk.Label(self, text="Duration — format HH:MM:SS:mm (usually 00:03:00:00)").pack(
            anchor="w", padx=12, pady=(12, 2))
        self.dur_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.dur_var).pack(fill="x", padx=12)

        btns = ttk.Frame(self); btns.pack(pady=18)
        ttk.Button(btns, text="Create Stringouts", command=self._confirm).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=6)

    def _confirm(self):
        clip, start, dur = self.clip_var.get().strip(), self.start_var.get().strip(), self.dur_var.get().strip()
        if not (clip and start and dur):
            messagebox.showwarning("Missing info", "All three fields are required.")
            return
        if not messagebox.askyesno(
                "Confirm", f"Clip/Take: {clip}\nStart: {start}\nDuration: {dur}\n\nProceed?"):
            return

        workDir = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), "stringouts")
        os.makedirs(workDir, exist_ok=True)
        downloadDir = filedialog.askdirectory(initialdir=workDir, title="Select stringouts download directory")
        if not downloadDir:
            return

        def done(dvrList):
            pass
        run_job(self.app, "Make Stringouts", action_stringout, self.app.dvrList,
                clip, start, dur, downloadDir, on_done=done)
        self.destroy()


class ArchiveDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Archive Clips")
        self.geometry("440x220")
        self.resizable(False, False)

        ttk.Label(self, text="Sessions/Takes, comma-separated (e.g. _1,_2+1)\n"
                              "Leave blank to just download the current stringout clips.").pack(
            anchor="w", padx=12, pady=(14, 2))
        self.list_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.list_var).pack(fill="x", padx=12)

        btns = ttk.Frame(self); btns.pack(pady=24)
        ttk.Button(btns, text="Choose Folder && Archive", command=self._confirm).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=6)

    def _confirm(self):
        raw = self.list_var.get().strip().replace(" ", "").replace("+", "%2b")
        dlList = raw.split(",") if raw else ["0"]

        workDir = os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), "Archive")
        os.makedirs(workDir, exist_ok=True)
        rootDir = filedialog.askdirectory(initialdir=workDir,
                                           title="Select root download directory for archiving")
        if not rootDir:
            return

        run_job(self.app, "Archive Clips", action_archive, self.app.dvrList, dlList, rootDir)
        self.destroy()


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

STATUS_COLUMNS = [
    ("dvrName", "Name"), ("ip", "IP"), ("clipName", "Clip Name"),
    ("mode", "Mode"), ("encoding", "Encoding"), ("state", "State"),
    ("actMediaSlot", "Slot"), ("mediaPercentage", "Media %"), ("takeNum", "Take #"),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AJA Ki Pro Ultra Interface Utility")
        self.geometry("1180x680")
        self.dvrList = []

        self._build_layout()
        self._build_sidebar()
        self._set_action_buttons_enabled(False)

        # console pane installs sys.stdout/stderr redirection
        akpi.logger()  # keep original file-logging behavior (writes to ./logs)

    # -- layout -----------------------------------------------------------
    def _build_layout(self):
        self.status_var = tk.StringVar(value="Ready. Load an inventory CSV to begin.")
        status_bar = ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken")
        status_bar.pack(side="bottom", fill="x")

        main = ttk.Frame(self)
        main.pack(side="left", fill="both", expand=True)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True, padx=(0, 8), pady=8)

        # Status table tab
        table_frame = ttk.Frame(self.notebook)
        self.tree = ttk.Treeview(table_frame, columns=[c[0] for c in STATUS_COLUMNS], show="headings")
        for key, label in STATUS_COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=110, anchor="center")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.notebook.add(table_frame, text="DVR Status")

        # Console tab
        self.console = ConsolePane(self.notebook)
        self.notebook.add(self.console, text="Console Log")

    def _build_sidebar(self):
        sidebar = ttk.Frame(self, width=230)
        sidebar.pack(side="right", fill="y", padx=8, pady=8)

        ttk.Label(sidebar, text="AJA Ki Pro Ultra", font=("", 13, "bold")).pack(pady=(0, 2))
        ttk.Label(sidebar, text="Interface Utility", font=("", 10)).pack(pady=(0, 12))

        ttk.Button(sidebar, text="Load Inventory CSV...", command=self.load_inventory).pack(fill="x", pady=3)
        ttk.Button(sidebar, text="Refresh / Show Status", command=self.refresh_table_job).pack(fill="x", pady=3)

        ttk.Separator(sidebar).pack(fill="x", pady=10)

        self.action_buttons = []

        def add_btn(text, cmd):
            b = ttk.Button(sidebar, text=text, command=cmd)
            b.pack(fill="x", pady=3)
            self.action_buttons.append(b)
            return b

        add_btn("Change Recorder Settings", self.open_change_settings)
        add_btn("Format Drives", self.confirm_format_drives)
        add_btn("Make Stringouts", self.open_stringout)
        add_btn("Download Stringouts", self.download_stringouts)
        add_btn("Archive Clips", self.open_archive)
        add_btn("DVR Storage Check", self.storage_check)

        ttk.Separator(sidebar).pack(fill="x", pady=10)
        ttk.Button(sidebar, text="Quit", command=self.destroy).pack(fill="x")

    # -- helpers ------------------------------------------------------------
    def set_busy(self, busy, label=""):
        self.status_var.set(f"Running: {label} ..." if busy else "Ready.")
        state = "disabled" if busy else "normal"
        for b in self.action_buttons:
            b.configure(state=state)

    def _set_action_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for b in self.action_buttons:
            b.configure(state=state)

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for dvr in self.dvrList:
            values = [getattr(dvr, key, "") for key, _ in STATUS_COLUMNS]
            self.tree.insert("", "end", values=values)
        self.notebook.select(0)

    # -- actions ------------------------------------------------------------
    def load_inventory(self):
        path = filedialog.askopenfilename(initialdir=".", title="Select inventory CSV",
                                           filetypes=(("CSV Files", "*.csv"),))
        if not path:
            return
        self.dvrList = []

        def done(dvrList):
            if dvrList:
                self.dvrList = dvrList
                self._set_action_buttons_enabled(True)
                self.refresh_table()
                print(f"\033[92m[SUCCESS]\033[0m Loaded {len(dvrList)} DVR(s) from inventory.")
            else:
                print("\033[91m[ERROR]\033[0m No DVRs loaded — check the CSV and network connectivity.")

        run_job(self, "Load Inventory", load_inventory_from_file, path, self.dvrList, on_done=done)

    def refresh_table_job(self):
        if not self.dvrList:
            messagebox.showinfo("No inventory", "Load an inventory CSV first.")
            return

        def do_reset(dvrList):
            for dvr in dvrList:
                dvr.reset()
            return dvrList

        run_job(self, "Refresh Status", do_reset, self.dvrList, on_done=lambda r: self.refresh_table())

    def open_change_settings(self):
        ChangeSettingsDialog(self)

    def confirm_format_drives(self):
        if not messagebox.askyesno(
                "DATA LOSS WARNING",
                "This will format ALL DVRs and EVERY slot.\n\nThis cannot be undone. Proceed?",
                icon="warning"):
            return
        run_job(self, "Format Drives", action_format_drives, self.dvrList,
                on_done=lambda r: self.refresh_table())

    def open_stringout(self):
        StringoutDialog(self)

    def download_stringouts(self):
        run_job(self, "Download Stringouts", action_set_data_lan_and_download_stringouts, self.dvrList,
                on_done=lambda r: self.refresh_table())

    def open_archive(self):
        ArchiveDialog(self)

    def storage_check(self):
        run_job(self, "DVR Storage Check", action_storage_check, self.dvrList)


if __name__ == "__main__":
    app = App()
    app.mainloop()
