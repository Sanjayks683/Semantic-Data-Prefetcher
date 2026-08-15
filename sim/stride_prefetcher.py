from config import STRIDE_TABLE_SIZE, STRIDE_CONF_THRESHOLD, BLOCK_OFFSET_BITS


class StrideEntry:
    def __init__(self):
        self.last_addr = 0
        self.stride = 0
        self.confidence = 0


class StridePrefetcher:
    def __init__(self, table_size=STRIDE_TABLE_SIZE, conf_threshold=STRIDE_CONF_THRESHOLD):
        self.table_size = table_size
        self.conf_threshold = conf_threshold
        self.table = [StrideEntry() for _ in range(table_size)]

    def _get_index(self, ip):
        return (ip >> 2) & (self.table_size - 1)

    def access(self, ip, addr):
        idx = self._get_index(ip)
        entry = self.table[idx]
        prefetch_candidate = None

        if entry.last_addr != 0:
            current_stride = addr - entry.last_addr

            if current_stride == entry.stride and entry.stride != 0:
                entry.confidence = min(3, entry.confidence + 1)
            else:
                if entry.confidence > 0:
                    entry.confidence -= 1
                if entry.confidence == 0:
                    entry.stride = current_stride
                    entry.confidence = 1

            if entry.confidence >= self.conf_threshold:
                prefetch_candidate = addr + entry.stride

        entry.last_addr = addr

        if prefetch_candidate is not None:
            prefetch_candidate = (prefetch_candidate >> BLOCK_OFFSET_BITS) << BLOCK_OFFSET_BITS

        return prefetch_candidate
