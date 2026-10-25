"""apply_pad_source.py - a game can read the controller and the keyboard by itself (Tsukihime: input.ini).

Changes (framework, runtime/src):
  platform.h       plat::pad_source (same type as pad_hook): the game's reading instead of read_pad's.
  hle_hid.cpp      defines it, null.
  platform_win.cpp read_pad(): if pad_source is not null it calls it, then input_script(), and returns; if it is null,
                   the usual (XInput index 0 + fixed keys).

Inert for Wonder: pad_source stays null, read_pad does exactly the same as before.
Requires apply_mouse.py (anchor on the pad_hook line).

Usage:  python apply_pad_source.py [--root <root>\\framework] [--remove]
Idempotent (marker AP_PAD_SOURCE), all or nothing, keeps CRLF, refuses if an anchor is not found.
"""
from _patch import run

MARK = "AP_PAD_SOURCE"

EDITS = [
    ("runtime/src/platform.h",
     "extern PadHook pad_hook;\n",
     "extern PadHook pad_hook;\n"
     "// AP_PAD_SOURCE (apply_pad_source.py): a game's own reading of the controller and keyboard in place of read_pad's\n"
     "// fixed one (XInput pad 0 + keys); read_pad still adds input_script after it. Null (the default): as always.\n"
     "extern PadHook pad_source;\n"),

    ("runtime/src/hle_hid.cpp",
     "}  // namespace nx\n",
     "}  // namespace nx\n"
     "\n"
     "namespace nx::plat {\n"
     "PadHook pad_source = nullptr;  // AP_PAD_SOURCE (platform.h): a game's own pad reading, none by default\n"
     "}  // namespace nx::plat\n"),

    ("runtime/src/platform_win.cpp",
     "void read_pad(PadState& p) {\n"
     "    XINPUT_STATE xs{};\n",
     "void read_pad(PadState& p) {\n"
     "    if (pad_source) {  // AP_PAD_SOURCE: the game reads the controller and keyboard itself (Tsukihime: input.ini)\n"
     "        pad_source(p);\n"
     "        input_script(p);\n"
     "        return;\n"
     "    }\n"
     "    XINPUT_STATE xs{};\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)
