from config import CACHE_SETS, CACHE_WAYS, BLOCK_OFFSET_BITS, SET_INDEX_BITS


class CacheLine:
    def __init__(self):
        self.valid = False
        self.tag = 0
        self.lru_counter = 0
        self.is_prefetched = False
        self.was_useful = False


class Cache:
    def __init__(self):
        self.sets = [[CacheLine() for _ in range(CACHE_WAYS)] for _ in range(CACHE_SETS)]
        self.global_lru = 0

    def _decompose_address(self, addr):
        block_addr = addr >> BLOCK_OFFSET_BITS
        set_idx = block_addr & (CACHE_SETS - 1)
        tag = block_addr >> SET_INDEX_BITS
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

        target_way.valid = True
        target_way.tag = tag
        target_way.lru_counter = self.global_lru
        target_way.is_prefetched = is_prefetch
        target_way.was_useful = False

        return evicted
