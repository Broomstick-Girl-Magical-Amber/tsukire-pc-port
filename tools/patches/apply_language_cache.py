"""apply_language_cache.py - the console language and config.txt are not read from disk on every call (AP_LANGUAGE_CACHE).

Why: during autoplay on Android the game's logic thread (guest thread 56) asks am's GetDesiredLanguage 250-1400 times
a second. desired_language() (runtime/src/language.h) read config.txt (plat::setting, an std::ifstream of the whole
file on Android) and then language.txt every time: on the phone's shared storage (/storage/emulated, FUSE) that took
0.6-0.9 ms per call on a Snapdragon 8 Elite and 1.2-1.4 ms on an Exynos 990, so the thread spent 0.3-0.85 s of each
second inside that one request (holding the IPC lock), and the main thread, which waits for it every frame, dropped
to 15-25 fps. Measured with apply_wait_diag.py ("wait diag: ipc thread 56 IApplicationFunctions cmd 21"). On a PC
opening a file costs microseconds, so it never showed there.

What changes:
  - language.h: desired_language() keeps the code it worked out and works it out again (config.txt, language.txt)
    at most once a second, so a language.txt changed while the game runs is still seen. A mutex guards the kept value.
    The old body is desired_language_read(), unchanged.
  - platform_android.cpp: plat::setting() reads config.txt once per data root and answers from memory (same parsing:
    trailing CR and spaces dropped, the first line with that key wins). config.txt is written before the game
    starts (home screen); the game's process ends when the game closes, so the next start reads it again.
  - ../tools/recomp_tsukihime.ps1: its $Markers rows, after the row of language.h of apply_game_language.py (no
    other patcher uses that row).
Windows, Linux and macOS read their settings as before; only the language is kept for a second there too.

  python tools\\patches\\apply_language_cache.py            then rebuild (Android: tools\\android\\build_apk.ps1 -Edition <ed>;
                                                           PC: recomp_tsukihime.ps1 -Edition <ed> -From 9)
  python tools\\patches\\apply_language_cache.py --remove   undoes it (byte for byte)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_LANGUAGE_CACHE"

LANG = "runtime/src/language.h"
PLAT = "runtime/src/platform_android.cpp"
RECO = "../tools/recomp_tsukihime.ps1"

LANG_INC_OLD = """#include <cstring>
#include <string>

#include "platform.h"
"""
LANG_INC_NEW = """#include <cstring>
#include <mutex>  // AP_LANGUAGE_CACHE
#include <string>

#include "platform.h"
"""

LANG_NAME_OLD = """inline uint64_t desired_language() {
    std::string value = language_lower_trimmed(plat::setting("language"));
"""
LANG_NAME_NEW = """inline uint64_t desired_language_read() {  // AP_LANGUAGE_CACHE: read from disk; desired_language() keeps it
    std::string value = language_lower_trimmed(plat::setting("language"));
"""

LANG_END_OLD = """}

}  // namespace nx
"""
LANG_END_NEW = """}

// AP_LANGUAGE_CACHE (apply_language_cache.py): some games ask for the language hundreds of times a second (Tsukihime's
// logic thread, through am's GetDesiredLanguage, while it shows text), and reading config.txt and language.txt took
// 0.6-1.4 ms each time on Android's shared storage, inside the IPC. The answer is kept and worked out again at most
// once a second, so a language.txt changed while the game runs is still seen.
inline uint64_t desired_language() {
    static std::mutex m;
    static uint64_t code = 0, at = 0;
    static bool have = false;
    std::lock_guard lk(m);
    const uint64_t now = plat::monotonic_ns();
    if (!have || now - at >= 1'000'000'000ull) code = desired_language_read(), at = now, have = true;
    return code;
}

}  // namespace nx
"""

PLAT_OLD = """std::string setting(const std::string& key) {
    std::ifstream f(g_root + "/config.txt");
    std::string line;
    while (std::getline(f, line)) {
        while (!line.empty() && (line.back() == '\\r' || line.back() == ' ')) line.pop_back();
        auto eq = line.find('=');
        if (eq != std::string::npos && line.substr(0, eq) == key) return line.substr(eq + 1);
    }
    return {};
}
"""
PLAT_NEW = """// AP_LANGUAGE_CACHE (apply_language_cache.py): config.txt is read once per data root and kept, not opened on every
// call (the language was asked hundreds of times a second, and opening a file on shared storage costs about a
// millisecond). The home screen writes it before the game starts; the process ends with the game.
std::string setting(const std::string& key) {
    static std::mutex m;
    static std::string root;
    static std::vector<std::pair<std::string, std::string>> lines;  // (key, value) in file order
    static bool loaded = false;
    std::lock_guard lk(m);
    if (!loaded || root != g_root) {
        lines.clear();
        std::ifstream f(g_root + "/config.txt");
        std::string line;
        while (std::getline(f, line)) {
            while (!line.empty() && (line.back() == '\\r' || line.back() == ' ')) line.pop_back();
            auto eq = line.find('=');
            if (eq != std::string::npos) lines.push_back({line.substr(0, eq), line.substr(eq + 1)});
        }
        root = g_root, loaded = true;
    }
    for (auto& [k, v] : lines)
        if (k == key) return v;
    return {};
}
"""

RECO_OLD = """    @('both', 'framework\\runtime\\src\\language.h', 'AP_LANG_DEFAULT', 'apply_game_language.py', $false),
"""
RECO_NEW = """    @('both', 'framework\\runtime\\src\\language.h', 'AP_LANG_DEFAULT', 'apply_game_language.py', $false),
    @('both', 'framework\\runtime\\src\\language.h', 'AP_LANGUAGE_CACHE', 'apply_language_cache.py', $false),
    @('both', 'framework\\runtime\\src\\platform_android.cpp', 'AP_LANGUAGE_CACHE', 'apply_language_cache.py', $false),
"""

EDITS = [
    (LANG, LANG_INC_OLD, LANG_INC_NEW),
    (LANG, LANG_NAME_OLD, LANG_NAME_NEW),
    (LANG, LANG_END_OLD, LANG_END_NEW),
    (PLAT, PLAT_OLD, PLAT_NEW),
    (RECO, RECO_OLD, RECO_NEW),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)