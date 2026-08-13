import random
import os


def generate_streaming_trace(num_accesses=5000, stride_bytes=64, base_addr=0x10000000):
    traces = []
    ip = 0x400100
    addr = base_addr
    for _ in range(num_accesses):
        traces.append((ip, addr))
        addr += stride_bytes
    return traces


def generate_matrix_trace(rows=50, cols=50, elem_size=64, base_addr=0x20000000):
    traces = []
    ip = 0x400200
    for c in range(cols):
        for r in range(rows):
            addr = base_addr + (r * cols + c) * elem_size
            traces.append((ip, addr))
    return traces


def generate_pointer_chasing_trace(num_nodes=500, traversals=10, base_addr=0x30000000):
    random.seed(42)
    node_offsets = random.sample(range(0, 1024 * 1024, 64), num_nodes)
    node_addrs = [base_addr + offset for offset in node_offsets]

    traces = []
    ip = 0x400300
    for _ in range(traversals):
        for addr in node_addrs:
            traces.append((ip, addr))
    return traces


def generate_markov_semantic_trace(num_nodes=800, steps=8000, base_addr=0x40000000):
    node_addrs = [base_addr + i * 64 for i in range(num_nodes)]
    traces = []
    ip = 0x400400
    
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
    traces_dir = os.path.join(os.path.dirname(__file__), "..", "traces")
    
    export_trace_to_file(generate_streaming_trace(), os.path.join(traces_dir, "streaming.trace"))
    export_trace_to_file(generate_matrix_trace(), os.path.join(traces_dir, "matrix.trace"))
    export_trace_to_file(generate_pointer_chasing_trace(), os.path.join(traces_dir, "pointer_chase.trace"))
    export_trace_to_file(generate_markov_semantic_trace(), os.path.join(traces_dir, "markov_semantic.trace"))
