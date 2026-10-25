"""apply_discord.py - video notices of the movie:: HLE for Discord Rich Presence (AP_DISCORD).

Discord Rich Presence lives entirely in games/tsukihime_common/discord_tsuki.cpp (new file, copied separately; picked
up by the GLOB of tsukihime_common.cmake). This patcher only adds the notices in hle_movie.cpp, without touching the
decoding path:
  - declaration of tsuki_discord::video_started / video_ended (defined by discord_tsuki.cpp);
  - Extractor::SetDataSource (by path and with ClientStreamReader): video_started(host Extractor, romfs path)
    after the log, if the file was read;
  - Extractor D2 (also used by D0): video_ended(host Extractor) before deleting it.
  The notices only copy the name under a mutex and wake the Discord thread: no I/O in the game threads.
It also adds two rows to $Markers of ../tools/recomp_tsukihime.ps1 (hle_movie.cpp and discord_tsuki.cpp with AP_DISCORD).

Wonder and the framework do not change (everything is in games/tsukihime_common). Both editions use the same texts
(English): the global one only adds _en/_zc/_zt to the video name and discord_tsuki.cpp removes it, no per-edition define.

Idempotent (marker AP_DISCORD), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found; --remove
leaves the files byte for byte as they were. Without discord_tsuki.cpp in tsukihime_common linking fails (missing
video_started/video_ended): copy it before building. After: cmake build\\tsukihime (reconfigure, because of the
new file in the GLOB) and cmake --build build\\tsukihime; the same with build\\tsukihime_global.
"""
from _patch import run

MARK = "AP_DISCORD"

DECL = r'''
// AP_DISCORD (apply_discord.py): avisos para Discord Rich Presence (games/tsukihime_common/discord_tsuki.cpp).
// Solo copian el nombre del video y despiertan a su hilo; no hacen E/S ni tocan la decodificacion.
namespace tsuki_discord {
void video_started(const void* key, const char* romfs_rel);
void video_ended(const void* key);
}  // namespace tsuki_discord
'''

STARTUP_WARNING = "    if (!data.empty()) tsuki_discord::video_started(ext(c.x[0]), rel.c_str());  // AP_DISCORD\n"

HLE = "games/tsukihime_common/hle_movie.cpp"
RECOMP = "../tools/recomp_tsukihime.ps1"

ANCHOR_ROW = r"    @('both', 'framework\runtime\src\platform_win.cpp', 'AP_DATA_ROOT_EXE', 'apply_data_root_exe.py', $false)," + "\n"
NEW_ROWS = (
    r"    @('both', 'framework\games\tsukihime_common\hle_movie.cpp', 'AP_DISCORD', 'apply_discord.py', $false)," + "\n" +
    r"    @('both', 'framework\games\tsukihime_common\discord_tsuki.cpp', 'AP_DISCORD', 'discord_tsuki.cpp (copy it to games\tsukihime_common)', $false)," + "\n"
)

EDITS = [
    (HLE,
     '#include "nvn/nvn.h"  // Texture, get<>, objects_lock, format_block (apply_movie_texture.py)\n',
     '#include "nvn/nvn.h"  // Texture, get<>, objects_lock, format_block (apply_movie_texture.py)\n' + DECL),
    (HLE,
     "void Extractor_D2(nx::CPU& c) {\n    delete ext(c.x[0]);\n",
     "void Extractor_D2(nx::CPU& c) {\n"
     "    tsuki_discord::video_ended(ext(c.x[0]));  // AP_DISCORD\n"
     "    delete ext(c.x[0]);\n"),
    (HLE,
     '    nx::log("movie: SetDataSource(\\"%s\\") -> %zu bytes", rel.c_str(), data.size());\n',
     '    nx::log("movie: SetDataSource(\\"%s\\") -> %zu bytes", rel.c_str(), data.size());\n' + STARTUP_WARNING),
    (HLE,
     '            (unsigned long long)c.x[1], path ? path : "(null)", data.size());\n',
     '            (unsigned long long)c.x[1], path ? path : "(null)", data.size());\n' + STARTUP_WARNING),
    (RECOMP, ANCHOR_ROW, ANCHOR_ROW + NEW_ROWS),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)