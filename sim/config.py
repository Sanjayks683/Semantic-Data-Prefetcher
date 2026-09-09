"""
config.py - Single source of truth for cache and prefetcher geometry.

Every derived quantity is computed here rather than written out twice, so a
change to one knob cannot silently desynchronise another. The RTL mirrors
these values in rtl/ngram_types_pkg.sv; check_rtl_sync.py verifies the two
stay in agreement.
"""

import os


def _log2_exact(value, name):
    """Return log2(value), rejecting anything that is not a positive power of two."""
    if value <= 0 or (value & (value - 1)) != 0:
        raise ValueError(f"{name} must be a positive power of two, got {value}")
    return value.bit_length() - 1


# ---------------------------------------------------------------- L1 cache --
CACHE_SETS = 64
CACHE_WAYS = 8
BLOCK_SIZE = 64

BLOCK_OFFSET_BITS = _log2_exact(BLOCK_SIZE, "BLOCK_SIZE")
SET_INDEX_BITS = _log2_exact(CACHE_SETS, "CACHE_SETS")
CACHE_CAPACITY_BYTES = CACHE_SETS * CACHE_WAYS * BLOCK_SIZE

# ------------------------------------------------------- N-Gram prefetcher --
# Two configurations are supported. Select with the NGRAM_PROFILE environment
# variable; "v1" is the default so existing results and scripts are unaffected.
#
#   v1  the design as originally built and verified: a 3-delta history window.
#   v2  the same design with a 2-delta window, which the depth sweep in
#       tools/sizing_sweep.py found to be better on every workload measured
#       (see results/depth_sweep_*.csv). Nothing else changes.
#
# The RTL mirrors this: rtl/ngram_types_pkg.sv selects the same two depths on
# `ifdef NGRAM_V2, so one RTL source builds either profile and the two cannot
# drift apart. tools/check_rtl_sync.py verifies both.

PROFILES = {
    "v1": {"NGRAM_DEPTH": 3},
    "v2": {"NGRAM_DEPTH": 2},
}

PROFILE = os.environ.get("NGRAM_PROFILE", "v1").strip().lower()
if PROFILE not in PROFILES:
    raise ValueError(
        f"NGRAM_PROFILE={PROFILE!r} is not a known profile; "
        f"expected one of {sorted(PROFILES)}"
    )

NGRAM_DEPTH = PROFILES[PROFILE]["NGRAM_DEPTH"]
TABLE_SIZE = 1024
CONFIDENCE_THRESHOLD = 2

CONF_BITS = 2
CONF_MAX = (1 << CONF_BITS) - 1

INDEX_BITS = _log2_exact(TABLE_SIZE, "TABLE_SIZE")
TAG_BITS = 6

# Width of a single delta as stored in hardware. Deltas are held as a signed
# two's-complement value of this width; anything that does not fit is treated
# as a history discontinuity rather than being silently wrapped.
DELTA_WIDTH = 16
DELTA_MIN = -(1 << (DELTA_WIDTH - 1))
DELTA_MAX = (1 << (DELTA_WIDTH - 1)) - 1
DELTA_MASK = (1 << DELTA_WIDTH) - 1

# The hash folds a DELTA_WIDTH-bit value down and then splits it into an index
# field and a tag field, so those two must fit inside one delta word.
if INDEX_BITS + TAG_BITS > DELTA_WIDTH:
    raise ValueError(
        f"INDEX_BITS ({INDEX_BITS}) + TAG_BITS ({TAG_BITS}) exceeds "
        f"DELTA_WIDTH ({DELTA_WIDTH}); the hash has no room for both fields"
    )

if CONFIDENCE_THRESHOLD > CONF_MAX:
    raise ValueError(
        f"CONFIDENCE_THRESHOLD ({CONFIDENCE_THRESHOLD}) is unreachable with "
        f"CONF_BITS ({CONF_BITS}); the prefetcher would never fire"
    )

# Per-tap rotation amounts for the folded XOR hash, one per n-gram position.
# Tap i is rotated left by ROTATES[i] so that identical deltas at different
# positions do not cancel each other out in the XOR.
ROTATES = tuple((i * 4 - 1) % DELTA_WIDTH if i > 0 else 0 for i in range(NGRAM_DEPTH))

# ------------------------------------------------------ Stride prefetcher ---
STRIDE_TABLE_SIZE = 256
STRIDE_CONF_THRESHOLD = 2
STRIDE_INDEX_BITS = _log2_exact(STRIDE_TABLE_SIZE, "STRIDE_TABLE_SIZE")
