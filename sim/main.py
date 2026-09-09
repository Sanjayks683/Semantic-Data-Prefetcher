"""
main.py - Runs every trace in traces/ through the three cache configurations
(no prefetcher / stride / n-gram) and writes results/benchmark_results.csv.
"""

import os
import sys
import csv
from cache import Cache
from stride_prefetcher import StridePrefetcher
from ngram_prefetcher import NGramPrefetcher
from metrics import Metrics
from trace_parser import get_trace_iterator
from config import (CACHE_SETS, CACHE_WAYS, BLOCK_SIZE,
                    CACHE_CAPACITY_BYTES, NGRAM_DEPTH, PROFILE)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(BASE_DIR, "..", "traces")
RESULTS_DIR = os.path.join(BASE_DIR, "..", "results")

CSV_FIELDS = [
    "workload", "prefetcher", "accesses", "hits", "misses", "miss_rate",
    "prefetches_generated", "prefetches_filtered", "prefetches_issued",
    "useful_prefetches", "accuracy", "pollution", "coverage",
]


def build_prefetcher(prefetcher_type):
    if prefetcher_type == "stride":
        return StridePrefetcher()
    if prefetcher_type == "ngram":
        return NGramPrefetcher()
    if prefetcher_type == "none":
        return None
    raise ValueError(f"unknown prefetcher type: {prefetcher_type!r}")


def run_simulation(trace_path, prefetcher_type="none"):
    cache = Cache()
    metrics = Metrics(name=prefetcher_type.upper())
    prefetcher = build_prefetcher(prefetcher_type)

    for ip, addr in get_trace_iterator(trace_path):
        hit, is_useful = cache.access(addr, is_prefetch=False)
        metrics.record_access(hit, is_useful)

        if not hit:
            cache.insert(addr, is_prefetch=False)

        if prefetcher is not None:
            prefetch_addr = prefetcher.access(ip, addr)
            if prefetch_addr is not None:
                metrics.record_prefetch_generated()
                already_in_cache, _ = cache.access(prefetch_addr, is_prefetch=True)
                if already_in_cache:
                    # Redundant: costs no bandwidth, so it is not an issued
                    # prefetch and must not dilute the accuracy denominator.
                    metrics.record_prefetch_filtered()
                else:
                    evicted = cache.insert(prefetch_addr, is_prefetch=True)
                    metrics.record_prefetch(evicted)

    metrics.dead_prefetches = cache.dead_prefetch_evictions
    return metrics


def run_all_benchmarks():
    if not os.path.isdir(TRACES_DIR):
        print(f"Error: traces directory not found: {TRACES_DIR}")
        print("Run 'python generate_trace.py' first.")
        return 1

    os.makedirs(RESULTS_DIR, exist_ok=True)

    trace_files = sorted(
        f for f in os.listdir(TRACES_DIR)
        if f.endswith(".trace") or f.endswith(".xz")
    )

    if not trace_files:
        print(f"No trace files found in {TRACES_DIR}")
        print("Run 'python generate_trace.py' first.")
        return 1

    # v1 keeps the original filename so its results stay directly comparable;
    # any other profile writes alongside it rather than overwriting.
    suffix = "" if PROFILE == "v1" else f"_{PROFILE}"
    csv_path = os.path.join(RESULTS_DIR, f"benchmark_results{suffix}.csv")
    csv_rows = []

    print("\n" + "=" * 75)
    print("   L1 Cache Simulation: Stride vs Semantic N-Gram Prefetcher")
    print(f"   profile {PROFILE}: {NGRAM_DEPTH}-delta history window")
    print(f"   {CACHE_CAPACITY_BYTES // 1024} KB, {CACHE_WAYS}-way, "
          f"{CACHE_SETS} sets, {BLOCK_SIZE} B lines")
    print("=" * 75)

    for trace_file in trace_files:
        trace_path = os.path.join(TRACES_DIR, trace_file)
        trace_name = os.path.splitext(trace_file)[0]

        m_none = run_simulation(trace_path, prefetcher_type="none")
        baseline_misses = m_none.misses
        m_stride = run_simulation(trace_path, prefetcher_type="stride")
        m_ngram = run_simulation(trace_path, prefetcher_type="ngram")

        print(f"\nWorkload: {trace_name}")
        print(f"{'-'*75}")
        print(f"{'Metric':<25} | {'No Prefetch':<14} | {'Stride':<14} | {'N-Gram':<14}")
        print(f"{'-'*75}")
        print(f"{'Total Accesses':<25} | {m_none.total_accesses:<14,d} | {m_stride.total_accesses:<14,d} | {m_ngram.total_accesses:<14,d}")
        print(f"{'Cache Hits':<25} | {m_none.hits:<14,d} | {m_stride.hits:<14,d} | {m_ngram.hits:<14,d}")
        print(f"{'Cache Misses':<25} | {m_none.misses:<14,d} | {m_stride.misses:<14,d} | {m_ngram.misses:<14,d}")
        print(f"{'Miss Rate (%)':<25} | {m_none.miss_rate:<13.2f}% | {m_stride.miss_rate:<13.2f}% | {m_ngram.miss_rate:<13.2f}%")
        print(f"{'Prefetches Issued':<25} | {'N/A':<14} | {m_stride.prefetches_issued:<14,d} | {m_ngram.prefetches_issued:<14,d}")
        print(f"{'Prefetches Filtered':<25} | {'N/A':<14} | {m_stride.prefetches_filtered:<14,d} | {m_ngram.prefetches_filtered:<14,d}")
        print(f"{'Useful Prefetches':<25} | {'N/A':<14} | {m_stride.useful_prefetches:<14,d} | {m_ngram.useful_prefetches:<14,d}")
        print(f"{'Prefetch Accuracy (%)':<25} | {'N/A':<14} | {m_stride.accuracy:<13.2f}% | {m_ngram.accuracy:<13.2f}%")
        print(f"{'Cache Pollution (%)':<25} | {'N/A':<14} | {m_stride.pollution_rate:<13.2f}% | {m_ngram.pollution_rate:<13.2f}%")
        print(f"{'Prefetch Coverage (%)':<25} | {'0.00':<13}% | {m_stride.compute_coverage(baseline_misses):<13.2f}% | {m_ngram.compute_coverage(baseline_misses):<13.2f}%")
        print(f"{'-'*75}")

        for m, p_type in [(m_none, "No-Prefetch"), (m_stride, "Stride"), (m_ngram, "N-Gram")]:
            csv_rows.append({
                "workload": trace_name,
                "prefetcher": p_type,
                "accesses": m.total_accesses,
                "hits": m.hits,
                "misses": m.misses,
                "miss_rate": round(m.miss_rate, 4),
                "prefetches_generated": m.prefetches_generated,
                "prefetches_filtered": m.prefetches_filtered,
                "prefetches_issued": m.prefetches_issued,
                "useful_prefetches": m.useful_prefetches,
                "accuracy": round(m.accuracy, 4),
                "pollution": round(m.pollution_rate, 4),
                "coverage": round(m.compute_coverage(baseline_misses), 4),
            })

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nResults saved to: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_benchmarks())
