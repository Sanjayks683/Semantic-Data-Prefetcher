# Semantic Data Prefetcher (N-Gram Based)

Hardware delta-correlation prefetcher aimed at irregular memory access — linked-list traversals and pointer chasing — which a stride prefetcher cannot learn.

Built as a B.Tech capstone project. Has both a Python trace-driven cache simulator and a synthesizable SystemVerilog RTL design, with an automated check that the two are behaviourally equivalent.

## At a glance

| | Result |
|---|---|
| **Synthetic traces** (written for this project) | 45.59% coverage on pointer chasing at 100% accuracy, where stride gets 0% |
| **8 real SPEC CPU2017 workloads, as shipped** | **Beats stride on none.** Mean coverage 5.4% against stride's 44.5% |
| **8 SPEC workloads, L2, realistic prefetch latency** | Best single n-gram configuration averages **21.1%**; stride with equal lookahead averages **41.6%**. The n-gram wins on **1 of 8** (xalancbmk) |
| **Instant prefetch arrival** (the unrealistic assumption) | Close to stride at L2 (36.2% vs 41.3%), and beats it on mcf. **Does not survive latency:** 4 accesses of delay cut the n-gram to 16.8%; at 64 it is 0.02% |
| **RTL vs Python model** | Equivalent on every access: both cores and both configurations on the synthetic traces, and both cores on 1M real mcf accesses (v1) |
| **Synthesis** (Xilinx 7-series, post-route) | Single-cycle core closes at 66.7 MHz. **Pipelined core closes at 166.7 MHz** (2.5×), proven equivalent. 250 MHz is still not met |

The short version: the design is correctly implemented and verified, and on real code with realistic prefetch latency a well-configured stride prefetcher beats it on six of eight workloads; it wins one, and on the last both make things worse. It predicts the next miss, and with a realistic fetch delay the next miss is exactly the one it cannot serve in time. The one workload where it wins, and the reason it loses elsewhere, are both explained below.

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
> fit it exactly, and they assume prefetches arrive instantly. Treat these
> numbers as an upper bound on favourable input. See
> [Eight-workload evaluation](#eight-workload-evaluation-with-prefetch-latency)
> for what happens on real SPEC code, where this configuration beats stride on
> none of eight workloads.

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

## Eight-workload evaluation with prefetch latency

This is the evaluation the rest of the README should be read against. It fixes
the three weaknesses of the earlier real-workload study below: two workloads is
a sample of two, prefetches arrived instantly, and the n-gram was compared
against a stride prefetcher that could only look one step ahead.

- **Eight SPEC CPU2017 workloads** from the DPC-3 trace set, 20,000,000 memory
  accesses each: mcf, omnetpp, gcc, xalancbmk, leela, xz, lbm and bwaves. The
  mix deliberately includes regular HPC codes (lbm, bwaves) where stride should
  win. The six added traces are each the first SimPoint listed for their
  benchmark, a rule fixed before any results were seen; mcf and omnetpp keep the
  SimPoints used in the earlier study.
- **Prefetch latency** is modelled: a prefetch issued on access *i* is usable
  from access *i* + 1 + *L*. A demand request that arrives first cancels it and
  counts it as *late* — it cost bandwidth, helped nobody, and counts against
  accuracy.
- **Lookahead and multiple slots** are evaluated for the n-gram, and **stride
  gets the same lookahead**, so neither design is handicapped.

Two choices were fixed in code before results existed, because either could
otherwise be tuned to flatter a design:

- **Headline latency: 64 accesses.** A DRAM round trip is on the order of 100 ns,
  which spans a few hundred instructions; mcf measures 0.61 memory accesses per
  instruction, so roughly 100 accesses is the right order of magnitude. 64 is the
  nearest sweep point below that, i.e. slightly generous to prefetchers. The full
  sweep from 0 to 256 is shown so the conclusion can be checked at any value.
- **"Best configuration" means the single configuration with the highest mean
  coverage across all eight workloads.** Choosing the best setting per workload
  would flatter both designs, so that figure only appears labelled as an oracle.

```bash
python tools/fetch_traces.py                 # ~3.9 GB of traces into traces/champsim/
python tools/evaluate.py                     # 432 runs; ~1 hour on 14 cores
python tools/summarize_evaluation.py         # the tables below
```

Coverage is **signed**: negative means the prefetcher caused more misses than no
prefetcher at all.

### As shipped: L1, 1,024 entries, 16-bit deltas, instant arrival

Coverage (accuracy in brackets).

| Workload | Stride | N-gram v1 | N-gram v2 |
|---|---:|---:|---:|
| **mcf** | 69.49% (100%) | 0.62% (80%) | 0.09% (45%) |
| **omnetpp** | 2.86% (58%) | 0.47% (62%) | 0.78% (42%) |
| **gcc** | 73.06% (99%) | 31.60% (100%) | 38.77% (100%) |
| **xalancbmk** | 7.37% (78%) | 1.37% (75%) | 2.33% (49%) |
| **leela** | 22.45% (92%) | 8.61% (62%) | 0.45% (29%) |
| **xz** | 4.33% (83%) | 0.13% (23%) | 0.06% (21%) |
| **lbm** | 77.80% (100%) | 0.01% (45%) | 0.00% (0%) |
| **bwaves** | 98.30% (99%) | 0.50% (70%) | 0.76% (59%) |
| *mean* | *44.46%* | *5.41%* | *5.40%* |

As built, the design beats stride on **none of the eight**. This extends the
two-workload result below from a sample of two to eight.

### Prefetch latency: the result that decides everything

Mean coverage across the eight workloads at L2, by latency in memory accesses:

| Configuration | 0 | 4 | 16 | 64 | 256 |
|---|---:|---:|---:|---:|---:|
| Stride, degree 1 | 41.29% | 38.02% | 29.65% | 20.93% | 13.89% |
| Stride, degree 4 | 45.57% | 45.36% | 43.69% | 33.33% | 22.40% |
| N-gram depth 2, degree 1 | 36.22% | 16.84% | 3.12% | 0.02% | −0.40% |
| N-gram depth 2, degree 4 | 39.19% | 34.05% | 17.19% | 3.33% | −0.95% |

Share of issued prefetches that arrived after the demand request:

| Configuration | 0 | 4 | 16 | 64 | 256 |
|---|---:|---:|---:|---:|---:|
| Stride, degree 1 | 0% | 4% | 29% | 60% | 89% |
| Stride, degree 4 | 0% | 0% | 1% | 26% | 55% |
| N-gram depth 2, degree 1 | 0% | 65% | 92% | 96% | 97% |
| N-gram depth 2, degree 4 | 0% | 15% | 62% | 87% | 96% |

With instant arrival the n-gram is competitive — 36.2% against stride's 41.3%.
**Four accesses of delay are enough to cut it to 16.8%**, while stride barely
moves; at 64, 96% of its prefetches arrive too late.

The reason is structural, not a tuning problem. The n-gram is a *global* delta
correlator: it predicts the very next miss in the stream it observes. With any
real fetch delay, the very next miss is precisely the one a prefetch cannot
reach in time. Stride is indexed per instruction, so its next prediction is for
the next access *by that same instruction*, which in interleaved code is
naturally further away. The same latency that barely dents stride removes almost
all of the n-gram's value.

Lookahead is therefore not an optional extra for this design — it is what makes
it viable at all.

### With realistic latency and equal lookahead

L2, 64-access latency. Best single configuration per family by mean coverage:
**stride, degree 8** and **n-gram, depth 2, degree 8, 2 slots**. The right-hand
columns are an oracle — the best configuration for that one workload — and no
single design achieves them.

| Workload | Stride | N-gram | Winner | Oracle stride | Oracle n-gram |
|---|---:|---:|---:|---:|---:|
| **mcf** | 58.72% (98%) | 0.30% (0%) | stride | 58.72% | 1.89% |
| **omnetpp** | 3.34% (22%) | 0.25% (11%) | stride | 4.75% | 6.86% |
| **gcc** | 82.65% (87%) | 60.71% (73%) | stride | 82.65% | 78.71% |
| **xalancbmk** | 3.72% (33%) | **17.00%** (7%) | **n-gram** | 4.10% | 21.78% |
| **leela** | −15.34% (14%) | −32.70% (13%) | neither (both harmful) | −4.13% | −3.97% |
| **xz** | 4.06% (77%) | 2.74% (38%) | stride | 4.06% | 2.74% |
| **lbm** | 99.96% (100%) | 29.46% (27%) | stride | 99.96% | 31.72% |
| **bwaves** | 95.83% (99%) | 90.66% (100%) | stride | 95.83% | 90.73% |
| *mean* | *41.62%* | *21.05%* | | | |

**Stride wins six, the n-gram wins one, and on leela both make things worse.**
Even granting the n-gram its best setting for every workload separately, it
beats stride's per-workload best only on omnetpp and xalancbmk.

What lookahead buys, in mean coverage:

| | Stride | N-gram (depth 2, 2 slots) |
|---|---:|---:|
| degree 1 | 20.93% | 3.26% |
| degree 2 | 28.16% | 8.05% |
| degree 4 | 33.33% | 15.54% |
| degree 8 | 41.62% | 21.05% |

Both designs gain from looking further ahead, and the n-gram gains
proportionally more — degree 8 is worth 6.5× its single-step coverage — but it
never closes the gap.

What extra slots buy (n-gram, depth 2, degree 8):

| Slots | mcf | omnetpp | gcc | xalancbmk | leela | xz | lbm | bwaves |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.08% | 0.26% | 48.90% | 2.29% | −11.94% | 2.30% | 25.56% | 0.11% |
| 2 | 0.30% | 0.25% | 60.71% | **17.00%** | −32.70% | 2.74% | 29.46% | **90.66%** |
| 4 | 0.36% | 0.69% | 60.68% | 16.56% | **−85.93%** | 2.46% | 31.72% | 90.68% |

Multiple slots are a real lever for branching access — xalancbmk's DOM
traversal goes from 2.29% to 17.00%, and bwaves from 0.11% to 90.66% — and a real
hazard: on leela, four slots nearly double the miss count, because every
confident follower is prefetched and most of them pollute a cache that did not
need them.

Configurations that increased misses over no prefetcher at all: **38 of 192**
n-gram runs and **4 of 32** stride runs.

### What survives

- **The implementation is sound.** Both RTL cores match the model on every
  access, and the model reproduces every earlier result exactly.
- **As a general-purpose prefetcher, the design loses to stride.** Once
  prefetches take time to arrive and stride may look equally far ahead, stride
  wins six of eight workloads and averages about twice the coverage, with fewer
  harmful configurations and ~2 KB of state against ~4 MB.
- **It has a niche.** On branching, pointer-heavy access — xalancbmk here — a
  multi-slot n-gram with lookahead reaches 17% where stride manages under 4%.
  The natural next step would be a hybrid that runs stride by default and
  arbitrates to the n-gram per instruction when stride is not confident. That is
  not implemented.
- **The earlier instant-arrival results are an upper bound**, not a prediction —
  including the two-workload finding below that a resized n-gram beats stride on
  mcf. That finding does not survive latency: at 64 accesses its coverage on mcf
  is 0.30%.

Caveats on this evaluation: latency is measured in memory accesses, not time;
each workload is one SimPoint and its first 20M accesses; the cache model is this
project's own rather than ChampSim's; and coverage is not the same as speedup —
no IPC is measured.

---

## Earlier two-workload study (instant arrival)

> **Superseded by the [eight-workload evaluation](#eight-workload-evaluation-with-prefetch-latency)
> above.** This study assumed prefetches arrive instantly. Its sizing and
> diagnostic findings still hold — the table-capacity analysis, and the
> recurrence/determinism measurement that predicts which workloads can benefit —
> but its headline, that a resized n-gram at L2 beats stride on mcf, does not
> survive realistic latency. It is kept because the diagnosis is still useful
> and because it is how the design's weaknesses were first found.

This section runs the model over two real SPEC workloads from the DPC-3 ChampSim
set — `605.mcf_s` and `620.omnetpp_s` — at 20,000,000 memory accesses each.

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

### What this meant at the time

*Written under the instant-arrival assumption; the eight-workload evaluation
shows that even the mcf win below disappears once prefetches take time to arrive.*

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
`tools/run_all_checks.py` verifies both, for both RTL cores, including RTL/model
co-simulation for each profile independently.

### What v2 buys

On the synthetic traces:

| Workload | v1 coverage | v2 coverage | Δ |
|---|---:|---:|---:|
| Streaming | 99.88% | 99.90% | +0.02 |
| Matrix | 97.56% | 97.68% | +0.12 |
| Pointer Chase | 45.59% | 45.62% | +0.03 |
| **Markov** | 35.60% | **40.15%** | **+4.55** |

On the real workloads at L2 with a 48-bit delta field and 524,288 entries, with
**instant prefetch arrival** (see the eight-workload evaluation for what latency
does to these):

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

The choice held up under the harder evaluation: with 64-access latency across
eight workloads, the n-gram configuration with the best mean coverage also uses a
2-delta window.

---

## How it compares

| | Array Coverage | Pointer Chase Coverage | Storage | Needs Compiler? | Pipeline depth |
|---|:---:|:---:|:---:|:---:|:---:|
| Intel/AMD stride prefetchers | ~95-99% | 0-5% | ~1-2 KB | No | 1 cycle |
| Peled et al. (ISCA '15) | ~95% | 30-45% | ~31 KB | Yes (LLVM NOPs) | Multi-cycle |
| IPCP (ISCA '20) | ~96% | 35-45% | ~12 KB | No | 1-2 cycles |
| **This project** (synthetic traces) | **99.88%** | **45.59%** | **3,200 bytes** | **No** | 1 or 5 cycles |
| **This project** (8 SPEC workloads, as shipped) | — | *mean* **5.4%** | 3,200 bytes | No | 1 or 5 cycles |
| **This project** (8 SPEC, L2, 64-access latency, best config) | — | *mean* **21.1%** | ~4.2 MB | No | model only |
| *Stride, same evaluation, degree 8* | — | *mean* **41.6%** | ~2 KB | No | — |

Caveats on this table:

- The published rows are measured on SPEC/GAP workloads under their own methodology, and the "pointer chase coverage" column is not a like-for-like metric across them. The table is for rough positioning, not a head-to-head result.
- "1 or 5 cycles": the single-cycle core decides in the same cycle but closes timing at only 66.7 MHz; the pipelined core takes 5 cycles and closes at 166.7 MHz. See [Synthesis](#synthesis).
- The best evaluated configuration uses lookahead (degree 8) and 2 slots per entry, which exist only in the Python model — neither RTL core implements them. See [Known limitations](#known-limitations).
- The 45.59% synthetic figure is the one this project was first built around. On real code with realistic latency, the same kind of stride prefetcher the design was meant to beat averages about twice its coverage.

---

## Verification

The Python model and the RTL are two implementations of the same algorithm, so the project checks that they actually agree rather than assuming it.

```bash
python tools/run_all_checks.py                # locally
python tools/run_all_checks.py --require-rtl  # as CI runs it: fail if iverilog is missing
```

Every check runs for **both configurations** (v1 and v2) and **both RTL cores** —
18 steps in total — and the same suite runs on each pull request via GitHub
Actions (`.github/workflows/checks.yml`).

| Step | What it proves |
|---|---|
| `tools/check_rtl_sync.py` | `rtl/ngram_types_pkg.sv` and `sim/config.py` declare identical geometry, and the derived invariants hold |
| `sim/test_cache.py` | 7 tests: LRU victim order, set isolation, prefetch-usefulness accounting, dead-prefetch counting |
| `sim/test_simulator.py` | 10 tests of the shared simulation loop, chiefly the latency model: exact arrival time, late-prefetch cancellation, in-flight deduplication, the global clock at L2 |
| `sim/test_prefetchers.py` | 23 tests: learning, confidence and overflow handling for both prefetchers, lookahead (read-only, stops when unconfident, follows the strongest slot), multi-slot branching and hysteresis, and both trace parsers |
| `rtl/tb/tb_ngram_prefetcher.sv` | Single-cycle core, 7 phases — cold start, learned cycle, same-index bypass, noise rejection, delta overflow, reset, retraining speed. 65 assertions for v1, 62 for v2 |
| `tools/cosim_check.py` | Drives an RTL core and the Python model with the same trace and diffs their prefetch streams access by access (`--core comb` or `--core pipe`) |
| `rtl/tb/tb_pipe_equiv.sv` | Runs the pipelined and single-cycle cores side by side on 300,000+ accesses with idle gaps, overflow jumps and mid-stream resets, comparing every cycle; fails if the stimulus is too weak to mean anything |

The analysis tools are not correctness checks, but they are what produced the
results: `tools/evaluate.py` and `tools/summarize_evaluation.py` (the
eight-workload evaluation), `tools/run_champsim.py` and `tools/sizing_sweep.py`
(the earlier study), and `synthesis/synth_vivado.tcl` / `fmax_sweep.tcl`.

**The tests were themselves tested.** The simulator, prefetcher and pipeline
checks were each mutation-tested: bugs were injected on purpose to confirm the
suite catches them. Each time, some slipped through at first — a late prefetch
that was never cancelled, lookahead writing to the table, a pipeline that did not
clear its retraining state on an overflow — and a test was added for the specific
scenario that exposes each one. All 15 injected bugs are now caught.

The co-simulation is the one that matters most. For v1 on the synthetic traces:

```
  markov_semantic.trace      8000 accesses  model=4419   rtl=4419    agree=8000/8000  [MATCH]
  matrix.trace               2500 accesses  model=2489   rtl=2489    agree=2500/2500  [MATCH]
  pointer_chase.trace        5000 accesses  model=2405   rtl=2405    agree=5000/5000  [MATCH]
  streaming.trace            5000 accesses  model=4995   rtl=4995    agree=5000/5000  [MATCH]
```

v2 agrees on the same 20,500 accesses, and so does the pipelined core for both
profiles. The check has also been run against real data — 1,000,000 accesses of
SPEC mcf, for both cores with v1, agreeing on every one with 222,047 prefetches
each — which matters because that trace exercises the delta-overflow path 501,936
times, where the synthetic traces never exercise it at all:

```bash
python tools/cosim_check.py traces/champsim/mcf.xz --limit 1000000
python tools/cosim_check.py traces/champsim/mcf.xz --limit 1000000 --core pipe
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

**This is architectural, not a bug.** The design is genuinely single-cycle — one access in, one prefetch decision out, same cycle — and that is exactly why it is slow. Reaching a realistic L1 clock would mean pipelining it into 3–4 stages, keeping one access per cycle of throughput while allowing several cycles of latency. That is a reasonable trade for a prefetcher, which sits off the demand-critical path and only needs its prediction to arrive before the data is used. That pipelined version now exists — see [Pipelined core](#pipelined-core) below; the numbers above are for the single-cycle design.

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

### Pipelined core

`rtl/ngram_prefetcher_pipe.sv` splits the single-cycle path into five registered
stages, making the same decision for every access five cycles later:

```
S0 input register -> S1 58-bit delta + range check -> S2 window + XOR hash
                  -> S3 table read, retrain, bypass, confidence -> S4 58-bit add -> outputs
```

Throughput is unchanged — one access per cycle, back to back — and an
`access_done` output pulses as each access's result emerges.

**Why it is exactly equivalent rather than approximately so.** Every interaction
with the prediction table — the lookup for an access and the retraining write
that access triggers — happens in S3, in the same cycle, just as in the
single-cycle core. Earlier writes have already committed, and the same-cycle case
is the read-during-write bypass the single-cycle core already has. No access can
observe table state the single-cycle core would not have shown it. That is
checked two ways: cycle by cycle against the single-cycle core
(`rtl/tb/tb_pipe_equiv.sv`), and access by access against the Python model,
including 1,000,000 real mcf accesses.

Same part, tool and constraints as the single-cycle core
(`-tclargs -core pipe`; reports in [`synthesis/reports/pipe/`](synthesis/reports/pipe/)):

| Target period | Target | Post-route WNS | Status |
|---:|---:|---:|:---|
| 4.0 ns | 250.0 MHz | −1.769 ns | VIOLATED |
| 5.0 ns | 200.0 MHz | −0.649 ns | VIOLATED |
| **6.0 ns** | **166.7 MHz** | **+0.053 ns** | **MET** |
| 7.0 ns | 142.9 MHz | +0.568 ns | MET |
| 8.0 ns | 125.0 MHz | +1.083 ns | MET |

| Post-route | Single-cycle (v1) | Pipelined (v1) |
|---|---:|---:|
| Fastest period met | 15.0 ns (66.7 MHz) | **6.0 ns (166.7 MHz)** |
| Slice LUTs | 2,289 | 1,832 |
|   — as logic | 1,777 | 1,320 |
|   — as distributed RAM | 512 | 512 |
| Registers | 1,184 | 1,563 |
| Slices | 693 | 592 |
| Total on-chip power | 0.174 W | 0.182 W |

**2.5× the frequency**, for 379 extra pipeline registers. LUT usage fell rather
than rose; that is consistent with synthesis no longer having to restructure a
36-level path, though it is an observation, not something isolated by experiment.

It still does not reach 250 MHz, and the reason has moved. The adders are no
longer critical. The worst path now sits entirely inside S3: registered table
index → 1024-deep distributed-RAM read (RAM64 → MUXF7 → MUXF8) → bypass mux →
tag and confidence compare → `s3_hit`, 5.716 ns over 7 logic levels, **71% of it
routing**. Splitting that stage would separate the table read from the compare,
which means forwarding writes across stages rather than relying on a same-cycle
bypass — a real redesign with equivalence risk, and not done here.

---

## Known limitations

- **A global delta predictor has an inherently short horizon.** It predicts the next miss in the stream it watches, and under any realistic fetch delay the next miss is the one a prefetch cannot reach in time. This, more than table size, is why the design loses to stride once latency is modelled: four accesses of delay cut its mean coverage from 36.2% to 16.8%. Lookahead offsets part of it, never all of it.
- **Lookahead and multiple slots exist only in the Python model.** Both RTL cores implement one step and one slot. The best evaluated configuration needs up to eight chained table lookups per access, which in hardware means several read ports or several cycles per access; given that it beats stride on one workload of eight, that hardware was not built. The eight-workload results for those configurations describe the algorithm, not a circuit.
- **Latency is modelled in memory accesses, not time.** A trace has no clock, so an arrival delay of *L* accesses stands in for a real fetch time. The headline of 64 accesses is an order-of-magnitude estimate, fixed before results were produced; the sweep from 0 to 256 is published so conclusions can be checked at other values. In that sweep, stride leads the n-gram in mean coverage at every latency tested, including zero; the lookahead and slot comparisons were run at 64 only.
- **Evaluation method.** Each SPEC workload is one SimPoint and its first 20M accesses; the cache model is this project's own rather than ChampSim's; and coverage is not speedup — no IPC is measured.
- **Random access patterns** (crypto, hash tables with uniform distribution): no repeating sequences to learn. The confidence counter limits bad guesses — the RTL testbench measures 0 speculative prefetches across 200 irregular accesses — but real workloads still show harm: 38 of 192 n-gram configurations increased misses.
- **High branching factor**: with one slot, a window followed alternately by different deltas never reaches confidence. Multiple slots fix that in the model (xalancbmk rises from 2.29% to 17.00%), at the cost of polluting caches that did not need the extra prefetches (leela falls to −85.93% with four slots).
- **Cold start**: needs the history window full plus 2 observations to build confidence. First pass through a new data structure is always blind.
- **Shared across threads**: interleaved accesses from different threads would corrupt the delta history. A real CPU would need per-thread state.
- **Delta range**: the RTL stores deltas in a 16-bit signed field. 54% of mcf's accesses and 78% of its L1 miss stream exceed it — real programs interleave stack and heap regions gigabytes apart. Both implementations treat an overflow as a discontinuity rather than wrapping it into a bogus delta; the model's 48-bit configurations remove the problem.
- **Table capacity**: real workloads present hundreds of thousands of distinct delta contexts against the RTL's 1,024 entries. The evaluated configurations use 524,288 entries (~4.2 MB), three orders of magnitude more state than stride's ~2 KB.
- **Clock frequency**: the single-cycle core closes at 66.7 MHz and the pipelined core at 166.7 MHz, both on a −1 speed-grade 7-series part. Neither reaches 250 MHz; the pipelined core's remaining critical path is the table read plus compare inside one stage.
- **Synthetic benchmark scope**: the four synthetic traces each use a single instruction pointer, so stride's IP-indexed table is only exercised as one entry there. The SPEC evaluation does not share this weakness.

---

## Files

```
sim/
  config.py                - all geometry; v1/v2 profile selection
  cache.py                 - set-associative cache model (LRU), any geometry
  simulator.py             - the one simulation loop: L1/L2 placement, prefetch latency
  stride_prefetcher.py     - baseline stride prefetcher, with lookahead degree
  ngram_prefetcher.py      - the n-gram reference model, with lookahead and slots
  generate_trace.py        - generates the synthetic workload traces
  trace_parser.py          - text and ChampSim binary trace readers
  metrics.py               - hit/miss rate, accuracy, signed coverage, pollution, late prefetches
  main.py                  - synthetic benchmarks -> results/benchmark_results*.csv
  plot_results.py          - bar chart comparisons
  test_cache.py            - cache unit tests
  test_simulator.py        - simulator and latency-model unit tests
  test_prefetchers.py      - prefetcher and parser unit tests

rtl/
  ngram_types_pkg.sv       - parameters and types; `ifdef NGRAM_V2 selects v2
  delta_generator.sv       - block delta plus range check
  history_shift_reg.sv     - history window with flush
  xor_hash.sv              - folded XOR with per-tap rotations
  sram_table.sv            - 1024-entry table, RAM payload + read-during-write bypass
  confidence_fsm.sv        - 2-bit saturating counter
  ngram_prefetcher.sv      - single-cycle top level
  ngram_prefetcher_pipe.sv - 5-stage pipelined top level
  tb/
    tb_ngram_prefetcher.sv - self-checking testbench, single-cycle core
    tb_pipe_equiv.sv       - pipelined vs single-cycle, every cycle
    tb_cosim.sv            - trace-driven harness for co-simulation (either core)

tools/
  run_all_checks.py        - every check, both profiles, both cores (18 steps)
  check_rtl_sync.py        - RTL/model parameter agreement
  cosim_check.py           - RTL/model behavioural equivalence (--core comb|pipe)
  fetch_traces.py          - download the eight SPEC traces into traces/champsim/
  evaluate.py              - eight-workload evaluation matrices, in parallel
  summarize_evaluation.py  - the README's evaluation tables, from the CSVs
  run_champsim.py          - one trace, three prefetchers (L1 or L2, --latency)
  sizing_sweep.py          - table size / delta width / depth sweep against a trace

synthesis/
  synth_vivado.tcl         - synthesis + place & route (-core, -profile, -period)
  fmax_sweep.tcl           - implements at several periods to find real Fmax
  constraints.xdc          - timing constraints (read before synthesis)
  reports/                 - single-cycle v1: utilisation, timing, power, Fmax sweep
  reports/v2/              - single-cycle v2
  reports/pipe/            - pipelined v1

results/
  benchmark_results*.csv   - synthetic traces, v1 and v2
  evaluation_*.csv         - eight-workload evaluation: shipped, latency, design
  champsim_*.csv, *sweep*  - the earlier two-workload study

traces/                    - synthetic traces (committed)
traces/champsim/           - SPEC traces (fetched, gitignored)
```

## Running it

Synthetic benchmarks:
```bash
cd sim
python generate_trace.py
python main.py                         # v1
NGRAM_PROFILE=v2 python main.py        # v2
```

Every correctness check (needs Icarus Verilog):
```bash
python tools/run_all_checks.py
```

The eight-workload evaluation:
```bash
python tools/fetch_traces.py           # ~3.9 GB
python tools/evaluate.py               # ~1 hour on 14 cores
python tools/summarize_evaluation.py
```

Synthesis (needs Vivado):
```bash
vivado -mode batch -source synthesis/synth_vivado.tcl -tclargs -core pipe
vivado -mode batch -source synthesis/fmax_sweep.tcl  -tclargs -core pipe -periods "5 6 7"
```

---

## References

1. Peled, Mannor, Weiser, Etsion — *"Semantic Locality and Context-based Prefetching Using Reinforcement Learning"*, ISCA 2015
2. Pakalapati, Biswal — *"IPCP: Instruction Pointer Classifying Prefetcher"*, ISCA 2020
3. Intel — *"Intel 64 and IA-32 Architectures Optimization Reference Manual"*, Ch. 2
