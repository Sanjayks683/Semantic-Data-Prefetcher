"""
test_prefetchers.py - Unit tests for both prefetchers and the trace parsers.
"""

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


# ------------------------------------------------------------- stride tests --

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
    """Address 0 must not be mistaken for an empty table entry."""
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

    # Confidence can never reach the threshold on a non-repeating delta stream.
    entry = p.table[p._get_index(IP)]
    assert entry.confidence < CONFIDENCE_THRESHOLD, (
        f"an irregular stream must not build confidence, got {entry.confidence}"
    )


# -------------------------------------------------------------- ngram tests --

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

    confidences = [e.confidence for e in p.table if e.valid]
    assert confidences, "a learned stream must populate the table"
    assert max(confidences) == CONF_MAX, (
        f"confidence must saturate at {CONF_MAX}, got {max(confidences)}"
    )
    assert all(c <= CONF_MAX for c in confidences), "confidence must never exceed CONF_MAX"


def test_ngram_delta_overflow_breaks_history():
    """A delta too large for the hardware field must not be silently wrapped."""
    p = NGramPrefetcher()
    base = 0x10000000

    for i in range(6):
        p.access(IP, base + i * BLOCK_SIZE)

    assert p.delta_overflows == 0

    # Jump far enough that the block delta cannot fit in a signed DELTA_WIDTH field.
    far = base + (DELTA_MAX + 1000) * BLOCK_SIZE
    out = p.access(IP, far)

    assert out is None, "an out-of-range delta must not produce a prediction"
    assert p.delta_overflows == 1, "the overflow must be counted, not hidden"
    assert p.history_valid_count == 0, "the window must be dropped after a discontinuity"


def test_ngram_no_prediction_below_threshold():
    """A single observation must never be enough to fire a prefetch."""
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


# ------------------------------------------------------------- parser tests --

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
    """Register fields must never be mistaken for memory operands."""
    record = struct.pack(
        INSTR_STRUCT_FORMAT,
        0x400100,                      # ip
        0, 0,                          # is_branch, branch_taken
        b"\x05\x06",                   # destination_registers - NOT addresses
        b"\x07\x08\x09\x0a",           # source_registers      - NOT addresses
        0xD000, 0,                     # destination_memory[2]
        0xA000, 0xA040, 0, 0xA080,     # source_memory[4]
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
    """Records must decode correctly across the internal read-block boundary.

    The reader pulls many records per LZMA read for speed; a record straddling
    two blocks is the case that silently corrupts or drops accesses.
    """
    from trace_parser import _RECORDS_PER_READ

    count = _RECORDS_PER_READ * 2 + 7   # deliberately not a block multiple
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


def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  [ok] {t.__name__}")
    print(f"[SUCCESS] {len(tests)} prefetcher and parser tests passed")


if __name__ == "__main__":
    run_tests()
