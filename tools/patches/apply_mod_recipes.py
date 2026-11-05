"""apply_mod_recipes.py - big files of an update are kept as recipes over the dump, not copied whole (AP_MOD_RECIPES).

An update / translation patch .nsp (BKTR) carries only the pieces of the RomFS it changes. mods_nsp.py mod wrote every
changed file WHOLE to mods\\<edition>\\<name>\\romfs: tsukihimates (2.5 GB .nsp) turned into 21 GB there, almost all of
it allpac.mrg (16.7 GB) and allpaccg.mrg (2 GB), archives where the patch replaces a few entries and the rest is the
base game, copied out of the dump. Now (only with --base-dump, the default of recomp_tsukihime.ps1 since
AP_ROMFS_DIRECT) a changed file of at least --recipe-min-mb (256) whose bytes come mostly from the base is written as
<file>.nxrcp: the ranges of the file in order, each one either "these bytes of the RomFS of the dump" or "these bytes
of the patch" (stored decrypted in the recipe). The game rebuilds the file while it reads it
(games/tsukihime_common/romfs_direct.cpp). Files the build tools read (shader/, *allui.*) are always whole. Every
recipe is checked right after it is written (random ranges against the patched RomFS). A processed update is processed
again on the next recompilation if its .nsp is still in updates\\ (the stamp includes mods_nsp.py); without the .nsp
its whole files stay and keep working.

Format of a .nxrcp (little endian): "NXRCP1\\0\\0", u32 version 1, u32 n, u64 file size, u64 program ID of the base,
u64 RomFS size of the base, u64 offset of the patch bytes in this file; n x (u64 length, u64 source: RomFS offset, or
top bit set + offset in the patch bytes); "END1"; the patch bytes.

Touches ../tools/internal/mods_nsp.py, games/tsukihime_common/romfs_direct.cpp, runtime/src/hle_fs.cpp and
../tools/recomp_tsukihime.ps1 (summary line and its $Markers). Needs apply_romfs_direct.py. Idempotent (marker
AP_MOD_RECIPES), all or nothing, keeps CRLF and BOM, --remove leaves the files byte for byte as they were.
Apply: cd <root>/framework; python ../tools/patches/apply_mod_recipes.py (the venv python)
"""
from _patch import run

MARK = "AP_MOD_RECIPES"
MODS = "../tools/internal/mods_nsp.py"
RD = "games/tsukihime_common/romfs_direct.cpp"
HLE = "runtime/src/hle_fs.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

# ------------------------------------------------------------------------------------------------ mods_nsp.py
PY_FUNCS = r'''# ---------------------------------------------------------------------------------------------------------- AP_MOD_RECIPES
# A big changed file that is mostly the base game is written as <file>.nxrcp (ranges of the RomFS of the dump + the
# bytes of the patch) instead of whole; the game rebuilds it while reading (romfs_direct.cpp). See apply_mod_recipes.py.
RECIPE_EXT = ".nxrcp"
RECIPE_MAGIC = b"NXRCP1\0\0"
RECIPE_LIT = 1 << 63


def recipe_plan(vs, voff, size, idx, rel, a):
    """[[kind, source, length]...] (kind 0 = RomFS offset of the base, 1 = virtual offset of the patched RomFS) and the
    bytes that come from the base, or None when the file is written whole."""
    if vs is None or a.recipe_min_mb <= 0 or size < (a.recipe_min_mb << 20):
        return None
    low = rel.lower()
    if low.startswith("shader/") or "allui." in low:  # read by the build tools (shaders, HUD icons)
        return None
    l6o, l6s = idx["l6_offset"], idx["l6_size"]
    ext, base_bytes, pos = [], 0, 0
    for s, p, m in vs.pieces(voff, size):
        if s == 0 and l6o <= p and p + m <= l6o + l6s:
            bo = p - l6o
            if ext and ext[-1][0] == 0 and ext[-1][1] + ext[-1][2] == bo:
                ext[-1][2] += m
            else:
                ext.append([0, bo, m])
            base_bytes += m
        elif ext and ext[-1][0] == 1:
            ext[-1][2] += m
        else:
            ext.append([1, voff + pos, m])
        pos += m
    if pos != size or base_bytes * 2 < size:
        return None
    return ext, base_bytes


def write_recipe(path, plan, size, pid, idx, vread, voff, dump, md5):
    """Writes <path> (.nxrcp) and checks it against the patched RomFS; returns the bytes not copied."""
    ext, base_bytes = plan
    head = 48 + 16 * len(ext) + 4
    rows, blob = [], 0
    for kind, src, m in ext:
        rows.append(struct.pack("<QQ", m, src if kind == 0 else RECIPE_LIT | blob))
        blob += m if kind == 1 else 0
    with open(path + ".tmp", "wb") as f:
        f.write(RECIPE_MAGIC + struct.pack("<IIQQQQ", 1, len(ext), size, pid, idx["l6_size"], head))
        f.write(b"".join(rows) + b"END1")
        for kind, src, m in ext:
            for c in range(0, m, CHUNK):
                n = min(CHUNK, m - c)
                if kind == 1:
                    d = vread(src + c, n)
                    f.write(d)
                    if md5 is not None:
                        md5.update(d)
                elif md5 is not None:
                    md5.update(dump.read_section(idx["l6_offset"] + src + c, n))
    os.replace(path + ".tmp", path)
    import random
    r = RecipeReader(path, dump, idx)
    rnd = random.Random(size)
    probes = [(0, min(size, 1 << 16)), (max(0, size - (1 << 16)), min(size, 1 << 16))]
    probes += [(o, min(size - o, 1 << 20)) for o in (rnd.randrange(0, size) for _ in range(48))]
    for kind, src, m in ext:  # both edges of every patch range (at most 200)
        if kind == 1 and len(probes) < 250:
            o = src - voff
            probes += [(max(0, o - 64), min(size - max(0, o - 64), 128)), (max(0, o + m - 64), min(size - max(0, o + m - 64), 128))]
    for o, n in probes:
        if n > 0 and r.read(o, n) != vread(voff + o, n):
            os.remove(path)
            fail(f"recipe of {os.path.basename(path)}: bytes at {o:#x} differ from the patched romfs (nothing kept)")
    return base_bytes


class RecipeReader:
    """A .nxrcp read back (bytes of the base from the dump)."""

    def __init__(self, path, dump, idx):
        self.f = open(path, "rb")
        hd = self.f.read(48)
        if hd[:8] != RECIPE_MAGIC:
            fail(f"{path} is not a recipe")
        _, n, self.size, _, _, self.data = struct.unpack_from("<IIQQQQ", hd, 8)
        rows = self.f.read(16 * n)
        self.ext, at = [], 0
        for k in range(n):
            m, src = struct.unpack_from("<QQ", rows, 16 * k)
            self.ext.append((at, m, src))
            at += m
        self.starts = [e[0] for e in self.ext]
        self.dump, self.l6 = dump, idx["l6_offset"]

    def read(self, off, n):
        out = bytearray()
        k = bisect.bisect_right(self.starts, off) - 1
        while n > 0 and 0 <= k < len(self.ext):
            at, m, src = self.ext[k]
            i = off - at
            c = min(m - i, n)
            if src & RECIPE_LIT:
                self.f.seek(self.data + (src & ~RECIPE_LIT) + i)
                out += self.f.read(c)
            else:
                out += self.dump.read_section(self.l6 + src + i, c)
            off, n, k = off + c, n - c, k + 1
        return bytes(out)


def cmd_unrecipe(a):
    """Rebuilds the whole file of a .nxrcp (for tools that need it on disk)."""
    d = DumpBase(a.base_dump, load_keys(a.keys), int(a.program_id, 16))
    r = RecipeReader(a.recipe, d, d.index)
    with open(a.out + ".tmp", "wb") as f:
        for o in range(0, r.size, CHUNK):
            f.write(r.read(o, min(CHUNK, r.size - o)))
    os.replace(a.out + ".tmp", a.out)
    print(f"UNRECIPE size={r.size}")


def main():
'''

PY_LOOP_OLD = '''        else:
            dst = to_host(out_romfs, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            equal = b is not None and b[1] == size
            bf = base_open(a, dump_base, b) if equal else None  # AP_ROMFS_DIRECT
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
'''
PY_LOOP_NEW = '''        else:
            dst = to_host(out_romfs, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            plan = recipe_plan(vs, voff, size, idx, rel, a) if dump_base is not None and b is not None else None  # AP_MOD_RECIPES
            if plan is not None:
                not_copied += write_recipe(dst + RECIPE_EXT, plan, size, int(a.program_id, 16), idx, vread, voff, dump_base, md5)
                recipes.append(rel)
                changed += 1
            else:
                equal = b is not None and b[1] == size
                bf = base_open(a, dump_base, b) if equal else None  # AP_ROMFS_DIRECT
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
'''

PY_EDITS = [
    (MODS, PY_LOOP_OLD, PY_LOOP_NEW),
    (MODS,
     '''    unchanged = changed = new = verified = 0
    mismatch = []
''',
     '''    unchanged = changed = new = verified = 0
    mismatch = []
    recipes, not_copied = [], 0  # AP_MOD_RECIPES
'''),
    (MODS,
     '''            "changed": changed, "new": new, "unchanged": unchanged, "removed": removed, "exefs_different": exefs_diff,
''',
     '''            "changed": changed, "new": new, "unchanged": unchanged, "removed": removed, "exefs_different": exefs_diff,
            "recipes": recipes, "recipe_bytes_not_copied": not_copied,  # AP_MOD_RECIPES
'''),
    (MODS,
     '''    print(f"   romfs: {len(pfiles)} files; changed {changed}, new {new}, unchanged {unchanged}, missing in the patch {len(removed)}")
''',
     '''    print(f"   romfs: {len(pfiles)} files; changed {changed}, new {new}, unchanged {unchanged}, missing in the patch {len(removed)}")
    if recipes:  # AP_MOD_RECIPES
        print(f"   {len(recipes)} of them kept as recipes over the dump ({not_copied / (1 << 30):.2f} GiB of the base not copied): "
              f"{', '.join(recipes[:5])}")
'''),
    (MODS, '''

def main():
''', '''

''' + PY_FUNCS),
    (MODS,
     '''    m.add_argument("--verify-csv")
    a = ap.parse_args()
    (cmd_index if a.cmd == "index" else cmd_mod)(a)
''',
     '''    m.add_argument("--verify-csv")
    m.add_argument("--recipe-min-mb", type=int, default=256)  # AP_MOD_RECIPES: 0 = every changed file whole
    u = sp.add_parser("unrecipe")  # AP_MOD_RECIPES: the whole file of a .nxrcp
    u.add_argument("--keys", required=True)
    u.add_argument("--base-dump", required=True)
    u.add_argument("--program-id", required=True)
    u.add_argument("--recipe", required=True)
    u.add_argument("--out", required=True)
    a = ap.parse_args()
    {"index": cmd_index, "mod": cmd_mod, "unrecipe": cmd_unrecipe}[a.cmd](a)  # AP_MOD_RECIPES
'''),
]

# ------------------------------------------------------------------------------------------------ romfs_direct.cpp
RD_EDITS = [
    (RD,
     '''    return new Handle{f, it->second.off, it->second.size};
''',
     '''    Handle* h = new Handle;  // AP_MOD_RECIPES: Handle has more members now (no brace init)
    h->f = f;
    h->start = it->second.off;
    h->size = it->second.size;
    return h;
'''),
    (RD,
     '''struct Handle {
    FILE* f = nullptr;
    uint64_t start = 0;  // offset of the file in the RomFS image
    uint64_t size = 0;
};
''',
     '''struct Handle {
    FILE* f = nullptr;
    uint64_t start = 0;  // offset of the file in the RomFS image
    uint64_t size = 0;
    // AP_MOD_RECIPES: a file of a mods layer kept as <file>.nxrcp (tools/internal/mods_nsp.py): its ranges, in order,
    // come from the RomFS of the dump (f) or from the patch bytes stored in the recipe (rf).
    FILE* rf = nullptr;
    uint64_t data_off = 0;
    std::vector<uint64_t> at, len, src;  // offset in the file, length, source (top bit: in rf; else RomFS offset)
};

uint64_t read_recipe(Handle* h, int64_t off, void* buf, uint64_t n);  // AP_MOD_RECIPES
'''),
    (RD,
     '''uint64_t read(Handle* h, int64_t off, void* buf, uint64_t n) {
    if (!h || off < 0 || uint64_t(off) >= h->size) return 0;
''',
     '''uint64_t read(Handle* h, int64_t off, void* buf, uint64_t n) {
    if (h && h->rf) return read_recipe(h, off, buf, n);  // AP_MOD_RECIPES
    if (!h || off < 0 || uint64_t(off) >= h->size) return 0;
'''),
    (RD,
     '''void close(Handle* h) {
    if (!h) return;
    if (h->f) std::fclose(h->f);
    delete h;
}

bool read_all(const std::string& rel, std::string& out) {
    Handle* h = open(rel);
    if (!h) return false;
''',
     '''void close(Handle* h) {
    if (!h) return;
    if (h->f) std::fclose(h->f);
    if (h->rf) std::fclose(h->rf);  // AP_MOD_RECIPES
    delete h;
}

// ---- AP_MOD_RECIPES: <file>.nxrcp of a mods layer --------------------------------------------------------------

namespace {
const uint64_t kRecipeLit = uint64_t(1) << 63;

uint64_t le64(const uint8_t* p) {
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--) v = (v << 8) | p[i];
    return v;
}

// Header of a recipe: false if it is not one (or not version 1).
bool recipe_header(FILE* f, uint32_t* n, uint64_t* size, uint64_t* pid, uint64_t* romfs, uint64_t* data) {
    uint8_t hd[48];
    if (!f || nx::plat::read_at(f, 0, hd, sizeof hd) != sizeof hd || std::memcmp(hd, "NXRCP1\\0\\0", 8) != 0) return false;
    if (uint32_t(le64(hd + 8)) != 1) return false;
    *n = uint32_t(le64(hd + 8) >> 32);
    *size = le64(hd + 16);
    *pid = le64(hd + 24);
    *romfs = le64(hd + 32);
    *data = le64(hd + 40);
    return true;
}
}  // namespace

bool recipe_size(const std::string& path, uint64_t* size) {
    FILE* f = nx::plat::fopen_utf8(path, "rb");
    uint32_t n = 0;
    uint64_t pid = 0, romfs = 0, data = 0;
    const bool ok = recipe_header(f, &n, size, &pid, &romfs, &data);
    if (f) std::fclose(f);
    return ok;
}

Handle* open_recipe(const std::string& path) {
    Source& s = src();
    if (!s.usable) {
        nx::log("romfs: %s needs the dump (the game is not reading its RomFS from it)", path.c_str());
        return nullptr;
    }
    FILE* rf = nx::plat::fopen_utf8(path, "rb");
    uint32_t n = 0;
    uint64_t size = 0, pid = 0, romfs = 0, data = 0;
    if (!recipe_header(rf, &n, &size, &pid, &romfs, &data)) {
        nx::log("romfs: %s is not a recipe of version 1", path.c_str());
        if (rf) std::fclose(rf);
        return nullptr;
    }
    if (pid != s.program_id || romfs != s.romfs_size) {
        nx::log("romfs: %s was made against another dump (program %016llx, RomFS %llu bytes): recompile to process the "
                "update again", path.c_str(), (unsigned long long)pid, (unsigned long long)romfs);
        std::fclose(rf);
        return nullptr;
    }
    std::vector<uint8_t> rows(size_t(n) * 16 + 4);
    bool ok = nx::plat::read_at(rf, 48, rows.data(), rows.size()) == rows.size() &&
              std::memcmp(rows.data() + size_t(n) * 16, "END1", 4) == 0 && data == 48 + rows.size();
    Handle* h = new Handle;
    h->size = size;
    h->rf = rf;
    h->data_off = data;
    uint64_t at = 0;
    for (uint32_t k = 0; ok && k < n; k++) {
        const uint64_t m = le64(&rows[size_t(k) * 16]), from = le64(&rows[size_t(k) * 16 + 8]);
        if (!(from & kRecipeLit) && from + m > s.romfs_size) ok = false;
        h->at.push_back(at);
        h->len.push_back(m);
        h->src.push_back(from);
        at += m;
    }
    ok = ok && at == size;
    if (ok) {
        h->f = nx::plat::fopen_utf8(s.dump, "rb");
        ok = h->f != nullptr;
    }
    if (!ok) {
        nx::log("romfs: %s is damaged", path.c_str());
        close(h);
        return nullptr;
    }
    return h;
}

uint64_t read_recipe(Handle* h, int64_t off, void* buf, uint64_t n) {
    if (!h || off < 0 || uint64_t(off) >= h->size) return 0;
    n = std::min<uint64_t>(n, h->size - uint64_t(off));
    uint8_t* out = static_cast<uint8_t*>(buf);
    size_t k = size_t(std::upper_bound(h->at.begin(), h->at.end(), uint64_t(off)) - h->at.begin()) - 1;
    uint64_t done = 0;
    while (done < n && k < h->at.size()) {
        const uint64_t in = uint64_t(off) + done - h->at[k];
        const uint64_t m = std::min<uint64_t>(h->len[k] - in, n - done);
        uint64_t got;
        if (h->src[k] & kRecipeLit) {
            got = nx::plat::read_at(h->rf, int64_t(h->data_off + (h->src[k] & ~kRecipeLit) + in), out + done, m);
        } else {
            Handle b;  // that range of the RomFS of the dump
            b.f = h->f;
            b.start = h->src[k];
            b.size = h->len[k];
            got = read(&b, int64_t(in), out + done, m);
        }
        done += got;
        if (got != m) break;
        k++;
    }
    return done;
}

bool read_all_recipe(const std::string& path, std::string& out) {
    Handle* h = open_recipe(path);
    if (!h) return false;
    out.resize(size_t(h->size));
    const uint64_t n = h->size ? read(h, 0, &out[0], h->size) : 0;
    close(h);
    if (n != out.size()) {
        nx::log("romfs: short read of %s (%llu of %zu bytes)", path.c_str(), (unsigned long long)n, out.size());
        out.clear();
        return false;
    }
    return true;
}

bool read_all(const std::string& rel, std::string& out) {
    Handle* h = open(rel);
    if (!h) return false;
'''),
]

# ------------------------------------------------------------------------------------------------ hle_fs.cpp
HLE_EDITS = [
    (HLE,
     '''void close(Handle* h);
bool read_all(const std::string& rel, std::string& out);
}  // namespace tsuki_romfs
''',
     '''void close(Handle* h);
bool read_all(const std::string& rel, std::string& out);
// AP_MOD_RECIPES (apply_mod_recipes.py): <file>.nxrcp of a mods layer = that file rebuilt from the dump + patch bytes.
Handle* open_recipe(const std::string& path);
bool recipe_size(const std::string& path, uint64_t* size);
bool read_all_recipe(const std::string& path, std::string& out);
}  // namespace tsuki_romfs
'''),
    # helper next to rom_direct_rel
    (HLE,
     '''    if (fs::is_regular_file(p, ec)) return false;
    rel = s;
    return true;
}
#endif
''',
     '''    if (fs::is_regular_file(p, ec)) return false;
    rel = s;
    return true;
}

// AP_MOD_RECIPES: a host path that is the recipe (<file>.nxrcp) of a mods layer.
bool is_recipe(const fs::path& p) {
    const std::string s = utf8(p.filename());
    if (s.size() <= 6) return false;
    std::string e = s.substr(s.size() - 6);
    for (char& ch : e) ch = char(std::tolower(static_cast<unsigned char>(ch)));
    return e == ".nxrcp";
}
#endif
'''),
    # rom_layer_path: a layer's recipe counts as its file (only while the dump is read)
    (HLE,
     '''        if (fs::is_regular_file(q, ec)) {
            if (trace_fs()) log("fs: %s from the layer %s", rest.c_str(), utf8(it->parent_path().filename()).c_str());
            return q;
        }
''',
     '''        if (fs::is_regular_file(q, ec)) {
            if (trace_fs()) log("fs: %s from the layer %s", rest.c_str(), utf8(it->parent_path().filename()).c_str());
            return q;
        }
#ifdef NX_ROMFS_DIRECT
        fs::path r = q;  // AP_MOD_RECIPES: the file kept as a recipe over the dump
        r += ".nxrcp";
        if (tsuki_romfs::enabled() && fs::is_regular_file(r, ec)) {
            if (trace_fs()) log("fs: %s from the layer %s (recipe)", rest.c_str(), utf8(it->parent_path().filename()).c_str());
            return r;
        }
#endif
'''),
    # rom_merge_dirs: recipes listed under their real name and size
    (HLE,
     '''void rom_merge_dirs(const std::string& guest, const fs::path& opened, int mode, std::vector<fs::directory_entry>& out) {
''',
     '''using RecipeList = std::vector<std::pair<std::string, uint64_t>>;  // AP_MOD_RECIPES: (name, size) of the recipes
void rom_merge_dirs(const std::string& guest, const fs::path& opened, int mode, std::vector<fs::directory_entry>& out,
                    RecipeList* recipes = nullptr) {
'''),
    (HLE,
     '''            bool dir = e.is_directory();
            if (!((dir && (mode & 1)) || (!dir && (mode & 2)))) continue;
            std::string k = key(e.path());
            bool found = false;
''',
     '''            bool dir = e.is_directory();
            if (!((dir && (mode & 1)) || (!dir && (mode & 2)))) continue;
            std::string k = key(e.path());
#ifdef NX_ROMFS_DIRECT
            if (recipes) {  // AP_MOD_RECIPES: <file>.nxrcp is <file>; a later layer's whole file replaces it and back
                if (!dir && is_recipe(e.path())) {
                    if (!tsuki_romfs::enabled()) continue;
                    const std::string name = utf8(e.path().filename()).substr(0, utf8(e.path().filename()).size() - 6);
                    const std::string nk = k.substr(0, k.size() - 6);
                    uint64_t sz = 0;
                    if (!tsuki_romfs::recipe_size(utf8(e.path()), &sz)) continue;
                    out.erase(std::remove_if(out.begin(), out.end(), [&](const fs::directory_entry& o) {
                                  return !o.is_directory() && key(o.path()) == nk;
                              }), out.end());
                    recipes->erase(std::remove_if(recipes->begin(), recipes->end(), [&](const auto& x) {
                                       std::string xk = x.first;
                                       for (char& ch : xk) ch = char(std::tolower(static_cast<unsigned char>(ch)));
                                       return xk == nk;
                                   }), recipes->end());
                    recipes->emplace_back(name, sz);
                    continue;
                }
                if (!dir)
                    recipes->erase(std::remove_if(recipes->begin(), recipes->end(), [&](const auto& x) {
                                       std::string xk = x.first;
                                       for (char& ch : xk) ch = char(std::tolower(static_cast<unsigned char>(ch)));
                                       return xk == k;
                                   }), recipes->end());
            }
#endif
            bool found = false;
'''),
    # OpenFile: a recipe opens like a file of the dump
    (HLE,
     '''#ifdef NX_ROMFS_DIRECT
    if (std::string rel; !(mode & 2) && rom_direct_rel(p, rel)) {  // AP_ROMFS_DIRECT: a RomFS file of the dump
''',
     '''#ifdef NX_ROMFS_DIRECT
    if (!(mode & 2) && is_recipe(p)) {  // AP_MOD_RECIPES: a layer's file rebuilt from its recipe
        tsuki_romfs::Handle* dh = tsuki_romfs::open_recipe(utf8(p));
        if (!dh) {
            note_open("OpenFile", guest, "recipe not usable");
            c.x[0] = kPathNotFound;
            return;
        }
        auto file = std::make_shared<File>(nullptr, guest);
        file->direct = dh;
        uint64_t h = g_next++;
        g_files[h] = file;
        st<uint64_t>(c.x[0], h);
        note_open("OpenFile", guest, "ok (recipe)");
        if (trace_fs()) log("fs: OpenFile(%s, %d) -> %llu (recipe)", guest.c_str(), mode, (unsigned long long)h);
        c.x[0] = kOk;
        return;
    }
    if (std::string rel; !(mode & 2) && rom_direct_rel(p, rel)) {  // AP_ROMFS_DIRECT: a RomFS file of the dump
'''),
    # live_music: a recipe is not opened raw
    (HLE,
     '''    FILE* f = (q.empty() || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
#ifdef NX_ROMFS_DIRECT
    tsuki_romfs::Handle* dh = nullptr;  // AP_ROMFS_DIRECT: the track may be in the dump
    if (std::string rel; !f && rom_direct_rel(q, rel)) dh = tsuki_romfs::open(rel);
''',
     '''#ifdef NX_ROMFS_DIRECT
    const bool rcp = !q.empty() && is_recipe(q);  // AP_MOD_RECIPES
    FILE* f = (q.empty() || rcp || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
    tsuki_romfs::Handle* dh = rcp ? tsuki_romfs::open_recipe(utf8(q)) : nullptr;  // AP_ROMFS_DIRECT: the track may be in the dump
    if (std::string rel; !f && !dh && rom_direct_rel(q, rel)) dh = tsuki_romfs::open(rel);
#else
    FILE* f = (q.empty() || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
#endif
#ifdef NX_ROMFS_DIRECT
'''),
    # OpenDirectory: recipes in the listing
    (HLE,
     '''#ifdef NX_ROMFS_MODS
    rom_merge_dirs(cstr(c.x[1]), p, mode, d.entries);  // AP_ROMFS_MODS
#endif
#ifdef NX_ROMFS_DIRECT
    if (direct) {  // AP_ROMFS_DIRECT: the dump's entries that no host folder (extract/romfs, mods) gave already
''',
     '''#ifdef NX_ROMFS_MODS
#ifdef NX_ROMFS_DIRECT
    RecipeList recipes;  // AP_MOD_RECIPES
    rom_merge_dirs(cstr(c.x[1]), p, mode, d.entries, &recipes);  // AP_ROMFS_MODS
    for (auto& [name, sz] : recipes) {
        d.names.push_back(name);
        d.is_dir.push_back(0);
        d.sizes.push_back(sz);
    }
#else
    rom_merge_dirs(cstr(c.x[1]), p, mode, d.entries);  // AP_ROMFS_MODS
#endif
#endif
#ifdef NX_ROMFS_DIRECT
    if (direct) {  // AP_ROMFS_DIRECT: the dump's entries that no host folder (extract/romfs, mods) gave already
'''),
    (HLE,
     '''        for (auto& e : d.entries) have.push_back(low(utf8(e.path().filename())));
''',
     '''        for (auto& e : d.entries) have.push_back(low(utf8(e.path().filename())));
        for (auto& nm : d.names) have.push_back(low(nm));  // AP_MOD_RECIPES: the recipes already listed
'''),
    # read_game_file: videos (or anything) kept as a recipe
    (HLE,
     '''    FILE* f = plat::fopen_utf8(utf8(src), "rb");
#ifdef NX_ROMFS_DIRECT
    if (!f) {  // AP_ROMFS_DIRECT: a RomFS file of the dump
''',
     '''#ifdef NX_ROMFS_DIRECT
    if (is_recipe(src)) {  // AP_MOD_RECIPES
        std::string s;
        return tsuki_romfs::read_all_recipe(utf8(src), s) ? s : std::string();
    }
#endif
    FILE* f = plat::fopen_utf8(utf8(src), "rb");
#ifdef NX_ROMFS_DIRECT
    if (!f) {  // AP_ROMFS_DIRECT: a RomFS file of the dump
'''),
]

# ------------------------------------------------------------------------------------------------ recomp_tsukihime.ps1
RECO_EDITS = [
    (RECO,
     '''        $removed = @($j.removed)
        if ($removed.Count) { Info "   $($removed.Count) base files are not in the update (they stay visible): $(($removed | Select-Object -First 3) -join ', ')" }
''',
     '''        $removed = @($j.removed)
        if ($removed.Count) { Info "   $($removed.Count) base files are not in the update (they stay visible): $(($removed | Select-Object -First 3) -join ', ')" }
        if ($j.PSObject.Properties['recipes'] -and @($j.recipes).Count) {   # AP_MOD_RECIPES
            Info ("   {0} of them kept as recipes over the dump ({1:N2} GiB of the base not copied; they need the dump)" -f @($j.recipes).Count, ([double]$j.recipe_bytes_not_copied / 1GB))
        }
'''),
    (RECO,
     '''    @('both', 'tools\\internal\\mods_nsp.py', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
''',
     '''    @('both', 'tools\\internal\\mods_nsp.py', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
    @('both', 'tools\\internal\\mods_nsp.py', 'AP_MOD_RECIPES', 'apply_mod_recipes.py', $false),
    @('both', 'framework\\runtime\\src\\hle_fs.cpp', 'AP_MOD_RECIPES', 'apply_mod_recipes.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_MOD_RECIPES', 'apply_mod_recipes.py', $false),
'''),
]

EDITS = PY_EDITS + RD_EDITS + HLE_EDITS + RECO_EDITS

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
