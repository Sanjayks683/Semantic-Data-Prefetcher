
import struct
import lzma

INSTR_STRUCT_FORMAT = "<QBB2s4s2Q4Q"
STRUCT_SIZE = struct.calcsize(INSTR_STRUCT_FORMAT)

_F_IP = 0
_F_DEST_MEM = slice(5, 7)
_F_SRC_MEM = slice(7, 11)

assert STRUCT_SIZE == 64, f"ChampSim input_instr must be 64 bytes, got {STRUCT_SIZE}"
assert len(struct.unpack(INSTR_STRUCT_FORMAT, b"\x00" * STRUCT_SIZE)) == 11

def parse_text_trace(filepath):
    with open(filepath, "r") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if len(parts) < 2:
                raise ValueError(
                    f"{filepath}:{line_no}: expected '<ip> <addr>', got {line!r}"
                )
            try:
                yield int(parts[0], 0), int(parts[1], 0)
            except ValueError as exc:
                raise ValueError(f"{filepath}:{line_no}: {exc}") from exc

_RECORDS_PER_READ = 8192
_READ_CHUNK = STRUCT_SIZE * _RECORDS_PER_READ

_unpack_from = struct.Struct(INSTR_STRUCT_FORMAT).unpack_from

def parse_champsim_binary_trace(filepath):
    with lzma.open(filepath, "rb") as f:
        leftover = b""
        while True:
            chunk = f.read(_READ_CHUNK)
            if not chunk:
                break
            if leftover:
                chunk = leftover + chunk

            whole = len(chunk) // STRUCT_SIZE
            for r in range(whole):
                fields = _unpack_from(chunk, r * STRUCT_SIZE)
                ip = fields[_F_IP]

                for addr in fields[_F_SRC_MEM]:
                    if addr != 0:
                        yield ip, addr

                for addr in fields[_F_DEST_MEM]:
                    if addr != 0:
                        yield ip, addr

            leftover = chunk[whole * STRUCT_SIZE:]

        if leftover:
            raise ValueError(
                f"{filepath}: trailing {len(leftover)} bytes do not form a "
                f"complete {STRUCT_SIZE}-byte record"
            )

def get_trace_iterator(filepath):
    if filepath.endswith(".xz"):
        return parse_champsim_binary_trace(filepath)
    return parse_text_trace(filepath)
