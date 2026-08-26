
import io
import os
import lzma
import struct
import tempfile

from ngram_prefetcher import NGramPrefetcher
from stride_prefetcher import StridePrefetcher
from trace_parser import (
    parse_text_trace,
    parse_champsim_binary_trace,
    INSTR_STRUCT_FORMAT,
    STRUCT_SIZE,
)
from config import BLOCK_SIZE, CONFIDENCE_THRESHOLD, DELTA_MAX, CONF_MAX

IP = 0x400100

def test_stride_learns_constant_stride():
    p = StridePrefetcher()
    base = 0x1000

    issued = [p.access(IP, base + i * BLOCK_SIZE) for i in range(8)]

    assert issued[0] is None, "no prediction is possible on the first access"
    assert any(a is not None for a in issued), "a constant stride must be learned"

    last = p.access(IP, base + 8 * BLOCK_SIZE)
    assert last == base + 9 * BLOCK_SIZE, (
        f"expected next-block prediction 0x{base + 9 * BLOCK_SIZE:x}, got {last}"
    )

def test_stride_address_zero_is_a_real_access():
    p = StridePrefetcher()

    p.access(IP, 0)
    p.access(IP, BLOCK_SIZE)
    p.access(IP, 2 * BLOCK_SIZE)
    out = p.access(IP, 3 * BLOCK_SIZE)

    assert out == 4 * BLOCK_SIZE, (
        f"a stream starting at address 0 must be learnable, got {out}"
    )

def test_stride_gives_up_on_irregular_pattern():
    p = StridePrefetcher()
    addrs = [0x1000, 0x9000, 0x2400, 0xF800, 0x300, 0xB100, 0x4C00]

    for a in addrs:
        p.access(IP, a)

    entry = p.table[p._get_index(IP)]
    assert entry.confidence < CONFIDENCE_THRESHOLD, (
        f"an irregular stream must not build confidence, got {entry.confidence}"
    )

def test_ngram_learns_repeating_cycle():
    p = NGramPrefetcher()
    base = 0x10000000
    cycle = [0, 0x180, 0x340, 0x40]

    hits = 0
    trials = 0
    for rep in range(12):
        for i, off in enumerate(cycle):
            out = p.access(IP, base + off)
            expected = base + cycle[(i + 1) % len(cycle)]
            if rep >= 4:
                trials += 1
                if out == expected:
                    hits += 1

    assert trials > 0
    assert hits == trials, (
        f"after warm-up every prediction must be correct, got {hits}/{trials}"
    )

def test_ngram_silent_on_first_accesses():
    p = NGramPrefetcher()
    outs = [p.access(IP, 0x2000 + i * BLOCK_SIZE) for i in range(3)]
    assert all(o is None for o in outs), (
        "the n-gram window needs three deltas before it can predict"
    )

def test_ngram_confidence_saturates():
    p = NGramPrefetcher()
    for i in range(40):
        p.access(IP, 0x3000 + i * BLOCK_SIZE)

    confidences = p.occupied_confidences()
    assert confidences, "a learned stream must populate the table"
    assert max(confidences) == CONF_MAX, (
        f"confidence must saturate at {CONF_MAX}, got {max(confidences)}"
    )
    assert all(c <= CONF_MAX for c in confidences), "confidence must never exceed CONF_MAX"

def test_ngram_delta_overflow_breaks_history():
    p = NGramPrefetcher()
    base = 0x10000000

    for i in range(6):
        p.access(IP, base + i * BLOCK_SIZE)

    assert p.delta_overflows == 0

    far = base + (DELTA_MAX + 1000) * BLOCK_SIZE
    out = p.access(IP, far)

    assert out is None, "an out-of-range delta must not produce a prediction"
    assert p.delta_overflows == 1, "the overflow must be counted, not hidden"
    assert p.history_valid_count == 0, "the window must be dropped after a discontinuity"

def test_ngram_no_prediction_below_threshold():
    p = NGramPrefetcher()
    base = 0x50000000
    cycle = [0, 0x200, 0x40, 0x2C0, 0x80]

    fired_before_threshold = 0
    for rep in range(2):
        for off in cycle:
            if p.access(IP, base + off) is not None:
                fired_before_threshold += 1

    assert fired_before_threshold == 0, (
        f"prefetches fired before confidence reached {CONFIDENCE_THRESHOLD}"
    )

def test_ngram_rejects_non_power_of_two_table():
    try:
        NGramPrefetcher(table_size=1000)
    except ValueError:
        return
    raise AssertionError("a non-power-of-two table size must be rejected")

def test_text_parser_skips_comments_and_blanks():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".trace", delete=False)
    tmp.write("# header\n\n0x400100 0x1000\n\n0x400100 0x1040\n# trailing\n")
    tmp.close()
    try:
        rows = list(parse_text_trace(tmp.name))
    finally:
        os.unlink(tmp.name)

    assert rows == [(0x400100, 0x1000), (0x400100, 0x1040)], rows

def test_text_parser_rejects_malformed_line():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".trace", delete=False)
    tmp.write("0x400100 0x1000\ngarbage\n")
    tmp.close()
    try:
        list(parse_text_trace(tmp.name))
    except ValueError:
        return
    finally:
        os.unlink(tmp.name)
    raise AssertionError("a malformed trace line must raise, not be dropped")

def test_champsim_parser_extracts_all_memory_operands():
    record = struct.pack(
        INSTR_STRUCT_FORMAT,
        0x400100,
        0, 0,
        b"\x05\x06",
        b"\x07\x08\x09\x0a",
        0xD000, 0,
        0xA000, 0xA040, 0, 0xA080,
    )
    assert len(record) == STRUCT_SIZE

    tmp = tempfile.NamedTemporaryFile(suffix=".xz", delete=False)
    tmp.close()
    with lzma.open(tmp.name, "wb") as f:
        f.write(record)
    try:
        rows = list(parse_champsim_binary_trace(tmp.name))
    finally:
        os.unlink(tmp.name)

    addrs = [a for _, a in rows]
    assert all(isinstance(a, int) for a in addrs), f"non-integer address: {addrs}"
    assert sorted(addrs) == [0xA000, 0xA040, 0xA080, 0xD000], (
        f"expected all four non-zero memory operands, got {[hex(a) for a in addrs]}"
    )

def test_champsim_parser_spans_read_boundaries():
    from trace_parser import _RECORDS_PER_READ

    count = _RECORDS_PER_READ * 2 + 7
    blob = b"".join(
        struct.pack(
            INSTR_STRUCT_FORMAT,
            0x400000 + k, 0, 0,
            b"\x01\x02", b"\x03\x04\x05\x06",
            0xD000 + k, 0,
            0xA000 + k, 0, 0, 0,
        )
        for k in range(count)
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".xz", delete=False)
    tmp.close()
    with lzma.open(tmp.name, "wb") as f:
        f.write(blob)
    try:
        rows = list(parse_champsim_binary_trace(tmp.name))
    finally:
        os.unlink(tmp.name)

    assert len(rows) == count * 2, (
        f"expected {count * 2} operands from {count} records, got {len(rows)}"
    )
    assert rows[0] == (0x400000, 0xA000)
    assert rows[-1] == (0x400000 + count - 1, 0xD000 + count - 1)

def _stream_from_deltas(deltas, reps, base_block=0x4000):
    block = base_block
    out = [(IP, block * BLOCK_SIZE)]
    for _ in range(reps):
        for d in deltas:
            block += d
            out.append((IP, block * BLOCK_SIZE))
    return out

def test_single_slot_cannot_learn_a_branch_but_two_slots_can():
    cycle = [1, 5, 1, 9]

    def followers_after_plus_one(slots):
        p = NGramPrefetcher(depth=1, slots=slots)
        found = set()
        stream = _stream_from_deltas(cycle, 30)
        prev = None
        for i, (ip, addr) in enumerate(stream):
            cands = p.access_all(ip, addr)
            block = addr >> 6
            if prev is not None and block - prev == 1 and i > 40:
                found |= {(c >> 6) - block for c in cands}
            prev = block
        return found

    assert followers_after_plus_one(1) == set(), (
        "one slot should never become confident about a branching window"
    )
    assert followers_after_plus_one(2) == {5, 9}, (
        "two slots must learn and prefetch both followers of a branching window"
    )

def test_lookahead_follows_the_predicted_chain():
    p = NGramPrefetcher(degree=4)
    stream = _stream_from_deltas([1], 40)
    for ip, addr in stream[:-1]:
        p.access_all(ip, addr)
    ip, addr = stream[-1]
    block = addr >> 6
    got = [(c >> 6) - block for c in p.access_all(ip, addr)]
    assert got == [1, 2, 3, 4], f"degree 4 on a +1 stride must reach 4 blocks ahead, got {got}"

def test_lookahead_stops_when_the_chain_is_not_confident():
    p = NGramPrefetcher(depth=1, degree=8)
    block = 0x4000
    stream = [(IP, block * BLOCK_SIZE)]
    for rep in range(60):
        for d in (1, 2, 3, 100 + 7 * rep):
            block += d
            stream.append((IP, block * BLOCK_SIZE))

    longest = 0
    for ip, addr in stream:
        cands = p.access_all(ip, addr)
        assert len(cands) <= 2, f"lookahead ran past the last confident step: {len(cands)}"
        longest = max(longest, len(cands))
    assert longest == 2, f"lookahead never reached the second confident step (max {longest})"

def test_lookahead_never_modifies_the_table():
    streams = [
        _stream_from_deltas([1], 30),
        _stream_from_deltas([3, 7, -2, 5, 3, 7, -2, 11], 40),
    ]
    for depth, stream in ((3, streams[0]), (2, streams[1])):
        plain = NGramPrefetcher(depth=depth, slots=2)
        ahead = NGramPrefetcher(depth=depth, slots=2, degree=6)
        for i, (ip, addr) in enumerate(stream):
            plain.access_all(ip, addr)
            ahead.access_all(ip, addr)
            for name in ("valid", "tags", "deltas", "confs"):
                assert getattr(plain, name) == getattr(ahead, name), (
                    f"lookahead changed table.{name} at access {i}"
                )

def test_multi_slot_retrains_when_a_follower_disappears():
    p = NGramPrefetcher(depth=1, slots=2)
    for ip, addr in _stream_from_deltas([1, 5, 1, 9], 20):
        p.access_all(ip, addr)

    for ip, addr in _stream_from_deltas([1, 7], 30, base_block=0x9000):
        p.access_all(ip, addr)
    idx, tag = p._compute_hash([1])
    _, _, slots = p.entry(idx)
    assert 7 in [d for d, c in slots if c >= CONFIDENCE_THRESHOLD], (
        f"the new follower +7 must become confident, slots={slots}"
    )

def test_one_novel_follower_does_not_evict_confident_slots():
    p = NGramPrefetcher(depth=1, slots=2)
    stream = _stream_from_deltas([1, 5, 1, 9], 20)
    block = stream[-1][1] >> 6
    for d in (1, 7):
        block += d
        stream.append((IP, block * BLOCK_SIZE))
    for ip, addr in stream:
        p.access_all(ip, addr)

    idx, tag = p._compute_hash([1])
    _, _, slots = p.entry(idx)
    held = {d: c for d, c in slots}
    assert set(held) == {5, 9}, f"one +7 must not evict an established follower, slots={slots}"
    assert all(c >= CONFIDENCE_THRESHOLD for c in held.values()), (
        f"both followers must stay confident after one miss, slots={slots}"
    )

def test_strongest_slot_is_issued_first_and_followed_by_lookahead():
    p = NGramPrefetcher(depth=1, slots=2, degree=2)
    block = 0x4000
    stream = [(IP, block * BLOCK_SIZE)]

    for d in (1, 5, 1, 5, 1, 5, 1, 9, 1, 9, 1):
        block += d
        stream.append((IP, block * BLOCK_SIZE))
    out = []
    for ip, addr in stream:
        out = p.access_all(ip, addr)
    got = [(c >> 6) - block for c in out]
    assert got == [5, 9, 6], (
        f"expected strongest (+5) first, then +9, then lookahead along +5 -> +1; got {got}"
    )

def test_stride_degree_prefetches_several_strides_ahead():
    p = StridePrefetcher(degree=3)
    base = 0x10000
    out = []
    for i in range(8):
        out = p.access_all(IP, base + i * BLOCK_SIZE)
    last = base + 7 * BLOCK_SIZE
    assert out == [last + BLOCK_SIZE, last + 2 * BLOCK_SIZE, last + 3 * BLOCK_SIZE], out

def test_stride_degree_deduplicates_sub_block_strides():
    p = StridePrefetcher(degree=4)
    out = []
    for i in range(8):
        out = p.access_all(IP, 0x20000 + i * 8)
    assert len(out) == len(set(out)), f"duplicate block prefetches: {out}"

def test_rejects_invalid_degree_and_slots():
    for kwargs in ({"degree": 0}, {"slots": 0}):
        try:
            NGramPrefetcher(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"NGramPrefetcher({kwargs}) must be rejected")
    try:
        StridePrefetcher(degree=0)
    except ValueError:
        return
    raise AssertionError("StridePrefetcher(degree=0) must be rejected")

def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  [ok] {t.__name__}")
    print(f"[SUCCESS] {len(tests)} prefetcher and parser tests passed")

if __name__ == "__main__":
    run_tests()
