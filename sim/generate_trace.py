"""
generate_trace.py - Synthetic memory traces covering the four access patterns
the prefetchers are evaluated on.

All generators are deterministic (fixed seed and fixed arithmetic) so the
benchmark numbers in the README are reproducible from a clean checkout.
"""

import random
import os

from config import BLOCK_SIZE

TRACES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "traces")

RANDOM_SEED = 42


def generate_streaming_trace(num_accesses=5000, stride_bytes=BLOCK_SIZE,
                             base_addr=0x10000000, ip=0x400100):
    """Sequential array scan: one new cache block per access."""
    traces = []
    addr = base_addr
    for _ in range(num_accesses):
        traces.append((ip, addr))
        addr += stride_bytes
    return traces


def generate_matrix_trace(rows=50, cols=50, elem_size=BLOCK_SIZE,
                          base_addr=0x20000000, ip=0x400200):
    """Column-major walk over a row-major matrix: large constant stride."""
    traces = []
    for c in range(cols):
        for r in range(rows):
            addr = base_addr + (r * cols + c) * elem_size
            traces.append((ip, addr))
    return traces


def generate_pointer_chasing_trace(num_nodes=500, traversals=10,
                                   heap_span=1024 * 1024,
                                   base_addr=0x30000000, ip=0x400300):
    """Linked-list traversal over randomly placed heap nodes.

    Node placement is random but the traversal order is fixed, so the delta
    sequence is irregular yet perfectly repeatable - exactly the case a stride
    prefetcher cannot learn and an n-gram prefetcher can.
    """
    rng = random.Random(RANDOM_SEED)
    node_offsets = rng.sample(range(0, heap_span, BLOCK_SIZE), num_nodes)
    node_addrs = [base_addr + offset for offset in node_offsets]

    traces = []
    for _ in range(traversals):
        for addr in node_addrs:
            traces.append((ip, addr))
    return traces


def generate_markov_semantic_trace(num_nodes=800, steps=8000,
                                   base_addr=0x40000000, ip=0x400400):
    """Graph traversal whose next-node choice cycles through three transitions."""
    node_addrs = [base_addr + i * BLOCK_SIZE for i in range(num_nodes)]
    traces = []

    current_node = 0
    for step in range(steps):
        traces.append((ip, node_addrs[current_node]))
        if step % 3 == 0:
            current_node = (current_node * 7 + 13) % num_nodes
        elif step % 3 == 1:
            current_node = (current_node * 3 + 5) % num_nodes
        else:
            current_node = (current_node + 1) % num_nodes

    return traces


def export_trace_to_file(traces, filepath):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        for ip, addr in traces:
            f.write(f"0x{ip:x} 0x{addr:x}\n")
    print(f"Generated {len(traces)} requests -> {filepath}")


if __name__ == "__main__":
    export_trace_to_file(generate_streaming_trace(),
                         os.path.join(TRACES_DIR, "streaming.trace"))
    export_trace_to_file(generate_matrix_trace(),
                         os.path.join(TRACES_DIR, "matrix.trace"))
    export_trace_to_file(generate_pointer_chasing_trace(),
                         os.path.join(TRACES_DIR, "pointer_chase.trace"))
    export_trace_to_file(generate_markov_semantic_trace(),
                         os.path.join(TRACES_DIR, "markov_semantic.trace"))
