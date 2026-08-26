
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

        self.rotates = tuple(
            (i * 4 - 1) % delta_width if i > 0 else 0 for i in range(depth)
        )

        self.history = [0] * depth
        self.history_valid_count = 0

        self.valid = bytearray(table_size)
        self.tags = array("Q", bytes(8 * table_size))
        self.deltas = array("q", bytes(8 * table_size * slots))
        self.confs = bytearray(table_size * slots)

        self.prev_block_addr = None
        self.last_lookup_idx = None
        self.last_lookup_tag = None

        self.delta_overflows = 0

    def entry(self, index):
        base = index * self.slots
        return (bool(self.valid[index]), self.tags[index],
                [(self.deltas[base + s], self.confs[base + s]) for s in range(self.slots)])

    def occupied_confidences(self):
        out = []
        for e in range(self.table_size):
            if self.valid[e]:
                base = e * self.slots
                out.extend(c for c in self.confs[base:base + self.slots] if c)
        return out

    def _rotate_left(self, value, amount):
        width = self.delta_width
        value &= self.delta_mask
        amount %= width
        if amount == 0:
            return value
        return ((value << amount) | (value >> (width - amount))) & self.delta_mask

    def _compute_hash(self, history_window):
        full_hash = 0
        for tap, rot in zip(history_window, self.rotates):
            full_hash ^= self._rotate_left(tap & self.delta_mask, rot)

        index = full_hash & (self.table_size - 1)
        tag = (full_hash >> self.index_bits) & self.tag_mask
        return index, tag

    def _reset_history(self):
        self.history = [0] * self.depth
        self.history_valid_count = 0
        self.last_lookup_idx = None
        self.last_lookup_tag = None

    def _train(self, index, tag, delta):
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

    def _confident_slots(self, index, tag):
        if not (self.valid[index] and self.tags[index] == tag):
            return []
        base = index * self.slots
        thr = self.conf_threshold
        found = [(self.confs[base + s], s, self.deltas[base + s])
                 for s in range(self.slots) if self.confs[base + s] >= thr]

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

    def access_all(self, ip, addr):
        curr_block = addr >> BLOCK_OFFSET_BITS

        if self.prev_block_addr is None:
            self.last_lookup_idx = None
            self.last_lookup_tag = None
            self.prev_block_addr = curr_block
            return []

        curr_delta = curr_block - self.prev_block_addr

        if not (self.delta_min <= curr_delta <= self.delta_max):
            self.delta_overflows += 1
            self._reset_history()
            self.prev_block_addr = curr_block
            return []

        if self.last_lookup_idx is not None:
            self._train(self.last_lookup_idx, self.last_lookup_tag, curr_delta)

        for i in range(self.depth - 1):
            self.history[i] = self.history[i + 1]
        self.history[self.depth - 1] = curr_delta
        self.history_valid_count = min(self.depth, self.history_valid_count + 1)

        if self.history_valid_count >= self.depth:
            out = self._predict(curr_block)
        else:
            self.last_lookup_idx = None
            self.last_lookup_tag = None
            out = []

        self.prev_block_addr = curr_block
        return out

    def access(self, ip, addr):
        out = self.access_all(ip, addr)
        return out[0] if out else None
