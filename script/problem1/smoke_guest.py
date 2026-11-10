"""Generate tiny static x86-64 ELF guests without a cross compiler.

This is a hardware/ROI/statistics smoke workload, not PARSEC. One independent
process runs per core. Only core 0 emits global m5 workbegin/workend events.
The load/add/store loop exercises data caches and translation as well as O3.
"""
import struct
from pathlib import Path


def write_guest(path: Path, leader: bool):
    code = bytearray.fromhex("31ff31f6")  # xor edi,edi; xor esi,esi (m5 args)
    if leader:
        # Warm up before beginning the measured region.
        code += bytes.fromhex("b9") + struct.pack("<I", 1000)
        code += bytes.fromhex("83e90175fb")  # sub ecx,1; jne -5
        code += bytes.fromhex("0f045a00")  # m5_work_begin(0,0)
    code += bytes.fromhex("b9") + struct.pack("<I", 20000 if leader else 2000000)
    code += bytes.fromhex("48bb") + struct.pack("<Q", 0x402000)
    # mov rax,[rbx]; add rax,1; mov [rbx],rax; sub ecx,1; jne loop
    code += bytes.fromhex("488b034883c00148890383e90175f1")
    if leader:
        code += bytes.fromhex("0f045b00")  # m5_work_end(0,0)
    code += bytes.fromhex("b83c00000031ff0f05")  # exit(0)
    names = b"\0.text\0.bss\0.shstrtab\0"
    names_offset = 4096 + len(code)
    section_offset = (names_offset + len(names) + 7) & ~7
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8)
    header = ident + struct.pack("<HHIQQQIHHHHHH", 2, 62, 1, 0x401000,
                                64, section_offset, 0, 64, 56, 1, 64, 4, 3)
    # Single loadable segment; code at page 1, zero-filled data at page 2.
    segment = struct.pack("<IIQQQQQQ", 1, 7, 0, 0x400000, 0x400000,
                          4096 + len(code), 12288, 4096)
    sections = bytes(64)  # mandatory null section
    sections += struct.pack("<IIQQQQIIQQ", 1, 1, 6, 0x401000, 4096, len(code), 0, 0, 16, 0)
    sections += struct.pack("<IIQQQQIIQQ", 7, 8, 3, 0x402000, 0, 4096, 0, 0, 8, 0)
    sections += struct.pack("<IIQQQQIIQQ", 12, 3, 0, 0, names_offset, len(names), 0, 0, 1, 0)
    data = (header + segment).ljust(4096, b"\0") + code + names
    path.write_bytes(data.ljust(section_offset, b"\0") + sections)
    path.chmod(0o755)
