"""apply_mouse_ini.py - [mouse] in input.ini replaces ALL the mouse defaults (marker AP_MOUSE_INI).

Before, a [mouse] section only cleared the right click: the wheel kept RSUP/RSDOWN and what the ini said was added to
it (Target::add). With the patch, [mouse] starts empty, like [teclado] and [mando] and as the header comment says.
Without input.ini, or without a [mouse] section, nothing changes. input.ini.ejemplo gives the same (it assigns the wheel explicitly).

Edits games/tsukihime_common/input_tsuki.cpp and ../tools/recomp_tsukihime.ps1 (row in $Markers).
Run: cd <root>\\framework; private\\venv\\Scripts\\python ..\\tools\\patches\\apply_mouse_ini.py [--remove]
After: cmake --build build\\tsukihime and build\\tsukihime_global.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _patch import run  # noqa: E402

MARK = "AP_MOUSE_INI"
EDITS = [
    ("games/tsukihime_common/input_tsuki.cpp",
     "                if (sec == kMouse) defaults_mouse(c.mouse), c.mouse[kMouseRight] = Target{};\n",
     "                if (sec == kMouse)  // AP_MOUSE_INI: [mouse] parte vacio (antes la rueda conservaba el default)\n"
     "                    for (Target& t : c.mouse) t = Target{};\n"),
    ("../tools/recomp_tsukihime.ps1",
     "    @('both', 'framework\\games\\tsukihime_common\\pad_sdl.cpp', 'AP_SDL_PAD', "
     "'pad_sdl.cpp and pad_sdl.h (copy them to games\\tsukihime_common)', $false),\n",
     "    @('both', 'framework\\games\\tsukihime_common\\pad_sdl.cpp', 'AP_SDL_PAD', "
     "'pad_sdl.cpp and pad_sdl.h (copy them to games\\tsukihime_common)', $false),\n"
     "    @('both', 'framework\\games\\tsukihime_common\\input_tsuki.cpp', 'AP_MOUSE_INI', 'apply_mouse_ini.py', $false),\n"),
]

if __name__ == "__main__":
    run(MARK, EDITS, __doc__)