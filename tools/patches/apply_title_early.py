"""apply_title_early.py - the title screen video is read during the TYPE-MOON logo, not when the title opens (AP_TITLE_EARLY).
Apply apply_title_smooth.py first (it reuses its background read of title videos).

Why: the first open of the title video in a session reads the whole file (movie/titlebg4.mp4 is 139 MB) in the game's
thread: ~1 s without a frame when the file is not in the disk cache (log of 2026-11-05: 1047 ms cold, 120-165 ms warm).
The logo video (TM_CI.mp4) plays for ~4 s just before, and nothing reads the disk meanwhile.

What it does (games/tsukihime_common/hle_movie.cpp only):
  - when a title video is served, its path (after title.ini) is written to <data root>\\title_last.txt if it changed;
  - when another video opens through ClientStreamReader (the logo) and no title video has opened yet in this session,
    the background read of AP_TITLE_SMOOTH starts for the path in title_last.txt. When the game then asks for the same
    video it takes it from memory; if it asks for another one (title.ini random, a new unlock) the read is dropped and
    the game's video is read as before;
  - nx.log: "title: early read of <path> ..." and, when the title video opens, whether it was used.
NX_TITLE_EARLY=0 turns it off; NX_TITLE_PREFETCH=0 (AP_TITLE_SMOOTH) turns it off too. Costs one read of the video
(and its memory, ~140 MB) a few seconds earlier than before.
Plus a row in $Markers of ../tools/recomp_tsukihime.ps1.
Idempotent (marker AP_TITLE_EARLY), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found; --remove
leaves the files byte for byte. It can be removed before or after the other title patchers.
After: cmake --build build\\tsukihime (or recomp_tsukihime.ps1 -Edition japan -From 9); the same with build\\tsukihime_global.

Usage (in <root>\\framework):
  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_title_early.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import FRAMEWORK_ROOT, run  # noqa: E402

MARK = "AP_TITLE_EARLY"
M = "games/tsukihime_common/hle_movie.cpp"
RECOMP_PS = "../tools/recomp_tsukihime.ps1"

INC_OLD = "#include <nx/runtime.h>\n"
INC_NEW = INC_OLD + "#include \"platform.h\"  // AP_TITLE_EARLY: data_root, fopen_utf8\n"

HELPER_OLD = "// ============================================================================================== funciones libres\n"
HELPER_NEW = HELPER_OLD + r'''
// AP_TITLE_EARLY (apply_title_early.py): the title video of the last session is read (the background read of apply_title_smooth.py)
// while the logo plays, so the first open of the title does not wait for the file. <data root>/title_last.txt.
std::atomic<bool> g_early_title_seen{false};
std::mutex g_early_m;
std::string g_early_last;     // what title_last.txt says (read once)
std::string g_early_started;  // what was read early ("" = nothing)
bool g_early_loaded = false;
std::string early_file() { return nx::plat::data_root() + "/title_last.txt"; }
bool early_enabled() {
    static const bool on = [] { const char* e = std::getenv("NX_TITLE_EARLY"); return !(e && e[0] == '0'); }();
    return on && prefetch_enabled();
}
void early_load_locked() {
    if (g_early_loaded) return;
    g_early_loaded = true;
    if (FILE* f = nx::plat::fopen_utf8(early_file(), "rb")) {
        char buf[256] = {};
        const size_t n = std::fread(buf, 1, sizeof buf - 1, f);
        std::fclose(f);
        std::string s(buf, n);
        while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
        if (is_title_video(s) && s.find("..") == std::string::npos) g_early_last = s;
    }
}
// After a video was read for SetDataSource(ClientStreamReader): `rel` = the file served, `ok` = it was read.
void title_early(const std::string& rel, bool ok) {
    if (!ok || rel.empty() || !early_enabled()) return;
    std::lock_guard<std::mutex> lk(g_early_m);
    early_load_locked();
    if (is_title_video(rel)) {
        if (!g_early_title_seen.exchange(true) && !g_early_started.empty())
            nx::log("title: early read of %s %s", g_early_started.c_str(),
                    g_early_started == rel ? "used" : ("not used: the game asked for " + rel).c_str());
        if (rel != g_early_last) {
            if (FILE* f = nx::plat::fopen_utf8(early_file(), "wb")) {
                std::fwrite(rel.data(), 1, rel.size(), f);
                std::fclose(f);
                g_early_last = rel;
            }
        }
        return;
    }
    if (g_early_title_seen.load() || g_early_last.empty()) return;
    // Another video opened before the title (it drops the background read): start it again.
    if (g_early_started.empty())
        nx::log("title: early read of %s (the title video of the last session; NX_TITLE_EARLY=0 turns it off)",
                g_early_last.c_str());
    g_early_started = g_early_last;
    prefetch_start(g_early_last);
}
'''

CALL_OLD = ("    ret(c, data.empty() ? kIoError : ext(c.x[0])->open_memory(std::move(data)));\n"
            "}\n"
            "void Extractor_GetTrackCount(nx::CPU& c) {\n")
CALL_NEW = "    title_early(rel, !data.empty());  // AP_TITLE_EARLY\n" + CALL_OLD

PS_OLD = ("    @('both', 'framework\\runtime\\src\\nvn\\nvn_draw_state.h', 'stage_program', 'apply_stage_programs.py', "
          "$false),\n")
PS_NEW = PS_OLD + ("    @('both', 'framework\\games\\tsukihime_common\\hle_movie.cpp', 'AP_TITLE_EARLY', 'apply_title_early.py', "
                   "$false),\n")

EDITS = [
    (M, INC_OLD, INC_NEW),
    (M, HELPER_OLD, HELPER_NEW),
    (M, CALL_OLD, CALL_NEW),
    (RECOMP_PS, PS_OLD, PS_NEW),
]


def check_base():
    """Refuses to apply without apply_title_smooth.py (it uses its prefetch_start)."""
    if "--remove" in sys.argv[1:]:
        return
    root = FRAMEWORK_ROOT
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--root" and i + 1 < len(args):
            root = args[i + 1]
        elif a.startswith("--root="):
            root = a.split("=", 1)[1]
    try:
        text = open(os.path.join(root, M), "rb").read().decode("utf-8-sig", "replace")
    except OSError:
        return  # run() reports the missing file
    if "void prefetch_start(const std::string& rel)" not in text and MARK not in text:
        sys.exit("hle_movie.cpp without apply_title_smooth.py: apply it first")


if __name__ == "__main__":
    check_base()
    run(MARK, EDITS, __doc__)