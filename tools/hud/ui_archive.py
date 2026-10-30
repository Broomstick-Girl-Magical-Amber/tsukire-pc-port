"""ui_archive.py - read textures from the game's MRG/HED/NAM UI archives (allui.*), for the HUD glyph assets.

Format (the one the Mangetsu project documents; checked against the Japanese allui):
  .nam  names of 32 bytes (ASCII, space padded), one per entry
  .hed  8 bytes per entry: u32 offset in 2048-byte sectors, u32 (low 16 bits: size in sectors); ends with 0xFFFFFFFF
  .mrg  the data. An entry starting with "NXGX" is 16 bytes of header (magic, u32 size, u32 packed size, u32 0) and a
        gzip stream; inside, a BNTX. Other entries (NXCX, raw) are skipped.
BNTX: BRTI pointers at 0x28 (count at 0x24); per BRTI: format +0x1c, width/height +0x24, block height log2 +0x34,
size +0x50, name +0x60, mip pointer table +0x70. Data is Tegra block-linear (GOB 64x8 bytes), untiled here the same
way the runtime does (so hashes match what the game uploads).
Nothing is extracted to disk: the caller gets the texture in memory.
"""
import struct
import zlib

import numpy as np


def read_names(nam_path):
    raw = open(nam_path, "rb").read()
    return [raw[i:i + 32].decode("ascii", "replace").strip(" \0\r\n\t") for i in range(0, len(raw) - len(raw) % 32, 32)]


def read_entries(hed_path):
    raw = open(hed_path, "rb").read()
    out = []
    for i in range(0, len(raw) - 7, 8):
        off, sec = struct.unpack_from("<II", raw, i)
        if off == 0xFFFFFFFF:
            break
        out.append((off * 2048, (sec & 0xFFFF) * 2048))
    return out


def entry_data(mrg, offset, size):
    """Unpacked bytes of an NXGX entry, or None for any other kind."""
    mrg.seek(offset)
    raw = mrg.read(size)
    if raw[:4] != b"NXGX":
        return None
    packed = struct.unpack_from("<I", raw, 8)[0]
    return zlib.decompressobj(31).decompress(raw[16:16 + packed])


def _str(d, p):
    n = struct.unpack_from("<H", d, p)[0]
    return d[p + 2:p + 2 + n].decode("utf-8", "replace")


def bntx_textures(d):
    """[{name, format, width, height, bh_log2, data (level 0, tiled)}] of a BNTX."""
    if d[:4] != b"BNTX":
        return []
    count = struct.unpack_from("<I", d, 0x24)[0]
    table = struct.unpack_from("<Q", d, 0x28)[0]
    out = []
    for p in struct.unpack_from("<%dQ" % count, d, table):
        if d[p:p + 4] != b"BRTI":
            continue
        fmt = struct.unpack_from("<I", d, p + 0x1C)[0]
        w, h = struct.unpack_from("<II", d, p + 0x24)
        bh = struct.unpack_from("<I", d, p + 0x34)[0] & 7
        size = struct.unpack_from("<I", d, p + 0x50)[0]
        name = _str(d, struct.unpack_from("<Q", d, p + 0x60)[0])
        mips = struct.unpack_from("<Q", d, p + 0x70)[0]
        mip0 = struct.unpack_from("<Q", d, mips)[0]
        out.append({"name": name, "format": fmt, "width": w, "height": h, "bh_log2": bh, "data": d[mip0:mip0 + size]})
    return out


def deswizzle(data, width, height, bh_log2, block=4, bpb=16):
    """Block-linear (GOB 64x8) -> linear rows of blocks, level 0 of a 2D texture."""
    bw, bh = (width + block - 1) // block, (height + block - 1) // block
    gobs = 1 << bh_log2
    gobs_x = (bw * bpb + 63) // 64
    xb = np.arange(bw * bpb)  # byte column
    yb = np.arange(bh)        # block row
    gob_y = yb // 8
    base = ((gob_y // gobs)[:, None] * gobs_x + (xb // 64)[None, :]) * gobs * 512 + (gob_y % gobs)[:, None] * 512
    xi, yi = (xb % 64)[None, :], (yb % 8)[:, None]
    off = base + (xi // 32) * 256 + (yi // 2) * 64 + ((xi % 32) // 16) * 32 + (yi % 2) * 16 + xi % 16
    src = np.frombuffer(data, np.uint8)
    if off.max() >= src.size:
        raise ValueError("texture data shorter than its layout")
    return src[off].tobytes()


def fnv1a64(data):
    """FNV-1a 64 of the bytes (the runtime hashes the untiled level 0 the same way)."""
    h = 0xCBF29CE484222325
    for b in data:
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def find_textures(hed, nam, mrg, wanted):
    """{name: [(format, width, height, linear level 0, entry name)]} for the textures `wanted` accepts: a set of names,
    or a function name -> bool."""
    accept = wanted if callable(wanted) else (lambda n: n in wanted)
    names = read_names(nam)
    found = {}
    with open(mrg, "rb") as f:
        for i, (off, size) in enumerate(read_entries(hed)):
            d = entry_data(f, off, size)
            if not d:
                continue
            for t in bntx_textures(d):
                if accept(t["name"]):
                    lin = deswizzle(t["data"], t["width"], t["height"], t["bh_log2"])
                    found.setdefault(t["name"], []).append((t["format"], t["width"], t["height"], lin,
                                                             names[i] if i < len(names) else str(i)))
    return found
