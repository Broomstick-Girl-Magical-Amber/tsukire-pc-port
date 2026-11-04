"""apply_romfs_direct.py - The game reads its RomFS straight from the .xci/.nsp in the root (AP_ROMFS_DIRECT).

Until now recomp_tsukihime.ps1 extracted the whole RomFS (~21-23 GB per edition) to data\\<edition>\\extract\\romfs and
the exe read it from there. With this patch, by default:
  - recomp step 2 (tools/internal/romfs_source.py, new) writes only <data>\\romfs_source.bin (the table of the RomFS, with
    the content key of its NCA: the exe never reads prod.keys), the exefs and <data>\\romfs_build (the RomFS files the
    build reads: romfs\\shader for step 8 and the *allui files of the HUD icons). Seconds instead of minutes, and
    ~21-23 GB less on disk per edition. The dump must stay in the ROOT of the port (any name: it is recognised by its
    content). Steps 1-2 run with python (no dotnet). -Extract: the full extraction with nsextract, as before.
  - -TrimXci (recomp and install_requirements -Recomp): cuts the 0xFF padding at the end of the .xci of the edition
    (checked byte by byte first; romfs_source.py untrim --input <xci> --apply undoes it).
  - prod.keys: if <root>\\data\\prod.keys does not exist and <root>\\prod.keys does, it is MOVED to data\\ (recomp and
    install_requirements; never copied or printed).
  - the exe (games/tsukihime_common/romfs_direct.cpp, new): with romfs_source.bin, rom: reads every file that is not in
    mods\\<edition> or in extract\\romfs straight from the dump, decrypting only what is read (AES-NI). Without the
    dump: a message box and exit code 7. Without romfs_source.bin: as before.
Files touched:
  - runtime/src/hle_fs.cpp (NX_ROMFS_DIRECT): GetEntryType, OpenFile, GetFileSize, reads, OpenDirectory/ReadDirectory/
    GetDirectoryEntryCount, MountRom, read_game_file (videos) and the title music (AP_TITLE_MUSIC, AP_TITLE_LIVE_MUSIC)
    also look in the dump; the mod layers (AP_ROMFS_MODS) keep winning. Without the define: the usual code.
  - games/tsukihime_common/tsukihime_common.cmake: NX_ROMFS_DIRECT.
  - ../tools/internal/mods_nsp.py: mod --base-dump <dump> (the base RomFS of a BKTR patch read from the dump; no index
    and no extract\\romfs needed). --index + --base-romfs keep working.
  - ../tools/hud/build_hud_assets.py: without extract\\romfs, the base layer is romfs_build.
  - ../tools/recomp_tsukihime.ps1: -Extract, -TrimXci, the keys rule, step 2 without extraction, updates against the
    dump, steps 8 and 10 with romfs_build, markers.
  - ../tools/install_requirements.ps1: -Extract and -TrimXci passed to recomp (-Recomp), the keys rule, space per
    edition without the RomFS.
  - ../.gitignore: games/tsukihime_common/romfs_direct.cpp versioned.
New files (copy them before applying): framework/games/tsukihime_common/romfs_direct.cpp,
tools/internal/romfs_source.py, tools/tests/test_romfs_direct.py.

Idempotent (marker AP_ROMFS_DIRECT), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found;
--remove leaves the files byte for byte as they were. Apply: cd <root>\\framework;
private\\venv\\Scripts\\python ..\\tools\\patches\\apply_romfs_direct.py. Then: recomp_tsukihime.ps1 -Edition <ed> -From 0
(step 2 makes romfs_source.bin; an existing extract\\romfs is not deleted: delete it by hand to free the space).
"""
from _patch import run

MARK = "AP_ROMFS_DIRECT"
FS = "runtime/src/hle_fs.cpp"
CMAKE = "games/tsukihime_common/tsukihime_common.cmake"
MODS = "../tools/internal/mods_nsp.py"
HUD = "../tools/hud/build_hud_assets.py"
RECO = "../tools/recomp_tsukihime.ps1"
INST = "../tools/install_requirements.ps1"
GITIGNORE = "../.gitignore"

EDITS = [
    # ------------------------------------------------------------------------------------------------ hle_fs.cpp
    (FS,
     '''std::string music_swap(const std::string& path, int64_t off);
}  // namespace tsuki_title
#endif
''',
     '''std::string music_swap(const std::string& path, int64_t off);
}  // namespace tsuki_title
#endif
#ifdef NX_ROMFS_DIRECT
// AP_ROMFS_DIRECT (apply_romfs_direct.py): the RomFS read straight from the .xci/.nsp of the root of the port
// (games/tsukihime_common/romfs_direct.cpp, <data root>/romfs_source.bin). rel = path inside the RomFS, '/' separated, any
// case. Not enabled (no romfs_source.bin, or no dump but a full extract/romfs): every call says "not there".
namespace tsuki_romfs {
struct Handle;
bool enabled();
bool file(const std::string& rel, uint64_t* size);
bool dir(const std::string& rel);
void list(const std::string& rel, std::vector<std::string>& names, std::vector<uint8_t>& is_dir, std::vector<uint64_t>& sizes);
Handle* open(const std::string& rel);
uint64_t size(const Handle* h);
uint64_t read(Handle* h, int64_t off, void* buf, uint64_t n);
void close(Handle* h);
bool read_all(const std::string& rel, std::string& out);
}  // namespace tsuki_romfs
#endif
'''),
    (FS,
     '''struct File {
    FILE* f;
    std::string path;
    File(FILE* file, std::string p) : f(file), path(std::move(p)) {}
    File(const File&) = delete;  // owns the FILE: one instance only
    File& operator=(const File&) = delete;
    ~File() { std::fclose(f); }
};
''',
     '''struct File {
    FILE* f;
    std::string path;
#ifdef NX_ROMFS_DIRECT
    tsuki_romfs::Handle* direct = nullptr;  // AP_ROMFS_DIRECT: a RomFS file read from the dump (then f is null)
#endif
    File(FILE* file, std::string p) : f(file), path(std::move(p)) {}
    File(const File&) = delete;  // owns the FILE: one instance only
    File& operator=(const File&) = delete;
#ifdef NX_ROMFS_DIRECT
    ~File() {
        if (f) std::fclose(f);
        tsuki_romfs::close(direct);
    }
#else
    ~File() { std::fclose(f); }
#endif
};
'''),
    (FS,
     '''struct Dir {
    std::vector<fs::directory_entry> entries;
    size_t pos = 0;
};
''',
     '''struct Dir {
    std::vector<fs::directory_entry> entries;
    size_t pos = 0;
#ifdef NX_ROMFS_DIRECT
    // AP_ROMFS_DIRECT: entries of the RomFS of the dump, after `entries` (a name already in `entries` is left out)
    std::vector<std::string> names;
    std::vector<uint8_t> is_dir;
    std::vector<uint64_t> sizes;
#endif
};
'''),
    (FS,
     '''std::string cstr(uint64_t p) { return p ? std::string(reinterpret_cast<const char*>(p)) : std::string(); }
''',
     '''std::string cstr(uint64_t p) { return p ? std::string(reinterpret_cast<const char*>(p)) : std::string(); }

#ifdef NX_ROMFS_DIRECT
// AP_ROMFS_DIRECT: the path inside the RomFS of a resolved host path that is under extract/romfs (the rom: mount) and
// is not a file on the host (extract/romfs, if it is still there, wins file by file; its folders are joined with the
// dump's). False otherwise.
bool rom_direct_rel(const fs::path& p, std::string& rel) {
    if (p.empty() || !tsuki_romfs::enabled()) return false;
    const fs::path r = p.lexically_normal().lexically_relative((data_root() / "extract" / "romfs").lexically_normal());
    std::string s = utf8(r);
    if (s.empty() || s == ".." || s.rfind("../", 0) == 0 || s.rfind("..\\\\", 0) == 0) return false;
    if (s == ".") s.clear();
    std::error_code ec;
    if (fs::is_regular_file(p, ec)) return false;
    rel = s;
    return true;
}
#endif
'''),
    (FS,
     '''    if (fs::exists(base_path, ec)) return {};
''',
     '''#ifdef NX_ROMFS_DIRECT
    std::string drel;  // AP_ROMFS_DIRECT: a path the base has in the dump is not a layer's folder
    if (fs::exists(base_path, ec) ||
        (rom_direct_rel(base_path, drel) && (tsuki_romfs::file(drel, nullptr) || tsuki_romfs::dir(drel))))
        return {};
#else
    if (fs::exists(base_path, ec)) return {};
#endif
'''),
    (FS,
     '''void MountRom(CPU& c) {
    std::lock_guard lk(g_fs);
    mount(cstr(c.x[0]), data_root() / "extract" / "romfs");
''',
     '''void MountRom(CPU& c) {
    std::lock_guard lk(g_fs);
#ifdef NX_ROMFS_DIRECT
    if (tsuki_romfs::enabled()) {  // AP_ROMFS_DIRECT: the RomFS is the dump (loaded now: a missing dump stops here)
        g_mounts[cstr(c.x[0])] = data_root() / "extract" / "romfs";  // the same key as always (mods layers); not created
        log("fs: mounted %s: -> the RomFS of the dump (extract/romfs and mods first, if present)", cstr(c.x[0]).c_str());
        c.x[0] = kOk;
        return;
    }
#endif
    mount(cstr(c.x[0]), data_root() / "extract" / "romfs");
'''),
    (FS,
     '''        st<uint32_t>(c.x[0], 1);
        c.x[0] = kOk;
        return;
    }
    std::error_code ec;
    if (p.empty() || !fs::exists(p, ec)) {
''',
     '''        st<uint32_t>(c.x[0], 1);
        c.x[0] = kOk;
        return;
    }
#ifdef NX_ROMFS_DIRECT
    if (std::string rel; rom_direct_rel(p, rel)) {  // AP_ROMFS_DIRECT
        if (tsuki_romfs::file(rel, nullptr) || tsuki_romfs::dir(rel)) {
            st<uint32_t>(c.x[0], tsuki_romfs::file(rel, nullptr) ? 1 : 0);
            c.x[0] = kOk;
            return;
        }
    }
#endif
    std::error_code ec;
    if (p.empty() || !fs::exists(p, ec)) {
'''),
    (FS,
     '''            if (!q.empty() && fs::is_regular_file(q, ec0)) guest = alt;
''',
     '''#ifdef NX_ROMFS_DIRECT
            std::string rel0;  // AP_ROMFS_DIRECT: the track may be in the dump
            if (!q.empty() && (fs::is_regular_file(q, ec0) || (rom_direct_rel(q, rel0) && tsuki_romfs::file(rel0, nullptr)))) guest = alt;
#else
            if (!q.empty() && fs::is_regular_file(q, ec0)) guest = alt;
#endif
'''),
    (FS,
     '''    std::error_code ec;
    if (p.empty() || !fs::is_regular_file(p, ec)) {
        note_open("OpenFile", guest, p.empty() ? "unknown mount" : "not found");
''',
     '''#ifdef NX_ROMFS_DIRECT
    if (std::string rel; !(mode & 2) && rom_direct_rel(p, rel)) {  // AP_ROMFS_DIRECT: a RomFS file of the dump
        if (tsuki_romfs::Handle* dh = tsuki_romfs::open(rel)) {
            auto file = std::make_shared<File>(nullptr, guest);
            file->direct = dh;
            uint64_t h = g_next++;
            g_files[h] = file;
            st<uint64_t>(c.x[0], h);
            note_open("OpenFile", guest, "ok (dump)");
            if (trace_fs()) log("fs: OpenFile(%s, %d) -> %llu (dump)", guest.c_str(), mode, (unsigned long long)h);
            c.x[0] = kOk;
            return;
        }
    }
#endif
    std::error_code ec;
    if (p.empty() || !fs::is_regular_file(p, ec)) {
        note_open("OpenFile", guest, p.empty() ? "unknown mount" : "not found");
'''),
    (FS,
     '''    std::lock_guard lk(g_fs);  // the FILE position is shared with writers
    FILE* f = file->f;
''',
     '''#ifdef NX_ROMFS_DIRECT
    if (file->direct) {  // AP_ROMFS_DIRECT
        st<int64_t>(c.x[0], int64_t(tsuki_romfs::size(file->direct)));
        c.x[0] = kOk;
        return;
    }
#endif
    std::lock_guard lk(g_fs);  // the FILE position is shared with writers
    FILE* f = file->f;
'''),
    (FS,
     '''    uint64_t n = plat::read_at(file.f, off, reinterpret_cast<void*>(buf), size);
''',
     '''#ifdef NX_ROMFS_DIRECT
    uint64_t n = file.direct ? tsuki_romfs::read(file.direct, off, reinterpret_cast<void*>(buf), size)  // AP_ROMFS_DIRECT
                             : plat::read_at(file.f, off, reinterpret_cast<void*>(buf), size);
#else
    uint64_t n = plat::read_at(file.f, off, reinterpret_cast<void*>(buf), size);
#endif
'''),
    (FS,
     '''    FILE* f = (q.empty() || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
    if (!f) {
        log("title: %s could not be opened: the music stays", alt.c_str());
        return file;
    }
    auto nf = std::make_shared<File>(f, alt);
''',
     '''    FILE* f = (q.empty() || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
#ifdef NX_ROMFS_DIRECT
    tsuki_romfs::Handle* dh = nullptr;  // AP_ROMFS_DIRECT: the track may be in the dump
    if (std::string rel; !f && rom_direct_rel(q, rel)) dh = tsuki_romfs::open(rel);
    if (!f && !dh) {
#else
    if (!f) {
#endif
        log("title: %s could not be opened: the music stays", alt.c_str());
        return file;
    }
    auto nf = std::make_shared<File>(f, alt);
#ifdef NX_ROMFS_DIRECT
    nf->direct = dh;
#endif
'''),
    (FS,
     '''    std::lock_guard lk(g_fs);
    plat::fseek64(file->f, int64_t(c.x[1]), SEEK_SET);
''',
     '''#ifdef NX_ROMFS_DIRECT
    if (!file->f) {  // AP_ROMFS_DIRECT: a file of the dump is read only
        c.x[0] = kTargetNotFound;
        return;
    }
#endif
    std::lock_guard lk(g_fs);
    plat::fseek64(file->f, int64_t(c.x[1]), SEEK_SET);
'''),
    (FS,
     '''    if (auto file = open_file(c.x[0])) {
        std::lock_guard lk(g_fs);
        fflush(file->f);
''',
     '''    if (auto file = open_file(c.x[0])) {
        std::lock_guard lk(g_fs);
#ifdef NX_ROMFS_DIRECT
        if (file->f) fflush(file->f);  // AP_ROMFS_DIRECT: fflush(nullptr) would flush every stream
#else
        fflush(file->f);
#endif
'''),
    (FS,
     '''    std::lock_guard lk(g_fs);
    std::fflush(file->f);
''',
     '''#ifdef NX_ROMFS_DIRECT
    if (!file->f) {  // AP_ROMFS_DIRECT: a file of the dump is read only
        c.x[0] = kPathNotFound;
        return;
    }
#endif
    std::lock_guard lk(g_fs);
    std::fflush(file->f);
'''),
    (FS,
     '''    bool found = !p.empty() && fs::is_directory(p, ec);
''',
     '''    bool found = !p.empty() && fs::is_directory(p, ec);
#ifdef NX_ROMFS_DIRECT
    std::string drel;  // AP_ROMFS_DIRECT: a folder of the RomFS of the dump
    const bool direct = rom_direct_rel(p, drel) && tsuki_romfs::dir(drel);
    found = found || direct;
#endif
'''),
    (FS,
     '''    rom_merge_dirs(cstr(c.x[1]), p, mode, d.entries);  // AP_ROMFS_MODS
#endif
''',
     '''    rom_merge_dirs(cstr(c.x[1]), p, mode, d.entries);  // AP_ROMFS_MODS
#endif
#ifdef NX_ROMFS_DIRECT
    if (direct) {  // AP_ROMFS_DIRECT: the dump's entries that no host folder (extract/romfs, mods) gave already
        std::vector<std::string> names;
        std::vector<uint8_t> dirs;
        std::vector<uint64_t> sizes;
        tsuki_romfs::list(drel, names, dirs, sizes);
        auto low = [](std::string s) {
            for (char& ch : s) ch = char(std::tolower(static_cast<unsigned char>(ch)));
            return s;
        };
        std::vector<std::string> have;
        for (auto& e : d.entries) have.push_back(low(utf8(e.path().filename())));
        for (size_t i = 0; i < names.size(); i++) {
            if (!((dirs[i] && (mode & 1)) || (!dirs[i] && (mode & 2)))) continue;
            if (std::find(have.begin(), have.end(), low(names[i])) != have.end()) continue;
            d.names.push_back(names[i]);
            d.is_dir.push_back(dirs[i]);
            d.sizes.push_back(sizes[i]);
        }
    }
#endif
'''),
    (FS,
     '''    }
    st<int64_t>(c.x[0], n);
    c.x[0] = kOk;
}
''',
     '''    }
#ifdef NX_ROMFS_DIRECT
    for (; n < max && d.pos < d.entries.size() + d.names.size(); n++, d.pos++) {  // AP_ROMFS_DIRECT: then the dump's
        const size_t k = d.pos - d.entries.size();
        uint64_t out = c.x[1] + uint64_t(n) * 0x310;
        std::memset(reinterpret_cast<void*>(out), 0, 0x310);
        std::memcpy(reinterpret_cast<void*>(out), d.names[k].c_str(), std::min<size_t>(d.names[k].size(), 0x300));
        st<int8_t>(out + 0x304, d.is_dir[k] ? 0 : 1);
        st<int64_t>(out + 0x308, d.is_dir[k] ? 0 : int64_t(d.sizes[k]));
    }
#endif
    st<int64_t>(c.x[0], n);
    c.x[0] = kOk;
}
'''),
    (FS,
     '''    st<int64_t>(c.x[0], it == g_dirs.end() ? 0 : int64_t(it->second.entries.size()));
''',
     '''#ifdef NX_ROMFS_DIRECT
    st<int64_t>(c.x[0], it == g_dirs.end() ? 0 : int64_t(it->second.entries.size() + it->second.names.size()));  // AP_ROMFS_DIRECT
#else
    st<int64_t>(c.x[0], it == g_dirs.end() ? 0 : int64_t(it->second.entries.size()));
#endif
'''),
    (FS,
     '''    FILE* f = plat::fopen_utf8(utf8(src), "rb");
    if (!f) return {};
''',
     '''    FILE* f = plat::fopen_utf8(utf8(src), "rb");
#ifdef NX_ROMFS_DIRECT
    if (!f) {  // AP_ROMFS_DIRECT: a RomFS file of the dump
        std::string rel, s;
        if (rom_direct_rel(src, rel) && tsuki_romfs::read_all(rel, s)) return s;
        return {};
    }
#endif
    if (!f) return {};
'''),

    # ------------------------------------------------------------------------------------------------ tsukihime_common.cmake
    (CMAKE,
     '''
# FFmpeg for the movie:: HLE.
''',
     '''# AP_ROMFS_DIRECT (apply_romfs_direct.py): rom: reads the RomFS straight from the .xci/.nsp of the root of the port
# (romfs_direct.cpp, with the <data root>/romfs_source.bin that recomp_tsukihime.ps1 writes); without it, extract/romfs
target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_DIRECT)

# FFmpeg for the movie:: HLE.
'''),

    # ------------------------------------------------------------------------------------------------ .gitignore
    (GITIGNORE,
     '''!/framework/games/tsukihime_common/hud_glyphs.h
''',
     '''!/framework/games/tsukihime_common/hud_glyphs.h
# AP_ROMFS_DIRECT: the RomFS read straight from the dump (apply_romfs_direct.py)
!/framework/games/tsukihime_common/romfs_direct.cpp
'''),

    # ------------------------------------------------------------------------------------------------ mods_nsp.py
    (MODS,
     '''dump (step "index"); after that the dump can be deleted and any number of patches can be processed.
''',
     '''dump (step "index"); after that the dump can be deleted and any number of patches can be processed.
AP_ROMFS_DIRECT (apply_romfs_direct.py): when the port reads the RomFS from the dump (the default), the dump stays in the
root and "mod --base-dump <dump>" reads the base romfs from it instead (no index, no extract\\\\romfs).
'''),
    (MODS,
     '''    idx = load_index(a.index, pid)
''',
     '''    idx, dump_base = base_source(a, keys, pid)  # AP_ROMFS_DIRECT
'''),
    (MODS,
     '''    base = BaseRomfs(idx, a.base_romfs)
''',
     '''    base = dump_base or BaseRomfs(idx, a.base_romfs)  # AP_ROMFS_DIRECT
'''),
    (MODS,
     '''                with open(to_host(a.base_romfs, b[2]), "rb") as f:
''',
     '''                with base_open(a, dump_base, b) as f:  # AP_ROMFS_DIRECT
'''),
    (MODS,
     '''            bf = open(to_host(a.base_romfs, b[2]), "rb") if equal else None
''',
     '''            bf = base_open(a, dump_base, b) if equal else None  # AP_ROMFS_DIRECT
'''),
    (MODS,
     '''

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
''',
     '''

# ---------------------------------------------------------------------------------------------------------- AP_ROMFS_DIRECT
class DumpBase:
    """The base romfs read straight from the dump: what BaseRomfs rebuilds from extract\\\\romfs + the index, without them."""

    def __init__(self, path, keys, pid):
        name, nca = find_program(container_files(path), keys, pid, False)
        self.sec = nca.section(1)
        self.l6_off, self.l6_size = self.sec.ivfc_data()
        files, _ = romfs_files(lambda o, n: self.sec.read(self.l6_off + o, n))
        self.index = {"l6_offset": self.l6_off, "l6_size": self.l6_size, "files": [[o, s, r] for o, s, r in files]}
        print(f"   base romfs read from {os.path.basename(path)} ({name}): {len(files)} files")

    def read_section(self, off, n):
        return self.sec.read(off, n)


class _DumpFile:
    """A base file read from the dump, with the part of the file API that cmd_mod uses."""

    def __init__(self, base, off, size):
        self.base, self.off, self.size, self.pos = base, off, size, 0

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        b = self.base.sec.read(self.base.l6_off + self.off + self.pos, n) if n else b""
        self.pos += n
        return b

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def base_source(a, keys, pid):
    """(index-like dict, DumpBase or None): --base-dump, or --index + --base-romfs as before."""
    if a.base_dump:
        d = DumpBase(a.base_dump, keys, pid)
        return d.index, d
    if not a.index or not a.base_romfs:
        fail("mod needs --base-dump <dump>, or --index and --base-romfs")
    return load_index(a.index, pid), None


def base_open(a, dump, b):
    """The base file b = (offset in the romfs, size, path) of the index, opened for reading."""
    return _DumpFile(dump, b[0], b[1]) if dump is not None else open(to_host(a.base_romfs, b[2]), "rb")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
'''),
    (MODS,
     '''    m.add_argument("--index", required=True)
    m.add_argument("--base-romfs", required=True)
''',
     '''    m.add_argument("--index")
    m.add_argument("--base-romfs")
    m.add_argument("--base-dump")  # AP_ROMFS_DIRECT: the base romfs straight from the dump (no index, no extract\\\\romfs)
'''),

    # ------------------------------------------------------------------------------------------------ build_hud_assets.py
    (HUD,
     '''    out = [os.path.join(root, DATA_DIRS[edition], "extract", "romfs")]
''',
     '''    out = [os.path.join(root, DATA_DIRS[edition], "extract", "romfs")]
    if not os.path.isdir(out[0]):  # AP_ROMFS_DIRECT: no full extraction: the RomFS files of the build (romfs_source.py)
        out = [os.path.join(root, DATA_DIRS[edition], "romfs_build")]
'''),

    # ------------------------------------------------------------------------------------------------ recomp_tsukihime.ps1
    (RECO,
     '''# -ProgressSeconds: how often the progress of the extraction (step 2) is reported (30 by default).
''',
     '''# -ProgressSeconds: how often the progress of the extraction (step 2) is reported (30 by default).
# AP_ROMFS_DIRECT (apply_romfs_direct.py): by default the RomFS is NOT extracted (~21-23 GB per edition). Step 2 writes
# <data>\\romfs_source.bin (the table of the RomFS, with the content key of its NCA: the exe never reads prod.keys), the
# exefs and <data>\\romfs_build (the RomFS files the build reads: romfs\\shader for step 8, the *allui files of the HUD
# icons); the exe reads every other file straight from the .xci/.nsp, which must STAY in the ROOT of the port (any
# name: it is recognised by its content). Steps 1-2 use tools\\internal\\romfs_source.py (python; no dotnet).
# -Extract: the full extraction with nsextract (extract\\romfs), as before. An extract\\romfs from before is not
# deleted: while it is there the exe reads it first; delete it by hand to free the space.
# -TrimXci: cuts the 0xFF padding at the end of the .xci of this edition, in place (everything after the data is checked
# to be 0xFF first; ~8.5 GB in the japanese cartridge; tools\\internal\\romfs_source.py untrim --input <xci> --apply
# undoes it). Keys: if <root>\\data\\prod.keys does not exist and <root>\\prod.keys does, it is MOVED to data\\.
'''),
    (RECO,
     '''    [switch]$NoUpdates,
''',
     '''    [switch]$NoUpdates,
    [switch]$Extract,   # AP_ROMFS_DIRECT: full extraction of the RomFS (nsextract) instead of reading it from the dump
    [switch]$TrimXci,   # AP_ROMFS_DIRECT: trim the 0xFF padding of this edition's .xci (in place, checked first)
'''),
    (RECO,
     '''if (-not $Keys) { $Keys = Join-Path $Root 'data\\prod.keys' }
''',
     '''$KeysGiven = [bool]$Keys   # AP_ROMFS_DIRECT: without -Keys, a prod.keys left in the root is moved to data\\
if (-not $Keys) { $Keys = Join-Path $Root 'data\\prod.keys' }
'''),
    (RECO,
     '''    @('both', 'tools\\install_requirements.ps1', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),
''',
     '''    @('both', 'tools\\install_requirements.ps1', 'AP_FFMPEG_PREBUILT', 'apply_ffmpeg_prebuilt.py', $false),
    @('both', 'framework\\runtime\\src\\hle_fs.cpp', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ROMFS_DIRECT', 'romfs_direct.cpp (copy it to games\\tsukihime_common)', $false),
    @('both', 'tools\\internal\\romfs_source.py', 'AP_ROMFS_DIRECT', 'romfs_source.py (copy it to tools\\internal)', $false),
    @('both', 'tools\\internal\\mods_nsp.py', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
    @('both', 'tools\\hud\\build_hud_assets.py', 'AP_ROMFS_DIRECT', 'apply_romfs_direct.py', $false),
'''),
    (RECO,
     '''$ModsPy = Under $Tools 'internal\\mods_nsp.py'
''',
     '''$ModsPy = Under $Tools 'internal\\mods_nsp.py'
$SourcePy = Under $Tools 'internal\\romfs_source.py'   # AP_ROMFS_DIRECT
$RomfsSource = Under $Data 'romfs_source.bin'          # the table the exe reads the dump with
$BuildRomfs = Under $Data 'romfs_build'                # the RomFS files the build reads (shaders, HUD)
'''),
    (RECO,
     '''# The keys do not leave data\\: only their path is passed to nsextract and mods_nsp.py.
function CheckKeys {
    $k = [IO.Path]::GetFullPath($Keys)
''',
     '''# AP_ROMFS_DIRECT: keys left in the root (next to the dump) go to data\\ (moved, never copied); if data\\ has some, those win.
function MoveRootKeys {
    if ($KeysGiven) { return }
    $inData = InRoot 'data\\prod.keys'; $inRoot = InRoot 'prod.keys'
    if (Test-Path -LiteralPath $inData -PathType Leaf) {
        if (Test-Path -LiteralPath $inRoot -PathType Leaf) { Warn 'there is a prod.keys in the root too: data\\prod.keys is used (the one in the root is left as is)' }
        return
    }
    if (-not (Test-Path -LiteralPath $inRoot -PathType Leaf)) { return }
    New-Item -ItemType Directory -Path (InRoot 'data') -Force | Out-Null
    Move-Item -LiteralPath $inRoot -Destination $inData
    Info 'prod.keys moved from the root to data\\ (not read)'
}
function CheckPyCrypto {   # AP_ROMFS_DIRECT: romfs_source.py (steps 1-2) needs pycryptodome
    $r = RunCapture $Python @('-c', 'import Crypto, lz4.block')
    if ($r.Code -ne 0) { Fail 'the project python does not have pycryptodome (tools\\install_requirements.ps1 -From 6 installs tools\\requirements.txt)' }
}

# The keys do not leave data\\: only their path is passed to nsextract and mods_nsp.py.
function CheckKeys {
    MoveRootKeys   # AP_ROMFS_DIRECT
    $k = [IO.Path]::GetFullPath($Keys)
'''),
    (RECO,
     '''    if ($From -le 2 -and $To -ge 1 -and $NsExtract -notlike '*.exe' -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10: builds and runs tools\\nsextract)' }   # AP_DROP_UNUSED, AP_TOOLCHAIN_XWIN
''',
     '''    if ($Extract -and $From -le 2 -and $To -ge 1 -and $NsExtract -notlike '*.exe' -and -not (Get-Command dotnet -ErrorAction SilentlyContinue)) { $missing += 'dotnet (.NET SDK 10: builds and runs tools\\nsextract)' }   # AP_DROP_UNUSED, AP_TOOLCHAIN_XWIN; AP_ROMFS_DIRECT: only -Extract
'''),
    (RECO,
     '''    if ($From -le 2 -and $To -ge 1 -and (ExtractedOk) -and -not $Replace) {
        Info 'exefs and romfs already extracted: the .xci/.nsp and the keys are not needed (-Replace to extract again)'
    } elseif ($From -le 2 -and $To -ge 1) {
        $script:Candidates = @(FindInputs)
        EnsureNsExtract
        CheckKeys
''',
     '''    if ($From -le 2 -and $To -ge 1 -and (ExtractedOk) -and -not $Replace -and -not $TrimXci) {
        if ($Extract) { Info 'exefs and romfs already extracted: the .xci/.nsp and the keys are not needed (-Replace to extract again)' }
        else { Info 'exefs, romfs_source.bin and romfs_build already made: the keys are not needed (the .xci/.nsp is: the game reads it; -Replace to make them again)' }   # AP_ROMFS_DIRECT
    } elseif ($From -le 2 -and $To -ge 1) {
        $script:Candidates = @(FindInputs)
        if ($Extract) { EnsureNsExtract } else { CheckPyCrypto }   # AP_ROMFS_DIRECT: steps 1-2 with romfs_source.py
        CheckKeys
'''),
    (RECO,
     '''function Step1 {
    if ((ExtractedOk) -and -not $Replace) {
''',
     '''# AP_ROMFS_DIRECT: -TrimXci cuts the 0xFF padding of the .xci of this edition (romfs_source.py checks every byte first;
# another edition's .xci is left alone, exit code 3).
function TrimInputs {
    $n = 0
    foreach ($c in @($script:Candidates)) {
        if ((ContainerType $c) -ne 'xci') { continue }
        Info "-TrimXci: $(Split-Path -Leaf $c) (everything after the data must be 0xFF; reading it takes about a minute)"
        & $Python $SourcePy trim --keys $Keys --input $c --program-id (Hex16 $V.ProgramId) --apply
        $code = $LASTEXITCODE
        if ($code -eq 3) { Info "$(Split-Path -Leaf $c): not the $Edition edition (not trimmed)"; continue }
        if ($code -ne 0) { Fail "romfs_source.py trim failed with $(Split-Path -Leaf $c) (code $code)" }
        $n++
    }
    if (-not $n) { Info "-TrimXci: there is no .xci of the $Edition edition in $Root (an .nsp has no padding)" }
}

function Step1 {
    if ($TrimXci) { TrimInputs }   # AP_ROMFS_DIRECT
    if ((ExtractedOk) -and -not $Replace) {
'''),
    (RECO,
     '''    $nc = ToolCmd $NsExtract   # AP_TOOLCHAIN_XWIN
    $r = RunCapture $nc[0] (@($nc | Select-Object -Skip 1) + @('info', '--keys', $Keys, '--input', $InputFile))
    if ($r.Code -ne 0) {
        $err = @($r.Output | Where-Object { $_ -match 'ERROR' } | Select-Object -First 3)
        Fail "nsextract failed to read $(Split-Path -Leaf $InputFile) (code $($r.Code)): $($err -join ' | ')"
    }
''',
     '''    if ($Extract) {
        $nc = ToolCmd $NsExtract   # AP_TOOLCHAIN_XWIN
        $r = RunCapture $nc[0] (@($nc | Select-Object -Skip 1) + @('info', '--keys', $Keys, '--input', $InputFile))
    } else {
        $r = RunCapture $Python @($SourcePy, 'info', '--keys', $Keys, '--input', $InputFile)   # AP_ROMFS_DIRECT
    }
    $tool = if ($Extract) { 'nsextract' } else { 'romfs_source.py' }
    if ($r.Code -ne 0) {
        $err = @($r.Output | Where-Object { $_ -match 'ERROR|SKIP' } | Select-Object -First 3)
        Fail "$tool failed to read $(Split-Path -Leaf $InputFile) (code $($r.Code)): $($err -join ' | ')"
    }
'''),
    (RECO,
     '''    Info "program ID $(Hex16 $progId); exefs and romfs will be read straight from the input (nsextract)"
''',
     '''    if ($Extract) { Info "program ID $(Hex16 $progId); exefs and romfs will be read straight from the input (nsextract)" }
    else { Info "program ID $(Hex16 $progId); the RomFS stays in the input (the game reads it there): only the table, the exefs and the build files are written" }   # AP_ROMFS_DIRECT
'''),
    (RECO,
     '''function ExtractedOk {
    $main = Under $Data 'exefs\\main'; $npdm = Under $Data 'exefs\\main.npdm'
    $romfs = Under $Data 'extract\\romfs'
''',
     '''# AP_ROMFS_DIRECT: the RomFS files the build reads: romfs_build, or a full extract\\romfs (-Extract, or from before).
function BuildRomfsDir { if (Test-Path -LiteralPath (Under $BuildRomfs 'shader')) { $BuildRomfs } else { Under $Data 'extract\\romfs' } }

function ExtractedOk {
    $main = Under $Data 'exefs\\main'; $npdm = Under $Data 'exefs\\main.npdm'
    $romfs = Under $Data 'extract\\romfs'
    if (-not $Extract) {   # AP_ROMFS_DIRECT: exefs + the table + the build files
        return ((Test-Path -LiteralPath $main) -and (Test-Path -LiteralPath $npdm) -and (Test-Path -LiteralPath $RomfsSource) -and
                (Test-Path -LiteralPath (Under $BuildRomfs 'shader')))
    }
'''),
    (RECO,
     '''function Step2 {
    $exefs = Under $Data 'exefs'; $romfs = Under $Data 'extract\\romfs'
''',
     '''function Step2 {
    if (-not $Extract) { Step2Direct; return }   # AP_ROMFS_DIRECT
    $exefs = Under $Data 'exefs'; $romfs = Under $Data 'extract\\romfs'
'''),
    (RECO,
     '''function Step3 {
''',
     '''# AP_ROMFS_DIRECT: step 2 without extracting the RomFS (tools\\internal\\romfs_source.py prepare, in _recomp\\, moved at the
# end): romfs_source.bin, romfs_build and the exefs (if it is not there yet).
function Step2Direct {
    $exefs = Under $Data 'exefs'
    if ((ExtractedOk) -and -not $Replace) {
        if ((ProgramIdOf (Under $exefs 'main.npdm')) -ne $V.ProgramId) { Fail "$exefs is from another program ID: check it (or -Replace)" }
        Info "already made: $exefs, $RomfsSource and $BuildRomfs; -Replace to make them again"
        RemoveStage
        DirectDumpCheck
        return
    }
    if (-not $script:Ident) { Fail 'step 1 (identify) is missing' }
    $exefsOk = (-not $Replace) -and (Test-Path -LiteralPath (Under $exefs 'main')) -and (Test-Path -LiteralPath (Under $exefs 'main.npdm')) -and
               (Test-Path -LiteralPath (Under $exefs 'sdk'))
    if ($exefsOk -and (ProgramIdOf (Under $exefs 'main.npdm')) -ne $V.ProgramId) { Fail "$exefs is from another program ID: check it (or -Replace)" }
    $replaced = @($BuildRomfs); if (-not $exefsOk) { $replaced += $exefs }
    foreach ($p in $replaced) {   # what SetAside would reject at the end
        if ((Test-Path -LiteralPath $p) -and (Test-Path -LiteralPath "$p.old")) { Fail "$p.old already exists (from a previous run): delete or rename it by hand" }
    }
    $free = FreeOn $Data
    if ($free -lt 2GB) { Fail "~2 GB free are needed on the drive of $Data and there are $(GB $free)" }
    NewStage
    $a = @($SourcePy, 'prepare', '--keys', $Keys, '--input', $InputFile, '--program-id', (Hex16 $V.ProgramId),
           '--source', (Under $Stage 'romfs_source.bin'), '--build-romfs', (Under $Stage 'romfs_build'))
    if (-not $exefsOk) { $a += @('--exefs', (Under $Stage 'exefs')) }
    Info 'reading the RomFS table and the build files from the input (romfs_source.py; the RomFS is not extracted)'
    $t0 = Get-Date
    $r = RunCapture $Python $a
    foreach ($l in $r.Output) { if ($l -notmatch '^PREPARE ') { Info $l } }
    if ($r.Code -ne 0) { Fail "romfs_source.py prepare failed (code $($r.Code))" }
    $kv = @{}
    foreach ($l in @($r.Output | Where-Object { $_ -match '^PREPARE ' })) {
        foreach ($p in (($l -replace '^PREPARE ', '') -split ' ')) { $q = $p -split '=', 2; if ($q.Count -eq 2) { $kv[$q[0]] = $q[1] } }
    }
    if (-not $kv.ContainsKey('files')) { Fail 'romfs_source.py prepare finished without its PREPARE line' }
    if (-not $exefsOk) {
        foreach ($f in 'main', 'main.npdm', 'sdk') { if (-not (Test-Path -LiteralPath (Under $Stage "exefs\\$f"))) { Fail "exefs without $f" } }
        if ((ProgramIdOf (Under $Stage 'exefs\\main.npdm')) -ne $V.ProgramId) { Fail 'the extracted main.npdm is from another program ID' }
    }
    if (-not (Test-Path -LiteralPath (Under $Stage 'romfs_build\\shader'))) { Fail 'the build files have no shader folder' }
    # Move into place (same disk: rename). What was there is kept as *.old; romfs_source.bin is regenerable.
    $previous = @()
    try {
        if (-not $exefsOk) {
            $x = SetAside $exefs; if ($x) { $previous += , @($exefs, $x) }
            Move-Item -LiteralPath (Under $Stage 'exefs') -Destination $exefs
        }
        $x = SetAside $BuildRomfs; if ($x) { $previous += , @($BuildRomfs, $x) }
        Move-Item -LiteralPath (Under $Stage 'romfs_build') -Destination $BuildRomfs
        Move-Item -LiteralPath (Under $Stage 'romfs_source.bin') -Destination $RomfsSource -Force
    } catch {
        foreach ($p in $previous) { if (-not (Test-Path -LiteralPath $p[0])) { Move-Item -LiteralPath $p[1] -Destination $p[0] } }
        throw
    }
    RemoveStage
    $n = [int]$kv['files']
    Info ('made in {0:mm\\:ss}: romfs_source.bin ({1} files, {2} folders), romfs_build ({3} files, {4:N1} MB){5}' -f ((Get-Date) - $t0), $n,
          $kv['dirs'], $kv['build_files'], ([int64]$kv['build_bytes'] / 1MB), $(if ($exefsOk) { '; the exefs was already there' } else { ', exefs' }))
    if ($n -ne $V.RomfsFiles) { Warn "romfs with $n files; today the $Edition edition has $($V.RomfsFiles)" }
    foreach ($p in $previous) { Warn "the previous one was kept in $($p[1]) (delete it by hand whenever you want)" }
    DirectDumpCheck
}

# AP_ROMFS_DIRECT: the game reads the RomFS from the dump: it must be in the root (as the exe looks for it). A full
# extract\\romfs from before is no longer needed (while it exists the exe reads it first).
function DirectDumpCheck {
    if (-not (Test-Path -LiteralPath $RomfsSource)) { return }
    $r = RunCapture $Python @($SourcePy, 'locate', '--source', $RomfsSource, '--root', $Root)
    $l = @($r.Output | Where-Object { $_ -match '^DUMP=' })
    if ($r.Code -eq 0 -and $l.Count) {
        $Summary['RomFS'] = "read by the game from $(Split-Path -Leaf ($l[0] -replace '^DUMP=', '')) (keep it in $Root)"
    } else {
        Warn "the dump romfs_source.bin was made from is not in $Root`: the game reads its files from it, put it there (any name)"
        $Summary['RomFS'] = "read from the dump, which is NOT in $Root (put it there)"
    }
    if (Test-Path -LiteralPath (Under $Data 'extract\\romfs')) {
        Info "$(Under $Data 'extract\\romfs') (a full extraction) is no longer needed: delete it to free ~21-23 GB (while it exists the game reads it first)"
    }
}

# AP_ROMFS_DIRECT: the base romfs of an update: from the dump (romfs_source.bin; no index and no extract\\romfs needed) or,
# with -Extract (or without the table), the index + extract\\romfs as before.
function BaseRomfsArgs {
    if (-not $Extract -and (Test-Path -LiteralPath $RomfsSource)) {
        $r = RunCapture $Python @($SourcePy, 'locate', '--source', $RomfsSource, '--root', $Root)
        $l = @($r.Output | Where-Object { $_ -match '^DUMP=' })
        if ($r.Code -eq 0 -and $l.Count) { return , @('--base-dump', ($l[0] -replace '^DUMP=', '')) }
        if (-not (Test-Path -LiteralPath (Under $Data 'extract\\romfs\\shader'))) {
            Fail "the dump romfs_source.bin was made from is not in $Root`: updates are read against it (put it back in the root)"
        }
    }
    EnsureRomfsIndex | Out-Host   # its python output is shown, not returned with the arguments
    return , @('--index', $RomfsIndex, '--base-romfs', (Under $Data 'extract\\romfs'))
}

function Step3 {
'''),
    (RECO,
     '''            EnsureRomfsIndex
            $tmp = Under $UpdData "_new_$name"
''',
     '''            $baseArgs = BaseRomfsArgs   # AP_ROMFS_DIRECT (was EnsureRomfsIndex)
            $tmp = Under $UpdData "_new_$name"
'''),
    (RECO,
     '''            & $Python $ModsPy mod --keys $Keys --nsp $f.FullName --program-id (Hex16 $V.ProgramId) --index $RomfsIndex `
                --base-romfs (Under $Data 'extract\\romfs') --base-exefs (Under $Data 'exefs') --out $tmp
''',
     '''            & $Python $ModsPy mod --keys $Keys --nsp $f.FullName --program-id (Hex16 $V.ProgramId) @baseArgs `
                --base-exefs (Under $Data 'exefs') --out $tmp   # AP_ROMFS_DIRECT: @baseArgs
'''),
    (RECO,
     '''    $a = @('--maxwell', $Maxwell, '--romfs-shader', (Under $Data 'extract\\romfs\\shader'), '--main-bin',''',
     '''    $a = @('--maxwell', $Maxwell, '--romfs-shader', (Under (BuildRomfsDir) 'shader'), '--main-bin','''),   # AP_ROMFS_DIRECT in the next edit
    (RECO,
     '''function CountShaders([string]$out) {
''',
     '''function CountShaders([string]$out) {   # AP_ROMFS_DIRECT: the shaders of romfs_build (or of extract\\romfs)
'''),
    (RECO,
     '''    if (-not $Summary.Contains('Shaders') -and (Test-Path -LiteralPath (Under $Data 'analysis\\main.bin')) -and (Test-Path -LiteralPath (Under $Data 'extract\\romfs\\shader'))) {
''',
     '''    if (-not $Summary.Contains('Shaders') -and (Test-Path -LiteralPath (Under $Data 'analysis\\main.bin')) -and (Test-Path -LiteralPath (Under (BuildRomfsDir) 'shader'))) {   # AP_ROMFS_DIRECT
'''),
    (RECO,
     '''    $Summary['Data root'] = $Data
''',
     '''    if (-not $Summary.Contains('RomFS')) {   # AP_ROMFS_DIRECT
        if (-not $Extract -and (Test-Path -LiteralPath $RomfsSource)) { DirectDumpCheck }
        elseif (Test-Path -LiteralPath (Under $Data 'extract\\romfs\\shader')) { $Summary['RomFS'] = Under $Data 'extract\\romfs' }
    }
    $Summary['Data root'] = $Data
'''),
    (RECO,
     '''    $left = @('gen.old', 'exefs.old', 'extract\\romfs.old', 'analysis\\main.bin.old', 'analysis\\sdk.bin.old' |
''',
     '''    $left = @('gen.old', 'exefs.old', 'extract\\romfs.old', 'romfs_build.old', 'analysis\\main.bin.old', 'analysis\\sdk.bin.old' |   # AP_ROMFS_DIRECT: romfs_build.old
'''),

    # ------------------------------------------------------------------------------------------------ install_requirements.ps1
    (INST,
     '''    [string]$InputFile = '',
    [string]$Keys = '',
''',
     '''    [string]$InputFile = '',
    [string]$Keys = '',
    [switch]$Extract,   # AP_ROMFS_DIRECT: passed to recomp_tsukihime.ps1 (-Recomp): full extraction of the RomFS
    [switch]$TrimXci,   # AP_ROMFS_DIRECT: passed to recomp_tsukihime.ps1 (-Recomp): trim the 0xFF padding of the .xci
'''),
    (INST,
     '''$SpacePerEdition = @{ global = 24GB; japan = 22GB }
''',
     '''$SpacePerEdition = @{ global = 24GB; japan = 22GB }
# AP_ROMFS_DIRECT: without -Extract the RomFS is not extracted (the game reads it from the dump): exefs, gen, analysis,
# build and the RomFS files of the build (romfs_build) only
if (-not $Extract) { $SpacePerEdition = @{ global = 3GB; japan = 3GB } }
'''),
    (INST,
     '''    $keysPath = if ($Keys) { [IO.Path]::GetFullPath($Keys) } else { PathOf $Root 'data/prod.keys' }
    if (Test-Path -LiteralPath $keysPath -PathType Leaf) { Info "prod.keys present: $keysPath (not read)" }
    else { Early "prod.keys not found: copy your keys to $keysPath" }
''',
     '''    $keysPath = if ($Keys) { [IO.Path]::GetFullPath($Keys) } else { PathOf $Root 'data/prod.keys' }
    $rootKeys = PathOf $Root 'prod.keys'   # AP_ROMFS_DIRECT: keys left in the root are moved to data\\ (recomp does the same)
    if (-not $Keys -and -not (Test-Path -LiteralPath $keysPath -PathType Leaf) -and (Test-Path -LiteralPath $rootKeys -PathType Leaf)) {
        if ($Check) { Info "prod.keys in the root: it will be moved to $keysPath (not read)" }
        else {
            New-Item -ItemType Directory -Path (Split-Path -Parent $keysPath) -Force | Out-Null
            Move-Item -LiteralPath $rootKeys -Destination $keysPath
            Info "prod.keys moved from the root to $keysPath (not read)"
        }
    } elseif (Test-Path -LiteralPath $keysPath -PathType Leaf) { Info "prod.keys present: $keysPath (not read)" }
    else { Early "prod.keys not found: copy your keys to $keysPath (or to $Root`: it is moved to data\\)" }
'''),
    (INST,
     '''    if ($Keys) { $a += @('-Keys', $Keys) }
    $script:InRecomp = "recomp_tsukihime.ps1 -Edition $ed -From $from -To $to"
''',
     '''    if ($Keys) { $a += @('-Keys', $Keys) }
    if ($Extract) { $a += '-Extract' }   # AP_ROMFS_DIRECT
    if ($TrimXci) { $a += '-TrimXci' }   # AP_ROMFS_DIRECT
    $script:InRecomp = "recomp_tsukihime.ps1 -Edition $ed -From $from -To $to"
'''),
    (INST,
     '''{ "copy your keys to $keys" }''',
     '''{ "copy your keys to $keys (or to the root: they are moved to data\\, AP_ROMFS_DIRECT)" }'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
