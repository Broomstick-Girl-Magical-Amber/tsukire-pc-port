"""apply_any_container.py - the .xci and the .nsp of the same game are the same dump (AP_ANY_CONTAINER).

An .xci and an .nsp of the same game hold the same program NCA (same bytes), each at its own offset in the file: the
NSP has it in its root PFS0, the XCI in the secure partition of its root HFS0. romfs_source.bin (recomp step 2) keeps
the offset of the dump it was made from, and the exe (romfs_direct.cpp), the Android launcher (Home.java) and
"romfs_source.py locate" (recomp) only looked there: recompiled from the .xci, the .nsp of the same game was "not the
dump" (and the other way round), with "Game data not found" on start.

Now the recorded offset is tried first (the dump it was made from: nothing changes, no extra read) and, if it does not
match, the file table of the container is read (NSP: the root PFS0; XCI: every partition of the root HFS0, secure
first; also an XCI with the 0x1000-byte key area in front) and the program NCA is the entry with the same size and the
same first 0x100 bytes. The exe then reads at the offset found (romfs_source.bin is not rewritten: the same table works
with either container, so switching from one to the other needs no recompile). Trimmed or not makes no difference
(the NCA must be whole in the file, as before).

Edits games/tsukihime_common/romfs_direct.cpp (is_the_dump / find_dump and the "Game data not found" box),
../tools/internal/romfs_source.py (locate), ../android/java/io/github/tsukire/launcher/Home.java (isTheDump and the
"not the dump" message) and ../tools/recomp_tsukihime.ps1 (two $Markers rows, after the one of apply_android_romfs.py).
Idempotent (marker AP_ANY_CONTAINER), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as
they were. Needs apply_romfs_direct.py, apply_mod_recipes.py and apply_android_romfs.py (their anchors). Then
recompile (step 9 is enough: the table does not change) and rebuild the APK for the launcher part.
Test: python tools/tests/test_any_container.py --cxx
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_any_container.py
"""
from _patch import run

MARK = "AP_ANY_CONTAINER"
RD = "games/tsukihime_common/romfs_direct.cpp"
PY = "../tools/internal/romfs_source.py"
JAVA = "../android/java/io/github/tsukire/launcher/Home.java"
RECO = "../tools/recomp_tsukihime.ps1"

EDITS = [
    # ------------------------------------------------------------------------------------------------ romfs_direct.cpp
    (RD,
     '''bool is_the_dump(const fs::path& p, const Source& s) {
    std::error_code ec;
    if (!fs::is_regular_file(p, ec)) return false;
    uint64_t size = fs::file_size(p, ec);
    if (ec || size < s.nca_off + s.nca_size) return false;
    FILE* f = nx::plat::fopen_utf8(utf8(p), "rb");
    if (!f) return false;
    uint8_t buf[0x100];
    bool ok = nx::plat::read_at(f, int64_t(s.nca_off), buf, sizeof buf) == sizeof buf &&
              std::memcmp(buf, s.sig, sizeof buf) == 0;
    std::fclose(f);
    return ok;
}
''',
     '''// AP_ANY_CONTAINER (apply_any_container.py): an .xci and an .nsp of the same game hold the same program NCA, each at
// its own offset. The offset of the table (the dump it was made from) is tried first; otherwise the file table of the
// container (NSP: root PFS0; XCI: every partition of the root HFS0, secure first) gives the entry with the same size
// and the same first 0x100 bytes.
constexpr uint64_t kNoNca = ~uint64_t(0);

uint32_t rd32(const uint8_t* p) {
    uint32_t v;
    std::memcpy(&v, p, 4);
    return v;
}

uint64_t rd64(const uint8_t* p) {
    uint64_t v;
    std::memcpy(&v, p, 8);
    return v;
}

bool nca_at(FILE* f, uint64_t size, uint64_t off, const Source& s) {
    if (off > size || s.nca_size > size - off || s.nca_size < 0x100) return false;
    uint8_t buf[0x100];
    return nx::plat::read_at(f, int64_t(off), buf, sizeof buf) == sizeof buf && std::memcmp(buf, s.sig, sizeof buf) == 0;
}

struct PartEntry {
    std::string name;
    uint64_t off = 0, size = 0;  // offset in the whole file
};

// The entries of the PFS0 / HFS0 at `at` (esize 0x18 / 0x40) appended to out; false if there is none there.
bool partition_entries(FILE* f, uint64_t size, uint64_t at, const char* magic, size_t esize, std::vector<PartEntry>& out) {
    uint8_t h[0x10];
    if (at > size || size - at < 0x10 || nx::plat::read_at(f, int64_t(at), h, sizeof h) != sizeof h ||
        std::memcmp(h, magic, 4) != 0)
        return false;
    const uint32_t count = rd32(h + 4), strsize = rd32(h + 8);
    if (count == 0 || count > 4096 || strsize > (1u << 20)) return false;
    const uint64_t tab = uint64_t(count) * esize + strsize;
    if (size - at - 0x10 < tab) return false;
    std::vector<uint8_t> t(static_cast<size_t>(tab));
    if (nx::plat::read_at(f, int64_t(at + 0x10), t.data(), tab) != tab) return false;
    const uint8_t* strs = t.data() + size_t(count) * esize;
    const uint64_t data = at + 0x10 + tab;
    for (uint32_t i = 0; i < count; i++) {
        const uint8_t* e = t.data() + size_t(i) * esize;
        PartEntry pe;
        const uint32_t no = rd32(e + 16);
        if (no < strsize) {
            const void* z = std::memchr(strs + no, 0, strsize - no);
            pe.name.assign(reinterpret_cast<const char*>(strs + no),
                           z ? size_t(static_cast<const uint8_t*>(z) - (strs + no)) : size_t(strsize - no));
        }
        pe.off = data + rd64(e);
        pe.size = rd64(e + 8);
        out.push_back(std::move(pe));
    }
    return true;
}

uint64_t find_nca(FILE* f, uint64_t size, const Source& s) {
    if (nca_at(f, size, s.nca_off, s)) return s.nca_off;  // the dump the table was made from (or one laid out the same)
    std::vector<PartEntry> files;
    if (!partition_entries(f, size, 0, "PFS0", 0x18, files)) {
        for (uint64_t base : {uint64_t(0), uint64_t(0x1000)}) {  // XCI; 0x1000: with the key area in front
            uint8_t hd[0x40];
            if (nx::plat::read_at(f, int64_t(base + 0x100), hd, sizeof hd) != sizeof hd || std::memcmp(hd, "HEAD", 4) != 0)
                continue;
            std::vector<PartEntry> parts;
            if (partition_entries(f, size, base + rd64(hd + 0x30), "HFS0", 0x40, parts)) {
                std::stable_partition(parts.begin(), parts.end(), [](const PartEntry& e) { return e.name == "secure"; });
                for (const auto& p : parts) partition_entries(f, size, p.off, "HFS0", 0x40, files);
            }
            break;
        }
    }
    for (const auto& e : files)
        if (e.size == s.nca_size && nca_at(f, size, e.off, s)) return e.off;
    return kNoNca;
}

// The program NCA of the table in p (an .xci or an .nsp of the same game): its offset in *nca_off.
bool is_the_dump(const fs::path& p, const Source& s, uint64_t* nca_off) {
    std::error_code ec;
    if (!fs::is_regular_file(p, ec)) return false;
    uint64_t size = fs::file_size(p, ec);
    if (ec) return false;
    FILE* f = nx::plat::fopen_utf8(utf8(p), "rb");
    if (!f) return false;
    const uint64_t at = find_nca(f, size, s);
    std::fclose(f);
    if (at == kNoNca) return false;
    *nca_off = at;
    return true;
}

// AP_ANY_CONTAINER: the dump found, with the NCA where it is in that file (the reads use s.nca_off).
std::string use_dump(Source& s, const fs::path& p, uint64_t at) {
    if (at != s.nca_off)
        nx::log("romfs: %s is another container of the same game: its program NCA is at 0x%llx (0x%llx in %s)",
                utf8(p).c_str(), (unsigned long long)at, (unsigned long long)s.nca_off, s.hint.c_str());
    s.nca_off = at;
    return utf8(p);
}
'''),
    (RD,
     '''std::string find_dump(const Source& s) {
    if (const char* e = std::getenv("NX_ROMFS_DUMP"); e && *e) {
        fs::path p = fs::u8path(e);
        if (is_the_dump(p, s)) return utf8(p);
''',
     '''std::string find_dump(Source& s) {  // AP_ANY_CONTAINER: sets s.nca_off for the dump found
    uint64_t at = 0;
    if (const char* e = std::getenv("NX_ROMFS_DUMP"); e && *e) {
        fs::path p = fs::u8path(e);
        if (is_the_dump(p, s, &at)) return use_dump(s, p, at);
'''),
    (RD,
     '''    for (const auto& p : cands)
        if (is_the_dump(p, s)) return utf8(p);
    return {};
''',
     '''    for (const auto& p : cands)
        if (is_the_dump(p, s, &at)) return use_dump(s, p, at);  // AP_ANY_CONTAINER
    return {};
'''),
    (RD,
     '''                       L"Put the .xci/.nsp back there (the same file the game was recompiled from; renaming it is fine)\\n"
''',
     '''                       L"Put the .xci or the .nsp of that same game back there (either one, with any name)\\n"  // AP_ANY_CONTAINER
'''),

    # ------------------------------------------------------------------------------------------------ romfs_source.py
    (PY,
     '''def matches(path, nca_off, nca_size, sig):
    try:
        if os.path.getsize(path) < nca_off + nca_size:
            return False
        with open(path, "rb") as f:
            f.seek(nca_off)
            return f.read(0x100) == sig
    except OSError:
        return False
''',
     '''def _entries(f, size, at, magic, esize):
    """AP_ANY_CONTAINER: [(name, offset in the file, size)] of the PFS0 / HFS0 at `at`; None if there is none."""
    if at + 0x10 > size:
        return None
    f.seek(at)
    h = f.read(0x10)
    if len(h) != 0x10 or h[:4] != magic:
        return None
    count, strsize = u32(h, 4), u32(h, 8)
    if count == 0 or count > 4096 or strsize > (1 << 20) or at + 0x10 + count * esize + strsize > size:
        return None
    tab = f.read(count * esize + strsize)
    strs, data, out = tab[count * esize:], at + 0x10 + count * esize + strsize, []
    for i in range(count):
        e = tab[i * esize:(i + 1) * esize]
        no = u32(e, 16)
        name = strs[no:strs.find(b"\\0", no) if strs.find(b"\\0", no) >= 0 else len(strs)].decode("utf-8", "replace")
        out.append((name, data + u64(e, 0), u64(e, 8)))
    return out


def matches(path, nca_off, nca_size, sig):
    """AP_ANY_CONTAINER: offset of the program NCA of the table in path (an .xci or an .nsp of the same game), or None.
    The recorded offset first (the dump it was made from), then the file table of the container, as romfs_direct.cpp."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            def at(o):
                if o + nca_size > size or nca_size < 0x100:
                    return False
                f.seek(o)
                return f.read(0x100) == sig
            if at(nca_off):
                return nca_off
            files = _entries(f, size, 0, b"PFS0", 0x18)
            if files is None:
                files = []
                for base in (0, 0x1000):  # XCI; 0x1000: with the key area in front
                    f.seek(base + 0x100)
                    hd = f.read(0x40)
                    if len(hd) != 0x40 or hd[:4] != b"HEAD":
                        continue
                    parts = _entries(f, size, base + u64(hd, 0x30), b"HFS0", 0x40) or []
                    for _, o, _ in sorted(parts, key=lambda p: p[0] != "secure"):
                        files += _entries(f, size, o, b"HFS0", 0x40) or []
                    break
            for _, o, n in files:
                if n == nca_size and at(o):
                    return o
    except OSError:
        pass
    return None
'''),
    (PY,
     '''    for p in cands:
        if matches(p, nca_off, nca_size, sig):
            print(f"DUMP={os.path.abspath(p)}")
            return
''',
     '''    for p in cands:
        found = matches(p, nca_off, nca_size, sig)  # AP_ANY_CONTAINER: an .xci or an .nsp of the same game
        if found is not None:
            if found != nca_off:
                print(f"   {os.path.basename(p)}: another container of the same game (program NCA at {found:#x}, "
                      f"{nca_off:#x} in {hint}): fine, the game finds it")
            print(f"DUMP={os.path.abspath(p)}")
            return
'''),

    # ------------------------------------------------------------------------------------------------ Home.java
    (JAVA,
     '''    static boolean isTheDump(File p, Source s) {
        if (s == null || !p.isFile() || p.length() < s.ncaOff + s.ncaSize) return false;
        try (RandomAccessFile r = new RandomAccessFile(p, "r")) {
            byte[] buf = new byte[0x100];
            r.seek(s.ncaOff);
            r.readFully(buf);
            return Arrays.equals(buf, s.sig);
        } catch (Exception e) {
            return false;
        }
    }
''',
     '''    // AP_ANY_CONTAINER (apply_any_container.py): an .xci or an .nsp of the same game (the same program NCA, each at its
    // own offset): the offset of the table first, then the file table of the container, as romfs_direct.cpp.
    static boolean isTheDump(File p, Source s) {
        if (s == null || !p.isFile()) return false;
        try (RandomAccessFile r = new RandomAccessFile(p, "r")) {
            long size = r.length();
            if (ncaAt(r, size, s.ncaOff, s)) return true;
            List<long[]> files = entries(r, size, 0, "PFS0", 0x18, null);
            if (files == null) {
                files = new ArrayList<>();
                for (long base : new long[] {0, 0x1000}) {  // XCI; 0x1000: with the key area in front
                    byte[] hd = new byte[0x40];
                    if (base + 0x140 > size) continue;
                    r.seek(base + 0x100);
                    r.readFully(hd);
                    if (!new String(hd, 0, 4, StandardCharsets.US_ASCII).equals("HEAD")) continue;
                    List<String> names = new ArrayList<>();
                    List<long[]> parts = entries(r, size, base + le(hd, 0x30, 8), "HFS0", 0x40, names);
                    if (parts != null)
                        for (int pass = 0; pass < 2; pass++)  // secure first
                            for (int i = 0; i < parts.size(); i++)
                                if (names.get(i).equals("secure") == (pass == 0)) {
                                    List<long[]> sub = entries(r, size, parts.get(i)[0], "HFS0", 0x40, null);
                                    if (sub != null) files.addAll(sub);
                                }
                    break;
                }
            }
            for (long[] e : files)
                if (e[1] == s.ncaSize && ncaAt(r, size, e[0], s)) return true;
            return false;
        } catch (Exception e) {
            return false;
        }
    }

    static long le(byte[] b, int o, int n) {
        long v = 0;
        for (int i = n - 1; i >= 0; i--) v = (v << 8) | (b[o + i] & 0xff);
        return v;
    }

    static boolean ncaAt(RandomAccessFile r, long size, long off, Source s) throws Exception {
        if (off < 0 || s.ncaSize < 0x100 || off > size || s.ncaSize > size - off) return false;
        byte[] buf = new byte[0x100];
        r.seek(off);
        r.readFully(buf);
        return Arrays.equals(buf, s.sig);
    }

    // {offset in the file, size} of every entry of the PFS0 / HFS0 at `at` (names in `names` if given); null if none.
    static List<long[]> entries(RandomAccessFile r, long size, long at, String magic, int esize, List<String> names) throws Exception {
        if (at < 0 || at + 0x10 > size) return null;
        byte[] h = new byte[0x10];
        r.seek(at);
        r.readFully(h);
        if (!new String(h, 0, 4, StandardCharsets.US_ASCII).equals(magic)) return null;
        long count = le(h, 4, 4), strsize = le(h, 8, 4);
        if (count == 0 || count > 4096 || strsize > (1 << 20) || at + 0x10 + count * esize + strsize > size) return null;
        byte[] t = new byte[(int) (count * esize + strsize)];
        r.readFully(t);
        long data = at + 0x10 + t.length;
        int strs = (int) (count * esize);
        List<long[]> out = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            int e = i * esize;
            out.add(new long[] {data + le(t, e, 8), le(t, e + 8, 8)});
            if (names != null) {
                int a = strs + (int) le(t, e + 16, 4), z = a;
                while (z >= 0 && z < t.length && t[z] != 0) z++;
                names.add(a >= strs && a < t.length ? new String(t, a, z - a, StandardCharsets.UTF_8) : "");
            }
        }
        return out;
    }
'''),
    (JAVA,
     '''            say(t("Ese archivo no es el dump con el que se recompil\\u00f3 el juego", "That file is not the dump the game was recompiled from")
''',
     '''            say(t("Ese archivo no es un dump (.xci o .nsp) del juego con el que se recompil\\u00f3", "That file is not a dump (.xci or .nsp) of the game it was recompiled from")  // AP_ANY_CONTAINER
'''),

    # ------------------------------------------------------------------------------------------------ recomp $Markers
    (RECO,
     '''    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ANDROID_ROMFS', 'apply_android_romfs.py', $false),
''',
     '''    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ANDROID_ROMFS', 'apply_android_romfs.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ANY_CONTAINER', 'apply_any_container.py', $false),
    @('both', 'tools\\internal\\romfs_source.py', 'AP_ANY_CONTAINER', 'apply_any_container.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
