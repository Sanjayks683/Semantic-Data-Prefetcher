#!/usr/bin/env python3
"""
check_rtl_sync.py - Verify the RTL package and the Python config agree.

The behavioural model and the RTL each declare the prefetcher's geometry. They
have to hold the same values or the co-simulation check is comparing two
different designs. This script parses rtl/ngram_types_pkg.sv and diffs the
parameters against sim/config.py.

Usage:
    python tools/check_rtl_sync.py
"""

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_PATH = os.path.join(REPO, "rtl", "ngram_types_pkg.sv")

sys.path.insert(0, os.path.join(REPO, "sim"))
import config  # noqa: E402

# RTL localparam name -> Python config attribute name.
PARAM_MAP = {
    "BLOCK_SIZE_BYTES": "BLOCK_SIZE",
    "BLOCK_OFFSET_BITS": "BLOCK_OFFSET_BITS",
    "DELTA_WIDTH": "DELTA_WIDTH",
    "NGRAM_DEPTH": "NGRAM_DEPTH",
    "TABLE_ENTRIES": "TABLE_SIZE",
    "INDEX_BITS": "INDEX_BITS",
    "TAG_BITS": "TAG_BITS",
    "CONF_BITS": "CONF_BITS",
    "CONF_MAX": "CONF_MAX",
    "CONF_THRESHOLD": "CONFIDENCE_THRESHOLD",
}

LOCALPARAM_RE = re.compile(
    r"localparam\s+(?:int|longint)\s+(\w+)\s*=\s*([^;]+);"
)


def parse_package(path):
    """Extract integer localparams, resolving $clog2 and references to earlier ones."""
    with open(path) as f:
        text = f.read()

    values = {}
    for name, expr in LOCALPARAM_RE.findall(text):
        expr = expr.strip()

        # Resolve $clog2(<expr>) using the values already parsed.
        def clog2(match):
            inner = eval(match.group(1), {"__builtins__": {}}, dict(values))
            return str(max(0, (int(inner) - 1).bit_length()))

        expr = re.sub(r"\$clog2\(([^()]*)\)", clog2, expr)

        # Drop SystemVerilog sized-literal syntax (64'sd1 -> 1) and shifts.
        expr = re.sub(r"\d+'s?[dhb]", "", expr)
        expr = expr.replace("<<<", "<<").replace(">>>", ">>")

        try:
            values[name] = int(eval(expr, {"__builtins__": {}}, dict(values)))
        except Exception:
            # Non-integer parameters (types, enums) are not compared here.
            continue

    return values


def main():
    if not os.path.exists(PKG_PATH):
        print(f"error: {PKG_PATH} not found")
        return 2

    rtl = parse_package(PKG_PATH)

    print("\nRTL / model parameter sync")
    print("=" * 66)
    print(f"  {'parameter':<22} {'RTL':>10} {'model':>10}   status")
    print("-" * 66)

    failures = 0
    for rtl_name, py_name in PARAM_MAP.items():
        if rtl_name not in rtl:
            print(f"  {rtl_name:<22} {'?':>10} {'':>10}   NOT FOUND in package")
            failures += 1
            continue

        rtl_val = rtl[rtl_name]
        py_val = getattr(config, py_name, None)

        if py_val is None:
            print(f"  {rtl_name:<22} {rtl_val:>10} {'?':>10}   missing in config.py")
            failures += 1
            continue

        ok = rtl_val == py_val
        failures += not ok
        print(f"  {rtl_name:<22} {rtl_val:>10} {py_val:>10}   "
              f"{'ok' if ok else 'MISMATCH'}")

    # Structural invariants that must hold on both sides.
    print("-" * 66)
    checks = [
        ("INDEX_BITS + TAG_BITS <= DELTA_WIDTH",
         config.INDEX_BITS + config.TAG_BITS <= config.DELTA_WIDTH),
        ("TABLE_SIZE == 2 ** INDEX_BITS",
         config.TABLE_SIZE == 2 ** config.INDEX_BITS),
        ("CONFIDENCE_THRESHOLD <= CONF_MAX",
         config.CONFIDENCE_THRESHOLD <= config.CONF_MAX),
        ("BLOCK_SIZE == 2 ** BLOCK_OFFSET_BITS",
         config.BLOCK_SIZE == 2 ** config.BLOCK_OFFSET_BITS),
        ("CACHE_SETS == 2 ** SET_INDEX_BITS",
         config.CACHE_SETS == 2 ** config.SET_INDEX_BITS),
    ]
    for desc, ok in checks:
        failures += not ok
        print(f"  {desc:<45} {'ok' if ok else 'FAILED'}")

    entry_bits = 1 + config.TAG_BITS + config.DELTA_WIDTH + config.CONF_BITS
    total_bits = entry_bits * config.TABLE_SIZE
    print("-" * 66)
    print(f"  table entry width : {entry_bits} bits "
          f"(valid + {config.TAG_BITS} tag + {config.DELTA_WIDTH} delta "
          f"+ {config.CONF_BITS} conf)")
    print(f"  total storage     : {total_bits} bits "
          f"= {total_bits / 8 / 1024:.2f} KB")
    print("=" * 66)

    if failures:
        print(f"RESULT: {failures} problem(s) found.\n")
        return 1

    print("RESULT: RTL and model parameters are in sync.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
