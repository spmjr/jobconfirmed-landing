"""
rack_probe.py - read-only probe of AJA Ki Pro Rack units. Changes NOTHING on the units.

Usage:   python3 rack_probe.py                 (probes 192.168.190.67-74)
         python3 rack_probe.py 192.168.190.67  (probe specific IP(s))

Writes rack_probe_results.txt next to the script. Send that file back.
"""
import re
import sys
import json
import requests

DEFAULT_IPS = [f"192.168.190.{n}" for n in range(67, 75)]

# Parameters the Ultra code reads. We try each on the Rack and record the raw answer.
CANDIDATE_PARAMS = [
    "eParamID_SysName", "eParamID_CustomClipName", "eParamID_MediaState",
    "eParamID_CustomTake", "eParamID_EncodeType_Low_FR", "eParamID_TransportState",
    "eParamID_SelectedSlot", "eParamID_MediaLoading", "eParamID_ChangeSlot",
    "eParamID_StoragePath", "eParamID_CurrentFileSystem",
    "eParamID_CurrentMediaAvailable", "eParamID_Storage_Removed_Alarm",
    "eParamID_SWVersion", "eParamID_SoftwareVersion", "eParamID_ProductName",
]


def get(ip, path, params=None):
    try:
        r = requests.get(f"http://{ip}/{path}", params=params, timeout=5)
        return r.status_code, r.text
    except Exception as e:
        return None, f"ERROR: {e}"


def probe(ip, out):
    out.append(f"\n==================== {ip} ====================")
    code, body = get(ip, "index.html")
    out.append(f"index.html -> HTTP {code}, {len(body)} bytes")
    if code is None:
        out.append(body)
        return
    m = re.search(r"<title>(.*?)</title>", body, re.I | re.S)
    out.append(f"page title: {m.group(1).strip() if m else '(none)'}")

    # The web GUI's own JavaScript lists the parameter names this unit understands.
    scripts = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', body)
    out.append(f"scripts linked: {scripts}")
    found = set(re.findall(r"eParamID_\w+", body))
    for s in scripts:
        c, js = get(ip, s.lstrip("/"))
        if c == 200:
            found |= set(re.findall(r"eParamID_\w+", js))
    out.append(f"eParamID names found in web GUI ({len(found)}):")
    out.append("  " + ", ".join(sorted(found)))

    out.append("--- candidate parameter reads (read-only) ---")
    for p in CANDIDATE_PARAMS:
        c, txt = get(ip, "config", {"action": "get", "paramid": p})
        out.append(f"{p}: HTTP {c}: {txt.strip()[:300]}")

    out.append("--- clips ---")
    c, txt = get(ip, "clips", {"action": "get_playlists"})
    out.append(f"get_playlists: HTTP {c}: {txt.strip()[:600]}")


def main():
    ips = sys.argv[1:] or DEFAULT_IPS
    out = []
    for ip in ips:
        print(f"probing {ip} ...")
        probe(ip, out)
    with open("rack_probe_results.txt", "w") as f:
        f.write("\n".join(out))
    print("\nDone. Send back rack_probe_results.txt")


if __name__ == "__main__":
    main()
