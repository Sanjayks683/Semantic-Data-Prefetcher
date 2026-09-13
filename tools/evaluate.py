#!/usr/bin/env python3
"""
evaluate.py - Run the full prefetcher evaluation matrix across real traces.

Answers, on every SPEC trace in traces/champsim/, with one consistent method:

  shipped   How do the configurations as originally built do at L1, with
            instant prefetches? (the README's "as shipped" question)
  latency   How much do realistic prefetch arrival delays cost?
  design    With latency modelled, what do lookahead depth (degree) and
            multiple deltas per entry (slots) buy, against a stride prefetcher
            given the same lookahead?

Usage:
    python tools/evaluate.py                        # all matrices, all traces
    python tools/evaluate.py --matrix design        # one matrix
    python tools/evaluate.py --traces mcf gcc --limit 5000000

Each trace is decompressed once and cached as raw arrays under
traces/champsim/.cache/, then (trace, configuration) jobs run in parallel.
Results are written as one tidy CSV per matrix in results/.

The headline latency is HEADLINE_LATENCY accesses. It was fixed before any
latency results were produced; see the README for the reasoning.
"""

import os
import sys
import csv
import time
import argparse
from array import array
from multiprocessing import Pool

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "sim"))

from simulator import simulate                   # noqa: E402
from stride_prefetcher import StridePrefetcher   # noqa: E402
from ngram_prefetcher import NGramPrefetcher     # noqa: E402
from trace_parser import get_trace_iterator      # noqa: E402

TRACE_DIR = os.path.join(REPO, "traces", "champsim")
CACHE_DIR = os.path.join(TRACE_DIR, ".cache")
RESULTS_DIR = os.path.join(REPO, "results")

ALL_TRACES = ["mcf", "omnetpp", "gcc", "xalancbmk", "leela", "xz", "lbm", "bwaves"]

HEADLINE_LATENCY = 64
LATENCIES = [0, 4, 16, 64, 256]

# The resized operating point established by tools/sizing_sweep.py.
RESIZED = {"table_size": 524288, "delta_width": 48, "tag_bits": 16}


# ---------------------------------------------------------------- matrices --

def cfg(prefetcher, level, latency, **params):
    parts = [prefetcher, level, f"lat{latency}"] + [f"{k}{v}" for k, v in sorted(params.items())
                                                    if k not in RESIZED]
    return {"id": "-".join(parts), "prefetcher": prefetcher, "level": level,
            "latency": latency, "params": params}


def matrix_shipped():
    """The configurations as originally built: L1, instant arrival."""
    return [
        cfg("none", "l1", 0),
        cfg("stride", "l1", 0, degree=1),
        cfg("ngram", "l1", 0, depth=3),        # v1
        cfg("ngram", "l1", 0, depth=2),        # v2
    ]


def matrix_latency():
    """Sensitivity to prefetch arrival delay at L2."""
    out = [cfg("none", "l2", 0)]
    for lat in LATENCIES:
        out.append(cfg("stride", "l2", lat, degree=1))
        out.append(cfg("stride", "l2", lat, degree=4))
        out.append(cfg("ngram", "l2", lat, depth=2, degree=1, slots=1, **RESIZED))
        out.append(cfg("ngram", "l2", lat, depth=2, degree=4, slots=1, **RESIZED))
    return out


def matrix_design():
    """Lookahead and multi-slot, at L2 with the headline latency."""
    lat = HEADLINE_LATENCY
    out = [cfg("none", "l2", 0)]
    for degree in (1, 2, 4, 8):
        out.append(cfg("stride", "l2", lat, degree=degree))
    for depth in (1, 2):
        for degree in (1, 2, 4, 8):
            for slots in (1, 2, 4):
                out.append(cfg("ngram", "l2", lat, depth=depth, degree=degree,
                               slots=slots, **RESIZED))
    return out


MATRICES = {"shipped": matrix_shipped, "latency": matrix_latency, "design": matrix_design}


# ------------------------------------------------------------------- traces --

def cache_paths(name, limit):
    stem = os.path.join(CACHE_DIR, f"{name}.{limit}")
    return stem + ".ips", stem + ".addrs"


def build_cache(job):
    """Decompress the first `limit` accesses of a trace into raw arrays, once."""
    name, limit = job
    ips_path, addrs_path = cache_paths(name, limit)
    if os.path.exists(ips_path) and os.path.exists(addrs_path):
        return name, "cached", 0.0

    t0 = time.time()
    ips, addrs = array("Q"), array("Q")
    for i, (ip, addr) in enumerate(get_trace_iterator(os.path.join(TRACE_DIR, f"{name}.xz"))):
        if i >= limit:
            break
        ips.append(ip)
        addrs.append(addr)

    os.makedirs(CACHE_DIR, exist_ok=True)
    for path, arr in ((ips_path, ips), (addrs_path, addrs)):
        with open(path + ".part", "wb") as f:
            arr.tofile(f)
        os.replace(path + ".part", path)
    return name, f"{len(ips):,} accesses", time.time() - t0


def load_cache(name, limit):
    ips, addrs = array("Q"), array("Q")
    ips_path, addrs_path = cache_paths(name, limit)
    n = os.path.getsize(ips_path) // 8
    with open(ips_path, "rb") as f:
        ips.fromfile(f, n)
    with open(addrs_path, "rb") as f:
        addrs.fromfile(f, n)
    return ips, addrs


# --------------------------------------------------------------------- jobs --

def build_prefetcher(c):
    p = c["params"]
    if c["prefetcher"] == "none":
        return None
    if c["prefetcher"] == "stride":
        return StridePrefetcher(degree=p.get("degree", 1))
    return NGramPrefetcher(**p)


def run_job(job):
    name, limit, c = job
    ips, addrs = load_cache(name, limit)
    pf = build_prefetcher(c)
    t0 = time.time()
    m = simulate(zip(ips, addrs), prefetcher=pf, level=c["level"], latency=c["latency"])
    p = c["params"]
    return {
        "workload": name,
        "config": c["id"],
        "prefetcher": c["prefetcher"],
        "level": c["level"].upper(),
        "latency": c["latency"],
        "depth": p.get("depth", ""),
        "degree": p.get("degree", ""),
        "slots": p.get("slots", ""),
        "table_entries": p.get("table_size", 1024 if c["prefetcher"] == "ngram" else ""),
        "delta_width": p.get("delta_width", 16 if c["prefetcher"] == "ngram" else ""),
        "accesses": len(ips),
        "reached_level": m.total_accesses,
        "misses": m.misses,
        "miss_rate": round(m.miss_rate, 4),
        "generated": m.prefetches_generated,
        "filtered": m.prefetches_filtered,
        "issued": m.prefetches_issued,
        "useful": m.useful_prefetches,
        "late": m.late_prefetches,
        "accuracy": round(m.accuracy, 4),
        "late_rate": round(m.late_rate, 4),
        "pollution": round(m.pollution_rate, 4),
        "overflows": getattr(m, "delta_overflows", ""),
        "seconds": round(time.time() - t0, 1),
    }


def add_coverage(rows):
    """Coverage against the no-prefetch baseline for the same trace and level."""
    base = {(r["workload"], r["level"]): r["misses"] for r in rows if r["prefetcher"] == "none"}
    for r in rows:
        b = base.get((r["workload"], r["level"]))
        r["baseline_misses"] = b if b is not None else ""
        # Signed: negative means the prefetcher caused more misses than no
        # prefetcher at all. Clamping to zero hid that on 42 of the first 224
        # design runs.
        r["coverage"] = round((b - r["misses"]) / b * 100, 4) if b else ""
    return rows


def rescore(path):
    """Recompute coverage in an existing results CSV from its stored miss counts."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["misses"] = int(r["misses"])
    rows = add_coverage(rows)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


FIELDS = ["workload", "config", "prefetcher", "level", "latency", "depth", "degree",
          "slots", "table_entries", "delta_width", "accesses", "reached_level",
          "baseline_misses", "misses", "miss_rate", "coverage", "accuracy",
          "generated", "filtered", "issued", "useful", "late", "late_rate",
          "pollution", "overflows", "seconds"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", choices=sorted(MATRICES) + ["all"], default="all")
    ap.add_argument("--traces", nargs="+", default=ALL_TRACES)
    ap.add_argument("--limit", type=int, default=20_000_000)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--rescore", action="store_true",
                    help="recompute coverage in existing results CSVs without re-simulating")
    args = ap.parse_args()

    if args.rescore:
        for mname in sorted(MATRICES):
            path = os.path.join(RESULTS_DIR, f"evaluation_{mname}.csv")
            if os.path.exists(path):
                print(f"rescored {rescore(path)} rows in {os.path.relpath(path, REPO)}")
        return 0

    missing = [t for t in args.traces if not os.path.exists(os.path.join(TRACE_DIR, f"{t}.xz"))]
    if missing:
        print(f"error: missing traces {missing}; run tools/fetch_traces.py")
        return 2

    names = sorted(MATRICES) if args.matrix == "all" else [args.matrix]

    with Pool(args.workers) as pool:
        print(f"Preparing {len(args.traces)} traces (limit {args.limit:,})...", flush=True)
        for name, state, secs in pool.imap_unordered(build_cache,
                                                     [(t, args.limit) for t in args.traces]):
            print(f"  {name:<10} {state}" + (f" in {secs:.0f}s" if secs else ""), flush=True)

        for mname in names:
            configs = MATRICES[mname]()
            jobs = [(t, args.limit, c) for t in args.traces for c in configs]
            # Longest jobs first, so the slow tail finishes alongside the rest.
            jobs.sort(key=lambda j: -(j[2]["params"].get("degree", 1)
                                       * j[2]["params"].get("slots", 1)))
            print(f"\n[{mname}] {len(configs)} configurations x {len(args.traces)} traces "
                  f"= {len(jobs)} runs on {args.workers} workers", flush=True)

            rows, t0 = [], time.time()
            for i, row in enumerate(pool.imap_unordered(run_job, jobs), 1):
                rows.append(row)
                if i % max(1, len(jobs) // 20) == 0 or i == len(jobs):
                    print(f"  {i}/{len(jobs)} done ({time.time()-t0:.0f}s)", flush=True)

            rows = add_coverage(rows)
            rows.sort(key=lambda r: (ALL_TRACES.index(r["workload"])
                                     if r["workload"] in ALL_TRACES else 99, r["config"]))
            out = os.path.join(RESULTS_DIR, f"evaluation_{mname}.csv")
            with open(out, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            print(f"  -> {os.path.relpath(out, REPO)}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
