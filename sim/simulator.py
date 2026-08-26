
from collections import deque

from cache import Cache
from metrics import Metrics

def candidates_of(prefetcher, ip, addr):
    access_all = getattr(prefetcher, "access_all", None)
    if access_all is not None:
        return access_all(ip, addr)
    cand = prefetcher.access(ip, addr)
    return [] if cand is None else [cand]

class _DelayedChannel:

    __slots__ = ("cache", "metrics", "latency", "offset_bits", "queue", "pending")

    def __init__(self, cache, metrics, latency):
        self.cache = cache
        self.metrics = metrics
        self.latency = latency
        self.offset_bits = cache.block_offset_bits
        self.queue = deque()
        self.pending = {}

    def deliver(self, now):
        queue, pending = self.queue, self.pending
        while queue and queue[0][0] <= now:
            ready_at, addr, block = queue.popleft()
            if pending.get(block) != ready_at:
                continue
            del pending[block]
            if self.cache.insert(addr, is_prefetch=True):
                self.metrics.evictions += 1

    def issue(self, now, addr):
        block = addr >> self.offset_bits
        resident, _ = self.cache.access(addr, is_prefetch=True)
        if resident or block in self.pending:
            self.metrics.record_prefetch_filtered()
            return
        self.metrics.prefetches_issued += 1
        ready_at = now + 1 + self.latency
        self.pending[block] = ready_at
        self.queue.append((ready_at, addr, block))

    def demand_missed(self, addr):
        if self.pending.pop(addr >> self.offset_bits, None) is not None:
            self.metrics.late_prefetches += 1

def simulate(stream, prefetcher=None, level="l1", latency=0,
             l2_sets=512, l2_ways=8, name=""):
    if level not in ("l1", "l2"):
        raise ValueError(f"level must be 'l1' or 'l2', got {level!r}")
    if latency < 0:
        raise ValueError(f"latency must be non-negative, got {latency}")

    l1 = Cache()
    target = Cache(sets=l2_sets, ways=l2_ways) if level == "l2" else l1
    m = Metrics(name=name)
    m.l1_hits = 0

    channel = _DelayedChannel(target, m, latency) if latency > 0 else None
    is_l2 = level == "l2"
    pf = prefetcher

    for now, (ip, addr) in enumerate(stream):
        if channel is not None:
            channel.deliver(now)

        if is_l2:
            l1_hit, _ = l1.access(addr, is_prefetch=False)
            if l1_hit:
                m.l1_hits += 1
                continue
            l1.insert(addr, is_prefetch=False)

        hit, useful = target.access(addr, is_prefetch=False)
        m.record_access(hit, useful)
        if not hit:
            if channel is not None:
                channel.demand_missed(addr)
            target.insert(addr, is_prefetch=False)

        if pf is None:
            continue

        for cand in candidates_of(pf, ip, addr):
            m.record_prefetch_generated()
            if channel is not None:
                channel.issue(now, cand)
                continue

            resident, _ = target.access(cand, is_prefetch=True)
            if resident:
                m.record_prefetch_filtered()
            else:
                m.record_prefetch(target.insert(cand, is_prefetch=True))

    m.dead_prefetches = target.dead_prefetch_evictions
    if pf is not None and hasattr(pf, "delta_overflows"):
        m.delta_overflows = pf.delta_overflows
    return m
