"""
rack_probe2.py - read-only. Pulls the Rack web GUI's own page + script and extracts the
sections that define format, slot-switch, slot names and encode types. Changes NOTHING.

Usage:  python3 rack_probe2.py                 (uses 192.168.190.67)
        python3 rack_probe2.py 192.168.190.68
Output: rack_probe2_results.txt  (+ rack_index.html and rack_common.js saved for reference)
Send back rack_probe2_results.txt
"""
import re
import sys
import requests

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.190.67"
KEYS = ["FormatMedia", "StorageCommand", "ChangeSlot", "SelectedSlot", "EncodeType",
        "MediaState", "ClipNumber", "CustomTake", "RecordFormat", "NoSlotClip"]
WINDOW = 500


def fetch(path):
    r = requests.get(f"http://{ip}/{path}", timeout=10)
    r.raise_for_status()
    return r.text


out = [f"Rack probe 2 for {ip}"]
for label, path, save in [("index.html", "index.html", "rack_index.html"),
                          ("kipro.common.js", "js/kipro.common.js", "rack_common.js")]:
    try:
        txt = fetch(path)
    except Exception as e:
        out.append(f"\n##### {label}: ERROR {e}")
        continue
    with open(save, "w", encoding="utf-8") as f:
        f.write(txt)
    out.append(f"\n##### {label}: {len(txt)} bytes (saved as {save})")
    for k in KEYS:
        spans = []
        for m in re.finditer(re.escape(k), txt):
            s, e = max(0, m.start() - WINDOW), min(len(txt), m.end() + WINDOW)
            if spans and s <= spans[-1][1]:
                spans[-1][1] = e
            else:
                spans.append([s, e])
        out.append(f"\n=== {label} :: {k} ({len(spans)} region(s)) ===")
        for s, e in spans[:6]:
            out.append(txt[s:e].replace("\r", ""))
            out.append("-----")

with open("rack_probe2_results.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("Done. Send back rack_probe2_results.txt")
