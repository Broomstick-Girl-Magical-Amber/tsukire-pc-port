#!/usr/bin/env python3
"""Synthetic test for tools/dev/romfs_direct_probe.py (no game data, made-up keys).

Builds a small Program NCA (exefs PartitionFS + RomFS with IVFC data level, AES-CTR, key area,
XTS header), wraps it as an NSP and as an XCI with 0xFF padding, and checks the probe's report:
keys moved from the root into data/, RomFS walk and decryption, trim size and padding scan,
and the rejections (sparse layer, wrong header key, non-0xFF byte in the padding).

Run: python tools/tests/test_romfs_direct_probe.py
"""

import io
import os
import struct
import sys
import tempfile
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
import romfs_direct_probe as probe  # noqa: E402
from Crypto.Cipher import AES  # noqa: E402

PROGRAM_ID = 0x01001DC01486A000  # japanese edition ID, only so the probe accepts it
HEADER_KEY = bytes(range(0x20, 0x40))
KAK = bytes(range(0x40, 0x50))
CONTENT_KEY = bytes(range(0x60, 0x70))
SECTION_CTR = bytes.fromhex("0000000100000002")  # as stored in the fs header (little-endian u64)


def pad(b, a):
    return b + b"\0" * (-len(b) % a)


def build_romfs(files):
    """files: {"dir/name": bytes} with one level of directories. Returns RomFS bytes."""
    dirs = sorted({p.split("/")[0] for p in files})
    data = bytearray()
    offsets = {}
    for p in sorted(files):
        data = bytearray(pad(bytes(data), 0x10))
        offsets[p] = len(data)
        data += files[p]
    # directory entries: root at 0, then one per dir
    dir_entries, dir_off = [], {}
    pos = 0x18  # root entry: 0x18 + empty name
    for d in dirs:
        dir_off[d] = pos
        pos += 0x18 + len(pad(d.encode(), 4))
    # file entries
    file_off, pos = {}, 0
    for p in sorted(files):
        file_off[p] = pos
        pos += 0x20 + len(pad(p.split("/")[1].encode(), 4))
    none = 0xFFFFFFFF
    dm = bytearray(struct.pack("<6I", 0, none, dir_off[dirs[0]], none, none, 0))
    for i, d in enumerate(dirs):
        sib = dir_off[dirs[i + 1]] if i + 1 < len(dirs) else none
        first = [p for p in sorted(files) if p.split("/")[0] == d][0]
        dm += struct.pack("<6I", 0, sib, none, file_off[first], none, len(d.encode())) + pad(d.encode(), 4)
    fm = bytearray()
    for d in dirs:
        group = [p for p in sorted(files) if p.split("/")[0] == d]
        for i, p in enumerate(group):
            sib = file_off[group[i + 1]] if i + 1 < len(group) else none
            name = p.split("/")[1].encode()
            fm += struct.pack("<IIQQII", dir_off[d], sib, offsets[p], len(files[p]), none, len(name)) + pad(name, 4)
    data_off = 0x200
    meta_off = data_off + len(pad(bytes(data), 0x10))
    dh_off, dh = meta_off, b"\xff" * 4
    dm_off = dh_off + 4
    fh_off = dm_off + len(dm)
    fh = b"\xff" * 4
    fm_off = fh_off + 4
    hdr = struct.pack("<10Q", 0x50, dh_off, 4, dm_off, len(dm), fh_off, 4, fm_off, len(fm), data_off)
    out = bytearray(pad(hdr, 0x200)) + pad(bytes(data), 0x10) + dh + dm + fh + fm
    return bytes(out)


def build_nca(romfs, sparse=False):
    exefs = pad(struct.pack("<4sII", b"PFS0", 0, 0) + b"\0" * 4, 0x200)
    romfs_level = 0x4000  # IVFC data level offset inside the section
    sec0_start = 0xC00
    sec1_start = sec0_start + len(exefs)
    sec1 = pad(b"\0" * romfs_level + romfs, 0x200)
    hdr = bytearray(0xC00)
    hdr[0x200:0x204] = b"NCA3"
    hdr[0x205] = 0  # Program
    hdr[0x206] = 2
    hdr[0x207] = 0
    struct.pack_into("<Q", hdr, 0x208, sec1_start + len(sec1))
    struct.pack_into("<Q", hdr, 0x210, PROGRAM_ID)
    hdr[0x220] = 17  # key generation 17 -> key_area_key_application_10
    struct.pack_into("<II", hdr, 0x240, sec0_start // 0x200, sec1_start // 0x200)
    struct.pack_into("<II", hdr, 0x250, sec1_start // 0x200, (sec1_start + len(sec1)) // 0x200)
    area = b"\0" * 0x20 + CONTENT_KEY + b"\0" * 0x10
    hdr[0x300:0x340] = AES.new(KAK, AES.MODE_ECB).encrypt(area)
    for i, (fs_type, hash_type) in enumerate([(1, 2), (0, 3)]):
        fs = bytearray(0x200)
        struct.pack_into("<HBBB", fs, 0, 2, fs_type, hash_type, 3)
        fs[0x140:0x148] = SECTION_CTR
        if hash_type == 3:
            fs[0x8:0xC] = b"IVFC"
            struct.pack_into("<III", fs, 0xC, 0x20000, 0x20, 7)
            struct.pack_into("<QQI", fs, 0x18 + 5 * 0x18, romfs_level, len(romfs), 14)
            if sparse:
                struct.pack_into("<Q", fs, 0x148 + 8, 0x40)
                struct.pack_into("<H", fs, 0x148 + 0x28, 1)
        hdr[0x400 + i * 0x200:0x600 + i * 0x200] = fs
    upper = SECTION_CTR[::-1]
    body = probe.ctr(CONTENT_KEY, upper, sec0_start, exefs) + probe.ctr(CONTENT_KEY, upper, sec1_start, sec1)
    return probe.xts(HEADER_KEY, bytes(hdr), 0, encrypt=True) + body


def build_pfs0(files):
    strtab = b"".join(n.encode() + b"\0" for n, _ in files)
    ent, pos, noff = b"", 0, 0
    for n, d in files:
        ent += struct.pack("<QQII", pos, len(d), noff, 0)
        pos += len(d)
        noff += len(n) + 1
    strtab = pad(strtab, 0x20)
    return struct.pack("<4sII", b"PFS0", len(files), len(strtab)) + b"\0" * 4 + ent + strtab + b"".join(d for _, d in files)


def build_hfs0(files):
    strtab = b"".join(n.encode() + b"\0" for n, _ in files)
    ent, pos, noff = b"", 0, 0
    for n, d in files:
        ent += struct.pack("<QQII", pos, len(d), noff, 0) + b"\0" * 8 + b"\0" * 0x20
        pos += len(d)
        noff += len(n) + 1
    strtab = pad(strtab, 0x20)
    return struct.pack("<4sII", b"HFS0", len(files), len(strtab)) + b"\0" * 4 + ent + strtab + b"".join(d for _, d in files)


def build_xci(nca, padding):
    secure = build_hfs0([("abcd.nca", nca)])
    root = build_hfs0([("update", build_hfs0([])), ("secure", secure)])
    hfs_off = 0xF000
    head = bytearray(0x200)
    head[0x100:0x104] = b"HEAD"
    head[0x10D] = 0xFA
    data_end = hfs_off + len(root)
    struct.pack_into("<I", head, 0x118, (data_end + 0x1FF) // 0x200 - 1)
    struct.pack_into("<QQ", head, 0x130, hfs_off, 0x200)
    img = bytes(head) + b"\0" * (hfs_off - 0x200) + root
    img = img + b"\xff" * (-len(img) % 0x200)
    return img + b"\xff" * padding


FILES = {
    "movie/a.mp4": b"\0\0\0\x18ftypisom" + os.urandom(3000),
    "movie/b.mp4": b"\0\0\0\x18ftypmp42" + os.urandom(70000),
    "stream/v.opus": b"OggS" + os.urandom(500),
    "stream/w.opus": struct.pack("<II", 0x80000001, 0x18) + os.urandom(800),
}


def run(root, *extra):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = probe.main(["--root", str(root), *extra])
    return code, buf.getvalue()


def write_keys(path, header_key=HEADER_KEY):
    path.write_text(f"header_key = {header_key.hex()}\nkey_area_key_application_10 = {KAK.hex()}\n")


def main():
    fails = 0

    def check(cond, what, out=""):
        nonlocal fails
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            fails += 1
            print(out)

    romfs = build_romfs(FILES)
    nca = build_nca(romfs)
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        (root / "game.nsp").write_bytes(build_pfs0([("abcd.nca", nca), ("abcd.tik", b"x" * 0x2C0)]))
        write_keys(root / "prod.keys")
        code, out = run(root)
        check(code == 0 and "direct read possible" in out, "NSP: direct read possible", out)
        check((root / "data" / "prod.keys").is_file() and not (root / "prod.keys").exists(), "keys moved from the root to data/", out)
        check("moved it there from the root" in out, "the move is reported", out)
        check("4 files" in out and "2 .mp4 files decrypted correctly" in out and "2 .opus files decrypted correctly" in out,
              "RomFS walked and decrypted", out)
        check(HEADER_KEY.hex() not in out and KAK.hex() not in out and CONTENT_KEY.hex() not in out, "no key printed", out)
        check("no sparse/compression layer" in out, "no sparse/compression reported", out)

        write_keys(root / "prod.keys")
        code, out = run(root)
        check("also a prod.keys in the root" in out and (root / "prod.keys").exists(), "keys in both places: data/ used, root left", out)
        (root / "prod.keys").unlink()

        (root / "game.nsp").unlink()
        xci = build_xci(nca, 3 * 1024 * 1024)
        (root / "game.xci").write_bytes(xci)
        code, out = run(root, "--scan-padding", "--bench")
        check(code == 0 and "safe to trim" in out and f"save {3 * 1024 * 1024:,} B" in out, "XCI: trim size and padding scan", out)
        check("bench:" in out, "bench line", out)

        bad = bytearray(xci)
        bad[-1000] = 0x00
        (root / "game.xci").write_bytes(bytes(bad))
        code, out = run(root, "--scan-padding")
        check("NOT safe to trim" in out, "XCI: non-0xFF byte in the padding detected", out)

        (root / "game.xci").write_bytes(build_xci(nca, 0))
        code, out = run(root)
        check("already trimmed" in out, "XCI already trimmed", out)

        (root / "game.xci").unlink()
        (root / "game.nsp").write_bytes(build_pfs0([("abcd.nca", build_nca(romfs, sparse=True))]))
        code, out = run(root)
        check(code == 1 and "SPARSE" in out and "NOT possible" in out, "sparse layer rejected", out)

        write_keys(root / "data" / "prod.keys", header_key=bytes(32))
        code, out = run(root)
        check(code == 1 and "wrong header_key" in out, "wrong header key reported", out)

        (root / "data" / "prod.keys").unlink()
        code, out = run(root)
        check(code == 1 and "prod.keys not found" in out, "missing keys reported", out)

    print("all passed" if not fails else f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
