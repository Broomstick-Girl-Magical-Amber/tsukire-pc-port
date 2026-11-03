"""apply_title_scope.py - the title screen music changes only on the title screen (AP_TITLE_SCOPE).
Apply apply_title_bg.py first (apply_title_music.py too: without it there is no music to scope).

The story also plays MOON01_BGM02.opus (trace of 2026-11-01: opened in game, no titlebg video after it), so the music
redirection of title_bg.cpp must not act there. title_bg.cpp keeps a "visit" to the title screen: it opens when the
program starts (the first screen is the title and the game opens its music BEFORE its video) and when a titlebg video is
asked for, and it closes when the Extractor of that video is destroyed (the player leaves the title). Outside a visit
the two tracks go through untouched. This patcher gives it the two notices it needs from hle_movie.cpp, without
touching the decoding path:
  - declaration of tsuki_title::video_started / video_ended (defined by title_bg.cpp);
  - Extractor::SetDataSource (by path and with ClientStreamReader): video_started(host Extractor, served path) after the
    log, if the file was read (the same place as the notice for Discord);
  - Extractor D2 (also used by D0): video_ended(host Extractor) before deleting it.
  Both only compare the name with titlebg<N> and take a mutex: no I/O in the game threads.
It also adds two rows to $Markers of ../tools/recomp_tsukihime.ps1 (hle_movie.cpp and title_bg.cpp with AP_TITLE_SCOPE).

Known limit: coming BACK to the title from the story, the game opens the music before the video, when no visit is open
yet, so that music stays the game's (the video does change). Only the first title screen after launch is certain.

Idempotent (marker AP_TITLE_SCOPE), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found; --remove
leaves the files byte for byte as they were. Without the new title_bg.cpp in tsukihime_common linking fails (missing
tsuki_title::video_started/video_ended): copy it before building. After: cmake --build build\\tsukihime; the same with
build\\tsukihime_global.
"""
from _patch import run

MARK = "AP_TITLE_SCOPE"

DECL_ANCHOR = "std::string remap(const std::string& rel);\n}  // namespace tsuki_title\n"
DECL_NEW = ("std::string remap(const std::string& rel);\n"
            "// AP_TITLE_SCOPE (apply_title_scope.py): the music changes only while the title screen is up (title_bg.cpp).\n"
            "void video_started(const void* key, const char* rel);\n"
            "void video_ended(const void* key);\n"
            "}  // namespace tsuki_title\n")

PATH_ANCHOR = ('    nx::log("movie: SetDataSource(\\"%s\\") -> %zu bytes", rel.c_str(), data.size());\n'
               "    if (!data.empty()) tsuki_discord::video_started(ext(c.x[0]), rel.c_str());  // AP_DISCORD\n")
PATH_NEW = PATH_ANCHOR + "    if (!data.empty()) tsuki_title::video_started(ext(c.x[0]), rel.c_str());  // AP_TITLE_SCOPE\n"

READER_ANCHOR = ('            (unsigned long long)c.x[1], path ? path : "(null)", data.size());\n'
                 "    if (!data.empty()) tsuki_discord::video_started(ext(c.x[0]), rel.c_str());  // AP_DISCORD\n")
READER_NEW = READER_ANCHOR + "    if (!data.empty()) tsuki_title::video_started(ext(c.x[0]), rel.c_str());  // AP_TITLE_SCOPE\n"

END_ANCHOR = "    tsuki_discord::video_ended(ext(c.x[0]));  // AP_DISCORD\n"
END_NEW = END_ANCHOR + "    tsuki_title::video_ended(ext(c.x[0]));  // AP_TITLE_SCOPE\n"

MARK_ANCHOR = ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_MUSIC', "
               "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")
MARK_NEW = MARK_ANCHOR + (
    "    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_TITLE_SCOPE', 'apply_title_scope.py', $false),\n"
    "    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_SCOPE', "
    "'title_bg.cpp (copy it to games\\tsukihime_common)', $false),\n")

EDITS = [
    ("games/tsukihime_common/hle_movie.cpp", DECL_ANCHOR, DECL_NEW),
    ("games/tsukihime_common/hle_movie.cpp", PATH_ANCHOR, PATH_NEW),
    ("games/tsukihime_common/hle_movie.cpp", READER_ANCHOR, READER_NEW),
    ("games/tsukihime_common/hle_movie.cpp", END_ANCHOR, END_NEW),
    ("../tools/recomp_tsukihime.ps1", MARK_ANCHOR, MARK_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
