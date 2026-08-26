
from cache import Cache
from config import CACHE_SETS, CACHE_WAYS, BLOCK_SIZE

SAME_SET_STRIDE = CACHE_SETS * BLOCK_SIZE

def test_cold_miss_then_hit():
    c = Cache()
    addr = 0x10000

    hit, _ = c.access(addr)
    assert not hit, "first access must be a cold miss"

    c.insert(addr)
    hit, _ = c.access(addr)
    assert hit, "access after insert must hit"

def test_prefetch_usefulness_counted_once():
    c = Cache()
    addr = 0x20000

    c.insert(addr, is_prefetch=True)

    hit, useful = c.access(addr)
    assert hit and useful, "demand hit on a prefetched line must report useful"

    hit, useful = c.access(addr)
    assert hit and not useful, "usefulness must not be double counted"

def test_prefetch_probe_does_not_mark_useful():
    c = Cache()
    addr = 0x21000

    c.insert(addr, is_prefetch=True)
    hit, useful = c.access(addr, is_prefetch=True)
    assert hit and not useful, "a prefetch probe is not a demand use"

def test_lru_eviction_order():
    c = Cache()
    base = 0x30000

    for i in range(CACHE_WAYS):
        c.insert(base + i * SAME_SET_STRIDE)

    for i in range(CACHE_WAYS):
        hit, _ = c.access(base + i * SAME_SET_STRIDE)
        assert hit, f"way {i} should still be resident"

    for i in range(1, CACHE_WAYS):
        c.access(base + i * SAME_SET_STRIDE)

    evicted = c.insert(base + CACHE_WAYS * SAME_SET_STRIDE)
    assert evicted, "filling a full set must evict"

    hit, _ = c.access(base + 0 * SAME_SET_STRIDE)
    assert not hit, "the least recently used way must be the victim"

    for i in range(1, CACHE_WAYS):
        hit, _ = c.access(base + i * SAME_SET_STRIDE)
        assert hit, f"way {i} must survive the eviction"

def test_set_isolation():
    c = Cache()
    base = 0x40000

    for i in range(CACHE_WAYS + 4):
        c.insert(base + i * SAME_SET_STRIDE)

    other_set = base + BLOCK_SIZE
    c.insert(other_set)
    for i in range(CACHE_WAYS + 4):
        c.insert(base + i * SAME_SET_STRIDE)

    hit, _ = c.access(other_set)
    assert hit, "pressure on one set must not evict a line from another"

def test_dead_prefetch_accounting():
    c = Cache()
    base = 0x50000

    c.insert(base, is_prefetch=True)
    for i in range(1, CACHE_WAYS + 1):
        c.insert(base + i * SAME_SET_STRIDE)

    assert c.dead_prefetch_evictions == 1, (
        f"an unused prefetch evicted from a full set must be counted as dead, "
        f"got {c.dead_prefetch_evictions}"
    )

def test_insert_is_idempotent():
    c = Cache()
    addr = 0x60000

    assert c.insert(addr) is False, "inserting into an empty set evicts nothing"
    assert c.insert(addr) is False, "re-inserting a resident line evicts nothing"

def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  [ok] {t.__name__}")
    print(f"[SUCCESS] {len(tests)} cache tests passed "
          f"({CACHE_SETS} sets x {CACHE_WAYS} ways x {BLOCK_SIZE} B)")

if __name__ == "__main__":
    run_tests()
