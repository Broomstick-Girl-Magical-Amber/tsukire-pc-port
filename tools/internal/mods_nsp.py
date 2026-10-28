"""mods_nsp.py - Updates and patches (.nsp in <root>\\updates\\) without keeping the base dump (used by recomp_tsukihime.ps1).

A patch NSP (an update, or a translation built as an update) carries a program NCA whose romfs is BKTR: a table that
says, for each range of the patched romfs, whether the bytes come from the BASE romfs or from the patch itself. Tools
like nstool or LibHac need the base NCA for that. Here the base romfs is rebuilt virtually from what the port already
has: the files of data\\<edition>\\extract\\romfs plus a small index (romfs_index.bin, a few hundred KB) with where each
file was inside the base romfs image, the metadata tables and any non-zero padding. The index is made ONCE from the
dump (step "index"); after that the dump can be deleted and any number of patches can be processed.

Commands:
  index  --keys K --input <base .xci/.nsp> --program-id ID --base-romfs <extract\\romfs> --out <romfs_index.bin>
         Reads the base program NCA of the dump (only the romfs metadata and the gaps between files; the files are
         checked by size against --base-romfs) and writes the index.
  mod    --keys K --nsp <patch.nsp> --program-id ID --index <romfs_index.bin> --base-romfs <extract\\romfs>
         --base-exefs <exefs> --out <new folder> [--verify-csv <R,L,H csv of the full patched romfs>]
         Writes <out>\\exefs (the patch's exefs, if it has one), <out>\\romfs (ONLY the files that differ from the base
         or are new) and <out>\\mod.json. The last line of the output is "MOD key=value ..." (read by the ps1).
         Exit code 3 (and a "SKIP" line) when the NSP has no program NCA of --program-id (another game or edition).
         --verify-csv: compares the MD5 of EVERY file of the patched romfs (unchanged ones read from the base files)
         against a "R","L","H" csv (Get-FileHash -Algorithm MD5 of a romfs extracted with nstool --basenca); slow
         (reads the whole romfs), meant to validate this tool once.

Formats (NCA3): header AES-XTS (header_key, 0x200 sectors, big-endian sector number as tweak); FS headers at 0x400;
sections AES-CTR (upper 8 bytes of the counter = the FS header's +0x140 as a little-endian u64, written big-endian;
lower 8 = offset in the NCA >> 4); BKTR: IndirectStorage (entries virtual u64, physical u64, storage s32; 0 = base, 1 = patch)
and AesCtrCounterExtended (entries offset u64, encryption u8, generation s32 at +0xC; the generation replaces the low
32 bits of the counter's upper half), both as bucket trees of 0x4000 nodes read with the plain section counter.
Needs pycryptodome and lz4 (tools\\requirements.txt). Never prints keys.
"""
import argparse
import base64
import bisect
import csv
import hashlib
import json
import os
import shutil
import struct
import sys
import zlib

try:
    from Crypto.Cipher import AES
except ImportError:
    sys.exit("ERROR: pycryptodome is missing in the venv (pip install -r tools\\requirements.txt)")

MEDIA = 0x200
NODE = 0x4000
CHUNK = 8 << 20
INDEX_VERSION = 1


def fail(msg):
    sys.exit("ERROR: " + msg)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


# ---------------------------------------------------------------------------------------------------------- keys
def load_keys(path):
    keys = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            k, v = (s.strip() for s in line.split("=", 1))
            try:
                keys[k.lower()] = bytes.fromhex(v)
            except ValueError:
                pass
    return keys


def need_key(keys, name, size=16):
    k = keys.get(name)
    if not k or len(k) != size:
        fail(f"the keys file does not have {name}")
    return k


# ---------------------------------------------------------------------------------------------------------- crypto
def _xor(a, b):
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(len(a), "little")


def xts_decrypt(key32, data, sector, sector_size=0x200):
    """AES-128-XTS with Nintendo's tweak (sector number big-endian)."""
    k1, k2 = AES.new(key32[:16], AES.MODE_ECB), AES.new(key32[16:], AES.MODE_ECB)
    out = bytearray()
    for s in range(len(data) // sector_size):
        t = int.from_bytes(k2.encrypt((sector + s).to_bytes(16, "big")), "little")
        blk = data[s * sector_size:(s + 1) * sector_size]
        for j in range(0, sector_size, 16):
            tb = t.to_bytes(16, "little")
            out += _xor(k1.decrypt(_xor(blk[j:j + 16], tb)), tb)
            t <<= 1
            if t >> 128:
                t = (t & ((1 << 128) - 1)) ^ 0x87
    return bytes(out)


def ctr_crypt(key, upper8, offset, data):
    """offset multiple of 16."""
    iv = upper8 + (offset >> 4).to_bytes(8, "big")
    return AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=iv).decrypt(data)


# ---------------------------------------------------------------------------------------------------------- containers
class Source:
    """A byte range of a file."""

    def __init__(self, fh, offset, size):
        self.fh, self.offset, self.size = fh, offset, size

    def sub(self, off, size):
        return Source(self.fh, self.offset + off, size)

    def read(self, off, n):
        if off < 0 or off + n > self.size:
            fail(f"read outside the container ({off:#x}+{n:#x} > {self.size:#x})")
        self.fh.seek(self.offset + off)
        b = self.fh.read(n)
        if len(b) != n:
            fail("short read (truncated file?)")
        return b


def partition_fs(src, magic, entry_size):
    h = src.read(0, 0x10)
    if h[:4] != magic:
        return None
    count, strsize = u32(h, 4), u32(h, 8)
    if count > 100000:
        fail("corrupt partition header")
    tab = src.read(0x10, count * entry_size + strsize)
    strs = tab[count * entry_size:]
    data = 0x10 + count * entry_size + strsize
    out = {}
    for i in range(count):
        e = tab[i * entry_size:(i + 1) * entry_size]
        off, size, name_off = u64(e, 0), u64(e, 8), u32(e, 16)
        end = strs.index(b"\0", name_off)
        out[strs[name_off:end].decode("utf-8", "replace")] = src.sub(data + off, size)
    return out


def container_files(path):
    """name -> Source. NSP: the root PFS0; XCI: the secure partition."""
    fh = open(path, "rb")
    fh.seek(0, 2)
    whole = Source(fh, 0, fh.tell())
    files = partition_fs(whole, b"PFS0", 0x18)
    if files is not None:
        return files
    hdr = whole.read(0, 0x200)
    if hdr[0x100:0x104] != b"HEAD":
        fail(f"{path} is neither an NSP (PFS0) nor an XCI (HEAD)")
    root_off = u64(hdr, 0x130)
    root = partition_fs(Source(fh, root_off, whole.size - root_off), b"HFS0", 0x40)
    if not root or "secure" not in root:
        fail(f"{path}: XCI without a secure partition")
    sec = partition_fs(root["secure"], b"HFS0", 0x40)
    if sec is None:
        fail(f"{path}: corrupt secure partition")
    return sec


# ---------------------------------------------------------------------------------------------------------- NCA
class Nca:
    def __init__(self, src, keys, titlekeys):
        self.src = src
        raw = src.read(0, 0xC00)
        h = xts_decrypt(need_key(keys, "header_key", 32), raw, 0)
        if h[0x200:0x204] != b"NCA3":
            raise ValueError("not an NCA3 (or wrong header_key)")
        self.content_type = h[0x205]
        self.title_id = u64(h, 0x210)
        gen = max(h[0x206], h[0x220])
        gen = gen - 1 if gen > 0 else 0
        self.rights_id = h[0x230:0x240]
        self.sections, self.fsh = {}, {}
        for i in range(4):
            start, end = u32(h, 0x240 + i * 0x10), u32(h, 0x244 + i * 0x10)
            if end > start:
                self.sections[i] = (start * MEDIA, (end - start) * MEDIA)
                self.fsh[i] = h[0x400 + i * 0x200:0x600 + i * 0x200]
        if any(self.rights_id):
            rid = self.rights_id.hex()
            if rid not in titlekeys:
                raise ValueError(f"rights ID {rid} without a ticket in the container")
            kek = need_key(keys, f"titlekek_{gen:02x}")
            self.key = AES.new(kek, AES.MODE_ECB).decrypt(titlekeys[rid])
        else:
            kak = need_key(keys, f"key_area_key_application_{gen:02x}")
            self.key = AES.new(kak, AES.MODE_ECB).decrypt(h[0x300:0x340])[0x20:0x30]

    def section(self, i):
        if i not in self.sections:
            fail(f"the NCA has no section {i}")
        return Section(self, i)


class Section:
    def __init__(self, nca, i):
        self.nca = nca
        self.start, self.size = nca.sections[i]
        f = nca.fsh[i]
        self.fsh = f
        self.fs_type, self.hash_type, self.enc = f[2], f[3], f[4]
        self.upper = u64(f, 0x140).to_bytes(8, "big")
        if u64(f, 0x150) != 0:  # SparseInfo: table size
            fail("sparse NCA section: not supported")
        if u64(f, 0x180) != 0:  # CompressionInfo: table size
            fail("compressed NCA section: not supported")
        if self.enc not in (1, 3, 4, 5, 6):
            fail(f"NCA section encryption type {self.enc} not supported")

    @property
    def bktr(self):
        return self.enc in (4, 6)

    def raw(self, off, n):
        return self.nca.src.read(self.start + off, n)

    def read(self, off, n, generation=None):
        """Plain section read (CTR with the header counter, or the given generation)."""
        if n <= 0:
            return b""
        if self.enc == 1:
            return self.raw(off, n)
        a = off & ~0xF
        pre = off - a
        ln = (pre + n + 15) & ~0xF
        ln = min(ln, self.size - a)
        upper = self.upper if generation is None else self.upper[:4] + generation.to_bytes(4, "big")
        # The counter counts from the start of the NCA, not of the section.
        return ctr_crypt(self.nca.key, upper, self.start + a, self.raw(a, ln))[pre:pre + n]

    def ivfc_data(self):
        if self.hash_type != 3 or self.fsh[8:12] != b"IVFC":
            fail("romfs section without IVFC")
        return u64(self.fsh, 0x90), u64(self.fsh, 0x98)

    def pfs0_region(self):
        if self.hash_type != 2:
            fail("exefs section without HierarchicalSha256")
        return u64(self.fsh, 0x40), u64(self.fsh, 0x48)

    def patch_info(self):
        f = self.fsh

        def tree(o):
            hdr = f[o + 0x10:o + 0x20]
            if hdr[:4] != b"BKTR":
                fail("BKTR section without its table header")
            return u64(f, o), u64(f, o + 8), struct.unpack_from("<i", hdr, 8)[0]
        return tree(0x100), tree(0x120)


def read_bucket_tree(sec, info, entry_size, parse, name):
    """Entries of a bucket tree (table at info = (offset, size, header count)) and its end offset.
    Like LibHac, the sets come from the L1 node and the entries of each set from its own node header; the count in the
    FS header is only cross-checked (some tools that build patches write it differently: a note, not an error)."""
    off, size, count = info
    if count == 0 and size == 0:
        return [], 0
    per = (NODE - 0x10) // entry_size
    t = sec.read(off, size)
    if len(t) < NODE:
        fail(f"{name} bucket tree: shorter than one node")
    sets, end = u32(t, 4), u64(t, 8)
    if sets == 0 or sets > (NODE - 0x10) // 8:
        fail(f"{name} bucket tree: {sets} entry sets (L2 levels are not supported)")
    if len(t) < NODE * (1 + sets):
        fail(f"{name} bucket tree: {sets} entry sets do not fit in its {size:#x} bytes")
    out = []
    for k in range(sets):
        b = NODE * (1 + k)
        idx, n = u32(t, b), u32(t, b + 4)
        if idx != k or n > per:
            fail(f"{name} bucket tree: corrupt entry set {k} (index {idx}, {n} entries)")
        for j in range(n):
            out.append(parse(t, b + 0x10 + j * entry_size))
    starts = [e[0] for e in out]
    if starts != sorted(starts) or (out and (starts[0] != 0 or starts[-1] >= end)):
        fail(f"{name} bucket tree: entries out of order or outside 0..{end:#x}")
    if len(out) != count:
        print(f"   note: {name} table with {len(out)} entries, its header says {count} (read like LibHac does)")
    return out, end


# ---------------------------------------------------------------------------------------------------------- romfs
def romfs_files(read):
    """[(data offset in the romfs, size, 'dir/name')] from a romfs image reader."""
    h = read(0, 0x50)
    if u64(h, 0) != 0x50:
        fail("romfs header with an unexpected size")
    dmeta = read(u64(h, 0x18), u64(h, 0x20))
    fmeta = read(u64(h, 0x38), u64(h, 0x40))
    data_off = u64(h, 0x48)
    dirs = {}

    def dir_path(o):
        if o in dirs:
            return dirs[o]
        parent, nlen = u32(dmeta, o), u32(dmeta, o + 0x14)
        name = dmeta[o + 0x18:o + 0x18 + nlen].decode("utf-8")
        p = name if o == 0 else ((dir_path(parent) + "/" if parent != 0 else "") + name)
        dirs[o] = p
        return p

    out = []
    o = 0
    while o + 0x20 <= len(fmeta):
        parent, off, size, nlen = u32(fmeta, o), u64(fmeta, o + 8), u64(fmeta, o + 0x10), u32(fmeta, o + 0x1C)
        name = fmeta[o + 0x20:o + 0x20 + nlen].decode("utf-8")
        d = dir_path(parent)
        out.append((data_off + off, size, (d + "/" if d else "") + name))
        o += 0x20 + ((nlen + 3) & ~3)
    return out, h


def to_host(base, rel):
    return os.path.join(base, *rel.split("/"))


# ---------------------------------------------------------------------------------------------------------- base romfs
class BaseRomfs:
    """The base romfs image, rebuilt from the extracted files + the index."""

    def __init__(self, index, base_dir):
        self.l6_off, self.l6_size = index["l6_offset"], index["l6_size"]
        self.files = sorted((f[0], f[1], f[2]) for f in index["files"] if f[1] > 0)
        self.starts = [f[0] for f in self.files]
        self.extras = sorted((e[0], base64.b64decode(e[1])) for e in index["extras"])
        self.extra_starts = [e[0] for e in self.extras]
        self.dir = base_dir
        self.handles = {}

    def _fh(self, rel):
        fh = self.handles.get(rel)
        if fh is None:
            if len(self.handles) > 64:
                for h in self.handles.values():
                    h.close()
                self.handles.clear()
            fh = self.handles[rel] = open(to_host(self.dir, rel), "rb")
        return fh

    def _gap(self, a, b):
        out = bytearray(b - a)
        k = max(0, bisect.bisect_right(self.extra_starts, a) - 1)
        while k < len(self.extras) and self.extras[k][0] < b:
            eo, eb = self.extras[k]
            lo, hi = max(a, eo), min(b, eo + len(eb))
            if lo < hi:
                out[lo - a:hi - a] = eb[lo - eo:hi - eo]
            k += 1
        return bytes(out)

    def read_romfs(self, off, n):
        out = bytearray()
        end = off + n
        while off < end:
            k = bisect.bisect_right(self.starts, off) - 1
            if k >= 0 and off < self.files[k][0] + self.files[k][1]:
                fo, fs, rel = self.files[k]
                m = min(end, fo + fs) - off
                fh = self._fh(rel)
                fh.seek(off - fo)
                b = fh.read(m)
                if len(b) != m:
                    fail(f"base file {rel} is shorter than in the index (extract\\romfs changed?)")
                out += b
            else:
                nxt = self.starts[k + 1] if k + 1 < len(self.starts) else self.l6_size
                m = min(end, nxt) - off
                out += self._gap(off, off + m)
            off += m
        return bytes(out)

    def read_section(self, off, n):
        r = off - self.l6_off
        if r < 0 or r + n > self.l6_size:
            fail(f"the patch reads the base outside its romfs (section {off:#x}+{n:#x}): not supported without the dump")
        return self.read_romfs(r, n)


# ---------------------------------------------------------------------------------------------------------- patched romfs
class Patched:
    """Virtual section of a BKTR NCA (base + patch)."""

    def __init__(self, sec, base):
        self.sec, self.base = sec, base
        rinfo, cinfo = sec.patch_info()
        self.rel, self.vsize = read_bucket_tree(
            sec, rinfo, 0x14, lambda t, o: (u64(t, o), u64(t, o + 8), struct.unpack_from("<i", t, o + 0x10)[0]), "relocation")
        self.rel_starts = [e[0] for e in self.rel]
        self.ctr, self.ctr_end = read_bucket_tree(
            sec, cinfo, 0x10, lambda t, o: (u64(t, o), t[o + 8], struct.unpack_from("<i", t, o + 0xC)[0]), "AesCtrEx")
        self.ctr_starts = [e[0] for e in self.ctr]

    def _patch(self, off, n):
        out = bytearray()
        end = off + n
        while off < end:
            k = bisect.bisect_right(self.ctr_starts, off) - 1
            if k < 0:
                fail(f"patch data at {off:#x} without an AesCtrEx entry")
            e_end = self.ctr_starts[k + 1] if k + 1 < len(self.ctr) else self.ctr_end
            m = min(end, e_end) - off
            if m <= 0:
                fail(f"patch data at {off:#x} past the AesCtrEx table")
            _, encv, gen = self.ctr[k]
            out += self.sec.raw(off, m) if encv == 1 else self.sec.read(off, m, generation=gen & 0xFFFFFFFF)
            off += m
        return bytes(out)

    def pieces(self, off, n):
        end = off + n
        while off < end:
            k = bisect.bisect_right(self.rel_starts, off) - 1
            if k < 0:
                fail(f"virtual offset {off:#x} without a relocation entry")
            v, p, s = self.rel[k]
            e_end = self.rel_starts[k + 1] if k + 1 < len(self.rel) else self.vsize
            m = min(end, e_end) - off
            if m <= 0:
                fail(f"virtual offset {off:#x} past the relocation table")
            yield s, p + (off - v), m
            off += m

    def read(self, off, n):
        out = bytearray()
        for s, p, m in self.pieces(off, n):
            if s == 0:
                out += self.base.read_section(p, m)
            elif s == 1:
                out += self._patch(p, m)
            else:
                fail(f"relocation storage index {s}")
        return bytes(out)


# ---------------------------------------------------------------------------------------------------------- NSO
def nso_image_sha(path):
    import lz4.block
    raw = open(path, "rb").read()
    if raw[:4] != b"NSO0":
        return None
    flags = u32(raw, 0x0C)
    img = bytearray()
    for i in range(3):
        file_off, mem_off, size = struct.unpack_from("<III", raw, 0x10 + i * 0x10)
        comp = u32(raw, 0x60 + i * 4)
        blob = raw[file_off:file_off + comp]
        if flags & (1 << i):
            blob = lz4.block.decompress(blob, uncompressed_size=size)
        if len(img) < mem_off:
            img += bytes(mem_off - len(img))
        img[mem_off:mem_off + len(blob)] = blob
    return hashlib.sha256(img).hexdigest()


# ---------------------------------------------------------------------------------------------------------- program NCA
def tickets_of(files):
    out = {}
    for name, s in files.items():
        if not name.endswith(".tik"):
            continue
        t = s.read(0, min(s.size, 0x2C0))
        if len(t) < 0x2B0 or t[:4] not in (b"\x04\x00\x01\x00", b"\x00\x01\x00\x04"):  # RSA-2048 SHA-256
            continue
        if t[0x281] != 0:
            fail(f"{name}: personalized ticket (not supported)")
        out[t[0x2A0:0x2B0].hex()] = t[0x180:0x190]
    return out


def find_program(files, keys, program_id, want_bktr):
    tks = tickets_of(files)
    found = []
    for name, s in sorted(files.items()):
        if not name.endswith(".nca") or name.endswith(".cnmt.nca") or s.size < 0xC00:
            continue
        try:
            n = Nca(s, keys, tks)
        except ValueError as e:
            print(f"   {name}: {e}")
            continue
        if n.content_type != 0 or n.title_id != program_id or 1 not in n.sections:
            continue
        b = n.section(1).bktr
        if want_bktr is not None and b != want_bktr:
            continue
        found.append((name, n))
    if not found:  # another game or the other edition: exit code 3, the ps1 skips it
        print(f"SKIP no program NCA of {program_id:016X}{' (base, without BKTR)' if want_bktr is False else ''}")
        sys.exit(3)
    if len(found) != 1:
        fail(f"{len(found)} program NCAs of {program_id:016X} found (1 expected)")
    return found[0]


# ---------------------------------------------------------------------------------------------------------- index
def cmd_index(a):
    keys = load_keys(a.keys)
    pid = int(a.program_id, 16)
    name, nca = find_program(container_files(a.input), keys, pid, False)
    sec = nca.section(1)
    l6_off, l6_size = sec.ivfc_data()
    print(f"   base program NCA {name}; romfs {l6_size:,} bytes at {l6_off:#x} of the section")
    rd = lambda o, n: sec.read(l6_off + o, n)
    files, _ = romfs_files(rd)
    # Check against the extracted files (by size; the content was extracted from this same NCA).
    bad = []
    for off, size, rel in files:
        p = to_host(a.base_romfs, rel)
        if not os.path.isfile(p):
            bad.append(f"missing {rel}")
        elif os.path.getsize(p) != size:
            bad.append(f"size of {rel}")
    if bad:
        fail(f"{a.base_romfs} does not match the dump ({len(bad)}: {', '.join(bad[:5])})")
    # Gaps between files (padding and metadata tables): only the non-zero bytes are kept.
    spans = sorted((o, o + s) for o, s, _ in files if s > 0)
    gaps, pos = [], 0
    for s, e in spans:
        if s > pos:
            gaps.append((pos, s))
        pos = max(pos, e)
    if pos < l6_size:
        gaps.append((pos, l6_size))
    extras, gap_bytes = [], 0
    for s, e in gaps:
        for c in range(s, e, CHUNK):
            n = min(CHUNK, e - c)
            gap_bytes += n
            b = rd(c, n)
            i = 0
            while i < n:  # runs of non-zero data, joined across short zero stretches
                while i < n and b[i] == 0:
                    i += 1
                if i >= n:
                    break
                j, zeros = i, 0
                while j < n and zeros < 64:
                    zeros = zeros + 1 if b[j] == 0 else 0
                    j += 1
                end = j - zeros
                extras.append([c + i, base64.b64encode(b[i:end]).decode()])
                i = j
    idx = {"version": INDEX_VERSION, "program_id": f"{pid:016X}", "nca": name, "l6_offset": l6_off,
           "l6_size": l6_size, "files": [[o, s, r] for o, s, r in files], "extras": extras}
    blob = zlib.compress(json.dumps(idx, separators=(",", ":")).encode(), 9)
    tmp = a.out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(b"TSKIDX1\0" + blob)
    os.replace(tmp, a.out)
    kept = sum(len(base64.b64decode(e[1])) for e in extras)
    print(f"   index: {len(files)} files, gaps {gap_bytes:,} bytes ({kept:,} non-zero kept); {os.path.getsize(a.out):,} bytes")
    print(f"INDEX files={len(files)} size={os.path.getsize(a.out)}")


def load_index(path, pid):
    if not os.path.isfile(path):
        fail(f"{path} does not exist (make it first with the index command)")
    raw = open(path, "rb").read()
    if raw[:8] != b"TSKIDX1\0":
        fail(f"{path} is not a romfs index")
    idx = json.loads(zlib.decompress(raw[8:]))
    if idx.get("version") != INDEX_VERSION:
        fail(f"{path}: index version {idx.get('version')}")
    if int(idx["program_id"], 16) != pid:
        fail(f"{path} is from program {idx['program_id']}")
    return idx


# ---------------------------------------------------------------------------------------------------------- mod
def cmd_mod(a):
    keys = load_keys(a.keys)
    pid = int(a.program_id, 16)
    idx = load_index(a.index, pid)
    if os.path.exists(a.out) and os.listdir(a.out):
        fail(f"{a.out} exists and is not empty")
    files = container_files(a.nsp)
    name, nca = find_program(files, keys, pid, None)
    print(f"   program NCA {name}")
    out_exefs, out_romfs = os.path.join(a.out, "exefs"), os.path.join(a.out, "romfs")
    os.makedirs(a.out, exist_ok=True)

    # exefs
    exefs_diff = []
    if 0 in nca.sections:
        sec0 = nca.section(0)
        po, ps = sec0.pfs0_region()
        pfs = partition_fs(_SectionSource(sec0, po, ps), b"PFS0", 0x18)
        if pfs is None:
            fail("exefs without PFS0")
        os.makedirs(out_exefs)
        for fn, s in pfs.items():
            with open(os.path.join(out_exefs, fn), "wb") as f:
                f.write(s.read(0, s.size))
        for fn in sorted(pfs):
            if fn == "main.npdm":
                continue
            mine, base = os.path.join(out_exefs, fn), os.path.join(a.base_exefs, fn)
            hm = nso_image_sha(mine)
            hb = nso_image_sha(base) if os.path.isfile(base) else None
            if hm is None:
                continue
            if hm != hb:
                exefs_diff.append(fn)
        print(f"   exefs: {', '.join(sorted(pfs))}; different from the base (decompressed): {', '.join(exefs_diff) or 'none'}")

    # romfs
    sec = nca.section(1)
    base = BaseRomfs(idx, a.base_romfs)
    if sec.bktr:
        vs = Patched(sec, base)
        vread = vs.read
    else:
        vs, vread = None, sec.read
    l6_off, l6_size = sec.ivfc_data()
    pfiles, _ = romfs_files(lambda o, n: vread(l6_off + o, n))
    base_by = {f[2].lower(): (f[0], f[1], f[2]) for f in idx["files"]}
    check = None
    if a.verify_csv:
        check = {}
        with open(a.verify_csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                check[row["R"].replace("\\", "/").lstrip("/").lower()] = row["H"].upper()
    unchanged = changed = new = verified = 0
    mismatch = []
    os.makedirs(out_romfs)
    for i, (foff, size, rel) in enumerate(pfiles):
        voff = l6_off + foff
        b = base_by.get(rel.lower())
        same_place = False
        if vs is not None and b is not None and b[1] == size:
            ps = list(vs.pieces(voff, size)) if size else []
            want = idx["l6_offset"] + b[0]
            same_place = all(s == 0 for s, _, _ in ps) and all(
                p == want + sum(m for _, _, m in ps[:k]) for k, (_, p, _) in enumerate(ps))
        md5 = hashlib.md5() if check is not None else None
        if same_place:
            unchanged += 1
            if md5 is not None:
                with open(to_host(a.base_romfs, b[2]), "rb") as f:
                    for c in iter(lambda: f.read(CHUNK), b""):
                        md5.update(c)
        else:
            dst = to_host(out_romfs, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            equal = b is not None and b[1] == size
            bf = open(to_host(a.base_romfs, b[2]), "rb") if equal else None
            with open(dst + ".tmp", "wb") as f:
                for c in range(0, size, CHUNK):
                    d = vread(voff + c, min(CHUNK, size - c))
                    f.write(d)
                    if md5 is not None:
                        md5.update(d)
                    if bf is not None and equal and bf.read(len(d)) != d:
                        equal = False
            if bf:
                bf.close()
            if equal:
                os.remove(dst + ".tmp")
                unchanged += 1
            else:
                os.replace(dst + ".tmp", dst)
                if b is None:
                    new += 1
                else:
                    changed += 1
        if md5 is not None:
            exp = check.get(rel.lower())
            if exp != md5.hexdigest().upper():
                mismatch.append(rel)
            verified += 1
        if (i + 1) % 2000 == 0:
            print(f"   ... {i + 1} of {len(pfiles)} files")
    for root, dirs, fs in os.walk(out_romfs, topdown=False):  # empty folders left by unchanged files
        if not fs and not dirs and root != out_romfs:
            os.rmdir(root)
    pset = {f[2].lower() for f in pfiles}
    removed = [f[2] for f in idx["files"] if f[2].lower() not in pset]
    info = {"nsp": os.path.basename(a.nsp), "program_nca": name, "bktr": sec.bktr, "romfs_files": len(pfiles),
            "changed": changed, "new": new, "unchanged": unchanged, "removed": removed, "exefs_different": exefs_diff,
            "main_sha256": nso_image_sha(os.path.join(out_exefs, "main")) if os.path.isfile(os.path.join(out_exefs, "main")) else None}
    with open(os.path.join(a.out, "mod.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    print(f"   romfs: {len(pfiles)} files; changed {changed}, new {new}, unchanged {unchanged}, missing in the patch {len(removed)}")
    if removed:
        print(f"   WARNING: files of the base that the patch does not have (the base one stays visible): {', '.join(removed[:5])}")
    if check is not None:
        missing = [r for r in check if r not in pset]
        print(f"   verify: {verified} files compared, {len(mismatch)} different, {len(missing)} in the csv but not in the patch")
        for r in (mismatch + missing)[:20]:
            print(f"   VERIFY FAIL {r}")
        if mismatch or missing:
            fail("the rebuilt romfs does not match the csv")
    print(f"MOD changed={changed} new={new} removed={len(removed)} exefs_different={','.join(exefs_diff) or '-'}"
          f" main_sha256={info['main_sha256'] or '-'}")


class _SectionSource(Source):
    """A range of a decrypted section seen as a Source (for the exefs PFS0)."""

    def __init__(self, sec, off, size):
        self.sec, self.offset, self.size = sec, off, size

    def sub(self, off, size):
        return _SectionSource(self.sec, self.offset + off, size)

    def read(self, off, n):
        if off < 0 or off + n > self.size:
            fail("read outside the exefs")
        return self.sec.read(self.offset + off, n)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("index")
    i.add_argument("--keys", required=True)
    i.add_argument("--input", required=True)
    i.add_argument("--program-id", required=True)
    i.add_argument("--base-romfs", required=True)
    i.add_argument("--out", required=True)
    m = sp.add_parser("mod")
    m.add_argument("--keys", required=True)
    m.add_argument("--nsp", required=True)
    m.add_argument("--program-id", required=True)
    m.add_argument("--index", required=True)
    m.add_argument("--base-romfs", required=True)
    m.add_argument("--base-exefs", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--verify-csv")
    a = ap.parse_args()
    (cmd_index if a.cmd == "index" else cmd_mod)(a)


if __name__ == "__main__":
    main()
