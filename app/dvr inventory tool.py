#!/usr/bin/env python3
"""
DVR Inventory Builder
----------------------
Turns a launch Video Recording Schedule (.xlsx) into a DVR_IP,CLIP_NAME
inventory CSV (same format as KA03_inventory.csv), using a fixed
IP <-> DVR-label reference table.

Workflow:
  1. Load Recording Schedule (.xlsx)   -> auto-fills clip names for DVR-1..DVR-50
     by reading the "DVR-N" label column and the clip name in the very next
     column to its right (the standard column pair used on these sheets).
  2. Review/edit the table              -> DVRA-*, and anything the parser
     couldn't find come in blank/SPARE; techs fix those by hand
     (double-click a CLIP NAME cell to edit).
  3. Export CSV                         -> writes DVR_IP,CLIP_NAME in the
     same IP order as the reference table.

The IP <-> DVR-label reference table is embedded below (from DVR.txt) since
that mapping is fixed. "Load DVR Map..." lets you swap in an updated
reference file (two columns, IP<TAB>LABEL, no header) if the site config
ever changes.
"""

import re
import csv
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import openpyxl
except ImportError:
    openpyxl = None

# ---------------------------------------------------------------------------
# Embedded default IP <-> DVR-label reference (from DVR.txt).
# Order here is preserved in the exported CSV.
# ---------------------------------------------------------------------------
DEFAULT_DVR_MAP_TEXT = """192.168.190.21\tDVR-1
192.168.190.22\tDVR-2
192.168.190.23\tDVR-3
192.168.190.24\tDVR-4
192.168.190.25\tDVR-5
192.168.190.26\tDVR-6
192.168.190.27\tDVR-7
192.168.190.28\tDVR-8
192.168.190.29\tDVR-9
192.168.190.30\tDVR-10
192.168.190.31\tDVR-11
192.168.190.32\tDVR-12
192.168.190.33\tDVR-13
192.168.190.34\tDVR-14
192.168.190.35\tDVR-15
192.168.190.36\tDVR-16
192.168.190.37\tDVR-17
192.168.190.38\tDVR-18
192.168.190.39\tDVR-19
192.168.190.40\tDVR-20
192.168.190.41\tDVR-21
192.168.190.42\tDVR-22
192.168.190.43\tDVR-23
192.168.190.44\tDVR-24
192.168.190.45\tDVR-25
192.168.190.46\tDVR-26
192.168.190.47\tDVR-27
192.168.190.48\tDVR-28
192.168.190.49\tDVR-29
192.168.190.50\tDVR-30
192.168.190.51\tDVR-31
192.168.190.52\tDVR-32
192.168.190.53\tDVR-33
192.168.190.54\tDVR-34
192.168.190.55\tDVR-35
192.168.190.56\tDVR-36
192.168.190.57\tDVR-37
192.168.190.58\tDVR-38
192.168.190.59\tDVR-39
192.168.190.60\tDVR-40
192.168.190.61\tDVR-41
192.168.190.62\tDVR-42
192.168.190.63\tDVR-43
192.168.190.64\tDVR-44
192.168.190.65\tDVR-45
192.168.190.66\tDVR-46
192.168.190.67\tDVRA-1
192.168.190.68\tDVRA-2
192.168.190.69\tDVRA-3
192.168.190.70\tDVRA-4
192.168.190.71\tDVRA-5
192.168.190.72\tDVRA-6
192.168.190.73\tDVRA-7
192.168.190.74\tDVRA-8
192.168.190.75\tDVRHLS-1
192.168.190.76\tDVRHLS-2
192.168.190.77\tDVRHLS-3
192.168.190.78\tDVRHLS-4
192.168.190.101\tDVR-47
192.168.190.102\tDVR-48
192.168.190.103\tDVR-49
192.168.190.104\tDVR-50
"""

# DVR-N and DVRHLS-N labels get auto-filled from the schedule sheet by
# taking the cell immediately to their right in the same row. DVRA-N clip
# assignments aren't reliably positioned on these sheets, so those rows
# are left for the tech to fill in by hand.
AUTO_LABEL_RE = re.compile(r'^DVR(HLS)?-\d+$', re.IGNORECASE)


def parse_dvr_map(text):
    """Parse IP<TAB>LABEL lines into an ordered list of (ip, label)."""
    entries = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t') if '\t' in line else line.split(',')
        if len(parts) != 2:
            raise ValueError(f"Line {lineno}: expected \"IP<TAB>LABEL\", got: {line!r}")
        ip, label = parts[0].strip(), parts[1].strip()
        entries.append((ip, label))
    return entries


def parse_schedule_xlsx(path):
    """
    Return {DVR-N label: clip name} by scanning every sheet for cells that
    are exactly "DVR-<number>" and taking the clip name from the next cell
    to the right in that same row.
    """
    if openpyxl is None:
        raise RuntimeError(
            "The 'openpyxl' package is required to read Excel files.\n"
            "Install it with:  pip install openpyxl"
        )
    wb = openpyxl.load_workbook(path, data_only=True)
    label_clip = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            cells = list(row)
            for i, cell in enumerate(cells):
                val = cell.value
                if not isinstance(val, str):
                    continue
                label = val.strip()
                if not AUTO_LABEL_RE.match(label):
                    continue
                label = label.upper()
                if label in label_clip:
                    continue  # first match wins
                if i + 1 < len(cells):
                    nxt = cells[i + 1].value
                    if nxt is not None and str(nxt).strip() != '':
                        label_clip[label] = str(nxt).strip()
    return label_clip


class DVRInventoryApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DVR Inventory Builder")
        self.geometry("760x640")
        self.minsize(620, 480)

        self.dvr_map = parse_dvr_map(DEFAULT_DVR_MAP_TEXT)  # [(ip, label), ...]
        self.schedule_path = None

        self._build_ui()
        self._populate_table(clip_lookup={})

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Button(top, text="1. Load Recording Schedule (.xlsx)...",
                   command=self.load_schedule).pack(side="left")
        ttk.Button(top, text="Load DVR Map...",
                   command=self.load_dvr_map).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Reset DVR Map to Default",
                   command=self.reset_dvr_map).pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Using built-in DVR map (62 devices). No schedule loaded yet.")
        ttk.Label(self, textvariable=self.status_var, padding=(10, 0)).pack(fill="x")

        mid = ttk.Frame(self, padding=10)
        mid.pack(fill="both", expand=True)

        cols = ("ip", "label", "clip")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("ip", text="DVR_IP")
        self.tree.heading("label", text="DVR LABEL")
        self.tree.heading("clip", text="CLIP_NAME  (double-click to edit)")
        self.tree.column("ip", width=150, anchor="w")
        self.tree.column("label", width=110, anchor="w")
        self.tree.column("clip", width=350, anchor="w")

        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self.tree.bind("<Double-1>", self._on_double_click)

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="2. Export CSV...", command=self.export_csv).pack(side="right")
        ttk.Label(bottom, text="Blank cells export as SPARE.").pack(side="left")

        self._edit_entry = None

    # -------------------------------------------------------------- table
    def _populate_table(self, clip_lookup):
        self.tree.delete(*self.tree.get_children())
        matched = 0
        for ip, label in self.dvr_map:
            clip = clip_lookup.get(label.upper(), "")
            if clip:
                matched += 1
            self.tree.insert("", "end", values=(ip, label, clip if clip else "SPARE"))
        return matched

    def _on_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != "#3":  # only CLIP_NAME is editable
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        x, y, w, h = self.tree.bbox(row_id, col)
        current = self.tree.set(row_id, "clip")

        if self._edit_entry is not None:
            self._edit_entry.destroy()

        entry = tk.Entry(self.tree)
        entry.insert(0, current)
        entry.select_range(0, "end")
        entry.focus()
        entry.place(x=x, y=y, width=w, height=h)
        self._edit_entry = entry

        def commit(_event=None):
            val = entry.get().strip() or "SPARE"
            self.tree.set(row_id, "clip", val)
            entry.destroy()
            self._edit_entry = None

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda e: entry.destroy())

    # ------------------------------------------------------------ actions
    def load_dvr_map(self):
        path = filedialog.askopenfilename(
            title="Select DVR IP map (IP<TAB>LABEL per line)",
            filetypes=[("Text/CSV", "*.txt *.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
            new_map = parse_dvr_map(text)
        except Exception as e:
            messagebox.showerror("Could not load DVR map", str(e))
            return
        self.dvr_map = new_map
        self.status_var.set(f"Loaded custom DVR map ({len(self.dvr_map)} devices) from {path}")
        self._populate_table(clip_lookup={})

    def reset_dvr_map(self):
        self.dvr_map = parse_dvr_map(DEFAULT_DVR_MAP_TEXT)
        self.status_var.set(f"Using built-in DVR map ({len(self.dvr_map)} devices).")
        self._populate_table(clip_lookup={})

    def load_schedule(self):
        path = filedialog.askopenfilename(
            title="Select Recording Schedule",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            label_clip = parse_schedule_xlsx(path)
        except Exception as e:
            messagebox.showerror("Could not read schedule", str(e))
            return
        self.schedule_path = path
        matched = self._populate_table(label_clip)
        total = len(self.dvr_map)
        self.status_var.set(
            f"Loaded {path.split('/')[-1].split(chr(92))[-1]}  —  "
            f"auto-filled {matched} of {total} rows. "
            f"DVRA-*/unmatched rows are SPARE — fill in by hand if needed."
        )
        messagebox.showinfo(
            "Schedule loaded",
            f"Auto-filled {matched} of {total} clip names from DVR-N and DVRHLS-N rows.\n\n"
            "Rows for DVRA-*, and anything the sheet didn't have a clear "
            "clip name for are marked SPARE — double-click a CLIP_NAME cell to fix "
            "before exporting."
        )

    def export_csv(self):
        if not self.tree.get_children():
            messagebox.showwarning("Nothing to export", "The table is empty.")
            return
        default_name = "inventory.csv"
        if self.schedule_path:
            base = self.schedule_path.replace("\\", "/").rsplit("/", 1)[-1]
            base = base.rsplit(".", 1)[0]
            default_name = f"{base}_inventory.csv"
        path = filedialog.asksaveasfilename(
            title="Save inventory CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["DVR_IP", "CLIP_NAME"])
                for row_id in self.tree.get_children():
                    ip, label, clip = self.tree.item(row_id, "values")
                    writer.writerow([ip, clip])
        except Exception as e:
            messagebox.showerror("Could not save file", str(e))
            return
        messagebox.showinfo("Saved", f"Inventory saved to:\n{path}")


def main():
    if openpyxl is None:
        print("WARNING: openpyxl is not installed. Install it with: pip install openpyxl",
              file=sys.stderr)
    app = DVRInventoryApp()
    app.mainloop()


if __name__ == "__main__":
    main()
