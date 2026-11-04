#!/usr/bin/env python3
"""Synthetic test of AP_ROMFS_DIRECT (no game data, made-up keys).

Builds a small base program NCA (exefs PFS0 + RomFS with an IVFC data level, AES-CTR, key area, XTS header) as an NSP
and as a padded XCI, plus a patch NSP for the same program, and checks:
  - tools/internal/romfs_source.py: info, prepare (table, build files, exefs), locate, trim / untrim
  - tools/internal/mods_nsp.py mod --base-dump gives the same result as --index + --base-romfs (when the patcher was
    applied: AP_ROMFS_DIRECT in mods_nsp.py)
  - games/tsukihime_common/romfs_direct.cpp (with --cxx, needs g++ or clang++): every file read through the reader
    (whole, and at random offsets and sizes; AES-NI and plain C paths) equals the original; folders and listings;
    the dump found by its content after a rename; a missing dump stops with code 7.

Run from anywhere: python tools/tests/test_romfs_direct.py [--cxx]
"""
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "internal"))
import mods_nsp as m  # noqa: E402
from Crypto.Cipher import AES  # noqa: E402

PID = 0x01001DC01486A000
HEADER_KEY = bytes(range(0x20, 0x40))
KAK = bytes(range(0x40, 0x50))
TITLE_KEY_GEN = 17  # key_area_key_application_10
RNG = random.Random(1234)


def pad(b, a):
    return b + b"\0" * (-len(b) % a)


def xts_encrypt(key, data, sector0=0):
    k1, k2 = AES.new(key[:16], AES.MODE_ECB), AES.new(key[16:32], AES.MODE_ECB)
    out = bytearray()
    for s in range(len(data) // 0x200):
        t = int.from_bytes(k2.encrypt((sector0 + s).to_bytes(16, "big")), "little")
        for b in range(0, 0x200, 16):
            tb = t.to_bytes(16, "little")
            blk = bytes(x ^ y for x, y in zip(data[s * 0x200 + b:s * 0x200 + b + 16], tb))
            out += bytes(x ^ y for x, y in zip(k1.encrypt(blk), tb))
            t <<= 1
            if t >> 128:
                t = (t & ((1 << 128) - 1)) ^ 0x87
    return bytes(out)


def build_romfs(files, empty_dirs=()):
    """files: {"a/b/c.ext": bytes}. A real RomFS layout (header, data, directory and file tables)."""
    dirs = {""}
    for p in list(files) + list(empty_dirs):
        parts = p.split("/")
        rng = range(1, len(parts)) if p in files else range(1, len(parts) + 1)
        for k in rng:
            dirs.add("/".join(parts[:k]))
    dirs = sorted(dirs, key=lambda d: (d.count("/"), d))
    data, foff = bytearray(), {}
    for p in sorted(files):
        data = bytearray(pad(bytes(data), 0x10))
        foff[p] = len(data)
        data += files[p]
    doff, pos = {}, 0
    for d in dirs:
        doff[d] = pos
        pos += 0x18 + len(pad(d.rsplit("/", 1)[-1].encode() if d else b"", 4))
    fent, pos = {}, 0
    for p in sorted(files):
        fent[p] = pos
        pos += 0x20 + len(pad(p.rsplit("/", 1)[-1].encode(), 4))
    none = 0xFFFFFFFF
    parent = lambda x: x.rsplit("/", 1)[0] if "/" in x else ""
    dm = bytearray()
    for d in dirs:
        subs = [x for x in dirs if x and parent(x) == d]
        fs = [p for p in sorted(files) if parent(p) == d]
        sib = none
        if d:
            peers = [x for x in dirs if x and parent(x) == parent(d)]
            k = peers.index(d)
            sib = doff[peers[k + 1]] if k + 1 < len(peers) else none
        name = d.rsplit("/", 1)[-1].encode() if d else b""
        dm += struct.pack("<6I", doff[parent(d)] if d else 0, sib, doff[subs[0]] if subs else none,
                          fent[fs[0]] if fs else none, none, len(name)) + pad(name, 4)
    fm = bytearray()
    for p in sorted(files):
        peers = [x for x in sorted(files) if parent(x) == parent(p)]
        k = peers.index(p)
        sib = fent[peers[k + 1]] if k + 1 < len(peers) else none
        name = p.rsplit("/", 1)[-1].encode()
        fm += struct.pack("<IIQQII", doff[parent(p)], sib, foff[p], len(files[p]), none, len(name)) + pad(name, 4)
    data_off = 0x200
    meta = data_off + len(pad(bytes(data), 0x10))
    hdr = struct.pack("<10Q", 0x50, meta, 4, meta + 4, len(dm), meta + 4 + len(dm), 4, meta + 8 + len(dm), len(fm), data_off)
    return bytes(pad(hdr, 0x200)) + pad(bytes(data), 0x10) + b"\xff" * 4 + bytes(dm) + b"\xff" * 4 + bytes(fm)


def build_pfs0(entries):
    strtab = pad(b"".join(n.encode() + b"\0" for n, _ in entries), 0x20)
    ent, pos, noff = b"", 0, 0
    for n, d in entries:
        ent += struct.pack("<QQII", pos, len(d), noff, 0)
        pos += len(d)
        noff += len(n) + 1
    return struct.pack("<4sII", b"PFS0", len(entries), len(strtab)) + b"\0" * 4 + ent + strtab + b"".join(d for _, d in entries)


def build_hfs0(entries):
    strtab = pad(b"".join(n.encode() + b"\0" for n, _ in entries), 0x20)
    ent, pos, noff = b"", 0, 0
    for n, d in entries:
        ent += struct.pack("<QQII", pos, len(d), noff, 0) + b"\0" * 0x28
        pos += len(d)
        noff += len(n) + 1
    return struct.pack("<4sII", b"HFS0", len(entries), len(strtab)) + b"\0" * 4 + ent + strtab + b"".join(d for _, d in entries)


def build_nca(romfs, exefs_files, content_key, ctr_upper, sig_seed):
    exefs = build_pfs0(exefs_files)
    sec0_data = b"\0" * 0x200 + pad(exefs, 0x200)       # hash table + PFS0
    l6 = 0x6000                                            # IVFC data level inside the section
    sec1_data = pad(b"\0" * l6 + romfs, 0x200)
    s0 = 0xC00
    s1 = s0 + len(sec0_data)
    hdr = bytearray(0xC00)
    hdr[0:0x100] = bytes((sig_seed + i) & 0xFF for i in range(0x100))  # stands in for the RSA signature
    hdr[0x200:0x204] = b"NCA3"
    hdr[0x205] = 0
    hdr[0x206], hdr[0x207] = 2, 0
    struct.pack_into("<Q", hdr, 0x208, s1 + len(sec1_data))
    struct.pack_into("<Q", hdr, 0x210, PID)
    hdr[0x220] = TITLE_KEY_GEN
    struct.pack_into("<II", hdr, 0x240, s0 // 0x200, s1 // 0x200)
    struct.pack_into("<II", hdr, 0x250, s1 // 0x200, (s1 + len(sec1_data)) // 0x200)
    hdr[0x300:0x340] = AES.new(KAK, AES.MODE_ECB).encrypt(b"\0" * 0x20 + content_key + b"\0" * 0x10)
    ctr_le = int.from_bytes(ctr_upper, "big").to_bytes(8, "little")   # stored little-endian in the FS header
    f0 = bytearray(0x200)
    struct.pack_into("<HBBB", f0, 0, 2, 1, 2, 3)
    struct.pack_into("<QQ", f0, 0x40, 0x200, len(exefs))               # PFS0 region of HierarchicalSha256
    f0[0x140:0x148] = ctr_le
    f1 = bytearray(0x200)
    struct.pack_into("<HBBB", f1, 0, 2, 0, 3, 3)
    f1[0x8:0xC] = b"IVFC"
    struct.pack_into("<III", f1, 0xC, 0x20000, 0x20, 7)
    struct.pack_into("<QQ", f1, 0x90, l6, len(romfs))
    f1[0x140:0x148] = ctr_le
    hdr[0x400:0x600], hdr[0x600:0x800] = f0, f1
    body = m.ctr_crypt(content_key, ctr_upper, s0, sec0_data) + m.ctr_crypt(content_key, ctr_upper, s1, sec1_data)
    # the signature (first 0x100 bytes) is part of the encrypted sector 0 in a real NCA; here sector 0 is
    # re-encrypted with the made-up signature in place so the header still decrypts
    enc = xts_encrypt(HEADER_KEY, bytes(hdr))
    return enc + body, enc[:0x100]


def build_xci(nca_name, nca, padding):
    secure = build_hfs0([(nca_name, nca)])
    root = build_hfs0([("update", build_hfs0([])), ("normal", build_hfs0([])), ("secure", secure)])
    hfs_off = 0xF000
    head = bytearray(0x200)
    head[0x100:0x104] = b"HEAD"
    head[0x10D] = 0xFA
    end = hfs_off + len(root)
    struct.pack_into("<I", head, 0x118, (end + 0x1FF) // 0x200 - 1)
    struct.pack_into("<QQ", head, 0x130, hfs_off, 0x200)
    img = bytes(head) + b"\0" * (hfs_off - 0x200) + root
    img += b"\xff" * (-len(img) % 0x200)
    return img + b"\xff" * padding


FILES = {
    "shader/a.bnsh": os.urandom(5000),
    "shader/sub/b.bnsh": os.urandom(123),
    "movie/prologue.mp4": b"\0\0\0\x18ftypisom" + os.urandom(300000),
    "movie/Op_Arc.MP4": b"\0\0\0\x18ftypmp42" + os.urandom(77777),
    "stream/v1.opus": struct.pack("<II", 0x80000001, 0x18) + os.urandom(901),
    "stream/MOON01_BGM01.opus": struct.pack("<II", 0x80000001, 0x18) + os.urandom(77),
    "allui.hed": os.urandom(64),
    "allui.nam": os.urandom(32),
    "allui.mrg": os.urandom(4097),
    "w_allui.mrg": os.urandom(33),
    "data/empty.bin": b"",
    "data/one.bin": b"\x01",
}
EMPTY_DIRS = ["data/nothing_here"]
def fake_nso(n):
    """A minimal uncompressed NSO (three segments of n bytes): enough for mods_nsp.nso_image_sha."""
    h = bytearray(0x100)
    h[0:4] = b"NSO0"
    for i in range(3):
        struct.pack_into("<III", h, 0x10 + i * 0x10, 0x100 + i * n, i * n, n)
        struct.pack_into("<I", h, 0x60 + i * 4, n)
    return bytes(h) + os.urandom(3 * n)


EXEFS = [("main", fake_nso(128)), ("main.npdm", b"META" + os.urandom(200)), ("sdk", fake_nso(64))]
KEY = bytes(range(0x60, 0x70))
CTR = bytes.fromhex("0000000200000003")


def py(*args, check=True):
    r = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)
    if check and r.returncode != 0:
        print(r.stdout, r.stderr)
        raise SystemExit(f"FAIL: {' '.join(map(str, args[:2]))} exited {r.returncode}")
    return r


class Checker:
    fails = 0

    def __call__(self, cond, what, out=""):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            Checker.fails += 1
            if out:
                print(out)


def cxx_test(work, data_root, check):
    cxx = shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        print("skip C++ part: no g++/clang++")
        return
    inc = work / "inc"
    (inc / "nx").mkdir(parents=True, exist_ok=True)
    (inc / "nx" / "runtime.h").write_text("#pragma once\nnamespace nx { void log(const char* fmt, ...); }\n")
    (inc / "platform.h").write_text(
        "#pragma once\n#include <cstdio>\n#include <cstdint>\n#include <string>\n"
        "namespace nx { namespace plat {\nFILE* fopen_utf8(const std::string& p, const char* m);\n"
        "int64_t ftell64(FILE* f);\nint fseek64(FILE* f, int64_t o, int w);\n"
        "uint64_t read_at(FILE* f, int64_t off, void* buf, uint64_t size);\nstd::string data_root();\n}}\n")
    harness = work / "harness.cpp"
    harness.write_text(r'''
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <nx/runtime.h>
#include "platform.h"
namespace nx {
void log(const char* fmt, ...) { va_list a; va_start(a, fmt); std::vfprintf(stdout, fmt, a); va_end(a); std::printf("\n"); std::fflush(stdout); }
namespace plat {
FILE* fopen_utf8(const std::string& p, const char* m) { return std::fopen(p.c_str(), m); }
int64_t ftell64(FILE* f) { return ftello(f); }
int fseek64(FILE* f, int64_t o, int w) { return fseeko(f, o, w); }
uint64_t read_at(FILE* f, int64_t off, void* buf, uint64_t size) { fseeko(f, off, SEEK_SET); return std::fread(buf, 1, size, f); }
std::string data_root() { return std::getenv("TEST_DATA_ROOT"); }
}}
namespace tsuki_romfs {
struct Handle;
bool enabled(); bool file(const std::string&, uint64_t*); bool dir(const std::string&);
void list(const std::string&, std::vector<std::string>&, std::vector<uint8_t>&, std::vector<uint64_t>&);
Handle* open(const std::string&); uint64_t size(const Handle*); uint64_t read(Handle*, int64_t, void*, uint64_t);
void close(Handle*); bool read_all(const std::string&, std::string&);
}
// argv: <list file: lines "path<TAB>expected file on disk"> <seed>
int main(int argc, char** argv) {
    if (!tsuki_romfs::enabled()) { std::printf("NOT ENABLED\n"); return 2; }
    FILE* l = std::fopen(argv[1], "r");
    char line[4096];
    int bad = 0, n = 0;
    std::srand(std::atoi(argv[2]));
    while (std::fgets(line, sizeof line, l)) {
        line[std::strcspn(line, "\r\n")] = 0;
        char* tab = std::strchr(line, '\t');
        *tab = 0;
        std::string rel = line, host = tab + 1;
        FILE* f = std::fopen(host.c_str(), "rb");
        std::fseek(f, 0, SEEK_END); long sz = std::ftell(f); std::fseek(f, 0, SEEK_SET);
        std::string want(size_t(sz), '\0'); if (sz) std::fread(&want[0], 1, size_t(sz), f); std::fclose(f);
        uint64_t got_size = 0;
        if (!tsuki_romfs::file(rel, &got_size) || got_size != uint64_t(sz)) { std::printf("size %s\n", rel.c_str()); bad++; continue; }
        std::string all;
        if (!tsuki_romfs::read_all(rel, all) || all != want) { std::printf("read_all %s\n", rel.c_str()); bad++; }
        tsuki_romfs::Handle* h = tsuki_romfs::open(rel);
        for (int k = 0; k < 200 && sz > 0; k++) {
            int64_t off = std::rand() % sz;
            uint64_t len = 1 + std::rand() % 70000;
            std::vector<char> buf(len + 32, 'x');
            uint64_t got = tsuki_romfs::read(h, off, buf.data(), len);
            uint64_t exp = std::min<uint64_t>(len, uint64_t(sz - off));
            if (got != exp || std::memcmp(buf.data(), want.data() + off, size_t(exp)) != 0 || buf[exp] != 'x') {
                std::printf("range %s off %lld len %llu got %llu\n", rel.c_str(), (long long)off, (unsigned long long)len, (unsigned long long)got);
                bad++; break;
            }
        }
        if (tsuki_romfs::read(h, sz, line, 10) != 0) { std::printf("read past the end %s\n", rel.c_str()); bad++; }
        tsuki_romfs::close(h);
        n++;
    }
    std::vector<std::string> names; std::vector<uint8_t> dirs; std::vector<uint64_t> sizes;
    tsuki_romfs::list("", names, dirs, sizes);
    std::printf("ROOT");
    for (size_t i = 0; i < names.size(); i++) std::printf(" %s%s", names[i].c_str(), dirs[i] ? "/" : "");
    std::printf("\n");
    names.clear(); dirs.clear(); sizes.clear();
    tsuki_romfs::list("DATA", names, dirs, sizes);
    std::printf("DATA");
    for (size_t i = 0; i < names.size(); i++) std::printf(" %s%s=%llu", names[i].c_str(), dirs[i] ? "/" : "", (unsigned long long)sizes[i]);
    std::printf("\n");
    std::printf("DIRS %d %d %d %d\n", tsuki_romfs::dir(""), tsuki_romfs::dir("shader/sub"), tsuki_romfs::dir("data/nothing_here"), tsuki_romfs::dir("nope"));
    std::printf("CASE %d\n", tsuki_romfs::file("MOVIE\\op_arc.mp4", nullptr));
    std::printf("CHECKED %d BAD %d\n", n, bad);
    return bad ? 1 : 0;
}
''')
    src = ROOT / "framework" / "games" / "tsukihime_common" / "romfs_direct.cpp"
    exe = work / "romfs_direct_test"
    r = subprocess.run([cxx, "-std=c++17", "-O1", "-DNX_ROMFS_DIRECT", f"-I{inc}", str(src), str(harness), "-o", str(exe)],
                       capture_output=True, text=True)
    check(r.returncode == 0, f"C++: romfs_direct.cpp compiles ({os.path.basename(cxx)})", r.stderr[-3000:])
    if r.returncode != 0:
        return
    lst = work / "list.txt"
    with open(lst, "w") as f:
        for rel in FILES:
            f.write(f"{rel}\t{work / 'expect' / rel}\n")
    for noni in ("", "1"):
        env = dict(os.environ, TEST_DATA_ROOT=str(data_root), NX_ROMFS_NO_AESNI=noni)
        r = subprocess.run([str(exe), str(lst), "7"], capture_output=True, text=True, env=env)
        path = "plain C" if noni else "AES-NI if present"
        check(r.returncode == 0 and f"CHECKED {len(FILES)} BAD 0" in r.stdout, f"C++ ({path}): every file whole and at 200 random ranges", r.stdout[-3000:])
        check(("AES-C" in r.stdout) == bool(noni), f"C++ ({path}): the path in the log", r.stdout[:400])
    out = r.stdout
    check("ROOT allui.hed allui.mrg allui.nam w_allui.mrg data/ movie/ shader/ stream/" in out.replace("  ", " ") or
          all(x in out.split("ROOT", 1)[1].split("\n", 1)[0] for x in ("data/", "movie/", "shader/", "stream/", "allui.mrg")),
          "C++: root listing", out)
    check("nothing_here/=0" in out and "empty.bin=0" in out and "one.bin=1" in out, "C++: folder listing with sizes and an empty folder", out)
    check("DIRS 1 1 1 0" in out, "C++: folders", out)
    check("CASE 1" in out, "C++: any case and backslashes", out)
    return exe, lst

HLE_HARNESS = r"""
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <chrono>
#include <nx/runtime.h>
#include "platform.h"
namespace nx {
bool g_trace = false;
void log(const char* fmt, ...) { va_list a; va_start(a, fmt); std::vfprintf(stdout, fmt, a); va_end(a); std::printf("\n"); std::fflush(stdout); }
static std::map<std::string, HleFn>& reg() { static std::map<std::string, HleFn> r; return r; }
HleRegistrar::HleRegistrar(const char* name, HleFn fn) { reg()[name] = fn; }
std::string read_game_file(const std::string& rel);
namespace plat {
FILE* fopen_utf8(const std::string& p, const char* m) { return std::fopen(p.c_str(), m); }
int64_t ftell64(FILE* f) { return ftello(f); }
int fseek64(FILE* f, int64_t o, int w) { return fseeko(f, o, w); }
uint64_t read_at(FILE* f, int64_t off, void* buf, uint64_t size) { fseeko(f, off, SEEK_SET); return std::fread(buf, 1, size, f); }
std::string data_root() { return std::getenv("TEST_DATA_ROOT"); }
uint64_t monotonic_ns() { return (uint64_t)std::chrono::steady_clock::now().time_since_epoch().count(); }
int64_t current_thread_id() { return 1; }
}  // namespace plat
}  // namespace nx
namespace tsuki_title {
std::string remap_stream(const std::string& path) { return path == "rom:/stream/MOON01_BGM02.opus" ? "rom:/stream/v1.opus" : ""; }
std::string music_swap(const std::string& path, int64_t off) { return path == "rom:/stream/MOON01_BGM01.opus" ? "rom:/data/one.bin" : ""; }
}
using nx::CPU;
static CPU* cpu() { static CPU* c = new CPU(); return c; }
static uint64_t call(const char* sym, std::vector<uint64_t> args) {
    nx::HleFn fn = nx::reg()[sym];
    if (!fn) { std::printf("NO HLE %s\n", sym); std::exit(9); }
    CPU& c = *cpu();
    for (size_t i = 0; i < args.size(); i++) c.x[i] = args[i];
    fn(c);
    return c.x[0];
}
#define P(s) (uint64_t)(uintptr_t)(s)
static std::string slurp(const char* p) { std::string s; FILE* f = std::fopen(p, "rb"); if (!f) return s; char b[65536]; size_t n; while ((n = std::fread(b, 1, sizeof b, f)) > 0) s.append(b, n); std::fclose(f); return s; }
static int bad = 0;
static void expect(bool ok, const char* what) { std::printf("%s %s\n", ok ? "PASS" : "FAILX", what); if (!ok) bad++; }
static std::string read_rom(const char* guest, int64_t off, uint64_t n, uint64_t* size = nullptr) {
    uint64_t h = 0;
    if (call("_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci", {P(&h), P(guest), 1}) != 0) return "<open failed>";
    int64_t sz = -1;
    call("_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE", {P(&sz), h});
    if (size) *size = uint64_t(sz);
    std::string buf(n, '\0');
    uint64_t got = 0;
    call("_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm", {P(&got), h, uint64_t(off), P(&buf[0]), n});
    buf.resize(got);
    call("_ZN2nn2fs9CloseFileENS0_10FileHandleE", {h});
    return buf;
}
struct Ent { std::string name; int type; int64_t size; };
static std::vector<Ent> list_rom(const char* guest, int mode, int64_t* count = nullptr) {
    std::vector<Ent> out;
    uint64_t h = 0;
    if (call("_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci", {P(&h), P(guest), uint64_t(mode)}) != 0) return out;
    int64_t cnt = -1;
    call("_ZN2nn2fs22GetDirectoryEntryCountEPlNS0_15DirectoryHandleE", {P(&cnt), h});
    if (count) *count = cnt;
    std::vector<char> ents(0x310 * 3);
    for (;;) {
        int64_t n = 0;
        call("_ZN2nn2fs13ReadDirectoryEPlPNS0_14DirectoryEntryENS0_15DirectoryHandleEl", {P(&n), P(ents.data()), h, 3});
        if (n <= 0) break;
        for (int64_t i = 0; i < n; i++) {
            const char* e = ents.data() + 0x310 * i;
            Ent x{std::string(e), (int)(int8_t)e[0x304], 0};
            std::memcpy(&x.size, e + 0x308, 8);
            out.push_back(x);
        }
    }
    call("_ZN2nn2fs14CloseDirectoryENS0_15DirectoryHandleE", {h});
    return out;
}
static bool has(const std::vector<Ent>& v, const std::string& n, int type, int64_t size) {
    int k = 0; for (auto& e : v) if (e.name == n) { k++; if (e.type != type || (size >= 0 && e.size != size)) return false; }
    return k == 1;
}
static uint32_t entry_type(const char* guest, uint32_t* t) { return (uint32_t)call("_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc", {P(t), P(guest)}); }
int main(int argc, char** argv) {
    std::string exp = argv[1];  // folder with the expected files
    const bool direct = std::string(argv[2]) == "direct";
    std::string root = std::getenv("TEST_DATA_ROOT");
    expect(call("_ZN2nn2fs8MountRomEPKcPvm", {P("rom"), 0, 0}) == 0, "MountRom");
    uint32_t t = 9;
    expect(entry_type("rom:/movie", &t) == 0 && t == 0, "GetEntryType folder");
    expect(entry_type("rom:/movie/Op_Arc.MP4", &t) == 0 && t == 1, "GetEntryType file");
    expect(!direct || (entry_type("rom:/movie/op_arc.mp4", &t) == 0 && t == 1), "GetEntryType any case (dump)");
    expect(entry_type("rom:/nope.bin", &t) == 0x202, "GetEntryType missing");
    expect(entry_type("rom:/newdir", &t) == 0 && t == 0, "GetEntryType layer-only folder");
    uint64_t sz = 0;
    std::string want = slurp((exp + "/stream/v1.opus").c_str());
    expect(read_rom("rom:/stream/v1.opus", 0, 100000, &sz) == want && sz == want.size(), "OpenFile + GetFileSize + ReadFile (whole)");
    expect(read_rom("rom:/stream/v1.opus", 13, 77) == want.substr(13, 77), "ReadFile at an offset");
    expect(read_rom("rom:/stream/v1.opus", int64_t(want.size()) - 3, 50) == want.substr(want.size() - 3), "ReadFile past the end is cut");
    std::string layer = slurp((root + "/../../mods/japan/layer1/romfs/movie/prologue.mp4").c_str());
    expect(read_rom("rom:/movie/prologue.mp4", 0, 1 << 20) == layer, "a mods layer wins over the dump");
    std::string hosted = slurp((root + "/extract/romfs/allui.hed").c_str());
    if (!hosted.empty()) expect(read_rom("rom:/allui.hed", 0, 1 << 20) == hosted, "extract/romfs wins file by file");
    else expect(read_rom("rom:/allui.hed", 0, 1 << 20) == slurp((exp + "/allui.hed").c_str()), "allui.hed from the dump");
    int64_t cnt = 0;
    auto d = list_rom("rom:/data", 3, &cnt);
    expect(d.size() == 4 && cnt == 4 && has(d, "empty.bin", 1, 0) && has(d, "one.bin", 1, 1) && has(d, "nothing_here", 0, 0) && has(d, "extra.bin", 1, 5),
           "OpenDirectory: dump + layer, sizes, empty folder, count");
    auto m = list_rom("rom:/movie", 2, &cnt);
    expect(m.size() == 2 && cnt == 2 && has(m, "prologue.mp4", 1, (int64_t)layer.size()) && has(m, "Op_Arc.MP4", 1, -1), "OpenDirectory: a layer file replaces the dump's");
    auto r = list_rom("rom:/", 1, &cnt);
    expect(has(r, "movie", 0, 0) && has(r, "shader", 0, 0) && has(r, "newdir", 0, 0) && has(r, "data", 0, 0), "OpenDirectory root, folders only");
    auto nd = list_rom("rom:/newdir", 3);
    expect(nd.size() == 1 && has(nd, "x.txt", 1, 3), "OpenDirectory: a folder only the layer has");
    expect(nx::read_game_file("movie/Op_Arc.MP4") == slurp((exp + "/movie/Op_Arc.MP4").c_str()), "read_game_file from the dump");
    expect(nx::read_game_file("movie/prologue.mp4") == layer, "read_game_file from the layer");
    expect(read_rom("rom:/stream/MOON01_BGM02.opus", 0, 100000) == want, "title music remapped to a track of the dump");
    uint64_t h = 0;
    expect(call("_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci", {P(&h), P("rom:/data/one.bin"), 2}) == 0x202 || !direct, "a dump file is not opened for writing");
    if (std::getenv("TEST_SWAP")) {
        uint64_t hh = 0;
        call("_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci", {P(&hh), P("rom:/stream/MOON01_BGM01.opus"), 1});
        char b[8] = {0};
        uint64_t got = 0;
        call("_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm", {P(&got), hh, 0, P(b), 8});
        expect(got == 1 && b[0] == 1, "title live music: the handle switched to a file of the dump");
        call("_ZN2nn2fs9CloseFileENS0_10FileHandleE", {hh});
    }
    std::printf("HLE BAD %d\n", bad);
    return bad ? 1 : 0;
}
"""


def hle_test(work, data_root, port, check, cxx):
    exe = work / "hle_fs_test"
    fw = ROOT / "framework"
    d = ["-DNX_ROMFS_MODS", '-DNX_ROMFS_MODS_EDITION="japan"', "-DNX_TITLE_MUSIC", "-DNX_TITLE_LIVE", "-DNX_ROMFS_DIRECT"]
    (work / "hle_harness.cpp").write_text(HLE_HARNESS)
    r = subprocess.run([cxx, "-std=c++20", "-O1", "-w", *d, f"-I{fw / 'runtime' / 'include'}", f"-I{fw / 'runtime' / 'src'}",
                        str(fw / "runtime" / "src" / "hle_fs.cpp"), str(fw / "games" / "tsukihime_common" / "romfs_direct.cpp"),
                        str(work / "hle_harness.cpp"), "-o", str(exe)], capture_output=True, text=True)
    check(r.returncode == 0, "hle_fs.cpp + romfs_direct.cpp link with a harness", r.stderr[-3000:])
    if r.returncode != 0:
        return
    layer = port / "mods" / "japan" / "layer1" / "romfs"
    (layer / "movie").mkdir(parents=True)
    (layer / "movie" / "prologue.mp4").write_bytes(b"\0\0\0\x18ftypLAYR" + os.urandom(999))
    (layer / "newdir").mkdir()
    (layer / "newdir" / "x.txt").write_bytes(b"abc")
    (layer / "data").mkdir()
    (layer / "data" / "extra.bin").write_bytes(b"12345")
    env = dict(os.environ, TEST_DATA_ROOT=str(data_root), TEST_SWAP="1")
    r = subprocess.run([str(exe), str(work / "expect"), "direct"], capture_output=True, text=True, env=env)
    check(r.returncode == 0 and "HLE BAD 0" in r.stdout, "hle_fs with the dump: files, folders, layers, videos, title music", r.stdout[-3000:])
    check(not (data_root / "extract").exists(), "MountRom with the dump does not create extract/romfs")
    (data_root / "extract" / "romfs").mkdir(parents=True)
    (data_root / "extract" / "romfs" / "allui.hed").write_bytes(b"HOST COPY")
    r = subprocess.run([str(exe), str(work / "expect"), "direct"], capture_output=True, text=True, env=env)
    check(r.returncode == 0 and "HLE BAD 0" in r.stdout, "hle_fs: a file of extract/romfs wins over the dump", r.stdout[-3000:])
    shutil.rmtree(data_root / "extract")
    # without romfs_source.bin: everything from a full extract/romfs, as before
    full = data_root / "extract" / "romfs"
    for rel, b in FILES.items():
        q = full / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(b)
    (full / "data" / "nothing_here").mkdir(parents=True, exist_ok=True)
    tbl = data_root / "romfs_source.bin"
    tbl.rename(data_root / "romfs_source.bin.off")
    r = subprocess.run([str(exe), str(work / "expect"), "extract"], capture_output=True, text=True, env=env)
    check(r.returncode == 0 and "HLE BAD 0" in r.stdout and "no " in r.stdout, "hle_fs without romfs_source.bin: extract/romfs as before", r.stdout[-3000:])
    (data_root / "romfs_source.bin.off").rename(tbl)
    shutil.rmtree(data_root / "extract")


def main():
    check = Checker()
    want_cxx = "--cxx" in sys.argv
    with tempfile.TemporaryDirectory() as t:
        work = Path(t)
        root = work / "port"
        (root / "data" / "tsuki").mkdir(parents=True)
        (root / "prod.keys").write_text(f"header_key = {HEADER_KEY.hex()}\nkey_area_key_application_10 = {KAK.hex()}\n")
        keys = root / "prod.keys"
        romfs = build_romfs(FILES, EMPTY_DIRS)
        nca, sig = build_nca(romfs, EXEFS, KEY, CTR, 7)
        xci = build_xci("05e6.nca", nca, 2 * 1024 * 1024)
        (root / "tsukihime.xci").write_bytes(xci)
        for rel, b in FILES.items():
            p = work / "expect" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)
        rs = ROOT / "tools" / "internal" / "romfs_source.py"

        r = py(rs, "info", "--keys", keys, "--input", root / "tsukihime.xci")
        check(f"PROGRAMID={PID:016X}" in r.stdout, "info: program ID", r.stdout)

        data = root / "data" / "tsuki"
        r = py(rs, "prepare", "--keys", keys, "--input", root / "tsukihime.xci", "--program-id", f"{PID:016X}",
               "--source", data / "romfs_source.bin", "--build-romfs", data / "romfs_build", "--exefs", data / "exefs")
        check(f"PREPARE files={len(FILES)} dirs=" in r.stdout and "exefs=3" in r.stdout, "prepare: summary line", r.stdout)
        ok = all((data / "exefs" / n).read_bytes() == b for n, b in EXEFS)
        check(ok, "prepare: exefs files equal")
        built = sorted(str(p.relative_to(data / "romfs_build")).replace(os.sep, "/") for p in (data / "romfs_build").rglob("*") if p.is_file())
        check(built == sorted(["shader/a.bnsh", "shader/sub/b.bnsh", "allui.hed", "allui.nam", "allui.mrg", "w_allui.mrg"]),
              "prepare: build files = shaders + *allui.hed/nam/mrg", str(built))
        check(all((data / "romfs_build" / r_).read_bytes() == FILES[r_] for r_ in built), "prepare: build files equal")
        raw = (data / "romfs_source.bin").read_bytes()
        check(KEY in raw and HEADER_KEY.hex() not in r.stdout and KEY.hex() not in r.stdout, "prepare: content key in the table, no key printed")

        r = py(rs, "locate", "--source", data / "romfs_source.bin", "--root", root)
        check(r.stdout.strip().endswith("tsukihime.xci"), "locate: finds the dump", r.stdout)
        (root / "tsukihime.xci").rename(root / "renamed.XCI")
        r = py(rs, "locate", "--source", data / "romfs_source.bin", "--root", root)
        check(r.stdout.strip().endswith("renamed.XCI"), "locate: finds it renamed (by its content)", r.stdout)
        (root / "renamed.XCI").rename(root / "tsukihime.xci")

        r = py(rs, "trim", "--keys", keys, "--input", root / "tsukihime.xci", "--program-id", f"{PID:016X}")
        check("TRIM would_save=2097152" in r.stdout, "trim (dry): would save the padding", r.stdout)
        bad = bytearray(xci)
        bad[-5] = 0
        (work / "bad.xci").write_bytes(bytes(bad))
        r = py(rs, "trim", "--keys", keys, "--input", work / "bad.xci", "--program-id", f"{PID:016X}", "--apply", check=False)
        check(r.returncode != 0 and "NOT trimmed" in (r.stdout + r.stderr) and (work / "bad.xci").stat().st_size == len(xci),
              "trim: a byte that is not 0xFF stops it, file untouched", r.stdout + r.stderr)
        r = py(rs, "trim", "--keys", keys, "--input", root / "tsukihime.xci", "--program-id", "010057A01E4D4000", "--apply", check=False)
        check(r.returncode == 3 and (root / "tsukihime.xci").stat().st_size == len(xci), "trim: another edition is not touched (code 3)", r.stdout)
        r = py(rs, "trim", "--keys", keys, "--input", root / "tsukihime.xci", "--program-id", f"{PID:016X}", "--apply")
        check("TRIM saved=2097152" in r.stdout and (root / "tsukihime.xci").stat().st_size == len(xci) - 2097152, "trim --apply: cut", r.stdout)
        r = py(rs, "trim", "--keys", keys, "--input", root / "tsukihime.xci", "--program-id", f"{PID:016X}", "--apply")
        check("TRIM saved=0" in r.stdout, "trim again: already trimmed", r.stdout)
        r = py(rs, "untrim", "--input", root / "tsukihime.xci")
        check("UNTRIM would_add=" in r.stdout, "untrim (dry): reports", r.stdout)
        r = py(rs, "locate", "--source", data / "romfs_source.bin", "--root", root)
        check(r.returncode == 0, "the table still matches the trimmed dump", r.stdout)
        nsp = build_pfs0([("05e6.nca", nca), ("x.cnmt.nca", b"\0" * 0x200)])
        (work / "game.nsp").write_bytes(nsp)
        r = py(rs, "trim", "--keys", keys, "--input", work / "game.nsp", "--program-id", f"{PID:016X}")
        check("not-xci" in r.stdout, "trim: an NSP is left alone", r.stdout)

        # mods_nsp.py mod: --base-dump against --index + --base-romfs (only once the patcher touched mods_nsp.py)
        mods_src = (ROOT / "tools" / "internal" / "mods_nsp.py").read_text(encoding="utf-8")
        if "AP_ROMFS_DIRECT" in mods_src:
            patched = dict(FILES)
            patched["movie/prologue.mp4"] = b"\0\0\0\x18ftypisom" + os.urandom(300000)  # same size, other bytes
            patched["stream/new.opus"] = struct.pack("<II", 0x80000001, 0x18) + os.urandom(50)
            pnca, _ = build_nca(build_romfs(patched), EXEFS, bytes(range(0x70, 0x80)), CTR, 9)
            (work / "patch.nsp").write_bytes(build_pfs0([("aaaa.nca", pnca)]))
            full = work / "full_romfs"
            for rel, b in FILES.items():
                p = full / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b)
            mp = ROOT / "tools" / "internal" / "mods_nsp.py"
            py(mp, "index", "--keys", keys, "--input", root / "tsukihime.xci", "--program-id", f"{PID:016X}",
               "--base-romfs", full, "--out", work / "idx.bin")
            r1 = py(mp, "mod", "--keys", keys, "--nsp", work / "patch.nsp", "--program-id", f"{PID:016X}", "--index", work / "idx.bin",
                    "--base-romfs", full, "--base-exefs", data / "exefs", "--out", work / "out_index")
            r2 = py(mp, "mod", "--keys", keys, "--nsp", work / "patch.nsp", "--program-id", f"{PID:016X}",
                    "--base-dump", root / "tsukihime.xci", "--base-exefs", data / "exefs", "--out", work / "out_dump")
            l1 = [l for l in r1.stdout.splitlines() if l.startswith("MOD ")]
            l2 = [l for l in r2.stdout.splitlines() if l.startswith("MOD ")]
            check(l1 and l1 == l2 and "changed=1 new=1" in l1[0], "mods_nsp mod: --base-dump = --index (changed 1, new 1)", r1.stdout + r2.stdout)
            f1 = sorted(str(p.relative_to(work / "out_index")) for p in (work / "out_index").rglob("*") if p.is_file())
            f2 = sorted(str(p.relative_to(work / "out_dump")) for p in (work / "out_dump").rglob("*") if p.is_file())
            same = f1 == f2 and all((work / "out_index" / x).read_bytes() == (work / "out_dump" / x).read_bytes() for x in f1 if not x.endswith("mod.json"))
            check(same, "mods_nsp mod: same output files", f"{f1}\n{f2}")
            sys.path.insert(0, str(ROOT / "tools" / "internal"))
            import importlib
            mm = importlib.reload(m)
            kk = mm.load_keys(str(keys))
            d = mm.DumpBase(str(root / "tsukihime.xci"), kk, PID)
            b = mm.BaseRomfs(mm.load_index(str(work / "idx.bin"), PID), str(full))
            rr = random.Random(5)
            okr = all(d.read_section(o, n) == b.read_section(o, n)
                      for o, n in ((d.l6_off + rr.randrange(0, d.l6_size - 1), rr.randrange(1, 9000)) for _ in range(300))
                      if o + n <= d.l6_off + d.l6_size)
            check(okr, "mods_nsp: DumpBase.read_section = BaseRomfs.read_section (300 random ranges)")
        else:
            print("skip mods_nsp --base-dump part: apply_romfs_direct.py not applied")

        if want_cxx:
            res = cxx_test(work, data, check)
            if res:
                exe, lst = res
                (root / "tsukihime.xci").rename(root / "other_name.xci")
                env = dict(os.environ, TEST_DATA_ROOT=str(data))
                r = subprocess.run([str(exe), str(lst), "3"], capture_output=True, text=True, env=env)
                check(r.returncode == 0 and "other_name.xci" in r.stdout, "C++: the dump found by its content after a rename", r.stdout[-800:])
                (root / "other_name.xci").rename(work / "away.xci")
                r = subprocess.run([str(exe), str(lst), "3"], capture_output=True, text=True, env=env)
                check(r.returncode == 7 and "Stopping" in r.stdout, "C++: no dump -> message and exit code 7", r.stdout[-800:])
                (data / "extract" / "romfs" / "shader").mkdir(parents=True)
                r = subprocess.run([str(exe), str(lst), "3"], capture_output=True, text=True, env=env)
                check(r.returncode == 2 and "extract/romfs is used instead" in r.stdout, "C++: no dump but a full extract/romfs -> disabled, no stop", r.stdout[-800:])
                shutil.rmtree(data / "extract")
                env2 = dict(env, NX_ROMFS_DUMP=str(work / "away.xci"))
                r = subprocess.run([str(exe), str(lst), "3"], capture_output=True, text=True, env=env2)
                check(r.returncode == 0, "C++: NX_ROMFS_DUMP points at a dump elsewhere", r.stdout[-800:])
                (data / "romfs_source.bin").write_bytes(raw[:-3])
                r = subprocess.run([str(exe), str(lst), "3"], capture_output=True, text=True, env=env2)
                check(r.returncode == 2 and "damaged" in r.stdout, "C++: a damaged table is ignored (extract/romfs as before)", r.stdout[-800:])
                (data / "romfs_source.bin").write_bytes(raw)
                (work / "away.xci").rename(root / "tsukihime.xci")
                if "AP_ROMFS_DIRECT" in (ROOT / "framework" / "runtime" / "src" / "hle_fs.cpp").read_text(encoding="utf-8"):
                    hle_test(work, data, root, check, shutil.which("g++") or shutil.which("clang++"))
                else:
                    print("skip hle_fs part: apply_romfs_direct.py not applied")

    print("all passed" if not Checker.fails else f"{Checker.fails} failed")
    return 1 if Checker.fails else 0


if __name__ == "__main__":
    sys.exit(main())
