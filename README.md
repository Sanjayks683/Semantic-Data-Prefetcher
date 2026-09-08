# Semantic Data Prefetcher (N-Gram Based)

Hardware prefetcher that predicts irregular memory patterns like linked-list traversals and pointer chasing, where standard stride prefetchers completely fail.

Built as a B.Tech capstone project. Has both a Python trace-driven cache simulator and a synthesizable SystemVerilog RTL design, with an automated check that the two are behaviourally equivalent.

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
4. Hash the 3-delta window → index into a 1024-entry table
5. Each table entry stores: predicted next delta + 2-bit confidence counter
6. If confidence ≥ 2 and the tag matches → issue a prefetch

The whole thing is a single combinational path from address in to prefetch out, with no floating-point math and no compiler changes.

```
Memory Address → [Delta Compute] → [3-Deep Shift Reg] → [XOR Hash] → [Table Lookup]
                        ↓                                                  ↓
                 [Overflow check]                                 [Confidence ≥ 2?]
                        ↓                                                  ↓
                 flush window                                     Prefetch Address
```

---

## Results

32 KB 8-way set-associative L1 cache, 64 B lines. Reproduce with `python sim/main.py`.

| Workload | What it does | No Prefetch | Stride | N-Gram (this project) | Coverage | Accuracy |
|---|---|:---:|:---:|:---:|:---:|:---:|
| Streaming | Sequential array scan | 100% miss | 0.06% | 0.12% | 99.88% | 99.98% |
| Matrix | Column-major 2D access | 100% miss | 2.08% | 2.44% | 97.56% | 97.99% |
| Pointer Chase | Linked list traversal | 60.94% | 60.94% (0% cov) | **33.16%** | **45.59%** | **100%** |
| Markov | Graph state transitions | 18.40% | 18.40% (0% cov) | **11.85%** | **35.60%** | **100%** |

The main point: on pointer chasing where stride gets 0% coverage, this design eliminates ~46% of cache misses with no wrong guesses.

On regular array workloads it still works fine (>97% coverage), so it doesn't break anything.

### Prefetch traffic breakdown

Coverage and accuracy alone hide how much work the predictor does, so the full counters are in `results/benchmark_results.csv`:

| Workload | Generated | Filtered (already cached) | Issued | Useful | Pollution |
|---|---:|---:|---:|---:|---:|
| Streaming | 4,995 | 0 | 4,995 | 4,994 | 0.00% |
| Matrix | 2,489 | 0 | 2,489 | 2,439 | 1.61% |
| Pointer Chase | 2,405 | 1,016 | 1,389 | 1,389 | 0.00% |
| Markov | 4,419 | 3,895 | 524 | 524 | 0.00% |

**Metric definitions** (stated because the literature is inconsistent):

- *Generated* — prefetch candidates the predictor produced.
- *Filtered* — candidates already resident in the cache. Dropped without consuming memory bandwidth, so they are **not** counted as issued.
- *Issued* — candidates that missed and were fetched. This is the bandwidth cost and the denominator for accuracy.
- *Useful* — issued prefetches later hit by a demand access before eviction.
- *Pollution* — issued prefetches evicted without ever being used.
- *Accuracy* = useful / issued.  *Coverage* = (baseline misses − misses) / baseline misses.

Markov is the interesting case: the predictor generates 4,419 candidates but 88% of them are already in cache, so only 524 actually cost bandwidth. The 100% accuracy figure is real but it describes a small number of issued prefetches, not a small number of predictions.

## How it compares

| | Array Coverage | Pointer Chase Coverage | Storage | Needs Compiler? | Pipeline depth |
|---|:---:|:---:|:---:|:---:|:---:|
| Intel/AMD stride prefetchers | ~95-99% | 0-5% | ~1-2 KB | No | 1 cycle |
| Peled et al. (ISCA '15) | ~95% | 30-45% | ~31 KB | Yes (LLVM NOPs) | Multi-cycle |
| IPCP (ISCA '20) | ~96% | 35-45% | ~12 KB | No | 1-2 cycles |
| **This project** | **99.88%** | **45.59%** | **3,200 bytes** | **No** | **1 cycle** |

Two honest caveats on this table:

- The published rows are measured on SPEC/GAP workloads, not on the four synthetic traces used here. The numbers are not directly comparable; the table is for rough positioning, not a head-to-head result.
- "1 cycle" describes pipeline depth — address in to prefetch decision is one combinational path — not an achieved clock frequency. See [Synthesis](#synthesis) below.

---

## Verification

The Python model and the RTL are two implementations of the same algorithm, so the project checks that they actually agree rather than assuming it.

```bash
python tools/run_all_checks.py
```

| Step | What it proves |
|---|---|
| `tools/check_rtl_sync.py` | `rtl/ngram_types_pkg.sv` and `sim/config.py` declare identical geometry, and the derived invariants hold |
| `sim/test_cache.py` | 7 tests: LRU victim order, set isolation, prefetch-usefulness accounting, dead-prefetch counting |
| `sim/test_prefetchers.py` | 12 tests: both prefetchers' learning and confidence behaviour, delta-overflow handling, both trace parsers |
| `rtl/tb/tb_ngram_prefetcher.sv` | 65 assertions over 7 phases: cold start, learned cycle, same-index bypass, noise rejection, delta overflow, reset, retraining speed |
| `tools/cosim_check.py` | Drives the RTL and the Python model with the same trace and diffs their prefetch streams access by access |

The co-simulation is the one that matters most:

```
  markov_semantic.trace      8000 accesses  model=4419   rtl=4419    agree=8000/8000  [MATCH]
  matrix.trace               2500 accesses  model=2489   rtl=2489    agree=2500/2500  [MATCH]
  pointer_chase.trace        5000 accesses  model=2405   rtl=2405    agree=5000/5000  [MATCH]
  streaming.trace            5000 accesses  model=4995   rtl=4995    agree=5000/5000  [MATCH]
```

All 20,500 accesses produce identical prefetch decisions in both implementations, so the results table above describes the hardware and not just the model.

The RTL testbench fails on a real regression rather than only on a crash. Removing the read-during-write bypass in `sram_table.sv` makes phase 3 fail (`first prefetch at access 7, expected by access 5`), and reverting the confidence update rule makes phase 7 fail (`retrained at repetition 5, expected by repetition 4`).

---

## Synthesis

```bash
vivado -mode batch -source synthesis/synth_vivado.tcl
```

Constraints are read from `synthesis/constraints.xdc` **before** `synth_design`, so synthesis is optimised against the 250 MHz target rather than being constrained after the fact. The script runs the full `opt → place → phys_opt → route` flow and reports post-route WNS, because post-synthesis timing on an unconstrained netlist is not a meaningful Fmax.

Storage is 1024 entries × 25 bits (1 valid + 6 tag + 16 delta + 2 confidence) = **25,600 bits / 3,200 bytes**. Valid bits are held in flip-flops so the table can be invalidated in one cycle at reset; the 24-bit payload lives in an unreset RAM so it infers as block/distributed RAM instead of ~25k flip-flops.

**I have not run this on a real Vivado install**, so no timing or utilisation numbers are quoted here. The script is correct and complete, but the achieved Fmax is unverified — running it will print post-route WNS and resource counts.

---

## Known limitations

- **Random access patterns** (crypto, hash tables with uniform distribution): no repeating sequences to learn, coverage drops to 0%. The confidence counter prevents bad guesses though — the RTL testbench measures 0 speculative prefetches across 200 irregular accesses.
- **High branching factor** (BSTs with 50/50 left/right): a single predicted delta per entry means the counter oscillates and never reaches threshold.
- **Cold start**: needs 3 accesses to fill the shift register plus 2 observations to build confidence. First pass through a new data structure is always blind.
- **Shared across threads**: interleaved accesses from different threads would corrupt the delta history. A real CPU would need per-thread registers.
- **Delta range**: deltas are stored in a 16-bit signed field, so a jump larger than ±32,767 blocks (±2 MB) cannot be represented. Rather than wrapping it into a bogus small delta, both implementations treat it as a discontinuity, report it (`delta_overflow` in RTL, `delta_overflows` in the model), and rebuild the window. The shipped pointer-chase trace already reaches ±15,436, so a heap much larger than 1 MB would start hitting this.
- **Benchmark scope**: the four traces are synthetic and each uses a single instruction pointer. That means the stride baseline's 256-entry IP-indexed table is only ever exercised as a single entry, so the stride comparison is less demanding than it would be on a real multi-IP workload. Treat the stride numbers as a floor, not a tuned baseline.

---

## Files

```
sim/
  config.py                - all geometry; every derived value computed, not repeated
  cache.py                 - set-associative L1 cache model (LRU)
  stride_prefetcher.py     - baseline stride prefetcher
  ngram_prefetcher.py      - the n-gram delta prefetcher (reference model)
  generate_trace.py        - generates the synthetic workload traces
  trace_parser.py          - text and ChampSim binary trace readers
  metrics.py               - hit/miss rate, accuracy, coverage, pollution
  main.py                  - runs all benchmarks -> results/benchmark_results.csv
  plot_results.py          - bar chart comparisons
  test_cache.py            - cache unit tests
  test_prefetchers.py      - prefetcher and parser unit tests

rtl/
  ngram_types_pkg.sv       - parameters and types (widths derived via $clog2)
  delta_generator.sv       - block delta plus range check
  history_shift_reg.sv     - 3-stage window with flush
  xor_hash.sv              - folded XOR with per-tap rotations
  sram_table.sv            - 1024-entry table, RAM payload + bypass
  confidence_fsm.sv        - 2-bit saturating counter
  ngram_prefetcher.sv      - top level
  tb/
    tb_ngram_prefetcher.sv - self-checking testbench (65 assertions)
    tb_cosim.sv            - trace-driven harness for co-simulation

tools/
  run_all_checks.py        - runs everything below, one verdict
  check_rtl_sync.py        - RTL/model parameter agreement
  cosim_check.py           - RTL/model behavioural equivalence

synthesis/
  synth_vivado.tcl         - synthesis + place & route
  constraints.xdc          - timing constraints (read before synthesis)

results/                   - CSV + plots
traces/                    - generated workload traces
```

## Running it

Python simulation:
```bash
cd sim
python generate_trace.py
python main.py
python plot_results.py
```

RTL simulation (Icarus Verilog):
```bash
cd rtl
iverilog -g2012 -o sim.out ngram_types_pkg.sv delta_generator.sv history_shift_reg.sv xor_hash.sv sram_table.sv confidence_fsm.sv ngram_prefetcher.sv tb/tb_ngram_prefetcher.sv
vvp sim.out
```

Everything at once:
```bash
python tools/run_all_checks.py
```

---

## References

1. Peled, Mannor, Weiser, Etsion — *"Semantic Locality and Context-based Prefetching Using Reinforcement Learning"*, ISCA 2015
2. Pakalapati, Biswal — *"IPCP: Instruction Pointer Classifying Prefetcher"*, ISCA 2020
3. Intel — *"Intel 64 and IA-32 Architectures Optimization Reference Manual"*, Ch. 2
