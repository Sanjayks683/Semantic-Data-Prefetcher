#!/usr/bin/env python3
"""
run_all_checks.py - Run every check in the project and report a single verdict.

    python tools/run_all_checks.py

Steps, in order:
    1. parameter sync   - RTL package and Python config declare the same geometry
    2. python unit tests - cache, both prefetchers, both trace parsers
    3. RTL testbench     - self-checking, 65 assertions across 7 phases
    4. co-simulation     - RTL and model agree access-for-access on every trace

Steps needing Icarus Verilog are skipped with a warning if it is not installed;
everything else still runs.
"""

import os
import sys
import shutil
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(REPO, "sim")
RTL = os.path.join(REPO, "rtl")
TOOLS = os.path.join(REPO, "tools")

RTL_SOURCES = [
    "ngram_types_pkg.sv", "delta_generator.sv", "history_shift_reg.sv",
    "xor_hash.sv", "sram_table.sv", "confidence_fsm.sv", "ngram_prefetcher.sv",
]


def run(label, cmd, cwd=None):
    print(f"\n{'=' * 78}\n  {label}\n{'=' * 78}")
    proc = subprocess.run(cmd, cwd=cwd)
    ok = proc.returncode == 0
    print(f"  -> {'PASS' if ok else 'FAIL'} ({label})")
    return ok


def have_iverilog():
    return shutil.which("iverilog") is not None and shutil.which("vvp") is not None


def main():
    py = sys.executable
    results = []

    results.append(("parameter sync", run(
        "1/4  RTL / model parameter sync",
        [py, os.path.join(TOOLS, "check_rtl_sync.py")])))

    results.append(("cache tests", run(
        "2/4a Python unit tests - cache",
        [py, "test_cache.py"], cwd=SIM)))

    results.append(("prefetcher tests", run(
        "2/4b Python unit tests - prefetchers and parsers",
        [py, "test_prefetchers.py"], cwd=SIM)))

    if not have_iverilog():
        print(f"\n{'=' * 78}")
        print("  SKIPPED: RTL testbench and co-simulation")
        print("  Icarus Verilog (iverilog / vvp) is not on PATH.")
        print(f"{'=' * 78}")
    else:
        build = os.path.join(REPO, "build")
        os.makedirs(build, exist_ok=True)
        out = os.path.join(build, "tb_ngram.out")

        compile_ok = run(
            "3/4a Compile RTL testbench",
            ["iverilog", "-g2012", "-o", out] +
            [os.path.join(RTL, s) for s in RTL_SOURCES] +
            [os.path.join(RTL, "tb", "tb_ngram_prefetcher.sv")])
        results.append(("rtl compile", compile_ok))

        if compile_ok:
            results.append(("rtl testbench", run(
                "3/4b Run RTL self-checking testbench", ["vvp", out])))

        results.append(("co-simulation", run(
            "4/4  RTL / model co-simulation",
            [py, os.path.join(TOOLS, "cosim_check.py")])))

    print(f"\n{'=' * 78}\n  SUMMARY\n{'=' * 78}")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    failed = [n for n, ok in results if not ok]
    print(f"{'=' * 78}")
    if failed:
        print(f"RESULT: {len(failed)} step(s) failed: {', '.join(failed)}\n")
        return 1
    print(f"RESULT: all {len(results)} steps passed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
