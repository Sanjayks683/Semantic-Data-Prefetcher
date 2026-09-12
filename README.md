# Semantic Data Prefetcher (N-Gram Based)

Hardware delta-correlation prefetcher aimed at irregular memory access — linked-list traversals and pointer chasing — which a stride prefetcher cannot learn.

Built as a B.Tech capstone project. Has both a Python trace-driven cache simulator and a synthesizable SystemVerilog RTL design, with an automated check that the two are behaviourally equivalent.

## At a glance

| | Result |
|---|---|
| **Synthetic traces** (written for this project) | 45.59% coverage on pointer chasing at 100% accuracy, where stride gets 0% |
| **Real SPEC CPU2017, as shipped** | **Fails** — 0.62% on mcf, 0.47% on omnetpp; stride wins both |
| **Real SPEC, at L2, resized and depth-tuned** | **Beats stride on both**, at different settings: 61.48% on mcf (depth 2), 6.27% on omnetpp (depth 1), with a ~4 MB table. A single depth-1, 2.1 MB configuration beats stride on both (52.69% / 6.18%). |
| **RTL vs Python model** | Equivalent on every access across 21M+ accesses, both configurations |
| **Synthesis** (Xilinx 7-series, post-route) | ≈70 MHz for both configurations; the 250 MHz target is **not** met. v2 is 10.5% smaller. |

The short version: the design works on real code, but not in the configuration it was originally built with, and its coverage numbers are optimistic because prefetch latency is not modelled. Each of those is explained below.

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
Each node sits at a random address decided by `malloc`. The deltas between nodes keep changing (+6, +7, -12, -1, ...), so stride prefetchers see no pattern and give up. On a pure linked-list traversal they get 0% coverage.

In fairness to stride: real programs mix that kind of access with a great deal of regular traffic, which stride handles very well. On SPEC mcf it covers 69% of L1 misses. The irregular part is what is left over.

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
> see [Real-workload evaluation](#real-workload-evaluation-spec-mcf-and-omnetpp)
> for what happens on SPEC mcf and omnetpp — where this configuration manages
> 0.62% and 0.47% coverage respectively.

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

## Real-workload evaluation (SPEC mcf and omnetpp)

The four traces above are synthetic and were written alongside the hardware, so
they cannot say whether the design generalises. This section runs the same model
over two real SPEC workloads from the DPC-3 ChampSim set — `605.mcf_s` and
`620.omnetpp_s` — at 20,000,000 memory accesses each.

```bash
python tools/fetch_traces.py                                        # ~1.1 GB, into traces/champsim/
python tools/run_champsim.py traces/champsim/mcf.xz --limit 20000000   # L1, as shipped
python tools/run_champsim.py traces/champsim/mcf.xz --level l2       # L2 placement
python tools/sizing_sweep.py traces/champsim/mcf.xz --level l2       # table/delta/depth sweep
```

### As shipped (1,024 entries, 16-bit deltas, L1), it fails on both

| Workload | | Miss rate | Coverage | Accuracy |
|---|---|---:|---:|---:|
| **mcf** | no prefetch | 10.06% | — | — |
| | stride | 3.07% | **69.49%** | 99.93% |
| | n-gram | 9.99% | **0.62%** | 79.78% |
| **omnetpp** | no prefetch | 3.83% | — | — |
| | stride | 3.72% | **2.86%** | 58.15% |
| | n-gram | 3.81% | **0.47%** | 62.07% |

Roughly half of all accesses overflow the 16-bit delta field on both workloads
(54.02% on mcf, 49.83% on omnetpp) — real programs interleave stack and heap
regions that are gigabytes apart. And both present hundreds of thousands of
distinct delta contexts to a 1,024-entry table, where the synthetic traces
present 393–500 and therefore fit it exactly.

### At L2, resized, the two workloads diverge sharply

Prefetching into a 256 KB L2 behind an unprefetched 32 KB L1, with the delta
field widened to 48 bits:

| Entries | Storage | mcf coverage | omnetpp coverage |
|---:|---:|---:|---:|
| 1,024 | 8 KB | 0.00% | 0.29% |
| 16,384 | 134 KB | 0.52% | 1.20% |
| 65,536 | 536 KB | 17.63% | 1.75% |
| 262,144 | 2.1 MB | 47.71% | 2.26% |
| 524,288 | 4.2 MB | **57.30%** | 2.43% |
| 2,097,152 | 17.2 MB | **66.28%** | 2.63% |
| *stride reference* | *~2 KB* | *52.22%* | *4.55%* |

On **mcf** the design scales cleanly with capacity and overtakes stride from
524,288 entries upward, reaching 66.28% coverage at 99.95% accuracy.

On **omnetpp** it does not. Coverage saturates around 2.6% and never catches
stride, which itself only manages 4.55% — this is a workload neither prefetcher
handles well.

### Why: two measurable workload properties predict the outcome

Measuring the L1 miss stream each L2 prefetcher actually observes:

| Workload | L2 accesses | Distinct contexts | Recurring | Determinism |
|---|---:|---:|---:|---:|
| **mcf** | 2,011,188 | 516,605 | **77.8%** | **96.9%** |
| **omnetpp** | 766,334 | 667,466 | **16.2%** | **55.1%** |

*recurring* = share of 3-delta windows seen more than once.
*determinism* = when a window recurs, how often the same delta follows it.

mcf's miss stream repeats itself and repeats itself **predictably**, so a large
enough table converts that directly into coverage. omnetpp's barely repeats at
all — only 16.2% of windows are ever seen twice — and when one does recur the
follower is the same only 55.1% of the time, which is close to noise. No table
size fixes that, which is exactly what the plateau above shows.

### What this means

The design is not universally applicable, and the honest claim is narrower than
the synthetic results suggest:

- It needs **L2 placement**, a **48-bit delta field**, and a table three orders
  of magnitude larger than the one shipped here. The 1,024-entry / 16-bit / L1
  configuration in this repository was, in effect, fitted to the synthetic
  benchmarks.
- Even then it only wins on workloads whose delta contexts **recur
  deterministically**. mcf qualifies; omnetpp does not.
- The cost is real: roughly 4 MB of metadata to beat a ~2 KB stride prefetcher,
  and only on the workloads where it works at all.

The useful part is that *recurrence* and *determinism* can be measured on a
trace directly, before committing any hardware — so whether this design is worth
building for a given workload is a question with a cheap, quantitative answer.

---

## Two configurations: v1 and v2

The history depth was never swept — it was fixed at 3 from the start. Sweeping
it (`tools/sizing_sweep.py --depths`) found 3 to be the worst of the values
tested, on every workload measured. Rather than silently changing the design,
both configurations are kept and both are verified:

| | History window | Selected by | Results |
|---|---|---|---|
| **v1** | 3 deltas | default | `results/benchmark_results.csv` |
| **v2** | 2 deltas | `NGRAM_PROFILE=v2` / `-DNGRAM_V2` | `results/benchmark_results_v2.csv` |

v1 is the design as originally built and verified; its numbers are unchanged and
remain the reference. v2 changes the window depth and nothing else.

```bash
python sim/main.py                          # v1
NGRAM_PROFILE=v2 python sim/main.py         # v2

iverilog -g2012 ... rtl/*.sv                # v1 RTL
iverilog -g2012 -DNGRAM_V2 ... rtl/*.sv     # v2 RTL
```

**There is one RTL source and one behavioural model.** The depth is selected by
`` `ifdef NGRAM_V2`` in `rtl/ngram_types_pkg.sv` and by `NGRAM_PROFILE` in
`sim/config.py`, so the two configurations cannot drift apart.
`tools/run_all_checks.py` verifies both — 11 checks, including RTL/model
co-simulation for each profile independently.

### What v2 buys

On the synthetic traces:

| Workload | v1 coverage | v2 coverage | Δ |
|---|---:|---:|---:|
| Streaming | 99.88% | 99.90% | +0.02 |
| Matrix | 97.56% | 97.68% | +0.12 |
| Pointer Chase | 45.59% | 45.62% | +0.03 |
| **Markov** | 35.60% | **40.15%** | **+4.55** |

On the real workloads at L2 with a 48-bit delta field and 524,288 entries:

| Workload | depth 1 | depth 2 (v2) | depth 3 (v1) | stride |
|---|---:|---:|---:|---:|
| **mcf** | 56.31% | **61.48%** | 57.30% | 52.22% |
| **omnetpp** | **6.27%** | 3.93% | 2.43% | 4.55% |

Depth 2 is at least as good as depth 3 on all six workloads. Depth 1 is better
still on streaming, matrix and omnetpp — and notably beats stride on omnetpp at
every table size tested, including 16,384 entries (134 KB). Coverage falls
monotonically past depth 2 while accuracy rises, which is the expected
specificity trade: a longer context matches less often but predicts better when
it does.

Depth 3 only looked reasonable on the synthetic traces because those were built
around 3- and 4-step cycles. It was chosen, not measured.

Worth stating plainly: the best depths measured are **1 and 2**, so the
"3-delta n-gram" this project is named for is not the configuration the evidence
supports. v2 is kept at depth 2 rather than depth 1 because it is the best
single choice across all six workloads; depth 1 wins on more of them but loses
badly on mcf and markov.

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
| **This project** (real omnetpp, L2, resized) | — | **2.43%** | 4.12 MB | No | 1 cycle |

Four honest caveats on this table:

- The published rows are measured on SPEC/GAP workloads, not on the four synthetic traces used here. The numbers are not directly comparable; the table is for rough positioning, not a head-to-head result.
- "1 cycle" describes pipeline depth — address in to prefetch decision is one combinational path — not an achieved clock frequency.
- That single-cycle path is also the design's main weakness: it synthesises at **≈70 MHz**, not at an L1-realistic clock. The commercial rows achieve 1-cycle latency at multi-GHz. See [Synthesis](#synthesis).
- The 45.59% figure is from synthetic traces. On real workloads the shipped configuration gets 0.62% (mcf) and 0.47% (omnetpp). Beating stride requires L2 placement and ~4 MB of metadata — a different design point from the 3,200-byte row — and even then only on mcf, not omnetpp.

---

## Verification

The Python model and the RTL are two implementations of the same algorithm, so the project checks that they actually agree rather than assuming it.

```bash
python tools/run_all_checks.py                # locally
python tools/run_all_checks.py --require-rtl  # as CI runs it: fail if iverilog is missing
```

Every check runs for **both configurations** (v1 and v2) — 11 steps in total — and the
same suite runs on each pull request via GitHub Actions
(`.github/workflows/checks.yml`).

| Step | What it proves |
|---|---|
| `tools/check_rtl_sync.py` | `rtl/ngram_types_pkg.sv` and `sim/config.py` declare identical geometry, and the derived invariants hold |
| `sim/test_cache.py` | 7 tests: LRU victim order, set isolation, prefetch-usefulness accounting, dead-prefetch counting |
| `sim/test_prefetchers.py` | 13 tests: both prefetchers' learning and confidence behaviour, delta-overflow handling, both trace parsers including records straddling a read-block boundary |
| `rtl/tb/tb_ngram_prefetcher.sv` | 7 phases — cold start, learned cycle, same-index bypass, noise rejection, delta overflow, reset, retraining speed. 65 assertions for v1, 62 for v2 (some phases scale with the window depth) |
| `tools/cosim_check.py` | Drives the RTL and the Python model with the same trace and diffs their prefetch streams access by access |
| `synthesis/synth_vivado.tcl` | Synthesises and place-and-routes the design; reports real post-route timing, area and power |
| `tools/run_champsim.py` | Evaluates against a real SPEC workload rather than the synthetic traces, at L1 or L2 |
| `tools/sizing_sweep.py` | Sweeps table size and delta width on a real trace to locate the design point |

The co-simulation is the one that matters most. For v1 on the synthetic traces:

```
  markov_semantic.trace      8000 accesses  model=4419   rtl=4419    agree=8000/8000  [MATCH]
  matrix.trace               2500 accesses  model=2489   rtl=2489    agree=2500/2500  [MATCH]
  pointer_chase.trace        5000 accesses  model=2405   rtl=2405    agree=5000/5000  [MATCH]
  streaming.trace            5000 accesses  model=4995   rtl=4995    agree=5000/5000  [MATCH]
```

v2 agrees on the same 20,500 accesses. The check has also been run against real
data — 1,000,000 accesses of SPEC mcf with v1, agreeing on every one — which
matters because that trace exercises the delta-overflow path 501,936 times,
where the synthetic traces never exercise it at all:

```bash
python tools/cosim_check.py traces/champsim/mcf.xz --limit 1000000
```

So the results tables describe the hardware and not just the model.

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

### v2 synthesis

The 2-delta configuration (`-tclargs -profile v2`), same part, tool and
constraints. Reports are in [`synthesis/reports/v2/`](synthesis/reports/v2/).

| Post-route | v1 (3-delta) | v2 (2-delta) | Δ |
|---|---:|---:|---:|
| Slice LUTs | 2,289 | **2,049** | −240 (−10.5%) |
|   — as logic | 1,777 | 1,537 | −240 |
|   — as distributed RAM | 512 | 512 | 0 |
| Registers | 1,184 | **1,167** | −17 |
| Slices | 693 | 625 | −68 |
| Total on-chip power | 0.174 W | 0.175 W | ~0 |
| WNS at 4.0 ns | −10.381 ns | −10.613 ns | −0.232 |
| Fastest period met | **15.0 ns** (66.7 MHz) | **16.0 ns** (62.5 MHz) | one step slower |

v2 is **10.5% smaller**. The 17-register saving is consistent with dropping one
16-bit history register; the table itself is unchanged, so distributed RAM is
identical.

It is **not faster**, and in this run it closed timing one sweep step later than
v1 — v2 missed 15.0 ns by 0.180 ns where v1 met it by 0.175 ns. The critical path
is the same wide-adder chain in both (28 CARRY4 stages in v1, 27 in v2), so
removing a hash tap does not touch it, and a difference of this size is more
likely placement variation than architecture. But that is an inference: the
measured result is that v2 closes at 16.0 ns, and that is what is reported here.

The practical reading: v2 buys coverage and area at no meaningful timing cost,
and neither configuration is anywhere near 250 MHz.

---

## Known limitations

- **Prefetch latency is not modelled, so every coverage figure in this README is optimistic.** A prefetched line is inserted into the cache the moment the prediction is made, and is usable on the very next access. In real hardware a prefetch has to travel to DRAM and back, which takes on the order of hundreds of cycles, so a prediction made only a few accesses before the data is needed arrives too late to help. Realistic evaluations count those as late prefetches and exclude them. This applies equally to both prefetchers and every configuration here, so *comparisons* between them are broadly fair, but the *absolute* coverage numbers overstate what hardware would achieve — and by how much has not been measured.

- **Random access patterns** (crypto, hash tables with uniform distribution): no repeating sequences to learn, coverage drops to 0%. The confidence counter prevents bad guesses though — the RTL testbench measures 0 speculative prefetches across 200 irregular accesses.
- **High branching factor** (BSTs with 50/50 left/right): a single predicted delta per entry means the counter oscillates and never reaches threshold.
- **Cold start**: needs 3 accesses to fill the shift register plus 2 observations to build confidence. First pass through a new data structure is always blind.
- **Shared across threads**: interleaved accesses from different threads would corrupt the delta history. A real CPU would need per-thread registers.
- **Delta range**: deltas are stored in a 16-bit signed field, so a jump larger than ±32,767 blocks (±2 MB) cannot be represented. Rather than wrapping it into a bogus small delta, both implementations treat it as a discontinuity, report it (`delta_overflow` in RTL, `delta_overflows` in the model), and rebuild the window. The shipped pointer-chase trace already reaches ±15,436, so a heap much larger than 1 MB would start hitting this.
- **Benchmark scope**: the four traces are synthetic and each uses a single instruction pointer. That means the stride baseline's 256-entry IP-indexed table is only ever exercised as a single entry, so the stride comparison is less demanding than it would be on a real multi-IP workload. Treat the stride numbers as a floor, not a tuned baseline.
- **Table capacity is the binding constraint when the workload cooperates**: mcf presents ~516,000 distinct 3-delta contexts where the shipped table holds 1,024, and coverage scales directly with capacity (17.63% at 64K entries, 57.30% at 512K, 66.28% at 2M). The synthetic traces hide this completely because they generate only 393–500 contexts.
- **Capacity does not help when the contexts do not repeat**: on omnetpp only 16.2% of delta windows ever recur, and when one does the follower matches just 55.1% of the time. Coverage plateaus near 2.6% regardless of table size. The design is only worth building for workloads that score well on those two measurements.
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
    tb_ngram_prefetcher.sv - self-checking testbench (65 assertions v1, 62 v2)
    tb_cosim.sv            - trace-driven harness for co-simulation

tools/
  run_all_checks.py        - runs everything below for both profiles
  fetch_traces.py          - download the SPEC traces into traces/champsim/
  run_champsim.py          - evaluate on a real ChampSim trace (L1 or L2)
  sizing_sweep.py          - sweep table size and delta width against a trace
  check_rtl_sync.py        - RTL/model parameter agreement
  cosim_check.py           - RTL/model behavioural equivalence

synthesis/
  synth_vivado.tcl         - synthesis + place & route
  fmax_sweep.tcl           - implements at several periods to find real Fmax (-profile v1|v2)
  constraints.xdc          - timing constraints (read before synthesis)
  reports/                 - measured utilisation, timing, power, Fmax sweep (v1)
  reports/v2/              - the same, for the 2-delta configuration

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
