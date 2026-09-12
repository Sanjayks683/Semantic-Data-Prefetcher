"""
cache.py - Set-associative cache model with true LRU replacement.

Geometry defaults to the L1 configuration in config.py, but is parameterised so
a second level can be instantiated alongside it (see tools/run_champsim.py
--level l2). Tracks per-line prefetch provenance so the metrics layer can tell a
demand hit on a prefetched line (a useful prefetch) from an ordinary hit.
"""

from config import CACHE_SETS, CACHE_WAYS, BLOCK_SIZE, _log2_exact


class CacheLine:
    def __init__(self):
        self.valid = False
        self.tag = 0
        self.lru_counter = 0
        self.is_prefetched = False
        self.was_useful = False


class Cache:
    def __init__(self, sets=CACHE_SETS, ways=CACHE_WAYS, block_size=BLOCK_SIZE):
        self.num_sets = sets
        self.num_ways = ways
        self.block_size = block_size

        self.block_offset_bits = _log2_exact(block_size, "block_size")
        self.set_index_bits = _log2_exact(sets, "sets")

        if ways <= 0:
            raise ValueError(f"ways must be positive, got {ways}")

        self.capacity_bytes = sets * ways * block_size

        self.sets = [[CacheLine() for _ in range(ways)] for _ in range(sets)]
        self.global_lru = 0
        # Prefetched lines evicted before any demand access ever used them.
        self.dead_prefetch_evictions = 0

    def _decompose_address(self, addr):
        block_addr = addr >> self.block_offset_bits
        set_idx = block_addr & (self.num_sets - 1)
        tag = block_addr >> self.set_index_bits
        return set_idx, tag

    def access(self, addr, is_prefetch=False):
        set_idx, tag = self._decompose_address(addr)
        target_set = self.sets[set_idx]
        self.global_lru += 1

        for way in target_set:
            if way.valid and way.tag == tag:
                way.lru_counter = self.global_lru
                is_useful = False
                if not is_prefetch and way.is_prefetched and not way.was_useful:
                    way.was_useful = True
                    is_useful = True
                return True, is_useful

        return False, False

    def insert(self, addr, is_prefetch=False):
        set_idx, tag = self._decompose_address(addr)
        target_set = self.sets[set_idx]
        self.global_lru += 1

        for way in target_set:
            if way.valid and way.tag == tag:
                way.lru_counter = self.global_lru
                return False

        victim_way = target_set[0]
        empty_way = None

        for way in target_set:
            if not way.valid:
                empty_way = way
                break
            if way.lru_counter < victim_way.lru_counter:
                victim_way = way

        target_way = empty_way if empty_way is not None else victim_way
        evicted = target_way.valid

        if evicted and target_way.is_prefetched and not target_way.was_useful:
            self.dead_prefetch_evictions += 1

        target_way.valid = True
        target_way.tag = tag
        target_way.lru_counter = self.global_lru
        target_way.is_prefetched = is_prefetch
        target_way.was_useful = False

        return evicted
