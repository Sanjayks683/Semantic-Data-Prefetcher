
from simulator import simulate
from config import BLOCK_SIZE

IP = 0x400100

class Scripted:

    def __init__(self, plan):
        self.plan = plan
        self.n = 0

    def access_all(self, ip, addr):
        out = self.plan.get(self.n, [])
        self.n += 1
        return list(out)

class SingleOutput:

    def __init__(self, target):
        self.target = target
        self.fired = False

    def access(self, ip, addr):
        if self.fired:
            return None
        self.fired = True
        return self.target

def blocks(*indices):
    return [(IP, (1000 + i) * BLOCK_SIZE) for i in indices]

TARGET = 5000 * BLOCK_SIZE

def stream_with_target_at(position, length):
    s = blocks(*range(length))
    s[position] = (IP, TARGET)
    return s

def test_zero_latency_usable_on_next_access():
    m = simulate(stream_with_target_at(1, 4), prefetcher=Scripted({0: [TARGET]}))
    assert m.useful_prefetches == 1, "latency 0 must make the prefetch usable at i+1"
    assert m.late_prefetches == 0

def test_latency_blocks_use_until_arrival():
    latency = 5

    for pos in range(1, 1 + latency):
        m = simulate(stream_with_target_at(pos, 12),
                     prefetcher=Scripted({0: [TARGET]}), latency=latency)
        assert m.useful_prefetches == 0, f"target at access {pos} must not see the prefetch"
        assert m.late_prefetches == 1, f"a demand at access {pos} must count the prefetch as late"
        assert m.prefetches_issued == 1

    m = simulate(stream_with_target_at(1 + latency, 12),
                 prefetcher=Scripted({0: [TARGET]}), latency=latency)
    assert m.useful_prefetches == 1, "the prefetch must be usable exactly on its arrival access"
    assert m.late_prefetches == 0

def test_late_prefetch_is_not_later_inserted():
    latency = 4
    s = stream_with_target_at(2, 20)
    s[15] = (IP, TARGET)
    m = simulate(s, prefetcher=Scripted({0: [TARGET]}), latency=latency)
    assert m.late_prefetches == 1
    assert m.useful_prefetches == 0, "the demand-fetched line must not be credited to the prefetch"

def test_cancelled_prefetch_cannot_arrive_after_eviction():
    from config import CACHE_SETS, CACHE_WAYS

    latency = 20
    same_set = [(IP, TARGET + k * CACHE_SETS * BLOCK_SIZE) for k in range(1, CACHE_WAYS + 1)]
    s = (blocks(0, 1)
         + [(IP, TARGET)]
         + same_set
         + blocks(*range(2, 16))
         + [(IP, TARGET)])
    m = simulate(s, prefetcher=Scripted({0: [TARGET]}), latency=latency)
    assert m.late_prefetches == 1
    assert m.useful_prefetches == 0, "a cancelled prefetch must never be credited as useful"

def test_in_flight_duplicates_are_filtered():
    plan = {0: [TARGET], 1: [TARGET], 2: [TARGET]}
    m = simulate(blocks(*range(10)), prefetcher=Scripted(plan), latency=8)
    assert m.prefetches_generated == 3
    assert m.prefetches_issued == 1, "only one fetch may be in flight per block"
    assert m.prefetches_filtered == 2

def test_resident_block_is_filtered_under_latency():
    s = blocks(*range(6))
    s[0] = (IP, TARGET)
    m = simulate(s, prefetcher=Scripted({1: [TARGET]}), latency=3)
    assert m.prefetches_issued == 0
    assert m.prefetches_filtered == 1

def test_access_fallback_for_single_output_prefetchers():
    m = simulate(stream_with_target_at(1, 4), prefetcher=SingleOutput(TARGET))
    assert m.prefetches_generated == 1 and m.useful_prefetches == 1

def test_multiple_candidates_per_access():
    other = 6000 * BLOCK_SIZE
    s = blocks(*range(6))
    s[2] = (IP, TARGET)
    s[3] = (IP, other)
    m = simulate(s, prefetcher=Scripted({0: [TARGET, other]}))
    assert m.prefetches_issued == 2 and m.useful_prefetches == 2

def test_l2_latency_uses_the_global_access_clock():
    latency = 3

    filler = (IP, 1000 * BLOCK_SIZE)
    s = [filler, filler, filler, filler, (IP, TARGET), (IP, 1001 * BLOCK_SIZE)]
    m = simulate(s, prefetcher=Scripted({0: [TARGET]}), level="l2", latency=latency)

    assert m.l1_hits == 3
    assert m.useful_prefetches == 1, "arrival must be timed by all accesses, not L2 accesses"

def test_rejects_bad_arguments():
    for kwargs in ({"level": "l3"}, {"latency": -1}):
        try:
            simulate([], **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"{kwargs} must be rejected")

def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  [ok] {t.__name__}")
    print(f"[SUCCESS] {len(tests)} simulator tests passed")

if __name__ == "__main__":
    run_tests()
