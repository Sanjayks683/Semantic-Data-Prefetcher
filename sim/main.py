import os
import csv
from cache import Cache
from stride_prefetcher import StridePrefetcher
from ngram_prefetcher import NGramPrefetcher
from metrics import Metrics
from trace_parser import get_trace_iterator


def run_simulation(trace_path, prefetcher_type="none"):
    cache = Cache()
    metrics = Metrics(name=prefetcher_type.upper())

    if prefetcher_type == "stride":
        prefetcher = StridePrefetcher()
    elif prefetcher_type == "ngram":
        prefetcher = NGramPrefetcher()
    else:
        prefetcher = None

    for ip, addr in get_trace_iterator(trace_path):
        hit, is_useful = cache.access(addr, is_prefetch=False)
        metrics.record_access(hit, is_useful)

        if not hit:
            cache.insert(addr, is_prefetch=False)

        if prefetcher is not None:
            prefetch_addr = prefetcher.access(ip, addr)
            if prefetch_addr is not None:
                already_in_cache, _ = cache.access(prefetch_addr, is_prefetch=True)
                if not already_in_cache:
                    evicted = cache.insert(prefetch_addr, is_prefetch=True)
                    metrics.record_prefetch(evicted)

    return metrics


def run_all_benchmarks():
    base_dir = os.path.dirname(__file__)
    traces_dir = os.path.join(base_dir, "..", "traces")
    results_dir = os.path.join(base_dir, "..", "results")
    os.makedirs(results_dir, exist_ok=True)

    trace_files = [f for f in os.listdir(traces_dir) if f.endswith(".trace") or f.endswith(".xz")]
    
    if not trace_files:
        print("No trace files found in traces/")
        return

    csv_path = os.path.join(results_dir, "benchmark_results.csv")
    csv_rows = []

    print("\n" + "="*75)
    print("   L1 Cache Simulation: Stride vs Semantic N-Gram Prefetcher")
    print("="*75)

    for trace_file in trace_files:
        trace_path = os.path.join(traces_dir, trace_file)
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
        print(f"{'Useful Prefetches':<25} | {'N/A':<14} | {m_stride.useful_prefetches:<14,d} | {m_ngram.useful_prefetches:<14,d}")
        print(f"{'Prefetch Accuracy (%)':<25} | {'N/A':<14} | {m_stride.accuracy:<13.2f}% | {m_ngram.accuracy:<13.2f}%")
        print(f"{'Prefetch Coverage (%)':<25} | {'0.00%':<14} | {m_stride.compute_coverage(baseline_misses):<13.2f}% | {m_ngram.compute_coverage(baseline_misses):<13.2f}%")
        print(f"{'-'*75}")

        for m, p_type in [(m_none, "No-Prefetch"), (m_stride, "Stride"), (m_ngram, "N-Gram")]:
            csv_rows.append({
                "workload": trace_name,
                "prefetcher": p_type,
                "accesses": m.total_accesses,
                "hits": m.hits,
                "misses": m.misses,
                "miss_rate": m.miss_rate,
                "prefetches_issued": m.prefetches_issued,
                "useful_prefetches": m.useful_prefetches,
                "accuracy": m.accuracy,
                "coverage": m.compute_coverage(baseline_misses)
            })

    with open(csv_path, "w", newline="") as f:
        fieldnames = ["workload", "prefetcher", "accesses", "hits", "misses", "miss_rate", "prefetches_issued", "useful_prefetches", "accuracy", "coverage"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nResults saved to: {csv_path}")


if __name__ == "__main__":
    run_all_benchmarks()
