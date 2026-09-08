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

> **These traces are synthetic and were designed alongside the hardware.** They
> produce 393–500 distinct delta contexts against a 1,024-entry table, so they
> fit it exactly. Treat these numbers as an upper bound on favourable input, and
> see [Real-workload evaluation](#real-workload-evaluation-spec-mcf) for what
> happens on SPEC mcf — where this configuration manages 0.62% coverage, and a
> correctly sized one at L2 beats stride.

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

## Real-workload evaluation (SPEC mcf)

The four traces above are synthetic and were written alongside the hardware, so
they cannot tell you whether the design generalises. This section runs the same
model over `605.mcf_s` from the DPC-3 ChampSim trace set — a real SPEC workload
nobody here designed — using 20,000,000 memory accesses.

```bash
python tools/run_champsim.py path/to/605.mcf_s-472B.champsimtrace.xz --limit 20000000
python tools/run_champsim.py path/to/605.mcf_s-472B.champsimtrace.xz --level l2
```

### At L1, in the configuration this repository ships, it fails

| | Misses | Miss rate | Coverage | Accuracy |
|---|---:|---:|---:|---:|
| No prefetch | 2,011,188 | 10.06% | — | — |
| **Stride** | **613,534** | **3.07%** | **69.49%** | 99.93% |
| **N-Gram** | 1,998,722 | 9.99% | **0.62%** | 79.78% |

The baseline this design was built to beat gets 69.49%. This design gets 0.62%.
It is not silent — it generated 3,895,894 predictions — but 99.6% of them were
for lines already in cache, so only 16,540 were issued.

Two measurements explain it:

| | Synthetic traces | mcf (real) |
|---|---:|---:|
| Distinct 3-delta contexts | 393–500 | **503,503** |
| Table entries | 1,024 | 1,024 |
| Accesses overflowing the 16-bit delta field | 0% | **54.02%** |

The synthetic traces produce 393–500 distinct contexts against a 1,024-entry
table — a perfect fit, because the workloads and the hardware were sized
together. Real code produces 503,503 against the same 1,024, a 491× overcommit,
and half its deltas do not fit the 16-bit field at all.

Importantly, the *idea* is not what fails. The correlation is genuinely present
in real code: **91.7%** of 3-delta windows in mcf recur, and when one recurs the
same delta follows **89.5%** of the time. The predictor simply has nowhere to
put them.

### At L2, correctly sized, it beats stride

An L1 absorbs the regular, high-locality traffic; what survives to L2 is the
irregular access this design targets. That is where a correlation prefetcher
normally lives. Running an unprefetched 32 KB L1 in front and prefetching into a
256 KB L2, with the delta field widened to 48 bits so the L1 miss stream no
longer overflows it:

| Table entries | Metadata | Coverage | Accuracy | vs stride |
|---:|---:|---:|---:|---:|
| 65,536 | 0.49 MB | 17.63% | 99.91% | −34.59 |
| 131,072 | 1.00 MB | 33.58% | 99.94% | −18.63 |
| 262,144 | 2.03 MB | 47.71% | 99.95% | −4.51 |
| **524,288** | **4.12 MB** | **57.30%** | **99.95%** | **+5.08** |
| 1,048,576 | 8.38 MB | 62.88% | 99.95% | +10.66 |
| 2,097,152 | 17.00 MB | **66.28%** | 99.95% | **+14.07** |

*(stride at L2: 52.22% coverage, 99.94% accuracy, ~2 KB)*

From 524,288 entries upward the design beats a stride prefetcher on a real
workload, at essentially perfect accuracy. Both changes are required — at 1,024
entries the 48-bit version still returns 0.00%, and at 16-bit deltas the large
table never accumulates a window.

### What this means

The algorithm works on real code. The *operating point in this repository does
not*: 1,024 entries and a 16-bit delta field at L1 were, in effect, fitted to
the synthetic benchmarks. The honest configuration is L2 placement, a 48-bit
delta field, and a table three orders of magnitude larger.

That is a real cost. Beating a ~2 KB stride prefetcher takes ~4 MB of metadata,
so this is not a free win — it is the usual trade for irregular prefetching,
where large metadata structures are the norm rather than the exception. The
headline synthetic numbers should be read as an upper bound obtained on
favourable workloads, and the mcf numbers as the generalisation result.

---

## How it compares

| | Array Coverage | Pointer Chase Coverage | Storage | Needs Compiler? | Pipeline depth |
|---|:---:|:---:|:---:|:---:|:---:|
| Intel/AMD stride prefetchers | ~95-99% | 0-5% | ~1-2 KB | No | 1 cycle |
| Peled et al. (ISCA '15) | ~95% | 30-45% | ~31 KB | Yes (LLVM NOPs) | Multi-cycle |
| IPCP (ISCA '20) | ~96% | 35-45% | ~12 KB | No | 1-2 cycles |
| **This project** (synthetic) | **99.88%** | **45.59%** | **3,200 bytes** | **No** | **1 cycle** |
| **This project** (real mcf, as shipped) | — | **0.62%** | 3,200 bytes | No | 1 cycle |
| **This project** (real mcf, L2, resized) | — | **57.30%** | 4.12 MB | No | 1 cycle |

Four honest caveats on this table:

- The published rows are measured on SPEC/GAP workloads, not on the four synthetic traces used here. The numbers are not directly comparable; the table is for rough positioning, not a head-to-head result.
- "1 cycle" describes pipeline depth — address in to prefetch decision is one combinational path — not an achieved clock frequency.
- That single-cycle path is also the design's main weakness: it synthesises at **≈70 MHz**, not at an L1-realistic clock. The commercial rows achieve 1-cycle latency at multi-GHz. See [Synthesis](#synthesis).
- The 45.59% figure is from synthetic traces. On a real workload the shipped configuration gets 0.62%; beating stride requires L2 placement and ~4 MB of metadata, which is a different design point from the 3,200-byte row above.

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
| `synthesis/synth_vivado.tcl` | Synthesises and place-and-routes the design; reports real post-route timing, area and power |
| `tools/run_champsim.py` | Evaluates against a real SPEC workload rather than the synthetic traces, at L1 or L2 |

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
vivado -mode batch -source synthesis/synth_vivado.tcl     # synth + place & route
vivado -mode batch -source synthesis/fmax_sweep.tcl       # frequency sweep
```

Constraints are read from `synthesis/constraints.xdc` **before** `synth_design`, so synthesis is optimised against the timing goal rather than being constrained after the fact. The script runs the full `opt → place → phys_opt → route` flow and quotes post-route WNS, because post-synthesis timing on an unconstrained netlist is not a meaningful Fmax.

Everything below is measured, not estimated. Reports are in [`synthesis/reports/`](synthesis/reports/).

**Target:** `xc7z020clg400-1` (Zynq-7000, Artix-7 fabric, −1 speed grade), Vivado 2026.1, out-of-context, 1.0 ns input + 1.0 ns output delay budget.

> The original script targeted `xc7a100tcsg324-1`. Vivado 2026.1 no longer ships standalone 7-series Artix parts — only the Zynq-7000 families are installed — so the default part is now `xc7z020clg400-1`. Same 7-series fabric and same −1 speed grade, so the numbers are directly comparable. Override with `-tclargs -part <part>`.

### Resource utilisation (post-route)

| Resource | Used | Available | Util |
|---|---:|---:|---:|
| Slice LUTs | 2,289 | 53,200 | 4.30% |
|   — as logic | 1,777 | | |
|   — as distributed RAM | 512 | 17,400 | 2.94% |
| Slice registers | 1,184 | 106,400 | 1.11% |
| Slices | 693 | 13,300 | 5.21% |
| Block RAM | 0 | 140 | 0% |
| Total on-chip power | 0.174 W | | |

The prediction table is 1024 × 25 bits (1 valid + 6 tag + 16 delta + 2 confidence) = **25,600 bits / 3,200 bytes**. The read is asynchronous, so the payload maps to **distributed RAM (512 LUTs)**, not BRAM — BRAM would need a registered read and a second cycle.

**The `sram_table` fix is worth quantifying.** Synthesising the original version (whole array reset in one `always_ff`, no read-during-write bypass) against the identical rest of the design:

| | Original | Fixed | |
|---|---:|---:|---|
| Flip-flops | 25,863 | **1,184** | 21.8× fewer |
| LUTs | 8,581 | **2,286** | 3.8× fewer |
| Distributed RAM | 0 | 512 | table now infers as RAM |

The reset loop was forcing all 25,600 table bits into flip-flops. Splitting valid bits (1,024 flops, single-cycle invalidate) from the payload (unreset, RAM-inferrable) is the entire difference.

### Timing — the 250 MHz target is not met

| Target period | Target | Post-route WNS | Status |
|---:|---:|---:|:---|
| 4.0 ns | 250.0 MHz | −10.381 ns | **VIOLATED** |
| 8.0 ns | 125.0 MHz | −5.808 ns | VIOLATED |
| 12.0 ns | 83.3 MHz | −1.709 ns | VIOLATED |
| 14.0 ns | 71.4 MHz | −0.281 ns | VIOLATED |
| **15.0 ns** | **66.7 MHz** | **+0.175 ns** | **MET** |
| 16.0 ns | 62.5 MHz | +0.678 ns | MET |

**Measured Fmax ≈ 70 MHz**, and 66.7 MHz is the fastest constraint it reliably closes. That is nowhere near 250 MHz, and the earlier "250 MHz" figure in this README was an aspiration that had never been synthesised. It is now a measurement.

The critical path runs straight from `mem_addr_in[7]` to `prefetch_addr_out[38]` — **36 logic levels, 28 of them CARRY4**, 12.346 ns of data path (5.66 ns logic, 6.68 ns routing):

```
mem_addr_in ─► 58-bit subtract (block delta)          ┐
            ─► 43-bit overflow reduction              │ 28 CARRY4 stages
            ─► XOR hash                               │ across the two
            ─► 1024-deep async distributed-RAM read   │ wide adders
            ─► tag + confidence compare               │
            ─► 59-bit add (predicted block)           ┘
            ─► prefetch_addr_out
```

Two ~58-bit ripple-carry adders in series with a deep asynchronous RAM read between them, all in one combinational path. Splitting out the I/O budget: pure internal logic needs 12.38 ns (**80.8 MHz**); the 1 ns in + 1 ns out out-of-context budget accounts for the rest.

**This is architectural, not a bug.** The design is genuinely single-cycle — one access in, one prefetch decision out, same cycle — and that is exactly why it is slow. Reaching a realistic L1 clock would mean pipelining it into 3–4 stages, keeping one access per cycle of throughput while allowing several cycles of latency. That is a reasonable trade for a prefetcher, which sits off the demand-critical path and only needs its prediction to arrive before the data is used. **That pipelining is not implemented** — the numbers above are for the single-cycle design as it stands.

---

## Known limitations

- **Random access patterns** (crypto, hash tables with uniform distribution): no repeating sequences to learn, coverage drops to 0%. The confidence counter prevents bad guesses though — the RTL testbench measures 0 speculative prefetches across 200 irregular accesses.
- **High branching factor** (BSTs with 50/50 left/right): a single predicted delta per entry means the counter oscillates and never reaches threshold.
- **Cold start**: needs 3 accesses to fill the shift register plus 2 observations to build confidence. First pass through a new data structure is always blind.
- **Shared across threads**: interleaved accesses from different threads would corrupt the delta history. A real CPU would need per-thread registers.
- **Delta range**: deltas are stored in a 16-bit signed field, so a jump larger than ±32,767 blocks (±2 MB) cannot be represented. Rather than wrapping it into a bogus small delta, both implementations treat it as a discontinuity, report it (`delta_overflow` in RTL, `delta_overflows` in the model), and rebuild the window. The shipped pointer-chase trace already reaches ±15,436, so a heap much larger than 1 MB would start hitting this.
- **Benchmark scope**: the four traces are synthetic and each uses a single instruction pointer. That means the stride baseline's 256-entry IP-indexed table is only ever exercised as a single entry, so the stride comparison is less demanding than it would be on a real multi-IP workload. Treat the stride numbers as a floor, not a tuned baseline.
- **Table capacity is the binding constraint on real code**: mcf presents 503,503 distinct 3-delta contexts where the shipped table holds 1,024. Coverage scales directly with table size (17.63% at 64K entries, 57.30% at 512K, 66.28% at 2M), so the 1,024-entry configuration is far below the knee of that curve on any real workload. The synthetic traces hide this completely because they only generate 393–500 contexts.
- **The 16-bit delta field is too narrow for real address streams**: 54% of mcf's accesses, and 78% of its L1 miss stream, exceed it — real programs interleave stack and heap regions that are gigabytes apart. 48 bits removes the problem entirely.
- **Clock frequency**: the single-cycle datapath synthesises at **≈70 MHz** on a −1 speed grade 7-series part — far below any real L1 clock. Two ~58-bit adders in series with a 1024-deep asynchronous RAM read between them is simply too much for one cycle. Fixing it means pipelining into 3–4 stages (throughput stays at one access per cycle; latency grows, which a prefetcher can absorb). Not implemented. The functional results above are unaffected — they are cycle-accurate at the algorithm level, not timing-dependent.

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
  run_champsim.py          - evaluate on a real ChampSim trace (L1 or L2)
  check_rtl_sync.py        - RTL/model parameter agreement
  cosim_check.py           - RTL/model behavioural equivalence

synthesis/
  synth_vivado.tcl         - synthesis + place & route
  fmax_sweep.tcl           - implements at several periods to find real Fmax
  constraints.xdc          - timing constraints (read before synthesis)
  reports/                 - measured utilisation, timing, power, Fmax sweep

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
