"""apply_data_root_exe.py - Tsukihime data root relative to the exe folder (AP_DATA_ROOT_EXE).

Problem (2026-10-18 23:00): on Windows plat::data_root() (runtime/src/platform_win.cpp) is NX_DATA_ROOT or the
pc_data_root of kInfo as is. With an absolute path in game.cpp (E:/port/data/...), the exe copied to another folder
or another PC does not find its data ("FATAL: missing .../analysis/main.bin").

Fix, per game and inert for the others (Wonder unchanged):
  - platform_win.cpp: with NX_DATA_ROOT_EXE defined and without NX_DATA_ROOT, a RELATIVE pc_data_root is resolved
    against the exe folder (GetModuleFileNameW + GetFullPathNameW, UTF-8, only once). Absolute: same as before.
    Without the define the compiled code is the usual one.
  - games/tsukihime/game.cmake and games/tsukihime_global/game.cmake: target_compile_definitions(NX_DATA_ROOT_EXE).
  - games/tsukihime/game.cpp and games/tsukihime_global/game.cpp: pc_data_root "../../../data/tsuki[_global]"
    (build/<game>/ -> port/data/<edition>). NX_DATA_ROOT still has priority.
  - ../tools/internal/create_tsukihime_global.ps1: its data root replacement pair moves to the relative path (otherwise,
    when recreating the global it would not find the japan anchor).

Idempotent (marker AP_DATA_ROOT_EXE), all or nothing, keeps CRLF and BOM, refuses if an anchor is not found;
--remove leaves the files byte for byte as they were. After: no need to reconfigure (game.cmake changes and ninja
reconfigures by itself); cmake --build build\\tsukihime and build\\tsukihime_global.
"""
from _patch import run

MARK = "AP_DATA_ROOT_EXE"

HELPER = r'''// AP_DATA_ROOT_EXE (apply_data_root_exe.py): with NX_DATA_ROOT_EXE (games/<game>/game.cmake) a relative
// pc_data_root is taken from the exe's folder instead of the current directory, so the build runs from any folder
// or PC. An absolute one, or NX_DATA_ROOT, behave as before. Without the define nothing here is compiled.
#ifdef NX_DATA_ROOT_EXE
static std::string data_root_exe(const char* rel) {
    static const std::string cached = [rel]() -> std::string {
        if (!rel || !*rel) return std::string();
        if (rel[0] == '/' || rel[0] == '\\' || rel[1] == ':') return std::string(rel);  // absolute: as given
        std::wstring exe(32768, L'\0');
        DWORD n = GetModuleFileNameW(nullptr, exe.data(), DWORD(exe.size()));
        if (n == 0 || n >= exe.size()) return std::string(rel);
        exe.resize(n);
        size_t slash = exe.find_last_of(L"\\/");
        std::wstring dir = slash == std::wstring::npos ? std::wstring(L".") : exe.substr(0, slash);
        int wl = MultiByteToWideChar(CP_UTF8, 0, rel, -1, nullptr, 0);
        if (wl <= 1) return std::string(rel);
        std::wstring wrel(size_t(wl), L'\0');
        MultiByteToWideChar(CP_UTF8, 0, rel, -1, wrel.data(), wl);
        wrel.resize(size_t(wl - 1));
        std::wstring joined = dir + L"\\" + wrel;
        std::wstring full(32768, L'\0');
        DWORD m = GetFullPathNameW(joined.c_str(), DWORD(full.size()), full.data(), nullptr);
        if (m == 0 || m >= full.size()) full = joined; else full.resize(m);
        int ul = WideCharToMultiByte(CP_UTF8, 0, full.c_str(), -1, nullptr, 0, nullptr, nullptr);
        if (ul <= 1) return std::string(rel);
        std::string out(size_t(ul), '\0');
        WideCharToMultiByte(CP_UTF8, 0, full.c_str(), -1, out.data(), ul, nullptr, nullptr);
        out.resize(size_t(ul - 1));
        return out;
    }();
    return cached;
}
#endif

'''

CMAKE_ANCHOR = "# Titulo e icono de la ventana (apply_game_window.py): kWindowTitle en game.cpp, icono 1 en tsukihime.rc\n"
CMAKE_BLOCK = ("# AP_DATA_ROOT_EXE: el pc_data_root relativo de game.cpp se toma desde la carpeta del exe (apply_data_root_exe.py)\n"
                "target_compile_definitions(${NX_GAME} PRIVATE NX_DATA_ROOT_EXE)\n\n")
OLD_COMMENT = "  // datos fuera del repo (NX_DATA_ROOT tiene prioridad si esta definida)"
NEW_COMMENT = "  // AP_DATA_ROOT_EXE: relativo a la carpeta del exe (build\\<juego>\\ -> port\\data\\...); NX_DATA_ROOT tiene prioridad"

EDITS = [
    ("runtime/src/platform_win.cpp", "std::string data_root() {\n", HELPER + "std::string data_root() {\n"),
    ("runtime/src/platform_win.cpp",
     'const char* r = std::getenv("NX_DATA_ROOT");\n',
     'const char* r = std::getenv("NX_DATA_ROOT");\n'
     "#ifdef NX_DATA_ROOT_EXE\n"
     "    if (!r) return data_root_exe(nx::game::kInfo.pc_data_root);  // AP_DATA_ROOT_EXE\n"
     "#endif\n"),
    ("games/tsukihime/game.cmake", CMAKE_ANCHOR, CMAKE_BLOCK + CMAKE_ANCHOR),
    ("games/tsukihime_global/game.cmake", CMAKE_ANCHOR, CMAKE_BLOCK + CMAKE_ANCHOR),
    ("games/tsukihime/game.cpp",
     '"E:/port/data/tsuki",' + OLD_COMMENT,
     '"../../../data/tsuki",' + NEW_COMMENT),
    ("games/tsukihime_global/game.cpp",
     '"E:/port/data/tsuki_global",' + OLD_COMMENT,
     '"../../../data/tsuki_global",' + NEW_COMMENT),
    ("../tools/internal/create_tsukihime_global.ps1",
     "    @('\"E:/port/data/tsuki\",', '\"E:/port/data/tsuki_global\",'),\n",
     "    @('\"../../../data/tsuki\",', '\"../../../data/tsuki_global\",'),  # AP_DATA_ROOT_EXE (apply_data_root_exe.py)\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)