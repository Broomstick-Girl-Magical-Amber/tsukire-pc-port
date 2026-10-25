"""apply_mouse.py - mouse on Windows for the games that ask for it (Tsukihime: click -> touch screen).

Changes (framework, runtime/src):
  platform.h       PointerState + read_pointer() (only _WIN32) and plat::pad_hook (extra buttons of a game).
  platform_win.cpp wndproc keeps position, buttons (with capture while dragging), clicks and wheel; read_pointer().
  hle_hid.cpp      defines plat::pad_hook (null) and calls it after read_pad() in the reading of player 1.

Inert for Wonder: nobody calls read_pointer() and pad_hook stays null. Only a game that uses it changes.

Usage:  python apply_mouse.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_MOUSE), all or nothing, keeps CRLF, refuses if an anchor is not found.
"""
import argparse
import os
import sys

MARK = "AP_MOUSE"

# (relative file, anchor, replacement). Text with \n; converted to \r\n if the file is CRLF.
EDITS = [
    ("runtime/src/platform.h",
     "void input_script(PadState& p);  // NX_INPUT_SCRIPT presses (input_script.cpp), for unattended runs\n",
     "void input_script(PadState& p);  // NX_INPUT_SCRIPT presses (input_script.cpp), for unattended runs\n"
     "// Mouse (AP_MOUSE, apply_mouse.py): the pointer over the window's client area, for a game that turns it into\n"
     "// touches or buttons. x, y in client pixels (may fall outside while a button is held: the window captures the\n"
     "// mouse); w, h the client size; buttons bit 0 left, 1 right, 2 middle; downs[k] counts presses of button k (a\n"
     "// click shorter than a frame still shows); wheel adds WHEEL_DELTA (120) per notch away from the user.\n"
     "struct PointerState {\n"
     "    int32_t x = 0, y = 0;\n"
     "    uint32_t w = 0, h = 0;\n"
     "    uint32_t buttons = 0;\n"
     "    uint32_t downs[3] = {};\n"
     "    int32_t wheel = 0;\n"
     "};\n"
     "#if defined(_WIN32)\n"
     "void read_pointer(PointerState& p);  // platform_win.cpp\n"
     "#endif\n"
     "// A game's own pad input on top of read_pad (hle_hid.cpp calls it for player 1); null (the default): none.\n"
     "using PadHook = void (*)(PadState& p);\n"
     "extern PadHook pad_hook;\n"),

    ("runtime/src/platform_win.cpp",
     "static LRESULT CALLBACK wndproc(HWND h, UINT msg, WPARAM w, LPARAM l) {\n"
     "    switch (msg) {\n",
     "// Mouse (AP_MOUSE, platform.h PointerState): written by the window thread, read by the game's through read_pointer.\n"
     "static std::atomic<int32_t> g_mouse_x{0}, g_mouse_y{0}, g_mouse_wheel{0};\n"
     "static std::atomic<uint32_t> g_mouse_buttons{0};\n"
     "static std::atomic<uint32_t> g_mouse_downs[3] = {};\n"
     "\n"
     "static void mouse_at(LPARAM l) {\n"
     "    g_mouse_x = int32_t(short(LOWORD(l)));\n"
     "    g_mouse_y = int32_t(short(HIWORD(l)));\n"
     "}\n"
     "\n"
     "static void mouse_button(HWND h, int k, bool down, LPARAM l) {\n"
     "    mouse_at(l);\n"
     "    if (down) {\n"
     "        g_mouse_downs[k]++;\n"
     "        g_mouse_buttons.fetch_or(1u << k);\n"
     "        SetCapture(h);  // the release arrives even outside the window (dragging)\n"
     "    } else if ((g_mouse_buttons.fetch_and(~(1u << k)) & ~(1u << k)) == 0) {\n"
     "        ReleaseCapture();\n"
     "    }\n"
     "}\n"
     "\n"
     "static LRESULT CALLBACK wndproc(HWND h, UINT msg, WPARAM w, LPARAM l) {\n"
     "    switch (msg) {\n"
     "        case WM_MOUSEMOVE: mouse_at(l); break;\n"
     "        case WM_LBUTTONDOWN: mouse_button(h, 0, true, l); break;\n"
     "        case WM_LBUTTONUP: mouse_button(h, 0, false, l); break;\n"
     "        case WM_RBUTTONDOWN: mouse_button(h, 1, true, l); break;\n"
     "        case WM_RBUTTONUP: mouse_button(h, 1, false, l); break;\n"
     "        case WM_MBUTTONDOWN: mouse_button(h, 2, true, l); break;\n"
     "        case WM_MBUTTONUP: mouse_button(h, 2, false, l); break;\n"
     "        case WM_MOUSEWHEEL: g_mouse_wheel += GET_WHEEL_DELTA_WPARAM(w); break;\n"
     "        case WM_CAPTURECHANGED:  // another window took the mouse (our own release passes 0 with nothing held)\n"
     "            if (reinterpret_cast<HWND>(l) != h) g_mouse_buttons = 0;\n"
     "            break;\n"
     "        case WM_ACTIVATEAPP:\n"
     "            if (!w) g_mouse_buttons = 0;  // alt-tab with a button held\n"
     "            break;\n"),

    ("runtime/src/platform_win.cpp",
     "void screen_size(uint32_t& w, uint32_t& h) { window_size(w, h); }\n",
     "void screen_size(uint32_t& w, uint32_t& h) { window_size(w, h); }\n"
     "\n"
     "void read_pointer(PointerState& p) {  // AP_MOUSE\n"
     "    p.x = g_mouse_x;\n"
     "    p.y = g_mouse_y;\n"
     "    window_size(p.w, p.h);\n"
     "    p.buttons = g_mouse_buttons;\n"
     "    for (int k = 0; k < 3; k++) p.downs[k] = g_mouse_downs[k];\n"
     "    p.wheel = g_mouse_wheel;\n"
     "}\n"),

    ("runtime/src/hle_hid.cpp",
     "#include \"platform.h\"\n"
     "\n"
     "namespace nx {\n",
     "#include \"platform.h\"\n"
     "\n"
     "namespace nx::plat {\n"
     "PadHook pad_hook = nullptr;  // AP_MOUSE (platform.h): a game's extra pad input, none by default\n"
     "}  // namespace nx::plat\n"
     "\n"
     "namespace nx {\n"),

    ("runtime/src/hle_hid.cpp",
     "    read_pad(p);\n"
     "    // Stick directions as buttons, like the hid sysmodule reports them.\n",
     "    read_pad(p);\n"
     "    if (pad_hook) pad_hook(p);  // AP_MOUSE: a game's own extra input (Tsukihime: mouse buttons and wheel)\n"
     "    // Stick directions as buttons, like the hid sysmodule reports them.\n"),
]


def load(path):
    with open(path, "rb") as f:
        raw = f.read()
    crlf = b"\r\n" in raw
    return raw.decode("utf-8"), crlf


def conv(s, crlf):
    return s.replace("\n", "\r\n") if crlf else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(__import__("pathlib").Path(__file__).resolve().parents[2] / "framework"))
    ap.add_argument("--remove", action="store_true", help="undoes the patch (leaves the files as they were)")
    a = ap.parse_args()

    files = {}
    for rel, _, _ in EDITS:
        if rel not in files:
            path = os.path.join(a.root, rel)
            if not os.path.isfile(path):
                sys.exit(f"ERROR: {path} does not exist")
            files[rel] = load(path)

    marked = {rel: MARK in txt for rel, (txt, _) in files.items()}
    if not a.remove and all(marked.values()):
        print("Already applied (marker AP_MOUSE in every file). Nothing to do.")
        return
    if a.remove and not any(marked.values()):
        print("It was not applied. Nothing to do.")
        return
    if any(marked.values()) and not all(marked.values()):
        sys.exit("ERROR: the marker AP_MOUSE is only in " +
                 ", ".join(r for r, m in marked.items() if m) + ". Mixed state: nothing touched.")

    new = {rel: txt for rel, (txt, _) in files.items()}
    for rel, old, rep in EDITS:
        crlf = files[rel][1]
        src, dst = (conv(rep, crlf), conv(old, crlf)) if a.remove else (conv(old, crlf), conv(rep, crlf))
        n = new[rel].count(src)
        if n != 1:
            sys.exit(f"ERROR: anchor found {n} times in {rel} (1 expected):\n{src[:200]}\nNothing touched.")
        new[rel] = new[rel].replace(src, dst)

    if a.remove and any(MARK in t for t in new.values()):
        sys.exit("ERROR: an AP_MOUSE is left after removing (file edited by hand?). Nothing touched.")

    for rel, txt in new.items():
        with open(os.path.join(a.root, rel), "wb") as f:
            f.write(txt.encode("utf-8"))
        print(("removed: " if a.remove else "patched: ") + rel)


if __name__ == "__main__":
    main()