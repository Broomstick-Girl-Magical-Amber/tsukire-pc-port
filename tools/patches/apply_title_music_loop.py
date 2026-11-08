"""apply_title_music_loop.py - the title music chosen with F5 / F6 loops where the game loops it (AP_TITLE_MUSIC_LOOP).

Why: back to video 4 with F5 / F6 (or a click on the logo), the port plays MOON01_BGM59.opus from its start and, at the
end of the file (packet 4992), jumped to packet 892. The game does not play the file to its end: its loop goes from
packet 4893 to 892 (log of 2026-11-05 with apply_title_music_diag.py: "game loop in MOON01_BGM59.opus: packet 4893 ->
892"). The last 99 packets (~2 s) are never heard in the game, so the port played ~2 s of music the game skips and then
jumped: "the music does not finish and starts again near the beginning", about 1:40 after the change.

What it does (games/tsukihime_common/title_bg.cpp only):
  - loop_end(): the packet after which the substitute track loops: MOON01_BGM59 packet 4893 (the game's), MOON01_BGM02
    the end of the file as before (its own loop end is not known yet);
  - music_packet: the substitute track wraps at loop_end() instead of at the end of the file.
Plus a row in $Markers of ../tools/recomp_tsukihime.ps1.
If apply_title_music_diag.py is applied, remove it first (it uses the same line).
Idempotent (marker AP_TITLE_MUSIC_LOOP), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found;
--remove leaves the files byte for byte.
After: cmake --build build\\tsukihime (or recomp_tsukihime.ps1 -Edition japan -From 9); the same with build\\tsukihime_global.

Usage (in <root>\\framework):
  private\\venv\\Scripts\\python ..\\tools\\patches\\apply_title_music_loop.py [--remove]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import FRAMEWORK_ROOT, run  # noqa: E402

MARK = "AP_TITLE_MUSIC_LOOP"
T = "games/tsukihime_common/title_bg.cpp"
RECOMP_PS = "../tools/recomp_tsukihime.ps1"

END_OLD = "    return size_t(c.loop02 * 50.0);  // 20 ms per packet\n}\n"
END_NEW = END_OLD + r'''
// AP_TITLE_MUSIC_LOOP (apply_title_music_loop.py): the packet after which the track goes back to loop_index().
// MOON01_BGM59: the game loops from packet 4893 to 892 (it never plays the last 99 packets). MOON01_BGM02: the end of
// the file (the game's loop end for it is not known).
size_t loop_end(int track, size_t packets) {
    if (track == 1) return std::min<size_t>(4894, packets);
    return packets;
}
'''

WRAP_OLD = "    if (++st.sub_idx >= t.offs.size()) st.sub_idx = std::min(loop_index(target, c), t.offs.size() - 1);\n"
WRAP_NEW = ("    if (++st.sub_idx >= loop_end(target, t.offs.size()))  // AP_TITLE_MUSIC_LOOP\n"
            "        st.sub_idx = std::min(loop_index(target, c), t.offs.size() - 1);\n")

PS_OLD = ("    @('both', 'framework\\runtime\\src\\nvn\\nvn_draw_state.cpp', 'stage_program', 'apply_stage_programs.py', "
          "$false),\n")
PS_NEW = PS_OLD + ("    @('both', 'framework\\games\\tsukihime_common\\title_bg.cpp', 'AP_TITLE_MUSIC_LOOP', "
                   "'apply_title_music_loop.py', $false),\n")

EDITS = [
    (T, END_OLD, END_NEW),
    (T, WRAP_OLD, WRAP_NEW),
    (RECOMP_PS, PS_OLD, PS_NEW),
]



def check_diag():
    """Refuses while apply_title_music_diag.py is applied (both use the line where the track wraps)."""
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
        text = open(os.path.join(root, T), "rb").read().decode("utf-8-sig", "replace")
    except OSError:
        return  # run() reports the missing file
    if "AP_TITLE_MUSIC_DIAG" in text:
        sys.exit("title_bg.cpp has apply_title_music_diag.py applied: remove it first (--remove)")


if __name__ == "__main__":
    check_diag()
    run(MARK, EDITS, __doc__)