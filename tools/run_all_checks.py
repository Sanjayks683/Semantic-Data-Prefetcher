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


# Both configurations are verified. They share one RTL source and one model,
# selected by NGRAM_PROFILE / -DNGRAM_V2, so neither can silently rot.
PROFILES = ("v1", "v2")


def run(label, cmd, cwd=None, env=None):
    print(f"\n{'=' * 78}\n  {label}\n{'=' * 78}")
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, cwd=cwd, env=full_env)
    ok = proc.returncode == 0
    print(f"  -> {'PASS' if ok else 'FAIL'} ({label})")
    return ok


def have_iverilog():
    return shutil.which("iverilog") is not None and shutil.which("vvp") is not None


def main():
    py = sys.executable
    results = []

    for prof in PROFILES:
        results.append((f"parameter sync [{prof}]", run(
            f"1/4  RTL / model parameter sync  (profile {prof})",
            [py, os.path.join(TOOLS, "check_rtl_sync.py")],
            env={"NGRAM_PROFILE": prof})))

    results.append(("cache tests", run(
        "2/4a Python unit tests - cache",
        [py, "test_cache.py"], cwd=SIM)))

    for prof in PROFILES:
        results.append((f"prefetcher tests [{prof}]", run(
            f"2/4b Python unit tests - prefetchers and parsers  (profile {prof})",
            [py, "test_prefetchers.py"], cwd=SIM,
            env={"NGRAM_PROFILE": prof})))

    if not have_iverilog():
        print(f"\n{'=' * 78}")
        print("  SKIPPED: RTL testbench and co-simulation")
        print("  Icarus Verilog (iverilog / vvp) is not on PATH.")
        print(f"{'=' * 78}")
    else:
        build = os.path.join(REPO, "build")
        os.makedirs(build, exist_ok=True)

        # Both profiles are built from the same RTL, selected by -DNGRAM_V2.
        defines = {"v1": [], "v2": ["-DNGRAM_V2"]}

        for prof in PROFILES:
            out = os.path.join(build, f"tb_ngram_{prof}.out")
            compile_ok = run(
                f"3/4a Compile RTL testbench  (profile {prof})",
                ["iverilog", "-g2012"] + defines[prof] + ["-o", out] +
                [os.path.join(RTL, s) for s in RTL_SOURCES] +
                [os.path.join(RTL, "tb", "tb_ngram_prefetcher.sv")])
            results.append((f"rtl compile [{prof}]", compile_ok))

            if compile_ok:
                results.append((f"rtl testbench [{prof}]", run(
                    f"3/4b Run RTL self-checking testbench  (profile {prof})",
                    ["vvp", out])))

            results.append((f"co-simulation [{prof}]", run(
                f"4/4  RTL / model co-simulation  (profile {prof})",
                [py, os.path.join(TOOLS, "cosim_check.py")],
                env={"NGRAM_PROFILE": prof})))

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
