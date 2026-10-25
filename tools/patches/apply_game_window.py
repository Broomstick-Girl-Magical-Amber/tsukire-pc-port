"""apply_game_window.py - a game's own window title and icon.

Changes (framework, runtime/src/platform_win.cpp), each one behind a define of games/<game>/game.cmake:
  NX_CUSTOM_WINDOW_TITLE  the window title is nx::game::kWindowTitle (wchar_t, defined in game.cpp) instead
                          of "<title> (Switch recomp)  -  F11: fullscreen". The window class does not change
                          (tools/capture.ps1 looks for it by it).
  NX_WINDOW_ICON          the window and the taskbar use icon 1 of the exe resources (a .rc of the game).
Without the defines (Wonder), everything as before.

Usage:  python apply_game_window.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_WINDOW), all or nothing, keeps CRLF, refuses if an anchor is not found.
"""
from _patch import run

MARK = "AP_WINDOW"

EDITS = [
    ("runtime/src/platform_win.cpp",
     "namespace nx {\n"
     "HWND g_window = nullptr;\n"
     "void boot();\n",
     "namespace nx {\n"
     "HWND g_window = nullptr;\n"
     "void boot();\n"
     "#ifdef NX_CUSTOM_WINDOW_TITLE  // AP_WINDOW (apply_game_window.py)\n"
     "namespace game {\n"
     "extern const wchar_t* const kWindowTitle;  // games/<juego>/game.cpp\n"
     "}\n"
     "#endif\n"),

    ("runtime/src/platform_win.cpp",
     "    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);\n",
     "    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);\n"
     "#ifdef NX_WINDOW_ICON  // AP_WINDOW: the exe's icon 1 (games/<juego>/*.rc) on the window and the taskbar\n"
     "    wc.hIcon = LoadIconW(wc.hInstance, MAKEINTRESOURCEW(1));\n"
     "#endif\n"),

    ("runtime/src/platform_win.cpp",
     "    g_window = CreateWindowW(wc.lpszClassName, (widen(nx::game::kInfo.title) + L\" (Switch recomp)  -  F11: fullscreen\").c_str(), style,\n",
     "#ifdef NX_CUSTOM_WINDOW_TITLE  // AP_WINDOW\n"
     "    const std::wstring title = nx::game::kWindowTitle;\n"
     "#else\n"
     "    const std::wstring title = widen(nx::game::kInfo.title) + L\" (Switch recomp)  -  F11: fullscreen\";\n"
     "#endif\n"
     "    g_window = CreateWindowW(wc.lpszClassName, title.c_str(), style,\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
