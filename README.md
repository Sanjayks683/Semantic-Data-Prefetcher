# Semantic Data Prefetcher (N-Gram Based)

Hardware prefetcher that can predict irregular memory patterns like linked list traversals and pointer chasing, where standard stride prefetchers completely fail.

Built as a B.Tech capstone project. Has both a Python trace-driven cache simulator and a fully synthesizable SystemVerilog RTL design.

---

## What's the problem?

CPUs use prefetchers to fetch data into cache before the processor actually needs it. The most common type is a stride prefetcher — it sees `addr`, `addr+64`, `addr+128` and prefetches `addr+192`. Works perfectly for array loops.

But real programs also do a lot of pointer chasing:
```c
while (node != NULL) {
    process(node);
    node = node->next;  // jumps to some random heap address
}
```
Each node sits at a random address decided by `malloc`. The deltas between nodes keep changing (+6, +7, -12, -1, ...), so stride prefetchers see no pattern and give up. On these workloads, they achieve literally 0% coverage.

## The approach

Got the initial idea from this paper:
> Peled et al., *"Semantic Locality and Context-based Prefetching Using Reinforcement Learning"*, ISCA 2015

They used reinforcement learning and compiler-injected NOP hints to solve this. Way too complex for a hardware implementation. So I simplified it into a pure hardware **N-Gram delta predictor**:

1. Convert each memory access to a cache block address (`addr >> 6`)
2. Compute the delta from the previous block
3. Keep a shift register of the last 3 deltas
4. Hash the 3-delta window → index into a 1024-entry SRAM table
5. Each table entry stores: predicted next delta + 2-bit confidence counter
6. If confidence ≥ 2 and tag matches → issue prefetch

The whole thing runs in a single clock cycle with no floating point math and no compiler changes needed.

```
Memory Address → [Delta Compute] → [3-Deep Shift Reg] → [XOR Hash] → [SRAM Table]
                                                                          ↓
                                                                   [Confidence ≥ 2?]
                                                                          ↓
                                                                   Prefetch Address
```

---

## Results

32 KB 8-way set-associative L1 cache, 64B lines.

| Workload | What it does | No Prefetch | Stride | N-Gram (this project) | Coverage | Accuracy |
|---|---|:---:|:---:|:---:|:---:|:---:|
| Streaming | Sequential array scan | 100% miss | 0.06% | 0.12% | 99.88% | 99.98% |
| Matrix | Column-major 2D access | 100% miss | 2.08% | 2.44% | 97.56% | 97.99% |
| Pointer Chase | Linked list traversal | 60.94% | 60.94% (0% cov) | **33.16%** | **45.59%** | **100%** |
| Markov | Graph state transitions | 18.40% | 18.40% (0% cov) | **11.85%** | **35.60%** | **100%** |

The main point: on pointer chasing where stride gets 0% coverage, this design eliminates ~46% of cache misses with zero wrong guesses.

On regular array workloads it still works fine (>97% coverage), so it doesn't break anything.

## How it compares

| | Array Coverage | Pointer Chase Coverage | Storage | Needs Compiler? | Latency |
|---|:---:|:---:|:---:|:---:|:---:|
| Intel/AMD stride prefetchers | ~95-99% | 0-5% | ~1-2 KB | No | 1 cycle |
| Peled et al. (ISCA '15) | ~95% | 30-45% | ~31 KB | Yes (LLVM NOPs) | Multi-cycle |
| IPCP (ISCA '20) | ~96% | 35-45% | ~12 KB | No | 1-2 cycles |
| **This project** | **99.88%** | **45.59%** | **3.2 KB** | **No** | **1 cycle** |

---

## Known limitations

- **Random access patterns** (crypto, hash tables with uniform distribution): no repeating sequences to learn, coverage drops to 0%. The confidence counter prevents bad guesses though.
- **High branching factor** (BSTs with 50/50 left/right): single predicted delta per entry means the counter keeps oscillating and never reaches threshold.
- **Cold start**: needs 3 accesses to fill the shift register + 2 observations to build confidence. First pass through a new data structure is always blind.
- **Shared across threads**: interleaved accesses from different threads would mess up the delta history. Real CPUs would need per-thread registers.

---

## Files

```
sim/
  config.py               - cache and prefetcher params
  cache.py                - set-associative L1 cache model (LRU)
  stride_prefetcher.py    - baseline stride prefetcher
  ngram_prefetcher.py     - the n-gram delta prefetcher
  generate_trace.py       - generates synthetic workload traces
  trace_parser.py         - reads .trace files
  metrics.py              - hit rate, miss rate, accuracy, coverage
  main.py                 - runs all benchmarks
  plot_results.py         - bar chart comparisons

rtl/
  ngram_types_pkg.sv      - parameters and types
  delta_generator.sv      - computes block delta
  history_shift_reg.sv    - 3-stage shift register
  xor_hash.sv             - folded XOR with bit rotations
  sram_table.sv           - 1024-entry lookup table
  confidence_fsm.sv       - 2-bit saturating counter
  ngram_prefetcher.sv     - top level module
  tb/
    tb_ngram_prefetcher.sv - testbench

results/                  - CSV + plots
synthesis/
  synth_vivado.tcl        - Vivado synthesis script
```

## Running it

Python sim:
```
cd sim
python generate_trace.py
python main.py
python plot_results.py
```

RTL sim (Icarus Verilog):
```
cd rtl
iverilog -g2012 -o sim.out ngram_types_pkg.sv delta_generator.sv history_shift_reg.sv xor_hash.sv sram_table.sv confidence_fsm.sv ngram_prefetcher.sv tb/tb_ngram_prefetcher.sv
vvp sim.out
```

---

## References

1. Peled, Mannor, Weiser, Etsion — *"Semantic Locality and Context-based Prefetching Using Reinforcement Learning"*, ISCA 2015
2. Pakalapati, Biswal — *"IPCP: Instruction Pointer Classifying Prefetcher"*, ISCA 2020
3. Intel — *"Intel 64 and IA-32 Architectures Optimization Reference Manual"*, Ch. 2
