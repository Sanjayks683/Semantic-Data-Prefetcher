#!/usr/bin/env python3
"""
summarize_evaluation.py - Turn results/evaluation_*.csv into the README's tables.

Every number in the README's real-workload evaluation comes from this script, so
the tables can be regenerated from the committed CSVs without re-simulating.

    python tools/summarize_evaluation.py > evaluation_summary.md

How a "best configuration" is chosen matters, so it is fixed here rather than
decided while looking at results: for each prefetcher family, the single
configuration with the highest MEAN coverage across all traces. Picking the best
configuration separately for each trace would flatter both designs, so that
per-trace figure is only ever shown labelled as an oracle upper bound.

Coverage is signed. Negative means the prefetcher caused more misses than having
no prefetcher at all.
"""

import os
import csv
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "results")
TRACES = ["mcf", "omnetpp", "gcc", "xalancbmk", "leela", "xz", "lbm", "bwaves"]


def load(name):
    path = os.path.join(RESULTS, f"evaluation_{name}.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def cov(r):
    return float(r["coverage"])


def fmt(x, digits=2):
    return f"{x:+.{digits}f}%" if x < 0 else f"{x:.{digits}f}%"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" if i == 0 else "---:" for i in range(len(header))) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def index(rows):
    by = defaultdict(dict)
    for r in rows:
        by[r["config"]][r["workload"]] = r
    return by


def mean_cov(by_cfg, cfg, traces):
    vals = [cov(by_cfg[cfg][t]) for t in traces if t in by_cfg[cfg]]
    return sum(vals) / len(vals) if len(vals) == len(traces) else None


def label(r):
    if r["prefetcher"] == "stride":
        return f"stride, degree {r['degree']}"
    parts = [f"depth {r['depth']}"]
    if r["degree"]:
        parts.append(f"degree {r['degree']}")
    if r["slots"]:
        parts.append(f"{r['slots']} slot{'s' if r['slots'] != '1' else ''}")
    return "n-gram, " + ", ".join(parts)


def section_shipped(rows):
    traces = [t for t in TRACES if any(r["workload"] == t for r in rows)]
    by = index(rows)
    cfgs = [("stride-l1-lat0-degree1", "Stride"),
            ("ngram-l1-lat0-depth3", "N-gram v1"),
            ("ngram-l1-lat0-depth2", "N-gram v2")]
    body = []
    for t in traces:
        row = [f"**{t}**"]
        for c, _ in cfgs:
            r = by[c][t]
            row.append(f"{fmt(cov(r))} ({float(r['accuracy']):.0f}%)")
        body.append(row)
    body.append(["*mean*"] + [f"*{fmt(mean_cov(by, c, traces))}*" for c, _ in cfgs])
    wins = sum(1 for t in traces if max(cov(by[c][t]) for c, _ in cfgs[1:]) > cov(by[cfgs[0][0]][t]))
    return ("### As shipped: L1, 1,024 entries, 16-bit deltas, instant arrival\n\n"
            "Coverage (accuracy in brackets).\n\n"
            + table(["Workload"] + [n for _, n in cfgs], body)
            + f"\n\nAn n-gram configuration beats stride on {wins} of {len(traces)} workloads.\n")


def section_latency(rows):
    traces = [t for t in TRACES if any(r["workload"] == t for r in rows)]
    by = index(rows)
    lats = sorted({int(r["latency"]) for r in rows if r["prefetcher"] != "none"})
    families = [("stride", "1", None, "Stride, degree 1"),
                ("stride", "4", None, "Stride, degree 4"),
                ("ngram", "1", "2", "N-gram depth 2, degree 1"),
                ("ngram", "4", "2", "N-gram depth 2, degree 4")]
    body = []
    for pf, deg, depth, name in families:
        row = [name]
        for lat in lats:
            if pf == "stride":
                c = f"stride-l2-lat{lat}-degree{deg}"
            else:
                c = f"ngram-l2-lat{lat}-degree{deg}-depth{depth}-slots1"
            m = mean_cov(by, c, traces)
            row.append(fmt(m) if m is not None else "-")
        body.append(row)

    late = []
    for pf, deg, depth, name in families:
        row = [name]
        for lat in lats:
            c = (f"stride-l2-lat{lat}-degree{deg}" if pf == "stride"
                 else f"ngram-l2-lat{lat}-degree{deg}-depth{depth}-slots1")
            issued = sum(int(by[c][t]["issued"]) for t in traces)
            lt = sum(int(by[c][t]["late"]) for t in traces)
            row.append(f"{100 * lt / issued:.0f}%" if issued else "-")
        late.append(row)

    return ("### Prefetch latency: how much arriving late costs\n\n"
            f"Mean coverage across {len(traces)} workloads at L2, by latency in memory accesses.\n\n"
            + table(["Configuration"] + [f"{l}" for l in lats], body)
            + "\n\nShare of issued prefetches that arrived after the demand request:\n\n"
            + table(["Configuration"] + [f"{l}" for l in lats], late) + "\n")


def section_design(rows):
    traces = [t for t in TRACES if any(r["workload"] == t for r in rows)]
    by = index(rows)
    cfgs = [c for c in by if not c.startswith("none")]
    stride_cfgs = [c for c in cfgs if c.startswith("stride")]
    ngram_cfgs = [c for c in cfgs if c.startswith("ngram")]

    best_s = max(stride_cfgs, key=lambda c: mean_cov(by, c, traces))
    best_n = max(ngram_cfgs, key=lambda c: mean_cov(by, c, traces))
    ls = label(next(iter(by[best_s].values())))
    ln = label(next(iter(by[best_n].values())))

    body, wins_n, wins_s = [], 0, 0
    for t in traces:
        s, n = by[best_s][t], by[best_n][t]
        oracle_s = max(stride_cfgs, key=lambda c: cov(by[c][t]))
        oracle_n = max(ngram_cfgs, key=lambda c: cov(by[c][t]))
        if cov(n) > cov(s):
            winner, wins_n = "n-gram", wins_n + 1
        elif cov(s) > cov(n):
            winner, wins_s = "stride", wins_s + 1
        else:
            winner = "tie"
        body.append([f"**{t}**",
                     f"{fmt(cov(s))} ({float(s['accuracy']):.0f}%)",
                     f"{fmt(cov(n))} ({float(n['accuracy']):.0f}%)",
                     winner,
                     fmt(cov(by[oracle_s][t])), fmt(cov(by[oracle_n][t]))])
    body.append(["*mean*", f"*{fmt(mean_cov(by, best_s, traces))}*",
                 f"*{fmt(mean_cov(by, best_n, traces))}*", "", "", ""])

    # Effect of slots, holding the best n-gram's depth and degree fixed.
    ref = by[best_n][traces[0]]
    slots_rows = []
    for s in ("1", "2", "4"):
        c = f"ngram-l2-lat{ref['latency']}-degree{ref['degree']}-depth{ref['depth']}-slots{s}"
        if c in by:
            slots_rows.append([f"{s} slot{'s' if s != '1' else ''}"]
                              + [fmt(cov(by[c][t])) for t in traces])

    # Effect of degree, holding the best n-gram's depth and slots fixed.
    deg_rows = []
    for d in ("1", "2", "4", "8"):
        c = f"ngram-l2-lat{ref['latency']}-degree{d}-depth{ref['depth']}-slots{ref['slots']}"
        cs = f"stride-l2-lat{ref['latency']}-degree{d}"
        if c in by and cs in by:
            deg_rows.append([f"degree {d}", fmt(mean_cov(by, cs, traces)), fmt(mean_cov(by, c, traces))])

    harm = defaultdict(lambda: [0, 0])
    for c in cfgs:
        fam = "stride" if c.startswith("stride") else "n-gram"
        for t in traces:
            harm[fam][1] += 1
            if cov(by[c][t]) < 0:
                harm[fam][0] += 1

    lat = ref["latency"]
    return (f"### Lookahead and multiple slots, at L2 with {lat}-access latency\n\n"
            "Best single configuration per family, chosen by mean coverage across all "
            f"workloads: **{ls}** and **{ln}**. Coverage (accuracy in brackets). The two "
            "right-hand columns are an oracle upper bound — the best configuration for that "
            "one workload — and are not achievable by any single design.\n\n"
            + table(["Workload", "Stride", "N-gram", "Winner", "Oracle stride", "Oracle n-gram"], body)
            + f"\n\nThe n-gram configuration wins on {wins_n} workloads, stride on {wins_s}.\n\n"
            + "#### What lookahead buys (mean coverage)\n\n"
            + table(["", "Stride", f"N-gram (depth {ref['depth']}, {ref['slots']} slots)"], deg_rows)
            + "\n\n#### What extra slots buy (n-gram, depth "
            + f"{ref['depth']}, degree {ref['degree']})\n\n"
            + table(["Slots"] + traces, slots_rows)
            + "\n\n#### Configurations that made things worse\n\n"
            + table(["Family", "Runs with negative coverage"],
                    [[k, f"{v[0]} of {v[1]}"] for k, v in sorted(harm.items())]) + "\n")


def main():
    out = []
    for name, fn in (("shipped", section_shipped), ("latency", section_latency),
                     ("design", section_design)):
        rows = load(name)
        if rows is None:
            out.append(f"<!-- results/evaluation_{name}.csv not found -->\n")
            continue
        out.append(fn(rows))
    sys.stdout.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
