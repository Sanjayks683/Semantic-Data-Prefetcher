#!/usr/bin/env python3
"""
cosim_check.py - Prove the RTL and the Python model make identical decisions.

For each trace, the Python NGramPrefetcher and the SystemVerilog DUT are driven
with the same address stream and their prefetch outputs are compared access by
access. Any difference in *when* a prefetch fires or *what* address it targets
is reported with the access index, so a divergence is actionable rather than
showing up later as an unexplained gap in coverage.

Usage:
    python tools/cosim_check.py                 # all traces in traces/
    python tools/cosim_check.py streaming.trace # one trace

Requires Icarus Verilog (iverilog / vvp) on PATH.
"""

import os
import sys
import shutil
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_DIR = os.path.join(REPO, "sim")
RTL_DIR = os.path.join(REPO, "rtl")
TRACES_DIR = os.path.join(REPO, "traces")

sys.path.insert(0, SIM_DIR)

from ngram_prefetcher import NGramPrefetcher          # noqa: E402
from trace_parser import get_trace_iterator           # noqa: E402

RTL_SOURCES = [
    "ngram_types_pkg.sv",
    "delta_generator.sv",
    "history_shift_reg.sv",
    "xor_hash.sv",
    "sram_table.sv",
    "confidence_fsm.sv",
    "ngram_prefetcher.sv",
    os.path.join("tb", "tb_cosim.sv"),
]

MAX_REPORTED = 10


def require_tools():
    missing = [t for t in ("iverilog", "vvp") if shutil.which(t) is None]
    if missing:
        print(f"error: {', '.join(missing)} not found on PATH.")
        print("Install Icarus Verilog to run the co-simulation check.")
        sys.exit(2)


def build(workdir):
    out = os.path.join(workdir, "cosim.out")
    cmd = ["iverilog", "-g2012", "-o", out] + [
        os.path.join(RTL_DIR, s) for s in RTL_SOURCES
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("error: RTL compilation failed")
        print(proc.stdout + proc.stderr)
        sys.exit(2)
    return out


def run_model(trace_path):
    """Return {access_index: prefetch_address} for the Python model."""
    p = NGramPrefetcher()
    out = {}
    for i, (ip, addr) in enumerate(get_trace_iterator(trace_path)):
        pf = p.access(ip, addr)
        if pf is not None:
            out[i] = pf
    return out, p


def run_rtl(binary, trace_path, workdir):
    """Return {access_index: prefetch_address} for the SystemVerilog DUT."""
    addr_file = os.path.join(workdir, "addrs.txt")
    with open(addr_file, "w") as f:
        for _, addr in get_trace_iterator(trace_path):
            f.write(f"{addr:x}\n")

    out_file = os.path.join(workdir, "rtl_out.txt")
    proc = subprocess.run(
        ["vvp", binary, f"+trace={addr_file}", f"+out={out_file}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print("error: RTL simulation failed")
        print(proc.stdout + proc.stderr)
        sys.exit(2)

    out = {}
    with open(out_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            idx, addr = line.split()
            out[int(idx)] = int(addr, 16)
    return out


def compare(name, model, rtl, accesses):
    only_model = sorted(set(model) - set(rtl))
    only_rtl = sorted(set(rtl) - set(model))
    mismatched = sorted(i for i in set(model) & set(rtl) if model[i] != rtl[i])

    total = len(only_model) + len(only_rtl) + len(mismatched)
    agree = accesses - total

    status = "MATCH" if total == 0 else "DIVERGED"
    print(f"  {name:<24} {accesses:>6} accesses  "
          f"model={len(model):<6} rtl={len(rtl):<6}  "
          f"agree={agree}/{accesses}  [{status}]")

    if total:
        for i in only_model[:MAX_REPORTED]:
            print(f"      access {i}: model prefetched 0x{model[i]:x}, RTL did not")
        for i in only_rtl[:MAX_REPORTED]:
            print(f"      access {i}: RTL prefetched 0x{rtl[i]:x}, model did not")
        for i in mismatched[:MAX_REPORTED]:
            print(f"      access {i}: model 0x{model[i]:x} != RTL 0x{rtl[i]:x}")
        if total > MAX_REPORTED:
            print(f"      ... {total} divergences in total")

    return total == 0


def main(argv):
    require_tools()

    if not os.path.isdir(TRACES_DIR):
        print(f"error: no traces directory at {TRACES_DIR}")
        return 2

    if argv:
        traces = argv
    else:
        traces = sorted(
            f for f in os.listdir(TRACES_DIR)
            if f.endswith(".trace") or f.endswith(".xz")
        )

    if not traces:
        print("error: no traces found. Run 'python sim/generate_trace.py' first.")
        return 2

    print("\nRTL / model co-simulation")
    print("=" * 78)

    all_ok = True
    with tempfile.TemporaryDirectory() as workdir:
        binary = build(workdir)

        for t in traces:
            path = t if os.path.isabs(t) else os.path.join(TRACES_DIR, t)
            if not os.path.exists(path):
                print(f"  {t}: not found")
                all_ok = False
                continue

            accesses = sum(1 for _ in get_trace_iterator(path))
            model, model_obj = run_model(path)
            rtl = run_rtl(binary, path, workdir)

            ok = compare(os.path.basename(t), model, rtl, accesses)
            if model_obj.delta_overflows:
                print(f"      note: {model_obj.delta_overflows} delta overflow(s) "
                      f"on this trace")
            all_ok &= ok

    print("=" * 78)
    if all_ok:
        print("RESULT: RTL and model agree on every access.\n")
        return 0

    print("RESULT: divergence detected.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
