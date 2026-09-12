"""
plot_results.py - Bar charts comparing miss rate and coverage across prefetchers.

Series are looked up by (workload, prefetcher) rather than by row position, so a
missing or reordered row produces an explicit error instead of a silently
misaligned chart.
"""

import os
import sys

import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
CSV_PATH = os.path.join(RESULTS_DIR, "benchmark_results.csv")

PREFETCHERS = ["No-Prefetch", "Stride", "N-Gram"]
COLORS = {"No-Prefetch": "#e74c3c", "Stride": "#3498db", "N-Gram": "#2ecc71"}
LABELS = {"No-Prefetch": "No Prefetcher", "Stride": "Stride Prefetcher",
          "N-Gram": "N-Gram Prefetcher"}


def series(df, workloads, prefetcher, column):
    """Pull one metric for one prefetcher, ordered to match `workloads`."""
    subset = df[df["prefetcher"] == prefetcher].set_index("workload")

    missing = [w for w in workloads if w not in subset.index]
    if missing:
        raise ValueError(
            f"benchmark_results.csv has no '{prefetcher}' row for: {missing}. "
            "Re-run main.py to regenerate a complete result set."
        )

    return np.array([subset.loc[w, column] for w in workloads], dtype=float)


def autolabel(ax, rects, suffix="%"):
    for rect in rects:
        height = rect.get_height()
        ax.annotate(f"{height:.1f}{suffix}",
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8)


def bar_chart(df, workloads, prefetchers, column, title, ylabel, outfile):
    x = np.arange(len(workloads))
    width = 0.8 / len(prefetchers)
    offsets = (np.arange(len(prefetchers)) - (len(prefetchers) - 1) / 2) * width

    fig, ax = plt.subplots(figsize=(9, 5), dpi=300)

    peak = 0.0
    for offset, name in zip(offsets, prefetchers):
        values = series(df, workloads, name, column)
        peak = max(peak, float(values.max()) if values.size else 0.0)
        rects = ax.bar(x + offset, values, width, label=LABELS[name], color=COLORS[name])
        autolabel(ax, rects)

    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(x)
    ax.set_xticklabels([w.replace("_", " ").title() for w in workloads])
    ax.legend()
    ax.set_ylim(0, max(peak * 1.15, 10))
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    ax.set_axisbelow(True)

    plt.tight_layout()
    plt.savefig(os.path.join(RESULTS_DIR, outfile))
    plt.close(fig)


def generate_plots():
    if not os.path.exists(CSV_PATH):
        print(f"Error: {CSV_PATH} not found. Run main.py first.")
        return 1

    df = pd.read_csv(CSV_PATH)
    workloads = sorted(df["workload"].unique())

    bar_chart(df, workloads, PREFETCHERS, "miss_rate",
              "L1 Cache Miss Rate Across Prefetchers",
              "Cache Miss Rate (%)", "miss_rate_comparison.png")

    bar_chart(df, workloads, ["Stride", "N-Gram"], "coverage",
              "Prefetch Coverage Comparison",
              "Prefetch Coverage (%)", "prefetch_coverage_comparison.png")

    bar_chart(df, workloads, ["Stride", "N-Gram"], "accuracy",
              "Prefetch Accuracy Comparison",
              "Prefetch Accuracy (%)", "prefetch_accuracy_comparison.png")

    print(f"Plots generated in {os.path.normpath(RESULTS_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(generate_plots())
