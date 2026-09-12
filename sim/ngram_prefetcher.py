"""
ngram_prefetcher.py - Semantic N-Gram delta prefetcher (behavioural model).

This model is the reference for rtl/ngram_prefetcher.sv. The two are meant to
be cycle-for-cycle equivalent in their decisions, so any change here has to be
mirrored in the RTL (and vice versa); tools/cosim_check.py proves the pair
still agree on a given trace.

Two extensions exist only in this model, for design-space evaluation, and are
off by default (degree=1, slots=1), where behaviour is identical to the RTL:

  degree  Lookahead. After predicting the next delta, feed that prediction back
          into the window and look up again, following the chain up to `degree`
          steps while each step stays confident. Issues a prefetch per step, so
          predictions reach further ahead - which matters once prefetches take
          time to arrive. Lookahead never writes to the table.

  slots   Candidate deltas per table entry. With one slot, a window followed
          alternately by two different deltas never builds confidence in either,
          because each observation retrains the other. With several, each
          follower gets its own counter, and every confident one is prefetched.
"""

from array import array

from config import (
    NGRAM_DEPTH,
    TABLE_SIZE,
    CONFIDENCE_THRESHOLD,
    BLOCK_OFFSET_BITS,
    TAG_BITS,
    CONF_MAX,
    DELTA_WIDTH,
)


class NGramPrefetcher:
    def __init__(self, depth=NGRAM_DEPTH, table_size=TABLE_SIZE,
                 conf_threshold=CONFIDENCE_THRESHOLD,
                 delta_width=DELTA_WIDTH, tag_bits=TAG_BITS,
                 degree=1, slots=1):
        """Defaults reproduce the configuration the RTL implements.

        depth, table_size, delta_width and tag_bits are overridable so the
        design-space sweeps can exercise this exact model rather than a
        re-implementation of it. Only the default configuration (degree=1,
        slots=1) is checked against the RTL by tools/cosim_check.py.
        """
        if table_size <= 0 or (table_size & (table_size - 1)) != 0:
            raise ValueError(f"table_size must be a power of two, got {table_size}")
        if depth < 1:
            raise ValueError(f"depth must be at least 1, got {depth}")
        if delta_width < 2:
            raise ValueError(f"delta_width must be at least 2, got {delta_width}")
        if degree < 1:
            raise ValueError(f"degree must be at least 1, got {degree}")
        if slots < 1:
            raise ValueError(f"slots must be at least 1, got {slots}")

        self.depth = depth
        self.table_size = table_size
        self.index_bits = table_size.bit_length() - 1
        self.conf_threshold = conf_threshold
        self.degree = degree
        self.slots = slots

        self.delta_width = delta_width
        self.tag_bits = tag_bits
        self.tag_mask = (1 << tag_bits) - 1
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

        # The table is held as flat arrays rather than one object per entry:
        # the resized tables evaluated on SPEC run to millions of entries, where
        # per-entry objects cost hundreds of megabytes. Slot s of entry e lives
        # at index e * slots + s. A slot with confidence 0 is empty.
        self.valid = bytearray(table_size)
        self.tags = array("Q", bytes(8 * table_size))
        self.deltas = array("q", bytes(8 * table_size * slots))
        self.confs = bytearray(table_size * slots)

        self.prev_block_addr = None
        self.last_lookup_idx = None
        self.last_lookup_tag = None

        # Observability: how many accesses were dropped because the block delta
        # did not fit in the DELTA_WIDTH-bit field the hardware provides.
        self.delta_overflows = 0

    # ------------------------------------------------------------ inspection --
    def entry(self, index):
        """(valid, tag, [(delta, confidence), ...]) for one table entry."""
        base = index * self.slots
        return (bool(self.valid[index]), self.tags[index],
                [(self.deltas[base + s], self.confs[base + s]) for s in range(self.slots)])

    def occupied_confidences(self):
        """Confidence of every occupied slot in every valid entry."""
        out = []
        for e in range(self.table_size):
            if self.valid[e]:
                base = e * self.slots
                out.extend(c for c in self.confs[base:base + self.slots] if c)
        return out

    # ------------------------------------------------------------------ hash --
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
        tag = (full_hash >> self.index_bits) & self.tag_mask
        return index, tag

    def _reset_history(self):
        """Drop the n-gram window; used when the delta stream is discontinuous."""
        self.history = [0] * self.depth
        self.history_valid_count = 0
        self.last_lookup_idx = None
        self.last_lookup_tag = None

    # -------------------------------------------------------------- training --
    def _train(self, index, tag, delta):
        """Update one entry with the delta that actually followed its window.

        With one slot this is exactly the rule the RTL's confidence_fsm
        implements: a confirmed prediction strengthens, a miss weakens, and an
        entry already at confidence 1 is retrained onto the new delta.

        With several slots, a delta matching any slot strengthens that slot. A
        delta matching none goes into the weakest slot if that slot is empty or
        at confidence 1; otherwise every slot is weakened, so a pattern that has
        genuinely changed still displaces stale followers eventually.
        """
        K = self.slots
        base = index * K
        confs, deltas = self.confs, self.deltas

        if not (self.valid[index] and self.tags[index] == tag):
            self.valid[index] = 1
            self.tags[index] = tag
            deltas[base] = delta
            confs[base] = 1
            for s in range(1, K):
                confs[base + s] = 0
            return

        for s in range(K):
            if confs[base + s] and deltas[base + s] == delta:
                c = confs[base + s]
                if c < CONF_MAX:
                    confs[base + s] = c + 1
                return

        weakest = base
        for s in range(1, K):
            if confs[base + s] < confs[weakest]:
                weakest = base + s

        if confs[weakest] <= 1:
            deltas[weakest] = delta
            confs[weakest] = 1
        else:
            for s in range(K):
                confs[base + s] -= 1

    # ------------------------------------------------------------ prediction --
    def _confident_slots(self, index, tag):
        """Confident (delta, confidence) slots of an entry, strongest first."""
        if not (self.valid[index] and self.tags[index] == tag):
            return []
        base = index * self.slots
        thr = self.conf_threshold
        found = [(self.confs[base + s], s, self.deltas[base + s])
                 for s in range(self.slots) if self.confs[base + s] >= thr]
        # Strongest first; equal confidence keeps slot order, so it is stable.
        found.sort(key=lambda t: (-t[0], t[1]))
        return [(d, c) for c, _, d in found]

    def _predict(self, curr_block):
        idx, tag = self._compute_hash(self.history)
        self.last_lookup_idx = idx
        self.last_lookup_tag = tag

        first = self._confident_slots(idx, tag)
        if not first:
            return []

        out = []
        seen = set()
        for delta, _ in first:
            block = curr_block + delta
            if block >= 0 and block not in seen:
                seen.add(block)
                out.append(block << BLOCK_OFFSET_BITS)

        # Lookahead follows the strongest prediction, one step at a time.
        window = list(self.history)
        block = curr_block
        delta = first[0][0]
        for _ in range(self.degree - 1):
            block += delta
            if block < 0:
                break
            window = window[1:] + [delta]
            idx, tag = self._compute_hash(window)
            nxt = self._confident_slots(idx, tag)
            if not nxt:
                break
            delta = nxt[0][0]
            target = block + delta
            if target < 0:
                break
            if target not in seen:
                seen.add(target)
                out.append(target << BLOCK_OFFSET_BITS)

        return out

    # ----------------------------------------------------------------- access --
    def access_all(self, ip, addr):
        """Observe one access; return every prefetch address it produces."""
        curr_block = addr >> BLOCK_OFFSET_BITS

        if self.prev_block_addr is None:
            self.last_lookup_idx = None
            self.last_lookup_tag = None
            self.prev_block_addr = curr_block
            return []

        curr_delta = curr_block - self.prev_block_addr

        # A delta that does not fit the hardware field is a discontinuity, not a
        # pattern. Wrapping it would poison the table with a bogus correlation,
        # so drop the window and start rebuilding from the next access.
        if not (self.delta_min <= curr_delta <= self.delta_max):
            self.delta_overflows += 1
            self._reset_history()
            self.prev_block_addr = curr_block
            return []

        # --- Train the entry that predicted this delta -----------------------
        if self.last_lookup_idx is not None:
            self._train(self.last_lookup_idx, self.last_lookup_tag, curr_delta)

        # --- Slide the window ------------------------------------------------
        for i in range(self.depth - 1):
            self.history[i] = self.history[i + 1]
        self.history[self.depth - 1] = curr_delta
        self.history_valid_count = min(self.depth, self.history_valid_count + 1)

        # --- Look up the next prediction -------------------------------------
        if self.history_valid_count >= self.depth:
            out = self._predict(curr_block)
        else:
            self.last_lookup_idx = None
            self.last_lookup_tag = None
            out = []

        self.prev_block_addr = curr_block
        return out

    def access(self, ip, addr):
        """Observe one access; return the single strongest prefetch, or None.

        This is the interface the RTL implements. With the default degree=1 and
        slots=1 it returns exactly what access_all() would.
        """
        out = self.access_all(ip, addr)
        return out[0] if out else None
