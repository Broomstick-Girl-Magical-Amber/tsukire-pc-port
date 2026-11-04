#!/usr/bin/env python3
"""Probe a Tsukihime .nsp/.xci before reading its RomFS directly (no extraction).

Read-only on the dumps. For each .nsp/.xci in the port root (or --input) it reports:
  - container type, partitions and the NCAs inside (type, program ID, key generation)
  - for the base Program NCA, per section: filesystem, hash type, encryption,
    and whether a sparse or compression layer is present (the direct reader needs neither)
  - RomFS: where its data starts inside the NCA, file count, total size, and a
    decryption sanity check (MP4 files must start with 'ftyp', Opus files with 'OggS')
  - XCI only: where the valid data ends and how much trimming the 0xFF padding would save
    (--scan-padding also checks that everything after that point really is 0xFF)

Keys: <root>/data/prod.keys. If it is not there but <root>/prod.keys exists, that file is
MOVED into <root>/data/ (the project rule). --keys <file> uses that file and moves nothing.
No key is ever printed.

Usage (from the port root):
  python tools/dev/romfs_direct_probe.py
  python tools/dev/romfs_direct_probe.py --input tsukihime.xci --scan-padding --bench
"""

import argparse
import os
import shutil
import struct
import sys
import time
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    sys.exit("pycryptodome is missing: run this with the project venv (private/venv) or pip install pycryptodome")

ROOT_DEFAULT = Path(__file__).resolve().parents[2]  # <root>/tools/dev/this.py

EDITIONS = {
    0x01001DC01486A000: ("japan", 16729),
    0x010057A01E4D4000: ("global", 16835),
}
CONTENT_TYPES = {0: "Program", 1: "Meta", 2: "Control", 3: "Manual", 4: "Data", 5: "PublicData"}
FS_TYPES = {0: "RomFS", 1: "PartitionFS"}
HASH_TYPES = {0: "Auto", 1: "None", 2: "HierarchicalSha256", 3: "HierarchicalIntegrity (IVFC)",
              4: "AutoSha3", 5: "HierarchicalSha3256", 6: "HierarchicalIntegritySha3"}
ENC_TYPES = {0: "Auto", 1: "None", 2: "AesXts", 3: "AesCtr", 4: "AesCtrEx (BKTR patch)",
             5: "AesCtrSkipLayerHash", 6: "AesCtrExSkipLayerHash"}
KAK_NAMES = {0: "key_area_key_application", 1: "key_area_key_ocean", 2: "key_area_key_system"}
# XCI RomSize byte -> cartridge size in GB; a formatted "GB" is 952 MiB (an untrimmed dump has this size)
CART_GB = {0xFA: 1, 0xF8: 2, 0xF0: 4, 0xE0: 8, 0xE1: 16, 0xE2: 32}
MIB = 1024 * 1024
NO_ENTRY = 0xFFFFFFFF


class ProbeError(Exception):
    pass


# ---- crypto -------------------------------------------------------------------------------------------------

def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def xts(key, data, sector0, encrypt=False, sector_size=0x200):
    """AES-128-XTS with Nintendo's tweak (sector number, big-endian)."""
    k1 = AES.new(key[:16], AES.MODE_ECB)
    k2 = AES.new(key[16:32], AES.MODE_ECB)
    out = bytearray()
    for s in range(len(data) // sector_size):
        t = int.from_bytes(k2.encrypt((sector0 + s).to_bytes(16, "big")), "little")
        chunk = data[s * sector_size:(s + 1) * sector_size]
        for b in range(0, sector_size, 16):
            tb = t.to_bytes(16, "little")
            blk = _xor(chunk[b:b + 16], tb)
            blk = k1.encrypt(blk) if encrypt else k1.decrypt(blk)
            out += _xor(blk, tb)
            t <<= 1
            if t >> 128:
                t = (t & ((1 << 128) - 1)) ^ 0x87
    return bytes(out)


def ctr(key, section_ctr, nca_offset, data):
    """AES-128-CTR as the NCA uses it: upper 8 bytes from the section, lower 8 = absolute NCA offset / 16."""
    aligned = nca_offset & ~0xF
    skip = nca_offset - aligned
    c = AES.new(key, AES.MODE_CTR, nonce=section_ctr, initial_value=aligned >> 4)
    return c.decrypt(b"\0" * skip + data)[skip:]


def load_keys(path):
    keys = {}
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split(";")[0].split("#")[0].strip()
        if "=" not in line:
            continue
        name, value = (p.strip() for p in line.split("=", 1))
        try:
            keys[name.lower()] = bytes.fromhex(value)
        except ValueError:
            pass
    return keys


def locate_keys(root, explicit):
    """data/prod.keys; if missing there, move <root>/prod.keys into data/. Returns (path, note)."""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise ProbeError(f"--keys: {p} does not exist")
        return p, None
    data_keys = root / "data" / "prod.keys"
    root_keys = root / "prod.keys"
    if data_keys.is_file():
        note = None
        if root_keys.is_file():
            note = "there is also a prod.keys in the root; data/prod.keys is used and the root one is left as is"
        return data_keys, note
    if root_keys.is_file():
        data_keys.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root_keys), str(data_keys))
        return data_keys, "prod.keys was not in data/: moved it there from the root"
    raise ProbeError(f"prod.keys not found in {root / 'data'} nor in {root}")


# ---- containers ---------------------------------------------------------------------------------------------

class Reader:
    def __init__(self, path):
        self.path = Path(path)
        self.f = open(path, "rb")
        self.size = os.path.getsize(path)

    def read(self, off, size):
        self.f.seek(off)
        data = self.f.read(size)
        if len(data) != size:
            raise ProbeError(f"short read at 0x{off:x} ({len(data)} of {size} bytes): truncated file?")
        return data

    def close(self):
        self.f.close()


def parse_pfs0(r, off):
    magic, n, strsz = struct.unpack_from("<4sII", r.read(off, 0x10))
    if magic != b"PFS0":
        raise ProbeError(f"no PFS0 at 0x{off:x}")
    ent = r.read(off + 0x10, n * 0x18)
    strtab = r.read(off + 0x10 + n * 0x18, strsz)
    base = off + 0x10 + n * 0x18 + strsz
    files = []
    for i in range(n):
        eoff, esize, noff = struct.unpack_from("<QQI", ent, i * 0x18)
        name = strtab[noff:strtab.index(b"\0", noff)].decode("utf-8", "replace")
        files.append((name, base + eoff, esize))
    return files


def parse_hfs0(r, off):
    magic, n, strsz = struct.unpack_from("<4sII", r.read(off, 0x10))
    if magic != b"HFS0":
        raise ProbeError(f"no HFS0 at 0x{off:x}")
    ent = r.read(off + 0x10, n * 0x40)
    strtab = r.read(off + 0x10 + n * 0x40, strsz)
    base = off + 0x10 + n * 0x40 + strsz
    files = []
    for i in range(n):
        eoff, esize, noff = struct.unpack_from("<QQI", ent, i * 0x40)
        name = strtab[noff:strtab.index(b"\0", noff)].decode("utf-8", "replace")
        files.append((name, base + eoff, esize))
    return files


def container_kind(r):
    if r.size >= 4 and r.read(0, 4) == b"PFS0":
        return "NSP", 0
    for base in (0, 0x1000):  # 0x1000 = dump with the key area prepended
        if r.size >= base + 0x104 and r.read(base + 0x100, 4) == b"HEAD":
            return "XCI", base
    return None, 0


# ---- NCA ----------------------------------------------------------------------------------------------------

def read_nca(r, off, size, keys):
    if "header_key" not in keys:
        raise ProbeError("prod.keys has no header_key")
    hdr = xts(keys["header_key"], r.read(off, 0xC00), 0)
    if hdr[0x200:0x204] != b"NCA3":
        return {"ok": False, "magic": hdr[0x200:0x204]}
    gen = max(hdr[0x206], hdr[0x220])
    rev = gen - 1 if gen > 0 else 0
    nca = {
        "ok": True, "off": off, "size": size, "hdr": hdr,
        "content": hdr[0x205], "kak_index": hdr[0x207], "gen": gen,
        "program_id": struct.unpack_from("<Q", hdr, 0x210)[0],
        "rights_id": hdr[0x230:0x240],
        "kak_name": f"{KAK_NAMES.get(hdr[0x207], 'key_area_key_?')}_{rev:02x}",
        "sections": [],
    }
    for i in range(4):
        start, end = struct.unpack_from("<II", hdr, 0x240 + i * 0x10)
        if start == 0 and end == 0:
            continue
        fs = hdr[0x400 + i * 0x200:0x600 + i * 0x200]
        sec = {
            "index": i, "start": start * 0x200, "end": end * 0x200,
            "fs_type": fs[2], "hash_type": fs[3], "enc": fs[4],
            "ctr": fs[0x140:0x148][::-1],
            "sparse_table_size": struct.unpack_from("<Q", fs, 0x148 + 0x8)[0],
            "sparse_generation": struct.unpack_from("<H", fs, 0x148 + 0x28)[0],
            "compression_table_offset": struct.unpack_from("<Q", fs, 0x178)[0],
            "compression_table_size": struct.unpack_from("<Q", fs, 0x178 + 0x8)[0],
            "data_offset": None,
        }
        if fs[3] == 3 and fs[8:12] == b"IVFC":
            layers = struct.unpack_from("<I", fs, 0x14)[0]
            lvl = 0x18 + (layers - 2) * 0x18
            sec["data_offset"], sec["data_size"] = struct.unpack_from("<QQ", fs, lvl)
        nca["sections"].append(sec)
    return nca


def content_key(nca, keys):
    if any(nca["rights_id"]):
        raise ProbeError("the NCA uses a rights ID (title key): the direct reader would need the ticket")
    kak = keys.get(nca["kak_name"])
    if kak is None:
        raise ProbeError(f"prod.keys has no {nca['kak_name']} (key generation {nca['gen']})")
    area = AES.new(kak, AES.MODE_ECB).decrypt(nca["hdr"][0x300:0x340])
    return area[0x20:0x30]  # index 2 = AES-CTR key


# ---- RomFS --------------------------------------------------------------------------------------------------

class RomFs:
    def __init__(self, r, nca, sec, key):
        self.r, self.nca, self.sec, self.key = r, nca, sec, key
        self.base = sec["start"] + sec["data_offset"]  # offset of the RomFS inside the NCA

    def read(self, off, size):
        nca_off = self.base + off
        return ctr(self.key, self.sec["ctr"], nca_off, self.r.read(self.nca["off"] + nca_off, size))

    def files(self):
        h = struct.unpack_from("<10Q", self.read(0, 0x50))
        if h[0] != 0x50:
            raise ProbeError(f"RomFS header size 0x{h[0]:x} (0x50 expected): wrong key or offset")
        self.header = h
        dirs = self.read(h[3], h[4])
        files = self.read(h[7], h[8])
        data_off = h[9]
        out = []
        stack = [(0, "")]
        while stack:
            d, prefix = stack.pop()
            _, _, child_dir, child_file, _, nlen = struct.unpack_from("<6I", dirs, d)
            name = dirs[d + 0x18:d + 0x18 + nlen].decode("utf-8", "replace")
            path = f"{prefix}{name}/" if d != 0 else ""
            f = child_file
            while f != NO_ENTRY:
                _, sib, foff, fsize, _, fnlen = struct.unpack_from("<IIQQII", files, f)
                fname = files[f + 0x20:f + 0x20 + fnlen].decode("utf-8", "replace")
                out.append((path + fname, data_off + foff, fsize))
                f = sib
            c = child_dir
            while c != NO_ENTRY:
                stack.append((c, path))
                c = struct.unpack_from("<I", dirs, c + 4)[0]
        return out


# ---- report -------------------------------------------------------------------------------------------------

def gib(n):
    return f"{n / (1024 ** 3):.2f} GiB"


def check_xci_trim(r, base, partitions, scan, log):
    h = r.read(base + 0x100, 0x100)
    rom_size = h[0x0D]
    valid_pages = struct.unpack_from("<I", h, 0x18)[0]
    header_end = base + (valid_pages + 1) * 0x200
    data_end = max((o + s for _, o, s in partitions), default=0)
    for _, poff, _ in partitions:
        try:
            data_end = max([data_end] + [o + s for _, o, s in parse_hfs0(r, poff)])
        except ProbeError:
            pass
    cut = max(header_end, data_end)
    gb = CART_GB.get(rom_size)
    full = gb * 952 * MIB + base if gb else None
    log(f"  XCI: cartridge {gb or '?'} GB (RomSize 0x{rom_size:02X}), file {r.size:,} B"
        + (f", untrimmed size {full:,} B" if full else ""))
    log(f"  XCI: valid data ends at 0x{header_end:x} per the header; last partition byte at 0x{data_end:x}")
    if r.size <= cut:
        log("  XCI: already trimmed (nothing after the valid data)")
        return
    log(f"  XCI: trimming at 0x{cut:x} would save {r.size - cut:,} B ({gib(r.size - cut)}); "
        f"trimmed size {cut:,} B ({gib(cut)})")
    if not scan:
        log("  XCI: padding not checked (use --scan-padding before trimming)")
        return
    t0 = time.time()
    off = cut
    ff = b"\xff" * (8 * MIB)
    while off < r.size:
        n = min(8 * MIB, r.size - off)
        chunk = r.read(off, n)
        if chunk != ff[:n]:
            bad = next(i for i, b in enumerate(chunk) if b != 0xFF)
            log(f"  XCI: NOT safe to trim: byte 0x{chunk[bad]:02x} at 0x{off + bad:x}")
            return
        off += n
    log(f"  XCI: padding checked, all 0xFF ({time.time() - t0:.1f} s): safe to trim")


def probe(path, keys, args, log):
    r = Reader(path)
    try:
        kind, base = container_kind(r)
        if kind is None:
            log(f"{path.name}: not an NSP (PFS0) nor an XCI (HEAD): skipped")
            return False
        log(f"{path.name}: {kind}, {r.size:,} B ({gib(r.size)})" + (" [key area prepended]" if base else ""))
        partitions = []
        if kind == "NSP":
            entries = parse_pfs0(r, 0)
        else:
            hfs_off = base + struct.unpack_from("<Q", r.read(base + 0x130, 8))[0]
            partitions = parse_hfs0(r, hfs_off)
            log("  partitions: " + ", ".join(f"{n} ({gib(s)})" for n, _, s in partitions))
            secure = [p for p in partitions if p[0] == "secure"]
            if not secure:
                raise ProbeError("XCI without a secure partition")
            entries = parse_hfs0(r, secure[0][1])

        base_nca = None
        for name, off, size in entries:
            if not name.endswith(".nca"):
                log(f"  {name}: {gib(size)} (not an NCA)")
                continue
            nca = read_nca(r, off, size, keys)
            if not nca["ok"]:
                log(f"  {name}: not NCA3 after decrypting the header (magic {nca['magic']!r}): wrong header_key?")
                continue
            ed = EDITIONS.get(nca["program_id"])
            ctype = CONTENT_TYPES.get(nca["content"], str(nca["content"]))
            patch = any(s["enc"] in (4, 6) for s in nca["sections"])
            log(f"  {name}: {ctype}, program {nca['program_id']:016X}, {gib(size)}, key gen {nca['gen']}"
                + (", rights ID" if any(nca["rights_id"]) else "") + (", patch (BKTR)" if patch else ""))
            if nca["content"] == 0 and ed and not patch:
                base_nca = (name, nca, ed)
        if base_nca is None:
            log("  no base Program NCA of a known Tsukihime edition")
            return False

        name, nca, (edition, expected) = base_nca
        log(f"  base Program NCA: {name} -> edition {edition}")
        romfs_sec = None
        for s in nca["sections"]:
            layers = []
            if s["sparse_generation"] or s["sparse_table_size"]:
                layers.append(f"SPARSE (generation {s['sparse_generation']}, table {s['sparse_table_size']} B)")
            if s["compression_table_size"]:
                layers.append(f"COMPRESSION (table {s['compression_table_size']} B)")
            log(f"    section {s['index']}: {FS_TYPES.get(s['fs_type'], s['fs_type'])}, "
                f"{HASH_TYPES.get(s['hash_type'], s['hash_type'])}, {ENC_TYPES.get(s['enc'], s['enc'])}, "
                f"0x{s['start']:x}-0x{s['end']:x} ({gib(s['end'] - s['start'])}); "
                + (", ".join(layers) if layers else "no sparse/compression layer"))
            if s["fs_type"] == 0:
                romfs_sec = s
        if romfs_sec is None or romfs_sec["data_offset"] is None:
            log("  RomFS section not found or without IVFC data level")
            return False
        ok = romfs_sec["enc"] == 3 and not romfs_sec["compression_table_size"] and not (
            romfs_sec["sparse_generation"] or romfs_sec["sparse_table_size"])

        key = content_key(nca, keys)
        fs = RomFs(r, nca, romfs_sec, key)
        files = fs.files()
        total = sum(s for _, _, s in files)
        log(f"  RomFS: data level at NCA+0x{fs.base:x}, files at RomFS+0x{fs.header[9]:x}; "
            f"{len(files)} files ({'as expected' if len(files) == expected else f'{expected} expected'}), "
            f"{gib(total)}")

        # files must not overlap (each one is a contiguous range inside the dump)
        spans = sorted((o, s) for _, o, s in files if s)
        overlaps = sum(1 for (a, sa), (b, _) in zip(spans, spans[1:]) if a + sa > b)
        log(f"  RomFS: {'no overlapping files' if not overlaps else f'{overlaps} overlapping files'}")

        # MP4: 'ftyp' at byte 4. Switch .opus is Nintendo's container (u32 LE 0x80000001 at byte 0), not Ogg.
        checks = [(".mp4", "'ftyp'", lambda b: b[4:8] == b"ftyp"),
                  (".opus", "Nintendo Opus 0x80000001 or 'OggS'", lambda b: b[0:4] in (b"\x01\x00\x00\x80", b"OggS"))]
        bad = 0
        for ext, what, test in checks:
            sample = [f for f in files if f[0].lower().endswith(ext) and f[2] >= 16]
            sample = sample[:3] + sample[3:][-3:]
            failed = 0
            for p, o, s in sample:
                head = fs.read(o, 16)
                if not test(head):
                    failed += 1
                    log(f"  check FAILED: {p} does not start as {what} (first bytes {head[:8].hex()})")
            if sample and not failed:
                log(f"  check: {len(sample)} {ext} files decrypted correctly ({what})")
            bad += failed
        ok = ok and not bad and not overlaps

        if args.bench:
            p, o, s = max(files, key=lambda f: f[2])
            t0 = time.time()
            raw = 0.0
            pos = 0
            while pos < s:
                n = min(8 * MIB, s - pos)
                t1 = time.time()
                data = r.read(nca["off"] + fs.base + o + pos, n)
                raw += time.time() - t1
                ctr(key, romfs_sec["ctr"], fs.base + o + pos, data)
                pos += n
            dt = time.time() - t0
            log(f"  bench: {p} ({s / MIB:.0f} MiB) read+decrypt {dt:.2f} s ({s / MIB / dt:.0f} MiB/s); "
                f"of that, disk {raw:.2f} s")

        if kind == "XCI":
            check_xci_trim(r, base, partitions, args.scan_padding, log)
        log(f"  RESULT: {'direct read possible' if ok else 'direct read NOT possible as designed (see above)'}")
        return ok
    finally:
        r.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Probe .nsp/.xci dumps for direct RomFS reading (read-only).")
    ap.add_argument("--root", default=str(ROOT_DEFAULT), help="port root (default: two folders above this script)")
    ap.add_argument("--input", action="append", help="dump to probe (repeatable; default: every .nsp/.xci in the root)")
    ap.add_argument("--keys", help="prod.keys to use as is (default: data/prod.keys, moved there from the root if needed)")
    ap.add_argument("--scan-padding", action="store_true", help="XCI: check that everything after the valid data is 0xFF")
    ap.add_argument("--bench", action="store_true", help="time reading and decrypting the largest RomFS file")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    log = print
    try:
        keys_path, note = locate_keys(root, args.keys)
        if note:
            log(f"keys: {note}")
        keys = load_keys(keys_path)
        if args.input:
            dumps = [Path(p) if Path(p).is_absolute() else root / p for p in args.input]
        else:
            dumps = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in (".nsp", ".xci"))
        if not dumps:
            log(f"no .nsp/.xci in {root}")
            return 2
        results = []
        for d in dumps:
            try:
                results.append(probe(d, keys, args, log))
            except ProbeError as e:
                log(f"  ERROR: {e}")
                results.append(False)
            log("")
        return 0 if all(results) else 1
    except ProbeError as e:
        log(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
