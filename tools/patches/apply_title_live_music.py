"""apply_title_live_music.py - F5 / F6 also change the title screen music (AP_TITLE_LIVE_MUSIC).
Apply apply_title_live.py first (with the new title_bg.cpp that goes with this patch).

A track that is playing cannot be swapped by the host: the game streams it through its own handle. What can be done is to
change the file behind that handle at the moment the game starts the track over. So:
  - runtime/src/hle_fs.cpp, nn::fs::ReadFile (both forms): on a read at offset 0 of MOON01_BGM02 / MOON01_BGM59, asks
    tsuki_title::music_swap(path, offset). If the player chose with F5 / F6 a video whose track is the other one (and the
    title screen is up, music = follow), the handle is pointed at the other file from then on (its size too). Any other read
    goes through exactly as before. Under NX_TITLE_LIVE only (Wonder unchanged);
  - title_bg.cpp (new version): music_swap().
This only works if the game starts the track again by reading from offset 0 (the loop of the music, or a new play). If it
loops by jumping to a loop point inside the file, nothing is swapped and the music changes the next time the title opens,
as before. With NX_TRACE_FS=1 the log shows the reads of these tracks ("fs: music read ...") to see what it does.
It also adds a row to $Markers of ../tools/recomp_tsukihime.ps1 for hle_fs.cpp and one for title_bg.cpp.

Idempotent (marker AP_TITLE_LIVE_MUSIC), all or nothing, keeps CRLF and BOM; --remove leaves the files byte for byte.
"""
from _patch import run

MARK = "AP_TITLE_LIVE_MUSIC"

DECL_ANCHOR = "std::string remap_stream(const std::string& path);\n}  // namespace tsuki_title\n#endif\n"
DECL_NEW = ("std::string remap_stream(const std::string& path);\n}  // namespace tsuki_title\n#endif\n"
            "#ifdef NX_TITLE_LIVE\n"
            "// AP_TITLE_LIVE_MUSIC (apply_title_live_music.py): the track to switch to when the title music starts over. \"\" = none.\n"
            "namespace tsuki_title {\n"
            "std::string music_swap(const std::string& path, int64_t off);\n"
            "}  // namespace tsuki_title\n"
            "#endif\n")

HELPER_ANCHOR = "void ReadFileOut(CPU& c) {  // (size_t* out, FileHandle, s64 off, void* buf, size_t size)\n"
HELPER_NEW = r'''#ifdef NX_TITLE_LIVE
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
    FILE* f = (q.empty() || !fs::is_regular_file(q, ec)) ? nullptr : plat::fopen_utf8(utf8(q), "rb");
    if (!f) {
        log("title: %s could not be opened: the music stays", alt.c_str());
        return file;
    }
    auto nf = std::make_shared<File>(f, alt);
    g_files[h] = nf;
    log("title: music %s -> %s (the game started it over)", file->path.c_str(), alt.c_str());
    return nf;
}
#endif

''' + HELPER_ANCHOR

OUT_OLD = "    st<uint64_t>(c.x[0], read_at(*file, int64_t(c.x[2]), c.x[3], c.x[4]));\n"
OUT_NEW = ("#ifdef NX_TITLE_LIVE\n"
           "    file = live_music(c.x[1], file, int64_t(c.x[2]));  // AP_TITLE_LIVE_MUSIC\n"
           "#endif\n" + OUT_OLD)
RD_OLD = "    read_at(*file, int64_t(c.x[1]), c.x[2], c.x[3]);\n"
RD_NEW = ("#ifdef NX_TITLE_LIVE\n"
          "    file = live_music(c.x[0], file, int64_t(c.x[1]));  // AP_TITLE_LIVE_MUSIC\n"
          "#endif\n" + RD_OLD)

MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_LIVE', "
               "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\runtime\\src\\hle_fs.cpp', 'AP_TITLE_LIVE_MUSIC', 'apply_title_live_music.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_LIVE_MUSIC', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    ("runtime/src/hle_fs.cpp", DECL_ANCHOR, DECL_NEW),
    ("runtime/src/hle_fs.cpp", HELPER_ANCHOR, HELPER_NEW),
    ("runtime/src/hle_fs.cpp", OUT_OLD, OUT_NEW),
    ("runtime/src/hle_fs.cpp", RD_OLD, RD_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
