
import os

def _log2_exact(value, name):
    if value <= 0 or (value & (value - 1)) != 0:
        raise ValueError(f"{name} must be a positive power of two, got {value}")
    return value.bit_length() - 1

CACHE_SETS = 64
CACHE_WAYS = 8
BLOCK_SIZE = 64

BLOCK_OFFSET_BITS = _log2_exact(BLOCK_SIZE, "BLOCK_SIZE")
SET_INDEX_BITS = _log2_exact(CACHE_SETS, "CACHE_SETS")
CACHE_CAPACITY_BYTES = CACHE_SETS * CACHE_WAYS * BLOCK_SIZE

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

DELTA_WIDTH = 16
DELTA_MIN = -(1 << (DELTA_WIDTH - 1))
DELTA_MAX = (1 << (DELTA_WIDTH - 1)) - 1
DELTA_MASK = (1 << DELTA_WIDTH) - 1

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

ROTATES = tuple((i * 4 - 1) % DELTA_WIDTH if i > 0 else 0 for i in range(NGRAM_DEPTH))

STRIDE_TABLE_SIZE = 256
STRIDE_CONF_THRESHOLD = 2
STRIDE_INDEX_BITS = _log2_exact(STRIDE_TABLE_SIZE, "STRIDE_TABLE_SIZE")
