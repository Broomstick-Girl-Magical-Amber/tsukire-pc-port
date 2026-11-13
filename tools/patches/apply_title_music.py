"""apply_title_music.py - title screen music chosen with title.ini (AP_TITLE_MUSIC). Apply apply_title_bg.py first.

The title screen has two music tracks, separate files (not the audio of the mp4): stream/MOON01_BGM02.opus for the videos
titlebg1-3 and stream/MOON01_BGM59.opus for titlebg4 (seen in the log with NX_TRACE_FS=1). The logic lives in
games/tsukihime_common/title_bg.cpp (remap_stream; the same file apply_title_bg.py needs, with the music added). This
patcher only wires it into the runtime:
  - runtime/src/hle_fs.cpp, nn::fs::OpenFile: for a file opened for reading, asks tsuki_title::remap_stream(path); if it
    answers another path and that file exists (a romfs layer counts), that one is opened. Any other file, and a track
    that has no replacement, go through exactly as before. Under NX_TITLE_MUSIC only: without the define the compiled
    code is the usual one (Wonder unchanged);
  - games/tsukihime_common/tsukihime_common.cmake: the define NX_TITLE_MUSIC;
  - two rows in $Markers of ../tools/recomp_tsukihime.ps1 (hle_fs.cpp and title_bg.cpp with AP_TITLE_MUSIC).

"""
from _patch import run

MARK = "AP_TITLE_MUSIC"

DECL_ANCHOR = "namespace fs = std::filesystem;\n"
DECL_NEW = DECL_ANCHOR + r'''
#ifdef NX_TITLE_MUSIC
// AP_TITLE_MUSIC (apply_title_music.py): the title screen tracks (games/tsukihime_common/title_bg.cpp). "" = no change.
namespace tsuki_title {
std::string remap_stream(const std::string& path);
}  // namespace tsuki_title
#endif
'''

OPEN_OLD = ("    std::string guest = cstr(c.x[1]);\n"
            "    fs::path p = resolve(guest);\n"
            "    int mode = int(c.x[2]);\n"
            "    if (const std::string* v = find_virtual(p)) {  // served from memory, through a temporary file\n")
OPEN_NEW = ("    std::string guest = cstr(c.x[1]);\n"
            "#ifdef NX_TITLE_MUSIC\n"
            "    if (!(int(c.x[2]) & 2)) {  // AP_TITLE_MUSIC: the title screen music may be another track (read only)\n"
            "        const std::string alt = tsuki_title::remap_stream(guest);\n"
            "        if (!alt.empty()) {\n"
            "            std::error_code ec0;\n"
            "            const fs::path q = resolve(alt);\n"
            "            if (!q.empty() && fs::is_regular_file(q, ec0)) guest = alt;\n"
            "            else log(\"title: %s does not exist, using %s\", alt.c_str(), guest.c_str());\n"
            "        }\n"
            "    }\n"
            "#endif\n"
            "    fs::path p = resolve(guest);\n"
            "    int mode = int(c.x[2]);\n"
            "    if (const std::string* v = find_virtual(p)) {  // served from memory, through a temporary file\n")

CMAKE_ANCHOR = "target_compile_definitions(${NX_GAME} PRIVATE NX_HUD_GLYPHS)\n"
CMAKE_NEW = CMAKE_ANCHOR + (
    "# AP_TITLE_MUSIC (apply_title_music.py): the music of the title screen can be another track (title_bg.cpp, title.ini)\n"
    "target_compile_definitions(${NX_GAME} PRIVATE NX_TITLE_MUSIC)\n")

MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_BG', "
               "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\runtime\\src\\hle_fs.cpp', 'AP_TITLE_MUSIC', 'apply_title_music.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_MUSIC', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    ("runtime/src/hle_fs.cpp", DECL_ANCHOR, DECL_NEW),
    ("runtime/src/hle_fs.cpp", OPEN_OLD, OPEN_NEW),
    ("games/tsukihime_common/tsukihime_common.cmake", CMAKE_ANCHOR, CMAKE_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
