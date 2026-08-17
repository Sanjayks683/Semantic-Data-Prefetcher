class Metrics:
    def __init__(self, name="Baseline"):
        self.name = name
        self.total_accesses = 0
        self.hits = 0
        self.misses = 0
        self.prefetches_issued = 0
        self.useful_prefetches = 0
        self.evictions = 0

    def record_access(self, hit, is_useful_prefetch):
        self.total_accesses += 1
        if hit:
            self.hits += 1
        else:
            self.misses += 1

        if is_useful_prefetch:
            self.useful_prefetches += 1

    def record_prefetch(self, evicted):
        self.prefetches_issued += 1
        if evicted:
            self.evictions += 1

    @property
    def miss_rate(self):
        return (self.misses / self.total_accesses * 100.0) if self.total_accesses > 0 else 0.0

    @property
    def hit_rate(self):
        return (self.hits / self.total_accesses * 100.0) if self.total_accesses > 0 else 0.0

    @property
    def accuracy(self):
        return (self.useful_prefetches / self.prefetches_issued * 100.0) if self.prefetches_issued > 0 else 0.0

    def compute_coverage(self, baseline_misses):
        if baseline_misses == 0:
            return 0.0
        reduction = max(0, baseline_misses - self.misses)
        return (reduction / baseline_misses) * 100.0

    def print_summary(self, baseline_misses=None):
        print(f"\n=======================================================")
        print(f"  METRICS REPORT: {self.name}")
        print(f"=======================================================")
        print(f"  Total Accesses:      {self.total_accesses:,}")
        print(f"  Cache Hits:          {self.hits:,} ({self.hit_rate:.2f}%)")
        print(f"  Cache Misses:        {self.misses:,} ({self.miss_rate:.2f}%)")
        print(f"  Prefetches Issued:   {self.prefetches_issued:,}")
        print(f"  Useful Prefetches:   {self.useful_prefetches:,}")
        print(f"  Prefetch Accuracy:   {self.accuracy:.2f}%")
        if baseline_misses is not None:
            cov = self.compute_coverage(baseline_misses)
            print(f"  Prefetch Coverage:   {cov:.2f}% (vs No-Prefetch)")
        print(f"=======================================================")
