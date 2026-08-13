"""
test_cache.py - Unit test for L1 cache simulator.
"""

from cache import Cache


def run_tests():
    c = Cache()
    base_addr = 0x10000

    # 1. Cold miss
    hit, useful = c.access(base_addr)
    assert not hit, "First access should be a cold miss"
    c.insert(base_addr)

    # 2. Immediate hit
    hit, useful = c.access(base_addr)
    assert hit, "Second access should be a cache hit"

    # 3. Test Prefetch tracking
    prefetch_addr = 0x20000
    c.insert(prefetch_addr, is_prefetch=True)
    hit, useful = c.access(prefetch_addr)
    assert hit and useful, "Demand hit on prefetched line should be marked as useful"

    # Second hit on the same prefetched line shouldn't be double-counted as newly useful
    hit, useful = c.access(prefetch_addr)
    assert hit and not useful, "Second hit on already used prefetch should not report useful again"

    # 4. Fill an entire set (8 ways) and test LRU eviction
    # All these addresses map to the exact same set because step is CACHE_SETS * BLOCK_SIZE = 64 * 64 = 4096 (0x1000)
    stride_same_set = 64 * 64
    set_test_base = 0x30000

    for i in range(8):
        addr = set_test_base + i * stride_same_set
        c.insert(addr)

    # Access all 8 to make sure they are in cache
    for i in range(8):
        addr = set_test_base + i * stride_same_set
        hit, _ = c.access(addr)
        assert hit, f"Way {i} should be present"

    # Insert a 9th address to force eviction of the least recently used
    # Access ways 1..7 so way 0 is the oldest (least recently used)
    for i in range(1, 8):
        c.access(set_test_base + i * stride_same_set)

    addr_9th = set_test_base + 8 * stride_same_set
    evicted = c.insert(addr_9th)
    assert evicted, "Inserting 9th block into 8-way set must cause an eviction"

    # Way 0 should now be evicted (miss)
    hit_0, _ = c.access(set_test_base + 0 * stride_same_set)
    assert not hit_0, "Way 0 should have been evicted by LRU policy"

    print("[SUCCESS] All Cache Unit Tests Passed!")


if __name__ == "__main__":
    run_tests()
