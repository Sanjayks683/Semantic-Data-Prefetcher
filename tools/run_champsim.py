#!/usr/bin/env python3
"""
run_champsim.py - Evaluate the prefetchers on a real ChampSim trace.

The four traces in traces/ are synthetic and were written to contain learnable
patterns, so a pattern learner is guaranteed to do well on them. This runs the
same cache model over a real workload nobody here designed, which is the only
way to find out whether the design generalises.

Two placements are supported:

  --level l1   the prefetcher sees every memory access and fills L1.
               This is the configuration the README's results use.

  --level l2   an unprefetched L1 runs first; the prefetcher sees only the L1
               MISS stream and fills L2. This is where a correlation prefetcher
               normally lives in a real machine: the L1 has already absorbed the
               regular, high-locality traffic, so what survives is the irregular
               access this design targets.

Usage:
    python tools/run_champsim.py <trace> [--limit N] [--level l1|l2] [--csv out.csv]

    --limit    stop after N memory accesses (default 10,000,000)
    --l2-sets  L2 sets, must be a power of two (default 512 -> 256 KB 8-way)
    --l2-ways  L2 associativity (default 8)

All configurations see the same access stream from the same starting point, so
cold-start effects are identical across them and the comparison is fair even
without a separate warm-up phase.

Traces: https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu/
"""

import os
import sys
import csv
import time
import argparse
from array import array

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "sim"))

from cache import Cache                                    # noqa: E402
from stride_prefetcher import StridePrefetcher             # noqa: E402
from ngram_prefetcher import NGramPrefetcher               # noqa: E402
from metrics import Metrics                                # noqa: E402
from trace_parser import get_trace_iterator                # noqa: E402
from config import CACHE_SETS, CACHE_WAYS, BLOCK_SIZE      # noqa: E402


def build(kind):
    if kind == "stride":
        return StridePrefetcher()
    if kind == "ngram":
        return NGramPrefetcher()
    if kind == "none":
        return None
    raise ValueError(kind)


def offer_prefetch(cache, m, pf, ip, addr):
    """Let the predictor see one access and place any candidate into `cache`."""
    if pf is None:
        return
    cand = pf.access(ip, addr)
    if cand is None:
        return
    m.record_prefetch_generated()
    resident, _ = cache.access(cand, is_prefetch=True)
    if resident:
        m.record_prefetch_filtered()
    else:
        m.record_prefetch(cache.insert(cand, is_prefetch=True))


def simulate_l1(ips, addrs, kind):
    """Prefetcher sees every access and fills L1."""
    l1 = Cache()
    m = Metrics(name=kind.upper())
    pf = build(kind)

    for ip, addr in zip(ips, addrs):
        hit, useful = l1.access(addr, is_prefetch=False)
        m.record_access(hit, useful)
        if not hit:
            l1.insert(addr, is_prefetch=False)
        offer_prefetch(l1, m, pf, ip, addr)

    m.dead_prefetches = l1.dead_prefetch_evictions
    if pf is not None and hasattr(pf, "delta_overflows"):
        m.delta_overflows = pf.delta_overflows
    return m


def simulate_l2(ips, addrs, kind, l2_sets, l2_ways):
    """Unprefetched L1 in front; prefetcher sees L1 misses and fills L2.

    Metrics are reported with respect to L2: total_accesses is the number of L1
    misses that reached L2, and misses are the requests that then went to
    memory. Coverage therefore measures reduction in memory traffic, which is
    what an L2 prefetcher exists to do.
    """
    l1 = Cache()
    l2 = Cache(sets=l2_sets, ways=l2_ways)
    m = Metrics(name=kind.upper())
    pf = build(kind)

    l1_hits = 0

    for ip, addr in zip(ips, addrs):
        l1_hit, _ = l1.access(addr, is_prefetch=False)
        if l1_hit:
            l1_hits += 1
            continue

        # L1 miss: fill L1, and the request proceeds to L2.
        l1.insert(addr, is_prefetch=False)

        l2_hit, useful = l2.access(addr, is_prefetch=False)
        m.record_access(l2_hit, useful)
        if not l2_hit:
            l2.insert(addr, is_prefetch=False)

        # The predictor is trained on, and predicts from, the L1 miss stream.
        offer_prefetch(l2, m, pf, ip, addr)

    m.dead_prefetches = l2.dead_prefetch_evictions
    m.l1_hits = l1_hits
    if pf is not None and hasattr(pf, "delta_overflows"):
        m.delta_overflows = pf.delta_overflows
    return m


def load(trace, limit):
    """Materialise the access stream once, compactly.

    Two unsigned-64 arrays cost 16 bytes per access; a list of Python tuples
    costs roughly 70, which turns a 20M-access run into gigabytes.
    """
    print(f"Decompressing and parsing (limit {limit:,} accesses)...", flush=True)
    t0 = time.time()

    ips = array("Q")
    addrs = array("Q")
    for i, (ip, addr) in enumerate(get_trace_iterator(trace)):
        if i >= limit:
            break
        ips.append(ip)
        addrs.append(addr)
        if i and i % 5_000_000 == 0:
            print(f"  {i:,} accesses  ({time.time()-t0:.0f}s)", flush=True)

    print(f"  {len(ips):,} accesses parsed in {time.time()-t0:.0f}s "
          f"({(len(ips)*16)/2**20:.0f} MB resident)\n", flush=True)
    return ips, addrs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--limit", type=int, default=10_000_000)
    ap.add_argument("--level", choices=("l1", "l2"), default="l1")
    ap.add_argument("--l2-sets", type=int, default=512)
    ap.add_argument("--l2-ways", type=int, default=8)
    ap.add_argument("--csv")
    args = ap.parse_args()

    if not os.path.exists(args.trace):
        print(f"error: {args.trace} not found")
        return 2

    name = os.path.basename(args.trace).split(".")[0]
    ips, addrs = load(args.trace, args.limit)
    if not len(ips):
        print("error: no accesses parsed")
        return 2

    n_acc = len(ips)
    unique_ips = len(set(ips))
    l2_kb = args.l2_sets * args.l2_ways * BLOCK_SIZE // 1024

    results = {}
    for kind in ("none", "stride", "ngram"):
        t0 = time.time()
        if args.level == "l1":
            results[kind] = simulate_l1(ips, addrs, kind)
        else:
            results[kind] = simulate_l2(ips, addrs, kind, args.l2_sets, args.l2_ways)
        print(f"  {kind:<7} done ({time.time()-t0:.0f}s)", flush=True)

    base = results["none"].misses
    n, s, g = results["none"], results["stride"], results["ngram"]

    l1_kb = CACHE_SETS * CACHE_WAYS * BLOCK_SIZE // 1024

    print("\n" + "=" * 75)
    print(f"   ChampSim workload: {name}")
    if args.level == "l1":
        print(f"   Prefetching into L1: {l1_kb} KB, {CACHE_WAYS}-way, "
              f"{CACHE_SETS} sets, {BLOCK_SIZE} B lines")
        print(f"   {n_acc:,} memory accesses, {unique_ips:,} distinct IPs")
    else:
        print(f"   L1 {l1_kb} KB (no prefetcher)  ->  "
              f"prefetching into L2 {l2_kb} KB, {args.l2_ways}-way")
        print(f"   {n_acc:,} memory accesses, {unique_ips:,} distinct IPs")
        print(f"   {n.total_accesses:,} reached L2 "
              f"({n.total_accesses/n_acc*100:.1f}% L1 miss rate)")
        print("   Rates below are with respect to L2; coverage = reduction in "
              "memory traffic")
    print("=" * 75)
    print(f"{'Metric':<25} | {'No Prefetch':<14} | {'Stride':<14} | {'N-Gram':<14}")
    print("-" * 75)
    print(f"{'Misses to memory' if args.level=='l2' else 'Cache Misses':<25} | "
          f"{n.misses:<14,d} | {s.misses:<14,d} | {g.misses:<14,d}")
    print(f"{'Miss Rate (%)':<25} | {n.miss_rate:<13.2f}% | {s.miss_rate:<13.2f}% | {g.miss_rate:<13.2f}%")
    print(f"{'Prefetches Generated':<25} | {'N/A':<14} | {s.prefetches_generated:<14,d} | {g.prefetches_generated:<14,d}")
    print(f"{'Prefetches Filtered':<25} | {'N/A':<14} | {s.prefetches_filtered:<14,d} | {g.prefetches_filtered:<14,d}")
    print(f"{'Prefetches Issued':<25} | {'N/A':<14} | {s.prefetches_issued:<14,d} | {g.prefetches_issued:<14,d}")
    print(f"{'Useful Prefetches':<25} | {'N/A':<14} | {s.useful_prefetches:<14,d} | {g.useful_prefetches:<14,d}")
    print(f"{'Prefetch Accuracy (%)':<25} | {'N/A':<14} | {s.accuracy:<13.2f}% | {g.accuracy:<13.2f}%")
    print(f"{'Cache Pollution (%)':<25} | {'N/A':<14} | {s.pollution_rate:<13.2f}% | {g.pollution_rate:<13.2f}%")
    print(f"{'Prefetch Coverage (%)':<25} | {'0.00':<13}% | {s.compute_coverage(base):<13.2f}% | {g.compute_coverage(base):<13.2f}%")
    print("-" * 75)
    if hasattr(g, "delta_overflows"):
        seen = g.total_accesses if args.level == "l2" else n_acc
        pct = g.delta_overflows / seen * 100
        print(f"N-gram delta overflows: {g.delta_overflows:,} "
              f"({pct:.2f}% of the accesses it observed exceeded the 16-bit "
              f"delta field)")
    print("=" * 75)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["workload", "level", "prefetcher", "accesses", "hits",
                        "misses", "miss_rate", "prefetches_generated",
                        "prefetches_filtered", "prefetches_issued",
                        "useful_prefetches", "accuracy", "pollution", "coverage"])
            for kind, label in (("none", "No-Prefetch"), ("stride", "Stride"),
                                ("ngram", "N-Gram")):
                m = results[kind]
                w.writerow([name, args.level.upper(), label, m.total_accesses,
                            m.hits, m.misses, round(m.miss_rate, 4),
                            m.prefetches_generated, m.prefetches_filtered,
                            m.prefetches_issued, m.useful_prefetches,
                            round(m.accuracy, 4), round(m.pollution_rate, 4),
                            round(m.compute_coverage(base), 4)])
        print(f"\nSaved to {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
