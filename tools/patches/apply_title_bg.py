"""apply_title_bg.py - title screen background video chosen with title.ini (AP_TITLE_BG).

The logic lives in games/tsukihime_common/title_bg.cpp (new file, copied separately; picked up by the GLOB of
tsukihime_common.cmake): it reads title.ini / NX_TITLE_BG and says which movie/titlebg<N>.mp4 to serve instead of the one
the game asked for (auto = the game's own choice, 1-4 = fixed, random, cycle). This patcher only wires it into
hle_movie.cpp, without touching the decoding path:
  - declaration of tsuki_title::remap (defined by title_bg.cpp);
  - read_video(rel): asks remap() for another file; if that one exists it is served (and rel changes to it), if not the
    game's original is used. Logs "title: <asked> -> <served>" when it changes something;
  - Extractor::SetDataSource (by path and with ClientStreamReader): read_video() instead of nx::read_game_file().
    Videos that are not titlebg* go through exactly as before (remap returns "").
It also adds two rows to $Markers of ../tools/recomp_tsukihime.ps1 (hle_movie.cpp and title_bg.cpp with AP_TITLE_BG).

Music is not touched: the game keeps playing its own track (a separate .opus), so a video from another group of
titlebg can come with the game's music.

Idempotent (marker AP_TITLE_BG), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found; --remove
leaves the files byte for byte as they were. Without title_bg.cpp in tsukihime_common linking fails (missing
tsuki_title::remap): copy it before building. After: cmake build\\tsukihime (reconfigure, because of the new file in
the GLOB) and cmake --build build\\tsukihime; the same with build\\tsukihime_global.
"""
from _patch import run

MARK = "AP_TITLE_BG"

DECL_ANCHOR = "}  // namespace tsuki_discord\n"
DECL_NEW = DECL_ANCHOR + r'''
// AP_TITLE_BG (apply_title_bg.py): which titlebg<N>.mp4 to serve (games/tsukihime_common/title_bg.cpp).
// "" = the one the game asked for.
namespace tsuki_title {
std::string remap(const std::string& rel);
}  // namespace tsuki_title
'''

HELPER_ANCHOR = "// ============================================================================================== funciones libres\n"
HELPER_NEW = r'''// AP_TITLE_BG (apply_title_bg.py): reads the video, but a titlebg<N> may be replaced by the one title.ini says. If the
// replacement does not exist the game's own file is used. `rel` ends up being the file that was actually served.
std::string read_video(std::string& rel) {
    const std::string alt = tsuki_title::remap(rel);
    if (!alt.empty() && alt != rel) {
        std::string data = nx::read_game_file(alt);
        if (!data.empty()) {
            nx::log("title: %s -> %s", rel.c_str(), alt.c_str());
            rel = alt;
            return data;
        }
        nx::log("title: %s does not exist, using %s", alt.c_str(), rel.c_str());
    }
    return nx::read_game_file(rel);
}

''' + HELPER_ANCHOR

PATH_OLD = '    std::string data = nx::read_game_file(rel);\n    nx::log("movie: SetDataSource(\\"%s\\") -> %zu bytes", rel.c_str(), data.size());\n'
PATH_NEW = '    std::string data = read_video(rel);  // AP_TITLE_BG\n    nx::log("movie: SetDataSource(\\"%s\\") -> %zu bytes", rel.c_str(), data.size());\n'

READER_OLD = "    std::string data = rel.empty() ? std::string() : nx::read_game_file(rel);\n"
READER_NEW = "    std::string data = rel.empty() ? std::string() : read_video(rel);  // AP_TITLE_BG\n"

MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\input_tsuki.cpp', 'AP_MOUSE_INI', 'apply_mouse_ini.py', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_TITLE_BG', 'apply_title_bg.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_BG', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    ("games/tsukihime_common/hle_movie.cpp", DECL_ANCHOR, DECL_NEW),
    ("games/tsukihime_common/hle_movie.cpp", HELPER_ANCHOR, HELPER_NEW),
    ("games/tsukihime_common/hle_movie.cpp", PATH_OLD, PATH_NEW),
    ("games/tsukihime_common/hle_movie.cpp", READER_OLD, READER_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
