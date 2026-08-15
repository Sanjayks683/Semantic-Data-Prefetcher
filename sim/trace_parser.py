import struct
import lzma
import os


def parse_text_trace(filepath):
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                ip = int(parts[0], 0)
                addr = int(parts[1], 0)
                yield ip, addr


def parse_champsim_binary_trace(filepath):
    INSTR_STRUCT_FORMAT = "<QBB2s4s2Q4Q"
    STRUCT_SIZE = struct.calcsize(INSTR_STRUCT_FORMAT)

    with lzma.open(filepath, "rb") as f:
        while True:
            chunk = f.read(STRUCT_SIZE)
            if len(chunk) < STRUCT_SIZE:
                break
            fields = struct.unpack(INSTR_STRUCT_FORMAT, chunk)
            ip = fields[0]
            dest_mem_0 = fields[3]
            dest_mem_1 = fields[4]
            src_mems = fields[5:9]

            for src_addr in src_mems:
                if src_addr != 0:
                    yield ip, src_addr

            if dest_mem_0 != 0:
                yield ip, dest_mem_0
            if dest_mem_1 != 0:
                yield ip, dest_mem_1


def get_trace_iterator(filepath):
    if filepath.endswith(".xz"):
        return parse_champsim_binary_trace(filepath)
    else:
        return parse_text_trace(filepath)
