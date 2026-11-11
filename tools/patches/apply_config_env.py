"""apply_config_env.py - <data root>/config.txt: the game's NX_* options in a file (AP_CONFIG_ENV).

The runtime reads its switches with getenv (NX_FULLSCREEN, NX_RENDER_RES, NX_FSR, NX_GPU, NX_HUD_GLYPHS, ...). On
Windows they could only be set in a console or a shortcut; the Windows launcher needs a file it can write, as Android
already has (platform_android.cpp: config.txt in the data root). With this patch the exe, as the first thing main()
does, reads <data root>/config.txt (data\\tsuki\\config.txt, data\\tsuki_global\\config.txt) and turns each NX_<NAME>=<value>
line into an environment variable:
- a variable already set (console, shortcut, Steam/Proton launch options) is never replaced: that one wins;
- comments start with ; or #; blank lines and lines that are not NX_<NAME>=<value> are skipped;
- an empty value sets nothing, and neither does 0 for the switches the runtime only tests for existence
  (NX_HANDHELD, NX_MUTE, NX_FIFO, NX_NO_FRAME_LIMIT), so "=0" always means off;
- it is read once, at start: a change needs the game to be started again.
The log says what was set: "config: <path>: NX_FULLSCREEN NX_FSR" (and which were kept from the environment).
NX_DATA_ROOT cannot come from the file (the file is found through it) and NX_CONSOLE is read before main()
(consola_tsuki.cpp): both still go by environment only. Without config.txt nothing changes.

Touches framework/runtime/src/platform_win.cpp (a function before main() and its call as the first line of main(),
both under #ifdef NX_CONFIG_ENV, so Wonder and any other game build exactly as before), the define in
framework/games/tsukihime_common/tsukihime_common.cmake (after the AP_ROMFS_DIRECT one) and two rows of $Markers in
tools/recomp_tsukihime.ps1 (after the romfs_direct.cpp row). Idempotent (marker AP_CONFIG_ENV), all or nothing, keeps
CRLF and BOM, --remove leaves the three files byte for byte as they were.
Apply: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_config_env.py   (then recompile from step 9)
"""
from _patch import run

MARK = "AP_CONFIG_ENV"
WIN = "runtime/src/platform_win.cpp"
CMAKE = "games/tsukihime_common/tsukihime_common.cmake"
RECO = "../tools/recomp_tsukihime.ps1"

LOADER = r'''#ifdef NX_CONFIG_ENV
// AP_CONFIG_ENV (apply_config_env.py): the NX_* lines of <data root>/config.txt (the launcher writes it; it can be
// edited by hand) become environment variables before anything reads them, as config.txt does on Android. A variable
// already set wins (console, shortcut, launch options). Comments with ; or #. An empty value sets nothing, and so
// does 0 for the switches that are only tested for existence. Read once: a change needs the game started again.
#include <cstdio>
#include <string>
static void load_config_env() {
    const std::string path = nx::plat::data_root() + "/config.txt";
    FILE* f = nx::plat::fopen_utf8(path, "rb");
    if (!f) return;
    std::string text;
    char buf[4096];
    for (size_t n; (n = std::fread(buf, 1, sizeof buf, f)) > 0;) text.append(buf, n);
    std::fclose(f);
    if (text.compare(0, 3, "\xEF\xBB\xBF") == 0) text.erase(0, 3);
    auto trim = [](const std::string& s) {
        size_t a = s.find_first_not_of(" \t\r"), b = s.find_last_not_of(" \t\r");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    };
    static const char* const kExistence[] = {"NX_HANDHELD", "NX_MUTE", "NX_FIFO", "NX_NO_FRAME_LIMIT"};
    std::string set, kept;
    size_t pos = 0;
    while (pos < text.size()) {
        size_t end = text.find('\n', pos);
        std::string line = text.substr(pos, end == std::string::npos ? std::string::npos : end - pos);
        pos = end == std::string::npos ? text.size() : end + 1;
        if (size_t c = line.find_first_of(";#"); c != std::string::npos) line.erase(c);
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        const std::string name = trim(line.substr(0, eq)), value = trim(line.substr(eq + 1));
        if (name.size() < 4 || name.compare(0, 3, "NX_") != 0 || value.empty() || name == "NX_DATA_ROOT") continue;
        if (name.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") != std::string::npos) continue;
        bool existence = false;
        for (const char* k : kExistence) existence |= name == k;
        if (existence && value == "0") continue;
        if (std::getenv(name.c_str())) {
            kept += " " + name;
            continue;
        }
        if (_putenv_s(name.c_str(), value.c_str()) == 0) set += " " + name;
    }
    nx::log("config: %s:%s%s%s", path.c_str(), set.empty() ? " nothing set" : set.c_str(),
            kept.empty() ? "" : "; already in the environment (kept):", kept.c_str());
}
#endif

int main() {
#ifdef NX_CONFIG_ENV
    load_config_env();  // AP_CONFIG_ENV: first, so every NX_* of config.txt is seen (the log, the memory guard...)
#endif
    nx::plat::start_memory_guard();'''

EDITS = [
    (WIN,
     '''int main() {
    nx::plat::start_memory_guard();''',
     LOADER),
    (CMAKE,
     '''target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_DIRECT)
''',
     '''target_compile_definitions(${NX_GAME} PRIVATE NX_ROMFS_DIRECT)
# AP_CONFIG_ENV (apply_config_env.py): the NX_* lines of <data root>/config.txt become environment variables at start
# (platform_win.cpp); the launcher's Controls and title tab writes that file
target_compile_definitions(${NX_GAME} PRIVATE NX_CONFIG_ENV)
'''),
    (RECO,
     '''    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ROMFS_DIRECT', 'romfs_direct.cpp (copy it to games\\tsukihime_common)', $false),
''',
     '''    @('both', 'framework\\games\\tsukihime_common\\romfs_direct.cpp', 'AP_ROMFS_DIRECT', 'romfs_direct.cpp (copy it to games\\tsukihime_common)', $false),
    @('both', 'framework\\runtime\\src\\platform_win.cpp', 'AP_CONFIG_ENV', 'apply_config_env.py', $false),
    @('both', 'framework\\games\\tsukihime_common\\tsukihime_common.cmake', 'AP_CONFIG_ENV', 'apply_config_env.py', $false),
'''),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
