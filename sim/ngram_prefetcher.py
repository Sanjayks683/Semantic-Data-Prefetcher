"""
ngram_prefetcher.py - Semantic N-Gram delta prefetcher (behavioural model).

This model is the reference for rtl/ngram_prefetcher.sv. The two are meant to
be cycle-for-cycle equivalent in their decisions, so any change here has to be
mirrored in the RTL (and vice versa); tools/cosim_check.py proves the pair
still agree on a given trace.
"""

from config import (
    NGRAM_DEPTH,
    TABLE_SIZE,
    CONFIDENCE_THRESHOLD,
    BLOCK_OFFSET_BITS,
    INDEX_BITS,
    TAG_BITS,
    CONF_MAX,
    DELTA_WIDTH,
    DELTA_MIN,
    DELTA_MAX,
    DELTA_MASK,
    ROTATES,
)


class TableEntry:
    def __init__(self):
        self.valid = False
        self.tag = 0
        self.predicted_delta = 0
        self.confidence = 0


class NGramPrefetcher:
    def __init__(self, depth=NGRAM_DEPTH, table_size=TABLE_SIZE,
                 conf_threshold=CONFIDENCE_THRESHOLD,
                 delta_width=DELTA_WIDTH, tag_bits=TAG_BITS):
        """Defaults reproduce the configuration the RTL implements.

        depth, table_size, delta_width and tag_bits are overridable so the
        design-space sweep in tools/sizing_sweep.py can exercise this exact
        model rather than a re-implementation of it. Only the default
        configuration is checked against the RTL by tools/cosim_check.py.
        """
        if table_size <= 0 or (table_size & (table_size - 1)) != 0:
            raise ValueError(f"table_size must be a power of two, got {table_size}")
        if depth < 1:
            raise ValueError(f"depth must be at least 1, got {depth}")
        if delta_width < 2:
            raise ValueError(f"delta_width must be at least 2, got {delta_width}")

        self.depth = depth
        self.table_size = table_size
        self.index_bits = table_size.bit_length() - 1
        self.conf_threshold = conf_threshold

        self.delta_width = delta_width
        self.tag_bits = tag_bits
        self.delta_mask = (1 << delta_width) - 1
        self.delta_min = -(1 << (delta_width - 1))
        self.delta_max = (1 << (delta_width - 1)) - 1

        if self.index_bits + tag_bits > delta_width:
            raise ValueError(
                f"index_bits ({self.index_bits}) + tag_bits ({tag_bits}) exceeds "
                f"delta_width ({delta_width}); the hash has no room for both"
            )

        # Same schedule config.ROTATES uses, evaluated for this instance.
        self.rotates = tuple(
            (i * 4 - 1) % delta_width if i > 0 else 0 for i in range(depth)
        )

        self.history = [0] * depth
        self.history_valid_count = 0
        self.table = [TableEntry() for _ in range(table_size)]

        self.prev_block_addr = None
        self.last_lookup_idx = None
        self.last_lookup_tag = None

        # Observability: how many accesses were dropped because the block delta
        # did not fit in the DELTA_WIDTH-bit field the hardware provides.
        self.delta_overflows = 0

    def _rotate_left(self, value, amount):
        """Rotate a delta_width-bit value left by `amount`, matching the RTL concat."""
        width = self.delta_width
        value &= self.delta_mask
        amount %= width
        if amount == 0:
            return value
        return ((value << amount) | (value >> (width - amount))) & self.delta_mask

    def _compute_hash(self, history_window):
        """Fold the n-gram window into an (index, tag) pair.

        Each tap is rotated by a distinct amount before the XOR so that a window
        of identical deltas does not collapse to zero. Mirrors rtl/xor_hash.sv.
        """
        full_hash = 0
        for tap, rot in zip(history_window, self.rotates):
            full_hash ^= self._rotate_left(tap & self.delta_mask, rot)

        index = full_hash & (self.table_size - 1)
        tag = (full_hash >> self.index_bits) & ((1 << self.tag_bits) - 1)
        return index, tag

    def _reset_history(self):
        """Drop the n-gram window; used when the delta stream is discontinuous."""
        self.history = [0] * self.depth
        self.history_valid_count = 0
        self.last_lookup_idx = None
        self.last_lookup_tag = None

    def access(self, ip, addr):
        curr_block = addr >> BLOCK_OFFSET_BITS
        prefetch_candidate = None

        if self.prev_block_addr is None:
            self.last_lookup_idx = None
            self.last_lookup_tag = None
            self.prev_block_addr = curr_block
            return None

        curr_delta = curr_block - self.prev_block_addr

        # A delta that does not fit the hardware field is a discontinuity, not a
        # pattern. Wrapping it would poison the table with a bogus correlation,
        # so drop the window and start rebuilding from the next access.
        if not (self.delta_min <= curr_delta <= self.delta_max):
            self.delta_overflows += 1
            self._reset_history()
            self.prev_block_addr = curr_block
            return None

        # --- Train the entry that predicted this delta -----------------------
        if self.last_lookup_idx is not None:
            entry = self.table[self.last_lookup_idx]

            if entry.valid and entry.tag == self.last_lookup_tag:
                if entry.predicted_delta == curr_delta:
                    entry.confidence = min(CONF_MAX, entry.confidence + 1)
                else:
                    if entry.confidence > 1:
                        entry.confidence -= 1
                    else:
                        entry.predicted_delta = curr_delta
                        entry.confidence = 1
            else:
                entry.valid = True
                entry.tag = self.last_lookup_tag
                entry.predicted_delta = curr_delta
                entry.confidence = 1

        # --- Slide the window ------------------------------------------------
        for i in range(self.depth - 1):
            self.history[i] = self.history[i + 1]
        self.history[self.depth - 1] = curr_delta
        self.history_valid_count = min(self.depth, self.history_valid_count + 1)

        # --- Look up the next prediction -------------------------------------
        if self.history_valid_count >= self.depth:
            idx, tag = self._compute_hash(self.history)
            self.last_lookup_idx = idx
            self.last_lookup_tag = tag

            entry = self.table[idx]
            if entry.valid and entry.tag == tag and entry.confidence >= self.conf_threshold:
                pred_block = curr_block + entry.predicted_delta
                if pred_block >= 0:
                    prefetch_candidate = pred_block << BLOCK_OFFSET_BITS
        else:
            self.last_lookup_idx = None
            self.last_lookup_tag = None

        self.prev_block_addr = curr_block
        return prefetch_candidate
