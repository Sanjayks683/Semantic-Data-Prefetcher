#!/usr/bin/env python3
"""
fetch_traces.py - Download the real SPEC ChampSim traces used in the README.

The synthetic traces in traces/ are committed. The real ones are hundreds of
megabytes each, so they are fetched on demand into traces/champsim/ (which is
gitignored) instead.

Usage:
    python tools/fetch_traces.py              # fetch every known trace
    python tools/fetch_traces.py mcf          # fetch one
    python tools/fetch_traces.py --list       # show what is known and what is present

A trace already on disk with the expected size is left alone, so re-running is
cheap. Downloads go to a .part file and are only renamed into place once the
byte count matches, so an interrupted run never leaves a truncated trace that
looks complete.

Source: the DPC-3 trace set, https://dpc3.compas.cs.stonybrook.edu/champsim-traces/
"""

import os
import sys
import time
import argparse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(REPO, "traces", "champsim")
BASE_URL = "https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu/"

# Stored under short names so results CSVs report "mcf" rather than "605".
TRACES = {
    "mcf": {
        "file": "605.mcf_s-472B.champsimtrace.xz",
        "bytes": 366_345_448,
        "about": "SPEC CPU2017 minimum-cost-flow solver; graph pointer chasing",
    },
    "omnetpp": {
        "file": "620.omnetpp_s-874B.champsimtrace.xz",
        "bytes": 802_283_392,
        "about": "SPEC CPU2017 discrete-event network simulator; irregular C++ heap access",
    },
}

CHUNK = 1 << 20


def local_path(name, dest):
    return os.path.join(dest, f"{name}.xz")


def is_present(name, dest):
    path = local_path(name, dest)
    return os.path.exists(path) and os.path.getsize(path) == TRACES[name]["bytes"]


def download(name, dest):
    info = TRACES[name]
    path = local_path(name, dest)
    part = path + ".part"
    url = BASE_URL + info["file"]
    expected = info["bytes"]

    if is_present(name, dest):
        print(f"  {name:<8} already present ({expected / 2**20:.0f} MB), skipping")
        return True

    os.makedirs(dest, exist_ok=True)
    print(f"  {name:<8} {url}")
    print(f"           {expected / 2**20:.0f} MB -> {path}")

    t0 = time.time()
    got = 0
    next_report = 50 * 2**20
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:
            while True:
                block = resp.read(CHUNK)
                if not block:
                    break
                out.write(block)
                got += len(block)
                if got >= next_report:
                    rate = got / max(1e-6, time.time() - t0) / 2**20
                    print(f"           {got / 2**20:6.0f} / {expected / 2**20:.0f} MB "
                          f"({rate:.1f} MB/s)", flush=True)
                    next_report += 50 * 2**20
    except Exception as exc:
        print(f"  {name:<8} FAILED: {exc}")
        return False

    if got != expected:
        print(f"  {name:<8} FAILED: received {got:,} bytes, expected {expected:,}; "
              f"partial file kept at {part}")
        return False

    os.replace(part, path)
    print(f"  {name:<8} done in {time.time() - t0:.0f}s")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help=f"traces to fetch (default: all of {sorted(TRACES)})")
    ap.add_argument("--dest", default=DEST, help="download directory")
    ap.add_argument("--list", action="store_true", help="list known traces and exit")
    args = ap.parse_args()

    if args.list:
        for name, info in TRACES.items():
            state = "present" if is_present(name, args.dest) else "missing"
            print(f"  {name:<8} {info['bytes'] / 2**20:6.0f} MB  {state:<8} {info['about']}")
        return 0

    names = args.names or list(TRACES)
    unknown = [n for n in names if n not in TRACES]
    if unknown:
        print(f"error: unknown trace(s) {unknown}; known: {sorted(TRACES)}")
        return 2

    ok = all([download(n, args.dest) for n in names])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
