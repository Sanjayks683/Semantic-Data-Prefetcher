#!/usr/bin/env python3
"""
sizing_sweep.py - Find the table size and delta width a workload actually needs.

The shipped configuration (1,024 entries, 16-bit deltas) was sized against the
synthetic traces, which present only a few hundred distinct delta contexts. Real
workloads present hundreds of thousands. This sweeps the two parameters that
matter and reports where the design starts to earn its area, with a stride
prefetcher as the reference line.

Usage:
    python tools/sizing_sweep.py <trace> [--level l1|l2] [--limit N] [--csv out.csv]

    --level    l1: predictor sees every access and fills L1
               l2: unprefetched L1 in front, predictor sees the miss stream and
                   fills L2 (default - where a correlation prefetcher belongs)
    --limit    accesses to simulate (default 20,000,000)

The sweep drives sim/ngram_prefetcher.NGramPrefetcher directly, so it measures
the same model the RTL is checked against rather than a re-implementation.
"""

import os
import sys
import csv
import time
import argparse
from array import array

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "sim"))

from cache import Cache                          # noqa: E402
from metrics import Metrics                      # noqa: E402
from stride_prefetcher import StridePrefetcher   # noqa: E402
from ngram_prefetcher import NGramPrefetcher     # noqa: E402
from trace_parser import get_trace_iterator      # noqa: E402
from config import CONF_BITS                     # noqa: E402

DEFAULT_ENTRIES = [1024, 16384, 65536, 262144, 524288, 2097152]
DEFAULT_WIDTHS = [16, 48]
MAX_TAG_BITS = 16


def tag_bits_for(entries, delta_width):
    """Widest sensible tag that still fits beside the index in one delta word."""
    index_bits = entries.bit_length() - 1
    return max(1, min(MAX_TAG_BITS, delta_width - index_bits))


def storage_bytes(entries, delta_width, tag_bits):
    return entries * (1 + tag_bits + delta_width + CONF_BITS) / 8


def load(trace, limit):
    print(f"Parsing {os.path.basename(trace)} (limit {limit:,})...", flush=True)
    t0 = time.time()
    ips, addrs = array("Q"), array("Q")
    for i, (ip, addr) in enumerate(get_trace_iterator(trace)):
        if i >= limit:
            break
        ips.append(ip)
        addrs.append(addr)
    print(f"  {len(ips):,} accesses in {time.time()-t0:.0f}s\n", flush=True)
    return ips, addrs


def run(ips, addrs, pf, level, l2_sets, l2_ways):
    """Simulate one predictor at the requested cache level."""
    l1 = Cache()
    l2 = Cache(sets=l2_sets, ways=l2_ways) if level == "l2" else None
    target = l2 if level == "l2" else l1
    m = Metrics()

    for ip, addr in zip(ips, addrs):
        if level == "l2":
            l1_hit, _ = l1.access(addr, is_prefetch=False)
            if l1_hit:
                continue
            l1.insert(addr, is_prefetch=False)

        hit, useful = target.access(addr, is_prefetch=False)
        m.record_access(hit, useful)
        if not hit:
            target.insert(addr, is_prefetch=False)

        if pf is not None:
            cand = pf.access(ip, addr)
            if cand is not None:
                m.record_prefetch_generated()
                resident, _ = target.access(cand, is_prefetch=True)
                if resident:
                    m.record_prefetch_filtered()
                else:
                    m.record_prefetch(target.insert(cand, is_prefetch=True))

    m.dead_prefetches = target.dead_prefetch_evictions
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--level", choices=("l1", "l2"), default="l2")
    ap.add_argument("--limit", type=int, default=20_000_000)
    ap.add_argument("--l2-sets", type=int, default=512)
    ap.add_argument("--l2-ways", type=int, default=8)
    ap.add_argument("--csv")
    args = ap.parse_args()

    if not os.path.exists(args.trace):
        print(f"error: {args.trace} not found")
        return 2

    name = os.path.basename(args.trace).split(".")[0]
    ips, addrs = load(args.trace, args.limit)

    baseline = run(ips, addrs, None, args.level, args.l2_sets, args.l2_ways)
    base_misses = baseline.misses

    stride = run(ips, addrs, StridePrefetcher(), args.level, args.l2_sets, args.l2_ways)
    stride_cov = stride.compute_coverage(base_misses)

    print("=" * 78)
    print(f"  {name} - prefetching into {args.level.upper()}")
    print(f"  {len(ips):,} accesses, {baseline.total_accesses:,} reached "
          f"{args.level.upper()}, {base_misses:,} baseline misses")
    print("=" * 78)
    print(f"  STRIDE reference: {stride_cov:.2f}% coverage, "
          f"{stride.accuracy:.2f}% accuracy, ~2 KB\n")

    print(f"{'delta':>6} {'entries':>10} {'tag':>4} {'storage':>10} {'ovf%':>7} "
          f"{'cover%':>8} {'accur%':>8} {'vs stride':>10}")
    print("-" * 78)

    rows = []
    for dw in DEFAULT_WIDTHS:
        for entries in DEFAULT_ENTRIES:
            tb = tag_bits_for(entries, dw)
            if entries.bit_length() - 1 + tb > dw:
                continue    # index + tag cannot fit in one delta word
            pf = NGramPrefetcher(table_size=entries, delta_width=dw, tag_bits=tb)
            m = run(ips, addrs, pf, args.level, args.l2_sets, args.l2_ways)

            cov = m.compute_coverage(base_misses)
            sb = storage_bytes(entries, dw, tb)
            ovf = pf.delta_overflows / max(1, baseline.total_accesses) * 100
            delta_vs = cov - stride_cov

            print(f"{dw:>6} {entries:>10,} {tb:>4} {sb/1024:>9.0f}K {ovf:>6.1f}% "
                  f"{cov:>7.2f}% {m.accuracy:>7.2f}% {delta_vs:>+9.2f} "
                  f"{'BEATS' if delta_vs > 0 else ''}")

            rows.append({
                "workload": name, "level": args.level.upper(),
                "delta_width": dw, "entries": entries, "tag_bits": tb,
                "storage_bytes": int(sb), "overflow_pct": round(ovf, 4),
                "coverage": round(cov, 4), "accuracy": round(m.accuracy, 4),
                "pollution": round(m.pollution_rate, 4),
                "issued": m.prefetches_issued,
                "stride_coverage": round(stride_cov, 4),
            })

    print("-" * 78)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Saved to {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
