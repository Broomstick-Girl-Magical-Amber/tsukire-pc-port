// nn::fs at the API level, on host files. The game imports these by name (main), so no fsp-srv IPC is involved:
//   rom:   -> private/extract/romfs (the RomFS with the update applied)
//   save:  -> private/user/save/<mount>
//   cache: -> private/user/cache<index>
#include <cctype>
#include <cerrno>
#include <cstdio>
#include <filesystem>
#include <algorithm>  // AP_ROMFS_MODS
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#if defined(_WIN32)
#include <io.h>
#else
#include <unistd.h>
#endif

#include <nx/runtime.h>

#include "platform.h"

namespace fs = std::filesystem;

#ifdef NX_TITLE_MUSIC
// AP_TITLE_MUSIC (apply_title_music.py): the title screen tracks (games/tsukihime_common/title_bg.cpp). "" = no change.
namespace tsuki_title {
std::string remap_stream(const std::string& path);
}  // namespace tsuki_title
#endif
#ifdef NX_TITLE_LIVE
// AP_TITLE_LIVE_MUSIC (apply_title_live_music.py): the track to switch to when the title music starts over. "" = none.
namespace tsuki_title {
std::string music_swap(const std::string& path, int64_t off);
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
// AP_MOD_RECIPES (apply_mod_recipes.py): <file>.nxrcp of a mods layer = that file rebuilt from the dump + patch bytes.
Handle* open_recipe(const std::string& path);
bool recipe_size(const std::string& path, uint64_t* size);
bool read_all_recipe(const std::string& path, std::string& out);
}  // namespace tsuki_romfs
#endif

namespace nx {

namespace {

// NX_TRACE_FS=1: every file the game opens (and the ones it looks for and does not find).
bool trace_fs() {
    static const bool on = g_trace || std::getenv("NX_TRACE_FS") != nullptr;
    return on;
}

// The last opens, newest last: a crash report that says what was being loaded (log_recent_files, read by the crash
// handlers; try_lock there, a handler must not wait for a thread that holds the lock).
constexpr size_t kRecentOps = 24;
std::mutex g_recent_lock;
struct RecentOp {
    int64_t thread = 0;
    uint64_t seq = 0;
    char text[200] = {0};
} g_recent[kRecentOps];
uint64_t g_recent_seq = 0;

void note_open(const char* what, const std::string& guest_path, const char* result) {
    std::lock_guard lk(g_recent_lock);
    RecentOp& r = g_recent[g_recent_seq % kRecentOps];
    r.thread = plat::current_thread_id();
    r.seq = ++g_recent_seq;
    std::snprintf(r.text, sizeof r.text, "%s %s: %s", what, guest_path.c_str(), result);
}

constexpr uint32_t kOk = 0;
constexpr uint32_t kPathNotFound = 0x202;       // fs 1
constexpr uint32_t kPathAlreadyExists = 0x402;  // fs 2
constexpr uint32_t kTargetNotFound = 0x2EE202;  // fs 6001-ish: generic "not mounted"

std::mutex g_fs;
std::map<std::string, fs::path> g_mounts;
// Open files: shared so a read in progress survives a close from another thread. Reads run outside g_fs (which
// only guards the tables): one global lock made every streaming thread (Bink animations, FMOD banks) wait behind
// any big read, felt as hitches.
struct File {
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
std::map<uint64_t, std::shared_ptr<File>> g_files;

// Files the runtime adds or replaces (mods): key = path under the mount point, '/' separated, lower case.
std::map<std::string, std::string> g_virtual;

std::string virtual_key(const fs::path& p) {
    auto g = p.lexically_normal().generic_u8string();
    std::string s(g.begin(), g.end());
    for (char& ch : s) ch = char(std::tolower(static_cast<unsigned char>(ch)));
    return s;
}

// The virtual file whose key the resolved path ends with, or nullptr.
const std::string* find_virtual(const fs::path& resolved) {
    if (g_virtual.empty() || resolved.empty()) return nullptr;
    std::string k = virtual_key(resolved);
    for (auto& [key, data] : g_virtual)
        if (k.size() >= key.size() + 1 && k.compare(k.size() - key.size(), key.size(), key) == 0 &&
            k[k.size() - key.size() - 1] == '/')
            return &data;
    return nullptr;
}
struct Dir {
    std::vector<fs::directory_entry> entries;
    size_t pos = 0;
#ifdef NX_ROMFS_DIRECT
    // AP_ROMFS_DIRECT: entries of the RomFS of the dump, after `entries` (a name already in `entries` is left out)
    std::vector<std::string> names;
    std::vector<uint8_t> is_dir;
    std::vector<uint64_t> sizes;
#endif
};
std::map<uint64_t, Dir> g_dirs;
uint64_t g_next = 1;

fs::path data_root() { return fs::u8path(plat::data_root()); }

std::string utf8(const fs::path& p) {
    auto s = p.u8string();
    return std::string(s.begin(), s.end());
}

std::string cstr(uint64_t p) { return p ? std::string(reinterpret_cast<const char*>(p)) : std::string(); }

#ifdef NX_ROMFS_DIRECT
// AP_ROMFS_DIRECT: the path inside the RomFS of a resolved host path that is under extract/romfs (the rom: mount) and
// is not a file on the host (extract/romfs, if it is still there, wins file by file; its folders are joined with the
// dump's). False otherwise.
bool rom_direct_rel(const fs::path& p, std::string& rel) {
    if (p.empty() || !tsuki_romfs::enabled()) return false;
    const fs::path r = p.lexically_normal().lexically_relative((data_root() / "extract" / "romfs").lexically_normal());
    std::string s = utf8(r);
    if (s.empty() || s == ".." || s.rfind("../", 0) == 0 || s.rfind("..\\", 0) == 0) return false;
    if (s == ".") s.clear();
    std::error_code ec;
    if (fs::is_regular_file(p, ec)) return false;
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

// AP_ROMFS_MODS (apply_romfs_mods.py): romfs layers. With NX_ROMFS_MODS (games/tsukihime_common) the rom: mount and
// read_game_file look first in every folder of <root>/mods/<edition> that has a romfs/ (NX_MODS_DIR overrides the
// folder), in name order: the LAST one first. Skipped: names starting with '_' or '.', and layers whose
// requires_main.txt (romfs of an update with its own main, recomp_tsukihime.ps1) is not the main of this build
// (<data root>/analysis/main_source.txt). Folder listings of rom: join base and layers. Without layers: as before.
#ifdef NX_ROMFS_MODS
struct RomLayers {
    fs::path base;               // extract/romfs (the mount path of MountRom)
    std::vector<fs::path> dirs;  // <mods>/<layer>/romfs, in name order (the last one wins)
};

std::string read_trimmed(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    std::string s((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

std::string lower(std::string s) {
    for (char& ch : s) ch = char(std::tolower(static_cast<unsigned char>(ch)));
    return s;
}

fs::path mods_dir() {
    if (const char* e = std::getenv("NX_MODS_DIR"); e && *e) return fs::u8path(e);
#ifdef NX_ROMFS_MODS_EDITION
    return (data_root() / ".." / ".." / "mods" / NX_ROMFS_MODS_EDITION).lexically_normal();  // <root>/mods/<edition>
#else
    return data_root() / "mods";
#endif
}

const RomLayers& rom_layers() {
    static const RomLayers r = [] {
        RomLayers l;
        l.base = data_root() / "extract" / "romfs";
        fs::path dir = mods_dir();
        std::error_code ec;
        std::vector<std::pair<std::string, fs::path>> found;
        for (auto it = fs::directory_iterator(dir, ec); !ec && it != fs::directory_iterator(); it.increment(ec)) {
            std::error_code ec2;
            if (!it->is_directory(ec2)) continue;
            std::string name = utf8(it->path().filename());
            if (name.empty() || name[0] == '_' || name[0] == '.') continue;
            if (!fs::is_directory(it->path() / "romfs", ec2)) {
                log("fs: mods: %s has no romfs folder: ignored", name.c_str());
                continue;
            }
            found.emplace_back(name, it->path());
        }
        if (found.empty()) return l;
        std::sort(found.begin(), found.end(), [](const auto& x, const auto& y) { return lower(x.first) < lower(y.first); });
        std::string main_src = lower(read_trimmed(data_root() / "analysis" / "main_source.txt"));
        log("fs: mods folder %s", utf8(dir).c_str());
        for (auto& [name, p] : found) {
            std::error_code ec2;
            fs::path req = p / "requires_main.txt";
            if (fs::exists(req, ec2)) {
                std::string want = lower(read_trimmed(req));
                if (want != main_src) {
                    log("fs: mods: %s skipped: it is the romfs of an update whose main (%.16s) is not the one of this build "
                        "(%.16s); recompile with recomp_tsukihime.ps1 to use it", name.c_str(), want.c_str(),
                        main_src.empty() ? "base" : main_src.c_str());
                    continue;
                }
            }
            l.dirs.push_back(p / "romfs");
            log("fs: romfs layer %zu: %s", l.dirs.size(), name.c_str());
        }
        return l;
    }();
    return r;
}

// Where a romfs path is served from when a layer has it: the file of the last layer that has it or, if the base does
// not have the path, a folder of a layer. Empty: the base one.
fs::path rom_layer_path(const std::string& rest, const fs::path& base_path) {
    const RomLayers& l = rom_layers();
    if (l.dirs.empty() || rest.empty()) return {};
    std::error_code ec;
    fs::path rel = fs::u8path(rest);
    for (auto it = l.dirs.rbegin(); it != l.dirs.rend(); ++it) {
        fs::path q = *it / rel;
        if (fs::is_regular_file(q, ec)) {
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
    }
#ifdef NX_ROMFS_DIRECT
    std::string drel;  // AP_ROMFS_DIRECT: a path the base has in the dump is not a layer's folder
    if (fs::exists(base_path, ec) ||
        (rom_direct_rel(base_path, drel) && (tsuki_romfs::file(drel, nullptr) || tsuki_romfs::dir(drel))))
        return {};
#else
    if (fs::exists(base_path, ec)) return {};
#endif
    for (auto it = l.dirs.rbegin(); it != l.dirs.rend(); ++it) {
        fs::path q = *it / rel;
        if (fs::is_directory(q, ec)) return q;
    }
    return {};
}

// OpenDirectory on rom:: adds the entries of the same folder in each layer (a layer's file replaces the one with the
// same name, case-insensitive).
using RecipeList = std::vector<std::pair<std::string, uint64_t>>;  // AP_MOD_RECIPES: (name, size) of the recipes
void rom_merge_dirs(const std::string& guest, const fs::path& opened, int mode, std::vector<fs::directory_entry>& out,
                    RecipeList* recipes = nullptr) {
    const RomLayers& l = rom_layers();
    if (l.dirs.empty()) return;
    auto colon = guest.find(':');
    if (colon == std::string::npos) return;
    auto m = g_mounts.find(guest.substr(0, colon));
    if (m == g_mounts.end() || m->second != l.base) return;
    std::string rest = guest.substr(colon + 1);
    while (!rest.empty() && (rest[0] == '/' || rest[0] == '\\')) rest.erase(0, 1);
    auto key = [](const fs::path& p) {
        std::string k = utf8(p.filename());
        for (char& ch : k) ch = char(std::tolower(static_cast<unsigned char>(ch)));
        return k;
    };
    std::error_code ec;
    for (const fs::path& layer : l.dirs) {
        fs::path d = rest.empty() ? layer : layer / fs::u8path(rest);
        if (d == opened || !fs::is_directory(d, ec)) continue;
        for (auto& e : fs::directory_iterator(d, ec)) {
            bool dir = e.is_directory();
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
            for (auto& o : out) {
                if (key(o.path()) == k) {
                    if (!dir) o = e;
                    found = true;
                    break;
                }
            }
            if (!found) out.push_back(e);
        }
    }
}
#endif

// "mount:/a/b" -> host path; empty when the mount is unknown.
fs::path resolve(const std::string& guest) {
    auto colon = guest.find(':');
    if (colon == std::string::npos) return {};
    auto it = g_mounts.find(guest.substr(0, colon));
    if (it == g_mounts.end()) return {};
    std::string rest = guest.substr(colon + 1);
    while (!rest.empty() && (rest[0] == '/' || rest[0] == '\\')) rest.erase(0, 1);
    fs::path base = it->second / fs::u8path(rest);
#ifdef NX_ROMFS_MODS
    if (it->second == rom_layers().base) {  // AP_ROMFS_MODS: a layer's file first
        fs::path q = rom_layer_path(rest, base);
        if (!q.empty()) return q;
    }
#endif
    return base;
}

void mount(const std::string& name, const fs::path& p) {
    fs::create_directories(p);
    g_mounts[name] = p;
    log("fs: mounted %s: -> %s", name.c_str(), p.string().c_str());
}

// ---- mounts ---------------------------------------------------------------------------------------------------

void QueryMountRomCacheSize(CPU& c) {
    st<uint64_t>(c.x[0], 0x1000);
    c.x[0] = kOk;
}

void MountRom(CPU& c) {
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
    c.x[0] = kOk;
}

void EnsureSaveData(CPU& c) { c.x[0] = kOk; }

void MountSaveData(CPU& c) {
    std::lock_guard lk(g_fs);
    std::string name = cstr(c.x[0]);
    mount(name, data_root() / "user" / "save");
    c.x[0] = kOk;
}

void Unmount(CPU& c) {
    std::lock_guard lk(g_fs);
    g_mounts.erase(cstr(c.x[0]));
    c.x[0] = kOk;
}

void Commit(CPU& c) { c.x[0] = kOk; }
void EnableGlobalFileDataCache(CPU& c) { c.x[0] = kOk; }
void DisableGlobalFileDataCache(CPU& c) { c.x[0] = kOk; }

// ---- files ----------------------------------------------------------------------------------------------------

void CreateFile(CPU& c) {
    std::lock_guard lk(g_fs);
    fs::path p = resolve(cstr(c.x[0]));
    int64_t size = int64_t(c.x[1]);
    if (p.empty() || !fs::exists(p.parent_path())) {
        c.x[0] = kPathNotFound;
        return;
    }
    if (fs::exists(p)) {
        c.x[0] = kPathAlreadyExists;
        return;
    }
    FILE* f = plat::fopen_utf8(utf8(p), "wb");
    if (f) {
        if (size > 0) {
            plat::fseek64(f, size - 1, SEEK_SET);
            fputc(0, f);
        }
        fclose(f);
    }
    c.x[0] = f ? kOk : kPathNotFound;
}

void DeleteFile(CPU& c) {
    std::lock_guard lk(g_fs);
    fs::path p = resolve(cstr(c.x[0]));
    std::error_code ec;
    c.x[0] = !p.empty() && fs::remove(p, ec) ? kOk : kPathNotFound;
}

void CreateDirectory(CPU& c) {
    std::lock_guard lk(g_fs);
    fs::path p = resolve(cstr(c.x[0]));
    if (p.empty()) {
        c.x[0] = kPathNotFound;
        return;
    }
    if (fs::exists(p)) {
        c.x[0] = kPathAlreadyExists;
        return;
    }
    std::error_code ec;
    c.x[0] = fs::create_directory(p, ec) ? kOk : kPathNotFound;
}

void GetEntryType(CPU& c) {  // (DirectoryEntryType* out, path)
    std::lock_guard lk(g_fs);
    fs::path p = resolve(cstr(c.x[1]));
    if (find_virtual(p)) {
        st<uint32_t>(c.x[0], 1);
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
        c.x[0] = kPathNotFound;
        return;
    }
    st<uint32_t>(c.x[0], fs::is_directory(p) ? 0 : 1);
    c.x[0] = kOk;
}

void OpenFile(CPU& c) {  // (FileHandle* out, path, int mode: 1 read, 2 write, 4 append)
    std::lock_guard lk(g_fs);
    std::string guest = cstr(c.x[1]);
#ifdef NX_TITLE_MUSIC
    if (!(int(c.x[2]) & 2)) {  // AP_TITLE_MUSIC: the title screen music may be another track (read only)
        const std::string alt = tsuki_title::remap_stream(guest);
        if (!alt.empty()) {
            std::error_code ec0;
            const fs::path q = resolve(alt);
#ifdef NX_ROMFS_DIRECT
            std::string rel0;  // AP_ROMFS_DIRECT: the track may be in the dump
            if (!q.empty() && (fs::is_regular_file(q, ec0) || (rom_direct_rel(q, rel0) && tsuki_romfs::file(rel0, nullptr)))) guest = alt;
#else
            if (!q.empty() && fs::is_regular_file(q, ec0)) guest = alt;
#endif
            else log("title: %s does not exist, using %s", alt.c_str(), guest.c_str());
        }
    }
#endif
    fs::path p = resolve(guest);
    int mode = int(c.x[2]);
    if (const std::string* v = find_virtual(p)) {  // served from memory, through a temporary file
        FILE* f = std::tmpfile();
        if (!f) {
            c.x[0] = kPathNotFound;
            return;
        }
        std::fwrite(v->data(), 1, v->size(), f);
        std::fflush(f);
        uint64_t h = g_next++;
        g_files[h] = std::make_shared<File>(f, guest);
        st<uint64_t>(c.x[0], h);
        note_open("OpenFile", guest, "virtual");
        if (trace_fs()) log("fs: OpenFile(%s) -> virtual, %zu bytes", guest.c_str(), v->size());
        c.x[0] = kOk;
        return;
    }
#ifdef NX_ROMFS_DIRECT
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
        if (trace_fs()) log("fs: OpenFile(%s) not found", guest.c_str());
        c.x[0] = kPathNotFound;
        return;
    }
    FILE* f = plat::fopen_utf8(utf8(p), (mode & 2) ? "r+b" : "rb");
    if (!f) {
        note_open("OpenFile", guest, "the host could not open it");
        if (trace_fs()) log("fs: OpenFile(%s) failed to open on the host (errno %d)", guest.c_str(), errno);
        c.x[0] = kPathNotFound;
        return;
    }
    uint64_t h = g_next++;
    g_files[h] = std::make_shared<File>(f, guest);
    st<uint64_t>(c.x[0], h);
    note_open("OpenFile", guest, "ok");
    if (trace_fs()) log("fs: OpenFile(%s, %d) -> %llu", guest.c_str(), mode, (unsigned long long)h);
    c.x[0] = kOk;
}

std::shared_ptr<File> open_file(uint64_t h) {
    std::lock_guard lk(g_fs);
    auto it = g_files.find(h);
    return it == g_files.end() ? nullptr : it->second;
}

void CloseFile(CPU& c) {
    std::lock_guard lk(g_fs);
    g_files.erase(c.x[0]);
}

void GetFileSize(CPU& c) {  // (s64* out, FileHandle)
    auto file = open_file(c.x[1]);
    if (!file) {
        c.x[0] = kTargetNotFound;
        return;
    }
#ifdef NX_ROMFS_DIRECT
    if (file->direct) {  // AP_ROMFS_DIRECT
        st<int64_t>(c.x[0], int64_t(tsuki_romfs::size(file->direct)));
        c.x[0] = kOk;
        return;
    }
#endif
    std::lock_guard lk(g_fs);  // the FILE position is shared with writers
    FILE* f = file->f;
    int64_t cur = plat::ftell64(f);
    plat::fseek64(f, 0, SEEK_END);
    st<int64_t>(c.x[0], plat::ftell64(f));
    if (g_trace) log("fs: size %s = %lld", file->path.c_str(), (long long)ld<int64_t>(c.x[0]));
    plat::fseek64(f, cur, SEEK_SET);
    c.x[0] = kOk;
}

uint64_t read_at(const File& file, int64_t off, uint64_t buf, uint64_t size) {
    uint64_t t0 = plat::monotonic_ns();
#ifdef NX_ROMFS_DIRECT
    uint64_t n = file.direct ? tsuki_romfs::read(file.direct, off, reinterpret_cast<void*>(buf), size)  // AP_ROMFS_DIRECT
                             : plat::read_at(file.f, off, reinterpret_cast<void*>(buf), size);
#else
    uint64_t n = plat::read_at(file.f, off, reinterpret_cast<void*>(buf), size);
#endif
    uint64_t ms = (plat::monotonic_ns() - t0) / 1'000'000;
    if (g_trace) log("fs: read %s at %lld size %llu -> %llu", file.path.c_str(), (long long)off, (unsigned long long)size,
                     (unsigned long long)n);
    if (trace_fs() && file.path.find(".bwav") != std::string::npos) {  // streamed audio: is it being consumed?
        static std::mutex m;
        static std::map<std::string, uint64_t> reads;
        std::lock_guard lk(m);
        uint64_t k = ++reads[file.path];
        if (k <= 3 || k % 50 == 0) log("fs: stream read #%llu %s at %lld", (unsigned long long)k, file.path.c_str(), (long long)off);
    }
    if (ms >= 30)
        log("fs: slow read %s: %llu bytes at %lld took %llu ms", file.path.c_str(), (unsigned long long)size,
            (long long)off, (unsigned long long)ms);
    return n;
}

#ifdef NX_TITLE_LIVE
// AP_TITLE_LIVE_MUSIC: the title music starts over (read at offset 0) and the player chose another track with F5 / F6:
// the handle now points at that file. Readers that still hold the old one finish with it (shared_ptr).
std::shared_ptr<File> live_music(uint64_t h, std::shared_ptr<File> file, int64_t off) {
    if (file->path.find("MOON01_BGM") == std::string::npos) return file;
    if (trace_fs()) {
        static std::mutex m;
        static std::map<std::string, int> seen;
        std::lock_guard lk(m);
        if (++seen[file->path] <= 12) log("fs: music read %s at %lld", file->path.c_str(), (long long)off);
    }
    if (off != 0) return file;
    const std::string alt = tsuki_title::music_swap(file->path, off);
    if (alt.empty()) return file;
    std::lock_guard lk(g_fs);
    std::error_code ec;
    const fs::path q = resolve(alt);
#ifdef NX_ROMFS_DIRECT
    const bool rcp = !q.empty() && is_recipe(q);  // AP_MOD_RECIPES
    FILE* f = (q.empty() || rcp || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
    tsuki_romfs::Handle* dh = rcp ? tsuki_romfs::open_recipe(utf8(q)) : nullptr;  // AP_ROMFS_DIRECT: the track may be in the dump
    if (std::string rel; !f && !dh && rom_direct_rel(q, rel)) dh = tsuki_romfs::open(rel);
#else
    FILE* f = (q.empty() || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
#endif
#ifdef NX_ROMFS_DIRECT
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
    g_files[h] = nf;
    log("title: music %s -> %s (the game started it over)", file->path.c_str(), alt.c_str());
    return nf;
}
#endif

void ReadFileOut(CPU& c) {  // (size_t* out, FileHandle, s64 off, void* buf, size_t size)
    auto file = open_file(c.x[1]);
    if (!file) {
        c.x[0] = kTargetNotFound;
        return;
    }
#ifdef NX_TITLE_LIVE
    file = live_music(c.x[1], file, int64_t(c.x[2]));  // AP_TITLE_LIVE_MUSIC
#endif
    st<uint64_t>(c.x[0], read_at(*file, int64_t(c.x[2]), c.x[3], c.x[4]));
    c.x[0] = kOk;
}

void ReadFile(CPU& c) {  // (FileHandle, s64 off, void* buf, size_t size)
    auto file = open_file(c.x[0]);
    if (!file) {
        c.x[0] = kTargetNotFound;
        return;
    }
#ifdef NX_TITLE_LIVE
    file = live_music(c.x[0], file, int64_t(c.x[1]));  // AP_TITLE_LIVE_MUSIC
#endif
    read_at(*file, int64_t(c.x[1]), c.x[2], c.x[3]);
    c.x[0] = kOk;
}

void WriteFile(CPU& c) {  // (FileHandle, s64 off, const void* buf, size_t size, const WriteOption&)
    auto file = open_file(c.x[0]);
    if (!file) {
        c.x[0] = kTargetNotFound;
        return;
    }
#ifdef NX_ROMFS_DIRECT
    if (!file->f) {  // AP_ROMFS_DIRECT: a file of the dump is read only
        c.x[0] = kTargetNotFound;
        return;
    }
#endif
    std::lock_guard lk(g_fs);
    plat::fseek64(file->f, int64_t(c.x[1]), SEEK_SET);
    fwrite(reinterpret_cast<const void*>(c.x[2]), 1, c.x[3], file->f);
    fflush(file->f);  // positional reads bypass the FILE buffer (saves only: rare and small)
    c.x[0] = kOk;
}

void FlushFile(CPU& c) {
    if (auto file = open_file(c.x[0])) {
        std::lock_guard lk(g_fs);
#ifdef NX_ROMFS_DIRECT
        if (file->f) fflush(file->f);  // AP_ROMFS_DIRECT: fflush(nullptr) would flush every stream
#else
        fflush(file->f);
#endif
    }
    c.x[0] = kOk;
}

// ---- directories ----------------------------------------------------------------------------------------------

void OpenDirectory(CPU& c) {  // (DirectoryHandle* out, path, int mode: 1 dirs, 2 files)
    std::lock_guard lk(g_fs);
    fs::path p = resolve(cstr(c.x[1]));
    int mode = int(c.x[2]);
    std::error_code ec;
    bool found = !p.empty() && fs::is_directory(p, ec);
#ifdef NX_ROMFS_DIRECT
    std::string drel;  // AP_ROMFS_DIRECT: a folder of the RomFS of the dump
    const bool direct = rom_direct_rel(p, drel) && tsuki_romfs::dir(drel);
    found = found || direct;
#endif
    note_open("OpenDirectory", cstr(c.x[1]), found ? "ok" : "not found");
    if (trace_fs()) log("fs: OpenDirectory(%s, %d)%s", cstr(c.x[1]).c_str(), mode, found ? "" : " not found");
    if (!found) {
        c.x[0] = kPathNotFound;
        return;
    }
    Dir d;
    for (auto& e : fs::directory_iterator(p, ec)) {
        bool dir = e.is_directory();
        if ((dir && (mode & 1)) || (!dir && (mode & 2))) d.entries.push_back(e);
    }
#ifdef NX_ROMFS_MODS
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
        for (auto& nm : d.names) have.push_back(low(nm));  // AP_MOD_RECIPES: the recipes already listed
        for (size_t i = 0; i < names.size(); i++) {
            if (!((dirs[i] && (mode & 1)) || (!dirs[i] && (mode & 2)))) continue;
            if (std::find(have.begin(), have.end(), low(names[i])) != have.end()) continue;
            d.names.push_back(names[i]);
            d.is_dir.push_back(dirs[i]);
            d.sizes.push_back(sizes[i]);
        }
    }
#endif
    uint64_t h = g_next++;
    g_dirs[h] = std::move(d);
    st<uint64_t>(c.x[0], h);
    c.x[0] = kOk;
}

void ReadDirectory(CPU& c) {  // (s64* out_count, DirectoryEntry* entries, DirectoryHandle, s64 max)
    std::lock_guard lk(g_fs);
    auto it = g_dirs.find(c.x[2]);
    if (it == g_dirs.end()) {
        c.x[0] = kTargetNotFound;
        return;
    }
    Dir& d = it->second;
    int64_t n = 0, max = int64_t(c.x[3]);
    for (; n < max && d.pos < d.entries.size(); n++, d.pos++) {
        const auto& e = d.entries[d.pos];
        uint64_t out = c.x[1] + uint64_t(n) * 0x310;
        std::memset(reinterpret_cast<void*>(out), 0, 0x310);
        auto u8 = e.path().filename().u8string();
        std::string name(u8.begin(), u8.end());
        std::memcpy(reinterpret_cast<void*>(out), name.c_str(), std::min<size_t>(name.size(), 0x300));
        bool dir = e.is_directory();
        st<int8_t>(out + 0x304, dir ? 0 : 1);
        st<int64_t>(out + 0x308, dir ? 0 : int64_t(e.file_size()));
    }
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

void GetDirectoryEntryCount(CPU& c) {  // (s64* out, DirectoryHandle)
    std::lock_guard lk(g_fs);
    auto it = g_dirs.find(c.x[1]);
#ifdef NX_ROMFS_DIRECT
    st<int64_t>(c.x[0], it == g_dirs.end() ? 0 : int64_t(it->second.entries.size() + it->second.names.size()));  // AP_ROMFS_DIRECT
#else
    st<int64_t>(c.x[0], it == g_dirs.end() ? 0 : int64_t(it->second.entries.size()));
#endif
    c.x[0] = kOk;
}

void CloseDirectory(CPU& c) {
    std::lock_guard lk(g_fs);
    g_dirs.erase(c.x[0]);
}

// ---- cache storage --------------------------------------------------------------------------------------------

void OpenCacheStorageList(CPU& c) {
    st<uint64_t>(c.x[0], 1);
    c.x[0] = kOk;
}
void ReadCacheStorageList(CPU& c) {  // (int* out, CacheStorageInfo*, handle, int)
    st<int32_t>(c.x[0], 0);
    c.x[0] = kOk;
}
void CloseCacheStorageList(CPU& c) {}
void CreateCacheStorage(CPU& c) { c.x[0] = kOk; }
void DeleteCacheStorage(CPU& c) { c.x[0] = kOk; }
void MountCacheStorage(CPU& c) {  // (name, int index)
    std::lock_guard lk(g_fs);
    mount(cstr(c.x[0]), data_root() / "user" / ("cache" + std::to_string(int(c.x[1]))));
    c.x[0] = kOk;
}

// ---- SDK 16 additions (Super Mario Bros. Wonder) ---------------------------------------------------------------
// MountRom(name) without a cache buffer, and ReadFile(size_t* out, FileHandle, s64, void*, size_t, const ReadOption&)
// are the older calls with one argument less / more: the same functions serve them.

void IsSaveDataExisting(CPU& c) { c.x[0] = 1; }  // (const account::Uid&) -> bool: EnsureSaveData made it

void SetFileSize(CPU& c) {  // (FileHandle, s64 size)
    auto file = open_file(c.x[0]);
    if (!file) {
        c.x[0] = kTargetNotFound;
        return;
    }
#ifdef NX_ROMFS_DIRECT
    if (!file->f) {  // AP_ROMFS_DIRECT: a file of the dump is read only
        c.x[0] = kPathNotFound;
        return;
    }
#endif
    std::lock_guard lk(g_fs);
    std::fflush(file->f);
#if defined(_WIN32)
    bool ok = _chsize_s(_fileno(file->f), int64_t(c.x[1])) == 0;
#else
    bool ok = ftruncate(fileno(file->f), off_t(c.x[1])) == 0;
#endif
    c.x[0] = ok ? kOk : kPathNotFound;
}

void DeleteDirectoryRecursively(CPU& c) {
    std::lock_guard lk(g_fs);
    fs::path p = resolve(cstr(c.x[0]));
    std::error_code ec;
    if (p.empty() || !fs::is_directory(p, ec)) {
        c.x[0] = kPathNotFound;
        return;
    }
    fs::remove_all(p, ec);
    c.x[0] = ec ? kPathNotFound : kOk;
}

}  // namespace

NX_HLE("_ZN2nn2fs22QueryMountRomCacheSizeEPm", QueryMountRomCacheSize);
NX_HLE("_ZN2nn2fs8MountRomEPKcPvm", MountRom);
NX_HLE("_ZN2nn2fs14EnsureSaveDataERKNS_7account3UidE", EnsureSaveData);
NX_HLE("_ZN2nn2fs13MountSaveDataEPKcRKNS_7account3UidE", MountSaveData);
NX_HLE("_ZN2nn2fs7UnmountEPKc", Unmount);
NX_HLE("_ZN2nn2fs10DeleteFileEPKc", DeleteFile);
NX_HLE("_ZN2nn2fs10CreateFileEPKcl", CreateFile);
NX_HLE("_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci", OpenFile);
NX_HLE("_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE", GetFileSize);
NX_HLE("_ZN2nn2fs15CreateDirectoryEPKc", CreateDirectory);
NX_HLE("_ZN2nn2fs12GetEntryTypeEPNS0_18DirectoryEntryTypeEPKc", GetEntryType);
NX_HLE("_ZN2nn2fs9CloseFileENS0_10FileHandleE", CloseFile);
NX_HLE("_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm", ReadFileOut);
NX_HLE("_ZN2nn2fs8ReadFileENS0_10FileHandleElPvm", ReadFile);
NX_HLE("_ZN2nn2fs9WriteFileENS0_10FileHandleElPKvmRKNS0_11WriteOptionE", WriteFile);
NX_HLE("_ZN2nn2fs9FlushFileENS0_10FileHandleE", FlushFile);
NX_HLE("_ZN2nn2fs25EnableGlobalFileDataCacheEPvm", EnableGlobalFileDataCache);
NX_HLE("_ZN2nn2fs26DisableGlobalFileDataCacheEv", DisableGlobalFileDataCache);
NX_HLE("_ZN2nn2fs6CommitEPKc", Commit);
NX_HLE("_ZN2nn2fs13OpenDirectoryEPNS0_15DirectoryHandleEPKci", OpenDirectory);
NX_HLE("_ZN2nn2fs13ReadDirectoryEPlPNS0_14DirectoryEntryENS0_15DirectoryHandleEl", ReadDirectory);
NX_HLE("_ZN2nn2fs14CloseDirectoryENS0_15DirectoryHandleE", CloseDirectory);
NX_HLE("_ZN2nn2fs22GetDirectoryEntryCountEPlNS0_15DirectoryHandleE", GetDirectoryEntryCount);
NX_HLE("_ZN2nn2fs20OpenCacheStorageListEPNS0_22CacheStorageListHandleE", OpenCacheStorageList);
NX_HLE("_ZN2nn2fs20ReadCacheStorageListEPiPNS0_16CacheStorageInfoENS0_22CacheStorageListHandleEi", ReadCacheStorageList);
NX_HLE("_ZN2nn2fs21CloseCacheStorageListENS0_22CacheStorageListHandleE", CloseCacheStorageList);
NX_HLE("_ZN2nn2fs18CreateCacheStorageEill", CreateCacheStorage);
NX_HLE("_ZN2nn2fs18DeleteCacheStorageEi", DeleteCacheStorage);
NX_HLE("_ZN2nn2fs17MountCacheStorageEPKci", MountCacheStorage);
NX_HLE("_ZN2nn2fs8MountRomEPKc", MountRom);
NX_HLE("_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvmRKNS0_10ReadOptionE", ReadFileOut);
NX_HLE("_ZN2nn2fs18IsSaveDataExistingERKNS_7account3UidE", IsSaveDataExisting);
NX_HLE("_ZN2nn2fs11SetFileSizeENS0_10FileHandleEl", SetFileSize);
NX_HLE("_ZN2nn2fs26DeleteDirectoryRecursivelyEPKc", DeleteDirectoryRecursively);

void log_recent_files() {
    std::unique_lock lk(g_recent_lock, std::try_to_lock);
    if (!lk.owns_lock()) return;
    log("  last file opens (newest last; thread = host thread id):");
    for (uint64_t k = 0; k < kRecentOps; k++) {
        const RecentOp& r = g_recent[(g_recent_seq + k) % kRecentOps];
        if (r.seq) log("    #%llu thread %lld  %s", (unsigned long long)r.seq, (long long)r.thread, r.text);
    }
}

void add_virtual_file(const std::string& path, std::string data) {
    std::lock_guard lk(g_fs);
    g_virtual[virtual_key(fs::u8path(path))] = std::move(data);
}

// AP_MOVIE_STREAM (apply_movie_stream.py): read_game_file's lookup (mods layer, extract/romfs, the dump) as a reader.
std::function<int64_t(uint64_t, void*, size_t)> open_game_stream(const std::string& relative_to_romfs, uint64_t* size) {
    fs::path src = data_root() / "extract" / "romfs" / fs::u8path(relative_to_romfs);
#ifdef NX_ROMFS_MODS
    {
        std::string rest = relative_to_romfs;
        while (!rest.empty() && (rest[0] == '/' || rest[0] == '\\')) rest.erase(0, 1);
        fs::path q = rom_layer_path(rest, src);
        if (!q.empty()) src = q;
    }
#endif
#ifdef NX_ROMFS_DIRECT
    if (is_recipe(src)) return {};  // a recipe is rebuilt whole
#endif
    FILE* raw = plat::fopen_utf8(utf8(src), "rb");
    if (raw) {
        std::shared_ptr<FILE> f(raw, [](FILE* p) { std::fclose(p); });
        if (std::fseek(raw, 0, SEEK_END) != 0) return {};
        const int64_t sz = plat::ftell64(raw);
        if (sz <= 0) return {};
        *size = uint64_t(sz);
        return [f](uint64_t off, void* buf, size_t n) -> int64_t { return int64_t(plat::read_at(f.get(), int64_t(off), buf, n)); };
    }
#ifdef NX_ROMFS_DIRECT
    if (std::string rel; rom_direct_rel(src, rel)) {
        tsuki_romfs::Handle* h = tsuki_romfs::open(rel);
        if (!h) return {};
        std::shared_ptr<tsuki_romfs::Handle> hp(h, [](tsuki_romfs::Handle* p) { tsuki_romfs::close(p); });
        *size = tsuki_romfs::size(h);
        return [hp](uint64_t off, void* buf, size_t n) -> int64_t { return int64_t(tsuki_romfs::read(hp.get(), int64_t(off), buf, n)); };
    }
#endif
    return {};
}

std::string read_game_file(const std::string& relative_to_romfs) {
    fs::path src = data_root() / "extract" / "romfs" / fs::u8path(relative_to_romfs);
#ifdef NX_ROMFS_MODS
    {  // AP_ROMFS_MODS: the videos of hle_movie also come from the layers
        std::string rest = relative_to_romfs;
        while (!rest.empty() && (rest[0] == '/' || rest[0] == '\\')) rest.erase(0, 1);
        fs::path q = rom_layer_path(rest, src);
        if (!q.empty()) src = q;
    }
#endif
#ifdef NX_ROMFS_DIRECT
    if (is_recipe(src)) {  // AP_MOD_RECIPES
        std::string s;
        return tsuki_romfs::read_all_recipe(utf8(src), s) ? s : std::string();
    }
#endif
    FILE* f = plat::fopen_utf8(utf8(src), "rb");
#ifdef NX_ROMFS_DIRECT
    if (!f) {  // AP_ROMFS_DIRECT: a RomFS file of the dump
        std::string rel, s;
        if (rom_direct_rel(src, rel) && tsuki_romfs::read_all(rel, s)) return s;
        return {};
    }
#endif
    if (!f) return {};
    std::string s;
    if (std::fseek(f, 0, SEEK_END) == 0) {  // AP_TITLE_SMOOTH: sized once, one read
        const long sz = std::ftell(f);
        std::rewind(f);
        if (sz > 0) {
            s.resize(size_t(sz));
            s.resize(std::fread(&s[0], 1, s.size(), f));
        }
    } else {
        std::rewind(f);
    }
    char buf[65536];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) s.append(buf, n);
    std::fclose(f);
    return s;
}

}  // namespace nx
