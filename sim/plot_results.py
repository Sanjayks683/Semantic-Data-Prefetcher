import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np


def generate_plots():
    results_dir = os.path.join(os.path.dirname(__file__), "..", "results")
    csv_path = os.path.join(results_dir, "benchmark_results.csv")

    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found.")
        return

    df = pd.read_csv(csv_path)

    workloads = df['workload'].unique()
    x = np.arange(len(workloads))
    width = 0.25

    # 1. Miss Rate Plot
    fig, ax = plt.subplots(figsize=(9, 5), dpi=300)

    miss_none = df[df['prefetcher'] == 'No-Prefetch']['miss_rate'].values
    miss_stride = df[df['prefetcher'] == 'Stride']['miss_rate'].values
    miss_ngram = df[df['prefetcher'] == 'N-Gram']['miss_rate'].values

    rects1 = ax.bar(x - width, miss_none, width, label='No Prefetcher', color='#e74c3c')
    rects2 = ax.bar(x, miss_stride, width, label='Stride Prefetcher', color='#3498db')
    rects3 = ax.bar(x + width, miss_ngram, width, label='N-Gram Prefetcher', color='#2ecc71')

    ax.set_ylabel('Cache Miss Rate (%)')
    ax.set_title('L1 Cache Miss Rate Across Prefetchers')
    ax.set_xticks(x)
    ax.set_xticklabels([w.replace('_', ' ').title() for w in workloads])
    ax.legend()
    ax.set_ylim(0, 110)

    def autolabel(rects):
        for rect in rects:
            height = rect.get_height()
            ax.annotate(f'{height:.1f}%',
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3),
                        textcoords="offset points",
                        ha='center', va='bottom', fontsize=8)

    autolabel(rects1)
    autolabel(rects2)
    autolabel(rects3)

    plt.tight_layout()
    plt.savefig(os.path.join(results_dir, "miss_rate_comparison.png"))
    plt.close()

    # 2. Coverage Plot
    fig, ax = plt.subplots(figsize=(9, 5), dpi=300)

    cov_stride = df[df['prefetcher'] == 'Stride']['coverage'].values
    cov_ngram = df[df['prefetcher'] == 'N-Gram']['coverage'].values

    width_cov = 0.35
    rects_c1 = ax.bar(x - width_cov/2, cov_stride, width_cov, label='Stride Prefetcher', color='#3498db')
    rects_c2 = ax.bar(x + width_cov/2, cov_ngram, width_cov, label='N-Gram Prefetcher', color='#2ecc71')

    ax.set_ylabel('Prefetch Coverage (%)')
    ax.set_title('Prefetch Coverage Comparison')
    ax.set_xticks(x)
    ax.set_xticklabels([w.replace('_', ' ').title() for w in workloads])
    ax.legend()
    ax.set_ylim(0, 115)

    autolabel(rects_c1)
    autolabel(rects_c2)

    plt.tight_layout()
    plt.savefig(os.path.join(results_dir, "prefetch_coverage_comparison.png"))
    plt.close()
    print("Plots generated in results/ directory.")


if __name__ == "__main__":
    generate_plots()
