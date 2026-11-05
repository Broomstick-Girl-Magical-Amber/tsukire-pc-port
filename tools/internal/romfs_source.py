"""romfs_source.py - The game reads its RomFS straight from the .xci/.nsp (used by recomp_tsukihime.ps1; AP_ROMFS_DIRECT).

Instead of extracting the whole RomFS (~21-23 GB per edition), the recompilation writes a small table of it,
<data>\\romfs_source.bin, and the exe (games/tsukihime_common/romfs_direct.cpp) reads every file from the dump in the
root of the port, decrypting only the bytes it asks for (AES-128-CTR). Only what the build itself needs is extracted:
the exefs and a few RomFS files (romfs\\shader for step 8, the *allui.hed/.nam/.mrg of the HUD icons).

Commands (the NCA code is the one of mods_nsp.py, imported from this folder):
  info    --keys K --input <.xci/.nsp>
          Prints PROGRAMID=<16 hex> of the base application (a program NCA without BKTR). Exit code 3: none.
  prepare --keys K --input <dump> --program-id ID --source <romfs_source.bin> --build-romfs <dir> [--exefs <dir>]
          Writes the table (with the content key of the NCA: option B, the exe never needs prod.keys), the RomFS files
          the build uses and, with --exefs, the exefs. The last line is "PREPARE key=value ...". Exit code 3: the dump
          has no base program NCA of that ID (another edition).
  locate  --source <romfs_source.bin> --root <port root>
          Prints DUMP=<path> of the .xci/.nsp of the root the table belongs to (same check as the exe: the first 0x100
          bytes of the program NCA at the recorded offset). Exit code 4: not found.
  trim    --keys K --input <.xci> --program-id ID [--apply]
          XCI only: the cartridge image is padded with 0xFF up to the cartridge size. Finds where the data ends (the
          larger of the header's valid data end and the last byte of the partitions), checks that EVERYTHING after it
          is 0xFF and, with --apply, cuts the file there (in place, seconds). Without --apply it only reports. Nothing
          of the game data changes: the table of romfs_source.bin stays valid. Exit code 3: another edition.
  untrim  --input <.xci> [--apply]
          Undoes trim: pads with 0xFF up to the cartridge size again (952 MiB per "GB" of the RomSize byte).

Never prints keys. Needs pycryptodome (tools\\requirements.txt).
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mods_nsp as m  # noqa: E402  (NCA, sections, RomFS tables, partitions)

MAGIC = b"NXRSRC1\0"
END = b"END1"
VERSION = 1
CHUNK = 8 << 20
MIB = 1 << 20
# RomSize byte of the XCI header -> cartridge size in "GB" (a formatted GB is 952 MiB: an untrimmed dump has that size)
CART_GB = {0xFA: 1, 0xF8: 2, 0xF0: 4, 0xE0: 8, 0xE1: 16, 0xE2: 32}


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def build_wanted(rel):
    """RomFS files the recompilation reads: shaders (step 8) and the UI archives of the HUD icons (build_hud_assets.py)."""
    low = rel.lower()
    if low.startswith("shader/"):
        return True
    base = low.rsplit("/", 1)[-1]
    stem, _, ext = base.rpartition(".")
    return stem.endswith("allui") and ext in ("hed", "nam", "mrg")


def romfs_dirs(read, h):
    """Every directory path of the RomFS ('' = root excluded), empty ones included."""
    dmeta = read(u64(h, 0x18), u64(h, 0x20))
    paths = {}

    def path(o):
        if o in paths:
            return paths[o]
        parent, nlen = u32(dmeta, o), u32(dmeta, o + 0x14)
        name = dmeta[o + 0x18:o + 0x18 + nlen].decode("utf-8")
        p = "" if o == 0 else ((path(parent) + "/" if parent != 0 else "") + name)
        paths[o] = p
        return p

    out, o = [], 0
    while o + 0x18 <= len(dmeta):
        nlen = u32(dmeta, o + 0x14)
        p = path(o)
        if p:
            out.append(p)
        o += 0x18 + ((nlen + 3) & ~3)
    return out


def base_program(files, keys):
    tks = m.tickets_of(files)
    out = []
    for name, s in sorted(files.items()):
        if not name.endswith(".nca") or name.endswith(".cnmt.nca") or s.size < 0xC00:
            continue
        try:
            n = m.Nca(s, keys, tks)
        except ValueError:
            continue
        if n.content_type == 0 and 1 in n.sections and n.section(1).enc not in (4, 6):
            out.append((name, n))
    return out


# ---------------------------------------------------------------------------------------------------------- info
def cmd_info(a):
    found = base_program(m.container_files(a.input), m.load_keys(a.keys))
    if not found:
        print("SKIP no base program NCA")
        sys.exit(3)
    ids = sorted({n.title_id for _, n in found})
    if len(ids) != 1:
        m.fail(f"{len(ids)} base applications in {a.input}: " + ", ".join(f"{i:016X}" for i in ids))
    print(f"PROGRAMID={ids[0]:016X}")


# ---------------------------------------------------------------------------------------------------------- prepare
def write_file(path, read, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        for c in range(0, size, CHUNK):
            f.write(read(c, min(CHUNK, size - c)))
    os.replace(path + ".tmp", path)


def cmd_prepare(a):
    keys = m.load_keys(a.keys)
    pid = int(a.program_id, 16)
    name, nca = m.find_program(m.container_files(a.input), keys, pid, False)
    sec = nca.section(1)
    if sec.enc != 3:
        m.fail(f"romfs section with encryption type {sec.enc}: the exe only reads AES-CTR (3)")
    l6_off, l6_size = sec.ivfc_data()
    rd = lambda o, n: sec.read(l6_off + o, n)
    files, h = m.romfs_files(rd)
    dirs = romfs_dirs(rd, h)
    print(f"   base program NCA {name}: romfs {l6_size:,} bytes, {len(files)} files, {len(dirs)} folders")
    t0 = time.time()

    exefs_n = 0
    if a.exefs:
        sec0 = nca.section(0)
        po, ps = sec0.pfs0_region()
        pfs = m.partition_fs(m._SectionSource(sec0, po, ps), b"PFS0", 0x18)
        if pfs is None:
            m.fail("exefs without PFS0")
        os.makedirs(a.exefs, exist_ok=True)
        for fn, s in pfs.items():
            with open(os.path.join(a.exefs, fn), "wb") as f:
                f.write(s.read(0, s.size))
        exefs_n = len(pfs)
        print(f"   exefs: {', '.join(sorted(pfs))}")

    sub = [(o, s, r) for o, s, r in files if build_wanted(r)]
    sub_bytes = sum(s for _, s, _ in sub)
    for o, s, r in sub:
        write_file(m.to_host(a.build_romfs, r), lambda c, n, o=o: rd(o + c, n), s)
    if not any(r.lower().startswith("shader/") for _, _, r in sub):
        m.fail("the romfs has no shader folder")
    print(f"   build files: {len(sub)} ({sub_bytes / MIB:.1f} MiB) -> {a.build_romfs}")

    # the table (see romfs_direct.cpp for the reader of this same layout)
    out = bytearray()
    out += MAGIC + struct.pack("<II", VERSION, 0)
    out += struct.pack("<QQQQQ", pid, nca.src.offset, nca.src.size, sec.start + l6_off, l6_size)
    out += sec.upper + nca.key
    out += nca.src.read(0, 0x100)
    hint = os.path.basename(a.input).encode("utf-8")
    out += struct.pack("<I", len(hint)) + hint
    out += struct.pack("<I", len(dirs))
    for d in dirs:
        b = d.encode("utf-8")
        out += struct.pack("<I", len(b)) + b
    out += struct.pack("<I", len(files))
    for o, s, r in files:
        b = r.encode("utf-8")
        out += struct.pack("<QQI", o, s, len(b)) + b
    out += END
    os.makedirs(os.path.dirname(os.path.abspath(a.source)), exist_ok=True)
    with open(a.source + ".tmp", "wb") as f:
        f.write(out)
    os.replace(a.source + ".tmp", a.source)
    print(f"   table: {len(out):,} bytes -> {a.source} ({time.time() - t0:.1f} s)")
    print(f"PREPARE files={len(files)} dirs={len(dirs)} build_files={len(sub)} build_bytes={sub_bytes} "
          f"exefs={exefs_n} romfs_bytes={l6_size} nca={name}")


# ---------------------------------------------------------------------------------------------------------- locate
def read_source_head(path):
    with open(path, "rb") as f:
        raw = f.read(0x150 + 4 + 4096)
    if raw[:8] != MAGIC or u32(raw, 8) != VERSION:
        m.fail(f"{path} is not a romfs_source.bin of version {VERSION}")
    nca_off, nca_size = u64(raw, 0x18), u64(raw, 0x20)
    sig = raw[0x50:0x150]
    nlen = u32(raw, 0x150)
    hint = raw[0x154:0x154 + nlen].decode("utf-8", "replace")
    return nca_off, nca_size, sig, hint


def matches(path, nca_off, nca_size, sig):
    try:
        if os.path.getsize(path) < nca_off + nca_size:
            return False
        with open(path, "rb") as f:
            f.seek(nca_off)
            return f.read(0x100) == sig
    except OSError:
        return False


def cmd_locate(a):
    nca_off, nca_size, sig, hint = read_source_head(a.source)
    cands = []
    if hint:
        cands.append(os.path.join(a.root, hint))
    for n in sorted(os.listdir(a.root)):
        p = os.path.join(a.root, n)
        if n.lower().endswith((".xci", ".nsp")) and os.path.isfile(p) and p not in cands:
            cands.append(p)
    for p in cands:
        if matches(p, nca_off, nca_size, sig):
            print(f"DUMP={os.path.abspath(p)}")
            return
    print(f"NOTFOUND none of the .xci/.nsp of {a.root} is the dump of this table (it was {hint})")
    sys.exit(4)


# ---------------------------------------------------------------------------------------------------------- trim
def xci_layout(path):
    """(data end, cartridge size, file size) of an XCI; None if the file is not an XCI with the header at 0."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        whole = m.Source(fh, 0, size)
        if size < 0x200:
            return None
        hdr = whole.read(0, 0x200)
        if hdr[0x100:0x104] != b"HEAD":
            if size >= 0x1200 and whole.read(0x1100, 4) == b"HEAD":
                m.fail(f"{path}: XCI with the key area prepended (0x1000 bytes before the header): not supported")
            return None
        valid_end = (u32(hdr, 0x118) + 1) * 0x200
        root_off = u64(hdr, 0x130)
        root = m.partition_fs(m.Source(fh, root_off, size - root_off), b"HFS0", 0x40) or {}
        data_end = 0
        for _, s in root.items():
            data_end = max(data_end, s.offset + s.size)
            sub = m.partition_fs(s, b"HFS0", 0x40) if s.size >= 0x10 else None
            for _, t in (sub or {}).items():
                data_end = max(data_end, t.offset + t.size)
        gb = CART_GB.get(hdr[0x10D])
        return max(valid_end, data_end), (gb * 952 * MIB if gb else None), size


def check_padding(path, start, end):
    """None if every byte of [start, end) is 0xFF, else the offset of the first one that is not."""
    ff = b"\xff" * CHUNK
    t0 = last = time.time()
    with open(path, "rb") as f:
        f.seek(start)
        off = start
        while off < end:
            n = min(CHUNK, end - off)
            b = f.read(n)
            if len(b) != n:
                m.fail("short read while checking the padding")
            if b != ff[:n]:
                return off + next(i for i, x in enumerate(b) if x != 0xFF)
            off += n
            if time.time() - last >= 10:
                last = time.time()
                print(f"   ... padding checked {(off - start) / (end - start) * 100:.0f} % ({time.time() - t0:.0f} s)")
    return None


def cmd_trim(a):
    lay = xci_layout(a.input)
    if lay is None:
        print("TRIM not-xci (only an XCI has padding to trim)")
        return
    m.find_program(m.container_files(a.input), m.load_keys(a.keys), int(a.program_id, 16), False)  # exit 3 if another edition
    cut, cart, size = lay
    if size <= cut:
        print(f"   already trimmed: {size:,} bytes, the data ends at {cut:#x}")
        print(f"TRIM saved=0 size={size}")
        return
    print(f"   data ends at {cut:#x}; {size - cut:,} bytes ({(size - cut) / (1 << 30):.2f} GiB) of padding after it")
    bad = check_padding(a.input, cut, size)
    if bad is not None:
        m.fail(f"NOT trimmed: the byte at {bad:#x} (after the data) is not 0xFF")
    if not a.apply:
        print(f"TRIM would_save={size - cut} size={cut}")
        return
    os.truncate(a.input, cut)
    if os.path.getsize(a.input) != cut:
        m.fail("the cut did not take")
    print(f"   trimmed: {size:,} -> {cut:,} bytes (romfs_source.py untrim undoes it)")
    print(f"TRIM saved={size - cut} size={cut}")


def cmd_untrim(a):
    lay = xci_layout(a.input)
    if lay is None:
        m.fail(f"{a.input} is not an XCI")
    cut, cart, size = lay
    if cart is None:
        m.fail("unknown cartridge size in the header")
    if size >= cart:
        print(f"UNTRIM nothing to do ({size:,} bytes, cartridge {cart:,})")
        return
    if not a.apply:
        print(f"UNTRIM would_add={cart - size} size={cart}")
        return
    with open(a.input, "r+b") as f:
        f.seek(size)
        left = cart - size
        while left:
            n = min(CHUNK, left)
            f.write(b"\xff" * n)
            left -= n
    print(f"UNTRIM added={cart - size} size={cart}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("info")
    p.add_argument("--keys", required=True)
    p.add_argument("--input", required=True)
    p = sp.add_parser("prepare")
    p.add_argument("--keys", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--program-id", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--build-romfs", required=True)
    p.add_argument("--exefs")
    p = sp.add_parser("locate")
    p.add_argument("--source", required=True)
    p.add_argument("--root", required=True)
    p = sp.add_parser("trim")
    p.add_argument("--keys", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--program-id", required=True)
    p.add_argument("--apply", action="store_true")
    p = sp.add_parser("untrim")
    p.add_argument("--input", required=True)
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    {"info": cmd_info, "prepare": cmd_prepare, "locate": cmd_locate, "trim": cmd_trim, "untrim": cmd_untrim}[a.cmd](a)


if __name__ == "__main__":
    main()
