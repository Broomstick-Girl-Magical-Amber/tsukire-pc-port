"""apply_romfs_mods.py - romfs layers: mods of <root>\\mods\\<edition>\\ read before the game's romfs (AP_ROMFS_MODS).

Every folder of <root>\\mods\\japan (or \\global) with a romfs\\ inside is a layer: mods copied by hand, and the romfs
that recomp_tsukihime.ps1 extracts from the updates of <root>\\updates (only the files that differ from the base).
This patch makes the runtime serve them, per game and inert for the others (Wonder unchanged):
  - runtime/src/hle_fs.cpp: with NX_ROMFS_MODS, the rom: mount and read_game_file (videos of hle_movie) look for each
    file first in the layers, in folder name order (the LAST one first), then in extract/romfs; a folder that only a
    layer has is served from it; OpenDirectory on rom: joins the entries of the base and of the layers (a layer's file
    replaces the base one with the same name, case-insensitive). Skipped: folders starting with '_' or '.', and a
    layer with a requires_main.txt (the romfs of an update with its own main) different from
    <data root>/analysis/main_source.txt (the main this build is made from: its scripts would not match the code).
    The folder is <data root>/../../mods/<NX_ROMFS_MODS_EDITION>; NX_MODS_DIR overrides it. Read once at startup and
    logged; NX_TRACE_FS=1 logs every file served from a layer. Without layers: as before.
    Without the define the compiled code is the usual one.
  - games/tsukihime_common/tsukihime_common.cmake: NX_ROMFS_MODS and NX_ROMFS_MODS_EDITION ("japan" | "global").

Idempotent (marker AP_ROMFS_MODS), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found;
--remove leaves the files byte for byte as they were. After: cmake --build build\\tsukihime (and _global).
"""
from _patch import run

MARK = "AP_ROMFS_MODS"

LAYERS = r'''// AP_ROMFS_MODS (apply_romfs_mods.py): romfs layers. With NX_ROMFS_MODS (games/tsukihime_common) the rom: mount and
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
    }
    if (fs::exists(base_path, ec)) return {};
    for (auto it = l.dirs.rbegin(); it != l.dirs.rend(); ++it) {
        fs::path q = *it / rel;
        if (fs::is_directory(q, ec)) return q;
    }
    return {};
}

// OpenDirectory on rom:: adds the entries of the same folder in each layer (a layer's file replaces the one with the
// same name, case-insensitive).
void rom_merge_dirs(const std::string& guest, const fs::path& opened, int mode, std::vector<fs::directory_entry>& out) {
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

'''

RESOLVE_OLD = "    return it->second / fs::u8path(rest);\n}\n"
RESOLVE_NEW = ("    fs::path base = it->second / fs::u8path(rest);\n"
               "#ifdef NX_ROMFS_MODS\n"
               "    if (it->second == rom_layers().base) {  // AP_ROMFS_MODS: a layer's file first\n"
               "        fs::path q = rom_layer_path(rest, base);\n"
               "        if (!q.empty()) return q;\n"
               "    }\n"
               "#endif\n"
               "    return base;\n}\n")

DIR_OLD = ("        if ((dir && (mode & 1)) || (!dir && (mode & 2))) d.entries.push_back(e);\n"
           "    }\n"
           "    uint64_t h = g_next++;\n"
           "    g_dirs[h] = std::move(d);\n")
DIR_NEW = ("        if ((dir && (mode & 1)) || (!dir && (mode & 2))) d.entries.push_back(e);\n"
           "    }\n"
           "#ifdef NX_ROMFS_MODS\n"
           "    rom_merge_dirs(cstr(c.x[1]), p, mode, d.entries);  // AP_ROMFS_MODS\n"
           "#endif\n"
           "    uint64_t h = g_next++;\n"
           "    g_dirs[h] = std::move(d);\n")

GAME_FILE_OLD = '    FILE* f = plat::fopen_utf8(utf8(data_root() / "extract" / "romfs" / fs::u8path(relative_to_romfs)), "rb");\n'
GAME_FILE_NEW = ('    fs::path src = data_root() / "extract" / "romfs" / fs::u8path(relative_to_romfs);\n'
                 "#ifdef NX_ROMFS_MODS\n"
                 "    {  // AP_ROMFS_MODS: the videos of hle_movie also come from the layers\n"
                 "        std::string rest = relative_to_romfs;\n"
                 "        while (!rest.empty() && (rest[0] == '/' || rest[0] == '\\\\')) rest.erase(0, 1);\n"
                 "        fs::path q = rom_layer_path(rest, src);\n"
                 "        if (!q.empty()) src = q;\n"
                 "    }\n"
                 "#endif\n"
                 '    FILE* f = plat::fopen_utf8(utf8(src), "rb");\n')

CMAKE_ANCHOR = 'target_include_directories(${NX_GAME} PRIVATE "${NX_TSUKI_COMMON}")  # movie_core.h, input_tsuki.h\n'
CMAKE_NEW = CMAKE_ANCHOR + (
    "# AP_ROMFS_MODS (apply_romfs_mods.py): romfs layers of <root>/mods/<edition>: mods copied by hand and the romfs of\n"
    "# the updates of <root>/updates (recomp_tsukihime.ps1)\n"
    "if(NX_GAME STREQUAL \"tsukihime_global\")\n"
    "    target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_MODS NX_ROMFS_MODS_EDITION=\"global\")\n"
    "else()\n"
    "    target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_MODS NX_ROMFS_MODS_EDITION=\"japan\")\n"
    "endif()\n")

EDITS = [
    ("runtime/src/hle_fs.cpp", "#include <filesystem>\n",
     "#include <filesystem>\n#include <algorithm>  // AP_ROMFS_MODS\n#include <cstdlib>\n#include <fstream>\n#include <iterator>\n"),
    ("runtime/src/hle_fs.cpp", '// "mount:/a/b" -> host path; empty when the mount is unknown.\n',
     LAYERS + '// "mount:/a/b" -> host path; empty when the mount is unknown.\n'),
    ("runtime/src/hle_fs.cpp", RESOLVE_OLD, RESOLVE_NEW),
    ("runtime/src/hle_fs.cpp", DIR_OLD, DIR_NEW),
    ("runtime/src/hle_fs.cpp", GAME_FILE_OLD, GAME_FILE_NEW),
    ("games/tsukihime_common/tsukihime_common.cmake", CMAKE_ANCHOR, CMAKE_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
