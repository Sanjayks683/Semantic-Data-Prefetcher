from config import NGRAM_DEPTH, TABLE_SIZE, CONFIDENCE_THRESHOLD, BLOCK_OFFSET_BITS


class TableEntry:
    def __init__(self):
        self.valid = False
        self.tag = 0
        self.predicted_delta = 0
        self.confidence = 0


class NGramPrefetcher:
    def __init__(self, depth=NGRAM_DEPTH, table_size=TABLE_SIZE, conf_threshold=CONFIDENCE_THRESHOLD):
        self.depth = depth
        self.table_size = table_size
        self.conf_threshold = conf_threshold
        
        self.history = [0] * depth
        self.history_valid_count = 0
        self.table = [TableEntry() for _ in range(table_size)]

        self.prev_block_addr = None
        self.last_lookup_idx = None
        self.last_lookup_tag = None

    def _compute_hash(self, history_window):
        h0 = history_window[0] & 0xFFFF
        h1 = ((history_window[1] << 3) | ((history_window[1] >> 13) & 0x7)) & 0xFFFF
        h2 = ((history_window[2] << 7) | ((history_window[2] >> 9) & 0x7F)) & 0xFFFF

        full_hash = (h0 ^ h1 ^ h2) & 0xFFFF
        index = full_hash & (self.table_size - 1)
        tag = (full_hash >> 10) & 0x3F
        return index, tag

    def access(self, ip, addr):
        curr_block = addr >> BLOCK_OFFSET_BITS
        prefetch_candidate = None

        if self.prev_block_addr is not None:
            curr_delta = curr_block - self.prev_block_addr

            if self.last_lookup_idx is not None:
                entry = self.table[self.last_lookup_idx]

                if entry.valid and entry.tag == self.last_lookup_tag:
                    if entry.predicted_delta == curr_delta:
                        entry.confidence = min(3, entry.confidence + 1)
                    else:
                        if entry.confidence > 0:
                            entry.confidence -= 1
                        if entry.confidence == 0:
                            entry.predicted_delta = curr_delta
                            entry.confidence = 1
                else:
                    entry.valid = True
                    entry.tag = self.last_lookup_tag
                    entry.predicted_delta = curr_delta
                    entry.confidence = 1

            for i in range(self.depth - 1):
                self.history[i] = self.history[i + 1]
            self.history[self.depth - 1] = curr_delta
            self.history_valid_count = min(self.depth, self.history_valid_count + 1)

            if self.history_valid_count >= self.depth:
                idx, tag = self._compute_hash(self.history)
                self.last_lookup_idx = idx
                self.last_lookup_tag = tag

                entry = self.table[idx]
                if entry.valid and entry.tag == tag and entry.confidence >= self.conf_threshold:
                    pred_block = curr_block + entry.predicted_delta
                    prefetch_candidate = pred_block << BLOCK_OFFSET_BITS
            else:
                self.last_lookup_idx = None
                self.last_lookup_tag = None
        else:
            self.last_lookup_idx = None
            self.last_lookup_tag = None

        self.prev_block_addr = curr_block
        return prefetch_candidate
