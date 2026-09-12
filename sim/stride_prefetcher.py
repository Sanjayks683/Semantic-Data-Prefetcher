"""
stride_prefetcher.py - Classic IP-indexed stride prefetcher (baseline).

Kept deliberately simple: one entry per instruction pointer, a single stride,
and a saturating confidence counter. This is the thing the N-Gram design is
measured against, so it gets a fair implementation rather than a straw man.
"""

from config import (
    STRIDE_TABLE_SIZE,
    STRIDE_CONF_THRESHOLD,
    BLOCK_OFFSET_BITS,
    CONF_MAX,
)


class StrideEntry:
    def __init__(self):
        self.valid = False
        self.last_addr = 0
        self.stride = 0
        self.confidence = 0


class StridePrefetcher:
    def __init__(self, table_size=STRIDE_TABLE_SIZE, conf_threshold=STRIDE_CONF_THRESHOLD):
        if table_size <= 0 or (table_size & (table_size - 1)) != 0:
            raise ValueError(f"table_size must be a power of two, got {table_size}")

        self.table_size = table_size
        self.conf_threshold = conf_threshold
        self.table = [StrideEntry() for _ in range(table_size)]

    def _get_index(self, ip):
        return (ip >> 2) & (self.table_size - 1)

    def access(self, ip, addr):
        entry = self.table[self._get_index(ip)]
        prefetch_candidate = None

        # An explicit valid bit, rather than treating address 0 as "empty" —
        # address 0 is a legal (if unusual) access and must not be mistaken for
        # an unused table entry.
        if entry.valid:
            current_stride = addr - entry.last_addr

            if current_stride == entry.stride and entry.stride != 0:
                entry.confidence = min(CONF_MAX, entry.confidence + 1)
            else:
                if entry.confidence > 1:
                    entry.confidence -= 1
                else:
                    entry.stride = current_stride
                    entry.confidence = 1

            if entry.confidence >= self.conf_threshold:
                candidate = addr + entry.stride
                if candidate >= 0:
                    prefetch_candidate = (candidate >> BLOCK_OFFSET_BITS) << BLOCK_OFFSET_BITS

        entry.last_addr = addr
        entry.valid = True

        return prefetch_candidate
